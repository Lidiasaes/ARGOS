"""HypothesisAgent runner — one LLM call per crux."""

import json
from pathlib import Path

from episteme.config import CASES_DIR, MODEL_SMART
from episteme.compile.crystallize import load_compiled
from episteme.compile.debate_state import load_debate_state
from episteme.core.cache import Cache, content_hash
from episteme.core.graph import GraphStore, ensure_attestations
from episteme.core.llm import call_llm
from episteme.pipeline.hypothesis_verify import verify_hypothesis
from episteme.prompts.hypothesis import HYPOTHESIS_AGENT

MAX_SUPPORT = 5
MAX_CONTRA = 5
MAX_GAPS = 4


def hypotheses_dir(case: str) -> Path:
    d = CASES_DIR / case / "hypotheses"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _node_bundle(node: dict | None) -> dict | None:
    if not node:
        return None
    return {
        "id": node["id"],
        "content": node.get("content", ""),
        "type": node.get("type"),
        "attestations": ensure_attestations(node),
    }


def _debate_by_id(case: str) -> dict[str, dict]:
    state = load_debate_state(case)
    if not state:
        return {}
    return {n["claim_id"]: n for n in state.get("nodes", [])}


def _gather_crux_evidence(crux: dict, store: GraphStore, debate_by_id: dict[str, dict]) -> tuple[list, list, list]:
    supporting = []
    contradicting = []
    gaps = []

    for cid in crux.get("claim_ids", [])[:MAX_SUPPORT]:
        b = _node_bundle(store.get_node(cid))
        if b:
            supporting.append(b)

    seen_contra = set()
    for cid in crux.get("claim_ids", []):
        dnode = debate_by_id.get(cid, {})
        for c in dnode.get("contradicted_by", []):
            cid2 = c.get("claim_id")
            if cid2 and cid2 not in seen_contra:
                seen_contra.add(cid2)
                contradicting.append(c)
            if len(contradicting) >= MAX_CONTRA:
                break
        if len(contradicting) >= MAX_CONTRA:
            break

    for gid in crux.get("gap_ids", [])[:MAX_GAPS]:
        g = store.get_node(gid)
        if g:
            gaps.append({"id": gid, "content": g.get("content", "")})

    return supporting, contradicting, gaps


def run_hypothesis(
    case: str,
    cache: Cache,
    store: GraphStore,
    crux_id: str | None = None,
) -> list[dict]:
    index = load_compiled(case)
    if not index:
        print("  No compiled index — run crystallize first")
        return []

    cruxes = index.get("cruxes", [])
    if crux_id:
        cruxes = [c for c in cruxes if c.get("id") == crux_id]
        if not cruxes:
            print(f"  Crux {crux_id} not found")
            return []

    debate_by_id = _debate_by_id(case)
    results = []

    for crux in cruxes:
        cid = crux.get("id", "")
        print(f"\n  -> hypothesis for crux: {cid}")

        supporting, contradicting, gaps = _gather_crux_evidence(crux, store, debate_by_id)
        crux_payload = {
            "question": crux.get("question", ""),
            "stakes": crux.get("stakes", ""),
            "resolution_path": crux.get("resolution_path", ""),
            "claim_ids": crux.get("claim_ids", []),
        }
        cache_key = f"hypothesis::{cid}::{content_hash(crux_payload)}"

        result = cache.get_or_run(
            "agent",
            cache_key,
            lambda: call_llm(
                HYPOTHESIS_AGENT.format(
                    crux_id=cid,
                    crux_question=crux.get("question", ""),
                    stakes=crux.get("stakes", ""),
                    resolution_path=crux.get("resolution_path", ""),
                    supporting_nodes=json.dumps(supporting, ensure_ascii=False, indent=2),
                    contradicting_nodes=json.dumps(contradicting, ensure_ascii=False, indent=2),
                    gaps=json.dumps(gaps, ensure_ascii=False, indent=2),
                ),
                model=MODEL_SMART,
                max_tokens=3500,
                parse_json=True,
                label="hypothesis_agent",
            ),
        )

        if not isinstance(result, dict) or result.get("parse_error"):
            print(f"    x failed")
            continue

        result["crux_id"] = cid
        result = verify_hypothesis(result, case, cache)
        n_overrides = len(result.get("verifier_overrides") or [])
        if n_overrides:
            print(f"    verifier: {n_overrides} grounding override(s)")

        out_path = hypotheses_dir(case) / f"{cid}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    saved: {out_path.name}")
        results.append(result)

    print(f"\n  Hypotheses: {len(results)} crux(es)")

    from episteme.pipeline.verification_report import build_verification_report, save_verification_report
    all_hyps = load_all_hypotheses(case)
    report = build_verification_report(case, all_hyps)
    report_path = save_verification_report(case, report)
    pct = report["study_parameters"]["grounded_pct"]
    print(
        f"  Verification report: {report['study_parameters']['total']} parameters, "
        f"{pct if pct is not None else 'n/a'}% grounded, "
        f"{report['verifier_overrides']['total']} override(s) -> {report_path.name}"
    )

    return results


def load_hypothesis(case: str, crux_id: str) -> dict | None:
    path = hypotheses_dir(case) / f"{crux_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def load_all_hypotheses(case: str) -> list[dict]:
    d = hypotheses_dir(case)
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out
