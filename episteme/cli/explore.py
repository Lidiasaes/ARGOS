#!/usr/bin/env python3
"""Navigate compiled Epistemic Index and expand node IDs."""

import argparse
import json
import sys

from episteme.compile import load_compiled
from episteme.compile.debate_state import load_debate_state
from episteme.core.graph import GraphStore, ensure_attestations, unique_attestation_source_count
from episteme.pipeline.hypothesis import load_hypothesis


def _print_node(node: dict, indent: str = ""):
    print(f"{indent}[{node['type']}] {node['id']}")
    print(f"{indent}  {node['content']}")
    if node.get("source_author"):
        print(f"{indent}  author: {node['source_author']}")
    atts = ensure_attestations(node)
    n_sources = unique_attestation_source_count(atts)
    if n_sources > 1:
        print(f"{indent}  attestations: {n_sources} sources")
        for grp_key in {a.get("source_id") or a.get("author", "?") for a in atts}:
            print(f"{indent}    - {grp_key}")
    elif atts and atts[0].get("quote"):
        print(f"{indent}  quote: \"{atts[0]['quote'][:100]}...\"")
    if node.get("subfield"):
        print(f"{indent}  subfield: {node['subfield']}")
    if node.get("evidential_weight") is not None:
        print(f"{indent}  ew={node['evidential_weight']:.2f}")
    if node.get("needs_review"):
        print(f"{indent}  ** needs_review **")


def cmd_summary(index: dict):
    stats = index.get("stats", {})
    print(f"Case: {index.get('case')}")
    print(f"Compiled: {index.get('compiled_at', '?')}")
    print(f"Raw nodes: {stats.get('total', '?')}")
    print(f"  Themes: {len(index.get('themes', []))}")
    print(f"  Ranked claims: {len(index.get('ranked_claims', []))}")
    print(f"  Chains: {len(index.get('chains', []))}")
    print(f"  Cruxes: {len(index.get('cruxes', []))}")
    print(f"  Gaps: {len(index.get('gaps', []))}")


def cmd_list(index: dict, what: str):
    if what == "themes":
        for t in index.get("themes", []):
            flag = " [REVIEW]" if t.get("needs_review") else ""
            print(f"  {t['id']}{flag} ({t['member_count']} members) — {t['label'][:80]}")
    elif what == "claims":
        for c in index.get("ranked_claims", []):
            print(f"  {c['id']} (centrality={c['centrality']}) — {c['content'][:80]}")
    elif what == "chains":
        for ch in index.get("chains", []):
            print(f"  {ch.get('id', '?')} — {ch.get('conclusion', '')[:80]}")
    elif what == "cruxes":
        for cr in index.get("cruxes", []):
            print(f"  {cr.get('id', '?')} — {cr.get('question', '')[:80]}")
    elif what == "gaps":
        for g in index.get("gaps", []):
            flag = " [CRITICAL]" if g.get("needs_review") else ""
            print(f"  {g['id']}{flag} — {g['content'][:80]}")


def cmd_theme(index: dict, theme_id: str, store: GraphStore):
    theme = next((t for t in index.get("themes", []) if t["id"] == theme_id), None)
    if not theme:
        print(f"Theme {theme_id} not found")
        return
    print(f"Theme: {theme['label']}")
    print(f"Members ({theme['member_count']}):")
    for mid in theme.get("member_ids", [])[:10]:
        node = store.get_node(mid)
        if node:
            _print_node(node, "  ")
    if theme.get("member_count", 0) > 10:
        print(f"  ... and {theme['member_count'] - 10} more")
    print(f"Linked claims: {theme.get('claim_ids', [])}")


def cmd_crux(index: dict, crux_id: str, store: GraphStore):
    crux = next((c for c in index.get("cruxes", []) if c.get("id") == crux_id), None)
    if not crux:
        print(f"Crux {crux_id} not found")
        return
    print(f"Question: {crux.get('question')}")
    print(f"Stakes: {crux.get('stakes')}")
    print(f"Resolution: {crux.get('resolution_path')}")
    for cid in crux.get("claim_ids", []):
        node = store.get_node(cid)
        if node:
            _print_node(node, "  ")


