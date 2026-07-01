# ── DOCUMENT SUMMARIZER — use MODEL_FAST (Haiku) ────────────────────────────
# Run once per source before chunking. Produces a 2-sentence argumentative
# structure summary that is passed to every EXTRACTOR call for that source.
# Goal: help the extractor distinguish chunks that belong to the main argument
# from chunks that are worked examples, analogies, or background context.
# Generalization: uses no topic-specific language — works for any domain.

DOC_SUMMARIZER = """
Read this text and write exactly 2 sentences describing its argumentative structure.

Sentence 1: What position is the author defending or attacking? State the core thesis.
Sentence 2: How does the author build the argument? Mention if they use worked examples,
            analogies, case studies, or illustrations from other domains to make their point —
            because this helps distinguish the main argument from the illustrative scaffolding.

Be precise. Do not summarize the content — describe the structure of the reasoning.
Write in English regardless of the source language.

TEXT (first 3000 characters):
{text}
"""

# ── CHUNKER — use MODEL_FAST (Haiku) ────────────────────────────────────────
# Returns split indices only — NOT the text itself — to keep output tokens minimal.
# The pipeline reconstructs the chunks from the original text using these indices.

CHUNKER = """
Split this text into argumentative units.
An argumentative unit is a block that: introduces a new claim,
presents evidence, offers a rebuttal, makes a concession, or raises a doubt.

Rules:
- Do NOT split by length. Split where the argumentative unit changes.
- Minimum 2 units, maximum 8 units per text.
- Return ONLY a JSON array of character-offset integers marking where each new unit starts.
- The first unit always starts at 0. Do not include 0 in the array.
- Example for a text split into 3 units: [245, 891]

Return a JSON array of integers. No other text.

TEXT:
{text}
"""

# ── SOURCE EVALUATOR first pass — use MODEL_FAST (Haiku) ────────────────────
# Filters obviously irrelevant or low-quality sources before processing them.
# If pass_to_deep=true, a second pass is done with MODEL_SMART.

SOURCE_EVAL_FAST = """
Classify this source. JSON only. No additional text.

URL: {url}
PREVIEW (first 500 chars): {preview}
CLAIM IT SUPPORTS: {claim}

{{
  "source_type": "primary_data|peer_reviewed|preprint|institutional|expert_opinion|essay_reflection|journalism|blog_informal|official_statement|debate_transcript",
  "claim_type": "empirical_finding|methodological|interpretive|speculative|normative|rhetorical",
  "methodology_explicit": true/false,
  "data_available": true/false,
  "pass_to_deep": true/false,
  "trust_level": "high|medium|low|reject",
  "reject_reason": "string or null"
}}
"""

# ── SOURCE EVALUATOR second pass — use MODEL_SMART (Sonnet) ─────────────────
# Evaluates specificity, independence, and genericity risk.
# The genericity_flag field is the most important: detects when a source
# gives a generic domain answer that is precisely what the work tries to prove —
# the most common and dangerous pattern in scientific papers.

SOURCE_EVAL_DEEP = """
Evaluate this source in depth for the specific claim.

URL: {url}
TYPE: {source_type}
CLAIM: {claim}
SPECIFIC SUBDOMAIN: {subdomain}
GENERAL DOMAIN: {domain}

1. specificity_match (0.0-1.0): how specific is it to {subdomain}?
   If it speaks of {domain} in general and applies it to {subdomain} without justification: low value.

2. genericity_flag (true/false):
   Does it give an answer that would be true for the general domain but adds
   nothing specific to the subdomain? Is that generic answer one of the
   premises of the work being evaluated? If yes to either: true.

3. independence_score (0.0-1.0):
   Does it bring genuinely new evidence or cite the same primary sources
   already in the graph? 0.0 = same origin, 1.0 = completely independent.

4. conflict_of_interest: list of strings
5. known_biases: list of strings
6. replication_status: replicated|failed|pending|not_applicable|unknown
7. recommended_role: "evidence"|"context"|"premise"|"reject"
   "premise": the source describes what the claim tries to prove — circularity.
8. trust_level: high|medium|low|reject
9. reason: string

JSON only.
"""


