"""Haiku prompt for ambiguous cross-paper reconcile pairs."""

RECONCILE_PAIR_VERDICT = """
You judge the epistemic relationship between two nodes from DIFFERENT source papers.

CASE DEBATE POSITIONS (rival stances in this case):
{debate_positions}

NODE A (source: {source_a}):
[{type_a}] {content_a}
Quote A: "{quote_a}"

NODE B (source: {source_b}):
[{type_b}] {content_b}
Quote B: "{quote_b}"

Embedding similarity: {similarity:.3f}

Decide which verdict best describes the relationship:

- "same" — same assertion, different wording → MERGE into canonical node
- "compatible" — compatible paraphrases of one claim → MERGE
- "contradicts" — nodes assert OPPOSING positions on the SAME question.
                  Both nodes address the same underlying topic but draw
                  incompatible conclusions. The authors disagree on
                  WHETHER X happens / is true. Do NOT merge — instead, this
                  signals epistemic conflict to record explicitly.
                  Examples:
                    A: "X causes Y"           B: "X does not cause Y"
                    A: "Method M is valid"    B: "Method M is invalid"
                    A: "HSM cluster is strong evidence for zoonosis"
                    B: "HSM cluster is negligible evidence for zoonosis"
                  Both nodes must be the source author's own assertions
                  (not reported speech they are rebutting).
- "quantitative_divergence" — both nodes ASSERT that a phenomenon EXISTS
                  but offer DIFFERENT estimates of its magnitude/frequency.
                  Both authors AGREE on the underlying fact; they disagree
                  only on the number. This is NOT a contradiction.
                  Example:
                    A: "wet markets host 50% of outbreaks"
                    B: "wet markets host 2 of 5 large outbreaks"
                    → both agree markets host outbreaks; only the figure
                      differs → quantitative_divergence (NOT contradicts)
                  Contrast with "contradicts", where authors disagree on
                  WHETHER the phenomenon happens at all. Do NOT merge.
- "distinct" — different claims on different questions → do NOT merge,
               no further action

CRITICAL FILTERS (return "distinct" if any apply):
- Either node has attributed_to="opposing_position" or "reported_speech"
  (the author is REPORTING someone else's position, not asserting their own)
- Either node has is_rhetorical_move=true (ad hominem, dismissal — not
  an evidential claim that can be contradicted)
- The nodes address different sub-questions of the debate, even if they
  share keywords

A real contradiction requires both authors to be ASSERTING their own
respective positions on THE SAME underlying question.

Return JSON only:
{{
  "verdict": "same | compatible | contradicts | quantitative_divergence | distinct",
  "reason": "one sentence explaining the verdict",
  "shared_question": "the underlying question both nodes address, if verdict is contradicts or quantitative_divergence; else empty"
}}
"""
