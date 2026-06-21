"""Expert reasoning orchestrator — uses graph output, not raw text."""

import json

from episteme.config import MODEL_SMART
from episteme.core.cache import Cache, content_hash
from episteme.core.graph import GraphStore
from episteme.core.llm import call_llm
from episteme.reasoning.context import (
    active_contradictions,
    attestors_for_claim,
    classify_claims,
    confidence_ceiling,
    devils_advocate_targets,
    domain_label,
    load_json_artifact,
    methodology_gaps_text,
    methodology_score_avg,
    methodology_scores_for_claim,
    methodology_summary_text,
    _load_audits,
    _methodology_by_source,
)
from episteme.reasoning.paths import (
    devils_advocate_path,
    field_briefing_path,
    open_questions_path,
    presuppositions_path,
)
from episteme.reasoning.prompts import (
    DEVILS_ADVOCATE,
    NARRATIVE_SYNTHESIZER,
    PRESUPPOSITION_MINER,
    QUESTION_GENERATOR,
)

PHASES = ("presuppositions", "devils", "questions", "narrative", "all")


def run_presupposition_miner(case: str, cache: Cache, store: GraphStore) -> list:
    settled, contested = classify_claims(case, store)
    if not settled and not contested:
        print("  No claims to mine — run crystallize + relate first")
        return []

    print(f"  Mining presuppositions ({len(settled)} settled, {len(contested)} contested)")

    presup_payload = {
        "settled": [c["id"] for c in settled],
        "contested": [c["id"] for c in contested],
    }
    result = cache.get_or_run(
        "agent",
        f"reasoning::presupposition_miner::{content_hash(presup_payload)}",
        lambda: call_llm(
            PRESUPPOSITION_MINER.format(
                domain=domain_label(case),
                settled_claims=json.dumps(settled, ensure_ascii=False, indent=2),
                contested_claims=json.dumps(contested, ensure_ascii=False, indent=2),
                methodology_summary=methodology_summary_text(case),
            ),
            model=MODEL_SMART,
            max_tokens=3000,
            parse_json=True,
            label="presupposition_miner",
        ),
    )

    if isinstance(result, dict) and result.get("parse_error"):
        print("  x presupposition miner parse failed")
        return []

    if isinstance(result, dict):
        items = result.get("presuppositions", result.get("items", [result]))
    elif isinstance(result, list):
        items = result
    else:
        items = []

    presuppositions_path(case).write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  Saved {len(items)} presuppositions -> reasoning/presuppositions.json")
    return items


def run_devils_advocate(case: str, cache: Cache, store: GraphStore) -> list[dict]:
    settled, _ = classify_claims(case, store)
    targets = devils_advocate_targets(settled)
    if not targets:
        print("  No settled multi-source claims for devil's advocate")
        return []

    meth_by_source = _methodology_by_source(_load_audits(case))
    results = []

    for claim in targets:
        cid = claim["id"]
        node = store.get_node(cid)
        if not node:
            continue
        print(f"  -> devil's advocate: {cid}")

        supporting = [claim]
        if claim.get("supported_by"):
            supporting.extend(claim["supported_by"])

        result = cache.get_or_run(
            "agent",
            f"reasoning::devils_advocate::{cid}",
            lambda c=claim, n=node, s=supporting: call_llm(
                DEVILS_ADVOCATE.format(
                    claim_content=c.get("content", ""),
                    supporting_nodes=json.dumps(s, ensure_ascii=False, indent=2),
                    attestors=attestors_for_claim(n),
                    methodology_scores=methodology_scores_for_claim(n, meth_by_source),
                ),
                model=MODEL_SMART,
                max_tokens=1500,
                parse_json=True,
                label="devils_advocate",
            ),
        )

        if not isinstance(result, dict) or result.get("parse_error"):
            print(f"    x failed")
            continue

        result["claim_id"] = cid
        out = devils_advocate_path(case, cid)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
        print(f"    saved -> {out.name}")

    print(f"  Devil's advocate: {len(results)} claim(s)")
    return results


