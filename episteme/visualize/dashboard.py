"""Static HTML dashboard — cruxes + methodology audits."""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path

from episteme.config import CASES_DIR
from episteme.compile.source_importance import load_source_importance
from episteme.compile.crystallize import load_compiled, repair_compiled_index
from episteme.compile.debate_state import load_debate_state
from episteme.pipeline.attestation_stance import StanceGuard, load_stance_guard
from episteme.compile.references import chains_for_crux, crux_criticality, _themes_by_id
from episteme.core.graph import GraphStore, group_attestations_by_source, unique_attestation_source_count, ensure_attestations
from episteme.methodology.criteria import load_methodology_profile
from episteme.methodology.paths import audits_dir
from episteme.pipeline.hypothesis import load_all_hypotheses
from episteme.pipeline.relate import load_cross_links
from episteme.reasoning.paths import (
    devils_advocate_dir,
    field_briefing_path,
    open_questions_path,
    presuppositions_path,
)


def _tab_legend_html(
    items: list[tuple[str, str, str]],
    note: str = "",
    *,
    include_vocab: bool = True,
) -> str:
    """Per-tab badge legend — unified 5-tier vocabulary plus tab-specific examples."""
    combined = list(BADGE_VOCAB) + list(items) if include_vocab else list(items)
    lis = [
        f'<li><span class="badge {cls}">{_esc(label)}</span> '
        f'<span class="legend-desc">{_esc(desc)}</span></li>'
        for cls, label, desc in combined
    ]
    note_html = f'<p class="legend-note">{_esc(note)}</p>' if note else ""
    return (
        f'<details class="badge-legend tab-legend" open>'
        f'<summary>Legend — badge vocabulary</summary>'
        f'<ul>{"".join(lis)}</ul>'
        f"{note_html}"
        f"</details>"
    )


# Unified badge tiers (5 visual levels — same vocabulary across all tabs)
BADGE_VOCAB = [
    ("b-danger", "critical", "Fatal impact, stance conflict, red flag, high vulnerability"),
    ("b-warn", "caution", "Major impact, contested, ungrounded, review, not declared"),
    ("b-ok", "confirmed", "Established, declared, canonical, grounded, central source"),
    ("b-muted", "neutral", "Single source, peripheral, not applicable, low leverage"),
    ("b-info", "category", "Relation or question type (supports, contradicts, empirical, …)"),
]

LEGEND_CRUXES = [
    ("b-danger", "FATAL", "Chain/theme — argument collapses if this fails"),
    ("b-warn", "MAJOR", "Chain/theme — important supporting condition"),
    ("b-warn", "REVIEW", "Theme or gap flagged for human review"),
]

LEGEND_DEBATE = [
    ("b-info", "supports", "Cross-paper relation type (also contradicts, qualifies, …)"),
    ("b-ok", "well_established", "3+ papers attest (reconcile pass)"),
    ("b-ok", "supported", "2 papers attest"),
    ("b-warn", "contested", "Contradicted by other papers"),
    ("b-ok", "canonical", "Representative wording in a compression group"),
    ("b-warn", "variant", "Paraphrase of canonical (similarity ≥ 0.75)"),
    ("b-ok", "from_evidence", "Hypothesis parameter grounded in corpus"),
    ("b-warn", "ungrounded", "Hypothesis parameter not traced to sources"),
]

LEGEND_METHODOLOGY = [
    ("b-ok", "declared", "Criterion adequately reported"),
    ("b-warn", "not_declared", "Criterion missing or not reported"),
    ("b-danger", "red_flag", "Serious methodological problem"),
    ("b-muted", "not_applicable", "Criterion does not apply"),
    ("b-ok", "central", "Source anchors multiple crux claims"),
    ("b-warn", "latent gem", "Unique high-value claims — low cross-reference"),
    ("b-muted", "peripheral", "Low graph footprint"),
]

LEGEND_REASONING = [
    ("b-danger", "high", "Presupposition vulnerability — if false, much breaks"),
    ("b-warn", "medium", "Moderate vulnerability"),
    ("b-info", "empirical", "Open question type"),
    ("b-ok", "high leverage", "Answering would shift the field most"),
    ("b-warn", "low confidence", "Devil's advocate lowered confidence"),
    ("b-ok", "ok", "Claim largely withstands skepticism"),
]


def _badge(label: str, tier: str) -> str:
    return f'<span class="badge {_esc(tier)}">{_esc(label)}</span>'


def _badge_impact(impact: str) -> str:
    tier = {"FATAL": "b-danger", "CRITICAL": "b-danger", "MAJOR": "b-warn"}.get(
        (impact or "").upper(), "b-muted"
    )
    return _badge(impact or "?", tier)


def _badge_review() -> str:
    return _badge("REVIEW", "b-warn")


def _badge_relation(rel_type: str) -> str:
    return _badge(rel_type or "?", "b-info")


def _badge_epistemic(status: str) -> str:
    if (status or "").strip() == "single_source":
        return ""
    tier = {
        "well_established": "b-ok",
        "supported": "b-ok",
        "contested": "b-warn",
    }.get((status or "").strip(), "b-muted")
    return _badge(status, tier)


def _badge_audit_status(status: str) -> str:
    tier = {
        "declared": "b-ok",
        "not_declared": "b-warn",
        "red_flag": "b-danger",
        "not_applicable": "b-muted",
    }.get((status or "").strip(), "b-muted")
    return _badge(status or "?", tier)


def _badge_importance(tier: str) -> str:
    t = (tier or "").lower()
    if t == "latent_gem":
        return '<span class="badge b-warn b-gem">latent gem</span>'
    if t == "central":
        return _badge("central", "b-ok")
    if t == "contributory":
        return _badge("contributory", "b-muted")
    if t == "peripheral":
        return _badge("peripheral", "b-muted")
    return ""


def _badge_grounding(grounding: str) -> str:
    g = (grounding or "").lower()
    tier = "b-ok" if g in ("from_evidence", "grounded", "from evidence") else "b-warn"
    return _badge(grounding or "ungrounded", tier)


def _badge_stance_conflict(*, in_attestations: bool = False) -> str:
    label = "stance conflict in attestations" if in_attestations else "stance conflict"
    return _badge(label, "b-danger")


def _badge_vulnerability(level: str) -> str:
    tier = {"high": "b-danger", "medium": "b-warn", "low": "b-muted"}.get(
        (level or "").lower(), "b-muted"
    )
    return _badge(level or "?", tier)


def _badge_leverage(level: str) -> str:
    tier = {"high": "b-ok", "medium": "b-warn", "low": "b-muted"}.get(
        (level or "").lower(), "b-muted"
    )
    return _badge(f"{level} leverage" if level else "leverage", tier)


def _badge_qtype(qtype: str) -> str:
    return _badge(qtype or "?", "b-info")


def _subfield_anchor(subfield: str) -> str:
    slug = re.sub(r"[^\w]+", "-", (subfield or "uncategorized").lower()).strip("-")
    return f"cross-{slug[:56] or 'other'}"


def _build_cross_links_grouped_html(relations: list[dict]) -> tuple[str, str]:
    by_subfield: dict[str, list[dict]] = defaultdict(list)
    for rel in relations:
        sf = (rel.get("subfield") or "").strip() or "Uncategorized"
        by_subfield[sf].append(rel)

    groups = sorted(by_subfield.items(), key=lambda x: (-len(x[1]), x[0].lower()))
    parts = []
    nav_bits = []

    for subfield, rels in groups:
        anchor = _subfield_anchor(subfield)
        nav_bits.append(
            f'<a href="#{_esc(anchor)}" class="nav-item debate-nav nav-subfield">'
            f"{_esc(subfield)} ({len(rels)})</a>"
        )
        rows = []
        for rel in sorted(
            rels,
            key=lambda r: (-float(r.get("strength") or 0), r.get("from_id", ""), r.get("to_id", "")),
        ):
            rows.append(
                f"<tr>"
                f"<td><code>{_esc(rel.get('from_id', ''))}</code></td>"
                f"<td>{_badge_relation(rel.get('type', ''))}</td>"
                f"<td><code>{_esc(rel.get('to_id', ''))}</code></td>"
                f"<td>{rel.get('strength', '')}</td>"
                f"<td class='rationale'>{_esc(rel.get('rationale', ''))}</td>"
                f"</tr>"
            )
        parts.append(
            f'<details class="cross-subfield-group" id="{_esc(anchor)}" open>'
            f"<summary><strong>{_esc(subfield)}</strong> "
            f'<span class="meta">({len(rels)} relations)</span></summary>'
            f'<table class="cross-table cross-table-grouped"><thead><tr>'
            f"<th>From</th><th>Type</th><th>To</th><th>Strength</th><th>Rationale</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            f"</details>"
        )

    body = (
        f'<section class="debate-section" id="cross-links-table">'
        f"<h2>Cross-paper relations ({len(relations)})</h2>"
        f'<p class="section-note">Edges between claims from different sources, grouped by '
        f"<code>subfield</code> ({len(groups)} groups). IDs reference <code>graph.json</code> nodes.</p>"
        f'<div class="cross-links-grouped">{"".join(parts)}</div>'
        f"</section>"
    )
    nav = (
        f'<a href="#cross-links-table" class="nav-item debate-nav">Cross-links ({len(relations)})</a>'
        + "".join(nav_bits)
    )
    return body, nav


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def _quote_for_node(node: dict) -> str | None:
    q = node.get("textual_evidence") or node.get("quote_exact")
    if q:
        return q
    for att in _attestations_for_node(node):
        aq = (att.get("quote") or "").strip()
        if aq:
            return aq
    return None


def _attestations_for_node(node: dict) -> list[dict]:
    return ensure_attestations(node)


def _valid_attestations(node: dict) -> list[dict]:
    return [a for a in _attestations_for_node(node) if (a.get("quote") or "").strip()]


def _attestation_quote_count(node: dict) -> int:
    atts = _valid_attestations(node)
    if not atts:
        return 1 if _quote_for_node(node) else 0
    return sum(
        max(1, len([q for q in (grp.get("quotes") or []) if q]))
        for grp in group_attestations_by_source(atts)
    )


def _attestations_body(node: dict, stance_guard: StanceGuard) -> str:
    """Full quote blocks for panel or expanded view."""
    atts = _valid_attestations(node)
    if not atts:
        quote = _quote_for_node(node)
        if quote:
            return f'<blockquote class="quote">"{_esc(quote)}"</blockquote>'
        return '<p class="no-quote">No verbatim quote in graph</p>'

    groups = group_attestations_by_source(atts)
    blocks = []
    for i, grp in enumerate(groups):
        author = grp.get("author") or grp.get("source_id", "")
        label = f"Source {i + 1}" if len(groups) > 1 else "Source"
        quote_blocks = []
        claim_content = node.get("content", "")
        for q in grp.get("quotes", []):
            warn = ""
            if stance_guard.attestation_conflicts_claim(q, claim_content):
                warn = " " + _badge_stance_conflict()
            quote_blocks.append(f'<blockquote class="quote">"{_esc(q)}"{warn}</blockquote>')
        if not quote_blocks:
            quote_blocks.append('<span class="no-quote-inline">no quote stored</span>')
        extra = ""
        if grp.get("count", 0) > len(grp.get("quotes", [])):
            extra = f' <span class="attestation-count">({grp["count"]} excerpts)</span>'
        blocks.append(
            f'<div class="attestation">'
            f'<div class="attestation-header">{label}: <span class="author">{_esc(author)}</span>{extra}</div>'
            f"{''.join(quote_blocks)}</div>"
        )
    return "".join(blocks)


def _attestations_html(node: dict, stance_guard: StanceGuard) -> str:
    """Legacy inline attestations — kept for debate tab."""
    body = _attestations_body(node, stance_guard)
    atts = _attestations_for_node(node)
    if not atts:
        return body
    unique_sources = len(group_attestations_by_source(atts))
    header = (
        f'<p class="attestation-count">{unique_sources} source attestation(s)</p>'
        if unique_sources > 1
        else ""
    )
    return header + body


def _theme_label(themes: dict, theme_id: str) -> str:
    label = (themes.get(theme_id, {}).get("label") or "").strip()
    if label:
        return label
    if theme_id:
        return f"(theme {theme_id} not in compiled index — re-run crystallize)"
    return "(missing theme_id)"


def _impact_badge(impact: str) -> str:
    return _badge_impact(impact)


def _status_badge(status: str) -> str:
    return _badge_audit_status(status)


