"""Second-pass Haiku verifier for hypothesis study_parameters grounding."""

from __future__ import annotations

import json
import re

from episteme.config import MODEL_FAST
from episteme.compile.crystallize import load_compiled
from episteme.core.cache import Cache, content_hash
from episteme.core.llm import call_llm
from episteme.pipeline.sources import get_content, load_sources, source_id
from episteme.prompts.hypothesis import HYPOTHESIS_VERIFIER

MAX_CORPUS_CHARS = 100_000
MAX_PER_SOURCE = 40_000


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def quote_in_corpus(quote: str, corpus: str) -> bool:
    """Literal substring check with whitespace normalization."""
    if not quote or not corpus:
        return False
    return _normalize_text(quote) in _normalize_text(corpus)


def _external_reference_specific(ref: str) -> bool:
    """Heuristic: title-like length + edition/year marker."""
    ref = (ref or "").strip()
    if len(ref) < 20:
        return False
    vague = (
        "standard practice",
        "field convention",
        "textbook",
        "generally accepted",
        "widely used",
        "common practice",
    )
    lower = ref.lower()
    if any(v in lower for v in vague) and not re.search(r"\d{4}|2nd|3rd|edition|ed\.", lower):
        return False
    return bool(re.search(r"\d{4}|edition|ed\.|vol\.|chapter|section|iso |who |cohen", lower))


def _deterministic_overrides(params: list[dict], corpus: str) -> tuple[list[dict], list[dict]]:
    """Apply hard rules before/alongside Haiku — reliable literal quote matching."""
    updated = []
    overrides = []

    for p in params:
        entry = dict(p)
        original = entry.get("grounding", "")
        new_grounding = original
        reason = ""

        if original == "from_evidence":
            quote = entry.get("attestation_quote") or ""
            if not quote_in_corpus(quote, corpus):
                new_grounding = "ungrounded"
                reason = (
                    "attestation_quote not found literally in source texts"
                    if quote
                    else "attestation_quote missing"
                )
        elif original == "external_standard":
            ref = entry.get("external_reference") or ""
            if not _external_reference_specific(ref):
                new_grounding = "ungrounded"
                reason = "external_reference lacks specific title and edition/version"
        elif original not in ("from_evidence", "external_standard"):
            new_grounding = "ungrounded"
            reason = f"grounding '{original}' is not verifiable — only from_evidence and external_standard allowed"

        if new_grounding != original:
            entry["grounding"] = new_grounding
            overrides.append({
                "parameter": entry.get("parameter", "?"),
                "from_grounding": original,
                "to_grounding": new_grounding,
                "reason": reason,
            })

        updated.append(entry)

    return updated, overrides


def load_case_source_corpus(case: str, cache: Cache) -> tuple[str, bool]:
    """Concatenate cached raw source bodies for verifier prompts."""
    sources = load_sources(case)
    parts: list[str] = []
    truncated = False
    budget = MAX_CORPUS_CHARS

    for source in sources:
        sid = source_id(source)
        if not sid:
            continue
        raw = cache.get_or_run("raw", sid, lambda s=source: get_content(s))
        if not raw or raw.startswith("FETCH_ERROR"):
            continue
        label = source.get("title") or source.get("author") or sid
        chunk = raw[:MAX_PER_SOURCE]
        if len(raw) > MAX_PER_SOURCE:
            truncated = True
        block = f"=== SOURCE: {label} ({sid}) ===\n{chunk}"
        if len(block) > budget:
            block = block[:budget]
            truncated = True
            parts.append(block)
            break
        parts.append(block)
        budget -= len(block) + 2

    return "\n\n".join(parts), truncated