def run_question_generator(case: str, cache: Cache, store: GraphStore) -> list:
    settled, contested = classify_claims(case, store)
    contradictions = active_contradictions(case, store)

    questions_payload = {
        "settled": [c["id"] for c in settled],
        "contested": [c["id"] for c in contested],
    }
    result = cache.get_or_run(
        "agent",
        f"reasoning::question_generator::{content_hash(questions_payload)}",
        lambda: call_llm(
            QUESTION_GENERATOR.format(
                domain=domain_label(case),
                settled_claims=json.dumps(settled, ensure_ascii=False, indent=2),
                contested_claims=json.dumps(contested, ensure_ascii=False, indent=2),
                contradictions=json.dumps(contradictions, ensure_ascii=False, indent=2),
                methodology_gaps=methodology_gaps_text(case),
            ),
            model=MODEL_SMART,
            max_tokens=3000,
            parse_json=True,
            label="question_generator",
        ),
    )

    if isinstance(result, dict) and result.get("parse_error"):
        print("  x question generator parse failed")
        return []

    if isinstance(result, dict):
        items = result.get("questions", result.get("items", [result]))
    elif isinstance(result, list):
        items = result
    else:
        items = []

    open_questions_path(case).write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  Saved {len(items)} open questions -> reasoning/open_questions.json")
    return items


def run_narrative_synthesizer(case: str, cache: Cache, store: GraphStore) -> str:
    settled, contested = classify_claims(case, store)
    contradictions = active_contradictions(case, store)
    presups = load_json_artifact(presuppositions_path(case)) or []
    questions = load_json_artifact(open_questions_path(case)) or []

    narrative_payload = {
        "settled": [c["id"] for c in settled],
        "contested": [c["id"] for c in contested],
        "presuppositions": presups,
        "open_questions": questions,
    }
    text = cache.get_or_run(
        "agent",
        f"reasoning::narrative_synthesizer::{content_hash(narrative_payload)}",
        lambda: call_llm(
            NARRATIVE_SYNTHESIZER.format(
                settled_claims=json.dumps(settled, ensure_ascii=False, indent=2),
                contested_claims=json.dumps(contested, ensure_ascii=False, indent=2),
                contradictions=json.dumps(contradictions, ensure_ascii=False, indent=2),
                presuppositions=json.dumps(presups, ensure_ascii=False, indent=2),
                open_questions=json.dumps(questions, ensure_ascii=False, indent=2),
                methodology_score_avg=f"{methodology_score_avg(case):.2f}",
                confidence_ceiling=confidence_ceiling(case),
            ),
            model=MODEL_SMART,
            max_tokens=4000,
            parse_json=False,
            label="narrative_synthesizer",
        ),
    )

    if not isinstance(text, str) or not text.strip():
        print("  x narrative synthesizer failed")
        return ""

    field_briefing_path(case).write_text(text.strip(), encoding="utf-8")
    print(f"  Saved field briefing -> reasoning/field_briefing.md ({len(text)} chars)")
    return text


def run_reasoning(
    case: str,
    cache: Cache,
    store: GraphStore,
    phase: str = "all",
) -> dict:
    """
    Expert reasoning layer. Requires compiled index; relate + methodology recommended.
    phase: presuppositions | devils | questions | narrative | all
    """
    if phase not in PHASES:
        raise ValueError(f"Unknown phase: {phase}")

    out: dict = {}

    if phase in ("presuppositions", "all"):
        print("\n-- PRESUPPOSITION MINER --")
        out["presuppositions"] = run_presupposition_miner(case, cache, store)

    if phase in ("devils", "all"):
        print("\n-- DEVIL'S ADVOCATE --")
        out["devils_advocate"] = run_devils_advocate(case, cache, store)

    if phase in ("questions", "all"):
        print("\n-- QUESTION GENERATOR --")
        out["open_questions"] = run_question_generator(case, cache, store)

    if phase in ("narrative", "all"):
        print("\n-- NARRATIVE SYNTHESIZER --")
        out["field_briefing"] = run_narrative_synthesizer(case, cache, store)

    return out
