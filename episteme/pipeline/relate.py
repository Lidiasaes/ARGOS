"""Cross-source relation pass — group by subfield, arbitrator LLM, validate IDs."""

import json
from collections import defaultdict
from pathlib import Path

from episteme.config import CASES_DIR, MODEL_SMART, RECONCILE_CONFLICT_TYPES
from episteme.core.cache import Cache, content_hash
from episteme.core.graph import GraphStore, ensure_attestations
from episteme.core.llm import call_llm
from episteme.pipeline.reconcile import update_epistemic_fields
from episteme.prompts.relate import RELATE_CROSS_SOURCE

RELATE_MIN_EW = 0.6
RELATE_BATCH_SIZE = 25
RELATE_MAX_PER_SUBFIELD = 50
RELATE_MAX_RELATIONS = 12
VALID_TYPES = frozenset({"supports", "contradicts", "requires", "explains", "undermines"})


def _cross_links_path(case: str) -> Path:
    d = CASES_DIR / case / "compiled"
    d.mkdir(parents=True, exist_ok=True)
    return d / "cross_links.json"


def _node_payload(node: dict) -> dict:
    atts = ensure_attestations(node)
    return {
        "id": node["id"],
        "type": node.get("type"),
        "content": node.get("content", ""),
        "evidential_weight": node.get("evidential_weight", 0),
        "attestations": [
            {
                "source_id": a.get("source_id"),
                "author": a.get("author"),
                "quote": (a.get("quote") or "")[:200],
            }
            for a in atts
        ],
    }


def _group_candidates(store: GraphStore) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for ntype in ("claim", "evidence", "question"):
        for node in store.get_nodes_by_type(ntype):
            if node.get("evidential_weight", 0) < RELATE_MIN_EW:
                continue
            sf = (node.get("subfield") or "").strip() or "_general"
            groups[sf].append(node)
    for sf in groups:
        groups[sf].sort(key=lambda n: -n.get("evidential_weight", 0))
        groups[sf] = groups[sf][:RELATE_MAX_PER_SUBFIELD]
    return {k: v for k, v in groups.items() if len(v) >= 2}


def _has_relation(node: dict, target_id: str, rel_type: str) -> bool:
    for rel in node.get("relations", []):
        if rel.get("target") == target_id and rel.get("type") == rel_type:
            return True
    return False


def _validate_relations(raw: list, valid_ids: set[str]) -> list[dict]:
    seen = set()
    out = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        fid = r.get("from_id")
        tid = r.get("to_id")
        rtype = r.get("type")
        if fid not in valid_ids or tid not in valid_ids or fid == tid:
            continue
        if rtype not in VALID_TYPES:
            continue
        key = (fid, tid, rtype)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "from_id": fid,
            "to_id": tid,
            "type": rtype,
            "rationale": (r.get("rationale") or "").strip(),
            "strength": min(max(float(r.get("strength", 0.6)), 0.3), 0.95),
        })
    return out


def run_relate(case: str, cache: Cache, store: GraphStore) -> dict:
    groups = _group_candidates(store)
    if not groups:
        print("  No subfield groups with 2+ candidates — skip relate")
        return {"relations": [], "stats": {"groups": 0, "added": 0}}

    all_relations: list[dict] = []
    added = 0
    skipped = 0

    for subfield, nodes in sorted(groups.items()):
        batches = [
            nodes[i : i + RELATE_BATCH_SIZE]
            for i in range(0, len(nodes), RELATE_BATCH_SIZE)
        ]
        print(f"\n  subfield: {subfield} ({len(nodes)} nodes, {len(batches)} batch(es))")

        for batch_idx, batch in enumerate(batches):
            valid_ids = {n["id"] for n in batch}
            payload = [_node_payload(n) for n in batch]
            cache_key = f"relate::{subfield}::batch_{batch_idx}::{content_hash(payload)}"

            result = cache.get_or_run(
                "agent",
                cache_key,
                lambda p=payload, sf=subfield: call_llm(
                    RELATE_CROSS_SOURCE.format(
                        subfield=sf,
                        nodes_json=json.dumps(p, ensure_ascii=False, indent=2),
                        max_relations=RELATE_MAX_RELATIONS,
                    ),
                    model=MODEL_SMART,
                    max_tokens=2000,
                    parse_json=True,
                    label="relate_cross_source",
                ),
            )

            if not isinstance(result, dict) or result.get("parse_error"):
                print(f"    batch {batch_idx}: parse failed")
                continue

            validated = _validate_relations(result.get("relations", []), valid_ids)
            print(f"    batch {batch_idx}: {len(validated)} relations")

            for rel in validated:
                rel["subfield"] = subfield
                all_relations.append(rel)
                from_id = rel["from_id"]
                if _has_relation(store.get_node(from_id) or {}, rel["to_id"], rel["type"]):
                    skipped += 1
                    continue
                store.add_relation(
                    from_id,
                    rel["to_id"],
                    rel["type"],
                    strength=rel["strength"],
                    rationale=rel["rationale"],
                    source="relate",
                )
                added += 1
                if rel["type"] in RECONCILE_CONFLICT_TYPES:
                    update_epistemic_fields(store, rel["to_id"])

    manifest = {
        "case": case,
        "relations": all_relations,
        "stats": {
            "groups": len(groups),
            "proposed": len(all_relations),
            "added_to_graph": added,
            "skipped_duplicate": skipped,
        },
    }
    _cross_links_path(case).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  Relate: {added} new relations ({len(all_relations)} proposed, {skipped} dup skip)")
    print(f"  Saved: cases/{case}/compiled/cross_links.json")

    from episteme.compile.debate_state import build_debate_state

    debate = build_debate_state(case, store)
    ds = debate.get("stats", {})
    print(
        f"  Debate state: {ds.get('total_with_structure', 0)} nodes "
        f"({ds.get('multi_source', 0)} multi-source, "
        f"{ds.get('with_contradictions', 0)} with contradictions)"
    )
    return manifest


def load_cross_links(case: str) -> dict | None:
    path = _cross_links_path(case)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None