def _node_source_label(node: dict) -> str:
    atts = _attestations_for_node(node)
    if atts:
        groups = group_attestations_by_source(atts)
        if groups:
            return groups[0].get("author") or groups[0].get("source_id", "")
    return node.get("source_author") or node.get("source_url", "") or node.get("source_type", "unknown")


def _claim_quote_fragments(
    claim_id: str,
    node: dict,
    debate_by_id: dict[str, dict],
    *,
    scope: str = "",
    stance_guard: StanceGuard,
) -> tuple[str, str]:
    """Quote button + hidden template for the right panel."""
    quote_count = _attestation_quote_count(node)
    scope_suffix = f"-{_esc(scope)}" if scope else ""
    quote_store_id = f"quotes-{_esc(claim_id)}{scope_suffix}"
    author = _node_source_label(node)
    relations_block = _debate_relations_html(claim_id, debate_by_id)
    quote_btn = (
        f'<button type="button" class="quote-btn" data-quote-target="{quote_store_id}">'
        f"Quotes ({quote_count})</button>"
        if quote_count
        else '<span class="no-quote-inline">no quote</span>'
    )
    _ew = node.get("evidential_weight")
    _ew_display = "N/A" if _ew is None else f"{_ew:.2f}"
    template = (
        f'<template id="{quote_store_id}">'
        f'<div class="quote-panel-claim">'
        f'<p class="quote-panel-claim-id"><code>{_esc(claim_id)}</code> '
        f'<span class="type">{_esc(node.get("type", ""))}</span></p>'
        f'<p class="quote-panel-claim-text">{_esc(node.get("content", ""))}</p>'
        f'<p class="quote-panel-meta">{_esc(author)} · ew={_ew_display}</p>'
        f"{_attestations_body(node, stance_guard)}"
        f"{relations_block}"
        f"</div></template>"
    )
    return quote_btn, template


def _cluster_source_keys(graph: dict, member_ids: list[str]) -> set[str]:
    keys: set[str] = set()
    for mid in member_ids:
        node = graph.get(mid)
        if not node:
            continue
        atts = _attestations_for_node(node)
        if atts:
            for grp in group_attestations_by_source(atts):
                keys.add(grp.get("source_id") or grp.get("author") or "?")
        else:
            keys.add(_node_source_label(node))
    return keys


def _claim_cluster_card(
    cluster: dict,
    graph: dict,
    debate_by_id: dict[str, dict],
    stance_guard: StanceGuard,
) -> str:
    rep_id = cluster.get("representative_id", "")
    rep_node = graph.get(rep_id)
    member_ids = cluster.get("member_ids", [])
    n_members = cluster.get("member_count", len(member_ids))
    n_sources = len(_cluster_source_keys(graph, member_ids))
    rep = cluster.get("representative", {})
    rep_content = rep.get("content") or (rep_node or {}).get("content", "")

    canonical_html = ""
    if rep_node:
        quote_btn, template = _claim_quote_fragments(
            rep_id, rep_node, debate_by_id, scope="cluster", stance_guard=stance_guard
        )
        relations_block = _debate_relations_html(rep_id, debate_by_id)
        canonical_html = (
            f'<div class="claim-card cluster-canonical" id="claim-{_esc(rep_id)}" data-claim-id="{_esc(rep_id)}">'
            f'<div class="claim-header">'
            f'<span class="badge b-ok">canonical</span> '
            f'<code>{_esc(rep_id)}</code> '
            f'<span class="author" title="{_esc(_node_source_label(rep_node))}">'
            f'{_esc(_node_source_label(rep_node))}</span> '
            f"{quote_btn}"
            f"</div>"
            f'<p class="claim-content">{_esc(rep_content)}</p>'
            f"{template}"
            f"{relations_block}"
            f"</div>"
        )
    else:
        canonical_html = (
            f'<div class="claim-card cluster-canonical missing">'
            f'<span class="badge b-ok">canonical</span> '
            f'<code>{_esc(rep_id)}</code> '
            f'<p class="claim-content">{_esc(rep_content)}</p>'
            f"</div>"
        )

    variants = []
    for mid in member_ids:
        if mid == rep_id:
            continue
        node = graph.get(mid)
        if not node:
            variants.append(
                f'<li class="cluster-variant missing"><code>{_esc(mid)}</code> — not in graph</li>'
            )
            continue
        quote_btn, template = _claim_quote_fragments(
            mid, node, debate_by_id, scope="cluster-member", stance_guard=stance_guard
        )
        variants.append(
            f'<li class="cluster-variant">'
            f'<div class="claim-card" id="claim-{_esc(mid)}" data-claim-id="{_esc(mid)}">'
            f'<div class="claim-header">'
            f'<span class="badge b-warn">variant</span> '
            f'<code>{_esc(mid)}</code> '
            f'<span class="author" title="{_esc(_node_source_label(node))}">'
            f'{_esc(_node_source_label(node))}</span> '
            f"{quote_btn}"
            f"</div>"
            f'<p class="claim-content">{_esc(node.get("content", ""))}</p>'
            f"{template}"
            f"</div></li>"
        )

    source_note = (
        f"{n_sources} sources"
        if n_sources > 1
        else "1 source"
    )
    variants_html = ""
    if variants:
        variants_html = (
            f'<h4>Variants ({len(variants)})</h4>'
            f'<p class="section-note-inline">Separate graph nodes merged at similarity ≥ 0.75 '
            f"(ingest dedup uses 0.85).</p>"
            f'<ul class="cluster-variants">{"".join(variants)}</ul>'
        )

    return (
        f'<div class="cluster-card" id="{_esc(cluster.get("cluster_id", ""))}">'
        f'<div class="cluster-header">'
        f'<h3><code>{_esc(cluster.get("cluster_id", ""))}</code></h3>'
        f'<span class="meta">{n_members} claims · {source_note}</span>'
        f"</div>"
        f"{canonical_html}"
        f"{variants_html}"
        f"</div>"
    )


def _epistemic_meta_html(claim_id: str, graph: dict) -> str:
    """Badge + paper counts derived from attestations with stored quotes."""
    g = graph.get(claim_id) or {}
    status = (g.get("epistemic_status") or "").strip()
    atts = _valid_attestations(g)
    support = unique_attestation_source_count(atts) if atts else 0
    contradict = g.get("contradict_count", 0)
    if support is None:
        support = unique_attestation_source_count(ensure_attestations(g))
    if not status:
        if contradict and contradict > 0:
            status = "contested"
        elif support >= 3:
            status = "well_established"
        elif support >= 2:
            status = "supported"
        else:
            status = "single_source"
    badge = _badge_epistemic(status)
    parts = [f"{support} paper{'s' if support != 1 else ''}"]
    if contradict:
        parts.append(f"{contradict} contradicting")
    meta = " · ".join(parts)
    return f'<div class="epistemic-meta">{badge} <span class="meta">{_esc(meta)}</span></div>'


def _multi_source_cards(graph: dict, debate_by_id: dict[str, dict], limit: int = 40) -> list[dict]:
    """Canonical multi-paper nodes from reconciled graph (not stale debate_state)."""
    rows = []
    for nid, gnode in graph.items():
        if gnode.get("type") not in ("claim", "evidence"):
            continue
        atts = _valid_attestations(gnode)
        if unique_attestation_source_count(atts) <= 1:
            continue
        rows.append({
            "claim_id": nid,
            "canonical": gnode.get("content", ""),
            "type": gnode.get("type"),
            "attestations": atts,
                "evidential_weight": gnode.get("evidential_weight") or 0,
            "support_count": unique_attestation_source_count(atts),
            "contradict_count": gnode.get("contradict_count", 0),
            "epistemic_status": gnode.get("epistemic_status", ""),
            "debate": debate_by_id.get(nid, {}),
        })
    rows.sort(
        key=lambda n: (
            -n.get("support_count", 0),
            -n.get("evidential_weight", 0),
            n.get("claim_id", ""),
        )
    )
    return rows[:limit]


def _debate_relations_html(claim_id: str, debate_by_id: dict[str, dict]) -> str:
    d = debate_by_id.get(claim_id)
    if not d:
        return ""
    parts = []
    for label, key in (
        ("Requires", "requires_true"),
        ("If false, falls", "if_false_then_falls"),
        ("Contradicted by", "contradicted_by"),
        ("Supported by", "supported_by"),
        ("Undermined by", "undermined_by"),
    ):
        items = d.get(key, [])
        if not items:
            continue
        lis = []
        for item in items:
            rel = item.get("relation", {})
            src = rel.get("source", "")
            meta = ""
            if rel:
                meta = (
                    f' <span class="rel-meta">'
                    f'({rel.get("type", "")}, strength={rel.get("strength", "?")}'
                    f'{", " + src if src else ""})</span>'
                )
            lis.append(
                f'<li><code>{_esc(item.get("claim_id", ""))}</code> — '
                f'{_esc(item.get("canonical", ""))}{meta}</li>'
            )
            if rel.get("rationale"):
                lis.append(f'<li class="rel-rationale">{_esc(rel["rationale"])}</li>')
        parts.append(f'<div class="rel-group"><strong>{label}</strong><ul>{"".join(lis)}</ul></div>')
    return f'<div class="debate-edges">{"".join(parts)}</div>' if parts else ""


def _hypothesis_n_needed_summary(n_needed) -> str:
    if isinstance(n_needed, dict):
        return str(n_needed.get("value") or "TBD")
    return str(n_needed or "")


def _hypothesis_n_needed_html(n_needed) -> str:
    if not isinstance(n_needed, dict):
        return ""
    blocks = []
    if n_needed.get("discovery_cohort_warning"):
        blocks.append(
            f'<div class="hypothesis-warning">'
            f'<strong>Discovery-cohort warning</strong> — '
            f'{_esc(n_needed["discovery_cohort_warning"])}</div>'
        )
    missing = n_needed.get("missing_inputs") or []
    if missing:
        lis = "".join(f"<li>{_esc(m)}</li>" for m in missing)
        blocks.append(f"<h5>Missing for power analysis</h5><ul>{lis}</ul>")
    pai = n_needed.get("power_analysis_inputs") or {}
    if pai:
        rows = []
        for key, label in (
            ("effect_size", "Effect size"),
            ("variance_estimate", "Variance"),
            ("alpha", "Alpha"),
            ("power", "Power"),
        ):
            entry = pai.get(key)
            if not entry:
                continue
            if isinstance(entry, dict):
                val = entry.get("value", "")
                src = entry.get("source", "")
                extra = entry.get("type") or entry.get("definition") or entry.get("note") or ""
                detail = f"{_esc(val)}"
                if extra:
                    detail += f" — {_esc(extra)}"
                if src:
                    detail += f' <span class="rel-meta">({_esc(src)})</span>'
            else:
                detail = _esc(str(entry))
            rows.append(f"<tr><td>{label}</td><td>{detail}</td></tr>")
        test_type = pai.get("test_type")
        if test_type:
            rows.append(f"<tr><td>Test</td><td>{_esc(test_type)}</td></tr>")
        if rows:
            blocks.append(
                f"<h5>Power analysis inputs</h5>"
                f'<table class="hypothesis-params-table"><tbody>{"".join(rows)}</tbody></table>'
            )
    if n_needed.get("caveat"):
        blocks.append(f'<p class="meta-line"><strong>N caveat:</strong> {_esc(n_needed["caveat"])}</p>')
    return "".join(blocks)


