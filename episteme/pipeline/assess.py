"""Assessment step — gaps + report from compiled index."""

from episteme.core.cache import Cache, content_hash
from episteme.core.graph import GraphStore, make_node
from episteme.core.llm import call_llm
from episteme.config import MODEL_SMART
from episteme.prompts import GAP_FINDER_COMPILED
from episteme.compile.crystallize import (
    load_compiled,
    run_crystallize,
    compiled_summary_for_llm,
    save_compiled,
    _summarize_gaps,
)
from episteme.report.generator import generate_report


def run_assessment(case: str, cache: Cache, store: GraphStore):
    index = load_compiled(case)
    if index is None:
        print("  No compiled index — running crystallize first...")
        index = run_crystallize(case, cache, store)

    stats = store.stats()
    print(f"  Graph: {stats}")
    print(
        f"  Compiled: {len(index.get('themes', []))} themes, "
        f"{len(index.get('chains', []))} chains, {len(index.get('cruxes', []))} cruxes"
    )

    compiled_payload = compiled_summary_for_llm(index)
    gap_id = f"gaps_compiled::{case}::{content_hash(compiled_payload)}"
    gaps_result = cache.get_or_run(
        "agent",
        gap_id,
        lambda: call_llm(
            GAP_FINDER_COMPILED.format(
                case=case,
                compiled_index=compiled_payload,
            ),
            model=MODEL_SMART,
            max_tokens=2000,
            parse_json=True,
            label="gap_finder",
        ),
    )

    new_gaps = 0
    for gap in gaps_result.get("gaps", []):
        if store.node_exists_similar(gap["content"]):
            continue
        gnode = make_node(
            type="gap",
            content=gap["content"],
            source_url="agent:assessor",
            agent_generated=True,
            needs_review=gap.get("impact") == "CRITICAL",
            case=case,
        )
        store.add_node(gnode)
        new_gaps += 1
    print(f"  Added {new_gaps} new gap nodes")

    flagged = 0
    for node in store.get_all_nodes():
        ew = node.get("evidential_weight")
        if ew is not None and ew < 0.3:
            store.update_node(node["id"], {"needs_review": True})
            flagged += 1
    print(f"  Flagged {flagged} nodes with evidential_weight < 0.3")

    index["gaps"] = _summarize_gaps(store)
    save_compiled(case, index)

    generate_report(case, store, cache)
