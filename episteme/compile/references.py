"""Collect node IDs referenced by compiled index (themes, chains, cruxes)."""

from __future__ import annotations


def _themes_by_id(compiled: dict) -> dict[str, dict]:
    return {t["id"]: t for t in compiled.get("themes", [])}


def collect_referenced_ids(compiled: dict, *, all_themes: bool = False) -> set[str]:
    """
    IDs anchored in the compiled epistemic structure.

    Default (all_themes=False): only presuppositions in themes linked by
    chain conditions or crux theme_ids (~150-200 presups for covid_small).

    all_themes=True: every theme member (~all presups — clustering covers graph).
    """
    referenced: set[str] = set()
    themes = _themes_by_id(compiled)

    active_theme_ids: set[str] = set()
    for chain in compiled.get("chains", []):
        referenced.update(chain.get("conclusion_claim_ids", []))
        referenced.update(chain.get("gap_ids", []))
        for cond in chain.get("conditions", []):
            tid = cond.get("theme_id")
            if tid:
                active_theme_ids.add(tid)

    for crux in compiled.get("cruxes", []):
        referenced.update(crux.get("claim_ids", []))
        referenced.update(crux.get("gap_ids", []))
        active_theme_ids.update(crux.get("theme_ids", []))

    for rc in compiled.get("ranked_claims", []):
        referenced.add(rc.get("id", ""))

    theme_ids_to_expand = set(themes.keys()) if all_themes else active_theme_ids

    for tid in theme_ids_to_expand:
        if tid not in themes:
            continue
        referenced.update(themes[tid].get("member_ids", []))
        referenced.add(themes[tid].get("medoid_id", ""))

    referenced.discard("")
    return referenced


def chains_for_crux(crux: dict, compiled: dict) -> list[dict]:
    """Chains that share themes, claims, or gaps with this crux."""
    crux_themes = set(crux.get("theme_ids", []))
    crux_claims = set(crux.get("claim_ids", []))
    crux_gaps = set(crux.get("gap_ids", []))
    result = []
    for chain in compiled.get("chains", []):
        chain_themes = {c.get("theme_id") for c in chain.get("conditions", [])}
        chain_claims = set(chain.get("conclusion_claim_ids", []))
        chain_gaps = set(chain.get("gap_ids", []))
        if crux_themes & chain_themes or crux_claims & chain_claims or crux_gaps & chain_gaps:
            result.append(chain)
    return result


def crux_criticality(crux: dict, compiled: dict) -> int:
    """Higher = more critical. Count FATAL impacts in linked themes."""
    themes = _themes_by_id(compiled)
    score = 0
    for tid in crux.get("theme_ids", []):
        t = themes.get(tid, {})
        if t.get("impact") == "FATAL":
            score += 3
        elif t.get("impact") == "MAJOR":
            score += 1
        if t.get("needs_review"):
            score += 1
    return score
