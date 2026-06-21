"""Detect non-evidence quotes (TOC lines, PDF extraction artifacts)."""

from __future__ import annotations

import re

# Dot leaders + page number: "7.3 Market cluster . . . . . . 64"
_TOC_DOT_LEADER = re.compile(r"(?:\.\s*){3,}\s*\d+\s*$", re.M)

# Embedded TOC block inside a longer quote
_TOC_EMBEDDED = re.compile(
    r"\d+(?:\.\d+)+\s+[\w][^.\n]{5,90}(?:\.\s*){3,}\s*\d+",
    re.I,
)

# Section header ending with lone period (no real sentence)
_TOC_SECTION_ONLY = re.compile(
    r"^\s*\d+(?:\.\d+)+\s+[A-Za-z][^.\n]{5,100}\.\s*$",
    re.M,
)

# Page number glued to section title: "46\n4.6.1 Covid testing..."
_PAGE_PREFIX_SECTION = re.compile(
    r"^\s*\d+\s*(?:\n\s*)?\d+(?:\.\d+)+\s+[A-Za-z].{5,90}\.\s*$",
    re.M,
)

# PDF footnote digit merged into word: "Huanan7 Seafood", "known10 to"
_FOOTNOTE_WORD_MERGE = re.compile(r"\b[A-Za-z]{4,}\d{1,2}\b")
_FOOTNOTE_BLOCK = re.compile(r"\n\d{1,2}\n(?:ie,|Being off-topic|[A-Z][a-z]{3,})", re.I)
_KNOWN_FOOTNOTE_GLUE = re.compile(r"known\d+\s+to\b", re.I)
_CORRUPT_ZERO_COUNT = re.compile(r"^0 people are known\b", re.I)

# Chained TOC section titles in one quote
_TOC_CHAIN = re.compile(
    r"(?:\d+(?:\.\d+)+\s+[^.\n]{4,50}\s*){2,}(?:\.\s*){2,}",
    re.I,
)

# Inline TOC chains: "Locating sars-cov-2 . . . Covid testing . . ."
_TOC_ELLIPSIS = re.compile(r"(?:\.\s*){3}")

# Section-header stub ending at colon with no quoted body
_SECTION_INTRO_STUB = re.compile(
    r"^The lack of infected animals\s*\nScott quotes[^.]{10,200}:\s*$",
    re.I | re.M,
)

# Bare PDF/essay section titles (no sentence punctuation)
_BARE_SECTION_FRAGMENT = re.compile(
    r"^\s*(?:\d+(?:\.\d+)+\s+)?(?:Covid testing of|Locating sars-cov-2|Correlations between)"
    r"[^.!?]{4,90}\s*$",
    re.I | re.M,
)

# Chunk/PDF clip starting mid-word at line start only (TOC debris), not normal sentences
_MID_WORD_START = re.compile(
    r"^(?:ated|nal|ing|tion|ions|ment|ally)\s",
    re.I,
)


def is_junk_quote(quote: str) -> bool:
    """True when quote is TOC / index debris, not argumentative evidence."""
    if not quote:
        return True

    q = quote.strip()
    if len(q) < 8:
        return True

    if _MID_WORD_START.match(q):
        return True
    if _FOOTNOTE_WORD_MERGE.search(q):
        return True
    if _FOOTNOTE_BLOCK.search(q):
        return True
    if _KNOWN_FOOTNOTE_GLUE.search(q):
        return True
    if _CORRUPT_ZERO_COUNT.match(q):
        return True
    if _TOC_CHAIN.search(q):
        return True
    if len(_TOC_ELLIPSIS.findall(q)) >= 2:
        return True
    if _SECTION_INTRO_STUB.search(q):
        return True
    if _BARE_SECTION_FRAGMENT.match(q):
        return True

    if _TOC_DOT_LEADER.search(q):
        return True
    if _TOC_EMBEDDED.search(q):
        return True
    if _PAGE_PREFIX_SECTION.match(q):
        return True

    lines = [ln.strip() for ln in q.splitlines() if ln.strip()]
    if lines:
        toc_lines = sum(
            1
            for ln in lines
            if _TOC_SECTION_ONLY.match(ln)
            or _TOC_DOT_LEADER.search(ln)
            or _PAGE_PREFIX_SECTION.match(ln)
        )
        if toc_lines and toc_lines >= max(1, len(lines) // 2):
            return True

    if len(lines) == 1 and _TOC_SECTION_ONLY.match(lines[0]):
        return True

    return False


def strip_toc_lines(text: str) -> str:
    """Remove TOC lines from extracted document text (e.g. PDF index pages)."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if _TOC_DOT_LEADER.search(stripped):
            continue
        if _TOC_SECTION_ONLY.match(stripped):
            continue
        if _PAGE_PREFIX_SECTION.match(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)
