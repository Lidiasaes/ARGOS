"""Anti-generic filters — entity overlap + optional LLM contrast."""

from episteme.config import (
    MIN_ENTITY_SPECIFICITY,
    GENERICITY_EMBED_THRESHOLD,
    SPECIFICITY_MISMATCH_THRESHOLD,
    MODEL_FAST,
    MAX_TOKENS_FAST,
)
from episteme.core.embeddings import embed, cosine_sim
from episteme.core.llm import call_llm
from episteme.prompts.extraction import GENERICITY_CHECK


def entity_overlap_score(content: str, entities: list[str]) -> float:
    """Fraction of case entities whose name appears in the claim (entity-level, not token-level)."""
    if not entities:
        return 1.0
    content_lower = content.lower()

    def mentioned(entity: str) -> bool:
        words = [w for w in entity.lower().split() if len(w) > 3]
        if not words:
            return entity.lower() in content_lower
        return sum(1 for w in words if w in content_lower) >= max(1, len(words) // 2)

    hits = sum(1 for e in entities if mentioned(e))
    return hits / len(entities)


def embedding_genericity_score(content: str, subfield: str = "") -> float:
    """
    Compare claim embedding to a generic paraphrase.
    High similarity → likely too generic.
    Returns similarity 0-1.
    """
    generic = f"In this field, it is generally believed that {content}"
    if subfield:
        generic = f"In {subfield}, researchers often claim that {content}"
    emb_claim = embed(content)
    emb_generic = embed(generic)
    if emb_claim is None or emb_generic is None:
        return 0.0
    return cosine_sim(emb_claim, emb_generic)


def _format_anti_generic_examples(case_profile: dict) -> tuple[str, str]:
    examples = case_profile.get("anti_generic_examples") or {}
    too_generic = examples.get("too_generic") or []
    specific = examples.get("appropriately_specific") or []
    too_fmt = "\n".join(f"  - {e}" for e in too_generic) or "  (none)"
    spec_fmt = "\n".join(f"  - {e}" for e in specific) or "  (none)"
    return too_fmt, spec_fmt


def llm_genericity_check(content: str, case_profile: dict, subfield: str = "") -> bool:
    """Returns True if claim is too generic (should reject)."""
    too_fmt, spec_fmt = _format_anti_generic_examples(case_profile)
    result = call_llm(
        GENERICITY_CHECK.format(
            entities=", ".join(case_profile.get("key_entities", [])[:30]),
            questions=", ".join(case_profile.get("central_questions", [])),
            too_generic_examples=too_fmt,
            specific_examples=spec_fmt,
            content=content,
            subfield=subfield or "unspecified",
        ),
        model=MODEL_FAST,
        max_tokens=MAX_TOKENS_FAST,
        parse_json=True,
        label="genericity_check",
    )
    if isinstance(result, dict):
        return result.get("too_generic", False)
    return False


def assess_specificity(
    node_data: dict,
    case_profile: dict,
    use_llm: bool = False,
) -> tuple[float, bool, str]:
    """
    Returns (specificity_score, should_reject, reason).
    specificity_score: 0-1, higher = more case-specific.
    """
    if node_data.get("has_verified_quote") and node_data.get("argument_level") == "direct":
        return 1.0, False, "verified quote is sufficient specificity proof"

    content = node_data.get("content", "")
    subfield = node_data.get("subfield", "")
    entities = case_profile.get("key_entities", [])

    entity_score = entity_overlap_score(content, entities)
    embed_sim = embedding_genericity_score(content, subfield)

    # Combined score: high entity overlap, low generic embedding similarity
    specificity = entity_score * (1.0 - embed_sim * 0.5)
    specificity = max(0.0, min(1.0, specificity))

    argument_level = node_data.get("argument_level", "direct")
    if argument_level == "methodological":
        # Methodological claims can be more abstract
        if specificity < SPECIFICITY_MISMATCH_THRESHOLD * 0.5:
            return specificity, True, "methodological but too abstract for case"
        return specificity, False, "methodological ok"

    if entity_score < MIN_ENTITY_SPECIFICITY and embed_sim >= GENERICITY_EMBED_THRESHOLD:
        return specificity, True, f"low entity overlap ({entity_score:.2f}) + high generic similarity ({embed_sim:.2f})"

    if use_llm and entity_score < MIN_ENTITY_SPECIFICITY:
        if llm_genericity_check(content, case_profile, subfield):
            return specificity, True, "LLM flagged as too generic"

    return specificity, False, "passed"