def cmd_debate(case: str):
    state = load_debate_state(case)
    if not state:
        print("No debate_state.json — run: python main.py --case {case} --step debate")
        return
    stats = state.get("stats", {})
    print(f"Debate nodes: {stats.get('total_with_structure', 0)}")
    print(f"  multi-source: {stats.get('multi_source', 0)}")
    print(f"  contradictions: {stats.get('with_contradictions', 0)}")
    print(f"  dependencies: {stats.get('with_dependencies', 0)}")
    for n in state.get("nodes", [])[:15]:
        flags = []
        n_sources = unique_attestation_source_count(n.get("attestations", []))
        if n_sources > 1:
            flags.append(f"{n_sources} sources")
        elif len(n.get("attestations", [])) > 1:
            flags.append(f"{len(n['attestations'])} quotes, 1 source")
        if n.get("contradicted_by"):
            flags.append(f"{len(n['contradicted_by'])} contra")
        if n.get("requires_true"):
            flags.append(f"{len(n['requires_true'])} requires")
        extra = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {n['claim_id']}{extra} — {n.get('canonical', '')[:70]}")


def cmd_hypothesis(case: str, crux_id: str):
    hyp = load_hypothesis(case, crux_id)
    if not hyp:
        print(f"No hypothesis for {crux_id} — run: scripts/run_hypothesis.py --case {case} --crux {crux_id}")
        return
    print(f"Crux: {hyp.get('crux_id')}")
    print(f"Hypothesis: {hyp.get('working_hypothesis')}")
    print(f"Falsification: {hyp.get('falsification_condition')}")
    study = hyp.get("proposed_study", {})
    print(f"Design: {study.get('design')}")
    print(f"N: {study.get('n_needed')}")


def cmd_expand(store: GraphStore, node_id: str, depth: int = 1):
    node = store.get_node(node_id)
    if not node:
        matches = [n for n in store.get_all_nodes() if n["id"].startswith(node_id)]
        if len(matches) == 1:
            node = matches[0]
        elif matches:
            print(f"Ambiguous prefix '{node_id}' — matches:")
            for m in matches[:5]:
                print(f"  {m['id']}: {m['content'][:60]}")
            return
        else:
            print(f"Node {node_id} not found")
            return

    _print_node(node)
    if depth > 0:
        for rel in node.get("relations", []):
            target = store.get_node(rel["target"])
            if target:
                print(f"\n  --{rel['type']}-->")
                cmd_expand(store, target["id"], depth - 1)


def main():
    parser = argparse.ArgumentParser(description="Explore compiled epistemic index")
    parser.add_argument("--case", required=True)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--list", choices=["themes", "claims", "chains", "cruxes", "gaps"])
    parser.add_argument("--theme", metavar="ID")
    parser.add_argument("--crux", metavar="ID")
    parser.add_argument("--debate", action="store_true")
    parser.add_argument("--hypothesis", metavar="CRUX_ID")
    parser.add_argument("--expand", metavar="NODE_ID")
    parser.add_argument("--depth", type=int, default=1)
    args = parser.parse_args()

    index = load_compiled(args.case)
    if index is None:
        print(f"No compiled index for case '{args.case}'. Run: python main.py --case {args.case} --step crystallize")
        sys.exit(1)

    store = GraphStore(args.case)

    if args.summary:
        cmd_summary(index)
    elif args.list:
        cmd_list(index, args.list)
    elif args.theme:
        cmd_theme(index, args.theme, store)
    elif args.crux:
        cmd_crux(index, args.crux, store)
    elif args.debate:
        cmd_debate(args.case)
    elif args.hypothesis:
        cmd_hypothesis(args.case, args.hypothesis)
    elif args.expand:
        cmd_expand(store, args.expand, args.depth)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