# ── PHILOSOPHER: PRESUPPOSITIONS — use MODEL_SMART (Sonnet) ─────────────────
# Makes visible what the debate assumes without defending.
# The PREMISE_OF_THIS_WORK field is the most critical: detects circularity.

PHILOSOPHER_PRESUPPOSITIONS = """
You are an analytic philosopher specializing in epistemology.

CLAIM: {claim}
SOURCE TYPE: {source_type}
CLAIM TYPE: {claim_type}
SUBDOMAIN: {subdomain}

Extract all non-explicit presuppositions.

For each presupposition:
- content: state it precisely
- type: FACTUAL|CONCEPTUAL|METHODOLOGICAL|NORMATIVE
- status:
    UNEXAMINED: nobody in the debate has questioned it
    CONTESTED: someone implicitly questions it
    SHARED: all participants share it
    PREMISE_OF_THIS_WORK: it is exactly what the work tries to prove
    — if used as support it is circularity, critical flag
- impact_if_false: FATAL|MAJOR|MINOR
- what_if_false: what would happen to the claim if this presupposition were false

Return {{"presuppositions": [...]}}. JSON only.
"""

# ── PHILOSOPHER: DISAMBIGUATION — use MODEL_SMART (Sonnet) ──────────────────
# The root cause of 80% of apparent disagreements: two people answering
# different questions without realising it.

PHILOSOPHER_DISAMBIGUATION = """
You are an analytic philosopher.

CLAIM_A: {claim_a} (source: {source_a})
CLAIM_B: {claim_b} (source: {source_b})

These claims appear contradictory. Analyze:

- same_question: do they answer exactly the same question? true/false
- question_a: the exact question A is answering
- question_b: the exact question B is answering
- common_question: common question of which both are sub-questions (or null)
- disagreement_type: REAL|APPARENT|SEMANTIC|LEVEL_MISMATCH
- abstraction_level_a: empirical|interpretive|theoretical|paradigmatic|normative
- abstraction_level_b: same
- resolution: if the questions were made explicit, would the disagreement DISAPPEAR|REDUCE|PERSIST
- narrative: brief readable paragraph explaining the analysis

JSON only.
"""

# ── PHILOSOPHER: LOGICAL RELEVANCE — use MODEL_SMART (Sonnet) ───────────────
# Detects when evidence and claim are cited together but have no real relationship.
# The most common and most damaging case: domain_adjacent (same field, doesn't support the claim).

PHILOSOPHER_RELEVANCE = """
You are an analytic philosopher specializing in informal logic.

CLAIM: {claim}
PROPOSED EVIDENCE: {evidence}
DECLARED RELATION: "{declared_relation}"
CLAIM SUBDOMAIN: {subdomain}
EVIDENCE DOMAIN: {evidence_domain}

1. Reason step by step: does the evidence increase P(claim)?

2. Real relation_type:
   LOGICALLY_NECESSARY: E makes C true by logical necessity
   PROBABILISTICALLY_RELEVANT: E demonstrably increases P(C)
   CORRELATIVE_ONLY: co-occur but without causal mechanism
   DOMAIN_ADJACENT: same field but does not directly support the claim
   RHETORICALLY_ASSOCIATED: share vocabulary but no logical relation
   INDEPENDENT: they are independent — the citation is an error or rhetoric

3. If DOMAIN_ADJACENT or below:
   what_would_help: what evidence would be logically relevant

4. specificity: high|medium|low|mismatched
   If MISMATCHED: is the evidence a premise of the work itself? premise_risk: true/false

5. genericity_flag: true if evidence is generic for the domain
   but not specific to the claim's subdomain

JSON only.
"""

# ── PHILOSOPHER: CONDITIONAL STRUCTURE — use MODEL_SMART (Sonnet) ────────────
# Makes explicit the "if X then Y" structure of the argument.
# Detects necessary conditions nobody is discussing.

