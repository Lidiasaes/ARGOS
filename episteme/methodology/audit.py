"""Per-source methodology audit — MethodologyAuditor."""

import json

from episteme.config import MODEL_SMART, MIN_QUOTE_CHARS
from episteme.core.cache import Cache, content_hash
from episteme.core.llm import call_llm
from episteme.filters.quote_gate import quote_in_chunk
from episteme.methodology.extract import extract_methods_text
from episteme.methodology.paths import audit_path
from episteme.methodology.scoring import finalize_audit_scores
from episteme.prompts.methodology import METHODOLOGY_AUDIT


def _source_label(source: dict) -> str:
    return (
        source.get("title")
        or source.get("author")
        or source.get("local_path")
        or source.get("url")
        or "unknown"
    )


def _verify_quotes(audit: dict, methods_text: str) -> dict:
    """Strip or flag quotes not found in auditable text."""
    for ev in audit.get("evaluations", []):
        quote = ev.get("evidence_quote")
        if quote and isinstance(quote, str) and len(quote.strip()) >= MIN_QUOTE_CHARS:
            if not quote_in_chunk(quote, methods_text):
                ev["quote_verified"] = False
                ev["reviewer_note"] = (ev.get("reviewer_note") or "") + " [quote not verified in source text]"
            else:
                ev["quote_verified"] = True
        else:
            ev["quote_verified"] = None

    for hit in audit.get("red_flag_hits", []):
        quote = hit.get("evidence_quote")
        if quote and isinstance(quote, str) and len(quote.strip()) >= MIN_QUOTE_CHARS:
            hit["quote_verified"] = quote_in_chunk(quote, methods_text)
        else:
            hit["quote_verified"] = None

    return audit


def load_audit(case: str, source_id: str) -> dict | None:
    path = audit_path(case, source_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_audit(case: str, source_id: str, audit: dict) -> None:
    audit_path(case, source_id).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def audit_source(
    case: str,
    cache: Cache,
    source: dict,
    source_id: str,
    raw: str,
    methodology_profile: dict,
    force: bool = False,
) -> dict:
    if not force and not cache.reset:
        existing = load_audit(case, source_id)
        if existing:
            return finalize_audit_scores(existing, methodology_profile)

    methods_text = extract_methods_text(raw, source)
    if not methods_text.strip():
        empty = {
            "source_id": source_id,
            "source_label": _source_label(source),
            "methodology_score": 0.0,
            "score_rationale": "No auditable methodology text extracted",
            "evaluations": [],
            "red_flag_hits": [],
        }
        save_audit(case, source_id, empty)
        return empty

    label = _source_label(source)
    criteria_payload = [
        {"id": c.get("id"), "severity": c.get("severity")}
        for c in methodology_profile.get("criteria", [])
    ]
    cache_key = f"methodology_audit::{source_id}::{content_hash(criteria_payload)}"

    result = cache.get_or_run(
        "agent",
        cache_key,
        lambda: call_llm(
            METHODOLOGY_AUDIT.format(
                methodology_profile=json.dumps(methodology_profile, ensure_ascii=False, indent=2),
                source_id=source_id,
                author=source.get("author", ""),
                source_label=label,
                publication_status=source.get("publication_status", "unknown"),
                methods_text=methods_text[:16000],
            ),
            model=MODEL_SMART,
            max_tokens=4000,
            parse_json=True,
            label="methodology_audit",
        ),
    )

    if not isinstance(result, dict) or result.get("parse_error"):
        failed = {
            "source_id": source_id,
            "source_label": label,
            "methodology_score": 0.0,
            "score_rationale": "Audit LLM call failed",
            "evaluations": [],
            "red_flag_hits": [],
            "parse_error": True,
        }
        save_audit(case, source_id, failed)
        return failed

    result["source_id"] = source_id
    result["source_label"] = label
    result = _verify_quotes(result, methods_text)
    result = finalize_audit_scores(result, methodology_profile)
    save_audit(case, source_id, result)
    return result
