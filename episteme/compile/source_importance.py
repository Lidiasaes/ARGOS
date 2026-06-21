"""Post-graph epistemic importance per source — discovered, not pre-declared."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from episteme.config import CASES_DIR
from episteme.core.graph import GraphStore, attestation_source_key, ensure_attestations
from episteme.compile.crystallize import load_compiled

HIGH_VALUE_EW = 0.7
NODE_TYPES = ("claim", "evidence")


def source_importance_path(case: str) -> Path:
    d = CASES_DIR / case / "compiled"
    d.mkdir(parents=True, exist_ok=True)
    return d / "source_importance.json"


def _node_sources(node: dict) -> set[str]:
    keys = {attestation_source_key(a) for a in ensure_attestations(node)}
    return {k for k in keys if k}


def _crux_claim_ids(index: dict | None) -> set[str]:
    if not index:
        return set()
    ids: set[str] = set()
    for crux in index.get("cruxes", []):
        ids.update(crux.get("claim_ids", []))
    return ids


def compute_source_importance(
    case: str,
    store: GraphStore,
    index: dict | None = None,
    roles: dict[str, dict] | None = None,
) -> dict:
    """
    Level 3: epistemic importance emerges from the graph after analysis.

    Metrics per source:
    - claims_count / evidence_count — nodes this source attests
    - unique_claims_count — sole attester (brilliant-ignored signal)
    - high_value_unique — unique nodes with evidential_weight >= threshold
    - crux_claims_count — nodes linked to crux anchor lists
    - crux_exclusive_count — sole attester on a crux anchor claim
    - epistemic_risk — weighted score: if we remove this source, what do we lose?
    - importance_tier — central | contributory | latent_gem | peripheral
    """
    if index is None:
        index = load_compiled(case)

    crux_ids = _crux_claim_ids(index)
    stats: dict[str, dict] = defaultdict(
        lambda: {
            "claims_count": 0,
            "evidence_count": 0,
            "unique_claims_count": 0,
            "high_value_unique": 0,
            "crux_claims_count": 0,
            "crux_exclusive_count": 0,
            "mean_evidential_weight": 0.0,
            "epistemic_risk": 0.0,
            "importance_tier": "peripheral",
        }
    )
    ew_sums: dict[str, float] = defaultdict(float)
    ew_counts: dict[str, int] = defaultdict(int)

    for ntype in NODE_TYPES:
        for node in store.get_nodes_by_type(ntype):
            sources = _node_sources(node)
            if not sources:
                continue
            ew = float(node.get("evidential_weight", 0) or 0)
            nid = node["id"]
            in_crux = nid in crux_ids
            is_unique = len(sources) == 1

            for sid in sources:
                key = "claims_count" if ntype == "claim" else "evidence_count"
                stats[sid][key] += 1
                ew_sums[sid] += ew
                ew_counts[sid] += 1

                if is_unique:
                    stats[sid]["unique_claims_count"] += 1
                    if ew >= HIGH_VALUE_EW:
                        stats[sid]["high_value_unique"] += 1

                if in_crux:
                    stats[sid]["crux_claims_count"] += 1
                    if is_unique:
                        stats[sid]["crux_exclusive_count"] += 1

    sources_out = []
    for sid, s in sorted(stats.items(), key=lambda x: -x[1]["crux_claims_count"]):
        n = ew_counts[sid] or 1
        s["mean_evidential_weight"] = round(ew_sums[sid] / n, 3)
        s["epistemic_risk"] = round(
            s["high_value_unique"] * 2.0
            + s["crux_exclusive_count"] * 3.0
            + s["unique_claims_count"] * 0.5
            + s["crux_claims_count"] * 0.25,
            2,
        )
        s["importance_tier"] = _importance_tier(s)
        entry = {
            "source_id": sid,
            **s,
        }
        if roles and sid in roles:
            entry["role"] = roles[sid].get("role", "unknown")
            entry["role_method"] = roles[sid].get("method", "")
            entry["label"] = roles[sid].get("label", sid)
        sources_out.append(entry)

    sources_out.sort(key=lambda x: (-x["epistemic_risk"], -x["crux_claims_count"]))

    manifest = {
        "case": case,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "high_value_ew_threshold": HIGH_VALUE_EW,
        "sources": sources_out,
        "stats": {
            "sources_with_nodes": len(sources_out),
            "latent_gems": sum(1 for s in sources_out if s["importance_tier"] == "latent_gem"),
            "central": sum(1 for s in sources_out if s["importance_tier"] == "central"),
        },
    }
    return manifest


def _importance_tier(s: dict) -> str:
    if s["crux_exclusive_count"] >= 1 or s["crux_claims_count"] >= 3:
        return "central"
    total = s.get("claims_count", 0) + s.get("evidence_count", 0)
    if s["high_value_unique"] >= 2 and total and s["unique_claims_count"] >= total * 0.6:
        return "latent_gem"
    if s["crux_claims_count"] >= 1 or s["unique_claims_count"] >= 2:
        return "contributory"
    return "peripheral"


def save_source_importance(case: str, manifest: dict) -> Path:
    path = source_importance_path(case)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_source_importance(case: str) -> dict | None:
    path = source_importance_path(case)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def run_source_importance(case: str, store: GraphStore, roles: dict | None = None) -> dict:
    index = load_compiled(case)
    manifest = compute_source_importance(case, store, index, roles=roles)
    path = save_source_importance(case, manifest)
    print(f"  Source importance: {len(manifest['sources'])} sources -> {path.name}")
    for s in manifest["sources"][:5]:
        print(
            f"    {s.get('label', s['source_id'])[:50]}: "
            f"tier={s['importance_tier']} risk={s['epistemic_risk']} "
            f"crux={s['crux_claims_count']} unique={s['unique_claims_count']}"
        )
    return manifest
