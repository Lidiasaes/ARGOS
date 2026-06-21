"""Deterministic methodology score from audit evaluations."""

SEVERITY_WEIGHTS = {"FATAL": 3.0, "MAJOR": 2.0, "MINOR": 1.0}
# Extra pattern hits (informational); capped so verbose LLM output cannot zero the score alone
RED_FLAG_HIT_PENALTY = 0.01
MAX_RED_FLAG_HIT_PENALTY = 0.05


def compute_methodology_score(audit: dict, profile: dict) -> float:
    """
    Weighted fraction of applicable criteria that are 'declared'.
    red_flag / not_declared earn 0 for that criterion (still in denominator).
    Small capped penalty for profile red_flag_hits (patterns in text).
    Capped at profile confidence_ceiling.
    """
    criteria_map = {c["id"]: c for c in profile.get("criteria", [])}
    ceiling = float(profile.get("confidence_ceiling", 1.0))

    applicable = [
        e for e in audit.get("evaluations", [])
        if e.get("status") != "not_applicable"
    ]
    if not applicable:
        return 0.0

    earned = 0.0
    total_weight = 0.0

    for ev in applicable:
        crit = criteria_map.get(ev.get("criterion_id", ""), {})
        w = SEVERITY_WEIGHTS.get(crit.get("severity", "MINOR"), 1.0)
        total_weight += w
        if ev.get("status") == "declared":
            earned += w

    base = earned / total_weight if total_weight > 0 else 0.0
    hit_penalty = min(
        len(audit.get("red_flag_hits", [])) * RED_FLAG_HIT_PENALTY,
        MAX_RED_FLAG_HIT_PENALTY,
    )
    raw = max(0.0, base - hit_penalty)
    return round(min(raw, ceiling), 3)


def score_breakdown(audit: dict, profile: dict) -> dict:
    criteria_map = {c["id"]: c for c in profile.get("criteria", [])}
    counts = {"declared": 0, "not_declared": 0, "red_flag": 0, "not_applicable": 0}
    weighted_declared = 0.0
    weighted_total = 0.0

    for ev in audit.get("evaluations", []):
        status = ev.get("status", "")
        if status in counts:
            counts[status] += 1
        if status == "not_applicable":
            continue
        crit = criteria_map.get(ev.get("criterion_id", ""), {})
        w = SEVERITY_WEIGHTS.get(crit.get("severity", "MINOR"), 1.0)
        weighted_total += w
        if status == "declared":
            weighted_declared += w

    ceiling = float(profile.get("confidence_ceiling", 1.0))
    applicable_n = counts["declared"] + counts["not_declared"] + counts["red_flag"]
    score = compute_methodology_score(audit, profile)
    base = weighted_declared / weighted_total if weighted_total > 0 else 0.0

    return {
        "applicable": applicable_n,
        "not_applicable": counts["not_applicable"],
        "declared": counts["declared"],
        "not_declared": counts["not_declared"],
        "red_flag_criteria": counts["red_flag"],
        "red_flag_hits": len(audit.get("red_flag_hits", [])),
        "weighted_declared": round(weighted_declared, 2),
        "weighted_total": round(weighted_total, 2),
        "base_ratio": round(base, 3),
        "confidence_ceiling": ceiling,
        "methodology_score": score,
    }


def score_rationale(audit: dict, profile: dict, score: float) -> str:
    bd = score_breakdown(audit, profile)
    return (
        f"Score {score:.3f} (deterministic): "
        f"weighted declared {bd['weighted_declared']}/{bd['weighted_total']} "
        f"(base {bd['base_ratio']}); "
        f"{bd['declared']}/{bd['applicable']} criteria declared, "
        f"{bd['not_declared']} not_declared, {bd['red_flag_criteria']} red_flag; "
        f"{bd['red_flag_hits']} pattern hits (penalty capped); "
        f"ceiling {bd['confidence_ceiling']}."
    )


def finalize_audit_scores(audit: dict, profile: dict) -> dict:
    """Apply deterministic score + breakdown; overwrites any LLM-provided score."""
    score = compute_methodology_score(audit, profile)
    audit["methodology_score"] = score
    audit["score_rationale"] = score_rationale(audit, profile, score)
    audit["score_breakdown"] = score_breakdown(audit, profile)
    audit["score_computed_by"] = "deterministic_v1"
    return audit
