"""
Align LLM-generated cruxes against deterministic propositions.

Two INDEPENDENT methods each produce a view of "what is disputed":

  - cruxes      (crystallize): centrality ranking + LLM extraction, stored in
                 compiled/index.json -> cruxes[], each with claim_ids.
  - propositions (this package): shared_question embedding clustering, stored in
                 compiled/propositions.json, each with claim_ids.

This deterministic, no-LLM, read-only step measures whether the two converge, by
Jaccard overlap of their claim_id sets. The signal cuts both ways:

  - ORPHAN CRUXES: a crux the proposition layer barely sees (low best Jaccard)
    may be a weak or hallucinated dispute.
  - ORPHAN PROPOSITIONS: a high-volume, multi-source contested proposition that
    no crux maps to is a real dispute the crux-builder missed.

DISPUTE-MASS COVERAGE (lax vs strict) — why both numbers matter:

  We report what fraction of the contested-dispute "mass" (summed edge_count over
  contested, multi-source propositions) the cruxes capture, under two rules:

    - LAX  (dispute_mass_coverage): a proposition is covered if it is any crux's
      best-match, *regardless of Jaccard*. This can nominally "cover" the single
      dominant dispute via a spurious one-shared-claim brush (Jaccard ~0.01).
    - STRICT (dispute_mass_coverage_strict): a proposition is covered only if its
      best-matching crux clears MIN_MEANINGFUL_JACCARD (config, default 0.1) —
      i.e. a real claim-set overlap, not an accidental one.

  The pair is diagnostic, not redundant. The crux-builder's blind spot is a
  function of debate *shape*, and the lax/strict gap exposes how it fails:
    - A debate whose mass is concentrated in one huge dispute can show high LAX
      coverage that collapses under STRICT — the dominant dispute was only
      "covered" by a fine-grained crux brushing it (e.g. eggs: a 96%-of-mass
      proposition matched at Jaccard 0.017 by a lipoprotein-particle-size crux).
    - A debate fragmented into many facets shows low coverage under *both* rules
      because the crux-builder chases sub-questions and orphans the bulk mass
      (e.g. covid). Same instrument, opposite verdicts, one comparable metric.

Nothing here is case-specific: it operates purely on claim_id sets and the
generic proposition fields (edge_count, source_count, status).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from episteme.compile.crystallize import compiled_dir
from episteme.config import MIN_MEANINGFUL_JACCARD

# Convergence / divergence thresholds (overridable via run_crux_alignment args).
STRONG_MATCH_JACCARD = 0.3      # crux<->proposition counts as a strong match
ORPHAN_CRUX_JACCARD = 0.1       # below this best-match, the crux is an orphan
ORPHAN_PROP_MIN_EDGES = 20      # a proposition must be this big to be "missed"
ORPHAN_PROP_MIN_SOURCES = 2     # ...and multi-source, i.e. a real dispute


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def _load_cruxes(case: str) -> list[dict]:
    path = compiled_dir(case) / "index.json"
    if not path.exists():
        return []
    index = json.loads(path.read_text(encoding="utf-8"))
    return index.get("cruxes", []) or []


def _load_propositions(case: str) -> list[dict]:
    path = compiled_dir(case) / "propositions.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")) or []


def align_cruxes(cruxes: list[dict], propositions: list[dict]) -> list[dict]:
    """Best-matching proposition per crux by Jaccard of claim_id sets.

    Deterministic: propositions are scanned in id order and the first to achieve
    the best (jaccard, shared_claim_count) wins ties.
    """
    props_sorted = sorted(propositions, key=lambda p: p.get("id", ""))
    prop_sets = {p["id"]: set(p.get("claim_ids", []) or []) for p in props_sorted}
    prop_q = {p["id"]: p.get("question", "") for p in props_sorted}

    alignments: list[dict] = []
    for crux in cruxes:
        cset = set(crux.get("claim_ids", []) or [])
        best_id = None
        best_jac = 0.0
        best_shared = 0
        for p in props_sorted:
            pid = p["id"]
            shared = len(cset & prop_sets[pid])
            jac = _jaccard(cset, prop_sets[pid])
            if (jac, shared) > (best_jac, best_shared):
                best_jac, best_shared, best_id = jac, shared, pid
        alignments.append({
            "crux_id": crux.get("id", ""),
            "crux_question": crux.get("question", ""),
            "best_proposition_id": best_id,
            "proposition_question": prop_q.get(best_id, "") if best_id else "",
            "jaccard": round(best_jac, 4),
            "shared_claim_count": best_shared,
            "crux_claim_count": len(cset),
        })
    return alignments


def _dispute_mass_coverage(
    alignments: list[dict],
    propositions: list[dict],
    min_jaccard: float = 0.0,
) -> dict:
    """How much of the contested-dispute 'mass' (summed edge_count over
    contested, multi-source propositions) is captured by the cruxes.

    COVERED = the proposition is the best-match (highest claim_ids Jaccard) of at
    least one crux whose best-match Jaccard is >= ``min_jaccard``. With the
    default ``min_jaccard=0.0`` this is the *lax* rule (any best-match counts,
    even a single-shared-claim brush at Jaccard ~0.01). Passing a positive floor
    (e.g. config.MIN_MEANINGFUL_JACCARD) gives the *strict* rule, where a
    proposition only counts as covered if some crux overlaps it meaningfully.

    Reuses the alignment already computed (best_proposition_id); it does not
    recompute matching. Deterministic; uncovered list ordered by (-edge_count,
    id). Edge counts are integers so M_covered + M_uncovered == M.
    """
    best_match_ids = {
        a["best_proposition_id"]
        for a in alignments
        if a["best_proposition_id"] and a["jaccard"] >= min_jaccard
    }
    contested = [
        p for p in propositions
        if p.get("status") == "contested" and (p.get("source_count", 0) or 0) >= 2
    ]
    M = sum(int(p.get("edge_count", 0) or 0) for p in contested)
    covered = [p for p in contested if p["id"] in best_match_ids]
    M_covered = sum(int(p.get("edge_count", 0) or 0) for p in covered)
    M_uncovered = M - M_covered
    coverage = (M_covered / M) if M else 0.0

    uncovered_props = [p for p in contested if p["id"] not in best_match_ids]
    uncovered_props.sort(key=lambda p: (-int(p.get("edge_count", 0) or 0), p["id"]))

    uncovered_out: list[dict] = []
    cumulative = 0
    for p in uncovered_props:
        ec = int(p.get("edge_count", 0) or 0)
        share = (ec / M) if M else 0.0
        cumulative += ec
        cumulative_share = (cumulative / M) if M else 0.0
        uncovered_out.append({
            "id": p["id"],
            "question": p.get("question", ""),
            "edge_count": ec,
            "source_count": p.get("source_count", 0),
            "share": round(share, 4),
            "cumulative_share": round(cumulative_share, 4),
        })

    return {
        "min_jaccard": min_jaccard,
        "M": M,
        "M_covered": M_covered,
        "M_uncovered": M_uncovered,
        "coverage": round(coverage, 4),
        "n_contested_props": len(contested),
        "n_covered": len(covered),
        "n_uncovered": len(uncovered_out),
        "uncovered_propositions": uncovered_out,
    }


def run_crux_alignment(
    case: str,
    *,
    strong_match: float = STRONG_MATCH_JACCARD,
    orphan_crux: float = ORPHAN_CRUX_JACCARD,
    orphan_prop_min_edges: int = ORPHAN_PROP_MIN_EDGES,
    orphan_prop_min_sources: int = ORPHAN_PROP_MIN_SOURCES,
    min_meaningful_jaccard: float = MIN_MEANINGFUL_JACCARD,
) -> dict:
    """Deterministic, $0, read-only alignment of cruxes vs propositions.

    Reads compiled/index.json and compiled/propositions.json, writes
    compiled/crux_alignment.json. Touches neither graph.json nor index.json.
    """
    cruxes = _load_cruxes(case)
    propositions = _load_propositions(case)
    alignments = align_cruxes(cruxes, propositions)

    strong_matches = [a for a in alignments if a["jaccard"] >= strong_match]
    orphan_cruxes = [a for a in alignments if a["jaccard"] < orphan_crux]
    mean_best_jaccard = (
        sum(a["jaccard"] for a in alignments) / len(alignments) if alignments else 0.0
    )

    # A proposition is "covered" if some crux's best match points to it with a
    # non-orphan-level overlap. High-volume contested propositions that remain
    # uncovered are disputes the crux-builder missed.
    covered = {
        a["best_proposition_id"]
        for a in alignments
        if a["best_proposition_id"] and a["jaccard"] >= orphan_crux
    }
    orphan_propositions = [
        {
            "id": p["id"],
            "question": p.get("question", ""),
            "edge_count": p.get("edge_count", 0),
            "source_count": p.get("source_count", 0),
            "status": p.get("status", ""),
        }
        for p in propositions
        if p.get("status") == "contested"
        and (p.get("edge_count", 0) or 0) >= orphan_prop_min_edges
        and (p.get("source_count", 0) or 0) >= orphan_prop_min_sources
        and p["id"] not in covered
    ]
    orphan_propositions.sort(key=lambda p: (-p["edge_count"], p["id"]))

    result = {
        "case": case,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "strong_match_jaccard": strong_match,
            "orphan_crux_jaccard": orphan_crux,
            "orphan_prop_min_edges": orphan_prop_min_edges,
            "orphan_prop_min_sources": orphan_prop_min_sources,
            "min_meaningful_jaccard": min_meaningful_jaccard,
        },
        "counts": {
            "cruxes": len(cruxes),
            "propositions": len(propositions),
            "strong_matches": len(strong_matches),
            "orphan_cruxes": len(orphan_cruxes),
            "orphan_propositions": len(orphan_propositions),
        },
        "mean_best_jaccard": round(mean_best_jaccard, 4),
        "alignments": alignments,
        "orphan_cruxes": orphan_cruxes,
        "orphan_propositions": orphan_propositions,
        "dispute_mass_coverage": _dispute_mass_coverage(alignments, propositions),
        "dispute_mass_coverage_strict": _dispute_mass_coverage(
            alignments, propositions, min_jaccard=min_meaningful_jaccard
        ),
    }

    path = compiled_dir(case) / "crux_alignment.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_report(result, path)
    return result


def _print_report(result: dict, path) -> None:
    counts = result["counts"]
    print(f"  Cruxes: {counts['cruxes']}  Propositions: {counts['propositions']}")
    print(
        f"  Convergence: {counts['strong_matches']}/{counts['cruxes']} cruxes "
        f"strongly matched (jaccard >= {result['params']['strong_match_jaccard']}), "
        f"mean best-jaccard {result['mean_best_jaccard']}"
    )

    print("  Alignment (crux -> best proposition):")
    print(f"    {'crux_id':<32} {'prop':<10} {'jac':>6} {'shared':>6}  questions")
    for a in result["alignments"]:
        bp = a["best_proposition_id"] or "-"
        print(
            f"    {a['crux_id'][:32]:<32} {bp:<10} {a['jaccard']:>6.3f} "
            f"{a['shared_claim_count']:>6}  "
            f"{a['crux_question'][:48]} || {a['proposition_question'][:48]}"
        )

    print(f"  Orphan cruxes (best jaccard < {result['params']['orphan_crux_jaccard']}) "
          f"-> possible weak/hallucinated cruxes: {len(result['orphan_cruxes'])}")
    for a in result["orphan_cruxes"]:
        print(f"    [{a['crux_id']}] jac={a['jaccard']:.3f} "
              f"shared={a['shared_claim_count']}/{a['crux_claim_count']}  "
              f"{a['crux_question'][:80]}")

    print(
        f"  Orphan propositions (contested, edges >= "
        f"{result['params']['orphan_prop_min_edges']}, sources >= "
        f"{result['params']['orphan_prop_min_sources']}, no crux) "
        f"-> disputes the crux-builder missed: {len(result['orphan_propositions'])}"
    )
    for p in result["orphan_propositions"]:
        print(f"    [{p['id']}] edges={p['edge_count']:<3} sources={p['source_count']:<2} "
              f"{p['question'][:90]}")

    cov = result["dispute_mass_coverage"]
    cov_strict = result["dispute_mass_coverage_strict"]
    M = cov["M"]
    strict_floor = result["params"]["min_meaningful_jaccard"]
    print(f"  Dispute universe: {cov['n_contested_props']} contested propositions, {M} edges.")
    print(f"  Lax coverage (best-match):   {cov['coverage']:>4.0%}  "
          f"({cov['M_covered']}/{M} edges)")
    print(f"  Strict coverage (J>={strict_floor}):   {cov_strict['coverage']:>4.0%}  "
          f"({cov_strict['M_covered']}/{M} edges)")
    uncovered = cov["uncovered_propositions"]
    if uncovered:
        top = uncovered[0]
        print(f"  Largest uncovered dispute: {top['id']} ({top['edge_count']} edges = "
              f"{top['share']:.0%} of all contested edges) — \"{top['question'][:80]}\"")
        print(f"    {'prop_id':<10} {'edges':>5} {'share':>6} {'cumshare':>8}  question")
        for p in uncovered:
            print(f"    {p['id']:<10} {p['edge_count']:>5} {p['share']:>6.0%} "
                  f"{p['cumulative_share']:>8.0%}  {p['question'][:60]}")

    print(f"  Alignment saved: {path}")
