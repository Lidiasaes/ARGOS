"""Methodology profile generation — CriteriaAgent."""

import json

from episteme.config import MODEL_SMART, MODEL_FAST, MAX_TOKENS_SMART
from episteme.core.cache import Cache, content_hash
from episteme.core.llm import call_llm
from episteme.methodology.domain import infer_inquiry_context, infer_inquiry_type, sources_meta_json
from episteme.methodology.paths import profile_path
from episteme.prompts.methodology import METHODOLOGY_PROFILE
from episteme.profiles.case_profile import load_case_profile


def load_methodology_profile(case: str) -> dict | None:
    path = profile_path(case)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_methodology_profile(case: str, profile: dict) -> None:
    profile["case"] = case
    profile_path(case).write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_sample_text(sources: list[dict], raw_by_id: dict[str, str], max_per_source: int = 4000) -> str:
    parts = []
    for src in sources:
        sid = src.get("local_path") or src.get("url") or ""
        raw = raw_by_id.get(sid, "")
        if raw and not raw.startswith("FETCH_ERROR"):
            label = src.get("author") or src.get("title") or sid
            parts.append(f"--- {label} ---\n{raw[:max_per_source]}")
    return "\n\n".join(parts)[:12000]


def _fallback_profile(case: str, inquiry_type: str, inquiry_context: str) -> dict:
    """Deterministic criteria when LLM profile generation fails."""
    if inquiry_type == "probabilistic_debate":
        criteria = [
            {
                "id": "base_rate_justification",
                "category": "reasoning",
                "severity": "FATAL",
                "question": "Are prior probabilities and base rates explicitly justified?",
                "expert_rationale": "Debate conclusions hinge on priors; unanchored priors make likelihood ratios uninterpretable.",
            },
            {
                "id": "likelihood_ratio_validity",
                "category": "statistics",
                "severity": "FATAL",
                "question": "Are likelihood ratios supported by cited evidence, not assertion?",
                "expert_rationale": "Bayesian updates require defensible likelihoods tied to observable data.",
            },
            {
                "id": "conditional_independence",
                "category": "reasoning",
                "severity": "MAJOR",
                "question": "Are conditional independence assumptions stated where composite updates are used?",
                "expert_rationale": "Double-counting correlated evidence inflates confidence.",
            },
            {
                "id": "evidence_traceability",
                "category": "inferential",
                "severity": "MAJOR",
                "question": "Can each key evidentiary claim be traced to a primary source?",
                "expert_rationale": "Debate rhetoric without traceable evidence should not drive scores.",
            },
            {
                "id": "conflict_of_interest",
                "category": "other",
                "severity": "MINOR",
                "question": "Are conflicts of interest or institutional stakes disclosed?",
                "expert_rationale": "Adversarial debate contexts require transparency on incentives.",
            },
            {
                "id": "uncertainty_acknowledgment",
                "category": "reasoning",
                "severity": "MAJOR",
                "question": "Are limits of evidence and model uncertainty explicitly acknowledged?",
                "expert_rationale": "Overconfident verdicts without uncertainty bounds are methodological failures.",
            },
        ]
        red_flags = [
            "certainty without cited evidence",
            "prior assumed without justification",
            "double-counting the same observation",
        ]
        ceiling = 0.5
        std = "low"
    else:
        criteria = [
            {
                "id": "sample_design",
                "category": "sample_design",
                "severity": "FATAL",
                "question": "Is the sample design and size reported?",
                "expert_rationale": "Without sample design, effect estimates cannot be weighted.",
            },
            {
                "id": "methods_transparency",
                "category": "other",
                "severity": "MAJOR",
                "question": "Are core methods and analysis steps described?",
                "expert_rationale": "Opaque methods block independent assessment.",
            },
            {
                "id": "uncertainty_acknowledgment",
                "category": "reasoning",
                "severity": "MAJOR",
                "question": "Are limitations and uncertainty acknowledged?",
                "expert_rationale": "Credible science states what is not established.",
            },
        ]
        red_flags = ["methods not described", "no sample size"]
        ceiling = 0.6
        std = "medium"

    return {
        "case": case,
        "inquiry_type": inquiry_type,
        "domain_summary": inquiry_context.split("\n")[0] if inquiry_context else case,
        "standardization_level": std,
        "applicable_guidelines": [],
        "guidelines_missing": "No field-wide CONSORT/PRISMA equivalent — criteria from inquiry-type template",
        "confidence_ceiling": ceiling,
        "criteria": criteria,
        "red_flags": red_flags,
        "profile_source": "fallback_template",
    }


def ensure_methodology_profile(
    case: str,
    cache: Cache,
    sources: list[dict],
    raw_by_id: dict[str, str],
    force: bool = False,
) -> dict:
    if not force and not cache.reset:
        existing = load_methodology_profile(case)
        if existing:
            return existing

    case_profile = load_case_profile(case) or {}
    inquiry_context = infer_inquiry_context(case, sources, case_profile)
    sample = _build_sample_text(sources, raw_by_id)

    profile_payload = {
        "subfields": case_profile.get("subfields", []),
        "key_entities": case_profile.get("key_entities", []),
    }
    cache_id = f"methodology_profile::{case}::{content_hash(profile_payload)}"
    result = cache.get_or_run(
        "profiles",
        cache_id,
        lambda: call_llm(
            METHODOLOGY_PROFILE.format(
                case=case,
                inquiry_context=inquiry_context,
                sources_meta=sources_meta_json(sources),
                case_profile=json.dumps(case_profile, ensure_ascii=False, indent=2),
                sample_text=sample or "(no sample text)",
            ),
            model=MODEL_SMART,
            max_tokens=MAX_TOKENS_SMART,
            parse_json=True,
            label="methodology_profile",
        ),
    )

    if isinstance(result, dict) and not result.get("parse_error"):
        result["profile_source"] = "llm"
        save_methodology_profile(case, result)
        n_crit = len(result.get("criteria", []))
        print(f"  Methodology profile: {result.get('inquiry_type')} | "
              f"{result.get('standardization_level')} | {n_crit} criteria")
        return result

    # Retry with fast model + smaller output
    print("  Warning: methodology profile failed (smart) — retrying with Haiku...")
    retry = call_llm(
        METHODOLOGY_PROFILE.format(
            case=case,
            inquiry_context=inquiry_context,
            sources_meta=sources_meta_json(sources),
            case_profile=json.dumps(case_profile, ensure_ascii=False, indent=2),
            sample_text=sample or "(no sample text)",
        ),
        model=MODEL_FAST,
        max_tokens=2500,
        parse_json=True,
        label="methodology_profile_retry",
    )
    if isinstance(retry, dict) and not retry.get("parse_error") and retry.get("criteria"):
        retry["profile_source"] = "llm_retry"
        save_methodology_profile(case, retry)
        print(f"  Methodology profile (retry): {len(retry.get('criteria', []))} criteria")
        return retry

    print("  Warning: methodology profile generation failed — using inquiry-type template")
    inquiry_type = infer_inquiry_type(case, sources, case_profile)
    fallback = _fallback_profile(case, inquiry_type, inquiry_context)
    save_methodology_profile(case, fallback)
    return fallback
