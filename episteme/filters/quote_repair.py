"""Repair truncated quotes by extending to sentence boundaries in source text."""

from __future__ import annotations

import re

from episteme.config import QUOTE_REPAIR_MAX_CHARS

# Sentence end in running prose: .!? optionally followed by closers, then whitespace.
_SENTENCE_END = re.compile(r'[.!?]["\')\]]*(?:\s+|$)')
_CLOSERS = '"\')]}'


def _find_span_in_text(quote: str, text: str) -> tuple[int, int] | None:
    """Return (start, end) char span of quote in text."""
    if not quote or not text:
        return None

    q = quote.strip()
    if not q:
        return None

    low_text, low_q = text.lower(), q.lower()
    idx = low_text.find(low_q)
    if idx >= 0:
        return idx, idx + len(q)

    # Partial clip from chunk boundary — anchor on opening words.
    anchor = low_q[: min(40, len(low_q))].strip()
    if len(anchor) >= 8:
        idx = low_text.find(anchor)
        if idx >= 0:
            return idx, min(idx + max(len(q), len(anchor) + 20), len(text))

    return None


def _terminal_punct(text: str, pos: int) -> str:
    """Punctuation closing the word at or before pos (skipping quote/closing brackets)."""
    i = min(pos, len(text) - 1)
    while i >= 0 and text[i] in _CLOSERS:
        i -= 1
    return text[i] if i >= 0 else ""


def _span_is_sentence_bounded(text: str, start: int, end: int) -> bool:
    """True when the span already sits between sentence boundaries in source text."""
    if start >= end:
        return False

    end_punct = _terminal_punct(text, end - 1)
    end_ok = end_punct in ".!?" or end >= len(text)

    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    start_ok = i < 0 or text[i] in ".!?\n" or (i > 0 and text[i] in _CLOSERS)

    return end_ok and start_ok


def _paragraph_start_before(text: str, pos: int) -> int:
    """Index after the nearest blank-line paragraph break before pos."""
    para = text.rfind("\n\n", 0, pos)
    return para + 2 if para >= 0 else 0


def _skip_leading_header_line(text: str, start: int, end: int) -> int:
    """Drop a short title line immediately above the anchored quote span."""
    nl = text.find("\n", start)
    if nl < 0 or nl >= end:
        return start
    first = text[start:nl].strip()
    if not first or re.search(r"[.!?]$", first) or len(first) >= 90:
        return start
    rest = text[nl + 1 : end].lstrip()
    if rest and rest[0].isupper():
        return nl + 1
    return start


def _cap_excerpt(excerpt: str, max_chars: int) -> str:
    """Trim to max_chars at a word boundary; never leave a trailing word fragment."""
    if len(excerpt) <= max_chars:
        return excerpt
    trimmed = excerpt[:max_chars]
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed.rstrip(".,;:") + "…"


def _extend_to_sentence(
    text: str, start: int, end: int, max_chars: int = QUOTE_REPAIR_MAX_CHARS
) -> str:
    """Expand span to the nearest sentence boundaries within max_chars."""
    n = len(text)
    start = max(0, start)
    end = min(n, end)
    para_start = _paragraph_start_before(text, start)

    prev_break = para_start
    for m in _SENTENCE_END.finditer(text[para_start:start]):
        prev_break = para_start + m.end()
    new_start = prev_break if (start - prev_break) < 200 else start
    new_start = max(new_start, para_start)
    new_start = _skip_leading_header_line(text, new_start, end)

    chunk = text[new_start : min(n, new_start + max_chars)]
    rel_end = end - new_start
    forward = chunk[rel_end:]
    m = _SENTENCE_END.search(forward)
    if m:
        new_end = new_start + rel_end + m.end()
    else:
        colon = re.search(r":\s*(?:\n|$)", forward[:160])
        if colon:
            new_end = new_start + rel_end + colon.end()
        else:
            nl = text.find("\n\n", end)
            cap = new_start + max_chars
            new_end = nl if nl > end and (nl - end) < 200 else min(n, cap)
            if new_end <= end:
                new_end = min(n, end + 80)

    excerpt = _cap_excerpt(text[new_start:new_end].strip(), max_chars)
    return excerpt


def _repair_is_grounded(text: str, start: int, end: int, repaired: str) -> bool:
    """Repaired excerpt must exist in source and cover the clipped span."""
    if not repaired:
        return False
    rep_start = text.lower().find(repaired.strip().lower())
    if rep_start < 0:
        return False
    rep_end = rep_start + len(repaired.strip())
    return rep_start <= start and rep_end >= end - 2


def repair_quote_in_text(quote: str, text: str) -> str:
    """
    Extend a quote to full sentence(s) using the source text.

    If the quote anchors in text and is not already bounded by sentence breaks
    there, extend to the next . ! ?
    """
    if not quote or not text:
        return quote

    original = quote.strip()
    if not original:
        return quote

    span = _find_span_in_text(original, text)
    if not span:
        return original

    start, end = span
    if _span_is_sentence_bounded(text, start, end):
        return original

    repaired = _extend_to_sentence(text, start, end).strip()
    if not repaired or not _repair_is_grounded(text, start, end, repaired):
        return original
    return repaired if len(repaired) >= len(original) else original
