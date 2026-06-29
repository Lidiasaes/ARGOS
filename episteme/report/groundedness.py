"""Groundedness audit for generated reports.

Classifies each prose paragraph of a report by how it traces back to the source graph:

    explicit_id  > a valid graph/index ID is cited (strongest)
    paraphrase   > no ID, but lexical or semantic match to a node's content
    ungrounded   > none of the above (pure LLM synthesis)

Produces a compact, self-describing transparency note appended to the report.
This measures *traceability*, not interpretive correctness or selection bias.
"""

from __future__ import annotations

import re
from collections import Counter

import numpy as np

from episteme.core.embeddings import embed_many

# Thresholds (calibrated on covid_small: ungrounded paragraphs sit at ~0.42-0.54
# cosine, genuine paraphrases at >=0.60, so 0.55 separates them cleanly).
MIN_PARAGRAPH_WORDS = 25
LEXICAL_OVERLAP_MIN = 0.18
SEMANTIC_SIM_MIN = 0.55
GRAPH_DERIVED_THRESHOLD = 50.0   # explicit-ID % above which a section is "graph-derived"
MIN_SECTION_PARAS = 3            # smaller sections are omitted from the prose note

NOTE_START = "<!-- GROUNDEDNESS_NOTE_START -->"
NOTE_END = "<!-- GROUNDEDNESS_NOTE_END -->"

_STOPWORDS = frozenset("""
a an the of and or but if then so as is are was were be been being have has
had do does did to from in on at by for with without about into onto upon
through during over under above below this that these those it its their
there here when where which who whom whose what why how all any some no not
only also more most less many few much such same other another both either
neither each every can could may might must shall should will would
""".split())

_ID_PATTERNS = [
    re.compile(r"\b(gap_[a-f0-9]{8})\b"),
    re.compile(r"\b(claim_[a-f0-9]{8})\b"),
    re.compile(r"\b(crux_[a-z0-9_]+)\b"),
    re.compile(r"\b(chain_[a-z0-9_]+)\b"),
    re.compile(r"\b(theme_\d{2})\b"),
    re.compile(r"(?<![a-z])([a-f0-9]{8})(?![a-z0-9])"),  # bare hash
]
_HEX_HAS_LETTER = re.compile(r"[a-f]")
_SECTION_RE = re.compile(r"\n## (\d+\. [^\n]+)\n", re.MULTILINE)


def _extract_ids(text: str) -> set[str]:
    low = text.lower()
    raw: list[str] = []
    for pat in _ID_PATTERNS:
        raw.extend(pat.findall(low))
    # Drop pure-numeric 8-char tokens (e.g. "99999999" from "99.9999999999%"); real
    # graph hashes always contain at least one a-f letter.
    return {
        r for r in raw
        if ("_" in r) or len(r) != 8 or _HEX_HAS_LETTER.search(r) is not None
    }


def _normalize_id(raw: str) -> str:
    if raw.startswith(("gap_", "claim_")):
        return re.sub(r"^(gap_|claim_)", "", raw)
    return raw


def _content_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _build_valid_ids(graph: dict, index: dict) -> set[str]:
    valid: set[str] = set(graph.keys())
    for t in index.get("themes", []):
        if t.get("id"):
            valid.add(t["id"])
    for c in index.get("cruxes", []):
        cid = c.get("id")
        if cid:
            valid.add(cid)
            valid.add(f"crux_{cid}")
    for ch in index.get("chains", []):
        cid = ch.get("id")
        if cid:
            valid.add(cid)
            if not cid.startswith("chain_"):
                valid.add(f"chain_{cid}")
    for g in index.get("gaps", []):
        gid = g.get("id")
        if gid:
            valid.add(gid)
            valid.add(f"gap_{gid}")
    for c in index.get("ranked_claims", []):
        if c.get("id"):
            valid.add(c["id"])
    valid.discard("")
    return valid


def _build_node_corpus(graph: dict, index: dict) -> list[str]:
    """Just the texts; IDs aren't needed for aggregate note figures."""
    texts: list[str] = []
    for node in graph.values():
        if node.get("content"):
            texts.append(node["content"])
    for t in index.get("themes", []):
        if t.get("label"):
            texts.append(t["label"])
    for c in index.get("cruxes", []):
        txt = " ".join(filter(None, [c.get("question"), c.get("stakes"), c.get("resolution_path")]))
        if txt.strip():
            texts.append(txt)
    for g in index.get("gaps", []):
        if g.get("content"):
            texts.append(g["content"])
    for ch in index.get("chains", []):
        txt = " ".join(filter(None, [ch.get("conclusion"), ch.get("narrative")]))
        if txt.strip():
            texts.append(txt)
    return texts


def _prose_paragraphs(body: str) -> list[str]:
    cleaned = []
    for p in re.split(r"\n\s*\n", body):
        p = p.strip()
        if not p or p.startswith(("#", "|", "```", "- ", "* ", "1.", "2.", "3.")):
            continue
        lines = p.split("\n")
        if sum(1 for ln in lines if ln.lstrip().startswith(("-", "*", "|"))) > len(lines) / 2:
            continue
        if len(p.split()) < MIN_PARAGRAPH_WORDS:
            continue
        cleaned.append(p)
    return cleaned


def _section_number(section_name: str) -> int | None:
    m = re.match(r"\s*(\d+)\.", section_name)
    return int(m.group(1)) if m else None


