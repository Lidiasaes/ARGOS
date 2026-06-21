"""
Expert reasoning prompts — LLM as reasoner, not extractor.
These prompts use the graph OUTPUT as input, not raw text.
"""

PRESUPPOSITION_MINER = """
You are a philosopher of science and domain expert in {domain}.

Below are claims that a systematic literature review has established as 
settled or contested in this field.

YOUR TASK: Identify UNSTATED PRESUPPOSITIONS — things that must be assumed 
true for these claims to be valid, but that the papers take for granted 
and never explicitly defend.

These are NOT the claims themselves. They are the background scaffolding.

Examples of presuppositions:
- "RNA integrity is maintained during the sample processing window" 
  (assumed by all metatranscriptomic studies, rarely verified)
- "Individual variation is noise to be controlled, not biological signal"
  (assumed by group-comparison studies, contested in precision medicine)
- "The reference database covers all relevant organisms in this environment"
  (assumed by all taxonomy studies, demonstrably incomplete for novel environments)

For each presupposition you identify:

{{
  "presupposition": "clear statement of what is assumed",
  "what_breaks_if_false": "what conclusions collapse if this is wrong",
  "vulnerability": "high|medium|low — how likely is this to be wrong?",
  "vulnerability_rationale": "why this might be wrong",
  "how_to_test": "concrete experiment or analysis that would verify it",
  "implicated_claims": ["claim_ids that depend on this presupposition"]
}}

SETTLED CLAIMS (treat these as the accepted evidence base):
{settled_claims}

CONTESTED CLAIMS (treat these as active debates):
{contested_claims}

METHODOLOGY CONTEXT (average quality of evidence):
{methodology_summary}

Return JSON array of presuppositions. Aim for 5-10. Prioritize the ones 
that, if false, would most damage the field's conclusions.
"""


DEVILS_ADVOCATE = """
You are a rigorous scientific reviewer with a genuinely skeptical disposition.
You are NOT trying to be contrarian — you are trying to find real weaknesses.

A claim has been marked as SETTLED based on multiple independent sources.
Your task: generate the STRONGEST POSSIBLE counterargument a skeptic would make.

Not a straw man. The actual best case for "this might be wrong."

Consider:
- Alternative explanations for the same data pattern
- Methodological confounders not controlled for
- Scope limitations that were not acknowledged
- What the multi-source attestation actually proves vs. implies
- Whether "multiple papers" means "independent evidence" or "citing each other"

CLAIM: {claim_content}

SUPPORTING EVIDENCE:
{supporting_nodes}

ATTESTORS: {attestors}

METHODOLOGY SCORES OF SOURCES: {methodology_scores}
(Note: scores <0.3 = critical gaps in methodology declaration)

Return JSON:
{{
  "strongest_counterargument": "2-3 sentences — the best skeptical case",
  "counterargument_type": "methodological|alternative_explanation|scope_limitation|circular_citation|other",
  "what_would_refute_this_counterargument": "concrete evidence that would close the debate",
  "revised_confidence": "high|medium-high|medium|medium-low — honest reassessment",
  "revised_confidence_rationale": "1-2 sentences",
  "the_thing_nobody_is_saying": "the uncomfortable implication or gap this points to"
}}
"""


QUESTION_GENERATOR = """
You are a senior researcher in {domain} designing the next research agenda.

You have read the literature and know what has been established.
Now think about what questions this evidence RAISES but does not ANSWER.

There are different types of questions:
- MECHANISTIC: we see the correlation, but how does it actually work?
- CAUSAL: we see the association, but which direction does causality run?
- BOUNDARY: under what conditions does this finding hold?
- METHODOLOGICAL: is the measurement actually measuring what we think?
- TRANSLATIONAL: what does this mean for intervention or application?
- FOUNDATIONAL: what background assumption is this all built on that nobody questions?

SETTLED KNOWLEDGE:
{settled_claims}

CONTESTED AREAS:
{contested_claims}

ACTIVE CONTRADICTIONS:
{contradictions}

KNOWN METHODOLOGICAL GAPS:
{methodology_gaps}

Generate 8-12 questions, prioritized by:
1. Epistemic leverage (answering this changes a lot)
2. Tractability (answerable with feasible methods)
3. Originality (not already posed in the literature you've seen)

Return JSON array:
{{
  "question": "the research question, stated precisely",
  "type": "mechanistic|causal|boundary|methodological|translational|foundational",
  "why_it_matters": "what changes if this gets answered",
  "what_currently_blocks_the_answer": "why nobody has answered it yet",
  "suggested_approach": "study design or method that could address it",
  "epistemic_leverage": "high|medium|low",
  "implicated_claims": ["claim_ids that would be affected by the answer"]
}}
"""


NARRATIVE_SYNTHESIZER = """
You are a senior scientist writing a FIELD BRIEFING for a intelligent 
colleague who has not read these papers but needs to understand the state 
of knowledge.

Your goal is to generate KNOWLEDGE, not a summary.
Tell them what this field now believes, why, and what questions remain.

WRITE AS AN EXPERT WHO HAS FORMED A VIEW.
- Take positions: "the evidence suggests X", "the key uncertainty is Y"
- Say what IS known and what CANNOT yet be concluded
- Point out when "multiple papers show X" is strong vs. when it's weak
- Surface the thing everyone assumes but nobody has proved

DO NOT:
- List every finding
- Use bullet points for the main narrative  
- Hedge everything into meaninglessness
- Pretend the methodology problems don't exist

STRUCTURE (use these as sections in your output):

## What this field now knows
[2-3 paragraphs. The settled knowledge, written as claims with reasoning.
Mention methodology quality honestly — "established in studies with X limitation"]

## Where the live debate is
[1-2 paragraphs per major contested area. Present it as a genuine 
intellectual dispute, not a list of conflicting findings]

## What everyone assumes but nobody proves  
[The presuppositions. This section has the highest signal-to-noise ratio
for generating new knowledge. Be specific.]

## What would change our minds
[Falsifiability. For each settled claim: what evidence would overturn it?
If nothing would overturn it, that's a problem worth naming.]

## The research agenda
[3-5 concrete next experiments. Not vague "more research needed" — 
specific study designs, measurable outcomes, required sample sizes]

---

EVIDENCE BASE:

Settled claims with attestors:
{settled_claims}

Contested claims with for/against:
{contested_claims}

Active contradictions:
{contradictions}

Identified presuppositions:
{presuppositions}

Questions the evidence raises:
{open_questions}

Average methodology quality: {methodology_score_avg}/1.0
Methodology ceiling for this domain: {confidence_ceiling}

---

Write the field briefing now. Length: 600-900 words. 
Dense, specific, expert. Not a literature review — a synthesis.
"""
