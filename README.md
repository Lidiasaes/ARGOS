## Start here

This file is a short overview only. For the full documentation, open **[ARGOS_ENTRY_POINT.md](ARGOS_ENTRY_POINT.md)**.

## How ARGOS works

<p align="center">
  <img src="extra_documentation/ARGOS_new_clean.jpg" alt="ARGOS workflow: input → ingestion → structure → assessment → output" width="900">
</p>

Goal: epistemic analysis pipeline for document cases: extracts claims, presuppositions, cruxes, cross-source debate, and expert reasoning. **Not a summarizer.**
The pipeline has five stages.

### 1. Input

Manually prepare a case folder under `cases/{case}/`:

- **`sources.json`**: manifest of every source (URL, author, optional `role`, `content_type`, etc.). See [Case inputs: `sources.json` and `files/`](#case-inputs--sourcesjson-and-files).
- **`files/`**: plain-text versions of each document. Convert PDFs to `.txt` manually before running the pipeline. YouTube sources can be fetched automatically at ingest when listed in `sources.json` with `content_type: video`.

Nothing in this stage calls the LLM. You are defining *what* ARGOS will read.

### 2. Tool functioning: Ingestion

**Pipeline steps:** `ingest` → `reconcile`

- **Extract claims and evidence**: reads each source, chunks text, pulls claims, evidence nodes, and open questions into `graph.json`. Assigns structural `role` per source and `evidential_weight` per node.
- **Reject unverified extractions and merge similar claims**: same-paper dedup at similarity ≥ 0.85 during ingest; cross-paper reconcile merges paraphrases (similarity 0.65–0.84) into canonical nodes with union attestations, `epistemic_status`, and junk/stance pruning.

**Key outputs:** `cases/{case}/graph.json` · `profiles/case_profile.json` · `profiles/theses/*.json` · `compiled/source_roles.json`

### 3. Tool functioning: Structure

**Pipeline steps:** `structure` → `crystallize`

- **Find presuppositions behind claims**: adds `presupposition` nodes and `presupposes` edges to the graph (runs after reconcile so only claim/evidence nodes were merged).
- **Deterministic filtering.** Only presuppositions marked fatal or major to the claim's validity are kept; anything marked minor is dropped to reduce graph noise and keep theme clustering focused on assumptions that can actually break the claim (softer field-level presuppositions are handled later by the reasoning layer). The result is capped at three presuppositions per claim (`MAX_PRESUPPOSITIONS_PER_BATCH`); when the model returns four or more qualifying entries, FATAL items are prioritized over MAJOR.
- **Build consequence chains and cruxes**: compresses the graph into themes, argument chains, semantic claim clusters, and cruxes (open disputes that matter). Computes per-source epistemic importance.

**Key outputs:** `graph.json` (presuppositions) · `compiled/index.json` · `compiled/source_importance.json`

### 4. Tool functioning: Assessment

**Pipeline steps:** `relate` · `debate` · `hypothesis` · `reasoning` · `methodology` · `assess`

- **Cross-corpus debate extraction**: cross-paper relations (supports, contradicts, …), debate state, multi-source contradictions, semantic compression groups.
- **Research design for solving cruxes**: hypothesis packages per crux with verifier pass on study parameters.
- **Expert reasoning**: field briefing, unstated presuppositions, open questions, devil's advocate per settled claim.
- **Methodology**: domain criteria and per-paper methodology audits.
- **Formal audit**: gap finder plus multi-section Epistemic Health Report (`assess`).

**Key outputs:** `compiled/cross_links.json` · `compiled/debate_state.json` · `hypotheses/` · `reasoning/` · `methodology/` · `reports/{case}_report.md`

### 5. Output

**Primary reader UI:** `cases/{case}/dashboard.html`: four tabs (Cruxes, Debate, Reasoning, Methodology) covering almost all generated analysis. Build or refresh with:

```powershell
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case {case}
```

**Also:** [`reports/{case}_report.md`](reports/) — Epistemic Health Report from `--step assess`.

The dashboard is not the complete knowledge package. For the full formal audit, raw graph, case profile, source theses, and original papers, see [If you only open `dashboard.html`](#if-you-only-open-dashboardhtml) below.

---

## Quick start

```powershell
cd "c:\your\path\ARGOS"
.\.venv-win\Scripts\python.exe main.py --case use_case --step all
```

Requires `ANTHROPIC_API_KEY` in `.env`. Python: `.\.venv-win\Scripts\python.exe`

Before pushing to GitHub, see **[Git: cases not to push](#git--cases-not-to-push)** if some `cases/` folders should stay local.

---

## Outputs

Pipeline: **sources → ingest → reconcile → structure → crystallize → relate/debate → reports/reasoning → dashboard**.

**Core order (must not be swapped):** `ingest` → `reconcile` → `structure` → `crystallize`. Reconcile runs **before** structure so it only merges `claim`/`evidence` nodes: not presuppositions added later by structure.

The dashboard (`cases/{case}/dashboard.html`) is the **main reader UI**. It is not the complete knowledge package: see below.

### What knowledge does ARGOS produce?

| Layer | Knowledge | Typical consumer question |
|-------|-----------|---------------------------|
| **Disputes** | Cruxes, argument chains, stakes, resolution protocols | What are the open disagreements that matter? |
| **Structure** | Themes (presupposition clusters), epistemic gaps | What assumptions sit behind the claims? What's missing? |
| **Cross-corpus** | Cross-paper canonical claims (`reconcile`), cross-links, multi-source attestations, contradictions | Do papers agree, clash, or converge on the same claim? |
| **Methodology** | Domain criteria, per-paper audit scores | Are the papers methodologically defensible for this domain? |
| **Expert reasoning** | Field briefing, unstated presuppositions, open questions, devil's advocate | What would a skeptical expert say? What should we research next? |
| **Research design** | Hypothesis packages per crux | What study would settle a crux? |
| **Formal audit** | Epistemic Health Report (markdown) | Full forensic walkthrough of the case (~10 sections) |
| **Ground truth** | Raw graph + source papers | Every claim, quote, relation: the underlying database |

---

### If you only open `dashboard.html`

**You get almost all generated *analysis* knowledge.** The four tabs cover crystallize, relate, reasoning, and methodology outputs. You do **not** need to open JSON files separately for: cruxes, debate structure (including reconcile badges), briefing, presuppositions, open questions, devil's advocate, methodology audits, or research hypotheses (Debate tab).

**You would miss the following**: open these in addition to the HTML:

| You miss | Why it matters | Path (from `ARGOS/`) | Step |
|----------|----------------|----------------------|------|
| **Epistemic Health Report** | The only long formal audit: question map, section-by-section forensic review, gap inventory at report scale. More exhaustive than any dashboard tab. | `reports/{case}_report.md` | `assess` |
| **Raw graph** | Complete database of every claim, evidence node, presupposition, gap, relation, and verbatim attestation. Dashboard shows **slices** linked to cruxes and debate: not the full graph. | `cases/{case}/graph.json` | `ingest`, `reconcile`, `structure` |
| **Case profile** | How extraction was framed: central questions and subfields that guided what got pulled from papers. | `cases/{case}/profiles/case_profile.json` | `ingest` |
| **Source theses** | Per-paper main-thesis summary used as ingest context. | `cases/{case}/profiles/theses/*.json` | `ingest` |
| **Original papers** | The inputs themselves. | `cases/{case}/files/` | manual |

**Not knowledge: skip unless debugging:** `cache/{case}/` (LLM response cache, speeds reruns).

**Reader guide for dashboard UI:** [`docs/dashboard_guide.html`](docs/dashboard_guide.html): what each tab and subsection means (linked in the dashboard sidebar).

**Dashboard UX (render-only, no graph changes):** chains and themes are defined once in a **Reference** appendix at the bottom of the Cruxes tab; each crux links to them with a one-line summary. Cross-paper relations in Debate are **grouped by `subfield`** (collapsible sections, all relations shown). Badges use a **unified 5-tier vocabulary** across tabs (`b-danger` / `b-warn` / `b-ok` / `b-muted` / `b-info`): see legend in each tab.

---

### In the dashboard vs elsewhere

| Knowledge | In dashboard? | Tab or file |
|-----------|---------------|-------------|
| Cruxes, chains, themes, gaps | Yes | **Cruxes** |
| Research hypothesis per crux | Yes | **Debate** → Research hypotheses |
| Cross-paper relations | Yes | **Debate** → Cross-paper relations (grouped by subfield) |
| Cross-paper canonical claims (`epistemic_status`) | Yes | **Debate** → Cross-paper canonical claims |
| Semantic compression (paraphrase groups) | Yes | **Debate** → Compression |
| Multi-source contradictions | Yes | **Debate** → Contradictions |
| Field briefing | Yes | **Reasoning** |
| Presuppositions, open questions, devil's advocate | Yes | **Reasoning** |
| Methodology criteria + per-paper audits | Yes | **Methodology** |
| Health report | **No** | `reports/{case}_report.md` |
| Full graph (all nodes) | **No** | `cases/{case}/graph.json` |
| Case profile, source theses | **No** | `cases/{case}/profiles/` |
| Source papers | **No** | `cases/{case}/files/` |

---

### File paths (all relative to `ARGOS/`)

**Inputs**: keep across reruns: `cases/{case}/sources.json`, `cases/{case}/files/` (see **Case inputs: `sources.json` and `files/`** below)

**Graph & profiles:** `cases/{case}/graph.json` · `cases/{case}/profiles/case_profile.json` · `cases/{case}/profiles/theses/*.json`

**Compiled:** `cases/{case}/compiled/index.json` · `cross_links.json` · `debate_state.json` · `source_roles.json` · `source_importance.json`

### Source role vs epistemic importance (do not conflate)

| Level | When | What | Who assigns |
|-------|------|------|-------------|
| **1: Role** | Ingest (upfront) | Structural document type: `primary_research`, `debate_transcript`, `judge_decision`, `rebuttal`, `commentary`, `review`, `unknown` | Deterministic rules on URL/author/title → Haiku if ambiguous → optional `"role"` in `sources.json` |
| **2: Evidential weight** | Ingest (per claim) | `evidential_weight` on each extracted node — `null` if the LLM extractor did not return it; never filled with a placeholder default | LLM extractor (`EXTRACTOR_V4`); nodes where the LLM omits it stay `null` until a dedicated assessment pass writes a real value |
| **2b: Independence score** | *(not yet implemented)* | `independence_score` on each node — always `null`; reserved for a future cross-source independence analysis | Placeholder until implemented; was formerly a hardcoded `1.0` default |
| **3: Epistemic importance** | Post-graph (after crystallize) | `unique_claims_count`, `high_value_unique`, `crux_exclusive_count`, `epistemic_risk`, `importance_tier` | Computed from graph + cruxes: **not pre-declared** |

`role` is context only: does not gate ingest. `unknown` is valid. Relevance emerges: a paper with many unique high-value claims or exclusive crux anchors scores high `epistemic_risk` even if nobody labeled it important upfront.

Optional human override in `sources.json`:
```json
{ "url": "...", "author": "...", "role": "judge_decision" }
```

**Reports:** `reports/{case}_report.md`

**Methodology:** `cases/{case}/methodology/profile.json` · `cases/{case}/methodology/audits/*.json`

**Hypotheses:** `cases/{case}/hypotheses/{crux_id}.json`

**Reasoning:** `cases/{case}/reasoning/field_briefing.md` · `presuppositions.json` · `open_questions.json` · `devils_advocate/{claim_id}.json`

**UI:** `cases/{case}/dashboard.html` · `docs/dashboard_guide.html` · `docs/quote_length_limits.md` · `README.md`

**Cache (not a deliverable):** `cache/{case}/`

| Situation | Action |
|-----------|--------|
| Re-run same step, same prompts | No flag needed: reads cache, ~$0 |
| Changed prompts | `invalidate_cache.py --level all` + delete derived files + `--reset-cache` |
| Fresh graph from sources | Delete `graph.json` + `compiled/` before re-ingest |
| Refresh UI only | `build_dashboard.py` only: reuses all JSON/MD (dashboard layout/badge changes need no graph re-run) |

---

### Output examples (use_case)

Minimal shape of each artifact so you know what to expect.

**Graph node** (`graph.json`): `--step ingest` (+ fields from `--step reconcile`):
```json
{
  "id": "9615c0e1",
  "type": "claim",
  "content": "Over 5,300 living microorganisms were identified...",
  "subfield": "endometrial microbiota",
  "evidential_weight": 0.85,
  "attestations": [
    { "source_id": "cases/use_case/files/paper_a.txt", "quote": "..." },
    { "source_id": "cases/use_case/files/paper_b.txt", "quote": "..." }
  ],
  "support_count": 2,
  "contradict_count": 0,
  "epistemic_status": "supported",
  "relations": [{ "type": "supports", "target": "fe7a8957", "source": "relate" }]
}
```

`epistemic_status` after reconcile: `well_established` (3+ papers), `supported` (2), `single_source` (1), `contested` (has cross-paper contradictions).

**Case profile** (`profiles/case_profile.json`): `--step ingest`:
```json
{
  "central_questions": ["Does RNA-based metatranscriptomics reliably indicate viable microbes?"],
  "subfields": ["endometrial microbiota", "metatranscriptomics", "16S rRNA gene sequencing"]
}
```

**Crux** (`compiled/index.json` → `cruxes[]`): `--step crystallize`:
```json
{
  "id": "rna_viability_indicator",
  "question": "Does detection of microbial RNA reliably indicate living, active microbes...?",
  "stakes": "If RNA does not reliably indicate living microbes: the entire foundation...",
  "claim_ids": ["8219ce56", "268f89e4"],
  "resolution_path": "Perform controlled experiments comparing pre- and post-collection..."
}
```

**Cross-link** (`compiled/cross_links.json`): `--step relate`:
```json
{
  "from_id": "2943e734", "to_id": "5f945bdf", "type": "supports",
  "rationale": "Both describe the limitation that DNA cannot distinguish living from dead microbes.",
  "strength": 0.95, "subfield": "16S rRNA gene sequencing"
}
```

**Debate node** (`compiled/debate_state.json`): `--step relate` / `debate`:
```json
{
  "claim_id": "268f89e4",
  "contradicted_by": [{ "claim_id": "61c67429", "canonical": "...", "relation": { "type": "contradicts" } }],
  "supported_by": [...]
}
```

**Health report** (`reports/use_case_report.md`): `--step assess`:
```markdown
## 1. Question Map
**Q1: What metabolically active microorganisms inhabit...** (**9615c0e1**)
## 7. Critical Gaps
Viability inference from RNA detection lacks validation...
```

**Methodology audit** (`methodology/audits/*.json`): `--step methodology`:
```json
{
  "source_id": "cases/use_case/files/sample_microbiota_2021.txt",
  "methodology_score": 0.012,
  "evaluations": [{ "criterion_id": "biomass_quantification", "status": "not_declared", "reviewer_note": "..." }]
}
```

**Hypothesis** (`hypotheses/rna_viability_indicator.json`): `--step hypothesis`:
```json
{
  "working_hypothesis": "Taxa detected by DNA but absent in RNA represent non-viable organisms...",
  "falsification_condition": "If Lactobacillus can be cultured from endometrial samples...",
  "study_parameters": [
    {
      "parameter": "viability assay",
      "value": "culture + live/dead staining",
      "grounding": "from_resolution_path",
      "source_node_ids": ["8219ce56"],
      "attestation_quote": null,
      "external_reference": null,
      "justification_note": "Matches resolution_path protocol; no fixed day-count invented."
    }
  ],
  "invented_or_unverified": [],
  "verifier_overrides": [
    { "parameter": "follow-up duration", "from_grounding": "from_evidence", "to_grounding": "ungrounded", "reason": "attestation_quote not found literally in source texts" }
  ],
  "proposed_study": {
    "design": "Prospective comparison",
    "n_needed": {
      "value": "TBD: formal power analysis required",
      "missing_inputs": ["effect size", "variance estimate"],
      "discovery_cohort_warning": null,
      "caveat": "Corpus does not provide inputs for formal N calculation."
    }
  }
}
```
Numbers, sample sizes, and power assumptions must be traceable via `study_parameters` and `n_needed.power_analysis_inputs`: otherwise `TBD` / `ungrounded`. A second Haiku pass (`HYPOTHESIS_VERIFIER`) updates grounding and fills `verifier_overrides[]`; overrides are shown in the dashboard.

**Presupposition** (`reasoning/presuppositions.json`): `--step reasoning`:
```json
{
  "presupposition": "Detected RNA originates from living microbes in vivo...",
  "vulnerability": "high",
  "how_to_test": "Single-cell RNA-seq with viability staining...",
  "implicated_claims": ["fe7a8957", "8219ce56"]
}
```

**Open question** (`reasoning/open_questions.json`): `--step reasoning`:
```json
{
  "question": "What is the minimum biomass threshold below which signals reflect contamination?",
  "type": "methodological",
  "epistemic_leverage": "high",
  "suggested_approach": "Serial dilution series with negative controls..."
}
```

**Devil's advocate** (`reasoning/devils_advocate/268f89e4.json`): `--step reasoning`:
```json
{
  "claim_id": "268f89e4",
  "strongest_counterargument": "Single study with methodology score 0.01...",
  "revised_confidence": "medium-low",
  "the_thing_nobody_is_saying": "RNA absence may reflect degradation, not biology."
}
```

**Field briefing** (`reasoning/field_briefing.md`): `--step reasoning`:
```markdown
## What this field now knows
The paradigm of endometrial sterility is dead, but rests on fragile methodological foundations...
## The research agenda
**Biomass-calibrated contamination thresholds**: Serial dilution of defined communities...
```

**Dashboard** (`dashboard.html`): `build_dashboard.py`: main reader UI; see **If you only open dashboard.html** above for what is not included.

---

## Step & command guide

What each step does, **where the prompt lives**, **what files to open**, and **where it appears in the dashboard**.

### Pipeline steps (`main.py`)

```powershell
.\.venv-win\Scripts\python.exe main.py --case {case} --step {step}
```

| Step | What it does | Prompt file(s) | Key constant(s) | Files to inspect | Dashboard tab |
|------|--------------|----------------|-----------------|------------------|---------------|
| **ingest** | **YouTube transcripts** (if `content_type: video` + YouTube URL, no local file). Then reads source texts, chunks, extracts claims/evidence/questions. Same-paper dedup at similarity ≥ 0.85. **Resolves structural `role` per source** (metadata rules → Haiku if ambiguous → optional `role` override in `sources.json`). | `episteme/pipeline/youtube_transcript.py`, `episteme/prompts/extraction.py`, `episteme/prompts/source_role.py` | `EXTRACTOR_V4`, `SOURCE_ROLE_CLASSIFIER`, … | `graph.json` (`source_role` on nodes) · `compiled/source_roles.json` · `files/youtube_*.txt` (auto) | Cruxes (claims later) |
| **reconcile** | Merges cross-paper `claim`/`evidence` paraphrases (sim 0.65–0.84) into canonical nodes with union attestations. Sets `support_count`, `contradict_count`, `epistemic_status`. Prunes junk/stance-conflicting attestations. **Runs before structure.** | `episteme/prompts/reconcile.py` | `RECONCILE_PAIR_VERDICT` (Haiku for ambiguous 0.65–0.75 pairs) | `graph.json` (fewer nodes, multi-attestation) | **Debate** → Cross-paper canonical |
| **structure** | Finds presuppositions behind claims; adds `presupposes` edges | `episteme/prompts/templates.py` | `PHILOSOPHER_BATCH`, `PHILOSOPHER_DISAMBIGUATION` | `graph.json` (nodes `type: presupposition`) | Cruxes → themes |
| **crystallize** | Compresses graph → themes, chains, cruxes, `claim_clusters[]` (semantic groups at 0.75). **Runs source importance** (Level 3). | `episteme/prompts/templates.py` | `CRYSTALLIZE_CHAINS`, `CRYSTALLIZE_CRUXES` | `cases/{case}/compiled/index.json` · `compiled/source_importance.json` | **Cruxes** · **Debate** → Compression |
| **importance** | Recompute per-source epistemic importance from graph + cruxes (**no LLM**) |: | `episteme/compile/source_importance.py` | `compiled/source_importance.json` |: (terminal / JSON) |
| **relate** | Cross-source relations between papers (supports, contradicts, …) | `episteme/prompts/relate.py` | `RELATE_CROSS_SOURCE` | `compiled/cross_links.json` · `compiled/debate_state.json` · `graph.json` (relations `source: relate`) | **Debate** |
| **debate** | Rebuilds debate structure from graph relations (**no LLM**) |: (deterministic Python) | `episteme/compile/debate_state.py` | `compiled/debate_state.json` | **Debate** |
| **assess** | Gap finder + multi-section health report | `episteme/prompts/templates.py` | `GAP_FINDER_COMPILED`, `HEALTH_REPORT_SECTION`, `REPORT_SECTIONS` | `reports/{case}_report.md` · `graph.json` (nodes `type: gap`) |: (markdown report) |
| **methodology** | Domain criteria + per-paper methodology audit | `episteme/prompts/methodology.py` | `METHODOLOGY_PROFILE`, `METHODOLOGY_AUDIT` | `methodology/profile.json` · `methodology/audits/*.json` | **Methodology** |
| **hypothesis** | Research hypothesis package per crux + Haiku verifier on `study_parameters` | `episteme/prompts/hypothesis.py` · `episteme/pipeline/hypothesis_verify.py` | `HYPOTHESIS_AGENT`, `HYPOTHESIS_VERIFIER` | `hypotheses/{crux_id}.json` (`verifier_overrides[]`) | **Debate** → Research hypotheses |
| **reasoning** | Expert layer: presuppositions, devil's advocate, questions, briefing | `episteme/reasoning/prompts.py` | see table below | `reasoning/` folder | **Reasoning** |
| **all** | Runs **ingest → reconcile → structure → crystallize → assess → methodology** | (all of the above) |: | all core outputs | partial |

**Cached LLM responses:** `cache/{case}/agent/*.json` (and `nodes/`, `profiles/`, etc.). Use `scripts/invalidate_cache.py` to clear.

---

### Reasoning sub-steps (`--step reasoning` or `run_reasoning.py --phase`)

| Phase | What it does | Prompt constant | File to inspect | What to read in the file |
|-------|--------------|-----------------|-----------------|--------------------------|
| **presuppositions** | Unstated assumptions papers never defend | `PRESUPPOSITION_MINER` | `reasoning/presuppositions.json` | `presupposition`, `what_breaks_if_false`, `vulnerability`, `how_to_test`, `implicated_claims` |
| **devils** | Strongest skeptical counter per multi-source “settled” claim | `DEVILS_ADVOCATE` | `reasoning/devils_advocate/{claim_id}.json` | `strongest_counterargument`, `revised_confidence`, `the_thing_nobody_is_saying` |
| **questions** | Research agenda: questions evidence raises but doesn't answer | `QUESTION_GENERATOR` | `reasoning/open_questions.json` | `question`, `type`, `epistemic_leverage`, `suggested_approach` |
| **narrative** | Field briefing synthesis (prose, not bullets) | `NARRATIVE_SYNTHESIZER` | `reasoning/field_briefing.md` | sections: *What this field now knows*, *Where the live debate is*, *What everyone assumes*, *Research agenda* |

```powershell
# all four phases
.\.venv-win\Scripts\python.exe main.py --case use_case --step reasoning

# one phase only
.\.venv-win\Scripts\python.exe scripts\run_reasoning.py --case use_case --phase devils
```

**Code:** `episteme/reasoning/runner.py` orchestrates calls · `episteme/reasoning/context.py` builds graph input (no raw text).

---

### Helper scripts

| Script | What it does | API? | Outputs / when to use |
|--------|--------------|------|------------------------|
| `build_dashboard.py` | Static HTML navigator | No | `cases/{case}/dashboard.html`: after any steps you want visualized |
| `repair_graph_data.py` | Quote repair (extend to sentence boundaries in source text) + prune bad attestations | No | Updates `graph.json`: use after ingest or when quotes look truncated/junk |
| `restore_graph_quotes.py` | Restore `graph.json` from backup, run quote repair, safe prune, refresh epistemic fields | No | `--from-backup cases/{case}/graph_backup_*.json` |
| `strip_eric_toc.py` | Remove TOC lines from a local judge/PDF extract (covid helper) | No | Rewrites `cases/covid/files/judge_eric_decision.txt` in place |
| `invalidate_cache.py` | Delete cached LLM responses | No | Clears `cache/{case}/`: **use before prompt-change reruns** |
| `build_debate_state.py` | Rebuild debate JSON from graph | No | `compiled/debate_state.json` |
| `run_relate.py` | Same as `--step relate` | Yes | `cross_links.json` |
| `run_hypothesis.py` | Hypothesis for all or one crux | Yes | `hypotheses/{crux_id}.json` |
| `run_reasoning.py` | Reasoning with `--phase` | Yes | `reasoning/*` |
| `filter_presuppositions.py` | Prune weak presuppositions from graph | No | `graph.json` (then re-run crystallize) |
| `case_stats.py` | Print graph + index + cache counts | No | terminal only |
| `migrate_attestations.py` | Count multi-source attestations | No | terminal only |
| `generate_methodology_profile.py` | Criteria only (no audits) | Yes | `methodology/profile.json` |
| `audit_methodology.py` | Per-source audits only | Yes | `methodology/audits/*.json` |
| `recompute_methodology_scores.py` | Recalculate scores in Python | No | updates existing audit JSONs |
| `explore.py` | CLI to browse index / graph | No | terminal only (`--summary`, `--list cruxes`, `--debate`, …) |

---

### Dashboard tabs (after `build_dashboard.py`)

**Reader guide:** [`docs/dashboard_guide.html`](docs/dashboard_guide.html): field definitions for every tab and subsection (no case text pasted). Linked from each dashboard sidebar.

**Badge vocabulary (all tabs):** five semantic tiers, same colors everywhere: see the collapsible **Legend: badge vocabulary** in each tab.

| Tier | Class | Typical labels |
|------|-------|----------------|
| critical | `b-danger` | FATAL, stance conflict, red_flag, failed, high vulnerability |
| caution | `b-warn` | MAJOR, contested, ungrounded, REVIEW, variant, latent gem |
| confirmed | `b-ok` | well_established, canonical, declared, central, from_evidence |
| neutral | `b-muted` | peripheral, not_applicable, single_source, low leverage |
| category | `b-info` | Relation types (supports, contradicts, …), question types |

| Tab | Pipeline step | One-line purpose |
|-----|---------------|------------------|
| **Cruxes** | crystallize | Open disputes: disagreements that would shift the debate if resolved |
| **Debate** | reconcile + relate + hypothesis | Cross-paper canonical claims, compression, relations, contradictions, research hypotheses |
| **Reasoning** | reasoning | Expert layer: briefing, unstated assumptions, open questions, devil's advocate |
| **Methodology** | methodology | Shared audit rubric + per-paper methodology scores |

#### Cruxes tab: subsections

| Block | JSON source | Meaning |
|-------|-------------|---------|
| `crux_id` (h2) | `cruxes[].id` | Short handle for navigation: **read `.question` for substance** |
| Question | `cruxes[].question` | The precise disagreement (primary title for readers) |
| What is at stake | `cruxes[].stakes` | What holds or collapses depending on resolution |
| Resolution protocol | `cruxes[].resolution_path` | What evidence would settle the crux |
| Argument chains | `chains[]` | **Short link** per chain → full definition in Reference appendix |
| Themes | `themes[]` | **Short link** per theme → full definition in Reference appendix |
| Anchor claims | `cruxes[].claim_ids` → `graph.json` | Primary claims; **Quotes** button → verbatim sources (right panel) |
| Epistemic gaps | `cruxes[].gap_ids` → `index.gaps[]` | What the corpus does not establish |
| **Reference → Chains / Themes** | `chains[]`, `themes[]` | Each chain/theme defined **once** with full conditions, badges, and “Also in cruxes” links |

Chains and themes are many-to-many with cruxes (by design). The reference appendix avoids duplicating the same HTML block in every crux section.

#### Debate tab: subsections

| Section | Meaning |
|---------|---------|
| Research hypotheses | Proposed studies per crux; `study_parameters` with Haiku verifier overrides (`hypotheses/{crux_id}.json`) |
| Semantic compression | Paraphrase groups from `compiled/index.json` → `claim_clusters[]` (embedding ≥ 0.75) |
| Cross-paper relations | All relations from `cross_links.json`, **grouped by `subfield`** (collapsible); nav lists each subfield with count |
| Cross-paper canonical claims | Nodes merged by `--step reconcile`; epistemic badge + paper count from live attestations |
| Direct contradictions | Claims with `contradicted_by` edges |

#### Reasoning tab: subsections

| Section | File | Meaning |
|---------|------|---------|
| Field briefing | `reasoning/field_briefing.md` | ~1-page expert synthesis (not a lit review) |
| Unstated presuppositions | `presuppositions.json` | Hidden assumptions + how to test |
| Open research questions | `open_questions.json` | Prioritized unanswered questions |
| Devil's advocate | `devils_advocate/{claim_id}.json` | Skeptical counter per multi-source claim |

#### Methodology tab: subsections

| Section | Meaning |
|---------|---------|
| Case criteria | Shared rubric: criteria table, red flags, confidence ceiling |
| Per-source audit | Each paper: score, criterion evaluations, evidence quotes |

**Not in dashboard:** `reports/{case}_report.md` (long assess report), raw `graph.json` (full DB).

### Graph quote quality (post-ingest maintenance)

Ingest can leave truncated quotes, TOC debris, or stance-mismatched attestations. These are fixed **on `graph.json`** (not in the dashboard HTML alone).

| Module / script | Role |
|-----------------|------|
| `episteme/filters/quote_repair.py` | Extend clipped quotes to sentence boundaries in source text (`QUOTE_REPAIR_MAX_CHARS` in `config.py`) |
| `episteme/filters/junk_quote.py` | Detect TOC lines, section-title fragments, PDF debris |
| `episteme/pipeline/attestation_stance.py` | Prune or flag attestations whose origin stance conflicts with the claim |
| `scripts/repair_graph_data.py` | Run quote repair + prune on current graph (backs up first) |
| `scripts/restore_graph_quotes.py` | Restore from `graph_backup_*.json`, then repair + prune |

```powershell
.\.venv-win\Scripts\python.exe scripts\repair_graph_data.py --case covid
.\.venv-win\Scripts\python.exe scripts\repair_graph_data.py --case covid --skip-prune   # quotes only
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case covid
```

See [`docs/quote_length_limits.md`](docs/quote_length_limits.md) for where quote length can be capped in the pipeline (repair cap vs ingest vs display-only slices).

---

### Prompt file map (quick lookup)

| File | Contains |
|------|----------|
| `episteme/prompts/extraction.py` | `EXTRACTOR_V4`, `CASE_PROFILE`, `SOURCE_THESIS`, `GENERICITY_CHECK` |
| `episteme/prompts/templates.py` | `CHUNKER`, `DOC_SUMMARIZER`, `PHILOSOPHER_*`, `CRYSTALLIZE_*`, `GAP_FINDER_*`, `REPORT_SECTIONS` |
| `episteme/prompts/relate.py` | `RELATE_CROSS_SOURCE` |
| `episteme/prompts/methodology.py` | `METHODOLOGY_PROFILE`, `METHODOLOGY_AUDIT` |
| `episteme/prompts/hypothesis.py` | `HYPOTHESIS_AGENT` |
| `episteme/reasoning/prompts.py` | `PRESUPPOSITION_MINER`, `DEVILS_ADVOCATE`, `QUESTION_GENERATOR`, `NARRATIVE_SYNTHESIZER` |

---

## FAQ

### How do I run a case that already exists?

Use the case name with any step. **Without** `--reset-cache`, cached LLM responses are reused → **~$0 API cost**.

```powershell
.\.venv-win\Scripts\python.exe main.py --case covid_small --step crystallize
```

---

### I changed prompts: how do I re-run from scratch?

Example: `covid_small` was already run, you improved prompts, you want a **clean rerun** without manually hunting through folders.

**Two things to reset:**
1. **API cache**: stored LLM responses in `cache/{case}/`
2. **Derived artifacts**: `graph.json`, `compiled/`, reports, etc. (old outputs mixed with new extractions)

**Step 1: clear API cache (no manual folder digging):**

```powershell
.\.venv-win\Scripts\python.exe scripts\invalidate_cache.py --case covid_small --level all
```

Levels available: `raw`, `chunks`, `nodes`, `agent`, `trust`, `doc_summary`, `profiles`, or `all`.

**Step 2: remove derived outputs (keeps `sources.json` and `files/`):**

```powershell
Remove-Item cases\covid_small\graph.json -ErrorAction SilentlyContinue
Remove-Item cases\covid_small\compiled -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item cases\covid_small\methodology -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item cases\covid_small\reasoning -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item cases\covid_small\hypotheses -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item cases\covid_small\dashboard.html -ErrorAction SilentlyContinue
Remove-Item reports\covid_small_report.md -ErrorAction SilentlyContinue
```

**Step 3: re-run pipeline with `--reset-cache`:**

```powershell
.\.venv-win\Scripts\python.exe main.py --case covid_small --step ingest --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step structure --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step crystallize --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step relate --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step assess --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step methodology --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step reasoning --reset-cache
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case covid_small
```

Or core shortcut:

```powershell
.\.venv-win\Scripts\python.exe main.py --case covid_small --step all --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step relate --reset-cache
.\.venv-win\Scripts\python.exe main.py --case covid_small --step reasoning --reset-cache
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case covid_small
```

| Flag / script | What it does |
|---------------|--------------|
| `--reset-cache` | Skips reading cache for **this run**; writes new responses |
| `invalidate_cache.py --level all` | **Deletes** all cached JSON files for the case |
| Deleting `graph.json` + `compiled/` | Forces graph and index to rebuild from new extractions |

> `--reset-cache` alone does not delete old cache files: use `invalidate_cache.py` for a true clean slate.

---

### I only changed prompts for one step (e.g. crystallize)

Invalidate **agent** cache + delete that step's outputs, then re-run:

```powershell
.\.venv-win\Scripts\python.exe scripts\invalidate_cache.py --case covid_small --level agent
Remove-Item cases\covid_small\compiled\index.json -ErrorAction SilentlyContinue
.\.venv-win\Scripts\python.exe main.py --case covid_small --step crystallize --reset-cache
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case covid_small
```

| Step changed | Invalidate level | Delete |
|--------------|------------------|--------|
| ingest / extract | `nodes`, `chunks`, `agent` | `graph.json`, `compiled/` |
| reconcile | `agent` (pair verdicts) | re-run reconcile on `graph.json`; then `debate` + dashboard |
| structure | `agent` | presup nodes in graph* |
| crystallize | `agent` | `compiled/index.json` |
| relate | `agent` | `compiled/cross_links.json` |
| hypothesis | `agent` | `hypotheses/*.json` |
| assess | `agent` | `reports/{case}_report.md` |
| methodology | `agent`, `profiles` | `methodology/` |
| reasoning | `agent` | `reasoning/` |

\*For structure changes, safest is full graph reset (see “from scratch” above).

---

### What does `--reset-cache` cost?

**Paid**: every LLM call runs again. Use only when prompts or inputs changed.

**Free**: re-run without the flag; reads from `cache/{case}/`.

---

### What does `--step all` include?

**Runs:** ingest → **reconcile** → structure → crystallize → assess → methodology

**Does NOT run:** relate, debate, hypothesis, reasoning: run those separately after the core chain.

---

### My case folder exists but `main.py` rejects it

Cases must be registered in [`episteme/config.py`](episteme/config.py) → `VALID_CASES`.

**Currently registered:**

```
covid · covid_small · covid_demo · lhc · eggs · use_case
```

Having `cases/use_case_eduard/` is **not enough**. Without registration you get:

```
error: argument --case: invalid choice: 'use_case_eduard' (choose from ...)
```

**To register a new case:**

1. Create the folder:
   ```
   cases/my_case/
   ├── sources.json          # manifest: one entry per source
   └── files/                # source texts (manual or auto-downloaded)
       ├── paper_a.txt
       └── youtube_XXXXXXXXXXX.txt   # optional: created by ingest for YouTube videos
   ```
2. Add to `VALID_CASES` in `episteme/config.py`:
   ```python
   VALID_CASES = ["covid", "covid_small", "lhc", "eggs", "use_case", "my_case"]
   ```
3. Use `--case my_case` in all commands.

---

### Case inputs: `sources.json` and `files/`

All paths are relative to the repo root (`ARGOS/`).

**Layout**

```
cases/{case}/
├── sources.json     # INPUT you edit this (manifest of sources)
└── files/           # INPUT text bodies live here
```

- **`sources.json`**: JSON array; one object per paper, essay, debate video, judge decision, etc.
- **`files/`**: plain-text (or PDF) content. For sources you already have, point `local_path` at a file here. For YouTube debates, leave `local_path` null and ingest will download the transcript into `files/` automatically.

**`sources.json` fields**

| Field | Required | Notes |
|-------|----------|-------|
| `local_path` | No* | Path under `cases/{case}/files/…`. Use for text you placed manually. `null` for YouTube videos (filled on first ingest). |
| `url` | No* | Canonical URL. Used for metadata, role detection, and URL fetch fallback. Required for auto YouTube transcripts. |
| `author` | Recommended | Shown in graph attestations and methodology audits. |
| `date` | Recommended | `YYYY-MM-DD` (e.g. `"2024-03-28"`). Used in attestations and audits. |
| `content_type` | Recommended | One of `"text"`, `"pdf"`, `"video"`, `"audio"`, `"web"`. Default `"text"` if omitted. See table below; canonical list in [`episteme/pipeline/sources.py`](episteme/pipeline/sources.py) (`source_type_map`). |
| `title` | Optional | Human label; used in methodology audits and YouTube transcript headers. |
| `bibliography` | Optional | Sidecar reference list: path to a separate `.txt` in `files/` (not merged into ingest body). |
| `publication_status` | Optional | `"published"` or `"unpublished"`: methodology step only. |
| `role` | Optional | Override structural role: `primary_research`, `debate_transcript`, `judge_decision`, `rebuttal`, `commentary`, `review`, `unknown`. |

\* At least one of `local_path` or `url` must be usable: either a file on disk, a fetchable URL, or a YouTube URL for auto-transcript.

**`content_type` values**

Accepted values (only these five):

```
"text" | "pdf" | "video" | "audio" | "web"
```

| Value | Use for | Ingest behaviour |
|-------|---------|------------------|
| `"text"` | Papers, essays, blog posts, judge decisions saved as `.txt` | Reads `local_path`; if missing, tries `url` scrape |
| `"pdf"` | PDF papers | Reads `local_path` (`.pdf`); text extraction via `pypdf` |
| `"video"` | Debate / interview videos (especially YouTube) | If `url` is YouTube and no local file: **auto-download transcript** at ingest |
| `"audio"` | Same as video for transcript purposes | Same YouTube auto-download rules as `"video"` |
| `"web"` | Journalism / web articles | Treated like text for trust typing; prefer `local_path` if the site blocks scrapers |

**Where this is enforced in code**

| File | What it uses `content_type` for |
|------|--------------------------------|
| [`episteme/pipeline/sources.py`](episteme/pipeline/sources.py) | Canonical accepted values (`source_type_map`); defaults to `"text"` |
| [`episteme/pipeline/youtube_transcript.py`](episteme/pipeline/youtube_transcript.py) | Auto-download transcript when `"video"` or `"audio"` + YouTube URL |
| [`episteme/pipeline/source_role.py`](episteme/pipeline/source_role.py) | Infers `debate_transcript` role for `"video"` / `"audio"` |

**Examples**

Local text (you downloaded the article):

```json
{
  "local_path": "cases/covid/files/scott_alexander_covid_debate.txt",
  "url": "https://www.astralcodexten.com/p/your-covid-questions-answered",
  "author": "Scott Alexander",
  "date": "2024-03-28",
  "content_type": "text"
}
```

Peer-reviewed paper with reference sidecar:

```json
{
  "local_path": "cases/use_case/files/sample_microbiota_2021.txt",
  "bibliography": "cases/use_case/files/sample_microbiota_2021_references.txt",
  "url": "https://academic.oup.com/humrep/article/36/4/1021/6141565",
  "title": "Mapping the entire functionally active endometrial microbiota",
  "author": "Alberto Sola-Leyva, …",
  "date": "2021-02-18",
  "content_type": "text",
  "publication_status": "published"
}
```

YouTube debate: no manual download; transcript fetched at ingest:

```json
{
  "local_path": null,
  "url": "https://www.youtube.com/watch?v=Y1vaooTKHCM",
  "author": "Rootclaim",
  "date": "2023-12-30",
  "content_type": "video",
  "title": "First Rootclaim Debate on Covid Origins, part 1"
}
```

On first `ingest`, the pipeline:

1. Detects `content_type: "video"` + YouTube `url` + missing `local_path`
2. Downloads the auto-generated English transcript (`youtube-transcript-api`)
3. Saves `cases/{case}/files/youtube_{VIDEO_ID}.txt`
4. Updates `sources.json` with the new `local_path` (skipped on later runs if the file exists)

Optional role override (otherwise inferred from URL/author/title):

```json
{
  "local_path": "cases/covid/files/judge_will_decision.txt",
  "url": "https://drive.google.com/file/d/…/view",
  "author": "Judge Will",
  "date": "2024-03-28",
  "content_type": "text",
  "role": "judge_decision"
}
```

**Code:** `episteme/pipeline/youtube_transcript.py` · wired in `episteme/pipeline/ingest.py` at the start of `--step ingest`.

---

### Where is the dashboard?

```powershell
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case {case}
```

Open `cases/{case}/dashboard.html` in a browser.

**What you'd miss with HTML only:** health report, raw graph, case profile: see **Outputs → If you only open dashboard.html** above.

**Reader guide:** `docs/dashboard_guide.html` (linked in dashboard sidebar).

---

### How do I inspect outputs from the terminal?

```powershell
.\.venv-win\Scripts\python.exe explore.py --case covid_small --summary
.\.venv-win\Scripts\python.exe explore.py --case covid_small --list cruxes
.\.venv-win\Scripts\python.exe explore.py --case covid_small --debate
.\.venv-win\Scripts\python.exe scripts\case_stats.py --case covid_small
```

No API cost.

---

## Full pipeline

Replace `use_case` with your case name.

```powershell
cd "c:\your\path\ARGOS"

# 1. CORE (order matters)
.\.venv-win\Scripts\python.exe main.py --case use_case --step ingest
.\.venv-win\Scripts\python.exe main.py --case use_case --step reconcile   # cross-paper merge → graph.json
.\.venv-win\Scripts\python.exe main.py --case use_case --step structure
.\.venv-win\Scripts\python.exe main.py --case use_case --step crystallize

# 2. FILTER (only if too many presuppositions)
.\.venv-win\Scripts\python.exe scripts\case_stats.py --case use_case
.\.venv-win\Scripts\python.exe scripts\filter_presuppositions.py --case use_case --dry-run
.\.venv-win\Scripts\python.exe scripts\filter_presuppositions.py --case use_case
.\.venv-win\Scripts\python.exe main.py --case use_case --step crystallize

# 3. CROSS-SOURCE
.\.venv-win\Scripts\python.exe main.py --case use_case --step relate
.\.venv-win\Scripts\python.exe main.py --case use_case --step debate    # free, no API

# 4. REPORT + METHODOLOGY
.\.venv-win\Scripts\python.exe main.py --case use_case --step assess
.\.venv-win\Scripts\python.exe main.py --case use_case --step methodology

# 5. OPTIONAL
.\.venv-win\Scripts\python.exe main.py --case use_case --step hypothesis
.\.venv-win\Scripts\python.exe main.py --case use_case --step reasoning

# 6. DASHBOARD (free)
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case use_case
```

---

## Pipeline steps (costs only)

| Step | API | ~Cost |
|------|-----|-------|
| `ingest` | Yes | $1–3 |
| `reconcile` | Yes (Haiku on ambiguous pairs) | ~$0.01–0.05 |
| `structure` | Yes | $0.10–0.50 |
| `crystallize` | Yes | $0.05–0.15 |
| `relate` | Yes | $0.05–0.20 |
| `debate` | **No** |: |
| `assess` | Yes | $0.05–0.15 |
| `methodology` | Yes | $0.05–0.15 |
| `hypothesis` | Yes | ~$0.02–0.05/crux (agent + verifier) |
| `reasoning` | Yes | $0.15–0.40 |

See **Step & command guide** above for prompts and files to inspect.

**Flags:**

| Flag | Effect |
|------|--------|
| `--reset-cache` | Ignore cache for this run; new API calls |
| `--demo` | Limit the number of sources ingested (demo default: 3) |
| `--max-chunks N` | Limit ingest chunks |

---

## Git: cases not to push

Some cases (private data, WIP, large corpora) should never land on GitHub. Git cannot read variables from a config file on its own, so this repo uses a **single list you edit** plus a script that writes the matching rules into `.gitignore`.

### 1. Edit the list

Open **`config/local_only_cases.txt`**. One case name per line: must match the folder under `cases/`:

```text
# Cases kept local only: not pushed to GitHub.
use_case
use_case_eduard
use_case_dna

# covid_small          ← commented out = will be pushed
```

Comment out a line with `#` when you want to **include** that case in git again.

### 2. Sync `.gitignore`

```powershell
python scripts/sync_gitignore_cases.py
```

This regenerates the `LOCAL-ONLY CASES` block at the bottom of **`.gitignore`**. Do not edit that block by hand: it gets overwritten on the next sync.

### 3. What gets ignored (per listed case)

| Path | Contents |
|------|----------|
| `cases/<name>/` | `sources.json`, source files, `graph.json`, `compiled/`, `dashboard.html`, `reasoning/`, `hypotheses/`, … |
| `reports/<name>_report.md` | Epistemic Health Report |
| `cache/<name>/` | LLM / extraction cache |
| `logs/<name>_pipeline_*.log` | Logs from `scripts/run_pipeline_logged.ps1` |

Both **inputs** (sources, files) and **pipeline outputs** for that case are covered.

### 4. Already committed?

`.gitignore` only blocks **new** untracked files. If a case was committed before you added it to the list:

```powershell
python scripts/sync_gitignore_cases.py --untrack
git commit -m "Stop tracking local-only cases"
```

Files stay on disk; they are removed from git’s index only.

### 5. Push a case later

1. Comment out its line in `config/local_only_cases.txt`
2. Run `python scripts/sync_gitignore_cases.py`
3. `git add cases/<name>/` (and report if you want it) and commit

**More detail:** `docs/local_only_cases.md`

---

## Project tree

### Repository layout

```
ARGOS/
├── main.py                      # CLI entry
├── explore.py                   # Browse index/graph (terminal)
├── README.md
├── .env                         # ANTHROPIC_API_KEY
│
├── episteme/                    # Core package
│   ├── cli/main.py              # Argument parsing
│   ├── config.py                # VALID_CASES, models, thresholds, QUOTE_REPAIR_MAX_CHARS
│   ├── core/                    # graph, cache, llm, embeddings
│   ├── filters/                 # quote_repair, junk_quote, quote_gate, genericity
│   ├── pipeline/                # ingest, reconcile, structure, relate, assess, runner
│   ├── compile/                 # crystallize, debate_state, references
│   ├── methodology/             # criteria, audit, scoring
│   ├── reasoning/               # expert layer (prompts, runner, context)
│   ├── prompts/                 # LLM prompt templates
│   ├── report/                  # health report generator
│   └── visualize/dashboard.py   # HTML builder (crux reference dedup, subfield groups, badges)
│
├── config/
│   └── local_only_cases.txt     # Case names excluded from git (see docs/local_only_cases.md)
│
├── docs/
│   ├── dashboard_guide.html     # Reader guide: tab/section glossary (static)
│   ├── local_only_cases.md      # Exclude cases from GitHub via config + sync script
│   └── quote_length_limits.md   # Where quotes can be truncated in the pipeline
│
├── scripts/                     # Wrappers (dashboard, cache, relate, sync_gitignore_cases, …)
│
├── cases/{case}/                # Per-case data (see below)
├── cache/{case}/                # LLM response cache (not deliverables)
└── reports/{case}_report.md     # Assess output
```

### Per-case output layout (`cases/{case}/`)

```
cases/{case}/
├── sources.json                 # INPUT: keep, manual (see README § Case inputs)
├── files/                       # INPUT: source texts; youtube_*.txt auto-created at ingest
│
├── profiles/
│   └── case_profile.json        # ingest
├── graph.json                   # ingest → reconcile → structure (graph_backup_*.json on repair)
│
├── compiled/
│   ├── index.json               # crystallize
│   ├── cross_links.json         # relate
│   └── debate_state.json        # relate / debate
│
├── methodology/
│   ├── profile.json             # methodology
│   └── audits/*.json            # methodology (one per paper)
│
├── hypotheses/
│   └── {crux_id}.json           # hypothesis
│
├── reasoning/
│   ├── presuppositions.json     # reasoning
│   ├── open_questions.json      # reasoning
│   ├── field_briefing.md        # reasoning
│   └── devils_advocate/
│       └── {claim_id}.json      # reasoning
│
├── dashboard.html               # build_dashboard.py
└── bibliography/                # optional manifest

cache/{case}/                    # outside cases/: API cache
├── raw/ chunks/ nodes/ agent/ profiles/ doc_summary/ trust/
```

---

## Done? (checklist)

See **Outputs** table above. Quick verify:

```powershell
.\.venv-win\Scripts\python.exe scripts\case_stats.py --case use_case
.\.venv-win\Scripts\python.exe scripts\build_dashboard.py --case use_case
```
