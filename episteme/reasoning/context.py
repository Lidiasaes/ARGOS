"""Build reasoning context from compiled index + graph (not raw text)."""

from __future__ import annotations

import json
from pathlib import Path

from episteme.compile.crystallize import load_compiled
from episteme.compile.debate_state import load_debate_state
from episteme.core.graph import (
    GraphStore,
    ensure_attestations,
    group_attestations_by_source,
    unique_attestation_source_count,
)
from episteme.methodology.criteria import load_methodology_profile
from episteme.methodology.paths import audits_dir
from episteme.profiles.case_profile import load_case_profile

MAX_SETTLED = 15
MAX_CONTESTED = 15
MAX_DEVILS_TARGETS = 8


def _claim_bundle(node: dict, debate_by_id: dict[str, dict] | None = None) -> dict:
    atts = ensure_attestations(node)
    sources = group_attestations_by_source(atts)
    bundle = {
        "id": node["id"],
        "content": node.get("content", ""),
        "type": node.get("type"),
        "evidential_weight": node.get("evidential_weight", 0),
        "subfield": node.get("subfield", ""),
        "source_count": unique_attestation_source_count(atts),
        "attestors": [
            {
                "source_id": s.get("source_id"),
                "author": (s.get("author") or "")[:120],
                "quote_count": s.get("count", 1),
            }
            for s in sources
        ],
    }
    if debate_by_id:
        d = debate_by_id.get(node["id"], {})
        if d.get("supported_by"):
            bundle["supported_by"] = [
                {"id": x.get("claim_id"), "content": (x.get("canonical") or "")[:200]}
                for x in d["supported_by"][:3]
            ]
        if d.get("contradicted_by"):
            bundle["contradicted_by"] = [
                {"id": x.get("claim_id"), "content": (x.get("canonical") or "")[:200]}
                for x in d["contradicted_by"][:3]
            ]
        if d.get("undermined_by"):
            bundle["undermined_by"] = [
                {"id": x.get("claim_id"), "content": (x.get("canonical") or "")[:200]}
                for x in d["undermined_by"][:3]
            ]
    return bundle


def _debate_by_id(case: str) -> dict[str, dict]:
    state = load_debate_state(case)
    if not state:
        return {}
    return {n["claim_id"]: n for n in state.get("nodes", [])}


