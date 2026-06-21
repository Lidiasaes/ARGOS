"""v4 extraction prompts — anti-generic, case-grounded."""

CASE_PROFILE = """
You are an epistemic analyst preparing a case profile for automated extraction.

CASE ID: {case}
SOURCES (metadata only):
{sources_meta}

SAMPLE TEXT (first ~4000 chars from each source, concatenated):
{sample_text}

From this material ONLY (do not import generic field knowledge), produce a case profile:

- central_questions: 3-6 precise questions the debate actually turns on
- subfields: 4-10 tags naming specific sub-areas (e.g. "gain-of-function research", not "science")
- key_entities: 10-30 named entities, institutions, methods, datasets specific to THIS case
- debate_positions: 2-5 named positions with one-line descriptions
- anti_generic_examples: 3 examples of claims that would be TOO GENERIC for this case
  (field-level platitudes) vs 3 examples of appropriately SPECIFIC claims

Return JSON only:
{{
  "central_questions": [...],
  "subfields": [...],
  "key_entities": [...],
  "debate_positions": [...],
  "anti_generic_examples": {{"too_generic": [...], "appropriately_specific": [...]}}
}}
"""

SOURCE_THESIS = """
You are an epistemic analyst mapping the argumentative structure of one source.

CASE PROFILE:
{case_profile}

SOURCE:
  id: {source_id}
  author: {author}
  title/url: {label}
  type: {source_type}

TEXT (first ~6000 chars):
{text}

Extract the source's argumentative spine — not a summary of facts, but the thesis structure.

Return JSON only:
{{
  "source_id": "{source_id}",
  "main_thesis": "one sentence",
  "sub_theses": ["...", "..."],
  "methodological_stance": "how this author reasons (1-2 sentences)",
  "targets": ["positions or authors attacked"],
  "key_questions_addressed": ["which central_questions from case profile"],
  "textual_anchors": ["3-5 short verbatim phrases (max 12 words each) grounding the thesis"]
}}
"""

EXTRACTOR_V4 = """
You are an epistemic analyst performing academic structure extraction on a published debate text.
Map claims, evidence, and argument structure — do not endorse or refute conclusions.

━━ CASE CONTEXT (mandatory grounding) ━━
CASE: {case}
CENTRAL QUESTIONS: {central_questions}
SUBFIELDS: {subfields}
KEY ENTITIES: {key_entities}

SOURCE THESIS:
{source_thesis}

FRAGMENT: {chunk}
SOURCE ID: {source_id}
SOURCE URL: {source_url}
AUTHOR: {author}
DATE: {date}
SOURCE TYPE: {source_type}
DOCUMENT CONTEXT: {document_context}

━━ RULES ━━
1. argument_level: direct | methodological only (NEVER illustrative — skip those)
2. Every claim and evidence node MUST include:
   - textual_evidence: verbatim quote from FRAGMENT (20-80 words), exact words only
   - key_question: which central_question this node addresses (copy from list or close paraphrase)
   - subfield: one tag from SUBFIELDS that best fits
3. content: your one-sentence paraphrase of the assertion (not the quote)
4. If you cannot find a verbatim quote supporting the node, do NOT include the node
5. Reject field-level platitudes — nodes must mention case-specific entities or methods
6. evidential_weight < 0.2 → omit

━━ TYPE (strict enum — no other values) ━━
type MUST be exactly one of: claim | evidence | question | presupposition | gap

NEVER use type for: rebuttal, counterargument, refutation, objection, response, argument.
Those are NOT node types. Encode them as:
  type="claim" + claim_type="dismissal" (or "rhetorical", "methodological_critique", …)
  + counterargument: one sentence on what is being attacked (optional)
  + is_rhetorical_move: true when attacking credibility/source rather than substance

Examples:
- "Scott's post merely repeats the judges" → type=claim, claim_type=dismissal, is_rhetorical_move=true
- "The study found 12% prevalence" → type=claim, claim_type=empirical_finding
- "Table 3 shows OR=2.1" → type=evidence

For each node:
- type: claim | evidence | question | presupposition | gap  (ONLY these five)
- argument_level: direct | methodological
- content, abstraction_level, confidence, evidential_weight, claim_type
- textual_evidence, key_question, subfield
- supporting_quote: short phrase (max 15 words) from fragment, or null
- counterargument: if attacking another position, one sentence on what it attacks (optional)
- is_rhetorical_move: true | false (credibility/dismissal moves, not evidential substance)

Return {{"nodes": [...]}}. JSON only.
"""

GENERICITY_CHECK = """
Is this claim too GENERIC for the specific debate described below?

CASE ENTITIES: {entities}
CENTRAL QUESTIONS: {questions}

EXAMPLES — too generic for THIS case:
{too_generic_examples}

EXAMPLES — appropriately specific for THIS case:
{specific_examples}

CLAIM TO EVALUATE: {content}
SUBFIELD TAG: {subfield}

A claim is TOO GENERIC if it resembles the "too generic" examples — field-level platitudes
that could appear in any textbook without naming this debate's entities, methods, or positions.

A claim is appropriately specific if it resembles the "appropriately specific" examples —
grounded in this case's contested claims, named entities, or methodological dispute.

Return JSON only: {{"too_generic": true|false, "reason": "one sentence"}}
"""
