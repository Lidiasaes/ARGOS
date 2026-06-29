"""Cross-paper reconcile — merge semantically similar claims across distinct sources."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from itertools import combinations

from episteme.config import (
    CONTESTED_MIN_CONTRADICTION_STRENGTH,
    MODEL_FAST,
    RECONCILE_CONFLICT_TYPES,
    RECONCILE_CONTRADICTION_MAX_SIM,
    RECONCILE_CONTRADICTION_MIN_SIM,
    RECONCILE_DETECT_CONTRADICTIONS,
    RECONCILE_HAIKU_CEILING,
    RECONCILE_MAX_SIM,
    RECONCILE_MIN_SIM,
    RECONCILE_NODE_TYPES,
    RECONCILE_SHARED_QUESTION_MIN_OVERLAP,
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


def _node_authors(node: dict) -> set[str]:
    """Normalized author set for a node (from attestations, falling back to
    the legacy node-level source_author)."""
    authors = {
        (a.get("author") or "").strip().lower()
        for a in ensure_attestations(node)
        if (a.get("author") or "").strip()
    }
    if not authors:
        sa = (node.get("source_author") or "").strip().lower()
        if sa:
            authors.add(sa)
    return authors


def _same_document(a: dict, b: dict) -> bool:
    """True when the two nodes share at least one source key — i.e. they come
    from the same document. A 'contradiction' between same-document nodes is
    one document matching/qualifying itself, not a cross-paper dispute."""
    return bool(_node_source_keys(a).intersection(_node_source_keys(b)))


def _same_author(a: dict, b: dict) -> bool:
    """True when both nodes are attributable to exactly the same author(s).
    Candidate pairs are already cross-source, so 'same author + different
    source' means one author contradicting themselves across documents —
    a self_inconsistency rather than a cross-paper debate contradiction."""
    aa, ab = _node_authors(a), _node_authors(b)
    return bool(aa and ab and aa == ab)


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


def _epistemic_status(
    support_count: int,
    strong_contradict_count: int,
    weak_contradict_count: int,
) -> str:
    # contested_weak sits below contested but above supported/well_established:
    # a deliberate, reversible design choice so weak (sub-floor or
    # shared-question-hallucinated) contradictions still surface without
    # masquerading as high-confidence disputes.
    if strong_contradict_count > 0:
        return "contested"
    if weak_contradict_count > 0:
        return "contested_weak"
    if support_count >= 3:
        return "well_established"
    if support_count >= 2:
        return "supported"
    return "single_source"


def _contradicting_source_count(store: GraphStore, node_id: str) -> tuple[int, int]:
    """Count UNIQUE source keys of nodes that contradict node_id, split into
    strong and weak. Only `contradicts` / `weak_contradicts` edges count;
    `self_inconsistency` and `quantitative_divergence` NEVER do.

    Intentionally narrower than the old RECONCILE_CONFLICT_TYPES behavior:
      - strong: type == "contradicts" AND strength >= floor
      - weak:   type == "contradicts" AND strength <  floor, OR
                type == "weak_contradicts"
    """
    strong_sources: set[str] = set()
    weak_sources: set[str] = set()
    for node in store.get_all_nodes():
        for rel in node.get("relations", []):
            if rel.get("target") != node_id:
                continue
            rtype = rel.get("type")
            if rtype == "contradicts":
                strength = rel.get("strength") or 0.0
                if strength >= CONTESTED_MIN_CONTRADICTION_STRENGTH:
                    strong_sources.update(_node_source_keys(node))
                else:
                    weak_sources.update(_node_source_keys(node))
            elif rtype == "weak_contradicts":
                weak_sources.update(_node_source_keys(node))
    return len(strong_sources), len(weak_sources)


def compute_epistemic_fields(store: GraphStore, node_id: str) -> dict:
    node = store.get_node(node_id)
    if not node:
        return {}
    support_count = unique_attestation_source_count(ensure_attestations(node))
    strong_contradict_count, weak_contradict_count = _contradicting_source_count(store, node_id)
    return {
        "support_count": support_count,
        # contradict_count keeps the strong count for backward compat with the dashboard.
        "contradict_count": strong_contradict_count,
        "weak_contradict_count": weak_contradict_count,
        "epistemic_status": _epistemic_status(
            support_count, strong_contradict_count, weak_contradict_count
        ),
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
) -> dict:
    """
    Returns dict with keys: verdict, reason, shared_question.
    verdict in {"same", "compatible", "contradicts",
                "quantitative_divergence", "distinct"}.

    Note: changed from str to dict to preserve the haiku's reason and
    shared_question fields for downstream logging and edge rationale.
    """
    sa = ", ".join(sorted(_node_source_keys(a))) or "unknown"
    sb = ", ".join(sorted(_node_source_keys(b))) or "unknown"
    qa = (a.get("textual_evidence") or a.get("quote_exact") or "")[:300]
    qb = (b.get("textual_evidence") or b.get("quote_exact") or "")[:300]
    pair_key = "::".join(sorted([a["id"], b["id"]]))

    # Cache key bumped to v3 because the prompt now offers a new verdict
    # ("quantitative_divergence"); old v2 cached results never carry it
    # and would silently keep classifying those pairs as "contradicts".
    result = cache.get_or_run(
        "agent",
        f"reconcile_v3::{pair_key}",
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
        verdict = (result.get("verdict") or "distinct").lower()
        if verdict not in (
            "same", "compatible", "contradicts",
            "quantitative_divergence", "distinct",
        ):
            verdict = "distinct"
        return {
            "verdict": verdict,
            "reason": result.get("reason", ""),
            "shared_question": result.get("shared_question", ""),
        }
    return {"verdict": "distinct", "reason": "parse error", "shared_question": ""}


def _record_quantitative_divergence(
    store: GraphStore,
    a_id: str,
    b_id: str,
    sim: float,
    reason: str,
    shared_q: str,
    edge_source: str,
    stats: dict,
) -> None:
    """Record a non-conflict 'quantitative_divergence' relation: both
    sources agree a phenomenon exists but report different magnitudes.
    Kept out of the contradicts bucket so it never inflates contradiction
    counts or flips epistemic_status to 'contested'."""
    rationale = reason
    if shared_q:
        rationale = f"{reason} (shared question: {shared_q})"
    added_a = store.add_relation(
        a_id, b_id, "quantitative_divergence",
        strength=sim,
        rationale=rationale,
        source=edge_source,
    )
    added_b = store.add_relation(
        b_id, a_id, "quantitative_divergence",
        strength=sim,
        rationale=rationale,
        source=edge_source,
    )
    if added_a or added_b:
        stats["quantitative_divergence"] += 1
        stats["quant_divergence_edges_added"] += int(added_a) + int(added_b)
        print(f"    quant divergence ({sim:.3f}): "
              f"{a_id[:8]} vs {b_id[:8]} — {reason[:90]}")


_RECONCILE_EDGE_SOURCES = frozenset({
    "reconcile_haiku",
    "reconcile_contradiction_sweep",
})


def _clear_reconcile_edges(store: GraphStore) -> int:
    """Remove edges previously created by reconcile so re-runs are
    idempotent. Without this, a pair reclassified from "contradicts" to
    e.g. "quantitative_divergence" or "self_inconsistency" would keep its
    stale "contradicts" edge alongside the new one (different types are not
    duplicates), silently defeating the refinement. Only edges tagged with
    a reconcile source are touched; edges from other steps are preserved."""
    removed = 0
    for node in store.get_all_nodes():
        rels = node.get("relations") or []
        kept = [r for r in rels if r.get("source") not in _RECONCILE_EDGE_SOURCES]
        if len(kept) != len(rels):
            removed += len(rels) - len(kept)
            store.update_node(node["id"], {"relations": kept})
    return removed


def _shared_question_overlap(shared_q: str, emb_a, emb_b) -> float | None:
    """Min cosine similarity between the haiku's shared_question and each
    node's content embedding. Returns None when it can't be computed
    (no question, or embeddings unavailable) so callers can skip the
    downgrade rather than penalize on missing data."""
    if not shared_q or emb_a is None or emb_b is None:
        return None
    sq_emb = embed(shared_q)
    if sq_emb is None:
        return None
    return min(cosine_sim(sq_emb, emb_a), cosine_sim(sq_emb, emb_b))


def _record_contradiction(
    store: GraphStore,
    a: dict,
    b: dict,
    sim: float,
    reason: str,
    shared_q: str,
    edge_source: str,
    stats: dict,
    emb_a=None,
    emb_b=None,
) -> bool:
    """Record a contradiction, routing it to the right bucket:

      - same author across documents      → "self_inconsistency"   (#2)
      - abstract/unverifiable shared_q     → "weak_contradicts"     (#3)
      - genuine cross-author disagreement  → "contradicts"

    Only "contradicts" feeds the high-confidence crux signal; the other two
    are kept separate so single-author flip-flops and coherence
    hallucinations don't inflate the debate's contradiction metrics.
    """
    a_id, b_id = a["id"], b["id"]

    # Defensive guard: shared source = one document matching/qualifying itself,
    # not a cross-paper dispute. _find_*candidate_pairs already require
    # _cross_paper, so this should be a no-op in normal flow; we keep it because
    # a contested node in real data points to a path that bypassed that check.
    # Routed to the same bucket/stats as the same-author case.
    if _same_document(a, b):
        rel_type, stat_key, edge_key = (
            "self_inconsistency", "self_inconsistency", "self_inconsistency_edges_added",
        )
    elif _same_author(a, b):
        rel_type, stat_key, edge_key = (
            "self_inconsistency", "self_inconsistency", "self_inconsistency_edges_added",
        )
    else:
        rel_type, stat_key, edge_key = (
            "contradicts", "haiku_contradicts", "contradicts_edges_added",
        )
        overlap = _shared_question_overlap(shared_q, emb_a, emb_b)
        if overlap is not None and overlap < RECONCILE_SHARED_QUESTION_MIN_OVERLAP:
            rel_type, stat_key, edge_key = (
                "weak_contradicts", "weak_contradicts", "weak_contradicts_edges_added",
            )

    rationale = reason
    if shared_q:
        rationale = f"{reason} (shared question: {shared_q})"

    added_a = store.add_relation(
        a_id, b_id, rel_type, strength=sim, rationale=rationale, source=edge_source,
    )
    added_b = store.add_relation(
        b_id, a_id, rel_type, strength=sim, rationale=rationale, source=edge_source,
    )
    if added_a or added_b:
        stats[stat_key] += 1
        stats[edge_key] += int(added_a) + int(added_b)
        print(f"    {rel_type.upper()} ({sim:.3f}): "
              f"{a_id[:8]} vs {b_id[:8]} — {reason[:90]}")
    return added_a or added_b


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


def _find_contradiction_candidate_pairs(
    nodes: list[dict],
    embeddings: dict[str, object],
) -> list[tuple[float, str, str]]:
    """
    Cross-paper pairs in the contradiction similarity window.
    Wider than the merge window because opposing claims sit at lower
    similarity than paraphrases.

    Skips pairs that:
      - share at least one source (not cross-paper)
      - involve rhetorical moves on either side
      - have attributed_to != source_author on either side

    Returns pairs sorted by similarity descending.
    """
    pairs: list[tuple[float, str, str]] = []
    for i, a in enumerate(nodes):
        if a.get("is_rhetorical_move"):
            continue
        if (a.get("attributed_to") or "source_author") != "source_author":
            continue
        ea = embeddings.get(a["id"])
        if ea is None:
            continue
        for b in nodes[i + 1:]:
            if b.get("is_rhetorical_move"):
                continue
            if (b.get("attributed_to") or "source_author") != "source_author":
                continue
            if a.get("type") != b.get("type"):
                continue
            if not _cross_paper(a, b):
                continue
            eb = embeddings.get(b["id"])
            if eb is None:
                continue
            sim = cosine_sim(ea, eb)
            if RECONCILE_CONTRADICTION_MIN_SIM <= sim <= RECONCILE_CONTRADICTION_MAX_SIM:
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


_STRENGTH_HIST_LO = 0.40
_STRENGTH_HIST_HI = 1.00
_STRENGTH_HIST_STEP = 0.05


def contradiction_strength_report(store: GraphStore) -> dict:
    """Deterministic diagnostic over all `contradicts` / `weak_contradicts`
    edges. No LLM/network. Makes the strength-aware + intra-document hardening
    observable: lists every contradiction edge with its strength and whether it
    is intra-document / same-author, plus a coarse strength histogram and a few
    headline counts."""
    edges: list[dict] = []
    buckets: dict[str, int] = {}
    n_buckets = round((_STRENGTH_HIST_HI - _STRENGTH_HIST_LO) / _STRENGTH_HIST_STEP)
    for i in range(n_buckets):
        lo = _STRENGTH_HIST_LO + i * _STRENGTH_HIST_STEP
        buckets[f"{lo:.2f}"] = 0

    total_contradicts = 0
    contradicts_below_floor = 0
    intra_document_edges = 0
    same_author_edges = 0

    for node in store.get_all_nodes():
        for rel in node.get("relations", []):
            rtype = rel.get("type")
            if rtype not in ("contradicts", "weak_contradicts"):
                continue
            target = store.get_node(rel.get("target", ""))
            if not target:
                continue
            strength = rel.get("strength") or 0.0
            intra = _same_document(node, target)
            same_auth = _same_author(node, target)

            edges.append({
                "source_node": node["id"],
                "target_node": target["id"],
                "type": rtype,
                "strength": strength,
                "intra_document": intra,
                "same_author": same_auth,
            })

            if rtype == "contradicts":
                total_contradicts += 1
                if strength < CONTESTED_MIN_CONTRADICTION_STRENGTH:
                    contradicts_below_floor += 1
            if intra:
                intra_document_edges += 1
            if same_auth:
                same_author_edges += 1

            clamped = min(max(strength, _STRENGTH_HIST_LO), _STRENGTH_HIST_HI - 1e-9)
            idx = int((clamped - _STRENGTH_HIST_LO) / _STRENGTH_HIST_STEP)
            idx = min(max(idx, 0), n_buckets - 1)
            key = f"{_STRENGTH_HIST_LO + idx * _STRENGTH_HIST_STEP:.2f}"
            buckets[key] += 1

    return {
        "edges": edges,
        "strength_histogram": buckets,
        "total_contradicts": total_contradicts,
        "contradicts_below_floor": contradicts_below_floor,
        "intra_document_edges": intra_document_edges,
        "same_author_edges": same_author_edges,
    }


def run_reconcile(case: str, cache: Cache, store: GraphStore) -> dict:
    case_profile = load_case_profile(case) or {}
    stance_guard = StanceGuard(case_profile)

    stats = {
        "candidates": 0,
        "contradiction_candidates": 0,
        "auto_merge_pairs": 0,
        "haiku_pairs": 0,
        "haiku_merged": 0,
        "haiku_skipped": 0,
        "haiku_contradicts": 0,
        "contradicts_edges_added": 0,
        "quantitative_divergence": 0,
        "quant_divergence_edges_added": 0,
        "self_inconsistency": 0,
        "self_inconsistency_edges_added": 0,
        "weak_contradicts": 0,
        "weak_contradicts_edges_added": 0,
        "contradiction_haiku_pairs": 0,
        "conflict_skipped": 0,
        "stance_skipped": 0,
        "large_components": 0,
        "nodes_merged": 0,
        "groups": 0,
        "reconcile_edges_cleared": 0,
    }

    # Idempotency: drop edges from prior reconcile runs before regenerating,
    # so reclassified pairs don't accumulate stale + new edges.
    stats["reconcile_edges_cleared"] = _clear_reconcile_edges(store)
    if stats["reconcile_edges_cleared"]:
        print(f"  Cleared {stats['reconcile_edges_cleared']} edges from a previous reconcile run")

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
                verdict_result = _haiku_verdict(cache, a, b, sim, case_profile)
                verdict = verdict_result["verdict"]
                reason = verdict_result["reason"]
                shared_q = verdict_result.get("shared_question", "")

                if verdict in ("same", "compatible"):
                    merge = True
                    stats["haiku_merged"] += 1
                    print(f"    haiku merge ({sim:.3f}): {a_id[:8]} + {b_id[:8]} — {verdict}")
                elif verdict == "contradicts":
                    # Routes to contradicts / self_inconsistency /
                    # weak_contradicts via validated add_relation (rejects
                    # self-loops, duplicates, orphans).
                    _record_contradiction(
                        store, a, b, sim, reason, shared_q,
                        "reconcile_haiku", stats,
                        emb_a=embeddings.get(a_id),
                        emb_b=embeddings.get(b_id),
                    )
                elif verdict == "quantitative_divergence":
                    _record_quantitative_divergence(
                        store, a_id, b_id, sim, reason, shared_q,
                        "reconcile_haiku", stats,
                    )
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

    # ─── Contradiction sweep: wider similarity window ──────────────
    # Run AFTER merges so we don't redundantly check pairs that just
    # merged. Uses RECONCILE_CONTRADICTION_MIN_SIM (0.45) — below the
    # merge floor — because polarity-opposed claims embed lower than
    # paraphrases.
    if RECONCILE_DETECT_CONTRADICTIONS:
        for ntype in RECONCILE_NODE_TYPES:
            nodes = store.get_nodes_by_type(ntype)
            if len(nodes) < 2:
                continue
            embeddings = {n["id"]: embed(n.get("content", "")) for n in nodes}
            cpairs = _find_contradiction_candidate_pairs(nodes, embeddings)
            stats["contradiction_candidates"] += len(cpairs)

            if not cpairs:
                continue

            print(f"\n  {ntype}: scanning {len(cpairs)} contradiction-candidate pairs "
                  f"in [{RECONCILE_CONTRADICTION_MIN_SIM}, {RECONCILE_CONTRADICTION_MAX_SIM}]")

            for sim, a_id, b_id in cpairs:
                # Skip pairs already merged in this run (their nodes no
                # longer exist) or already related via contradicts.
                a = store.get_node(a_id)
                b = store.get_node(b_id)
                if not a or not b:
                    continue
                if _pair_has_conflict(store, a_id, b_id):
                    continue

                stats["contradiction_haiku_pairs"] += 1
                verdict_result = _haiku_verdict(cache, a, b, sim, case_profile)
                verdict = verdict_result["verdict"]
                reason = verdict_result["reason"]
                shared_q = verdict_result.get("shared_question", "")

                if verdict == "quantitative_divergence":
                    _record_quantitative_divergence(
                        store, a_id, b_id, sim, reason, shared_q,
                        "reconcile_contradiction_sweep", stats,
                    )
                    continue

                if verdict != "contradicts":
                    continue

                _record_contradiction(
                    store, a, b, sim, reason, shared_q,
                    "reconcile_contradiction_sweep", stats,
                    emb_a=embeddings.get(a_id),
                    emb_b=embeddings.get(b_id),
                )

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

    # Deterministic diagnostic so the strength-aware + intra-document hardening
    # is observable on every (free) rerun.
    strength_report = contradiction_strength_report(store)
    stats["contradiction_total_contradicts"] = strength_report["total_contradicts"]
    stats["contradiction_below_floor"] = strength_report["contradicts_below_floor"]
    stats["contradiction_intra_document_edges"] = strength_report["intra_document_edges"]
    stats["contradiction_same_author_edges"] = strength_report["same_author_edges"]

    nonzero_hist = {k: v for k, v in strength_report["strength_histogram"].items() if v}
    print(
        f"\n  Contradiction strength report: "
        f"{strength_report['total_contradicts']} contradicts edges "
        f"({strength_report['contradicts_below_floor']} below floor "
        f"{CONTESTED_MIN_CONTRADICTION_STRENGTH}), "
        f"{strength_report['intra_document_edges']} intra-document, "
        f"{strength_report['same_author_edges']} same-author"
    )
    print(f"    strength histogram (0.05 buckets): {nonzero_hist or '{}'}")

    print(
        f"\n  Reconcile: {stats['nodes_merged']} nodes absorbed into {stats['groups']} groups, "
        f"{stats['multi_source_after']} multi-source nodes, "
        f"{stats['haiku_contradicts']} contradictions detected ({stats['contradicts_edges_added']} edges), "
        f"{stats['weak_contradicts']} weak ({stats['weak_contradicts_edges_added']} edges), "
        f"{stats['self_inconsistency']} self-inconsistencies ({stats['self_inconsistency_edges_added']} edges), "
        f"{stats['quantitative_divergence']} quantitative divergences ({stats['quant_divergence_edges_added']} edges), "
        f"{stats['conflict_skipped']} conflict skips, "
        f"{stats['stance_skipped']} stance skips, {pruned} attestations pruned"
    )
    return stats


def reconcile_summary(store: GraphStore) -> dict:
    """Counts by epistemic_status for dashboard/debug."""
    counts: dict[str, int] = defaultdict(int)
    # Seed known statuses (incl. the new contested_weak tier) so they appear
    # as explicit 0s rather than silently missing.
    for status in (
        "contested",
        "contested_weak",
        "well_established",
        "supported",
        "single_source",
    ):
        counts[status] = 0
    for ntype in RECONCILE_NODE_TYPES:
        for node in store.get_nodes_by_type(ntype):
            status = node.get("epistemic_status") or "unknown"
            counts[status] += 1
    return dict(counts)
