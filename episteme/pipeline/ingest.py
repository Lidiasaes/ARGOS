"""Ingestion step — fetch, chunk, extract v4 with anti-generic filters."""

import json

from episteme.config import MODEL_SMART, EXTRACTOR_VERSION
from episteme.core.cache import Cache
from episteme.core.graph import GraphStore, make_node, make_attestation
from episteme.core.llm import call_llm
from episteme.core.embeddings import embed, cosine_sim
from episteme.filters import passes_quote_gate, assess_specificity
from episteme.filters.polarity import check_polarity, polarity_risk_level
from episteme.profiles import ensure_case_profile, ensure_source_thesis
from episteme.prompts import EXTRACTOR_V4
from episteme.pipeline.sources import (
    load_sources,
    source_id,
    get_content,
    evaluate_source,
    chunk_text,
    summarize_document,
)
from episteme.pipeline.bibliography import register_bibliography
from episteme.pipeline.source_role import resolve_all_roles, resolve_role
from episteme.filters.quote_repair import repair_quote_in_text
from episteme.pipeline.youtube_transcript import prepare_youtube_transcripts
from episteme.core.node_schema import normalize_extracted_node


def _save_source_roles(case: str, roles: dict[str, dict]) -> None:
    from episteme.config import CASES_DIR
    path = CASES_DIR / case / "compiled" / "source_roles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")


