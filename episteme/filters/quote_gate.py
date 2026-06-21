"""Quote grounding gate — reject nodes without verbatim support in chunk."""

import re
from difflib import SequenceMatcher

from episteme.config import MIN_QUOTE_CHARS, MIN_GROUNDING_RATIO, REQUIRE_QUOTE_FOR
from episteme.filters.junk_quote import is_junk_quote


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text


def quote_in_chunk(quote: str, chunk: str) -> bool:
    """Check if quote appears (exact or fuzzy) in chunk."""
    if not quote or not chunk:
        return False
    q = _normalize(quote)
    c = _normalize(chunk)
    if len(q) < MIN_QUOTE_CHARS:
        return False
    if q in c:
        return True
    # Fuzzy: sliding window over chunk
    words_q = q.split()
    if len(words_q) < 4:
        return SequenceMatcher(None, q, c).ratio() >= MIN_GROUNDING_RATIO
    window = len(words_q)
    c_words = c.split()
    for i in range(len(c_words) - window + 1):
        segment = " ".join(c_words[i : i + window])
        if SequenceMatcher(None, q, segment).ratio() >= MIN_GROUNDING_RATIO:
            return True
    return False


def passes_quote_gate(node_data: dict, chunk: str) -> tuple[bool, str]:
    """
    Returns (passed, reason).
    Uses textual_evidence first, then supporting_quote.
    """
    node_type = node_data.get("type", "claim")
    if node_type not in REQUIRE_QUOTE_FOR:
        return True, "quote not required for type"

    quote = node_data.get("textual_evidence") or node_data.get("supporting_quote") or ""
    if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
        return False, f"missing quote (min {MIN_QUOTE_CHARS} chars)"

    if is_junk_quote(quote):
        return False, "quote is TOC/index debris, not evidence"

    if quote_in_chunk(quote, chunk):
        return True, "grounded"

    return False, "quote not found in chunk"
