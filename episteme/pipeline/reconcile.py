"""Cross-paper reconcile — merge semantically similar claims across distinct sources."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from itertools import combinations

from episteme.config import (
    MODEL_FAST,
    RECONCILE_CONFLICT_TYPES,
    RECONCILE_HAIKU_CEILING,
    RECONCILE_MAX_SIM,
    RECONCILE_MIN_SIM,
    RECONCILE_NODE_TYPES,
)
from episteme.core.cache import Cache
from episteme.core.embeddings import cosine_sim, embed
from episteme.core.graph import (
    GraphStore,
    attestation_source_key,
    ensure_attestations,
    unique_attestation_source_count,
)
from episteme.core.llm import call_llm
from episteme.pipeline.attestation_stance import StanceGuard, load_stance_guard
from episteme.profiles.case_profile import load_case_profile
from episteme.filters.junk_quote import is_junk_quote
from episteme.prompts.reconcile import RECONCILE_PAIR_VERDICT

logger = logging.getLogger(__name__)

_MAX_EXACT_COMPONENT_SIZE = 12


class _UnionFind:
    def __init__(self, ids: list[str]):
        self.parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for i in self.parent:
            out[self.find(i)].append(i)
        return dict(out)


def _node_source_keys(node: dict) -> set[str]:
    return {
        attestation_source_key(a)
        for a in ensure_attestations(node)
        if attestation_source_key(a)
    }


def _cross_paper(a: dict, b: dict) -> bool:
    sa, sb = _node_source_keys(a), _node_source_keys(b)
    return bool(sa and sb and not sa.intersection(sb))


def _pair_has_conflict(store: GraphStore, a_id: str, b_id: str) -> bool:
    for src, tgt in ((a_id, b_id), (b_id, a_id)):
        node = store.get_node(src)
        if not node:
            continue
        for rel in node.get("relations", []):
            if rel.get("target") == tgt and rel.get("type") in RECONCILE_CONFLICT_TYPES:
                return True
    return False


def _node_pair_conflicts(
    store: GraphStore,
    stance_guard: StanceGuard,
    a_id: str,
    b_id: str,
) -> bool:
    """Single source of truth for 'can these two nodes ever share a merged
    node'. Used both for fresh candidate pairs and to re-validate pairs
    that were never directly compared during candidate generation. Both
    checks are cheap (no LLM call)."""
    if _pair_has_conflict(store, a_id, b_id):
        return True
    a = store.get_node(a_id)
    b = store.get_node(b_id)
    if a and b and stance_guard.pair_merge_stance_conflict(a, b):
        return True
    return False


def _best_clique_partition(
    members: list[str],
    conflicts: dict[str, set[str]],
    pair_weight: dict[frozenset, float],
) -> list[set[str]]:
    """Exact search over all partitions of `members` into cliques of the
    compatibility graph, maximizing total confirmed-pair similarity
    captured. Exponential in the worst case (Bell number of |members|);
    only called for components <= _MAX_EXACT_COMPONENT_SIZE."""
    members = sorted(members)
    n = len(members)
    best: list[set[str]] = []
    best_score = -1.0

    def score(partition: list[set[str]]) -> float:
        return sum(
            pair_weight.get(frozenset((a, b)), 0.0)
            for group in partition
            for a, b in combinations(group, 2)
        )

    def backtrack(i: int, partition: list[set[str]]) -> None:
        nonlocal best, best_score
        if i == n:
            s = score(partition)
            if s > best_score:
                best_score, best = s, [set(g) for g in partition]
            return
        node = members[i]
        for group in partition:
            if conflicts[node].isdisjoint(group):
                group.add(node)
                backtrack(i + 1, partition)
                group.remove(node)
        partition.append({node})
        backtrack(i + 1, partition)
        partition.pop()

    backtrack(0, [])
    return [g for g in best if len(g) >= 2]


def _greedy_clique_groups(
    store: GraphStore,
    stance_guard: StanceGuard,
    members: list[str],
    confirmed: list[tuple[str, str, float]],
) -> dict[str, list[str]]:
    """Fallback for components above _MAX_EXACT_COMPONENT_SIZE. Builds
    groups incrementally in similarity-descending order, only admitting a
    node into a group if it's compatible with every existing member.
    Order-dependent (not guaranteed optimal) but still guarantees every
    resulting group is internally conflict-free."""
    member_set = set(members)
    local_pairs = [
        (a, b) for a, b, _sim in confirmed
        if a in member_set and b in member_set
    ]

    groups: list[set[str]] = []
    node_to_group: dict[str, int] = {}

    for a_id, b_id in local_pairs:
        ga, gb = node_to_group.get(a_id), node_to_group.get(b_id)
        if ga is not None and ga == gb:
            continue
        if ga is None and gb is None:
            groups.append({a_id, b_id})
            idx = len(groups) - 1
            node_to_group[a_id] = node_to_group[b_id] = idx
        elif ga is not None and gb is None:
            if all(not _node_pair_conflicts(store, stance_guard, b_id, m) for m in groups[ga]):
                groups[ga].add(b_id)
                node_to_group[b_id] = ga
        elif gb is not None and ga is None:
            if all(not _node_pair_conflicts(store, stance_guard, a_id, m) for m in groups[gb]):
                groups[gb].add(a_id)
                node_to_group[a_id] = gb
        else:
            if all(
                not _node_pair_conflicts(store, stance_guard, x, y)
                for x in groups[ga] for y in groups[gb]
            ):
                groups[ga] |= groups[gb]
                for m in groups[gb]:
                    node_to_group[m] = ga
                groups[gb] = set()

    return {f"fallback_{i}": list(g) for i, g in enumerate(groups) if len(g) >= 2}


def _resolve_groups(
    store: GraphStore,
    stance_guard: StanceGuard,
    confirmed: list[tuple[str, str, float]],
    stats: dict,
) -> dict[str, list[str]]:
    """Builds final merge groups from confirmed pairs. Discovery (which
    nodes might end up together) still uses union-find; the actual
    grouping decision re-validates every pair within each component
    rather than trusting transitive closure."""
    if not confirmed:
        return {}

    discovery = _UnionFind(list({nid for a, b, _ in confirmed for nid in (a, b)}))
    for a_id, b_id, _ in confirmed:
        discovery.union(a_id, b_id)

    pair_weight = {frozenset((a, b)): sim for a, b, sim in confirmed}
    groups: dict[str, list[str]] = {}

    for root, members in discovery.groups().items():
        if len(members) < 2:
            continue

        if len(members) > _MAX_EXACT_COMPONENT_SIZE:
            logger.warning(
                "reconcile: component of %d nodes exceeds exact-partition "
                "limit (%d); falling back to greedy grouping",
                len(members), _MAX_EXACT_COMPONENT_SIZE,
            )
            stats["large_components"] = stats.get("large_components", 0) + 1
            groups.update(_greedy_clique_groups(store, stance_guard, members, confirmed))
            continue

        conflicts: dict[str, set[str]] = {m: set() for m in members}
        for a, b in combinations(members, 2):
            if _node_pair_conflicts(store, stance_guard, a, b):
                conflicts[a].add(b)
                conflicts[b].add(a)

        for i, clique in enumerate(_best_clique_partition(members, conflicts, pair_weight)):
            groups[f"{root}_{i}"] = list(clique)

    return groups


def _epistemic_status(support_count: int, contradict_count: int) -> str:
    if contradict_count > 0:
        return "contested"
    if support_count >= 3:
        return "well_established"
    if support_count >= 2:
        return "supported"
    return "single_source"


def _contradicting_source_count(store: GraphStore, node_id: str) -> int:
    sources: set[str] = set()
    for node in store.get_all_nodes():
        for rel in node.get("relations", []):
            if rel.get("target") == node_id and rel.get("type") in RECONCILE_CONFLICT_TYPES:
                sources.update(_node_source_keys(node))
    return len(sources)


def compute_epistemic_fields(store: GraphStore, node_id: str) -> dict:
    node = store.get_node(node_id)
    if not node:
        return {}
    support_count = unique_attestation_source_count(ensure_attestations(node))
    contradict_count = _contradicting_source_count(store, node_id)
    return {
        "support_count": support_count,
        "contradict_count": contradict_count,
        "epistemic_status": _epistemic_status(support_count, contradict_count),
    }


def update_epistemic_fields(store: GraphStore, node_id: str) -> dict:
    fields = compute_epistemic_fields(store, node_id)
    if fields:
        store.update_node(node_id, fields)
    return fields


def _format_debate_positions(case_profile: dict | None) -> str:
    positions = (case_profile or {}).get("debate_positions") or []
    if not positions:
        return "(none — merge only on semantic identity, not rival stances)"
    return "\n".join(f"- {p}" for p in positions[:6])


def _haiku_verdict(
    cache: Cache,
    a: dict,
    b: dict,
    similarity: float,
    case_profile: dict | None,
) -> str:
    sa = ", ".join(sorted(_node_source_keys(a))) or "unknown"
    sb = ", ".join(sorted(_node_source_keys(b))) or "unknown"
    qa = (a.get("textual_evidence") or a.get("quote_exact") or "")[:300]
    qb = (b.get("textual_evidence") or b.get("quote_exact") or "")[:300]
    pair_key = "::".join(sorted([a["id"], b["id"]]))

    result = cache.get_or_run(
        "agent",
        f"reconcile::{pair_key}",
        lambda: call_llm(
            RECONCILE_PAIR_VERDICT.format(
                debate_positions=_format_debate_positions(case_profile),
                source_a=sa,
                source_b=sb,
                type_a=a.get("type", ""),
                type_b=b.get("type", ""),
                content_a=a.get("content", ""),
                content_b=b.get("content", ""),
                quote_a=qa or "(none)",
                quote_b=qb or "(none)",
                similarity=similarity,
            ),
            model=MODEL_FAST,
            max_tokens=400,
            parse_json=True,
            label="reconcile_pair",
        ),
    )
    if isinstance(result, dict) and not result.get("parse_error"):
        return (result.get("verdict") or "distinct").lower()
    return "distinct"


def _find_candidate_pairs(
    nodes: list[dict],
    embeddings: dict[str, object],
) -> list[tuple[float, str, str]]:
    pairs: list[tuple[float, str, str]] = []
    for i, a in enumerate(nodes):
        ea = embeddings.get(a["id"])
        if ea is None:
            continue
        for b in nodes[i + 1 :]:
            if a.get("type") != b.get("type"):
                continue
            if not _cross_paper(a, b):
                continue
            eb = embeddings.get(b["id"])
            if eb is None:
                continue
            sim = cosine_sim(ea, eb)
            if RECONCILE_MIN_SIM <= sim <= RECONCILE_MAX_SIM:
                pairs.append((sim, a["id"], b["id"]))
    pairs.sort(reverse=True)
    return pairs


def _merge_groups(store: GraphStore, groups: dict[str, list[str]]) -> int:
    merged = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical_id = max(
            members,
            key=lambda mid: ((store.get_node(mid) or {}).get("evidential_weight") or 0),
        )
        for mid in members:
            if mid != canonical_id and store.get_node(mid):
                store.merge_nodes(canonical_id, mid)
                merged += 1
        update_epistemic_fields(store, canonical_id)
    return merged


def _should_drop_attestation(quote: str, content: str, stance_guard: StanceGuard) -> bool:
    if not quote:
        return False
    if is_junk_quote(quote):
        return True
    if stance_guard.attestation_conflicts_claim(quote, content):
        return True
    if stance_guard.attestation_lacks_debate_support(quote, content):
        return True
    return False


def prune_conflicting_attestations(
    store: GraphStore,
    stance_guard: StanceGuard | None = None,
) -> int:
    """Remove junk, irrelevant, or stance-conflicting attestations."""
    guard = stance_guard or load_stance_guard(store.case)
    removed = 0
    for node in store.get_all_nodes():
        content = node.get("content", "")
        atts = ensure_attestations(node)
        kept = []
        for att in atts:
            quote = att.get("quote") or ""
            drop = is_junk_quote(quote)
            if not drop and node.get("type") in RECONCILE_NODE_TYPES:
                drop = _should_drop_attestation(quote, content, guard)
            if not quote or drop:
                removed += 1
                continue
            kept.append(att)
        if len(kept) != len(atts):
            store.update_node(node["id"], {"attestations": kept})
            if node.get("type") in RECONCILE_NODE_TYPES:
                update_epistemic_fields(store, node["id"])
    return removed


def run_reconcile(case: str, cache: Cache, store: GraphStore) -> dict:
    case_profile = load_case_profile(case) or {}
    stance_guard = StanceGuard(case_profile)

    stats = {
        "candidates": 0,
        "auto_merge_pairs": 0,
        "haiku_pairs": 0,
        "haiku_merged": 0,
        "haiku_skipped": 0,
        "conflict_skipped": 0,
        "stance_skipped": 0,
        "large_components": 0,
        "nodes_merged": 0,
        "groups": 0,
    }

    for ntype in RECONCILE_NODE_TYPES:
        nodes = store.get_nodes_by_type(ntype)
        if len(nodes) < 2:
            continue

        embeddings = {n["id"]: embed(n.get("content", "")) for n in nodes}
        pairs = _find_candidate_pairs(nodes, embeddings)
        stats["candidates"] += len(pairs)
        print(f"\n  {ntype}: {len(nodes)} nodes, {len(pairs)} cross-paper pairs in "
              f"[{RECONCILE_MIN_SIM}, {RECONCILE_MAX_SIM}]")

        confirmed: list[tuple[str, str, float]] = []

        for sim, a_id, b_id in pairs:
            if _pair_has_conflict(store, a_id, b_id):
                stats["conflict_skipped"] += 1
                continue

            a = store.get_node(a_id)
            b = store.get_node(b_id)
            if a and b and stance_guard.pair_merge_stance_conflict(a, b):
                stats["stance_skipped"] += 1
                continue

            merge = False
            if sim >= RECONCILE_HAIKU_CEILING:
                merge = True
                stats["auto_merge_pairs"] += 1
            else:
                stats["haiku_pairs"] += 1
                if not a or not b:
                    continue
                verdict = _haiku_verdict(cache, a, b, sim, case_profile)
                if verdict in ("same", "compatible"):
                    merge = True
                    stats["haiku_merged"] += 1
                    print(f"    haiku merge ({sim:.3f}): {a_id[:8]} + {b_id[:8]} — {verdict}")
                else:
                    stats["haiku_skipped"] += 1

            if merge:
                confirmed.append((a_id, b_id, sim))

        ntype_groups = _resolve_groups(store, stance_guard, confirmed, stats)
        if ntype_groups:
            merged = _merge_groups(store, ntype_groups)
            stats["nodes_merged"] += merged
            stats["groups"] += len(ntype_groups)
            print(f"  {ntype}: merged {merged} nodes into {len(ntype_groups)} group(s)")

    # Refresh epistemic fields on all reconcile-eligible nodes
    labeled = 0
    for ntype in RECONCILE_NODE_TYPES:
        for node in store.get_nodes_by_type(ntype):
            update_epistemic_fields(store, node["id"])
            labeled += 1

    stats["labeled"] = labeled
    pruned = prune_conflicting_attestations(store, stance_guard)
    stats["attestations_pruned"] = pruned
    multi = sum(
        1
        for ntype in RECONCILE_NODE_TYPES
        for n in store.get_nodes_by_type(ntype)
        if unique_attestation_source_count(ensure_attestations(n)) > 1
    )
    stats["multi_source_after"] = multi

    print(
        f"\n  Reconcile: {stats['nodes_merged']} nodes absorbed into {stats['groups']} groups, "
        f"{stats['multi_source_after']} multi-source nodes, {stats['conflict_skipped']} conflict skips, "
        f"{stats['stance_skipped']} stance skips, {pruned} attestations pruned"
    )
    return stats


def reconcile_summary(store: GraphStore) -> dict:
    """Counts by epistemic_status for dashboard/debug."""
    counts: dict[str, int] = defaultdict(int)
    for ntype in RECONCILE_NODE_TYPES:
        for node in store.get_nodes_by_type(ntype):
            status = node.get("epistemic_status") or "unknown"
            counts[status] += 1
    return dict(counts)