def run_ingestion(
    case: str,
    cache: Cache,
    store: GraphStore,
    demo_mode: bool,
    max_chunks: int | None = None,
):
    sources = load_sources(case)
    if demo_mode:
        sources = sources[:3]
        print(f"  [demo mode] Using {len(sources)} sources only")

    print("\n  -- YouTube transcripts --")
    sources = prepare_youtube_transcripts(case, sources)

    # Pre-fetch raw for case profile sample
    raw_by_id: dict[str, str] = {}
    for source in sources:
        sid = source_id(source)
        if sid:
            raw_by_id[sid] = cache.get_or_run("raw", sid, lambda s=source: get_content(s))

    case_profile = ensure_case_profile(case, cache, sources, raw_by_id)
    roles_by_id = resolve_all_roles(sources, cache, raw_by_id, source_id)
    _save_source_roles(case, roles_by_id)
    chunks_processed = 0
    seen_chunk_embs = []
    stats = {"added": 0, "dedup": 0, "attestations": 0, "flagged_ungrounded_quote": 0, "blocked_generic": 0, "blocked_illustrative": 0, "normalized_type": 0,
             "polarity_high": 0, "polarity_conflict": 0, "polarity_low": 0,}

    for source in sources:
        sid = source_id(source)
        label = source.get("local_path") or source.get("url") or sid
        print(f"\n  -> {label}")

        if not (sid or "").strip():
            print(f"    [SKIP] Source has no local_path or url — cannot identify. "
                  f"Source dict keys: {list(source.keys())}")
            continue

        register_bibliography(case, source)

        raw = raw_by_id.get(sid) or cache.get_or_run("raw", sid, lambda s=source: get_content(s))
        if isinstance(raw, str) and raw.startswith("FETCH_ERROR"):
            print(f"    x {raw}")
            continue

        trust = cache.get_or_run("trust", sid, lambda s=source, r=raw: evaluate_source(s, r))
        if trust.get("trust_level") == "reject":
            print(f"    x Rejected: {trust.get('reject_reason', trust.get('reason', ''))}")
            continue

        role_info = roles_by_id.get(sid, {})
        source_role = role_info.get("role", "unknown")
        print(f"    role: {source_role} ({role_info.get('method', '?')})")

        source_thesis = ensure_source_thesis(case, cache, source, sid, raw, case_profile, trust)

        doc_context = cache.get_or_run("doc_summary", sid, lambda r=raw: summarize_document(r))
        print(f"    doc: {doc_context[:100]}...")

        from episteme.config import CHUNK_VERSION
        chunks = cache.get_or_run(
            "chunks",
            f"{CHUNK_VERSION}::{sid}",
            lambda r=raw: chunk_text(r),
        )
        print(f"    {len(chunks)} chunks")

        if max_chunks is not None and chunks_processed >= max_chunks:
            print(f"    --max-chunks {max_chunks} reached, stopping ingestion")
            break

        new_nodes = 0
        for i, chunk in enumerate(chunks):
            if max_chunks is not None and chunks_processed >= max_chunks:
                break
            chunks_processed += 1

            if _chunk_is_duplicate(chunk, seen_chunk_embs):
                print(f"      chunk {i} duplicate, skipped")
                continue

            chunk_id = f"{sid}::chunk_{i}"
            cache_key = f"nodes_{EXTRACTOR_VERSION}::{chunk_id}"

            nodes_data = cache.get_or_run(
                "nodes",
                cache_key,
                lambda c=chunk, s=source, t=trust, dc=doc_context, st=source_thesis: _extract_nodes_v4(
                    case, c, s, sid, t, dc, case_profile, st
                ),
            )

            if nodes_data.get("refused"):
                print(f"      chunk {i} refused, skipped")
                continue
            if nodes_data.get("parse_error"):
                print(f"      chunk {i} extraction failed")
                continue

            for node_data in nodes_data.get("nodes", []):
                node_data, norm_warn = normalize_extracted_node(node_data)
                if norm_warn:
                    stats["normalized_type"] += 1
                    print(f"      [type fix] {norm_warn}: {node_data.get('content', '')[:50]}")

                if node_data.get("argument_level") == "illustrative":
                    stats["blocked_illustrative"] += 1
                    continue

                ok, reason = passes_quote_gate(node_data, chunk)
                quote_grounded = ok
                polarity_result = check_polarity(node_data, cache, chunk_id)
                p_risk = polarity_risk_level(polarity_result, node_data)
                if p_risk == "high":
                    stats["polarity_high"] += 1
                    print(f"      [polarity HIGH] {polarity_result.get('reason', '')}: "
                          f"{node_data.get('content', '')[:70]}")
                elif p_risk == "conflict":
                    stats["polarity_conflict"] += 1
                    print(f"      [polarity CONFLICT] extractor vs haiku disagree: "
                          f"{node_data.get('content', '')[:70]}")
                elif p_risk == "low":
                    stats["polarity_low"] += 1
                if ok:
                    quote_key = "textual_evidence" if node_data.get("textual_evidence") else "supporting_quote"
                    if node_data.get(quote_key):
                        repaired = repair_quote_in_text(node_data[quote_key], chunk)
                        node_data[quote_key] = repaired
                        if quote_key == "textual_evidence":
                            node_data["textual_evidence"] = repaired
                    node_data["has_verified_quote"] = True
                else:
                    stats["flagged_ungrounded_quote"] += 1
                    node_data["has_verified_quote"] = False
                    print(f"      [quote gate] {reason} — kept, flagged ungrounded: {node_data.get('content', '')[:60]}")

                spec_score, reject, gen_reason = assess_specificity(node_data, case_profile)
                if reject:
                    stats["blocked_generic"] += 1
                    print(f"      [generic] {gen_reason}: {node_data.get('content', '')[:60]}")
                    continue

                similar_id = store.node_exists_similar(node_data["content"])
                quote = node_data.get("textual_evidence") or node_data.get("supporting_quote")
                att = make_attestation(
                    sid,
                    author=source.get("author", ""),
                    date=source.get("date", ""),
                    source_url=source.get("url") or source.get("local_path", ""),
                    quote=quote,
                    source_type=trust.get("source_type", "unknown"),
                )
                if similar_id:
                    if store.append_attestation(similar_id, att):
                        stats["attestations"] += 1
                        print(f"      + attestation -> {similar_id} ({sid})")
                    else:
                        stats["dedup"] += 1
                else:
                    node = make_node(
                        type=node_data.get("type", "claim"),
                        content=node_data["content"],
                        source_url=source.get("url") or source.get("local_path", ""),
                        source_author=source.get("author", ""),
                        source_date=source.get("date", ""),
                        source_type=trust.get("source_type", "unknown"),
                        claim_type=node_data.get("claim_type", "unknown"),
                        argument_level=node_data.get("argument_level", "direct"),
                        abstraction_level=node_data.get("abstraction_level", "empirical"),
                        evidential_weight=node_data.get("evidential_weight") or None,
                        # Extractor-proposed relations reference IDs that don't exist yet
                        # (extractor sees a chunk in isolation, not the graph). These produce
                        # self-loops and orphan targets. Discard them — real relations are built
                        # by structure.py (presupposes) and reconcile.py (contradicts/supports)
                        # which see the full graph and can resolve real node IDs.
                        relations=[],
                        quote_exact=node_data.get("textual_evidence") or node_data.get("supporting_quote"),
                        textual_evidence=node_data.get("textual_evidence"),
                        key_question=node_data.get("key_question"),
                        subfield=node_data.get("subfield", ""),
                        specificity_score=spec_score,
                        source_id=sid,
                        source_role=source_role,
                        counterargument=node_data.get("counterargument"),
                        is_rhetorical_move=bool(node_data.get("is_rhetorical_move")),
                        attributed_to=node_data.get("attributed_to", "source_author"),
                        polarity_risk=p_risk,
                        genericity_flag=False,
                        independence_score=None,
                        case=case,
                        agent_generated=False,
                        quote_grounded=quote_grounded,
                        needs_review=spec_score < 0.3 or not quote_grounded or p_risk in ("high", "conflict"),
                    )
                    store.add_node(node)
                    new_nodes += 1
                    stats["added"] += 1
                    flag_marker = " [ungrounded]" if not quote_grounded else ""
                    print(f"      + [{node.type}]{flag_marker} sf={node.subfield[:20] if node.subfield else '?'} | {node.content[:70]}")

        print(f"    {new_nodes} new nodes from this source")

    print(f"  Ingest stats: {stats}")

    print("\n  -- GRAPH INVARIANTS (post-ingest) --")
    invariants = store.validate_invariants()
    for key, value in invariants.items():
        if "total" in key or value > 0:
            marker = "  " if "total" in key else "  ⚠ " if value > 0 else "  "
            print(f"{marker}{key}: {value}")


