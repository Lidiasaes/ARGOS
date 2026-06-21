"""Extract auditable methodology text per source — not argumentative chunks."""

import re

ABSTRACT_START = re.compile(
    r"(?im)^(?:#+\s*)?(?:abstract\b|study\s+question\b|summary\s+answer\b)"
)
METHODS_START = re.compile(
    r"(?im)^(?:#+\s*)?(?:materials?\s+and\s+methods?|methods?\b|patients?\s+and\s+methods?|"
    r"study\s+design|experimental\s+(?:design|procedures?)|participants?\s+and\s+methods?|"
    r"participants?/materials|setting,\s+methods?)\b"
)
RESULTS_START = re.compile(
    r"(?im)^(?:#+\s*)?(?:main\s+results|results?\b|findings\b|key\s+findings)\b"
)
SECTION_END = re.compile(
    r"(?im)^(?:#+\s*)?(?:discussion|conclusions?|acknowledg|references?|funding|"
    r"competing\s+interests?|limitations)\b"
)
METHODS_END = re.compile(
    r"(?im)^(?:#+\s*)?(?:results?|discussion|conclusions?|acknowledg|references?|supplementary)\b"
)
SUPPLEMENTARY_HINT = re.compile(
    r"(?i)\b(?:supplementary|supplemental|online\s+methods?|supp\.?\s+table|"
    r"supplementary\s+material|additional\s+file|data\s+availability|"
    r"software\s+version|github\.com|zenodo|version\s+\d+\.\d+)\b"
)
DEBATE_KEYWORDS = re.compile(
    r"(?i)\b(?:bayesian|base\s+rate|likelihood\s+ratio|prior\s+probability|methodolog|"
    r"conditional\s+probability|bayes\s+factor|reasoning|calculation|assumption|"
    r"sample\s+size|control\s+group|confounder|exclusion\s+criteria|irb|ethics|"
    r"cohort|participants?|power\s+analysis|randomi[sz]ed|placebo|blinded|"
    r"statistical\s+(?:method|analysis|test)|confidence\s+interval|p[\s-]?value)\b"
)


def extract_methods_text(raw: str, source: dict, max_chars: int = 18000) -> str:
    """
    Return methodology-relevant text for one source.
    Papers: abstract + methods + results summary + supplementary hints.
    Debates: paragraphs with methodological keywords + intro fallback.
    """
    if not raw or raw.startswith("FETCH_ERROR"):
        return ""

    publication = (source.get("publication_status") or "").lower()
    is_debate = publication in ("", "unpublished") and not _looks_like_paper(raw)

    if is_debate:
        return _extract_debate_methodology(raw, max_chars)
    return _extract_paper_audit_text(raw, max_chars)


def _looks_like_paper(text: str) -> bool:
    lower = text[:12000].lower()
    markers = (
        "abstract",
        "materials and methods",
        "study question",
        "summary answer",
        "study design",
        "participants",
        "introduction",
        "supplementary",
        "main results",
    )
    return sum(1 for m in markers if m in lower) >= 2


def _extract_section(raw: str, start_pat: re.Pattern, end_pat: re.Pattern | None) -> str:
    start = start_pat.search(raw)
    if not start:
        return ""
    if end_pat:
        end = end_pat.search(raw, start.end())
        section = raw[start.start() : end.start() if end else len(raw)]
    else:
        section = raw[start.start() :]
    return section.strip()


def _extract_abstract(raw: str) -> str:
    section = _extract_section(raw, ABSTRACT_START, METHODS_START)
    if section and len(section) >= 100:
        return section
    # Structured abstracts without "Abstract" header (e.g. Hum Reprod paste)
    if re.search(r"(?im)^study\s+question\b", raw):
        end = METHODS_START.search(raw) or RESULTS_START.search(raw)
        if end:
            chunk = raw[: end.start()].strip()
            if len(chunk) >= 200:
                return chunk
    return ""


def _extract_methods_section(raw: str) -> str:
    section = _extract_section(raw, METHODS_START, METHODS_END)
    if section and len(section) >= 200:
        return section
    return ""


def _extract_results_summary(raw: str, max_len: int = 4000) -> str:
    """N per subgroup often appears in Results, not Methods."""
    section = _extract_section(raw, RESULTS_START, SECTION_END)
    if section and len(section) >= 80:
        return section[:max_len]
    # Structured abstract blocks
    for pat in (
        r"(?is)(main\s+results[^\n]*\n.*?)(?:\n(?:large\s+scale|limitations|wider\s+implications)\b)",
        r"(?is)(study\s+design,\s*size,\s*duration[^\n]*\n.*?)(?:\n(?:participants|main\s+results)\b)",
    ):
        m = re.search(pat, raw, re.IGNORECASE)
        if m and len(m.group(1).strip()) >= 40:
            return m.group(1).strip()[:max_len]
    return ""


def _extract_supplementary_hints(raw: str, max_len: int = 3000) -> str:
    paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
    hits = [p for p in paragraphs if SUPPLEMENTARY_HINT.search(p)]
    if not hits:
        lines = [ln.strip() for ln in raw.splitlines() if SUPPLEMENTARY_HINT.search(ln)]
        hits = lines
    if not hits:
        return ""
    combined = "\n\n".join(hits)
    return combined[:max_len]


def _extract_paper_audit_text(raw: str, max_chars: int) -> str:
    parts = []
    abstract = _extract_abstract(raw)
    if abstract:
        parts.append("=== ABSTRACT / STUDY DESIGN ===\n" + abstract)

    methods = _extract_methods_section(raw)
    if methods:
        parts.append("=== METHODS ===\n" + methods)

    results = _extract_results_summary(raw)
    if results:
        parts.append("=== RESULTS (sample sizes / outcomes) ===\n" + results)

    suppl = _extract_supplementary_hints(raw)
    if suppl:
        parts.append("=== SUPPLEMENTARY / VERSION HINTS ===\n" + suppl)

    if parts:
        return "\n\n".join(parts)[:max_chars]

    return raw[:max_chars]


def _extract_debate_methodology(raw: str, max_chars: int) -> str:
    paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
    hits = [p for p in paragraphs if DEBATE_KEYWORDS.search(p)]
    if hits:
        combined = "\n\n".join(hits)
        if len(combined) >= 500:
            return combined[:max_chars]

    return raw[:max_chars]