def _hypothesis_grounding_html(hyp: dict) -> str:
    """Render parameter justifications and warnings for expert review."""
    params = hyp.get("study_parameters") or []
    invented = hyp.get("invented_or_unverified") or []
    blocks = []

    ungrounded = [
        p for p in params
        if (p.get("grounding") or "") in ("ungrounded", "to_be_determined")
    ]
    if ungrounded or invented:
        items = []
        for p in ungrounded:
            items.append(
                f'<li><strong>{_esc(p.get("parameter", "?"))}</strong>: '
                f'{_esc(p.get("value", ""))} — '
                f'{_badge_grounding(p.get("grounding", "ungrounded"))} '
                f'{_esc(p.get("justification_note", ""))}</li>'
            )
        for note in invented:
            items.append(f'<li>{_esc(note)}</li>')
        blocks.append(
            f'<div class="hypothesis-warning">'
            f'<strong>Needs justification</strong> — not guaranteed by source evidence:'
            f'<ul>{"".join(items)}</ul></div>'
        )

    overrides = hyp.get("verifier_overrides") or []
    if overrides:
        items = []
        for ov in overrides:
            items.append(
                f'<li><strong>{_esc(ov.get("parameter", "?"))}</strong>: '
                f'<code>{_esc(ov.get("from_grounding", ""))}</code> → '
                f'{_badge_grounding(ov.get("to_grounding", ""))} — '
                f'{_esc(ov.get("reason", ""))}</li>'
            )
        blocks.append(
            f'<div class="hypothesis-warning verifier-overrides">'
            f'<strong>Verifier overrides</strong> (Haiku second pass):'
            f'<ul>{"".join(items)}</ul></div>'
        )
    elif not params:
        n_needed = (hyp.get("proposed_study") or {}).get("n_needed")
        if not isinstance(n_needed, dict):
            blocks.append(
                f'<div class="hypothesis-warning">'
                f'<strong>Legacy hypothesis</strong> — generated before parameter-grounding rules. '
                f'Numeric criteria (e.g. follow-up days, sample size) may be LLM-invented. '
                f'Re-run <code>--step hypothesis --reset-cache</code> to regenerate with sources.</div>'
            )

    grounded = [p for p in params if p not in ungrounded]
    if grounded:
        rows = []
        for p in grounded:
            refs = []
            if p.get("source_node_ids"):
                refs.append(
                    "nodes: " + ", ".join(
                        f'<code>{_esc(i)}</code>' for i in p.get("source_node_ids", [])
                    )
                )
            if p.get("attestation_quote"):
                refs.append(f'quote: "{_esc(p.get("attestation_quote", ""))}"')
            if p.get("external_reference"):
                refs.append(f'ref: {_esc(p.get("external_reference", ""))}')
            ref_html = " · ".join(refs) if refs else _esc(p.get("justification_note", ""))
            rows.append(
                f'<tr>'
                f'<td>{_esc(p.get("parameter", ""))}</td>'
                f'<td>{_esc(p.get("value", ""))}</td>'
                f"<td>{_badge_grounding(p.get('grounding', ''))}</td>"
                f'<td class="rationale">{ref_html}</td>'
                f'</tr>'
            )
        blocks.append(
            f'<h5>Parameter grounding</h5>'
            f'<table class="hypothesis-params-table"><thead><tr>'
            f'<th>Parameter</th><th>Value</th><th>Source</th><th>Justification</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
        )

    return "".join(blocks)


def _hypothesis_html(hyp: dict, crux: dict | None = None) -> str:
    if not hyp:
        return ""
    study = hyp.get("proposed_study", {}) or {}
    measurements = "".join(f"<li>{_esc(m)}</li>" for m in study.get("key_measurements", []))
    controls = "".join(f"<li>{_esc(c)}</li>" for c in study.get("controls", []))
    cannot = "".join(
        f"<li>{_esc(x)}</li>" for x in hyp.get("what_you_cannot_claim_yet", [])
    )
    crux_id = hyp.get("crux_id") or (crux or {}).get("id", "")
    crux_block = ""
    if crux_id:
        question = (crux or {}).get("question", "")
        crux_block = (
            f'<p class="hypothesis-crux">'
            f'Settles crux <a href="#{_esc(crux_id)}"><code>{_esc(crux_id)}</code></a>'
            + (f' — <span class="hypothesis-crux-q">{_esc(question)}</span>' if question else "")
            + "</p>"
        )
    n_needed = study.get("n_needed")
    grounding_block = _hypothesis_grounding_html(hyp)
    n_detail = _hypothesis_n_needed_html(n_needed)
    return (
        f'<div class="hypothesis-card" id="hypothesis-{_esc(crux_id)}">'
        f"{crux_block}"
        f'<h4>Research hypothesis</h4>'
        f'<p class="hypothesis-main">{_esc(hyp.get("working_hypothesis", ""))}</p>'
        f'<p><strong>Falsification:</strong> {_esc(hyp.get("falsification_condition", ""))}</p>'
        f"{grounding_block}"
        f'<div class="study-grid">'
        f'<div><strong>Design</strong><br>{_esc(study.get("design", ""))}</div>'
        f'<div><strong>N needed</strong><br>{_esc(_hypothesis_n_needed_summary(n_needed))}</div>'
        f"</div>"
        f"{n_detail}"
        f"<h5>Key measurements</h5><ul>{measurements or '<li>—</li>'}</ul>"
        f"<h5>Controls</h5><ul>{controls or '<li>—</li>'}</ul>"
        f"<h5>Cannot claim yet</h5><ul>{cannot or '<li>—</li>'}</ul>"
        f"</div>"
    )


def _load_source_roles(case: str) -> dict[str, dict]:
    path = CASES_DIR / case / "compiled" / "source_roles.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _short_source_name(source_id: str, label: str = "") -> str:
    if label:
        return label
    stem = Path(source_id.replace("\\", "/")).stem
    if stem.startswith("sample_"):
        stem = stem[7:]
    return stem or source_id


_TIER_SORT = {"latent_gem": 0, "central": 1, "contributory": 2, "peripheral": 3}


def _importance_tier_badge(tier: str) -> str:
    return _badge_importance(tier)


def _source_importance_card(
    src: dict,
    audit: dict | None,
    role_info: dict | None,
) -> str:
    sid = src.get("source_id", "")
    tier = (src.get("importance_tier") or "peripheral").lower()
    name = _short_source_name(sid, src.get("label", ""))
    role = (role_info or {}).get("role") or src.get("role") or "unknown"
    meth_score = audit.get("methodology_score") if audit else None
    meth_html = f"<strong>{meth_score:.3f}</strong>" if meth_score is not None else "—"
    tier_badge = _importance_tier_badge(tier)
    open_attr = " open" if tier == "latent_gem" else ""

    latent_block = ""
    if tier == "latent_gem":
        latent_block = (
            f'<div class="latent-gem-callout">'
            f'<p class="latent-gem-title">💎 LATENT GEM</p>'
            f'<p>High unique contribution · Low cross-reference</p>'
            f'<p>This source contains claims no other source makes '
            f'that directly touch active cruxes.</p>'
            f'<p><strong>Risk if removed:</strong> {_esc(str(src.get("epistemic_risk", 0)))}</p>'
            f'<p class="latent-gem-warn">Review before concluding.</p>'
            f"</div>"
        )

    body = (
        f'<dl class="source-profile-dl">'
        f"<dt>role</dt><dd><code>{_esc(role)}</code> <span class='meta'>(Level 1)</span></dd>"
        f"<dt>methodology score</dt><dd>{meth_html}</dd>"
        f"<dt>importance</dt><dd>{tier_badge or _esc(tier.upper())} "
        f"<span class='meta'>(Level 3)</span></dd>"
        f"<dt>crux claims</dt><dd>{src.get('crux_claims_count', 0)}</dd>"
        f"<dt>unique claims</dt><dd>{src.get('unique_claims_count', 0)}</dd>"
        f"<dt>epistemic risk</dt><dd>{src.get('epistemic_risk', 0)}</dd>"
        f"</dl>"
        f"{latent_block}"
    )

    summary_extra = ""
    if tier == "latent_gem":
        summary_extra = " — review before concluding"
    elif tier == "peripheral":
        summary_extra = " — low footprint"

    return (
        f'<details class="source-importance-card tier-{tier}" id="importance-{_audit_anchor(sid)}"{open_attr}>'
        f'<summary><strong>{_esc(name)}</strong> {tier_badge}{summary_extra}</summary>'
        f'<div class="source-importance-body">{body}</div>'
        f"</details>"
    )


def _build_source_importance_section(
    importance: dict | None,
    audits: list[dict],
    roles: dict[str, dict],
) -> tuple[str, str]:
    if not importance or not importance.get("sources"):
        return "", ""

    audits_by_id = {a.get("source_id"): a for a in audits if a.get("source_id")}
    sources = sorted(
        importance.get("sources", []),
        key=lambda s: (_TIER_SORT.get(s.get("importance_tier", "peripheral"), 9), -s.get("epistemic_risk", 0)),
    )
    cards = [
        _source_importance_card(s, audits_by_id.get(s.get("source_id")), roles.get(s.get("source_id")))
        for s in sources
    ]
    latent_n = importance.get("stats", {}).get("latent_gems", 0)
    note = (
        f'<p class="section-note"><strong>{latent_n} latent gem(s)</strong> detected — '
        f"unique contributions touching cruxes with low cross-reference. "
        f"Expand orange cards before concluding.</p>"
        if latent_n
        else ""
    )
    section = (
        f'<section class="source-importance-section" id="source-importance">'
        f"<h2>Source importance ({len(sources)})</h2>"
        f'<p class="section-note">Epistemic footprint per paper — role (structural), methodology score (audit), '
        f"importance tier (post-graph). Not pre-declared; emerges from crux linkage and unique claims.</p>"
        f"{note}"
        f'<div class="source-importance-cards">{"".join(cards)}</div>'
        f"</section>"
    )
    nav = (
        f'<a href="#source-importance" class="nav-item method-nav">'
        f"Source importance ({len(sources)})</a>"
    )
    return section, nav


def _load_audits(case: str) -> list[dict]:
    adir = audits_dir(case)
    if not adir.exists():
        return []
    audits = []
    for path in sorted(adir.glob("*.json")):
        try:
            audits.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return audits


def _one_line(text: str, max_len: int = 200) -> str:
    """Single-line summary for chain/theme refs in crux sections."""
    s = " ".join(str(text or "").split())
    if len(s) <= max_len:
        return s
    trimmed = s[:max_len].rsplit(" ", 1)[0]
    return trimmed + "…"


def _crux_link_list(crux_ids: list[str], exclude: str | None = None) -> str:
    """Links to other cruxes sharing this chain/theme."""
    links = [
        f'<a href="#{_esc(cid)}"><code>{_esc(cid)}</code></a>'
        for cid in sorted(crux_ids)
        if cid and cid != exclude
    ]
    if not links:
        return ""
    return (
        f'<p class="registry-cruxes"><strong>Also in cruxes:</strong> '
        f'{", ".join(links)}</p>'
    )


def _chain_conditions_html(chain: dict, themes: dict) -> str:
    items = []
    for c in chain.get("conditions", []):
        items.append(
            f'<li>{_impact_badge(c.get("impact", ""))} '
            f'<code>{_esc(c.get("theme_id"))}</code> '
            f'({_esc(c.get("role"))}) — '
            f'{_esc(_theme_label(themes, c.get("theme_id", "")))}</li>'
        )
    return f'<ul class="conditions">{"".join(items)}</ul>' if items else ""


def _chain_full_card(
    chain: dict,
    themes: dict,
    crux_ids: list[str],
) -> str:
    ch_id = chain.get("id", "")
    conds = _chain_conditions_html(chain, themes)
    crux_links = _crux_link_list(crux_ids)
    narrative = (chain.get("narrative") or "").strip()
    narrative_html = ""
    if narrative:
        narrative_html = (
            f'<details class="chain-narrative">'
            f"<summary>Narrative</summary>"
            f'<p>{_esc(narrative)}</p>'
            f"</details>"
        )
    return (
        f'<div class="chain-card registry-card" id="{_esc(ch_id)}">'
        f'<h4><code>{_esc(ch_id)}</code></h4>'
        f'<p class="conclusion">{_esc(chain.get("conclusion", ""))}</p>'
        f"{conds}"
        f"{narrative_html}"
        f"{crux_links}"
        f"</div>"
    )


def _chain_ref_card(chain: dict) -> str:
    ch_id = chain.get("id", "")
    return (
        f'<div class="chain-ref">'
        f'<a href="#{_esc(ch_id)}"><code>{_esc(ch_id)}</code></a>'
        f'<p class="conclusion-oneline">{_esc(_one_line(chain.get("conclusion", "")))}</p>'
        f"</div>"
    )


def _theme_full_card(
    theme_id: str,
    theme: dict,
    crux_ids: list[str],
) -> str:
    review = " " + _badge_review() if theme.get("needs_review") else ""
    crux_links = _crux_link_list(crux_ids)
    return (
        f'<div class="theme-card registry-card" id="{_esc(theme_id)}">'
        f'<h4><code>{_esc(theme_id)}</code> {_impact_badge(theme.get("impact", ""))}{review}</h4>'
        f'<p>{_esc(theme.get("label", ""))}</p>'
        f'<p class="meta">{theme.get("member_count", 0)} presuppositions</p>'
        f"{crux_links}"
        f"</div>"
    )


def _theme_ref_card(theme_id: str, theme: dict) -> str:
    return (
        f'<div class="theme-ref">'
        f'<a href="#{_esc(theme_id)}"><code>{_esc(theme_id)}</code></a>'
        f'{_impact_badge(theme.get("impact", ""))}'
        f'<p class="conclusion-oneline">{_esc(_one_line(theme.get("label", "")))}</p>'
        f"</div>"
    )