PHILOSOPHER_CONDITIONAL = """
You are an analytic philosopher.

CENTRAL CLAIM: {claim}
DEPENDENT NODES: {dependent_nodes}
PRESUPPOSITIONS ALREADY EXTRACTED: {presuppositions}

Build the complete conditional map.

For each condition:
- condition: state the condition precisely
- type: NECESSARY|SUFFICIENT|CONTRIBUTING|BLOCKING
- status: DEFENDED|ASSUMED|CONTESTED|IGNORED|PREMISE_OF_THIS_WORK
- collapse_if_false: true/false

- ignored_conditions: necessary conditions NOBODY is considering
  but that would change the analysis if false

- narrative: readable text:
  "If [C1] and [C2], then [CLAIM].
   If not [C1], the claim does not hold because...
   [C2] is being assumed without defense.
   Nobody is discussing [C4], a necessary condition."

JSON only with narrative field at the end.
"""

# ── PHILOSOPHER: SEMANTIC MAP — use MODEL_SMART (Sonnet) ─────────────────────
# Detects when the same term is used with different meanings.
# Does not arbitrate — maps the real semantic landscape of the debate.

PHILOSOPHER_SEMANTIC = """
You are an analytic philosopher.

TERM: {term}
USES IN THE DEBATE: {uses_with_sources}

- consistent_usage: same meaning across all sources? true/false
- meanings: if false, map each meaning with its sources
- semantic_disagreements: moments where the disagreement is actually semantic
- minimal_shared_definition: minimal shared definition or null
- conditions_per_definition: under what conditions each definition is valid

Do not decide which is correct. Map the terrain.
JSON only.
"""

# ── GAP FINDER — use MODEL_SMART (Sonnet) ────────────────────────────────────
# Detects what is missing: unanswered questions, absent evidence,
# unrepresented perspectives. Produces new knowledge, does not summarize.

GAP_FINDER = """
You are an epistemic analyst.

CURRENT GRAPH for case {case}:
Total nodes: {total_nodes}
Main claims: {main_claims}
Available evidence: {evidence_summary}
Detected presuppositions: {presuppositions}

Identify critical gaps:

1. questions_unanswered: important questions nobody is answering
2. evidence_missing: evidence that would shift the graph's balance if it existed
3. perspectives_absent: voices or disciplines not represented
4. assumptions_undefended: central claims with high centrality but no primary support
5. circular_chains: chains of claims that support each other without external anchor

For each gap:
- type: question|evidence|perspective|assumption|circularity
- content: precise description of the gap
- impact: CRITICAL|HIGH|MEDIUM
- how_to_fill: what would be needed to resolve it

Return {{"gaps": [...]}}. JSON only.
"""

# ── HEALTH REPORT — use MODEL_SMART (Sonnet) ─────────────────────────────────
# Final human-readable synthesis. Combines all graph analysis.

HEALTH_REPORT = """
You are a senior epistemic analyst. Generate the Epistemic Health Report for case {case}.

GRAPH STATISTICS: {stats}
MAIN CLAIMS: {main_claims}
DETECTED PRESUPPOSITIONS: {presuppositions}
IDENTIFIED GAPS: {gaps}
DISAMBIGUATIONS: {disambiguations}
AGENT-INFERRED NODES: {inferred_nodes}

Generate a report in markdown with these exact sections:

## 1. Question Map
Declared central question vs real questions being answered.
Important questions nobody is answering.

## 2. Central Conditional Structure
If [conditions] → main conclusion.
Which conditions are being assumed without defense.
Which necessary conditions nobody is discussing.

## 3. Non-thematized Presuppositions
Ordered by impact if false: FATAL → MAJOR → MINOR.

## 4. Semantic Map
Key terms with divergent meanings across sources.

## 5. Real Cruxes
The disagreements that, if resolved, would most shift the graph's balance.

## 6. Evidence Independence
Which sources bring genuinely independent evidence.
Which consensus is actually a single primary source propagated.

## 7. Critical Gaps
Unanswered questions, missing evidence, absent perspectives.

## 8. Rhetoric Flags
Claims with high rhetorical_weight and low evidential_weight.

## 9. New Knowledge Generated
Inferences not present in any source.
For each: nodes that imply it, falsification conditions.

## 10. System's Own Presuppositions
What EPISTEME is assuming when analyzing this case.
"""

# ── PHILOSOPHER BATCH — use MODEL_SMART (Sonnet) ─────────────────────────────
# Processes multiple representative claims in one call (structure v2).

