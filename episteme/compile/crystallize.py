"""
Compress raw graph.json into a compiled Epistemic Index for LLM reasoning.

Deterministic layer: embeddings + greedy clustering + centrality ranking.
Generative layer: conditional chains + cruxes (1-2 LLM calls).
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from episteme.config import (
    CASES_DIR,
    CLUSTER_THRESHOLD,
    CLAIM_CLUSTER_THRESHOLD,
    MAX_RANKED_CLAIMS,
    MAX_THEMES,
    MODEL_SMART,
)
from episteme.core.cache import Cache, content_hash
from episteme.core.graph import GraphStore
from episteme.core.embeddings import embed, cosine_sim
from episteme.core.llm import call_llm
from episteme.prompts import CRYSTALLIZE_CHAINS, CRYSTALLIZE_CRUXES


def compiled_dir(case: str) -> Path:
    d = CASES_DIR / case / "compiled"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_compiled(case: str) -> dict | None:
    path = compiled_dir(case) / "index.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_compiled(case: str, index: dict) -> Path:
    path = compiled_dir(case) / "index.json"
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _embed_or_none(text: str):
    try:
        return embed(text)
    except Exception:
        return None


def greedy_cluster(
    items: list[dict],
    text_key: str = "content",
    threshold: float = CLUSTER_THRESHOLD,
) -> list[list[dict]]:
    """Greedy semantic clustering using embeddings. Deterministic given model."""
    if not items:
        return []

    embeddings = []
    for item in items:
        emb = _embed_or_none(item.get(text_key, ""))
        embeddings.append(emb)

    clusters: list[list[dict]] = []
    used = [False] * len(items)

    for i, item in enumerate(items):
        if used[i]:
            continue
        cluster = [item]
        used[i] = True
        if embeddings[i] is None:
            clusters.append(cluster)
            continue
        for j in range(i + 1, len(items)):
            if used[j] or embeddings[j] is None:
                continue
            if cosine_sim(embeddings[i], embeddings[j]) >= threshold:
                cluster.append(items[j])
                used[j] = True
        clusters.append(cluster)

    return clusters


def _cluster_medoid(cluster: list[dict], text_key: str = "content") -> dict:
    """Pick the member closest to the cluster centroid as label anchor."""
    embs = []
    for item in cluster:
        embs.append(_embed_or_none(item.get(text_key, "")))

    valid = [(i, e) for i, e in enumerate(embs) if e is not None]
    if not valid:
        return cluster[0]
    if len(valid) == 1:
        return cluster[valid[0][0]]

    centroid = np.mean([e for _, e in valid], axis=0)
    best_i, best_sim = valid[0][0], -1.0
    for i, e in valid:
        sim = float(np.dot(e, centroid))
        if sim > best_sim:
            best_sim = sim
            best_i = i
    return cluster[best_i]


def _claim_centrality(node: dict) -> float:
    rels = node.get("relations", [])
    presup_count = sum(1 for r in rels if r.get("type") == "presupposes")
    return (
        len(rels) * 1.0
        + presup_count * 2.0
        + (node.get("evidential_weight") or 0) * 3.0
        + (2.0 if node.get("needs_review") else 0)
    )


def rank_claims(store: GraphStore, n: int = MAX_RANKED_CLAIMS) -> list[dict]:
    claims = store.get_nodes_by_type("claim")
    ranked = sorted(claims, key=_claim_centrality, reverse=True)[:n]
    return [
        {
            "id": c["id"],
            "content": c["content"],
            "source_author": c.get("source_author", ""),
            "evidential_weight": c.get("evidential_weight") or 0,
            "centrality": round(_claim_centrality(c), 2),
            "relation_count": len(c.get("relations", [])),
            "needs_review": c.get("needs_review", False),
        }
        for c in ranked
    ]


def build_themes(store: GraphStore) -> list[dict]:
    presups = store.get_nodes_by_type("presupposition")
    if not presups:
        return []

    clusters = greedy_cluster(presups, threshold=CLUSTER_THRESHOLD)
    clusters.sort(key=len, reverse=True)

    themes = []
    for i, cluster in enumerate(clusters[:MAX_THEMES]):
        medoid = _cluster_medoid(cluster)
        member_ids = [p["id"] for p in cluster]

        # Find claims that presuppose any member
        claim_ids = []
        for claim in store.get_nodes_by_type("claim"):
            for rel in claim.get("relations", []):
                if rel.get("type") == "presupposes" and rel.get("target") in member_ids:
                    claim_ids.append(claim["id"])
                    break

        themes.append({
            "id": f"theme_{i:02d}",
            "label": medoid["content"][:200],
            "medoid_id": medoid["id"],
            "member_ids": member_ids,
            "member_count": len(member_ids),
            "claim_ids": list(dict.fromkeys(claim_ids))[:15],
            "needs_review": any(p.get("needs_review") for p in cluster),
            "impact": "FATAL" if any(p.get("needs_review") for p in cluster) else "MAJOR",
        })

    return themes


def cluster_claims(store: GraphStore, threshold: float = CLAIM_CLUSTER_THRESHOLD) -> list[dict]:
    """Cluster claims for structure v2 batch philosopher."""
    claims = store.get_nodes_by_type("claim")
    if not claims:
        return []

    clusters = greedy_cluster(claims, threshold=threshold)
    result = []
    for i, cluster in enumerate(clusters):
        rep = _cluster_medoid(cluster)
        result.append({
            "cluster_id": f"claim_cluster_{i:02d}",
            "representative_id": rep["id"],
            "member_ids": [c["id"] for c in cluster],
            "member_count": len(cluster),
            "representative": {
                "id": rep["id"],
                "content": rep["content"],
                "source_type": rep.get("source_type", "unknown"),
                "claim_type": rep.get("claim_type", "unknown"),
            },
        })
    return result


_STOP_WORDS = frozenset({
    "that", "this", "with", "from", "have", "been", "were", "their", "there",
    "which", "would", "could", "should", "about", "into", "than", "then", "when",
    "what", "whether", "because", "these", "those", "other", "after", "before",
    "such", "only", "also", "more", "most", "some", "than", "them", "they",
})


def _token_set(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())} - _STOP_WORDS


def _overlap_score(a: str, b: str) -> float:
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _score_gaps_for_text(
    text: str,
    gaps: list[dict],
    *,
    max_n: int = 4,
    min_sim: float = 0.32,
) -> list[str]:
    """Link gap node ids to a crux/chain by embedding or keyword overlap."""
    if not text.strip() or not gaps:
        return []
    text_emb = _embed_or_none(text)
    scored: list[tuple[float, str]] = []
    for g in gaps:
        content = g.get("content", "")
        if text_emb is not None:
            g_emb = _embed_or_none(content)
            if g_emb is not None:
                scored.append((cosine_sim(text_emb, g_emb), g["id"]))
                continue
        scored.append((_overlap_score(text, content), g["id"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    floor = min_sim if text_emb is not None else 0.06
    picked = [gid for sim, gid in scored[:max_n] if sim >= floor]
    if not picked and scored and scored[0][0] > 0:
        picked = [gid for _, gid in scored[: min(3, max_n)]]
    return picked


def _build_theme_resolver(themes: list[dict]):
    """Map orphan theme_ids to nearest valid theme (embedding or keyword overlap)."""
    valid = {t["id"] for t in themes}
    theme_labels = {t["id"]: t.get("label", "") for t in themes}
    theme_embs = {tid: _embed_or_none(lbl) for tid, lbl in theme_labels.items()}

    def nearest_theme(hint: str) -> str | None:
        hint_emb = _embed_or_none(hint)
        best_id, best_sim = None, 0.0
        if hint_emb is not None:
            for tid, te in theme_embs.items():
                if te is None:
                    continue
                sim = cosine_sim(hint_emb, te)
                if sim > best_sim:
                    best_sim, best_id = sim, tid
            if best_sim >= 0.32:
                return best_id
        for tid, lbl in theme_labels.items():
            sim = _overlap_score(hint, lbl)
            if sim > best_sim:
                best_sim, best_id = sim, tid
        return best_id if best_sim >= 0.06 else None

    def resolve_theme(tid: str, hint: str) -> str | None:
        if tid in valid:
            return tid
        return nearest_theme(hint)

    return resolve_theme


def _build_claim_resolver(ranked_claims: list[dict]):
    """Map orphan claim_ids to nearest valid claim (embedding or keyword overlap)."""
    valid = {c["id"] for c in ranked_claims}
    claim_texts = {c["id"]: c.get("content", "") for c in ranked_claims}
    claim_embs = {cid: _embed_or_none(txt) for cid, txt in claim_texts.items()}

    def nearest_claim(hint: str) -> str | None:
        hint_emb = _embed_or_none(hint)
        best_id, best_sim = None, 0.0
        if hint_emb is not None:
            for cid, ce in claim_embs.items():
                if ce is None:
                    continue
                sim = cosine_sim(hint_emb, ce)
                if sim > best_sim:
                    best_sim, best_id = sim, cid
            if best_sim >= 0.32:
                return best_id
        for cid, txt in claim_texts.items():
            sim = _overlap_score(hint, txt)
            if sim > best_sim:
                best_sim, best_id = sim, cid
        return best_id if best_sim >= 0.06 else None

    def resolve_claim(cid: str, hint: str) -> str | None:
        if cid in valid:
            return cid
        return nearest_claim(hint)

    return resolve_claim


def sanitize_chain_theme_refs(
    chains: list[dict],
    themes: list[dict],
    gaps: list[dict] | None = None,
) -> list[dict]:
    """
    Ensure every condition.theme_id exists in themes[].
    Orphan ids are mapped to the nearest theme; unmapped conditions are dropped.
    """
    if not themes:
        return chains

    resolve_theme = _build_theme_resolver(themes)
    gaps = gaps or []
    sanitized: list[dict] = []

    for chain in chains:
        chain = dict(chain)
        new_conds = []
        for cond in chain.get("conditions", []):
            hint = f"{cond.get('role', '')} {chain.get('conclusion', '')}"
            resolved = resolve_theme(cond.get("theme_id", ""), hint)
            if resolved:
                cond = dict(cond)
                cond["theme_id"] = resolved
                new_conds.append(cond)
        chain["conditions"] = new_conds
        if gaps and not chain.get("gap_ids"):
            chain["gap_ids"] = _score_gaps_for_text(
                f"{chain.get('conclusion', '')} {chain.get('narrative', '')}",
                gaps,
            )
        sanitized.append(chain)

    return sanitized


def sanitize_crux_theme_refs(
    cruxes: list[dict],
    themes: list[dict],
    gaps: list[dict] | None = None,
) -> list[dict]:
    """Ensure every crux theme_id exists in themes[]; drop orphans that cannot be mapped."""
    if not themes:
        return cruxes

    resolve_theme = _build_theme_resolver(themes)
    gaps = gaps or []
    sanitized: list[dict] = []

    for crux in cruxes:
        crux = dict(crux)
        new_themes: list[str] = []
        for tid in crux.get("theme_ids", []):
            resolved = resolve_theme(tid, crux.get("question", ""))
            if resolved and resolved not in new_themes:
                new_themes.append(resolved)
        crux["theme_ids"] = new_themes
        if gaps and not crux.get("gap_ids"):
            crux["gap_ids"] = _score_gaps_for_text(
                " ".join(
                    [
                        crux.get("question", ""),
                        crux.get("stakes", ""),
                        crux.get("resolution_path", ""),
                    ]
                ),
                gaps,
            )
        sanitized.append(crux)

    return sanitized


def sanitize_crux_claim_refs(cruxes: list[dict], ranked_claims: list[dict]) -> list[dict]:
    """
    Ensure every crux.claim_ids entry exists in ranked_claims (the candidate
    set the LLM was shown when generating cruxes). Orphan ids — hallucinated
    or malformed — are mapped to the nearest claim by content similarity to
    the crux's own question/stakes; ids that cannot be mapped are dropped.

    Without this, an orphan id survives into hypothesis.py's
    store.get_node(cid) lookup, which returns None and is silently skipped —
    so a crux can lose supporting evidence with no visible sign anything
    went wrong.
    """
    if not ranked_claims:
        return cruxes

    resolve_claim = _build_claim_resolver(ranked_claims)
    sanitized: list[dict] = []

    for crux in cruxes:
        crux = dict(crux)
        hint = " ".join([crux.get("question", ""), crux.get("stakes", "")])
        new_claim_ids: list[str] = []
        for cid in crux.get("claim_ids", []):
            resolved = resolve_claim(cid, hint)
            if resolved and resolved not in new_claim_ids:
                new_claim_ids.append(resolved)
        crux["claim_ids"] = new_claim_ids
        sanitized.append(crux)

    return sanitized


def repair_compiled_index(index: dict) -> dict:
    """
    Post-process crystallize output:
    - Map hallucinated theme_ids to nearest valid theme (embedding or keyword overlap).
    - Drop conditions that cannot be mapped.
    - Fill empty gap_ids on cruxes/chains via match to index gaps.
    - Map hallucinated crux claim_ids to nearest valid claim (embedding or
      keyword overlap against ranked_claims); drop ids that cannot be mapped.
    """
    themes = index.get("themes", [])
    gaps = index.get("gaps", [])
    ranked_claims = index.get("ranked_claims", [])

    repaired = dict(index)
    cruxes = index.get("cruxes", [])

    if themes:
        repaired["chains"] = sanitize_chain_theme_refs(index.get("chains", []), themes, gaps)
        cruxes = sanitize_crux_theme_refs(cruxes, themes, gaps)

    repaired["cruxes"] = sanitize_crux_claim_refs(cruxes, ranked_claims)

    return repaired


def _summarize_gaps(store: GraphStore) -> list[dict]:
    return [
        {
            "id": g["id"],
            "content": g["content"],
            "needs_review": g.get("needs_review", False),
        }
        for g in store.get_nodes_by_type("gap")
    ]


def _extract_chains(case: str, cache: Cache, ranked_claims, themes, gaps) -> list[dict]:
    payload = {"ranked_claims": ranked_claims, "themes": themes, "gaps": gaps}
    gap_id = f"crystallize::chains::{case}::{content_hash(payload)}"
    result = cache.get_or_run(
        "agent", gap_id,
        lambda: call_llm(
            CRYSTALLIZE_CHAINS.format(
                case=case,
                ranked_claims=json.dumps(ranked_claims, ensure_ascii=False),
                themes=json.dumps(themes, ensure_ascii=False),
                gaps=json.dumps(gaps, ensure_ascii=False),
            ),
            model=MODEL_SMART,
            max_tokens=2000,
            parse_json=True,
            label="crystallize_chains",
        ),
    )
    return result.get("chains", []) if isinstance(result, dict) else []


def _extract_cruxes(case: str, cache: Cache, ranked_claims, themes, chains, gaps) -> list[dict]:
    payload = {"ranked_claims": ranked_claims, "themes": themes, "chains": chains, "gaps": gaps}
    gap_id = f"crystallize::cruxes::{case}::{content_hash(payload)}"
    result = cache.get_or_run(
        "agent", gap_id,
        lambda: call_llm(
            CRYSTALLIZE_CRUXES.format(
                case=case,
                ranked_claims=json.dumps(ranked_claims, ensure_ascii=False),
                themes=json.dumps(themes, ensure_ascii=False),
                chains=json.dumps(chains, ensure_ascii=False),
                gaps=json.dumps(gaps, ensure_ascii=False),
            ),
            model=MODEL_SMART,
            max_tokens=2000,
            parse_json=True,
            label="crystallize_cruxes",
        ),
    )
    return result.get("cruxes", []) if isinstance(result, dict) else []


def run_crystallize(case: str, cache: Cache, store: GraphStore) -> dict:
    """
    Build compiled Epistemic Index from raw graph.
    Returns the index dict and saves to cases/{case}/compiled/index.json.
    """
    stats = store.stats()
    print(f"  Raw graph: {stats}")

    print("  Clustering presuppositions into themes...")
    themes = build_themes(store)
    print(f"  -> {len(themes)} themes (from {stats['by_type'].get('presupposition', 0)} presuppositions)")

    print("  Ranking claims by centrality...")
    ranked_claims = rank_claims(store)
    print(f"  -> top {len(ranked_claims)} claims")

    claim_clusters = cluster_claims(store)
    print(f"  -> {len(claim_clusters)} claim clusters (for structure reference)")

    gaps = _summarize_gaps(store)

    print("  Extracting conditional chains (LLM)...")
    chains = _extract_chains(case, cache, ranked_claims, themes, gaps)
    chains = sanitize_chain_theme_refs(chains, themes, gaps)
    print(f"  -> {len(chains)} chains")

    print("  Extracting cruxes (LLM)...")
    cruxes = _extract_cruxes(case, cache, ranked_claims, themes, chains, gaps)
    cruxes = sanitize_crux_theme_refs(cruxes, themes, gaps)
    print(f"  -> {len(cruxes)} cruxes")

    index = repair_compiled_index({
        "case": case,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "themes": themes,
        "ranked_claims": ranked_claims,
        "claim_clusters": claim_clusters,
        "chains": chains,
        "cruxes": cruxes,
        "gaps": gaps,
    })

    claim_refs_before = sum(len(c.get("claim_ids", [])) for c in cruxes)
    claim_refs_after = sum(len(c.get("claim_ids", [])) for c in index.get("cruxes", []))
    if claim_refs_after != claim_refs_before:
        print(f"  Crux claim_id sanitizer: {claim_refs_before} -> {claim_refs_after} refs (orphans resolved/dropped)")

    path = save_compiled(case, index)
    print(f"  Compiled index saved: {path}")
    print(f"  Index size: {len(themes)} themes, {len(chains)} chains, {len(cruxes)} cruxes")

    from episteme.compile.source_importance import run_source_importance
    from episteme.config import CASES_DIR
    import json as _json

    roles_path = CASES_DIR / case / "compiled" / "source_roles.json"
    roles = _json.loads(roles_path.read_text(encoding="utf-8")) if roles_path.exists() else None
    run_source_importance(case, store, roles=roles)

    return index


def compiled_summary_for_llm(index: dict) -> str:
    """Compact JSON string for LLM prompts — excludes member_ids lists where huge."""
    compact = {
        "case": index.get("case"),
        "stats": index.get("stats"),
        "themes": [
            {k: v for k, v in t.items() if k != "member_ids"}
            for t in index.get("themes", [])
        ],
        "ranked_claims": index.get("ranked_claims", []),
        "chains": index.get("chains", []),
        "cruxes": index.get("cruxes", []),
        "gaps": index.get("gaps", []),
    }
    return json.dumps(compact, ensure_ascii=False)
