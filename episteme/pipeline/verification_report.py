"""Verification report — aggregates grounding/verification quality across all hypotheses for a case."""

import json
from pathlib import Path

from episteme.config import CASES_DIR
from episteme.compile.crystallize import load_compiled


def verification_report_path(case: str) -> Path:
    return CASES_DIR / case / "compiled" / "verification_report.json"


def _crux_claim_counts(case: str) -> dict[str, int]:
    """claim_ids count per crux, post-sanitization, from the compiled index."""
    index = load_compiled(case)
    if not index:
        return {}
    return {c.get("id", ""): len(c.get("claim_ids", [])) for c in index.get("cruxes", [])}


def _param_breakdown(hypotheses: list[dict]) -> dict:
    counts = {"from_evidence": 0, "external_standard": 0, "ungrounded": 0, "other": 0}
    for h in hypotheses:
        for p in h.get("study_parameters", []) or []:
            g = p.get("grounding", "")
            if g in counts:
                counts[g] += 1
            else:
                counts["other"] += 1
    return counts


def build_verification_report(case: str, hypotheses: list[dict]) -> dict:
    """
    Aggregate grounding quality across all saved hypotheses for a case.
    Pure summary over already-saved hypothesis files + the compiled index —
    does not call the LLM or re-verify anything, just summarizes what
    run_hypothesis + verify_hypothesis already produced.
    """
    claim_counts = _crux_claim_counts(case)
    param_breakdown = _param_breakdown(hypotheses)
    total_params = sum(param_breakdown.values())

    overrides = []
    truncated_cruxes = []
    zero_evidence_cruxes = []
    unverified_invented = []

    for h in hypotheses:
        cid = h.get("crux_id", "unknown")

        for ov in h.get("verifier_overrides") or []:
            overrides.append({"crux_id": cid, **ov})

        if h.get("verifier_corpus_truncated"):
            truncated_cruxes.append(cid)

        if claim_counts.get(cid, 0) == 0:
            zero_evidence_cruxes.append(cid)

        invented = h.get("invented_or_unverified") or []
        if invented:
            unverified_invented.append({"crux_id": cid, "items": invented})

    grounded = param_breakdown["from_evidence"] + param_breakdown["external_standard"]
    return {
        "case": case,
        "hypotheses_count": len(hypotheses),
        "study_parameters": {
            "total": total_params,
            "breakdown": param_breakdown,
            "grounded_pct": round(100 * grounded / total_params, 1) if total_params else None,
        },
        "verifier_overrides": {
            "total": len(overrides),
            "details": overrides,
        },
        "corpus_truncated_cruxes": truncated_cruxes,
        "zero_claim_evidence_cruxes": zero_evidence_cruxes,
        "self_reported_invented_or_unverified": unverified_invented,
        "verification_scope": {
            "verified": [
                "study_parameters grounding: from_evidence quotes are checked literally against the source corpus; external_standard references are checked for a specific title and edition/year.",
            ],
            "not_verified": [
                "Crux stakes and resolution_path are LLM synthesis produced by crystallize and are not checked against the source corpus by any process in this pipeline. Treat them as framing, not as verified fact.",
            ],
        },
    }


def save_verification_report(case: str, report: dict) -> Path:
    path = verification_report_path(case)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
