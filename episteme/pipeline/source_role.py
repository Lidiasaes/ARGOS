"""Structural source role — metadata rules + optional Haiku for ambiguous cases."""

from __future__ import annotations

from episteme.config import MODEL_FAST
from episteme.core.cache import Cache
from episteme.core.llm import call_llm
from episteme.prompts.source_role import SOURCE_ROLE_CLASSIFIER

VALID_ROLES = frozenset({
    "primary_research",
    "review",
    "commentary",
    "debate_transcript",
    "judge_decision",
    "rebuttal",
    "unknown",
})

ROLE_RULES: list[tuple] = [
    (lambda s: "youtube.com" in (s.get("url") or "").lower(), "debate_transcript"),
    (lambda s: "youtu.be" in (s.get("url") or "").lower(), "debate_transcript"),
    (
        lambda s: "judge" in (s.get("author") or "").lower()
        or "judge" in (s.get("title") or "").lower(),
        "judge_decision",
    ),
    (
        lambda s: any(
            w in (s.get("title") or s.get("local_path") or (s.get("url") or "")).lower()
            for w in (
                "response to", "response-to", "response_to",
                "rebuttal", "reply to", "reply",
            )
        ),
        "rebuttal",
    ),
    (
        lambda s: any(
            d in (s.get("url") or "").lower()
            for d in ("doi.org", "pubmed", "arxiv.org", "biorxiv", "ncbi.nlm.nih.gov")
        ),
        "primary_research",
    ),
    (
        lambda s: any(
            d in (s.get("url") or "").lower()
            for d in (
                "academic.oup.com",
                "sciencedirect.com",
                "springer.com",
                "nature.com",
                "wiley.com",
                "cell.com",
                "thelancet.com",
            )
        )
        and s.get("publication_status") != "unpublished",
        "primary_research",
    ),
    (
        lambda s: any(
            w in (s.get("title") or "").lower()
            for w in ("meta-analysis", "systematic review", "meta analysis")
        ),
        "review",
    ),
    (
        lambda s: any(
            d in (s.get("url") or "").lower()
            for d in ("substack.com", "medium.com", "blogspot", "wordpress.com")
        ),
        "commentary",
    ),
    (lambda s: (s.get("content_type") or "") in ("video", "audio"), "debate_transcript"),
]


def _source_label(source: dict) -> str:
    return (
        source.get("title")
        or source.get("author")
        or source.get("local_path")
        or source.get("url")
        or "untitled"
    )


def _haiku_classify_role(source: dict, preview: str, cache: Cache) -> tuple[str, str]:
    title = source.get("title") or source.get("local_path") or ""
    author = source.get("author") or ""
    url = source.get("url") or ""
    cache_key = f"source_role::{title}::{author}::{url}"

    result = cache.get_or_run(
        "agent",
        cache_key,
        lambda: call_llm(
            SOURCE_ROLE_CLASSIFIER.format(
                title=title[:300],
                author=author[:200],
                url=url[:300],
                preview=(preview or "")[:200],
            ),
            model=MODEL_FAST,
            max_tokens=200,
            parse_json=True,
            label="source_role",
        ),
    )
    if isinstance(result, dict) and not result.get("parse_error"):
        role = (result.get("role") or "unknown").strip().lower().replace(" ", "_")
        if role in VALID_ROLES:
            return role, "haiku"
    return "unknown", "haiku_failed"


def resolve_role(
    source: dict,
    cache: Cache | None = None,
    raw_preview: str = "",
) -> tuple[str, str]:
    """
    Assign structural document role from metadata.

    Returns (role, method) where method is override | rule | haiku | haiku_failed | unknown.
    Does not block ingest — unknown is valid.
    """
    if source.get("role"):
        role = str(source["role"]).strip().lower().replace(" ", "_")
        if role not in VALID_ROLES:
            role = "unknown"
        return role, "override"

    for condition, role in ROLE_RULES:
        try:
            if condition(source):
                return role, "rule"
        except Exception:
            continue

    if cache is not None:
        return _haiku_classify_role(source, raw_preview, cache)

    return "unknown", "unknown"


def resolve_all_roles(
    sources: list[dict],
    cache: Cache | None,
    raw_by_id: dict[str, str],
    id_fn,
) -> dict[str, dict]:
    """Resolve roles for all sources. Returns {source_id: {role, method, label}}."""
    out: dict[str, dict] = {}
    for source in sources:
        sid = id_fn(source)
        if not sid:
            continue
        raw = raw_by_id.get(sid, "")
        preview = raw[:200] if raw and not raw.startswith("FETCH_ERROR") else ""
        role, method = resolve_role(source, cache=cache, raw_preview=preview)
        out[sid] = {
            "role": role,
            "method": method,
            "label": _source_label(source),
            "author": source.get("author", ""),
            "url": source.get("url") or source.get("local_path", ""),
        }
    return out