PHILOSOPHER_BATCH = """
You are an analytic philosopher specializing in epistemology.

SUBDOMAIN: {subdomain}

CLAIMS (JSON array of representative claims from a semantic cluster):
{claims}

For EACH claim, extract at most {max_presup} non-explicit presuppositions.
Only include presuppositions with impact_if_false FATAL or MAJOR.
Skip MINOR presuppositions entirely.

For each presupposition:
- claim_id: the id of the claim it belongs to
- content: state it precisely (one sentence)
- type: FACTUAL|CONCEPTUAL|METHODOLOGICAL|NORMATIVE
- status: UNEXAMINED|CONTESTED|SHARED|PREMISE_OF_THIS_WORK
- impact_if_false: FATAL|MAJOR

Return {{"results": [{{"claim_id": "...", "presuppositions": [...]}}]}}. JSON only.
"""

# ── CRYSTALLIZE: CONDITIONAL CHAINS — use MODEL_SMART (Sonnet) ───────────────

CRYSTALLIZE_CHAINS = """
You are an epistemic analyst.

CASE: {case}
TOP CLAIMS (ranked by centrality): {ranked_claims}
THEME CLUSTERS (deduplicated presuppositions): {themes}
EXISTING GAPS: {gaps}

Extract 1-3 main conditional argument chains in this debate.

For each chain:
- id: short snake_case id (e.g. chain_rootclaim_validity)
- conclusion: the main conclusion this chain supports (one sentence)
- conclusion_claim_ids: list of claim ids from the input that support this conclusion
- conditions: list of {{"theme_id": "theme_XX", "role": "necessary|supporting", "impact": "FATAL|MAJOR|CRITICAL"}}
- gap_ids: list of gap node ids that weaken this chain (from input only)
- narrative: one paragraph explaining the chain

Only reference theme_ids and claim_ids that appear in the input. JSON only.
Return {{"chains": [...]}}.
"""

# ── CRYSTALLIZE: CRUXES — use MODEL_SMART (Sonnet) ───────────────────────────

CRYSTALLIZE_CRUXES = """
You are an epistemic analyst.

CASE: {case}
TOP CLAIMS: {ranked_claims}
THEMES: {themes}
CHAINS: {chains}
GAPS: {gaps}

Identify 4-8 real cruxes — disagreements that, if resolved, would most shift the debate.

For each crux:
- id: short snake_case id
- question: the precise disagreement in one sentence
- stakes: what collapses if resolved one way vs the other
- claim_ids: relevant claim ids from input
- theme_ids: relevant theme ids from input
- gap_ids: relevant gap ids from input
- resolution_path: what evidence or analysis would resolve it

Only use ids from the input. JSON only.
Return {{"cruxes": [...]}}.
"""

# ── GAP FINDER (compiled index) — use MODEL_SMART (Sonnet) ───────────────────

GAP_FINDER_COMPILED = """
You are an epistemic analyst.

CASE: {case}
COMPILED INDEX (compressed graph summary):
{compiled_index}

Identify critical gaps not already listed in the index gaps section.

For each gap:
- type: question|evidence|perspective|assumption|circularity
- content: precise description
- impact: CRITICAL|HIGH|MEDIUM
- how_to_fill: what would resolve it
- related_theme_ids: theme ids from the index (if any)
- related_claim_ids: claim ids from the index (if any)

Return {{"gaps": [...]}}. JSON only. Max 12 gaps.
"""

# ── HEALTH REPORT SECTION — use MODEL_SMART (Sonnet) ─────────────────────────
# One section per LLM call to avoid truncation.

HEALTH_REPORT_SECTION = """
You are a senior epistemic analyst writing section {section_num} of the Epistemic Health Report.

CASE: {case}
SECTION TO WRITE: {section_title}

COMPILED INDEX (themes, ranked claims, chains, cruxes, gaps):
{compiled_index}

Write ONLY section {section_num} in markdown. Start with the heading:
## {section_num}. {section_title}

Use node/theme/chain/crux ids from the index when referencing specific items.
Be precise and analytical. Do not write other sections.
"""

REPORT_SECTIONS = [
    "Question Map",
    "Central Conditional Structure",
    "Non-thematized Presuppositions",
    "Semantic Map",
    "Real Cruxes",
    "Evidence Independence",
    "Critical Gaps",
    "Rhetoric Flags",
    "New Knowledge Generated",
    "System's Own Presuppositions",
]
