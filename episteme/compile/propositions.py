"""
Collapse redundant typed conflict edges into a deterministic "proposition" layer.

Reconcile (episteme/pipeline/reconcile.py) emits typed conflict edges
(contradicts / weak_contradicts / quantitative_divergence / self_inconsistency),
each carrying a Haiku-generated "shared_question" describing the proposition the
two claims disagree about. That shared_question is currently only embedded in the
edge's rationale string, so one hub claim can sprout 7+ edges that are all the
SAME underlying proposition phrased slightly differently — edge explosion.

This module is a NO-LLM, read-only compile step. It reads the existing
graph.json, groups shared_question strings into propositions via deterministic
CENTROID-ANCHORED greedy clustering over their embeddings, and writes a new
artifact (compiled/propositions.json). It NEVER mutates the graph.

Why not crystallize.greedy_cluster? That helper is SEED-ANCHORED: membership of
a later item depends only on its similarity to whichever item happened to be the
cluster's first seed. With alphabetically-sorted seeds, a family of ~0.80-similar
questions fails to coalesce and the boundary wobbles with seed order.
cluster_questions() below anchors on the running cluster CENTROID instead, so a
family coalesces and neighbouring families stay crisp.

Everything here is case-agnostic: no topic/keyword strings appear in the
clustering or the calibration, so it works identically for covid, fertility,
lhc, or any future case.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone

import numpy as np

from episteme.config import CASES_DIR, PROPOSITION_CLUSTER_THRESHOLD
# compiled_dir is reused for artifact placement; clustering/medoid are now done
# locally with centroid anchoring (see module docstring).
from episteme.compile.crystallize import compiled_dir
from episteme.core.embeddings import embed, cosine_sim
from episteme.core.graph import (
    GraphStore,
    attestation_source_key,
    ensure_attestations,
)

# Only these edge types describe a "the two claims disagree about X" relation
# and therefore carry a meaningful shared_question.
PROPOSITION_EDGE_TYPES = frozenset({
    "contradicts",
    "weak_contradicts",
    "quantitative_divergence",
    "self_inconsistency",
})

# Format reconcile writes the shared_question into the rationale string:
#   "<reason> (shared question: <q>)"
_SHARED_Q_RE = re.compile(r"\(shared question:\s*(.*)\)\s*$")


def _node_source_keys(node: dict) -> set[str]:
    """Source keys (paper identities) attesting a node. Mirrors the helper in
    reconcile.py so proposition source counts use the same notion of 'source'."""
    return {
        attestation_source_key(a)
        for a in ensure_attestations(node)
        if attestation_source_key(a)
    }


def extract_shared_question(rel: dict) -> str:
    """Pull the shared_question for an edge, robust to graphs built before the
    first-class field existed.

    1. Prefer the first-class rel["shared_question"] (written by current
       reconcile runs — see reconcile._record_contradiction /
       _record_quantitative_divergence).
    2. Fall back to regex-parsing it out of the rationale string (the format
       reconcile has always written), so this works on older graphs with zero
       re-ingest / re-reconcile.
    """
    sq = (rel.get("shared_question") or "").strip()
    if sq:
        return sq
    m = _SHARED_Q_RE.search(rel.get("rationale", "") or "")
    if m:
        return m.group(1).strip()
    return ""


def _embed_cached(question: str, embeds: dict):
    """Embed a question exactly once, caching the (possibly None) result so we
    never re-embed the same string."""
    if question not in embeds:
        try:
            embeds[question] = embed(question)
        except Exception:
            embeds[question] = None
    return embeds[question]


def _normalized_centroid(vectors: list):
    """Mean of member embeddings, renormalized to unit length so cosine sim
    against it is a plain dot product. Returns None for an empty/degenerate set."""
    if not vectors:
        return None
    c = np.mean(np.asarray(vectors), axis=0)
    norm = float(np.linalg.norm(c))
    if norm == 0.0:
        return None
    return c / norm


def cluster_questions(
    questions: list[str],
    embeds: dict,
    threshold: float,
) -> list[list[str]]:
    """Deterministic CENTROID-ANCHORED greedy clustering of question strings.

    - Each unique question is embedded once (cached in `embeds`); we never
      re-embed.
    - Seed order is length DESC (longest / most-specific question first), with
      the question string as a tie-break, giving a stable, meaningful order.
    - For each question we compute cosine similarity to the CURRENT CENTROID
      (mean of member embeddings) of every existing cluster, join the
      highest-similarity cluster whose sim >= threshold, else open a new cluster,
      then recompute that cluster's centroid.

    Because membership depends on the cluster CENTER rather than one arbitrary
    seed, a family of mutually-similar questions coalesces at a higher threshold
    while the boundary with a neighbouring family stays crisp. Fully
    deterministic given the embedding model.
    """
    for q in questions:
        _embed_cached(q, embeds)

    ordered = sorted(questions, key=lambda q: (-len(q), q))

    clusters: list[list[str]] = []
    member_vecs: list[list] = []
    centroids: list = []

    for q in ordered:
        v = embeds.get(q)
        if v is None:
            clusters.append([q])
            member_vecs.append([])
            centroids.append(None)
            continue

        best_i, best_sim = -1, -1.0
        for i, c in enumerate(centroids):
            if c is None:
                continue
            sim = cosine_sim(v, c)
            if sim > best_sim:
                best_sim, best_i = sim, i

        if best_i >= 0 and best_sim >= threshold:
            clusters[best_i].append(q)
            member_vecs[best_i].append(v)
            centroids[best_i] = _normalized_centroid(member_vecs[best_i])
        else:
            clusters.append([q])
            member_vecs.append([v])
            centroids.append(_normalized_centroid([v]))

    return clusters


def merge_close_clusters(
    clusters: list[list[str]],
    embeds: dict,
    threshold: float,
) -> list[list[str]]:
    """Deterministic post-clustering consolidation (agglomerative).

    cluster_questions is order-dependent: two large clusters can each seed early
    and then never be compared to one another, so near-identical propositions
    coexist (covid showed prop_0001 vs prop_0003 at centroid sim 0.92 with
    threshold 0.78). This pass repeatedly fuses the single closest pair of
    clusters whose centroids are >= threshold, recomputing after each merge,
    until none remain.

    Guarantees the invariant: NO two returned clusters have centroid cosine
    similarity >= threshold. Fully deterministic — clusters are processed in
    smallest-member-question order, and ties are resolved by that same order
    (earliest pair wins). Reuses cached embeddings; never re-embeds.
    """
    work = [list(c) for c in clusters]

    def _centroid_of(members: list[str]):
        return _normalized_centroid(
            [v for v in (embeds.get(q) for q in members) if v is not None]
        )

    while True:
        # Stable order so pair discovery (and tie-breaking) is deterministic.
        work.sort(key=lambda c: min(c) if c else "")
        centroids = [_centroid_of(c) for c in work]

        best = None  # (sim, i, j)
        n = len(work)
        for i in range(n):
            ci = centroids[i]
            if ci is None:
                continue
            for j in range(i + 1, n):
                cj = centroids[j]
                if cj is None:
                    continue
                sim = cosine_sim(ci, cj)
                # strict > keeps the earliest (smallest-member-string) pair on ties
                if sim >= threshold and (best is None or sim > best[0]):
                    best = (sim, i, j)

        if best is None:
            break

        _, i, j = best
        merged = work[i] + work[j]
        work = [c for k, c in enumerate(work) if k not in (i, j)]
        work.append(merged)

    work.sort(key=lambda c: min(c) if c else "")
    return work


def _medoid_question(questions: list[str], embeds: dict) -> str:
    """Pick the question closest to the cluster centroid as canonical label,
    reusing the cached embeddings (no re-embedding). Deterministic: ties broken
    by question string."""
    valid = [(q, embeds.get(q)) for q in questions if embeds.get(q) is not None]
    if not valid:
        return sorted(questions)[0]
    if len(valid) == 1:
        return valid[0][0]
    centroid = _normalized_centroid([v for _, v in valid])
    if centroid is None:
        return sorted(questions)[0]
    best_q, best_sim = sorted(questions)[0], -2.0
    for q, v in sorted(valid, key=lambda x: x[0]):
        sim = cosine_sim(v, centroid)
        if sim > best_sim:
            best_sim, best_q = sim, q
    return best_q


def collect_proposition_edges(store: GraphStore) -> list[dict]:
    """Every qualifying directed edge in the graph, with its shared_question.

    A qualifying edge has a type in PROPOSITION_EDGE_TYPES, a resolvable target,
    and a non-empty shared_question.
    """
    edges: list[dict] = []
    for node in store.get_all_nodes():
        src_id = node.get("id")
        for rel in node.get("relations", []) or []:
            etype = rel.get("type")
            if etype not in PROPOSITION_EDGE_TYPES:
                continue
            tgt_id = rel.get("target")
            if not tgt_id:
                continue
            shared_q = extract_shared_question(rel)
            if not shared_q:
                continue
            edges.append({
                "shared_question": shared_q,
                "source_node_id": src_id,
                "target_node_id": tgt_id,
                "edge_type": etype,
            })
    return edges


def _status_for(edge_types: set[str], source_count: int) -> str:
    """Deterministic proposition status from the set of edge types present in
    the cluster and the number of distinct sources across its member claims."""
    if "contradicts" in edge_types and source_count >= 2:
        return "contested"
    if "quantitative_divergence" in edge_types:
        return "divergent"
    if edge_types == {"self_inconsistency"}:
        return "single_author_tension"
    if edge_types == {"weak_contradicts"}:
        return "weak_tension"
    return "open"


def build_propositions(store: GraphStore) -> tuple[list[dict], list[dict], dict]:
    """Cluster qualifying edges into propositions.

    Returns (propositions, qualifying_edges, embeds) so callers can report the
    raw qualifying-edge count E alongside the proposition count P, and reuse the
    embedding cache for calibration without re-embedding.
    """
    edges = collect_proposition_edges(store)
    embeds: dict = {}
    if not edges:
        return [], edges, embeds

    # Cluster on UNIQUE shared_question strings — short strings repeat verbatim
    # across both directions of a conflict, and clustering uniques keeps the
    # embedding work proportional to distinct propositions, not edge count.
    unique_questions = sorted({e["shared_question"] for e in edges})
    clusters = cluster_questions(unique_questions, embeds, PROPOSITION_CLUSTER_THRESHOLD)
    # Consolidate clusters left split by the order-dependent greedy pass, so the
    # final invariant holds: no two propositions sit at centroid sim >= threshold.
    clusters = merge_close_clusters(clusters, embeds, PROPOSITION_CLUSTER_THRESHOLD)

    # Map each shared_question string to the edges that carry it.
    edges_by_question: dict[str, list[dict]] = {}
    for e in edges:
        edges_by_question.setdefault(e["shared_question"], []).append(e)

    raw_props: list[dict] = []
    for cluster_qs in clusters:
        canonical_question = _medoid_question(cluster_qs, embeds)

        cluster_edges: list[dict] = []
        for q in cluster_qs:
            cluster_edges.extend(edges_by_question.get(q, []))

        claim_ids: list[str] = []
        seen_claims: set[str] = set()
        for e in cluster_edges:
            for cid in (e["source_node_id"], e["target_node_id"]):
                if cid and cid not in seen_claims:
                    seen_claims.add(cid)
                    claim_ids.append(cid)

        sources: set[str] = set()
        for cid in claim_ids:
            node = store.get_node(cid)
            if node:
                sources |= _node_source_keys(node)

        edge_types = Counter(e["edge_type"] for e in cluster_edges)
        status = _status_for(set(edge_types), len(sources))

        raw_props.append({
            "question": canonical_question,
            "variants": sorted(set(cluster_qs)),
            "claim_ids": claim_ids,
            "edge_count": len(cluster_edges),
            "edge_types": dict(edge_types),
            "sources": sorted(sources),
            "source_count": len(sources),
            "status": status,
        })

    # Deterministic ordering + ids: biggest collapses first, then by question.
    raw_props.sort(key=lambda p: (-p["edge_count"], p["question"]))
    propositions = []
    for i, p in enumerate(raw_props):
        propositions.append({"id": f"prop_{i:04d}", **p})
    return propositions, edges, embeds


def save_propositions(case: str, propositions: list[dict]):
    path = compiled_dir(case) / "propositions.json"
    path.write_text(
        json.dumps(propositions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _boundary_calibration(propositions: list[dict], embeds: dict) -> dict:
    """Case-agnostic separability diagnostic over the produced propositions.

    Computes each proposition's centroid from the cached question embeddings and
    returns the closest pair of distinct propositions plus a count of pairs at or
    above the clustering threshold ("near-duplicate" propositions the greedy pass
    left split). No topic strings, no per-case logic — works for any case.
    """
    centroids: list[tuple[str, str, object]] = []
    for p in propositions:
        vecs = [embeds.get(q) for q in p.get("variants", [])]
        c = _normalized_centroid([v for v in vecs if v is not None])
        if c is not None:
            centroids.append((p["id"], p["question"], c))

    near_dup_pairs = 0
    best = None  # (sim, id_a, q_a, id_b, q_b)
    for i in range(len(centroids)):
        id_a, q_a, ca = centroids[i]
        for j in range(i + 1, len(centroids)):
            id_b, q_b, cb = centroids[j]
            sim = cosine_sim(ca, cb)
            if sim >= PROPOSITION_CLUSTER_THRESHOLD:
                near_dup_pairs += 1
            if best is None or sim > best[0]:
                best = (sim, id_a, q_a, id_b, q_b)

    closest_pair = None
    max_sim = None
    if best is not None:
        max_sim = float(best[0])
        closest_pair = {
            "a": best[1],
            "a_question": best[2],
            "b": best[3],
            "b_question": best[4],
            "sim": round(max_sim, 4),
        }
    return {
        "near_duplicate_proposition_pairs": near_dup_pairs,
        "max_inter_proposition_centroid_sim": max_sim,
        "closest_pair": closest_pair,
    }


def run_propositions(case: str, store: GraphStore) -> dict:
    """Deterministic, $0, read-only collapse of conflict edges into propositions.

    Reads the existing graph (no re-ingest, no reconcile re-run, no LLM calls),
    writes compiled/propositions.json, and NEVER mutates graph.json.
    """
    propositions, edges, embeds = build_propositions(store)
    E = len(edges)
    P = len(propositions)
    collapse_ratio = (E / P) if P else 0.0

    path = save_propositions(case, propositions)

    top = propositions[:10]
    largest = propositions[0] if propositions else None

    # ── Calibration (CASE-AGNOSTIC): how crisp are the proposition boundaries
    # at the chosen threshold? For every proposition we take the centroid of its
    # member-question embeddings (reused from the cache — no re-embedding) and
    # inspect the most-similar pair of DISTINCT propositions. If even the closest
    # pair sits below threshold, no two propositions could have merged — the
    # boundaries are crisp. Pairs at/above threshold are "near-duplicate"
    # propositions the greedy pass left split (a fragmentation / path-dependence
    # signal worth lowering the threshold for). Nothing here references a
    # specific case or topic, so it generalizes to fertility, lhc, etc.
    calib = _boundary_calibration(propositions, embeds)

    print(f"  Qualifying conflict edges (E): {E}")
    print(f"  Propositions created (P):      {P}")
    print(f"  Collapse ratio (E/P):          {collapse_ratio:.2f}")
    if largest:
        print(
            f"  Largest proposition absorbed {largest['edge_count']} edges:\n"
            f"    \"{largest['question']}\""
        )
    print("  Top propositions by edge_count:")
    for p in top:
        print(
            f"    [{p['id']}] edges={p['edge_count']:<3} "
            f"status={p['status']:<22} "
            f"sources={p['source_count']:<2} "
            f"claims={len(p['claim_ids']):<3} "
            f"{p['question'][:80]}"
        )

    max_inter_sim = calib["max_inter_proposition_centroid_sim"]
    sim_str = "n/a" if max_inter_sim is None else f"{max_inter_sim:.4f}"
    print(f"  Calibration (threshold {PROPOSITION_CLUSTER_THRESHOLD}):")
    print(f"    near-duplicate proposition pairs (centroid sim >= threshold): "
          f"{calib['near_duplicate_proposition_pairs']}")
    print(f"    max inter-proposition centroid sim: {sim_str}"
          + (f"  ({calib['closest_pair']['a']} vs {calib['closest_pair']['b']})"
             if calib["closest_pair"] else ""))
    if calib["closest_pair"]:
        cp = calib["closest_pair"]
        print(f"      {cp['a']}: {cp['a_question'][:72]}")
        print(f"      {cp['b']}: {cp['b_question'][:72]}")
    print(f"  Propositions saved: {path}")

    return {
        "case": case,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "qualifying_edges": E,
        "propositions": P,
        "collapse_ratio": round(collapse_ratio, 4),
        "threshold": PROPOSITION_CLUSTER_THRESHOLD,
        "near_duplicate_proposition_pairs": calib["near_duplicate_proposition_pairs"],
        "max_inter_proposition_centroid_sim": (
            None if max_inter_sim is None else round(float(max_inter_sim), 4)
        ),
        "closest_pair": calib["closest_pair"],
        "largest_proposition": (
            {
                "id": largest["id"],
                "question": largest["question"],
                "edge_count": largest["edge_count"],
            }
            if largest
            else None
        ),
        "top": [
            {
                "id": p["id"],
                "question": p["question"],
                "edge_count": p["edge_count"],
                "status": p["status"],
                "source_count": p["source_count"],
                "claim_count": len(p["claim_ids"]),
            }
            for p in top
        ],
        "path": str(path),
    }
