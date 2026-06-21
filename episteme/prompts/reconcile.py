"""Haiku prompt for ambiguous cross-paper reconcile pairs."""

RECONCILE_PAIR_VERDICT = """
You judge whether two epistemic nodes from DIFFERENT source papers should be merged
into one canonical node with multiple attestations.

CASE DEBATE POSITIONS (rival stances in this case — do not merge across them):
{debate_positions}

NODE A (source: {source_a}):
[{type_a}] {content_a}
Quote A: "{quote_a}"

NODE B (source: {source_b}):
[{type_b}] {content_b}
Quote B: "{quote_b}"

Embedding similarity: {similarity:.3f}

If the nodes assert OPPOSITE conclusions on the same disputed point (rival positions above),
verdict MUST be "distinct" even if wording overlaps.

Verdict options:
- "same" — same assertion, different wording → merge
- "compatible" — compatible paraphrases of one claim → merge
- "distinct" — different claims that must stay separate → do NOT merge

Return JSON only:
{{
  "verdict": "same | compatible | distinct",
  "reason": "one sentence"
}}
"""
