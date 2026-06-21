"""Per-source thesis extraction — academic workflow spine."""

import json
from pathlib import Path

from episteme.config import CASES_DIR, MODEL_SMART, MAX_TOKENS_SMART
from episteme.core.cache import Cache
from episteme.core.llm import call_llm
from episteme.prompts.extraction import SOURCE_THESIS


def theses_dir(case: str) -> Path:
    d = CASES_DIR / case / "profiles" / "theses"
    d.mkdir(parents=True, exist_ok=True)
    return d


def thesis_path(case: str, source_id: str) -> Path:
    safe = source_id.replace("/", "_").replace("\\", "_")[:80]
    return theses_dir(case) / f"{safe}.json"


def load_source_thesis(case: str, source_id: str) -> dict | None:
    path = thesis_path(case, source_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_source_thesis(case: str, source_id: str, thesis: dict) -> Path:
    path = thesis_path(case, source_id)
    path.write_text(json.dumps(thesis, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_source_thesis(
    case: str,
    cache: Cache,
    source: dict,
    source_id: str,
    raw: str,
    case_profile: dict,
    trust: dict,
) -> dict:
    """1 LLM call per source, cached."""
    existing = load_source_thesis(case, source_id)
    if existing and not cache.reset:
        return existing

    label = source.get("local_path") or source.get("url") or source_id
    cache_id = f"source_thesis::{source_id}"

    thesis = cache.get_or_run(
        "profiles",
        cache_id,
        lambda: call_llm(
            SOURCE_THESIS.format(
                case_profile=json.dumps(case_profile, ensure_ascii=False),
                source_id=source_id,
                author=source.get("author", ""),
                label=label,
                source_type=trust.get("source_type", "unknown"),
                text=raw[:6000] if raw else "",
            ),
            model=MODEL_SMART,
            max_tokens=MAX_TOKENS_SMART,
            parse_json=True,
            label="source_thesis",
        ),
    )

    if isinstance(thesis, dict) and not thesis.get("parse_error"):
        thesis["source_id"] = source_id
        save_source_thesis(case, source_id, thesis)
        print(f"    Thesis: {thesis.get('main_thesis', '?')[:80]}")
        return thesis

    fallback = {
        "source_id": source_id,
        "main_thesis": "(thesis extraction failed)",
        "sub_theses": [],
        "methodological_stance": "",
        "targets": [],
        "key_questions_addressed": [],
        "textual_anchors": [],
    }
    save_source_thesis(case, source_id, fallback)
    return fallback