def _chain_crux_index(cruxes: list[dict], index: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for crux in cruxes:
        cid = crux.get("id", "")
        for ch in chains_for_crux(crux, index):
            ch_id = ch.get("id", "")
            if ch_id:
                out.setdefault(ch_id, []).append(cid)
    return out


def _theme_crux_index(cruxes: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for crux in cruxes:
        cid = crux.get("id", "")
        for tid in crux.get("theme_ids", []):
            if tid:
                out.setdefault(tid, []).append(cid)
    return out


def _build_chain_theme_registry(
    index: dict,
    themes: dict,
    cruxes: list[dict],
) -> tuple[str, str]:
    """Full chain/theme definitions once + sidebar nav links."""
    chain_idx = _chain_crux_index(cruxes, index)
    theme_idx = _theme_crux_index(cruxes)

    chains_html = [
        _chain_full_card(ch, themes, chain_idx.get(ch.get("id", ""), []))
        for ch in index.get("chains", [])
    ]
    themes_html = [
        _theme_full_card(tid, themes[tid], theme_idx[tid])
        for tid in sorted(theme_idx.keys())
        if tid in themes
    ]

    n_chains = len(chains_html)
    n_themes = len(themes_html)
    body = (
        f'<section class="registry-section" id="ref-chains">'
        f"<h2>Argument chains (reference)</h2>"
        f'<p class="section-note">Each chain is defined once here. Crux sections above show a '
        f"short link — click to jump to the full conclusion and condition list.</p>"
        f'<div class="registry-list chains">{"".join(chains_html)}</div>'
        f"</section>"
        f'<section class="registry-section" id="ref-themes">'
        f"<h2>Shared presuppositions — themes (reference)</h2>"
        f'<p class="section-note">Theme clusters from the structure step. '
        f"{n_themes} themes linked across cruxes.</p>"
        f'<div class="registry-list themes">{"".join(themes_html)}</div>'
        f"</section>"
    )
    nav = (
        f'<div class="nav-group-label">Reference</div>'
        f'<a href="#ref-chains" class="nav-item">Chains ({n_chains})</a>'
        f'<a href="#ref-themes" class="nav-item">Themes ({n_themes})</a>'
    )
    return body, nav


def _crux_details_block(title: str, note: str, inner_html: str, count: int) -> str:
    """Collapsible crux subsection — closed by default to cut duplicate noise."""
    if count == 0 and "empty" in inner_html.lower():
        return (
            f'<details class="crux-collapsible">'
            f'<summary>{_esc(title)} (0)</summary>'
            f'<div class="crux-collapsible-body">{inner_html}</div>'
            f"</details>"
        )
    return (
        f'<details class="crux-collapsible">'
        f"<summary><strong>{_esc(title)}</strong> ({count})</summary>"
        f'<div class="crux-collapsible-body">'
        f'<p class="section-note">{note}</p>'
        f"{inner_html}"
        f"</div>"
        f"</details>"
    )


def _build_cruxes_html(
    case: str,
    index: dict,
    graph: dict,
    themes: dict,
    gaps_by_id: dict[str, dict],
    debate_by_id: dict[str, dict],
    cross_stats: dict | None,
    source_importance: dict | None = None,
    stance_guard: StanceGuard | None = None,
) -> tuple[str, str, int]:
    guard = stance_guard or load_stance_guard(case)
    cruxes = sorted(
        index.get("cruxes", []),
        key=lambda c: (-crux_criticality(c, index), c.get("id", "")),
    )

    sections = []
    nav_items = []

    for i, crux in enumerate(cruxes):
        cid = crux.get("id", f"crux_{i}")
        nav_items.append(
            f'<a href="#{_esc(cid)}" class="nav-item crux-nav">{_esc(cid.replace("_", " "))}</a>'
        )

        linked_chains = chains_for_crux(crux, index)
        chains_html = [_chain_ref_card(ch) for ch in linked_chains]

        themes_html = []
        for tid in crux.get("theme_ids", []):
            t = themes.get(tid, {})
            if t:
                themes_html.append(_theme_ref_card(tid, t))

        claims_html = []
        for claim_id in crux.get("claim_ids", []):
            node = graph.get(claim_id)
            if not node:
                claims_html.append(
                    f'<div class="claim-card missing">Missing: <code>{_esc(claim_id)}</code></div>'
                )
                continue
            relations_block = _debate_relations_html(claim_id, debate_by_id)
            author = _node_source_label(node)
            quote_btn, template = _claim_quote_fragments(
                claim_id, node, debate_by_id, scope=cid, stance_guard=guard
            )
            stance_warn = ""
            for att in _attestations_for_node(node):
                if guard.attestation_conflicts_claim(att.get("quote") or "", node.get("content", "")):
                    stance_warn = " " + _badge_stance_conflict(in_attestations=True)
                    break
            _ew = node.get("evidential_weight")
            ew_str = f"{_ew:.2f}" if _ew is not None else "N/A"
            claims_html.append(
                f'<div class="claim-card" id="claim-{_esc(claim_id)}" data-claim-id="{_esc(claim_id)}">'
                f'<div class="claim-header">'
                f'<code>{_esc(claim_id)}</code> '
                f'<span class="type">{_esc(node.get("type"))}</span> '
                f'<span class="author" title="{_esc(author)}">{_esc(author)}</span>'
                f'<span class="ew">ew={ew_str}</span>'
                f'{stance_warn}'
                f'{quote_btn}'
                f"</div>"
                f'<p class="claim-content">{_esc(node.get("content", ""))}</p>'
                f"{template}"
                f"{relations_block}"
                f"</div>"
            )

        gaps_html = []
        for gid in crux.get("gap_ids", [])[:6]:
            g = gaps_by_id.get(gid) or graph.get(gid)
            if g:
                review = " " + _badge_review() if g.get("needs_review") else ""
                gaps_html.append(
                    f'<li><code>{_esc(gid)}</code>{review} — {_esc(g.get("content", ""))}</li>'
                )

        chains_block = _crux_details_block(
            "Argument chains",
            "Short links to shared chain definitions (full conditions in Reference → Chains below).",
            f'<div class="chain-refs">{"".join(chains_html) or "<p class=\"empty\">None linked</p>"}</div>',
            len(linked_chains),
        )
        themes_block = _crux_details_block(
            "Shared presuppositions — themes",
            "Short links to theme definitions (full labels in Reference → Themes below).",
            f'<div class="theme-refs">{"".join(themes_html) or "<p class=\"empty\">None linked</p>"}</div>',
            len(themes_html),
        )
        gaps_block = _crux_details_block(
            "Epistemic gaps",
            "What the corpus still does not establish — missing evidence or perspectives.",
            f'<ul class="gaps">{"".join(gaps_html) or "<li>None linked</li>"}</ul>',
            len(gaps_html),
        )

        sections.append(
            f'<section class="crux-section" id="{_esc(cid)}">'
            f'<h2><code>{_esc(cid)}</code></h2>'
            f'<p class="section-note">Crux — a disagreement that, if resolved, would shift the debate. '
            f'The precise question is below (not the short id above).</p>'
            f'<p class="question">{_esc(crux.get("question", ""))}</p>'
            f'<div class="stakes"><strong>What is at stake</strong> '
            f'<p class="section-note-inline">If resolved one way vs the other:</p>'
            f'<p>{_esc(crux.get("stakes", ""))}</p></div>'
            f'<div class="resolution"><strong>How to settle it (resolution protocol)</strong> '
            f'<p>{_esc(crux.get("resolution_path", ""))}</p></div>'
            f"{chains_block}"
            f"{themes_block}"
            f'<h3>Anchor claims ({len(claims_html)})</h3>'
            f'<p class="section-note">Primary claims from the graph. Click <strong>Quotes</strong> to open verbatim sources and debate links in the right panel.</p>'
            f'<div class="claims">{"".join(claims_html)}</div>'
            f"{gaps_block}"
            f"</section>"
        )

    registry_body, registry_nav = _build_chain_theme_registry(index, themes, cruxes)
    nav_items.append(registry_nav)

    stats = index.get("stats", {})
    latent_gems = (source_importance or {}).get("stats", {}).get("latent_gems", 0)
    latent_line = ""
    if latent_gems > 0:
        gem_word = "gem" if latent_gems == 1 else "gems"
        latent_line = f" · {latent_gems} latent {gem_word} detected"
    cross_line = ""
    if cross_stats:
        cross_line = (
            f'<p>Cross-source: {cross_stats.get("proposed", 0)} relations proposed, '
            f'{cross_stats.get("added_to_graph", 0)} in graph · '
            f'Debate nodes: {cross_stats.get("debate_nodes", 0)} '
            f'({cross_stats.get("multi_source", 0)} multi-attestation)</p>'
        )
    hero = (
        f'<div class="hero">'
        f'<h2>Cruxes — open disputes</h2>'
        f'<p>Each section is one crux from <code>compiled/index.json</code>. '
        f'The <strong>question</strong> line is the real title; the <code>crux_id</code> is a short handle.</p>'
        f'<p>{len(cruxes)} cruxes · {len(index.get("chains", []))} chains · '
        f'{stats.get("total", "?")} nodes{latent_line}</p>'
        f'{cross_line}'
        f'<p class="guide-link"><a href="../../docs/dashboard_guide.html" target="_blank">Dashboard guide</a> '
        f'— field definitions for every tab and subsection. '
        f'<strong>Not in this HTML:</strong> health report, full graph, case profile — see '
        f'<code>README.md</code> → <em>If you only open dashboard.html</em>.</p>'
        f'{_tab_legend_html(LEGEND_CRUXES)}'
        f"</div>"
    )
    return hero + "".join(sections) + registry_body, "".join(nav_items), len(cruxes)


def _build_debate_html(
    cross_links: dict | None,
    debate_state: dict | None,
    hypotheses: list[dict],
    index: dict,
    graph: dict,
    debate_by_id: dict[str, dict],
    stance_guard: StanceGuard,
) -> tuple[str, str]:
    claim_clusters = index.get("claim_clusters", [])
    compressed = [c for c in claim_clusters if c.get("member_count", 1) > 1]
    has_structure = bool(cross_links or debate_state or compressed)
    if not has_structure and not hypotheses:
        empty = (
            '<div class="hero"><h2>Debate — cross-paper structure</h2>'
            '<p>No debate data yet. Run: <code>python main.py --case {case} --step relate</code></p></div>'
        )
        return empty, ""

    nav_items = []
    sections = []
    relations = (cross_links or {}).get("relations", [])
    nodes = (debate_state or {}).get("nodes", [])
    crux_by_id = {c["id"]: c for c in index.get("cruxes", [])}

    if hypotheses:
        cards = []
        for hyp in sorted(hypotheses, key=lambda h: h.get("crux_id", "")):
            cid = hyp.get("crux_id", "")
            cards.append(_hypothesis_html(hyp, crux_by_id.get(cid)))
        sections.append(
            f'<section class="debate-section" id="research-hypotheses">'
            f"<h2>Research hypotheses ({len(hypotheses)})</h2>"
            f'<p class="section-note">Proposed studies that would settle each crux. Every number or timeframe '
            f'should be traceable in <strong>Parameter grounding</strong> — items marked '
            f'<span class="badge b-warn">ungrounded</span> are not guaranteed by source papers.</p>'
            f'<div class="hypothesis-cards">{"".join(cards)}</div>'
            f"</section>"
        )
        nav_items.append(
            '<a href="#research-hypotheses" class="nav-item debate-nav">Research hypotheses</a>'
        )

    if compressed:
        cards = [
            _claim_cluster_card(c, graph, debate_by_id, stance_guard)
            for c in sorted(compressed, key=lambda c: (-c.get("member_count", 0), c.get("cluster_id", "")))
        ]
        cross_paper = sum(
            1 for c in compressed
            if len(_cluster_source_keys(graph, c.get("member_ids", []))) > 1
        )
        sections.append(
            f'<section class="debate-section" id="semantic-compression">'
            f"<h2>Semantic compression ({len(compressed)} groups)</h2>"
            f'<p class="section-note">Paraphrases of the same idea grouped at embedding similarity ≥ 0.75 '
            f"(<code>claim_clusters</code> in <code>compiled/index.json</code>). "
            f"Ingest merges only at ≥ 0.85, so variants in the 0.75–0.85 band stay as separate nodes "
            f"but appear here under one <span class='badge b-ok'>canonical</span> wording. "
            f"{cross_paper} groups span multiple sources.</p>"
            f'<div class="cluster-cards">{"".join(cards)}</div>'
            f"</section>"
        )
        nav_items.append(
            '<a href="#semantic-compression" class="nav-item debate-nav">Compression</a>'
        )

    if relations:
        section_html, cross_nav = _build_cross_links_grouped_html(relations)
        sections.append(section_html)
        nav_items.append(cross_nav)

    multi = _multi_source_cards(graph, debate_by_id)
    if multi:
        cards = []
        for n in multi:
            source_lines = []
            for grp in group_attestations_by_source(n.get("attestations", [])):
                if not grp.get("quotes"):
                    continue
                author = grp.get("author") or grp.get("source_id", "")
                n_quotes = len(grp.get("quotes", [])) or grp.get("count", 1)
                suffix = f" — {n_quotes} quotes" if n_quotes > 1 else ""
                source_lines.append(f'<li>{_esc(author)}{suffix}</li>')
            quote_btn, template = _claim_quote_fragments(
                n["claim_id"],
                graph[n["claim_id"]],
                debate_by_id,
                scope="multi-source",
                stance_guard=stance_guard,
            )
            cards.append(
                f'<div class="claim-card" id="claim-{_esc(n["claim_id"])}" '
                f'data-claim-id="{_esc(n["claim_id"])}">'
                f'<div class="claim-header">'
                f'<code>{_esc(n.get("claim_id", ""))}</code> '
                f'<span class="type">{_esc(n.get("type", ""))}</span> '
                f"{quote_btn}"
                f"</div>"
                f"{_epistemic_meta_html(n['claim_id'], graph)}"
                f'<p class="claim-content">{_esc(n.get("canonical", ""))}</p>'
                f"{template}"
                f"<ul class='multi-source-papers'>{''.join(source_lines)}</ul></div>"
            )
        sections.append(
            f'<section class="debate-section" id="multi-source">'
            f"<h2>Cross-paper canonical claims ({len(multi)})</h2>"
            f'<p class="section-note">Nodes merged by <code>--step reconcile</code> — same assertion across '
            f'distinct papers. Badge shows <code>epistemic_status</code>; count is attesting papers '
            f'(plus contradicting papers if contested).</p>'
            f'<div class="claims">{"".join(cards)}</div>'
            f"</section>"
        )
        nav_items.append('<a href="#multi-source" class="nav-item debate-nav">Multi-source</a>')

    contra = [n for n in nodes if n.get("contradicted_by")]
    if contra:
        cards = []
        for n in contra[:30]:
            targets = "".join(
                f'<li><code>{_esc(c.get("claim_id", ""))}</code> — {_esc(c.get("canonical", ""))}</li>'
                for c in n.get("contradicted_by", [])
            )
            cards.append(
                f'<div class="claim-card">'
                f'<p><code>{_esc(n.get("claim_id", ""))}</code> — {_esc(n.get("canonical", ""))}</p>'
                f"<ul>{targets}</ul></div>"
            )
        sections.append(
            f'<section class="debate-section" id="contradictions">'
            f"<h2>Direct contradictions ({len(contra)})</h2>"
            f'<p class="section-note">Claims with explicit <code>contradicted_by</code> edges — papers that disagree on the same point.</p>'
            f'<div class="claims">{"".join(cards)}</div>'
            f"</section>"
        )
        nav_items.append('<a href="#contradictions" class="nav-item debate-nav">Contradictions</a>')

    ds = (debate_state or {}).get("stats", {})
    cl = (cross_links or {}).get("stats", {})
    hero = (
        f'<div class="hero"><h2>Debate — structure &amp; research</h2>'
        f"<p>Cross-paper relations, convergent evidence, contradictions — plus "
        f"<strong>research hypotheses</strong> proposing how to settle open cruxes.</p>"
        f"<p>{len(compressed)} compressed claim groups · "
        f"{len(multi)} cross-paper canonical · "
        f"{cl.get('proposed', 0)} cross-links · "
        f"{ds.get('total_with_structure', 0)} debate nodes · "
        f"{ds.get('with_contradictions', 0)} with contradictions · "
        f"{len(hypotheses)} research hypotheses · "
        f'<a href="../../docs/dashboard_guide.html#tab-debate" target="_blank">guide</a></p>'
        f'{_tab_legend_html(LEGEND_DEBATE, "Orange warning box = hypothesis parameter needs external justification.")}'
        f"</div>"
    )
    return hero + "".join(sections), "".join(nav_items)


def _audit_anchor(source_id: str) -> str:
    safe = source_id.replace("/", "--").replace("\\", "--").replace(":", "-").replace(" ", "-")
    return f"audit-{safe}"


def _nav_group_label(text: str) -> str:
    return f'<div class="nav-group-label">{_esc(text)}</div>'


def _profile_generation_failed(profile: dict) -> bool:
    if not profile:
        return False
    if profile.get("profile_source") == "fallback_template":
        return False
    criteria = profile.get("criteria") or []
    missing = (profile.get("guidelines_missing") or "").lower()
    if not criteria and ("failed" in missing or "profile generation" in missing):
        return True
    return not criteria and not profile.get("profile_source")


def _audit_criterion_ids(audits: list[dict]) -> set[str]:
    ids: set[str] = set()
    for audit in audits:
        if audit.get("parse_error"):
            continue
        for ev in audit.get("evaluations", []):
            cid = ev.get("criterion_id")
            if cid:
                ids.add(cid)
    return ids


def _method_rubric_heading(profile: dict | None, audits: list[dict]) -> tuple[str, str]:
    profile_ids = {c.get("id") for c in (profile or {}).get("criteria", []) if c.get("id")}
    audit_ids = _audit_criterion_ids(audits)

    if profile and _profile_generation_failed(profile):
        return (
            "Case criteria (profile incomplete)",
            "Profile generation failed — criteria table below is empty. "
            "Per-source audits may use ad-hoc criterion IDs until you re-run "
            "<code>python scripts/generate_methodology_profile.py --case …</code>.",
        )
    if not profile_ids and audit_ids:
        return (
            "Per-source criterion IDs (no shared rubric)",
            "The case profile has no criteria; each audit used reviewer-invented "
            "<code>criterion_id</code> values — scores are not directly comparable.",
        )
    if profile_ids and audit_ids and not audit_ids.issubset(profile_ids):
        return (
            "Case criteria + per-source overrides",
            "Some audits reference criterion IDs outside the shared profile — "
            "treat cross-source score comparisons with caution.",
        )
    return (
        "Case criteria (shared rubric)",
        "Domain checklist from <code>methodology/profile.json</code> — "
        "same criteria applied to every source below.",
    )


def _confidence_ceiling_banner(profile: dict) -> str:
    ceiling = profile.get("confidence_ceiling")
    if ceiling is None:
        return ""
    inquiry = profile.get("inquiry_type", "")
    lines = [
        f"<strong>Confidence ceiling = {ceiling}</strong> caps how high any single "
        "source audit score can go after methodological review.",
    ]
    if ceiling == 0.5 and inquiry in ("probabilistic_debate", "mixed"):
        lines.append(
            "For adversarial / probabilistic debate cases this is a <em>domain cap</em> "
            "(priors and likelihoods are rarely fully anchored) — not a claim that "
            "every source scored badly."
        )
    elif profile.get("profile_source") == "fallback_template":
        lines.append(
            "Ceiling comes from the inquiry-type fallback template because LLM profile "
            "generation did not produce a parseable rubric."
        )
    return (
        f'<div class="data-warn-banner">'
        f'<p class="section-note">{" ".join(lines)}</p>'
        f"</div>"
    )


def _build_methodology_html(
    case: str,
    profile: dict | None,
    audits: list[dict],
    source_importance: dict | None = None,
    source_roles: dict[str, dict] | None = None,
) -> tuple[str, str]:
    if not profile and not audits and not (source_importance or {}).get("sources"):
        empty = (
            '<div class="hero"><h2>Methodology Audit</h2>'
            f'<p>No methodology data for <code>{_esc(case)}</code>. '
            "Run: <code>python main.py --case {case} --step methodology</code></p></div>"
        )
        return empty, ""

    nav_items = []
    sections = []

    if profile:
        rubric_title, rubric_note = _method_rubric_heading(profile, audits)
        profile_warn = ""
        if _profile_generation_failed(profile):
            profile_warn = (
                '<div class="data-warn-banner data-warn-fatal">'
                "<strong>Profile generation failed.</strong> "
                f'{_esc(profile.get("guidelines_missing", ""))} — '
                "re-run methodology profile generation before trusting the criteria table."
                "</div>"
            )
        criteria_rows = "".join(
            f"<tr>"
            f"<td><code>{_esc(c.get('id'))}</code></td>"
            f"<td>{_esc(c.get('category'))}</td>"
            f"<td>{_impact_badge(c.get('severity', ''))}</td>"
            f"<td>{_esc(c.get('question', ''))}</td>"
            f"<td class='rationale'>{_esc(c.get('expert_rationale', ''))}</td>"
            f"</tr>"
            for c in profile.get("criteria", [])
        )
        red_flags = "".join(
            f"<li>{_esc(rf)}</li>" for rf in profile.get("red_flags", [])
        )
        sections.append(
            f'<section class="method-profile" id="method-profile">'
            f"<h2>{_esc(rubric_title)}</h2>"
            f'<p class="section-note">{_esc(rubric_note)}</p>'
            f"{profile_warn}"
            f"{_confidence_ceiling_banner(profile)}"
            f'<p class="domain">{_esc(profile.get("domain_summary", ""))}</p>'
            f'<div class="meta-grid">'
            f'<div><strong>Inquiry type</strong><br><code>{_esc(profile.get("inquiry_type"))}</code></div>'
            f'<div><strong>Standardization</strong><br>{_esc(profile.get("standardization_level"))}</div>'
            f'<div><strong>Confidence ceiling</strong><br>{profile.get("confidence_ceiling", "?")}'
            f' <span class="muted">(domain cap)</span></div>'
            f'<div><strong>Guidelines</strong><br>{_esc(", ".join(profile.get("applicable_guidelines", [])) or "none")}</div>'
            f"</div>"
            f'<p class="guidelines-missing"><strong>Missing standards:</strong> '
            f'{_esc(profile.get("guidelines_missing", ""))}</p>'
            f"<h3>Audit criteria ({len(profile.get('criteria', []))})</h3>"
            f'<p class="section-note">Each row: <code>criterion_id</code>, severity, question the reviewer asks, rationale.</p>'
            f'<table class="criteria-table"><thead><tr>'
            f"<th>ID</th><th>Category</th><th>Severity</th><th>Question</th><th>Rationale</th>"
            f"</tr></thead><tbody>{criteria_rows}</tbody></table>"
            f"<h3>Red flag patterns</h3>"
            f'<p class="section-note">Recurring methodological warning signs to watch for across papers.</p>'
            f"<ul class='red-flags'>{red_flags or '<li>None</li>'}</ul>"
            f"</section>"
        )
        nav_items.append(_nav_group_label("Case criteria"))
        nav_items.append(
            '<a href="#method-profile" class="nav-item method-nav">All sources — criteria</a>'
        )

    if audits:
        nav_items.append(_nav_group_label("Per-source audit"))

    for i, audit in enumerate(audits):
        sid = audit.get("source_id", f"source_{i}")
        title = audit.get("source_label", sid)
        aid = _audit_anchor(sid)
        failed = bool(audit.get("parse_error"))
        nav_items.append(
            f'<a href="#{aid}" class="nav-item method-nav source-nav">'
            f'<span class="source-prefix">Source:</span> {_esc(title)}'
            + (" " + _badge("failed", "b-danger") if failed else "")
            + f"</a>"
        )

        if failed:
            sections.append(
                f'<details class="audit-details audit-failed" id="{aid}">'
                f'<summary class="audit-summary">'
                f'<span class="source-prefix">Source:</span> {_esc(title)} '
                f'— {_badge("audit failed", "b-danger")}'
                f"</summary>"
                f'<div class="audit-details-body">'
                f'<div class="data-warn-banner data-warn-fatal">'
                f"<strong>LLM audit did not complete.</strong> "
                f'{_esc(audit.get("score_rationale", "parse_error"))} — '
                "this source is excluded from methodology score comparisons."
                f"</div>"
                f"</div>"
                f"</details>"
            )
            continue

        eval_rows = "".join(
            f"<tr>"
            f"<td><code>{_esc(e.get('criterion_id'))}</code></td>"
            f"<td>{_status_badge(e.get('status', ''))}</td>"
            f"<td>{_esc(e.get('reviewer_note', ''))}</td>"
            f"<td>"
            + (
                f'<blockquote class="quote">"{_esc(e.get("evidence_quote"))}"</blockquote>'
                if e.get("evidence_quote")
                else "—"
            )
            + f"</td></tr>"
            for e in audit.get("evaluations", [])
        )

        rf_rows = "".join(
            f"<li><strong>{_esc(h.get('pattern'))}</strong> — {_esc(h.get('reviewer_note', ''))}"
            + (
                f'<blockquote class="quote">"{_esc(h.get("evidence_quote"))}"</blockquote>'
                if h.get("evidence_quote")
                else ""
            )
            + "</li>"
            for h in audit.get("red_flag_hits", [])
        )

        bd = audit.get("score_breakdown", {})
        breakdown = ""
        if bd:
            breakdown = (
                f'<p class="score-breakdown">'
                f"Applicable: {bd.get('declared', '?')}/{bd.get('applicable', '?')} declared · "
                f"{bd.get('not_declared', 0)} not_declared · "
                f"{bd.get('red_flag_criteria', 0)} red_flags · "
                f"{bd.get('red_flag_hits', 0)} pattern hits · "
                f"ceiling {bd.get('confidence_ceiling', '?')}"
                f"</p>"
            )

        score = audit.get("methodology_score", 0)
        sections.append(
            f'<details class="audit-details" id="{aid}">'
            f'<summary class="audit-summary">'
            f'<span class="source-prefix">Source:</span> {_esc(title)} '
            f'— score <strong>{score:.3f}</strong>'
            f"</summary>"
            f'<div class="audit-details-body">'
            f'<p class="section-note">Per-paper score against the case criteria. Lower score = more methodological gaps.</p>'
            f'<p class="score">Methodology score: <strong>{score:.3f}</strong> '
            f"— {_esc(audit.get('score_rationale', ''))}</p>"
            f"{breakdown}"
            f"<h3>Evaluations</h3>"
            f'<table class="eval-table"><thead><tr>'
            f"<th>Criterion</th><th>Status</th><th>Reviewer note</th><th>Evidence</th>"
            f"</tr></thead><tbody>{eval_rows or '<tr><td colspan=4>No evaluations</td></tr>'}</tbody></table>"
            f"<h3>Red flag hits</h3>"
            f"<ul>{rf_rows or '<li>None</li>'}</ul>"
            f"</div>"
            f"</details>"
        )

    imp_section, imp_nav = _build_source_importance_section(
        source_importance, audits, source_roles or {}
    )
    if imp_section:
        sections.append(imp_section)
        nav_items.append(_nav_group_label("Source importance"))
        nav_items.append(imp_nav)

    latent_gems = (source_importance or {}).get("stats", {}).get("latent_gems", 0)
    latent_hero = ""
    if latent_gems > 0:
        latent_hero = f" · {latent_gems} latent gem{'s' if latent_gems != 1 else ''} detected"

    n_imp_sources = len((source_importance or {}).get("sources", []))
    profiles_line = f" · {n_imp_sources} source profiles" if n_imp_sources else ""

    hero = (
        f'<div class="hero">'
        f"<h2>Methodology — paper audits</h2>"
        f"<p>Three layers: <strong>case criteria</strong>, <strong>per-source audits</strong>, "
        f"and <strong>source importance</strong> (epistemic footprint from the graph). "
        f'<a href="../../docs/dashboard_guide.html#tab-methodology" target="_blank">guide</a></p>'
        f"<p>{len(audits)} audits{profiles_line}{latent_hero}</p>"
        f'{_tab_legend_html(LEGEND_METHODOLOGY)}'
        f"</div>"
    )
    return hero + "".join(sections), "".join(nav_items)


def _vuln_badge(level: str) -> str:
    return _badge_vulnerability(level)


def _leverage_badge(level: str) -> str:
    return _badge_leverage(level)


def _qtype_badge(qtype: str) -> str:
    return _badge_qtype(qtype)


def _md_briefing_to_html(text: str) -> str:
    blocks = []
    for para in text.replace("\r\n", "\n").split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if para.startswith("## "):
            blocks.append(f'<h3 class="briefing-h3">{_esc(para[3:].strip())}</h3>')
        elif para.startswith("# "):
            blocks.append(f'<h2 class="briefing-h2">{_esc(para[2:].strip())}</h2>')
        else:
            lines = [_esc(ln.strip()) for ln in para.split("\n") if ln.strip()]
            blocks.append(f'<p class="briefing-p">{" ".join(lines)}</p>')
    return "".join(blocks) or f'<p class="briefing-p">{_esc(text)}</p>'


def _load_reasoning(case: str) -> dict:
    out: dict = {
        "briefing": None,
        "presuppositions": [],
        "questions": [],
        "devils": [],
    }
    bp = field_briefing_path(case)
    if bp.exists():
        try:
            out["briefing"] = bp.read_text(encoding="utf-8")
        except OSError:
            pass
    pp = presuppositions_path(case)
    if pp.exists():
        try:
            data = json.loads(pp.read_text(encoding="utf-8"))
            out["presuppositions"] = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            pass
    qp = open_questions_path(case)
    if qp.exists():
        try:
            data = json.loads(qp.read_text(encoding="utf-8"))
            out["questions"] = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            pass
    ddir = devils_advocate_dir(case)
    if ddir.exists():
        for path in sorted(ddir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                item.setdefault("claim_id", path.stem)
                out["devils"].append(item)
            except (json.JSONDecodeError, OSError):
                continue
    return out


def _build_reasoning_html(case: str, graph: dict[str, dict]) -> tuple[str, str]:
    data = _load_reasoning(case)
    if not any([data["briefing"], data["presuppositions"], data["questions"], data["devils"]]):
        empty = (
            '<div class="hero"><h2>Expert Reasoning</h2>'
            f'<p>No reasoning data for <code>{_esc(case)}</code>. '
            'Run: <code>python main.py --case {case} --step reasoning</code></p></div>'
        )
        return empty, ""

    nav_items = []
    sections = []

    if data["briefing"]:
        nav_items.append('<a href="#reasoning-briefing" class="nav-item reasoning-nav">Field briefing</a>')
        sections.append(
            f'<section class="reasoning-section" id="reasoning-briefing">'
            f"<h2>Field briefing</h2>"
            f'<p class="section-note">~1-page expert synthesis from <code>reasoning/field_briefing.md</code> — '
            f'state of knowledge and tensions, not a literature summary.</p>'
            f'<div class="briefing-content">{_md_briefing_to_html(data["briefing"])}</div>'
            f"</section>"
        )

    if data["presuppositions"]:
        nav_items.append(
            f'<a href="#reasoning-presups" class="nav-item reasoning-nav">'
            f'Presuppositions ({len(data["presuppositions"])})</a>'
        )
        cards = []
        for i, p in enumerate(data["presuppositions"]):
            claims = "".join(
                f'<code>{_esc(c)}</code> ' for c in p.get("implicated_claims", [])[:8]
            )
            cards.append(
                f'<div class="presup-card">'
                f'<div class="card-header">'
                f'{_vuln_badge(p.get("vulnerability", ""))} '
                f'<span class="presup-title">{_esc(p.get("presupposition", ""))}</span>'
                f"</div>"
                f'<p><strong>If false:</strong> {_esc(p.get("what_breaks_if_false", ""))}</p>'
                f'<p class="meta-line"><strong>Why vulnerable:</strong> '
                f'{_esc(p.get("vulnerability_rationale", ""))}</p>'
                f'<p><strong>How to test:</strong> {_esc(p.get("how_to_test", ""))}</p>'
                f'<p class="implicated">Claims: {claims or "—"}</p>'
                f"</div>"
            )
        sections.append(
            f'<section class="reasoning-section" id="reasoning-presups">'
            f"<h2>Unstated presuppositions ({len(data['presuppositions'])})</h2>"
            f'<p class="section-note">Assumptions the papers treat as obvious — plus how to test and what breaks if false.</p>'
            f'<div class="reasoning-cards">{"".join(cards)}</div>'
            f"</section>"
        )

    if data["questions"]:
        nav_items.append(
            f'<a href="#reasoning-questions" class="nav-item reasoning-nav">'
            f'Open questions ({len(data["questions"])})</a>'
        )
        cards = []
        for q in data["questions"]:
            claims = "".join(
                f'<code>{_esc(c)}</code> ' for c in q.get("implicated_claims", [])[:6]
            )
            cards.append(
                f'<div class="question-card">'
                f'<div class="card-header">'
                f'{_qtype_badge(q.get("type", ""))} '
                f'{_leverage_badge(q.get("epistemic_leverage", ""))}'
                f"</div>"
                f'<p class="question-main">{_esc(q.get("question", ""))}</p>'
                f'<p><strong>Why it matters:</strong> {_esc(q.get("why_it_matters", ""))}</p>'
                f'<p class="meta-line"><strong>Blocked by:</strong> '
                f'{_esc(q.get("what_currently_blocks_the_answer", ""))}</p>'
                f'<p><strong>Approach:</strong> {_esc(q.get("suggested_approach", ""))}</p>'
                f'<p class="implicated">Claims: {claims or "—"}</p>'
                f"</div>"
            )
        sections.append(
            f'<section class="reasoning-section" id="reasoning-questions">'
            f"<h2>Open research questions ({len(data['questions'])})</h2>"
            f'<p class="section-note">Prioritized questions the corpus does not answer — with leverage and what blocks an answer.</p>'
            f'<div class="reasoning-cards">{"".join(cards)}</div>'
            f"</section>"
        )

    if data["devils"]:
        nav_items.append(_nav_group_label("Devil's advocate"))
        devil_sections = []
        for d in data["devils"]:
            cid = d.get("claim_id", "")
            nav_items.append(
                f'<a href="#devil-{_esc(cid)}" class="nav-item reasoning-nav">'
                f'<code>{_esc(cid)}</code></a>'
            )
            node = graph.get(cid, {})
            claim_text = node.get("content", "") if node else ""
            conf = d.get("revised_confidence", "")
            devil_sections.append(
                f'<div class="devil-card" id="devil-{_esc(cid)}">'
                f'<h4><code>{_esc(cid)}</code> '
                f'{_badge(conf, "b-warn" if "low" in (conf or "").lower() else "b-ok")}</h4>'
                f'<p class="claim-content">{_esc(claim_text)}</p>'
                f'<p><strong>Strongest counter:</strong> '
                f'{_esc(d.get("strongest_counterargument", ""))}</p>'
                f'<p class="meta-line"><strong>Type:</strong> '
                f'{_esc(d.get("counterargument_type", ""))}</p>'
                f'<p><strong>Would refute counter:</strong> '
                f'{_esc(d.get("what_would_refute_this_counterargument", ""))}</p>'
                f'<p class="nobody-says"><strong>Nobody is saying:</strong> '
                f'{_esc(d.get("the_thing_nobody_is_saying", ""))}</p>'
                f'<p class="meta-line">{_esc(d.get("revised_confidence_rationale", ""))}</p>'
                f"</div>"
            )
        sections.append(
            f'<section class="reasoning-section" id="reasoning-devils">'
            f"<h2>Devil's advocate ({len(data['devils'])})</h2>"
            f'<p class="section-note">Strongest skeptical case per multi-source claim — counterargument, what would refute it, revised confidence.</p>'
            f'<div class="reasoning-cards">{"".join(devil_sections)}</div>'
            f"</section>"
        )

    hero = (
        f'<div class="hero"><h2>Reasoning — expert layer</h2>'
        f"<p>Synthesis beyond the compiled index: presuppositions, open questions, skeptical review. "
        f"Distinct from the long <code>reports/{{case}}_report.md</code> (assess step).</p>"
        f"<p>{len(data['presuppositions'])} presuppositions · "
        f"{len(data['questions'])} open questions · "
        f"{len(data['devils'])} devil's advocate reviews"
        f'{"" if data["briefing"] else " · no briefing"} · '
        f'<a href="../../docs/dashboard_guide.html#tab-reasoning" target="_blank">guide</a></p>'
        f'{_tab_legend_html(LEGEND_REASONING)}'
        f"</div>"
    )
    return hero + "".join(sections), "".join(nav_items)


def build_dashboard(case: str, output: Path | None = None) -> Path:
    raw_index = load_compiled(case)
    if raw_index is None:
        raise FileNotFoundError(f"No compiled index for '{case}' — run crystallize first")

    index = repair_compiled_index(raw_index)
    store = GraphStore(case)
    graph = {n["id"]: n for n in store.get_all_nodes()}
    themes = _themes_by_id(index)
    gaps_by_id = {g["id"]: g for g in index.get("gaps", [])}

    debate_state = load_debate_state(case)
    debate_by_id = {n["claim_id"]: n for n in (debate_state or {}).get("nodes", [])}
    cross_links = load_cross_links(case)
    cross_stats = None
    if cross_links or debate_state:
        cross_stats = {
            **(cross_links or {}).get("stats", {}),
            "debate_nodes": (debate_state or {}).get("stats", {}).get("total_with_structure", 0),
            "multi_source": (debate_state or {}).get("stats", {}).get("multi_source", 0),
        }
    hypotheses = load_all_hypotheses(case)

    if output is None:
        output = CASES_DIR / case / "dashboard.html"

    profile = load_methodology_profile(case)
    audits = _load_audits(case)
    source_importance = load_source_importance(case)
    source_roles = _load_source_roles(case)
    method_body, method_nav = _build_methodology_html(
        case, profile, audits, source_importance, source_roles
    )
    has_method = bool(
        profile or audits or (source_importance or {}).get("sources")
    )

    stance_guard = load_stance_guard(case)

    debate_body, debate_nav = _build_debate_html(
        cross_links, debate_state, hypotheses, index, graph, debate_by_id, stance_guard
    )
    has_compression = any(c.get("member_count", 1) > 1 for c in index.get("claim_clusters", []))
    has_debate = bool(cross_links or debate_state or hypotheses or has_compression)

    reasoning_body, reasoning_nav = _build_reasoning_html(case, graph)
    reasoning_data = _load_reasoning(case)
    has_reasoning = bool(
        reasoning_data["briefing"]
        or reasoning_data["presuppositions"]
        or reasoning_data["questions"]
        or reasoning_data["devils"]
    )

    crux_body, crux_nav, n_cruxes = _build_cruxes_html(
        case,
        index,
        graph,
        themes,
        gaps_by_id,
        debate_by_id,
        cross_stats,
        source_importance,
        stance_guard,
    )

    n_profiles = len((source_importance or {}).get("sources", []))
    method_subtitle = f"{len(audits)} audits"
    if n_profiles:
        method_subtitle += f" · {n_profiles} profiles"


    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EPISTEME — {_esc(case)}</title>
  <style>
    :root {{
      --bg: #0f1419; --surface: #1a2332; --border: #2d3a4d;
      --text: #e7ecf3; --muted: #8b9cb3; --accent: #5b9fd4;
      --fatal: #e74c3c; --major: #e67e22; --review: #9b59b6; --ok: #27ae60;
      --quote-bg: #121a24;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }}
    .layout {{ display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }}
    .layout.quote-open {{ grid-template-columns: 280px minmax(0, 1fr) 360px; }}
    nav {{ background: var(--surface); border-right: 1px solid var(--border); padding: 1.5rem 1rem; position: sticky; top: 0; height: 100vh; overflow-y: auto; }}
    nav h1 {{ font-size: 1rem; margin: 0 0 0.5rem; color: var(--accent); }}
    nav .subtitle {{ font-size: 0.75rem; color: var(--muted); margin-bottom: 1rem; }}
    .tab-bar {{ display: flex; gap: 0.25rem; margin-bottom: 1rem; }}
    .tab-btn {{ flex: 1; padding: 0.5rem; border: 1px solid var(--border); background: var(--bg); color: var(--muted); border-radius: 6px; cursor: pointer; font-size: 0.8rem; }}
    .tab-btn.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
    .nav-panel {{ display: none; }}
    .nav-panel.active {{ display: block; }}
    .nav-item {{ display: block; padding: 0.5rem 0.75rem; margin: 0.25rem 0; color: var(--text); text-decoration: none; border-radius: 6px; font-size: 0.85rem; }}
    .nav-group-label {{
      font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--muted); margin: 1rem 0 0.35rem;
      padding: 0.25rem 0.5rem; border-top: 1px solid var(--border);
    }}
    .nav-panel .nav-group-label:first-child {{ border-top: none; margin-top: 0; }}
    .source-nav {{ font-size: 0.8rem; line-height: 1.35; }}
    .source-prefix {{ color: var(--accent); font-weight: 600; }}
    .section-note {{ color: var(--muted); font-size: 0.9rem; margin: 0.25rem 0 1rem; }}
    .section-note-inline {{ color: var(--muted); font-size: 0.85rem; margin: 0 0 0.35rem; }}
    .guide-link a, nav .guide-nav {{ color: var(--accent); font-size: 0.85rem; }}
    .badge-legend {{ margin: 0.75rem 0 0; padding: 0.6rem 0.75rem; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; font-size: 0.72rem; }}
    .hero .tab-legend {{ margin-top: 1rem; }}
    .badge-legend summary {{ cursor: pointer; font-weight: 600; color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; }}
    .badge-legend ul {{ list-style: none; margin: 0.5rem 0 0; padding: 0; }}
    .badge-legend li {{ display: flex; gap: 0.4rem; align-items: flex-start; margin: 0.35rem 0; line-height: 1.35; }}
    .badge-legend .badge {{ flex-shrink: 0; font-size: 0.65rem; margin-top: 0.1rem; }}
    .legend-desc {{ color: var(--muted); }}
    .legend-note {{ margin: 0.5rem 0 0; color: var(--muted); font-size: 0.68rem; line-height: 1.4; }}
    main {{ padding: 2rem 2.5rem; max-width: 960px; min-width: 0; }}
    aside.quote-panel {{
      display: none; background: var(--surface); border-left: 1px solid var(--border);
      position: sticky; top: 0; height: 100vh; overflow-y: auto; padding: 1.25rem 1rem;
    }}
    .layout.quote-open aside.quote-panel {{ display: block; }}
    .quote-panel-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }}
    .quote-panel-header h3 {{ margin: 0; font-size: 0.95rem; color: var(--accent); }}
    .quote-panel-close {{
      background: transparent; border: 1px solid var(--border); color: var(--muted);
      border-radius: 6px; cursor: pointer; padding: 0.2rem 0.55rem; font-size: 1rem; line-height: 1;
    }}
    .quote-panel-hint {{ color: var(--muted); font-size: 0.85rem; margin: 0; }}
    .quote-panel-claim-id {{ margin: 0 0 0.5rem; font-size: 0.85rem; }}
    .quote-panel-claim-text {{ margin: 0 0 0.75rem; font-weight: 500; line-height: 1.5; }}
    .quote-panel-meta {{ margin: 0 0 1rem; font-size: 0.8rem; color: var(--muted); }}
    .quote-btn {{
      margin-left: auto; padding: 0.25rem 0.6rem; border-radius: 6px; border: 1px solid var(--accent);
      background: transparent; color: var(--accent); cursor: pointer; font-size: 0.75rem;
    }}
    .quote-btn:hover, .claim-card.quote-active .quote-btn {{
      background: var(--accent); color: #fff;
    }}
    .claim-card.quote-active {{ border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }}
    .quote-panel-claim .debate-edges {{ margin-top: 1rem; }}
    .view-panel {{ display: none; }}
    .view-panel.active {{ display: block; }}
    .hero {{ margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); }}
    .hero h2 {{ margin: 0 0 0.5rem; font-size: 1.5rem; }}
    .hero p {{ color: var(--muted); margin: 0.25rem 0; font-size: 0.9rem; }}
    .crux-section, .audit-details, .method-profile {{ margin-bottom: 3rem; padding-bottom: 2rem; border-bottom: 1px solid var(--border); }}
    .crux-section h2, .audit-summary {{ color: var(--accent); text-transform: capitalize; margin-top: 0; }}
    .crux-collapsible {{
      margin: 1rem 0; background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 0.5rem 0.75rem;
    }}
    .crux-collapsible summary {{ cursor: pointer; font-size: 0.95rem; list-style: none; }}
    .crux-collapsible summary::-webkit-details-marker {{ display: none; }}
    .crux-collapsible-body {{ padding: 0.75rem 0 0.25rem; }}
    .crux-collapsible-body h3 {{ margin-top: 0; }}
    .audit-details {{
      margin: 1rem 0; background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 0.5rem 0.75rem;
    }}
    .audit-details summary {{ cursor: pointer; font-size: 0.95rem; list-style: none; }}
    .audit-details summary::-webkit-details-marker {{ display: none; }}
    .audit-details-body {{ padding: 0.75rem 0 0.25rem; }}
    .audit-summary {{ font-weight: 600; }}
    .data-warn-banner {{
      background: rgba(255, 180, 0, 0.1); border: 1px solid var(--major);
      border-radius: 8px; padding: 0.75rem 1rem; margin: 1rem 0; font-size: 0.88rem;
    }}
    .data-warn-banner.data-warn-fatal {{
      background: rgba(220, 60, 60, 0.08); border-color: var(--fatal);
    }}
    .audit-failed {{ border-color: var(--fatal); }}
    .question {{ font-size: 1.1rem; font-weight: 500; }}
    .stakes, .resolution {{ margin: 1rem 0; padding: 1rem; background: var(--surface); border-radius: 8px; border-left: 3px solid var(--accent); }}
    .resolution {{ border-left-color: #27ae60; }}
    h3 {{ font-size: 0.95rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 1.5rem; }}
    .badge {{ font-size: 0.7rem; padding: 0.15rem 0.4rem; border-radius: 4px; font-weight: 600; }}
    .badge.b-danger, .badge.fatal, .badge.status-fatal {{ background: var(--fatal); color: #fff; }}
    .badge.b-warn, .badge.major, .badge.status-warn, .badge.review, .badge.minor {{ background: var(--major); color: #fff; }}
    .badge.b-ok, .badge.ok, .badge.status-ok {{ background: var(--ok); color: #fff; }}
    .badge.b-muted, .badge.muted, .badge.status-muted {{ background: var(--border); color: var(--muted); }}
    .badge.b-info, .badge.rel-type {{ background: var(--accent); color: #fff; }}
    .badge.b-gem {{ border: 1px solid var(--major); }}
    .nav-subfield {{ font-size: 0.78rem; padding-left: 1.25rem; color: var(--muted); }}
    .cross-subfield-group {{ margin: 1rem 0; border: 1px solid var(--border); border-radius: 8px; padding: 0.5rem 0.75rem; background: var(--surface); }}
    .cross-subfield-group summary {{ cursor: pointer; padding: 0.35rem 0; }}
    .cross-subfield-group .cross-table {{ margin-top: 0.5rem; }}
    .cross-links-grouped {{ margin-top: 0.5rem; }}
    .chain-card, .theme-card, .claim-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin: 0.75rem 0; }}
    .chain-ref, .theme-ref {{
      padding: 0.6rem 0.75rem; margin: 0.35rem 0;
      background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    }}
    .chain-ref a, .theme-ref a {{ color: var(--accent); text-decoration: none; }}
    .chain-ref a:hover, .theme-ref a:hover {{ text-decoration: underline; }}
    .conclusion-oneline {{ margin: 0.35rem 0 0; color: var(--muted); font-size: 0.88rem; line-height: 1.4; }}
    .registry-section {{ margin-top: 3rem; padding-top: 2rem; border-top: 2px solid var(--border); }}
    .registry-section h2 {{ color: var(--accent); font-size: 1.15rem; }}
    .registry-card {{ scroll-margin-top: 1rem; }}
    .registry-cruxes {{ font-size: 0.85rem; color: var(--muted); margin: 0.75rem 0 0; }}
    .registry-cruxes a {{ color: var(--accent); }}
    .chain-narrative {{ margin-top: 0.75rem; font-size: 0.9rem; color: var(--muted); }}
    .chain-narrative summary {{ cursor: pointer; color: var(--text); }}
    .conclusion {{ font-style: italic; color: var(--muted); }}
    .conditions {{ margin: 0.5rem 0 0; padding-left: 1.25rem; font-size: 0.9rem; }}
    .conditions li {{ margin: 0.35rem 0; word-break: break-word; }}
    .claim-header {{ display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 0.5rem; font-size: 0.85rem; }}
    .type, .author, .ew {{ color: var(--muted); }}
    .claim-content {{ margin: 0.5rem 0; line-height: 1.55; }}
    .author {{ max-width: 14rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    blockquote.quote {{ margin: 0.5rem 0 0; padding: 0.75rem 1rem; background: var(--quote-bg); border-left: 3px solid #27ae60; font-size: 0.9rem; color: #b8d4c8; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }}
    .no-quote {{ font-size: 0.8rem; color: var(--major); margin: 0.5rem 0 0; }}
    .no-quote-inline {{ font-size: 0.8rem; color: var(--major); }}
    .attestation-count {{ font-size: 0.8rem; color: var(--muted); margin: 0.5rem 0 0.25rem; }}
    .attestation {{ margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid var(--border); }}
    .attestation:first-of-type {{ border-top: none; padding-top: 0; }}
    .attestation-header {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 0.25rem; }}
    .gaps {{ font-size: 0.9rem; color: var(--muted); }}
    .debate-edges {{ margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px dashed var(--border); font-size: 0.85rem; }}
    .rel-group {{ margin: 0.5rem 0; }}
    .rel-group ul {{ margin: 0.25rem 0; padding-left: 1.25rem; }}
    .rel-meta {{ color: var(--muted); font-size: 0.8rem; }}
    .rel-rationale {{ color: var(--muted); font-style: italic; list-style: none; margin-left: -1rem; }}
    .hypothesis-card {{ background: var(--surface); border: 1px solid var(--accent); border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
    .hypothesis-cards {{ display: flex; flex-direction: column; gap: 0.75rem; }}
    .hypothesis-crux {{ font-size: 0.88rem; margin: 0 0 0.75rem; padding: 0.5rem 0.75rem; background: var(--bg); border-radius: 6px; border-left: 3px solid var(--accent); }}
    .hypothesis-crux a {{ color: var(--accent); text-decoration: none; }}
    .hypothesis-crux a:hover {{ text-decoration: underline; }}
    .hypothesis-crux-q {{ color: var(--muted); }}
    .hypothesis-warning {{ background: rgba(230, 126, 34, 0.12); border: 1px solid var(--major); border-radius: 8px; padding: 0.75rem 1rem; margin: 0.75rem 0; font-size: 0.88rem; }}
    .hypothesis-warning ul {{ margin: 0.5rem 0 0; padding-left: 1.25rem; }}
    .hypothesis-params-table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; margin: 0.5rem 0 1rem; }}
    .hypothesis-params-table th, .hypothesis-params-table td {{ padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
    .hypothesis-main {{ font-weight: 500; }}
    .study-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin: 0.75rem 0; font-size: 0.9rem; }}
    .debate-section {{ margin-bottom: 3rem; padding-bottom: 2rem; border-bottom: 1px solid var(--border); }}
    .cluster-cards {{ display: flex; flex-direction: column; gap: 1.5rem; }}
    .cluster-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.25rem; }}
    .cluster-header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; margin-bottom: 0.75rem; }}
    .cluster-header h3 {{ margin: 0; font-size: 1rem; }}
    .cluster-header .meta {{ color: var(--muted); font-size: 0.85rem; }}
    .cluster-canonical {{ margin-bottom: 0.75rem; }}
    .cluster-variants {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.75rem; }}
    .cluster-variant {{ margin: 0; padding: 0; }}
    .cluster-variant .claim-card {{ margin: 0; }}
    .epistemic-meta {{ margin: 0.35rem 0 0.5rem; font-size: 0.85rem; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
    .epistemic-meta .meta {{ color: var(--muted); }}
    .multi-source-papers {{ margin: 0.5rem 0 0; padding-left: 1.25rem; font-size: 0.9rem; }}
    .cross-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
    .cross-table th, .cross-table td {{ padding: 0.4rem; border-bottom: 1px solid var(--border); vertical-align: top; word-break: break-word; }}
    code {{ background: var(--bg); padding: 0.1rem 0.35rem; border-radius: 3px; font-size: 0.85em; }}
    .missing {{ opacity: 0.6; }}
    .meta-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; margin: 1rem 0; }}
    .meta-grid div {{ background: var(--surface); padding: 0.75rem; border-radius: 6px; font-size: 0.9rem; }}
    .criteria-table, .eval-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; margin: 1rem 0; }}
    .criteria-table th, .eval-table th {{ text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--border); color: var(--muted); }}
    .criteria-table td, .eval-table td {{ padding: 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; word-break: break-word; }}
    .rationale {{ color: var(--muted); max-width: 280px; }}
    .score {{ font-size: 1rem; }}
    .score-breakdown {{ font-size: 0.85rem; color: var(--muted); margin-top: 0.25rem; }}
    .domain {{ font-size: 1rem; color: var(--text); }}
    .reasoning-section {{ margin-bottom: 3rem; padding-bottom: 2rem; border-bottom: 1px solid var(--border); }}
    .reasoning-section h2 {{ color: var(--accent); margin-top: 0; }}
    .briefing-content {{ font-size: 0.95rem; line-height: 1.65; }}
    .briefing-h2 {{ color: var(--accent); font-size: 1.25rem; margin: 1.5rem 0 0.75rem; }}
    .briefing-h3 {{ color: var(--text); font-size: 1.05rem; margin: 1.25rem 0 0.5rem; border-left: 3px solid var(--accent); padding-left: 0.75rem; }}
    .briefing-p {{ margin: 0.75rem 0; color: var(--text); }}
    .reasoning-cards {{ display: flex; flex-direction: column; gap: 0.75rem; }}
    .presup-card {{ background: var(--surface); border: 1px solid var(--major); border-radius: 8px; padding: 1rem; }}
    .question-card {{ background: var(--surface); border: 1px solid var(--accent); border-radius: 8px; padding: 1rem; }}
    .devil-card {{ background: var(--surface); border: 1px solid var(--fatal); border-radius: 8px; padding: 1rem; margin: 0.75rem 0; }}
    .devil-card h4 {{ margin: 0 0 0.5rem; }}
    .card-header {{ margin-bottom: 0.5rem; display: flex; flex-wrap: wrap; gap: 0.35rem; align-items: center; }}
    .presup-title, .question-main {{ font-weight: 500; }}
    .meta-line {{ font-size: 0.9rem; color: var(--muted); }}
    .implicated {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.5rem; }}
    .source-importance-section {{ margin-top: 2.5rem; padding-top: 2rem; border-top: 1px solid var(--border); }}
    .source-importance-cards {{ display: flex; flex-direction: column; gap: 0.75rem; }}
    .source-importance-card {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
      padding: 0.5rem 0.75rem;
    }}
    .source-importance-card.tier-latent_gem {{
      border-color: var(--major); background: rgba(230,126,34,0.08);
    }}
    .source-importance-card.tier-peripheral {{ opacity: 0.82; }}
    .source-importance-card summary {{ cursor: pointer; font-size: 0.95rem; list-style: none; }}
    .source-importance-card summary::-webkit-details-marker {{ display: none; }}
    .source-importance-body {{ padding: 0.75rem 0 0.25rem; }}
    .source-profile-dl {{
      display: grid; grid-template-columns: 9rem 1fr; gap: 0.35rem 1rem;
      margin: 0; font-size: 0.88rem;
    }}
    .source-profile-dl dt {{ color: var(--muted); margin: 0; }}
    .source-profile-dl dd {{ margin: 0; }}
    .latent-gem-callout {{
      margin-top: 0.75rem; padding: 0.75rem 1rem;
      background: rgba(230,126,34,0.12); border-left: 4px solid var(--major);
      border-radius: 4px; font-size: 0.88rem;
    }}
    .latent-gem-title {{ margin: 0 0 0.35rem; font-weight: 700; color: #f5b041; }}
    .latent-gem-warn {{ margin: 0.5rem 0 0; font-weight: 600; color: var(--major); }}
    .nobody-says {{ background: var(--quote-bg); padding: 0.75rem; border-left: 3px solid var(--fatal); margin: 0.75rem 0; font-size: 0.9rem; }}
    @media (max-width: 768px) {{
      .layout, .layout.quote-open {{ grid-template-columns: 1fr; }}
      nav {{ position: relative; height: auto; }}
      aside.quote-panel {{ position: fixed; inset: 0; z-index: 20; height: 100vh; }}
      .meta-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <nav>
      <h1>ARGOS</h1>
      <div class="subtitle">{_esc(case)}</div>
      <p class="guide-nav"><a href="../../docs/dashboard_guide.html" target="_blank">Reader guide</a> — what each tab means</p>
      <div class="tab-bar">
        <button class="tab-btn active" data-tab="cruxes" title="Open disputes (crystallize)">Cruxes</button>
        {"<button class='tab-btn' data-tab='debate' title='Cross-paper relations and research hypotheses'>Debate</button>" if has_debate else ""}
        {"<button class='tab-btn' data-tab='reasoning' title='Expert synthesis layer'>Reasoning</button>" if has_reasoning else ""}
        {"<button class='tab-btn' data-tab='methodology' title='Per-paper methodology audit'>Methodology</button>" if has_method else ""}
      </div>
      <div id="nav-cruxes" class="nav-panel active">
        <div class="subtitle">{n_cruxes} cruxes</div>
        {crux_nav}
      </div>
      {"<div id='nav-debate' class='nav-panel'>" + debate_nav + "</div>" if has_debate else ""}
      {"<div id='nav-reasoning' class='nav-panel'>" + reasoning_nav + "</div>" if has_reasoning else ""}
      {"<div id='nav-methodology' class='nav-panel'><div class='subtitle'>" + method_subtitle + "</div>" + method_nav + "</div>" if has_method else ""}
    </nav>
    <main>
      <div id="view-cruxes" class="view-panel active">{crux_body}</div>
      {"<div id='view-debate' class='view-panel'>" + debate_body + "</div>" if has_debate else ""}
      {"<div id='view-reasoning' class='view-panel'>" + reasoning_body + "</div>" if has_reasoning else ""}
      {"<div id='view-methodology' class='view-panel'>" + method_body + "</div>" if has_method else ""}
    </main>
    <aside id="quote-panel" class="quote-panel" aria-label="Source quotes">
      <div class="quote-panel-header">
        <h3>Source evidence</h3>
        <button type="button" class="quote-panel-close" id="quote-panel-close" title="Close">×</button>
      </div>
      <div id="quote-panel-body">
        <p class="quote-panel-hint">Click <strong>Quotes</strong> on any anchor claim to read full verbatim excerpts here.</p>
      </div>
    </aside>
  </div>
  <script>
    const layout = document.querySelector('.layout');
    const panelBody = document.getElementById('quote-panel-body');
    const panelClose = document.getElementById('quote-panel-close');

    function openQuotePanel(targetId, card) {{
      const tpl = document.getElementById(targetId);
      if (!tpl) return;
      panelBody.innerHTML = tpl.innerHTML;
      layout.classList.add('quote-open');
      document.querySelectorAll('.claim-card.quote-active').forEach(el => el.classList.remove('quote-active'));
      if (card) card.classList.add('quote-active');
    }}

    function closeQuotePanel() {{
      layout.classList.remove('quote-open');
      document.querySelectorAll('.claim-card.quote-active').forEach(el => el.classList.remove('quote-active'));
      panelBody.innerHTML = '<p class="quote-panel-hint">Click <strong>Quotes</strong> on any anchor claim to read full verbatim excerpts here.</p>';
    }}

    document.querySelectorAll('.quote-btn').forEach(btn => {{
      btn.addEventListener('click', (e) => {{
        e.stopPropagation();
        openQuotePanel(btn.dataset.quoteTarget, btn.closest('.claim-card'));
      }});
    }});

    panelClose.addEventListener('click', closeQuotePanel);

    function switchToTab(tab) {{
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
      document.querySelectorAll('.nav-panel').forEach(p => p.classList.toggle('active', p.id === 'nav-' + tab));
      document.querySelectorAll('.view-panel').forEach(p => p.classList.toggle('active', p.id === 'view-' + tab));
    }}

    document.querySelectorAll('.hypothesis-crux a[href^="#"]').forEach(a => {{
      a.addEventListener('click', (e) => {{
        const target = a.getAttribute('href');
        if (!target || target.length < 2) return;
        e.preventDefault();
        switchToTab('cruxes');
        const el = document.querySelector(target);
        if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
        history.replaceState(null, '', target);
      }});
    }});

    document.querySelectorAll('.tab-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        const tab = btn.dataset.tab;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
        document.querySelectorAll('.nav-panel').forEach(p => p.classList.toggle('active', p.id === 'nav-' + tab));
        document.querySelectorAll('.view-panel').forEach(p => p.classList.toggle('active', p.id === 'view-' + tab));
      }});
    }});
  </script>
</body>
</html>"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(doc, encoding="utf-8")
    return output