def _compress_ranges(nums: list[int]) -> str:
    """[1,2,5,6,7,8,10] -> '\u00a71, \u00a72, \u00a75\u2013\u00a78, \u00a710'."""
    if not nums:
        return ""
    nums = sorted(set(nums))
    runs: list[tuple[int, int]] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            runs.append((start, prev))
            start = prev = n
    runs.append((start, prev))
    return ", ".join(f"\u00a7{a}" if a == b else f"\u00a7{a}\u2013\u00a7{b}" for a, b in runs)


def audit_groundedness(report_text: str, graph: dict, index: dict) -> dict | None:
    """Audit a report against its source graph + compiled index.

    Returns an aggregate dict (per-section + totals), or None if the embedding model
    is unavailable (in which case the caller should skip the note rather than emit
    misleading figures).
    """
    valid_ids = _build_valid_ids(graph, index)
    node_texts = _build_node_corpus(graph, index)
    if not node_texts:
        return None

    node_embeddings = embed_many(node_texts)
    if node_embeddings is None:
        return None  # model unavailable; lexical/ID alone would overstate "ungrounded"
    node_embeddings = np.asarray(node_embeddings)
    node_tokens = [_content_tokens(t) for t in node_texts]

    sections = list(zip(*[iter(_SECTION_RE.split(report_text)[1:])] * 2)) \
        if _SECTION_RE.search(report_text) else []

    sec_summaries = []
    for sec_name, sec_body in sections:
        paras = _prose_paragraphs(sec_body)
        if not paras:
            continue

        para_embeddings = np.asarray(embed_many(paras))
        counts = Counter()
        total_ids = valid_cited = 0
        for para, p_emb in zip(paras, para_embeddings):
            ids = _extract_ids(para)
            valid_refs = {i for i in ids if _normalize_id(i) in valid_ids or i in valid_ids}
            total_ids += len(ids)
            valid_cited += len(valid_refs)

            if valid_refs:
                counts["explicit_id"] += 1
                continue
            p_tokens = _content_tokens(para)
            has_lexical = any(_jaccard(p_tokens, nt) >= LEXICAL_OVERLAP_MIN for nt in node_tokens)
            sem_best = float(np.max(node_embeddings @ p_emb))
            has_semantic = sem_best >= SEMANTIC_SIM_MIN
            if has_lexical or has_semantic:
                counts["paraphrase"] += 1
            else:
                counts["ungrounded"] += 1

        n = len(paras)
        sec_summaries.append({
            "section": sec_name,
            "number": _section_number(sec_name),
            "paragraphs": n,
            "explicit_id_pct": counts["explicit_id"] / n * 100,
            "ungrounded": counts["ungrounded"],
            "explicit_id": counts["explicit_id"],
            "total_ids": total_ids,
            "valid_ids_cited": valid_cited,
        })

    if not sec_summaries:
        return None

    total_paras = sum(s["paragraphs"] for s in sec_summaries)
    total_explicit = sum(s["explicit_id"] for s in sec_summaries)
    total_ungrounded = sum(s["ungrounded"] for s in sec_summaries)
    total_ids = sum(s["total_ids"] for s in sec_summaries)
    total_valid = sum(s["valid_ids_cited"] for s in sec_summaries)

    return {
        "sections": sec_summaries,
        "total_paragraphs": total_paras,
        "explicit_pct": total_explicit / max(total_paras, 1) * 100,
        "ungrounded_pct": total_ungrounded / max(total_paras, 1) * 100,
        "id_validity_pct": total_valid / max(total_ids, 1) * 100,
        "total_ids": total_ids,
    }


def build_transparency_note(audit: dict) -> str:
    """Compact, self-describing provenance footnote computed live from the audit."""
    graph_derived, synthesis = [], []
    for s in audit["sections"]:
        if s["number"] is None or s["paragraphs"] < MIN_SECTION_PARAS:
            continue
        bucket = graph_derived if s["explicit_id_pct"] >= GRAPH_DERIVED_THRESHOLD else synthesis
        bucket.append(s["number"])

    parts = []
    if graph_derived:
        parts.append(
            f"{_compress_ranges(graph_derived)} are graph-derived "
            f"(\u2265{GRAPH_DERIVED_THRESHOLD:.0f}% explicit-ID citation)"
        )
    if synthesis:
        parts.append(
            f"{_compress_ranges(synthesis)} combine traceable paraphrase with interpretive synthesis"
        )
    provenance = ("; ".join(parts) + ". ") if parts else ""

    id_validity = audit["id_validity_pct"]
    hallucination = (
        "no hallucinated references"
        if id_validity >= 99.95
        else f"{100 - id_validity:.1f}% of cited IDs do not resolve"
    )

    return (
        f"{NOTE_START}\n\n"
        f"## Groundedness & Provenance\n\n"
        f"*Every paragraph of this report was automatically audited against the source "
        f"knowledge graph and compiled index. {provenance}"
        f"Overall: **{audit['explicit_pct']:.0f}% explicit-ID citation**, "
        f"**{audit['ungrounded_pct']:.1f}% ungrounded** (pure synthesis), and "
        f"**{id_validity:.0f}% ID validity** ({hallucination}).*\n\n"
        f"<sub>Grounding per paragraph: explicit graph ID > traceable paraphrase "
        f"(lexical/semantic match to a node) > ungrounded. Measures traceability, not "
        f"interpretive correctness or selection bias.</sub>\n\n"
        f"{NOTE_END}"
    )
