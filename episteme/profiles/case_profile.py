"""Auto-generated case profile — no manual ontology."""

import json
from pathlib import Path

from episteme.config import CASES_DIR, MODEL_SMART, MAX_TOKENS_SMART
from episteme.core.cache import Cache
from episteme.core.llm import call_llm
from episteme.prompts.extraction import CASE_PROFILE


def profile_path(case: str) -> Path:
    d = CASES_DIR / case / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d / "case_profile.json"


def load_case_profile(case: str) -> dict | None:
    path = profile_path(case)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_case_profile(case: str, profile: dict) -> Path:
    path = profile_path(case)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _build_sample_text(sources: list[dict], raw_by_id: dict[str, str], max_per_source: int = 4000) -> str:
    parts = []
    for src in sources:
        sid = src.get("local_path") or src.get("url") or ""
        raw = raw_by_id.get(sid, "")
        if raw and not raw.startswith("FETCH_ERROR"):
            label = src.get("author") or sid
            parts.append(f"--- {label} ---\n{raw[:max_per_source]}")
    return "\n\n".join(parts)[:12000]


def ensure_case_profile(
    case: str,
    cache: Cache,
    sources: list[dict],
    raw_by_id: dict[str, str] | None = None,
) -> dict:
    """
    Load or generate case profile (1 LLM call per case, cached).
    """
    existing = load_case_profile(case)
    if existing and not cache.reset:
        return existing

    raw_by_id = raw_by_id or {}
    sources_meta = json.dumps(
        [
            {
                "author": s.get("author"),
                "url": s.get("url"),
                "local_path": s.get("local_path"),
                "content_type": s.get("content_type"),
            }
            for s in sources
        ],
        ensure_ascii=False,
        indent=2,
    )
    sample = _build_sample_text(sources, raw_by_id)

    cache_id = f"case_profile::{case}"
    profile = cache.get_or_run(
        "profiles",
        cache_id,
        lambda: call_llm(
            CASE_PROFILE.format(
                case=case,
                sources_meta=sources_meta,
                sample_text=sample or "(no sample text available)",
            ),
            model=MODEL_SMART,
            max_tokens=MAX_TOKENS_SMART,
            parse_json=True,
            label="case_profile",
        ),
    )

    if isinstance(profile, dict) and not profile.get("parse_error"):
        profile["case"] = case
        save_case_profile(case, profile)
        print(f"  Case profile: {len(profile.get('subfields', []))} subfields, "
              f"{len(profile.get('key_entities', []))} entities")
        return profile

    print("  Warning: case profile generation failed, using minimal fallback")
    fallback = {
        "case": case,
        "central_questions": [f"What are the main contested claims in {case}?"],
        "subfields": [case],
        "key_entities": [],
        "debate_positions": [],
    }
    save_case_profile(case, fallback)
    return fallback
