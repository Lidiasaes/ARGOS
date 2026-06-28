"""Source loading and content fetching."""

import json
import re
from pathlib import Path

from episteme.config import BASE_DIR, CASES_DIR, CHUNK_MAX_CHARS, CHUNK_MAX_OUTPUT_TOKENS, MODEL_FAST
from episteme.core.llm import call_llm
from episteme.prompts import CHUNKER, DOC_SUMMARIZER

_JS_BLOCK_PHRASES = [
    "enable javascript",
    "javascript is required",
    "please enable js",
    "you need to enable javascript",
    "subscribe to read",
    "this content is for subscribers",
    "access this article",
    "sign in to read",
    "create a free account",
    "403 forbidden",
    "429 too many",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cookie": "cookieconsent=accepted; gdpr=1; euconsent=1",
    "DNT": "1",
}


def load_sources(case: str) -> list:
    path = CASES_DIR / case / "sources.json"
    return json.loads(path.read_text(encoding="utf-8"))


def source_id(source: dict) -> str:
    return source.get("local_path") or source.get("url") or ""


def get_content(source: dict) -> str:
    """
    Load argumentative body only. Bibliography sidecars (source['bibliography'])
    are handled separately by bibliography.register_bibliography — never merged here.
    """
    local = source.get("local_path")
    if local:
        result = fetch_local(Path(local))
        if not result.startswith("FETCH_ERROR"):
            return result
        print(f"    Local file not found ({local}), trying URL...")

    url = source.get("url")
    if not url:
        return "FETCH_ERROR: No url and no local_path in source entry."
    return fetch_url(url)


def fetch_local(path: Path) -> str:
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        return f"FETCH_ERROR: Local file not found: {path}"

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            from episteme.filters.junk_quote import strip_toc_lines

            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return strip_toc_lines("\n\n".join(pages))[:50000]
        except Exception as e:
            return f"FETCH_ERROR: Could not read PDF {path}: {e}"

    try:
        from episteme.filters.junk_quote import strip_toc_lines

        raw = path.read_text(encoding="utf-8", errors="ignore")[:50000]
        if path.name.startswith("judge_eric"):
            return strip_toc_lines(raw)
        return raw
    except Exception as e:
        return f"FETCH_ERROR: Could not read file {path}: {e}"


def fetch_url(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup

    try:
        r = requests.get(url, timeout=20, headers=_HEADERS, allow_redirects=True)
        if r.status_code == 403:
            return f"FETCH_ERROR: 403 Forbidden — site blocks scrapers. URL: {url}"
        if r.status_code == 429:
            return f"FETCH_ERROR: 429 Rate-limited. URL: {url}"
        if r.status_code >= 400:
            return f"FETCH_ERROR: HTTP {r.status_code}. URL: {url}"

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(
            ["script", "style", "nav", "footer", "header", "aside", "form", "button", "noscript",
             "[class*='cookie']", "[id*='cookie']", "[class*='popup']", "[class*='modal']",
             "[class*='banner']", "[class*='overlay']"]
        ):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)[:50000]
        text_lower = text.lower()
        if len(text) < 800 or any(p in text_lower for p in _JS_BLOCK_PHRASES):
            hint = "Add a local_path to sources.json with a downloaded copy."
            return f"FETCH_ERROR: JS-blocked, paywalled, or popup-gated ({len(text)} chars). {hint} URL: {url}"
        return text
    except Exception as e:
        return f"FETCH_ERROR: {e}"


def evaluate_source(source: dict, raw: str) -> dict:
    """Phase 1: curated sources — trusted by default."""
    content_type = source.get("content_type", "text")
    source_type_map = {
        "text": "essay_reflection",
        "pdf": "peer_reviewed",
        "video": "debate_transcript",
        "audio": "debate_transcript",
        "web": "journalism",
    }
    return {
        "source_type": source_type_map.get(content_type, "essay_reflection"),
        "claim_type": "interpretive",
        "trust_level": "high",
        "pass_to_deep": False,
        "genericity_flag": False,
        "independence_score": 1.0,
    }


_SECTION_RE = re.compile(
    r"^\s*("
    r"abstract|introduction|background|"
    r"methods?|materials?\s*(?:and\s+methods?)?|"
    r"results?|findings?|"
    r"discussion|conclusions?|limitations?|"
    r"supplementary|acknowledgements?|references?|bibliography|"
    r"\d+[\.\s]+(?:introduction|methods?|results?|discussion|conclusion)"
    r")\s*$",
    re.IGNORECASE,
)

