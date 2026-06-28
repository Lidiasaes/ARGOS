"""
Polarity verification — detects the noA problem:
LLM extracted claim X but source actually defends NOT-X.

Architecture:
  Signal A — attributed_to field set at extraction time in EXTRACTOR_V4.
              If the extractor sees "critics argue X, but we show NOT-X"
              it sets attributed_to="opposing_position" on the X node.

  Signal B — dedicated haiku call on (textual_evidence, content) pair.
              Independent verification: does the verbatim quote support
              the extracted claim or argue against it?

Compound decision table:
  A=opposing + B=argues_against  → "high"     (both signals agree: noA)
  A=source   + B=argues_against  → "conflict" (most valuable for review)
  A=*        + B=reports_others  → "low"      (soft flag)
  A=*        + B=asserts         → "none"     (safe)

TODO v.0.1.0 — NLI Local Validation (Signal C, offline deterministic):
  Model:  cross-encoder/nli-deberta-v3-large
          OR mjwong/e5-large-v2-nli  (scientific NLI, better out-of-domain)
          NOT nli-deberta-v3-small (trained on SNLI, fails on technical text)
  Input:  premise=textual_evidence (50-80 words, NOT the full chunk —
          full chunk is out-of-distribution for models trained on SNLI/MultiNLI)
          hypothesis=content (1 sentence)
  Labels: [contradiction, entailment, neutral] — argmax gives verdict
  Integration: add as third tiebreaker when A and B conflict.
  Compound update:
    A=source + B=argues_against + C=contradiction → "high"   (triple signal)
    A=source + B=argues_against + C=entailment   → "low"    (B/C disagree)
    A=source + B=asserts        + C=contradiction → "conflict" (B/C disagree)
  Load pattern:
    _nli_model = None
    def _get_nli_model():
        global _nli_model
        if _nli_model is None:
            try:
                from sentence_transformers import CrossEncoder
                _nli_model = CrossEncoder("cross-encoder/nli-deberta-v3-large")
            except ImportError:
                _nli_model = "unavailable"
        return None if _nli_model == "unavailable" else _nli_model
"""

from __future__ import annotations

from episteme.config import MODEL_FAST
from episteme.core.llm import call_llm

_POLARITY_PROMPT = """
You are verifying whether an extracted claim correctly reflects
what a source ASSERTS as its own position.

TEXTUAL EVIDENCE (verbatim from source):
{evidence}

EXTRACTED CLAIM:
{content}

Does the source ASSERT the extracted claim as true, or does it argue
AGAINST it (e.g., presents it as another position to rebut)?

Return JSON only:
{{
  "verdict": "asserts" | "argues_against" | "reports_others",
  "confidence": 0.0-1.0,
  "reason": "one sentence max"
}}

Rules:
- "asserts": the source author defends this claim as true
- "argues_against": the source presents this as a position to rebut or refute
- "reports_others": the source reports what others say, no clear endorsement
- If the textual evidence is ambiguous, set confidence < 0.6
"""


def check_polarity(node_data: dict, cache, chunk_index: str) -> dict:
    """
    Run compound polarity check (Signal A + Signal B) for a single node.

    Signal A is read from node_data.attributed_to (set at extraction).
    Signal B is a dedicated haiku call on (textual_evidence, content).

    Returns dict with keys: verdict, confidence, reason, skipped.
    Cached by chunk_index + content hash — won't re-run on repeated pipeline calls.
    """
    node_type = (node_data.get("type") or "claim").lower()
    if node_type not in ("claim", "evidence"):
        return {
            "verdict": "asserts",
            "confidence": 1.0,
            "reason": "not checked for this node type",
            "skipped": True,
        }

    # Signal A — attributed_to set at extraction time
    attributed = node_data.get("attributed_to", "source_author")
    if attributed in ("opposing_position", "reported_speech"):
        return {
            "verdict": "argues_against",
            "confidence": 0.9,
            "reason": f"attributed_to={attributed} flagged at extraction",
            "skipped": False,
        }

    evidence = (
        node_data.get("textual_evidence")
        or node_data.get("supporting_quote")
        or ""
    ).strip()
    content = (node_data.get("content") or "").strip()

    if not evidence or not content:
        return {
            "verdict": "asserts",
            "confidence": 0.5,
            "reason": "no textual_evidence to verify against",
            "skipped": True,
        }

    # Signal B — dedicated haiku call (cached, ~$0.00006 per node)
    content_key = content[:60].replace(" ", "_").replace("/", "_")
    cache_key = f"polarity_v1::{chunk_index}::{content_key}"

    result = cache.get_or_run(
        "polarity",
        cache_key,
        lambda e=evidence, c=content: call_llm(
            _POLARITY_PROMPT.format(evidence=e[:600], content=c),
            model=MODEL_FAST,
            max_tokens=80,
            parse_json=True,
            label="polarity_check",
        ),
    )

    if not isinstance(result, dict) or result.get("parse_error"):
        return {
            "verdict": "asserts",
            "confidence": 0.5,
            "reason": "parse error in polarity check",
            "skipped": True,
        }

    return {
        "verdict": result.get("verdict", "asserts"),
        "confidence": float(result.get("confidence", 0.5)),
        "reason": result.get("reason", ""),
        "skipped": False,
    }


def polarity_risk_level(polarity_result: dict, node_data: dict) -> str:
    """
    Compound risk from Signal A (attributed_to) + Signal B (haiku verdict).

    Returns: "none" | "low" | "high" | "conflict"
      "high"     — A and B both flag noA. Hard needs_review.
      "conflict" — A says source_author but B says argues_against.
                   Most epistemically interesting case. Surface for review.
      "low"      — Weak signal (low confidence or reports_others). Soft flag.
      "none"     — Both signals agree claim is correctly attributed.
    """
    if polarity_result.get("skipped"):
        return "none"

    verdict = polarity_result.get("verdict", "asserts")
    confidence = polarity_result.get("confidence", 0.5)
    attributed = node_data.get("attributed_to", "source_author")

    if attributed == "opposing_position" and verdict == "argues_against":
        return "high"
    if attributed == "source_author" and verdict == "argues_against" and confidence >= 0.7:
        return "conflict"
    if verdict == "argues_against" and confidence < 0.7:
        return "low"
    if verdict == "reports_others":
        return "low"
    return "none"
