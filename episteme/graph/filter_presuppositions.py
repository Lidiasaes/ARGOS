"""Filter presuppositions to those referenced in compiled index or high weight."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from episteme.config import CASES_DIR
from episteme.compile.crystallize import load_compiled
from episteme.compile.references import collect_referenced_ids

PRESUPPOSITION_MIN_WEIGHT = 0.4


def filter_presuppositions(
    case: str,
    min_weight: float = PRESUPPOSITION_MIN_WEIGHT,
    dry_run: bool = False,
    backup: bool = True,
    all_themes: bool = False,
) -> dict:
    """
    Keep all non-presupposition nodes.
    Keep presuppositions only if their id appears in the compiled
    index's referenced ids. min_weight/evidential_weight is NOT used as
    a fallback: every presupposition currently gets evidential_weight=0.5
    by default (structure.py never assigns it a differentiated value),
    so no min_weight threshold could meaningfully distinguish between
    them. The min_weight parameter is kept for forward compatibility and
    recorded in the output stats, but has no effect on what gets removed
    until a real per-presupposition scoring criterion exists.
    Returns stats dict.
    """
    compiled = load_compiled(case)
    if compiled is None:
        raise FileNotFoundError(f"No compiled index for case '{case}'. Run crystallize first.")

    graph_path = CASES_DIR / case / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    referenced = collect_referenced_ids(compiled, all_themes=all_themes)
    before = len(graph)
    presup_before = sum(1 for n in graph.values() if n.get("type") == "presupposition")

    kept = {}
    removed_presups = []
    for nid, node in graph.items():
        if node.get("type") != "presupposition":
            kept[nid] = node
            continue
        # Presuppositions: only those anchored in compiled index (not ew — defaults are 0.5+)
        if nid in referenced:
            kept[nid] = node
        else:
            removed_presups.append(nid)

    presup_after = sum(1 for n in kept.values() if n.get("type") == "presupposition")
    stats = {
        "case": case,
        "nodes_before": before,
        "nodes_after": len(kept),
        "presuppositions_before": presup_before,
        "presuppositions_after": presup_after,
        "presuppositions_removed": presup_before - presup_after,
        "referenced_ids_count": len(referenced),
        "min_weight": min_weight,
        "all_themes": all_themes,
    }

    if dry_run:
        stats["dry_run"] = True
        return stats

    pruned_relations = 0
    for node in kept.values():
        before_rels = node.get("relations", [])
        after_rels = [r for r in before_rels if r.get("target") in kept]
        pruned_relations += len(before_rels) - len(after_rels)
        node["relations"] = after_rels
    stats["dangling_relations_pruned"] = pruned_relations

    if backup:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup_path = graph_path.with_name(f"graph_backup_{ts}.json")
        shutil.copy2(graph_path, backup_path)
        stats["backup"] = str(backup_path)

    graph_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats
