"""Infer inquiry context for methodology layer — domain-agnostic."""

import json


def infer_inquiry_type(case: str, sources: list[dict], case_profile: dict | None) -> str:
    """
    Classify inquiry shape from case profile + source metadata only.
    No per-case dicts or domain-specific keyword lists.
    """
    profile = case_profile or {}
    positions = profile.get("debate_positions") or []
    questions = " ".join(profile.get("central_questions", [])).lower()
    subfields = " ".join(profile.get("subfields", [])).lower()
    blob = f"{questions} {subfields}"

    debate_score = 0
    if any("debate" in (s.get("content_type") or "").lower() for s in sources):
        debate_score += 3
    probabilistic_terms = (
        "prior ", "posterior", "bayesian", "likelihood ratio",
        "odds ratio", "base rate", "conditional probability",
    )
    if any(term in questions for term in probabilistic_terms):
        debate_score += 3
    if len(positions) >= 2 and any(
        marker in questions
        for marker in (" versus ", " vs ", "more likely", "odds", "prior ", "posterior")
    ):
        debate_score += 1

    science_score = 0
    paper_markers = ("paper", "preprint", "research", "journal", "dataset")
    research_sources = sum(
        1 for s in sources
        if (s.get("publication_status") or "").lower() in ("peer_reviewed", "published")
        or any(m in (s.get("content_type") or "").lower() for m in paper_markers)
    )
    if sources and research_sources >= max(1, len(sources) // 2):
        science_score += 2
    methods_terms = (
        "sample", "cohort", "method", "statistical", "experiment",
        "trial", "replication", "sequencing", "transcriptom",
    )
    if sum(1 for term in methods_terms if term in blob) >= 2:
        science_score += 2

    if debate_score >= 3 and debate_score >= science_score:
        return "probabilistic_debate"
    if science_score >= 3 and science_score > debate_score:
        return "empirical_bench_science"
    return "mixed"


def infer_inquiry_context(case: str, sources: list[dict], case_profile: dict | None) -> str:
    """
    Build a text summary of the case domain for methodology prompts.
    Uses case_profile + source metadata — no hardcoded per-case dicts.
    """
    profile = case_profile or {}
    parts = [f"Case: {case}"]

    questions = profile.get("central_questions", [])
    if questions:
        parts.append("Central questions: " + "; ".join(questions[:4]))

    subfields = profile.get("subfields", [])
    if subfields:
        parts.append("Subfields: " + ", ".join(subfields[:8]))

    positions = profile.get("debate_positions", [])
    if positions:
        position_strs = []
        for p in positions[:3]:
            if isinstance(p, dict):
                name = p.get("name", "")
                desc = p.get("description", "")
                position_strs.append(f"{name}: {desc}".strip(": ") if name or desc else "")
            else:
                position_strs.append(str(p))
        position_strs = [s for s in position_strs if s]
        if position_strs:
            parts.append("Debate positions: " + "; ".join(position_strs))

    source_types = []
    for s in sources:
        label = s.get("author") or s.get("title") or s.get("local_path") or s.get("url", "")
        status = s.get("publication_status", "")
        ctype = s.get("content_type", "text")
        domain = s.get("domain", "")
        subdomain = s.get("subdomain", "")
        bits = [label]
        if domain:
            bits.append(domain)
        if subdomain:
            bits.append(subdomain)
        if status:
            bits.append(status)
        bits.append(ctype)
        source_types.append(" | ".join(bits))
    if source_types:
        parts.append("Sources: " + " // ".join(source_types[:5]))

    return "\n".join(parts)


def sources_meta_json(sources: list[dict]) -> str:
    return json.dumps(
        [
            {
                "author": s.get("author"),
                "title": s.get("title"),
                "url": s.get("url"),
                "local_path": s.get("local_path"),
                "content_type": s.get("content_type"),
                "publication_status": s.get("publication_status"),
                "domain": s.get("domain"),
                "subdomain": s.get("subdomain"),
                "date": s.get("date"),
            }
            for s in sources
        ],
        ensure_ascii=False,
        indent=2,
    )