def _load_audits(case: str) -> list[dict]:
    adir = audits_dir(case)
    if not adir.exists():
        return []
    out = []
    for path in sorted(adir.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _methodology_by_source(audits: list[dict]) -> dict[str, float]:
    return {a.get("source_id", ""): a.get("methodology_score", 0) for a in audits}


def domain_label(case: str) -> str:
    profile = load_methodology_profile(case)
    if profile and profile.get("domain_summary"):
        return profile["domain_summary"]
    cp = load_case_profile(case)
    if cp:
        parts = cp.get("central_questions", [])[:2]
        if parts:
            return "; ".join(parts)
    return case


def methodology_summary_text(case: str) -> str:
    audits = _load_audits(case)
    profile = load_methodology_profile(case)
    if not audits and not profile:
        return "No methodology audits available."
    scores = [a.get("methodology_score", 0) for a in audits]
    avg = sum(scores) / len(scores) if scores else 0
    ceiling = profile.get("confidence_ceiling", "?") if profile else "?"
    inquiry = profile.get("inquiry_type", "?") if profile else "?"
    return (
        f"Inquiry type: {inquiry}. "
        f"Average methodology score: {avg:.2f} across {len(audits)} sources. "
        f"Confidence ceiling: {ceiling}."
    )


def methodology_gaps_text(case: str) -> str:
    audits = _load_audits(case)
    profile = load_methodology_profile(case)
    lines = []
    if profile:
        missing = profile.get("guidelines_missing", "")
        if missing:
            lines.append(f"Missing standards: {missing}")
        for rf in profile.get("red_flags", [])[:5]:
            lines.append(f"Red flag pattern: {rf}")
    for audit in audits:
        label = audit.get("source_label", audit.get("source_id", ""))
        for ev in audit.get("evaluations", []):
            if ev.get("status") in ("not_declared", "red_flag"):
                lines.append(
                    f"[{label}] {ev.get('criterion_id')}: {ev.get('reviewer_note', '')[:120]}"
                )
        for hit in audit.get("red_flag_hits", [])[:2]:
            lines.append(f"[{label}] {hit.get('pattern', '')}: {hit.get('reviewer_note', '')[:100]}")
    return "\n".join(lines[:20]) or "No major methodology gaps flagged."


def methodology_score_avg(case: str) -> float:
    audits = _load_audits(case)
    if not audits:
        return 0.0
    scores = [a.get("methodology_score", 0) for a in audits]
    return sum(scores) / len(scores)


def confidence_ceiling(case: str) -> float | str:
    profile = load_methodology_profile(case)
    if profile:
        return profile.get("confidence_ceiling", "?")
    return "?"


def _is_contested(node_id: str, debate_by_id: dict[str, dict]) -> bool:
    d = debate_by_id.get(node_id, {})
    return bool(d.get("contradicted_by") or d.get("undermined_by"))


def classify_claims(case: str, store: GraphStore) -> tuple[list[dict], list[dict]]:
    """Return (settled, contested) claim bundles from graph + compiled index."""
    index = load_compiled(case)
    debate_by_id = _debate_by_id(case)
    settled_ids: list[str] = []
    contested_ids: set[str] = set()

    for n in (load_debate_state(case) or {}).get("nodes", []):
        if unique_attestation_source_count(n.get("attestations", [])) > 1:
            if not n.get("contradicted_by"):
                settled_ids.append(n["claim_id"])
        if n.get("contradicted_by"):
            contested_ids.add(n["claim_id"])
            for c in n["contradicted_by"]:
                contested_ids.add(c.get("claim_id", ""))

    if index:
        ranked = {rc["id"]: rc for rc in index.get("ranked_claims", [])}
        for cid in sorted(
            ranked.keys(),
            key=lambda i: (-ranked[i].get("centrality", 0), -ranked[i].get("evidential_weight", 0)),
        ):
            node = store.get_node(cid)
            if not node or node.get("type") not in ("claim", "evidence"):
                continue
            if _is_contested(cid, debate_by_id):
                contested_ids.add(cid)
            elif cid not in settled_ids and node.get("evidential_weight", 0) >= 0.65:
                settled_ids.append(cid)

        for crux in index.get("cruxes", []):
            for cid in crux.get("claim_ids", []):
                if _is_contested(cid, debate_by_id):
                    contested_ids.add(cid)
                elif cid not in settled_ids and cid not in contested_ids:
                    node = store.get_node(cid)
                    if node and node.get("evidential_weight", 0) >= 0.5:
                        contested_ids.add(cid)

    settled_ids = [i for i in settled_ids if i and i not in contested_ids]
    seen = set()
    settled_out = []
    for cid in settled_ids:
        if cid in seen:
            continue
        node = store.get_node(cid)
        if node:
            seen.add(cid)
            settled_out.append(_claim_bundle(node, debate_by_id))
        if len(settled_out) >= MAX_SETTLED:
            break

    contested_out = []
    for cid in contested_ids:
        if not cid or cid in seen:
            continue
        node = store.get_node(cid)
        if node:
            contested_out.append(_claim_bundle(node, debate_by_id))
        if len(contested_out) >= MAX_CONTESTED:
            break

    return settled_out, contested_out


def active_contradictions(case: str, store: GraphStore) -> list[dict]:
    """Pairs of contradicting claims with rationale."""
    out = []
    for n in (load_debate_state(case) or {}).get("nodes", []):
        for c in n.get("contradicted_by", []):
            rel = c.get("relation", {})
            out.append({
                "claim_a": n["claim_id"],
                "content_a": (n.get("canonical") or "")[:200],
                "claim_b": c.get("claim_id"),
                "content_b": (c.get("canonical") or "")[:200],
                "strength": rel.get("strength"),
                "rationale": rel.get("rationale", ""),
            })
    return out


def devils_advocate_targets(settled: list[dict]) -> list[dict]:
    """Top settled claims for skeptical review — multi-source first."""
    ranked = sorted(
        settled,
        key=lambda c: (-c.get("source_count", 0), -c.get("evidential_weight", 0)),
    )
    return ranked[:MAX_DEVILS_TARGETS]


def attestors_for_claim(node: dict) -> str:
    atts = ensure_attestations(node)
    groups = group_attestations_by_source(atts)
    lines = []
    for g in groups:
        author = (g.get("author") or g.get("source_id") or "?")[:100]
        lines.append(f"- {author} ({g.get('count', 1)} quote(s))")
    return "\n".join(lines) or "Unknown"


def methodology_scores_for_claim(node: dict, meth_by_source: dict[str, float]) -> str:
    atts = ensure_attestations(node)
    lines = []
    for att in atts:
        sid = att.get("source_id", "")
        score = meth_by_source.get(sid)
        if score is not None:
            lines.append(f"- {sid}: {score:.2f}")
    return "\n".join(lines) or "No methodology scores for attestors."


def load_json_artifact(path: Path) -> list | dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
