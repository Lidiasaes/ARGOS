"""Pipeline orchestrator."""

from episteme.core.cache import Cache
from episteme.core.graph import GraphStore
from episteme.core.llm import print_budget_report, reset_budget
from episteme.pipeline.ingest import run_ingestion
from episteme.pipeline.structure import run_structure
from episteme.pipeline.assess import run_assessment
from episteme.compile.crystallize import run_crystallize


def run_pipeline(
    case: str,
    step: str,
    reset_cache: bool,
    demo_mode: bool,
    max_chunks: int | None = None,
):
    reset_budget()
    cache = Cache(case=case, reset=reset_cache)
    store = GraphStore(case=case)

    print(f"  Cache stats before: {cache.stats()}")
    print(f"  Graph stats before: {store.stats()}")

    if step in ("ingest", "all"):
        print("\n-- INGESTION --")
        run_ingestion(case, cache, store, demo_mode, max_chunks)

    if step in ("reconcile", "all"):
        print("\n-- RECONCILE (cross-paper) --")
        from episteme.pipeline.reconcile import run_reconcile
        run_reconcile(case, cache, store)
        # Auto-chain: the proposition collapse is deterministic, $0 and read-only,
        # and consumes exactly the conflict edges reconcile just produced — so we
        # always run it right after reconcile to keep the proposition layer fresh.
        print("\n-- PROPOSITIONS (auto: deterministic edge collapse) --")
        from episteme.compile.propositions import run_propositions
        run_propositions(case, store)

    # Standalone re-run (e.g. after tweaking PROPOSITION_CLUSTER_THRESHOLD)
    # without paying for a full reconcile.
    if step == "propositions":
        print("\n-- PROPOSITIONS (deterministic edge collapse) --")
        from episteme.compile.propositions import run_propositions
        run_propositions(case, store)

    if step == "relate":
        print("\n-- RELATE (cross-source) --")
        from episteme.pipeline.relate import run_relate
        run_relate(case, cache, store)

    if step == "debate":
        print("\n-- DEBATE STATE (deterministic) --")
        from episteme.compile.debate_state import build_debate_state
        debate = build_debate_state(case, store)
        ds = debate.get("stats", {})
        print(
            f"  {ds.get('total_with_structure', 0)} nodes with structure "
            f"({ds.get('multi_source', 0)} multi-source)"
        )
        print(f"  Saved: cases/{case}/compiled/debate_state.json")

    if step == "hypothesis":
        print("\n-- HYPOTHESIS (per crux) --")
        from episteme.pipeline.hypothesis import run_hypothesis
        run_hypothesis(case, cache, store)

    if step == "reasoning":
        print("\n-- REASONING (expert layer) --")
        from episteme.reasoning.runner import run_reasoning
        run_reasoning(case, cache, store, phase="all")

    if step in ("structure", "all"):
        print("\n-- STRUCTURE --")
        run_structure(case, cache, store)

    if step in ("crystallize", "all"):
        print("\n-- CRYSTALLIZE --")
        run_crystallize(case, cache, store)
        # Auto-chain: aligning the freshly-built cruxes (index.json) against the
        # deterministic propositions is $0, no-LLM and read-only — run it now so
        # convergence / orphan signals are always available after crystallize.
        print("\n-- CRUX ALIGNMENT (auto: cruxes vs propositions) --")
        from episteme.compile.crux_alignment import run_crux_alignment
        run_crux_alignment(case)

    # Standalone re-run against existing compiled artifacts.
    if step == "crux_alignment":
        print("\n-- CRUX ALIGNMENT (cruxes vs propositions) --")
        from episteme.compile.crux_alignment import run_crux_alignment
        run_crux_alignment(case)

    if step == "importance":
        print("\n-- SOURCE IMPORTANCE (post-graph) --")
        from episteme.compile.source_importance import run_source_importance
        from episteme.config import CASES_DIR
        import json
        roles_path = CASES_DIR / case / "compiled" / "source_roles.json"
        roles = json.loads(roles_path.read_text(encoding="utf-8")) if roles_path.exists() else None
        run_source_importance(case, store, roles=roles)

    if step in ("assess", "all"):
        print("\n-- ASSESSMENT --")
        run_assessment(case, cache, store)

    if step in ("methodology", "all"):
        print("\n-- METHODOLOGY --")
        from episteme.methodology.runner import run_methodology
        run_methodology(case, cache)

    stats = store.stats()
    print(f"\n  Graph stats after: {stats}")
    print(f"  Cache stats after: {cache.stats()}")
    print(f"\n  Graph saved to: {store.path.resolve()}")
    print(f"  Cache at:       {cache.base.resolve()}")
    print_budget_report()