def verify_hypothesis(
    hypothesis: dict,
    case: str,
    cache: Cache,
    *,
    use_haiku: bool = True,
) -> dict:
    """
    Verify study_parameters grounding against raw sources.
    Updates grounding fields and adds verifier_overrides.
    """
    params = hypothesis.get("study_parameters")
    if not isinstance(params, list) or not params:
        hypothesis["verifier_overrides"] = []
        return hypothesis

    corpus, truncated = load_case_source_corpus(case, cache)
    if not corpus.strip():
        hypothesis["verifier_overrides"] = []
        hypothesis["verifier_skipped"] = "no source corpus available"
        return hypothesis

    det_params, det_overrides = _deterministic_overrides(params, corpus)

    if not use_haiku:
        hypothesis["study_parameters"] = det_params
        hypothesis["verifier_overrides"] = det_overrides
        if truncated:
            hypothesis["verifier_corpus_truncated"] = True
        return hypothesis

    crux_id = hypothesis.get("crux_id", "unknown")
    crux = next(
        (c for c in (load_compiled(case) or {}).get("cruxes", []) if c.get("id") == crux_id),
        {},
    )
    crux_payload = {
        "question": crux.get("question", ""),
        "stakes": crux.get("stakes", ""),
        "resolution_path": crux.get("resolution_path", ""),
        "claim_ids": crux.get("claim_ids", []),
    }
    cache_key = f"hypothesis_verify::{crux_id}::{content_hash(crux_payload)}"

    payload_for_verify = dict(hypothesis)
    payload_for_verify["study_parameters"] = det_params

    verified = cache.get_or_run(
        "agent",
        cache_key,
        lambda: call_llm(
            HYPOTHESIS_VERIFIER.format(
                hypothesis_json=json.dumps(payload_for_verify, ensure_ascii=False, indent=2),
                source_corpus=corpus + ("\n\n[TRUNCATED — corpus exceeded size limit]" if truncated else ""),
            ),
            model=MODEL_FAST,
            max_tokens=2500,
            parse_json=True,
            label="hypothesis_verifier",
        ),
    )

    if not isinstance(verified, dict) or verified.get("parse_error"):
        hypothesis["study_parameters"] = det_params
        hypothesis["verifier_overrides"] = det_overrides
        hypothesis["verifier_fallback"] = "deterministic_only"
        if truncated:
            hypothesis["verifier_corpus_truncated"] = True
        return hypothesis

    haiku_params = verified.get("study_parameters")
    haiku_overrides = verified.get("verifier_overrides") or []

    if isinstance(haiku_params, list) and haiku_params:
        merged_params, merged_overrides = _merge_verifier_results(
            det_params, haiku_params, haiku_overrides, corpus
        )
        hypothesis["study_parameters"] = merged_params
        hypothesis["verifier_overrides"] = merged_overrides
    else:
        hypothesis["study_parameters"] = det_params
        hypothesis["verifier_overrides"] = det_overrides

    if truncated:
        hypothesis["verifier_corpus_truncated"] = True

    return hypothesis


def _merge_verifier_results(
    det_params: list[dict],
    haiku_params: list[dict],
    haiku_overrides: list[dict],
    corpus: str,
) -> tuple[list[dict], list[dict]]:
    """Prefer Haiku judgment but re-apply deterministic quote guardrails."""
    by_name = {p.get("parameter"): p for p in haiku_params if p.get("parameter")}
    merged: list[dict] = []
    overrides: list[dict] = []
    seen_override_keys: set[tuple[str, str, str]] = set()

    for base in det_params:
        name = base.get("parameter", "")
        entry = dict(by_name.get(name, base))
        original = base.get("grounding", "")
        final = entry.get("grounding", original)

        if final == "from_evidence":
            quote = entry.get("attestation_quote") or base.get("attestation_quote") or ""
            if not quote_in_corpus(quote, corpus):
                final = "ungrounded"

        if final != original:
            key = (name, original, final)
            if key not in seen_override_keys and final != original:
                seen_override_keys.add(key)
                overrides.append({
                    "parameter": name,
                    "from_grounding": original,
                    "to_grounding": final,
                    "reason": _override_reason(original, final, entry, haiku_overrides, name),
                })

        entry["grounding"] = final
        merged.append(entry)

    for ov in haiku_overrides:
        key = (ov.get("parameter", ""), ov.get("from_grounding", ""), ov.get("to_grounding", ""))
        if (
            key not in seen_override_keys
            and all(key)
            and ov.get("from_grounding") != ov.get("to_grounding")
        ):
            seen_override_keys.add(key)
            overrides.append(ov)

    return merged, overrides


def _override_reason(
    original: str,
    final: str,
    entry: dict,
    haiku_overrides: list[dict],
    name: str,
) -> str:
    for ov in haiku_overrides:
        if ov.get("parameter") == name and ov.get("to_grounding") == final:
            return ov.get("reason", "")
    if final == "ungrounded" and original == "from_evidence":
        return "attestation_quote not found literally in source texts"
    if final == "ungrounded" and original == "external_standard":
        return "external_reference lacks specific title and edition/version"
    if final == "ungrounded":
        return f"grounding '{original}' failed verification"
    return "verifier updated grounding"
