"""Valid node types and normalization for extracted graph nodes."""

from __future__ import annotations

VALID_NODE_TYPES = frozenset({"claim", "evidence", "presupposition", "question", "gap"})

# Invalid extractor types → canonical type
_TYPE_ALIASES: dict[str, str] = {
    "rebuttal": "claim",
    "counterargument": "claim",
    "refutation": "claim",
    "objection": "claim",
    "response": "claim",
    "argument": "claim",
}

# claim_type values that flag rhetorical / credibility moves (not evidential)
RHETORICAL_CLAIM_TYPES = frozenset({
    "dismissal",
    "rhetorical",
    "ad_hominem",
    "credibility_attack",
    "straw_man",
})

_SCORE_WORDS: dict[str, float] = {
    "very_high": 0.9,
    "high": 0.85,
    "medium": 0.65,
    "hedged": 0.55,
    "moderate": 0.6,
    "low": 0.4,
    "very_low": 0.3,
}


def coerce_score(value, default: float = 0.5) -> float:
    """Normalize confidence/evidential_weight — LLM may return words like 'medium'."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        key = value.strip().lower().replace(" ", "_")
        if key in _SCORE_WORDS:
            return _SCORE_WORDS[key]
        try:
            return max(0.0, min(1.0, float(value.strip())))
        except ValueError:
            return default
    return default


def infer_is_rhetorical_move(node_data: dict) -> bool:
    """True when the node is a credibility/rhetorical move rather than evidential content."""
    claim_type = (node_data.get("claim_type") or "").lower().strip()
    if claim_type in RHETORICAL_CLAIM_TYPES:
        return True
    if node_data.get("counterargument") and claim_type in ("", "unknown", "interpretive"):
        return True
    return bool(node_data.get("is_rhetorical_move"))


def normalize_extracted_node(node_data: dict) -> tuple[dict, str | None]:
    """
    Ensure extracted node uses a valid graph type.

    Returns (normalized_node_data, warning_message_or_None).
    Maps rebuttal → claim + claim_type=dismissal when unset.
    """
    out = dict(node_data)
    raw = (out.get("type") or "claim").lower().strip()

    warning = None
    if raw not in VALID_NODE_TYPES:
        canonical = _TYPE_ALIASES.get(raw, "claim")
        out["type"] = canonical
        warning = f"invalid type '{raw}' → '{canonical}'"

        if raw == "rebuttal":
            ct = (out.get("claim_type") or "").strip()
            if not ct or ct == "unknown":
                out["claim_type"] = "dismissal"
            out["is_rhetorical_move"] = True
        elif canonical == "claim" and raw not in _TYPE_ALIASES:
            warning = f"unknown type '{raw}' → 'claim'"

    if out.get("type") == "claim":
        out["is_rhetorical_move"] = infer_is_rhetorical_move(out)

    if "confidence" in out:
        out["confidence"] = coerce_score(out.get("confidence"))
    if "evidential_weight" in out:
        out["evidential_weight"] = coerce_score(out.get("evidential_weight"), default=0.5)

    return out, warning