def _call_extractor_v4(
    case: str,
    chunk: str,
    source: dict,
    source_id_str: str,
    trust: dict,
    doc_context: str,
    case_profile: dict,
    source_thesis: dict,
) -> dict:
    return call_llm(
        EXTRACTOR_V4.format(
            case=case,
            central_questions=json.dumps(case_profile.get("central_questions", []), ensure_ascii=False),
            subfields=json.dumps(case_profile.get("subfields", []), ensure_ascii=False),
            key_entities=json.dumps(case_profile.get("key_entities", [])[:30], ensure_ascii=False),
            source_thesis=json.dumps(source_thesis, ensure_ascii=False),
            chunk=chunk,
            source_id=source_id_str,
            source_url=source.get("url") or source.get("local_path", ""),
            author=source.get("author", ""),
            date=source.get("date", ""),
            source_type=trust.get("source_type", "unknown"),
            document_context=doc_context or "No document context available.",
        ),
        model=MODEL_SMART,
        max_tokens=3000,
        parse_json=True,
        retries=3,
        label="node_extraction_v4",
    )


def _split_chunk_half(chunk: str) -> list[str]:
    paragraphs = [p.strip() for p in chunk.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        mid = len(chunk) // 2
        return [chunk[:mid].strip(), chunk[mid:].strip()]
    mid = len(paragraphs) // 2
    return ["\n\n".join(paragraphs[:mid]), "\n\n".join(paragraphs[mid:])]


def _extract_nodes_v4(
    case: str,
    chunk: str,
    source: dict,
    source_id_str: str,
    trust: dict,
    doc_context: str,
    case_profile: dict,
    source_thesis: dict,
) -> dict:
    result = _call_extractor_v4(case, chunk, source, source_id_str, trust, doc_context, case_profile, source_thesis)
    if not result.get("refused"):
        return result

    print("      API refused full chunk — splitting...")
    all_nodes = []
    for i, sub in enumerate(_split_chunk_half(chunk)):
        if not sub.strip():
            continue
        sub_result = _call_extractor_v4(case, sub, source, source_id_str, trust, doc_context, case_profile, source_thesis)
        if sub_result.get("refused") or sub_result.get("parse_error"):
            continue
        all_nodes.extend(sub_result.get("nodes", []))
    return {"nodes": all_nodes} if all_nodes else {"parse_error": True, "refused": True, "nodes": []}


def _chunk_is_duplicate(chunk: str, seen_embs: list, threshold: float = 0.85) -> bool:
    new_emb = embed(chunk)
    if new_emb is None:
        return False
    for emb in seen_embs:
        if cosine_sim(new_emb, emb) >= threshold:
            return True
    seen_embs.append(new_emb)
    return False
