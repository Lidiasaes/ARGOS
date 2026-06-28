"""
ONE-SHOT EVALUATION SCRIPT — manual review of detected contradictions.

Extracts all 'contradicts' relations from a case graph and prints them in
human-readable format. Each pair shows:
  - similarity score
  - source A: author + quote verbatim
  - source B: author + quote verbatim
  - haiku's reason for the verdict
  - shared question identified

USAGE:
    python argos_in_progress/evaluate_contradictions.py --case covid_small
    python argos_in_progress/evaluate_contradictions.py --case covid_small --sample 10
    python argos_in_progress/evaluate_contradictions.py --case covid_small --csv eval.csv

Use --sample N to review only N random pairs (good for spot-checking).
Use --csv PATH to export pairs to CSV for systematic labeling
(columns: pair_id, claim_a, claim_b, similarity, shared_question,
haiku_reason, your_verdict, your_notes).

This script is for manual quality evaluation. Delete when no longer needed.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from episteme.config import CASES_DIR  # noqa: E402


def extract_contradicts_pairs(nodes: dict) -> list[dict]:
    """
    Extract unique contradicts pairs (deduplicated bidirectional edges).
    Returns list of dicts with both sides + edge metadata.
    """
    seen_pairs: set[frozenset] = set()
    pairs = []

    for node_id, node in nodes.items():
        for rel in node.get("relations", []):
            if rel.get("type") != "contradicts":
                continue
            target_id = rel.get("target")
            if not target_id or target_id not in nodes:
                continue

            key = frozenset((node_id, target_id))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            target = nodes[target_id]
            quote_a = (node.get("textual_evidence")
                       or node.get("quote_exact") or "").strip()
            quote_b = (target.get("textual_evidence")
                       or target.get("quote_exact") or "").strip()

            atts_a = node.get("attestations") or []
            atts_b = target.get("attestations") or []
            source_a = atts_a[0].get("source_id", "") if atts_a else ""
            source_b = atts_b[0].get("source_id", "") if atts_b else ""

            pairs.append({
                "id_a": node_id,
                "id_b": target_id,
                "content_a": node.get("content", ""),
                "content_b": target.get("content", ""),
                "quote_a": quote_a,
                "quote_b": quote_b,
                "author_a": node.get("source_author", ""),
                "author_b": target.get("source_author", ""),
                "source_a": source_a,
                "source_b": source_b,
                "similarity": rel.get("strength", 0.0),
                "rationale": rel.get("rationale", ""),
                "edge_source": rel.get("source", ""),
                "type_a": node.get("type", ""),
                "type_b": target.get("type", ""),
            })

    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs


def print_pair(idx: int, total: int, pair: dict) -> None:
    """Pretty-print one pair for terminal review."""
    print()
    print("═" * 78)
    print(f" PAIR {idx}/{total}   similarity: {pair['similarity']:.3f}   "
          f"type: {pair['type_a']}   detected by: {pair['edge_source']}")
    print("═" * 78)

    print()
    print("─── A " + "─" * 72)
    print(f" ID:     {pair['id_a']}")
    print(f" AUTHOR: {pair['author_a']}")
    print(f" SOURCE: {pair['source_a']}")
    print()
    print(f" CONTENT:")
    print(f"   {pair['content_a']}")
    print()
    print(f" QUOTE (verbatim):")
    for line in _wrap(pair['quote_a'], 72):
        print(f"   {line}")

    print()
    print("─── B " + "─" * 72)
    print(f" ID:     {pair['id_b']}")
    print(f" AUTHOR: {pair['author_b']}")
    print(f" SOURCE: {pair['source_b']}")
    print()
    print(f" CONTENT:")
    print(f"   {pair['content_b']}")
    print()
    print(f" QUOTE (verbatim):")
    for line in _wrap(pair['quote_b'], 72):
        print(f"   {line}")

    print()
    print("─── VERDICT ANALYSIS " + "─" * 56)
    print(f" Haiku rationale:")
    for line in _wrap(pair['rationale'], 72):
        print(f"   {line}")

    # Quick cross-paper check
    same_source = pair['source_a'] == pair['source_b'] and pair['source_a'] != ""
    print()
    print(f" Cross-paper check: "
          f"{'❌ SAME SOURCE (bug!)' if same_source else '✓ different papers'}")

    print()


def _wrap(text: str, width: int) -> list[str]:
    """Naive word-wrap for terminal."""
    if not text:
        return ["(empty)"]
    words = text.split()
    lines = []
    current = ""
    for w in words:
        if len(current) + len(w) + 1 > width:
            if current:
                lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()
    if current:
        lines.append(current)
    return lines


def export_csv(pairs: list[dict], path: Path) -> None:
    """Export pairs to CSV with empty columns for human labeling."""
    fieldnames = [
        "pair_idx",
        "id_a",
        "id_b",
        "similarity",
        "author_a",
        "author_b",
        "content_a",
        "content_b",
        "quote_a",
        "quote_b",
        "haiku_rationale",
        "edge_source",
        # Human evaluation columns (empty for you to fill)
        "human_verdict",      # real_contradiction | weak | false_positive
        "human_notes",
        "addresses_same_question",
        "polarity_truly_opposite",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, p in enumerate(pairs, start=1):
            writer.writerow({
                "pair_idx": i,
                "id_a": p["id_a"],
                "id_b": p["id_b"],
                "similarity": round(p["similarity"], 3),
                "author_a": p["author_a"],
                "author_b": p["author_b"],
                "content_a": p["content_a"],
                "content_b": p["content_b"],
                "quote_a": p["quote_a"],
                "quote_b": p["quote_b"],
                "haiku_rationale": p["rationale"],
                "edge_source": p["edge_source"],
                "human_verdict": "",
                "human_notes": "",
                "addresses_same_question": "",
                "polarity_truly_opposite": "",
            })
    print(f"\n  Exported {len(pairs)} pairs to {path}")


def main():
    parser = argparse.ArgumentParser(description="Manually review detected contradictions")
    parser.add_argument("--case", required=True, help="Case name (e.g., covid_small)")
    parser.add_argument("--sample", type=int, default=0,
                        help="Show only N random pairs (0 = show all)")
    parser.add_argument("--csv", help="Export pairs to CSV at this path")
    parser.add_argument("--top", type=int, default=0,
                        help="Show only top N by similarity")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for sampling")
    args = parser.parse_args()

    graph_path = CASES_DIR / args.case / "graph.json"
    if not graph_path.exists():
        print(f"Graph not found: {graph_path}")
        sys.exit(1)

    nodes = json.loads(graph_path.read_text(encoding="utf-8"))
    pairs = extract_contradicts_pairs(nodes)

    print(f"\n  Case: {args.case}")
    print(f"  Total nodes: {len(nodes)}")
    print(f"  Total contradicts pairs: {len(pairs)}")
    if pairs:
        sims = [p["similarity"] for p in pairs]
        print(f"  Similarity range: {min(sims):.3f} – {max(sims):.3f}")

    if args.csv:
        export_csv(pairs, Path(args.csv))
        return

    selection = pairs
    if args.top > 0:
        selection = pairs[: args.top]
        print(f"  Showing top {args.top} by similarity")
    elif args.sample > 0:
        random.seed(args.seed)
        if args.sample < len(pairs):
            selection = random.sample(pairs, args.sample)
        print(f"  Showing random sample of {len(selection)} pairs (seed {args.seed})")

    for i, pair in enumerate(selection, start=1):
        print_pair(i, len(selection), pair)

    print()
    print("═" * 78)
    print(f"  Reviewed {len(selection)} of {len(pairs)} contradiction pairs")
    print("═" * 78)
    print()
    print("  Recommendation for evaluation:")
    print("  - Use --csv eval.csv to export and fill in human_verdict column")
    print("  - Aim for at least 20-30 labeled pairs for meaningful stats")
    print("  - Categories: real_contradiction | weak | false_positive")


if __name__ == "__main__":
    main()