_SECTION_EPISTEMIC = {
    "results":      "empirical_finding — higher evidential weight",
    "findings":     "empirical_finding — higher evidential weight",
    "methods":      "methodological — describes procedure not finding",
    "limitations":  "constrained — author flags scope restriction",
    "discussion":   "interpretive — author inference, not raw data",
    "introduction": "framing — may contain reported_speech of others",
    "background":   "framing — may contain reported_speech of others",
    "conclusion":   "synthesis — interpretive, check for overgeneralization",
    "abstract":     "summary — verify claims against body sections",
}


def _detect_section(paragraph: str) -> str | None:
    """Return section name if paragraph is a header line, else None."""
    if _SECTION_RE.match(paragraph.strip()):
        return paragraph.strip().title()
    return None


def _epistemic_hint(section: str) -> str:
    key = section.lower().strip()
    for k, v in _SECTION_EPISTEMIC.items():
        if k in key:
            return v
    return "general — no specific epistemic weight"


def chunk_text(raw: str) -> list:
    """
    Split raw document text into argumentative chunks.

    Each chunk is prefixed with:
      [SECTION: X | EPISTEMIC: Y]   — section metadata, always present
      [PRIOR CONTEXT: ...]           — last 250 chars of previous block,
                                       only on first chunk of each block,
                                       for cross-boundary polarity detection.

    EXTRACTOR RULE: textual_evidence must come from MAIN CHUNK only,
    never from [PRIOR CONTEXT]. Prior context is for polarity disambiguation.
    """
    from episteme.config import CHUNK_MAX_CHARS

    CONTEXT_OVERLAP_CHARS = 250

    # Pass 1 — tag each paragraph with its section
    paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
    current_section = "Body"
    tagged: list[tuple[str, str]] = []

    for p in paragraphs:
        section = _detect_section(p)
        if section:
            current_section = section
            continue  # headers are metadata, not content
        tagged.append((p, current_section))

    # Pass 2 — accumulate into blocks respecting CHUNK_MAX_CHARS
    blocks: list[tuple[str, str, str]] = []  # (text, section, prev_tail)
    current_text = ""
    current_tag = "Body"
    prev_tail = ""

    for p_text, p_section in tagged:
        if current_text and (len(current_text) + len(p_text) > CHUNK_MAX_CHARS):
            blocks.append((current_text, current_tag, prev_tail))
            prev_tail = current_text[-CONTEXT_OVERLAP_CHARS:].strip()
            current_text = p_text
            current_tag = p_section
        else:
            if not current_text:
                current_tag = p_section
            current_text += ("\n\n" + p_text) if current_text else p_text

    if current_text:
        blocks.append((current_text, current_tag, prev_tail))

    # Pass 3 — LLM argumentative split within each block
    chunks = []
    for block_text, section_tag, ctx_prefix in blocks:
        result = call_llm(
            CHUNKER.format(text=block_text),
            model=MODEL_FAST,
            max_tokens=60,
            parse_json=True,
            label="chunking",
        )
        offsets = _normalize_chunk_offsets(result)
        ep_hint = _epistemic_hint(section_tag)

        if offsets is not None:
            boundaries = sorted(set([0] + offsets + [len(block_text)]))
            for idx, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
                piece = block_text[start:end].strip()
                if not piece:
                    continue
                header = f"[SECTION: {section_tag} | EPISTEMIC: {ep_hint}]"
                if idx == 0 and ctx_prefix:
                    header += f"\n[PRIOR CONTEXT: {ctx_prefix}]"
                chunks.append(f"{header}\n\n{piece}")
        else:
            header = f"[SECTION: {section_tag} | EPISTEMIC: {ep_hint}]"
            if ctx_prefix:
                header += f"\n[PRIOR CONTEXT: {ctx_prefix}]"
            chunks.append(f"{header}\n\n{block_text}")

    return chunks


def _normalize_chunk_offsets(result) -> list[int] | None:
    """CHUNKER returns a JSON array of char offsets; tolerate int/float."""
    if not isinstance(result, list):
        return None
    offsets = []
    for x in result:
        if isinstance(x, bool):
            return None
        if isinstance(x, int):
            offsets.append(x)
        elif isinstance(x, float) and x == int(x):
            offsets.append(int(x))
        else:
            return None
    return offsets


def summarize_document(raw: str) -> str:
    result = call_llm(
        DOC_SUMMARIZER.format(text=raw[:3000]),
        model=MODEL_FAST,
        max_tokens=120,
        label="doc_summary",
    )
    return result.strip() if isinstance(result, str) else ""


def get_subdomain(case: str) -> str:
    from episteme.profiles.case_profile import load_case_profile

    profile = load_case_profile(case)
    if profile:
        questions = profile.get("central_questions") or []
        if questions:
            return str(questions[0])[:160]
        positions = profile.get("debate_positions") or []
        if positions:
            labels = [p.split(":", 1)[0].strip() for p in positions[:3] if p]
            if labels:
                return "; ".join(labels)
    return case
