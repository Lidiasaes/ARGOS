"""Build debate state from graph relations + attestations (no LLM)."""

import json
from pathlib import Path

from episteme.config import CASES_DIR
from episteme.core.graph import GraphStore, ensure_attestations, group_attestations_by_source, unique_attestation_source_count
from episteme.pipeline.reconcile import compute_epistemic_fields


def debate_state_path(case: str) -> Path:
    d = CASES_DIR / case / "compiled"
    d.mkdir(parents=True, exist_ok=True)
    return d / "debate_state.json"


def _node_summary(node: dict | None) -> dict | None:
    if not node:
        return None
    return {
        "claim_id": node["id"],
        "canonical": node.get("content", ""),
        "type": node.get("type"),
        "attestations": ensure_attestations(node),
    }


def _collect_incoming(store: GraphStore) -> dict[str, list[tuple[dict, dict]]]:
    incoming: dict[str, list[tuple[dict, dict]]] = {}
    for node in store.get_all_nodes():
        for rel in node.get("relations", []):
            tid = rel.get("target")
            if tid:
                incoming.setdefault(tid, []).append((node, rel))
    return incoming


def build_debate_state(case: str, store: GraphStore) -> dict:
    """
    Derive per-node debate structure from relations (incl. cross-source relate edges).
    """
    incoming = _collect_incoming(store)
    nodes_out = []

    for node in store.get_all_nodes():
        if node.get("type") not in ("claim", "evidence", "question"):
            continue

        nid = node["id"]
        fields = compute_epistemic_fields(store, nid)
        entry = {
            "claim_id": nid,
            "canonical": node.get("content", ""),
            "type": node.get("type"),
            "subfield": node.get("subfield", ""),
            "evidential_weight": node.get("evidential_weight") or 0,
            "attestations": ensure_attestations(node),
            "support_count": fields.get("support_count", 0),
            "contradict_count": fields.get("contradict_count", 0),
            "epistemic_status": fields.get("epistemic_status", ""),
            "requires_true": [],
            "if_false_then_falls": [],
            "contradicted_by": [],
            "supported_by": [],
            "undermined_by": [],
            "explained_by": [],
        }

        for rel in node.get("relations", []):
            target = store.get_node(rel.get("target", ""))
            summary = _node_summary(target)
            if not summary:
                continue
            summary["relation"] = {
                "type": rel.get("type"),
                "strength": rel.get("strength"),
                "rationale": rel.get("rationale", ""),
                "source": rel.get("source", ""),
            }
            rtype = rel.get("type")
            if rtype == "requires":
                entry["requires_true"].append(summary)
            elif rtype == "contradicts":
                entry["contradicted_by"].append(summary)
            elif rtype == "supports":
                entry["supported_by"].append(summary)
            elif rtype == "undermines":
                entry["undermined_by"].append(summary)
            elif rtype == "explains":
                entry["explained_by"].append(summary)

        for src_node, rel in incoming.get(nid, []):
            rtype = rel.get("type")
            summary = _node_summary(src_node)
            if not summary:
                continue
            summary["relation"] = {
                "type": rtype,
                "strength": rel.get("strength"),
                "rationale": rel.get("rationale", ""),
                "source": rel.get("source", ""),
            }
            if rtype == "requires":
                entry["if_false_then_falls"].append(summary)

        has_edges = any(
            entry[k]
            for k in (
                "requires_true",
                "if_false_then_falls",
                "contradicted_by",
                "supported_by",
                "undermined_by",
                "explained_by",
            )
        )
        if has_edges or len(entry["attestations"]) > 1:
            nodes_out.append(entry)

    manifest = {
        "case": case,
        "nodes": nodes_out,
        "stats": {
            "total_with_structure": len(nodes_out),
            "multi_source": sum(
                1 for n in nodes_out if unique_attestation_source_count(n["attestations"]) > 1
            ),
            "with_contradictions": sum(1 for n in nodes_out if n["contradicted_by"]),
            "with_dependencies": sum(1 for n in nodes_out if n["requires_true"] or n["if_false_then_falls"]),
        },
    }
    debate_state_path(case).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def load_debate_state(case: str) -> dict | None:
    path = debate_state_path(case)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None
