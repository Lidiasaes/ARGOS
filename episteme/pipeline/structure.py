"""Structure step — batched philosopher + disambiguation."""

import json

from episteme.config import MODEL_SMART, PHILOSOPHER_BATCH_SIZE, MAX_PRESUPPOSITIONS_PER_BATCH
from episteme.core.cache import Cache
from episteme.core.graph import GraphStore, make_node
from episteme.core.llm import call_llm
from episteme.prompts import PHILOSOPHER_BATCH, PHILOSOPHER_DISAMBIGUATION
from episteme.compile.crystallize import cluster_claims
from episteme.pipeline.sources import get_subdomain


def run_structure(case: str, cache: Cache, store: GraphStore):
    all_claims = store.get_nodes_by_type("claim")
    clusters = cluster_claims(store)
    print(f"  {len(all_claims)} claims -> {len(clusters)} semantic clusters")

    batches = [clusters[i : i + PHILOSOPHER_BATCH_SIZE] for i in range(0, len(clusters), PHILOSOPHER_BATCH_SIZE)]
    print(f"  Running philosopher in {len(batches)} batches...")

    for batch_idx, batch in enumerate(batches):
        batch_id = f"presup_batch::{batch_idx}"
        claims_payload = [
            {
                "id": c["representative"]["id"],
                "content": c["representative"]["content"],
                "source_type": c["representative"]["source_type"],
                "claim_type": c["representative"]["claim_type"],
            }
            for c in batch
        ]

        result = cache.get_or_run(
            "agent",
            batch_id,
            lambda cp=claims_payload: call_llm(
                PHILOSOPHER_BATCH.format(
                    subdomain=get_subdomain(case),
                    claims=json.dumps(cp, ensure_ascii=False),
                    max_presup=MAX_PRESUPPOSITIONS_PER_BATCH,
                ),
                model=MODEL_SMART,
                max_tokens=2000,
                parse_json=True,
                label="philosopher_batch",
            ),
        )

        cluster_by_rep = {c["representative_id"]: c for c in batch}

        for item in result.get("results", []):
            claim_id = item.get("claim_id")
            cluster = cluster_by_rep.get(claim_id)
            if not cluster:
                continue

            presups = sorted(
                [
                    p for p in item.get("presuppositions", [])
                    if p.get("impact_if_false") in ("FATAL", "MAJOR")
                ],
                key=lambda p: 0 if p.get("impact_if_false") == "FATAL" else 1,
            )[:MAX_PRESUPPOSITIONS_PER_BATCH]
            for p in presups:

                similar_id = store.node_exists_similar(p["content"])
                if similar_id:
                    pid = similar_id
                else:
                    pnode = make_node(
                        type="presupposition",
                        content=p["content"],
                        source_url=f"agent:philosopher_batch::{batch_idx}",
                        confidence=0.8,
                        agent_generated=True,
                        needs_review=p.get("status") == "PREMISE_OF_THIS_WORK",
                        case=case,
                    )
                    pid = store.add_node(pnode)

                rejected = 0
                for member_id in cluster["member_ids"]:
                    if not store.add_relation(member_id, pid, "presupposes", 0.9):
                        rejected += 1
                if rejected:
                    print(f"    [structure] {rejected} presupposes relations rejected by validator")

    print("  Presuppositions extracted (batched + deduped).")

    contradictions = _find_contradicting_pairs(store)
    print(f"  Found {len(contradictions)} contradicting pairs.")

    for a, b in contradictions[:10]:
        ids = tuple(sorted([a["id"], b["id"]]))
        dis_id = f"disambig::{ids[0]}::{ids[1]}"
        result = cache.get_or_run(
            "agent",
            dis_id,
            lambda a=a, b=b: call_llm(
                PHILOSOPHER_DISAMBIGUATION.format(
                    claim_a=a["content"],
                    source_a=a["source_url"],
                    claim_b=b["content"],
                    source_b=b["source_url"],
                ),
                model=MODEL_SMART,
                max_tokens=1000,
                parse_json=True,
                label="philosopher",
            ),
        )
        if result.get("disagreement_type") in ("APPARENT", "SEMANTIC", "LEVEL_MISMATCH"):
            narrative = result.get("narrative", "")
            if narrative and not store.node_exists_similar(narrative):
                dnode = make_node(
                    type="question_disambiguation",
                    content=narrative,
                    source_url="agent:philosopher",
                    confidence=0.7,
                    agent_generated=True,
                    case=case,
                )
                store.add_node(dnode)

    print("\n  -- GRAPH INVARIANTS (post-structure) --")
    invariants = store.validate_invariants()
    for key, value in invariants.items():
        if "total" in key or value > 0:
            marker = "  " if "total" in key else "  ⚠ " if value > 0 else "  "
            print(f"{marker}{key}: {value}")


def _find_contradicting_pairs(store: GraphStore) -> list:
    pairs = []
    seen: set[frozenset] = set()
    for node in store.get_nodes_by_type("claim"):
        for rel in node.get("relations", []):
            if rel["type"] != "contradicts":
                continue
            target = store.get_node(rel["target"])
            if not target:
                continue
            key = frozenset((node["id"], target["id"]))
            if key in seen:
                continue
            seen.add(key)
            # Canonical order: lower id first, so the cache key and the
            # claim_a/claim_b content fed to the LLM are deterministic
            # regardless of which direction the "contradicts" edge
            # happened to be recorded.
            ordered = (node, target) if node["id"] < target["id"] else (target, node)
            pairs.append(ordered)
    return pairs
