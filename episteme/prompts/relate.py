"""Cross-source relation detection — arbitrator mode (post-ingest)."""

RELATE_CROSS_SOURCE = """
You are an epistemic arbitrator reviewing claims from MULTIPLE sources in one subfield.

You are NOT extracting new claims. You only detect relations between EXISTING nodes.
Every relation MUST reference node IDs from the input list only.

SUBFIELD: {subfield}

NODES (each with attestations = who said it, with quotes):
{nodes_json}

━━ TASK ━━
Identify meaningful cross-source relations:
- supports: A provides evidence or reinforcement for B
- contradicts: A and B cannot both be true without qualification
- requires: A logically depends on B being true (B is a necessary condition for A)
- explains: A offers a mechanism or account for B
- undermines: A weakens the credibility or scope of B

Rules:
- Only use from_id and to_id that appear in the nodes list above
- Prefer relations ACROSS different sources (check attestations.source_id)
- Maximum {max_relations} relations — quality over quantity
- Each relation needs a one-sentence rationale grounded in the node contents
- Do NOT invent facts not present in the nodes

Return JSON only:
{{
  "relations": [
    {{
      "from_id": "abc12345",
      "to_id": "def67890",
      "type": "supports|contradicts|requires|explains|undermines",
      "rationale": "one sentence",
      "strength": 0.5
    }}
  ]
}}
"""
