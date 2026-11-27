---
name: reading-10-knowledge-graph
description: >-
  Build verifiable, evidence-grounded scientific literature knowledge graphs
  and answer multi-hop research questions across single or multi-paper corpora
  (including JATS dual-paper bundles and PDFs) with cross-paper alignment,
  non-merge guardrails, incremental v1-to-v2 updates, conflict preservation,
  and deterministic 18-rule quality auditing.
---

# Literature Knowledge Graph Construction & Grounded Subgraph Q&A

Construct a verifiable, incremental scientific knowledge graph and execute provenance-bound subgraph queries across single or multi-paper inputs (Mode A: JATS dual-paper corpus packages; Mode B: PDF/DOCX/LaTeX/HTML/Markdown/TXT).

> **Execution Discipline**: Always run Python with `python -B`. Before writing batches, inspect [`references/kg_ontology.json`](references/kg_ontology.json), [`references/entity_alignment_rules.json`](references/entity_alignment_rules.json), [`references/query_templates.json`](references/query_templates.json), and [`references/schema_dictionary.md`](references/schema_dictionary.md). Budget: **64 requests / 900s / 16,384 tokens**.

---

## 1. Fast 4-Step Execution Workflow (DeepSeek Harness Budget-Safe)

Use [`scripts/kg_pipeline.py`](scripts/kg_pipeline.py) as the single CLI driver (`bootstrap` → `inspect` → `patch` → `finish` → optional `query` / `hotfix`).

### Step 1 — `bootstrap` (Ingest, Index, PubInfo Isolation & Early Fallback)

```bash
python -B /skills/weekly-skill/scripts/kg_pipeline.py bootstrap \
  --input /input \
  --work /app/work \
  --output /app/output \
  --max-packet-tokens 4000
```

Read the compact JSON printed to `stdout`:
- Check `ingest_mode` (`jats_corpus` vs `document_files`) and `locator_scheme` (`jats_paragraph` vs `pdf_page_block`).
- Check `independent_paper_count`, `verification_twins`, `extracted_questions`, and `pubinfo_summary`.
- An honest `partial` fallback `/app/output/result.json` (preserving `case_id` and `output_contract`) is immediately staged.

### Step 2 — `inspect` (Zero-Script Paragraph & Quote Lookup)

Instead of writing scratch Python scripts, grep or filter indexed paragraphs directly:

```bash
python -B /skills/weekly-skill/scripts/kg_pipeline.py inspect \
  --work /app/work \
  --source primary \
  --role results \
  --limit 20
```

Supported filters: `--source <source_id>`, `--role <section_role>`, `--grep <keyword>`, `--anchor <anchor_id>`, `--limit <N>`.
Also inspect `/app/work/anchors_compact.txt` or `/app/work/reading_packets/packet_*.md`. For scanned PDFs, inspect `/app/work/rendered_pages/*.png` if OCR was unavailable or marked `ocr`.

### Step 3 — `patch` Incrementally (`v1_primary` → `v2_secondary_update`)

Submit Paper 1 (`--stage v1_primary`) first, then Paper 2 (`--stage v2_secondary_update`) so `kg_assembler.py` computes an exact `v1 → v2` `incremental_update_diff`:

```bash
python -B /skills/weekly-skill/scripts/kg_pipeline.py patch \
  --work /app/work \
  --batch-file /app/work/batch_v1.json \
  --stage v1_primary

python -B /skills/weekly-skill/scripts/kg_pipeline.py patch \
  --work /app/work \
  --batch-file /app/work/batch_v2.json \
  --stage v2_secondary_update
```

You may also pipe JSON via `--stdin` (`cat batch.json | python -B .../kg_pipeline.py patch --work /app/work --stdin --stage v1_primary`).
Inspect `precheck_warnings` immediately on each `patch` output; fix any unaligned quote or invalid type before proceeding.

### Step 4 — `finish` (Assemble + Query + 18-Rule Audit + Render + Validate)

```bash
python -B /skills/weekly-skill/scripts/kg_pipeline.py finish \
  --work /app/work \
  --output /app/output
```

Read the top-surfaced dashboard JSON:
- If `high_blocker_count > 0` or `validation_passed == false`, inspect `top_high_issues` (`target_id`, `code`, `fix_hint`), submit **1 targeted hotfix batch** (`--stage hotfix` using `replaces_id` / `remove_node_ids` / `remove_edge_ids` / `remove_merge_ids` / `remove_query_ids`), and re-run `finish`.
- Ad-hoc query verification:

```bash
python -B /skills/weekly-skill/scripts/kg_pipeline.py query \
  --work /app/work \
  --question "Compare catalyst outcomes across both papers." \
  --type cross_paper_comparison \
  --entity N-001
```

---

## 2. Non-Negotiable Scientific & JATS Corpus Rules

1. **Dual-Paper Metadata Isolation**:
   - `case.json` root `doi`, `title`, and `authors` belong **exclusively** to `primary`. Never copy them onto `secondary`!
   - `secondary` metadata comes strictly from `corpus-manifest.json`, `secondary-material.json`, or `secondary-publisher.xml`. Missing fields remain `null` (`unreported`).
2. **Verification Twin Exclusion (`secondary-publisher.xml`)**:
   - `secondary-publisher.xml` is a same-paper `verification_twin` of `secondary-material.json` (`is_verification_twin=true`).
   - **NEVER** count it as a 3rd independent paper, and **NEVER** claim JSON + XML of the same paper as cross-paper alignment or cross-paper corroboration (`SAME_PAPER_MULTI_FORMAT_AS_CROSS_PAPER`).
3. **JATS Locator Integrity (`locator_scheme="jats_paragraph"`)**:
   - Cite every node and edge using `source_id`, `paragraph_id`, and a contiguous `verbatim_quote` from `anchors_compact.txt`.
   - JATS bundles have **no PDF page numbers**: keep `page_number` as `null` (or omit it). Never fabricate page `1` (`FAKE_PDF_PAGE_IN_JATS`).
   - Respect `content_limitations`: inline formulas/subscripts are linearized; image pixels, supplementary files, and raw clinical/experimental tables are not provided and must never be guessed.
4. **Controlled Ontology (8 Entities / 35 Relations / 4 Epistemic Levels)**:
   - **8 Entity Types**: `Researcher`, `Material_or_Chemical`, `Method_or_Model`, `Task_or_Problem`, `Dataset_or_Sample`, `Metric_or_Property`, `Experimental_Condition`, `Finding_or_Claim`.
   - **35 Directed Relations**: Attribution (`authored_by`, `uses_method`, `evaluates_on`, `targets_task`, `reports_finding`), Causal (`causes`, `inhibits`, `activates`, `promotes`, `suppresses`, `mediates`, `requires`), Quantitative (`increases`, `decreases`, `has_value`, `no_significant_effect_on`, `achieves_metric`), Comparative (`outperforms`, `underperforms`, `comparable_to`, `differs_from`), Associative (`correlates_with`, `positively_correlates_with`, `negatively_correlates_with`, `associated_with`), Structural (`composed_of`, `part_of`, `located_in`, `measured_by`, `derived_from`, `applicable_to`), Epistemic (`supports`, `contradicts`, `replicates`, `fails_to_replicate`).
   - **4 Epistemic Levels**: `direct_experimental`, `reported_prior_work`, `author_speculation`, `definition_or_setup`.
   - **`co_occurrence_only` is strictly forbidden**. Citations in `introduction`/`references` (`[12]`, `et al.`) must use `reported_prior_work`. Hedged phrasing (`may`, `might`, `suggests`, `correlates with`) must NOT be upgraded to `direct_experimental` causal predicates.
5. **Non-Merge Guardrails & Conflict Preservation**:
   - **Do NOT blindly merge** context-sensitive generic terms across papers or differing conditions (`control group`, `baseline`, `catalyst`, `sample`, `mice`, `patients`, `CNN`, `ResNet`, `transformer`, `dataset`, `accuracy`, `model`, `algorithm`, `对照组`, `催化剂`, `模型`, `数据集`). Split into paper-scoped nodes and log in `non_merge_decisions`.
   - **Synthesis Route & Lifecycle Stage Isolation**: Keep same-formula physical samples prepared by distinct synthesis routes (e.g., ALD vs. sol-gel, dry-pressed vs. sintered) as separate paper-scoped instances (linking only to a shared chemical host if needed), and check experimental lifecycle stages (e.g., formation vs. post-cycling) before cross-paper quantitative comparisons.
   - Only merge across papers when unambiguous identifiers or matching `Full Name (ACR)` expansions exist (record in `merge_decisions`).
   - Preserve opposite findings (`increases` vs `decreases`, distinct numerical values) in `conflicts` with `resolution_policy="preserved_without_averaging"`.
6. **Minimum 3 Grounded Queries & Honest Status**:
   - Execute at least 3 real queries (including `single_hop_fact`, `multi_hop_path`, `cross_paper_comparison`, `conflict_and_disambiguation`, `provenance_reverse_trace`, or `unreported_boundary_check`).
   - `status="success"` is blocked by any `HIGH` blocker, but **allowed** when genuine scientific conflicts (`SCIENTIFIC_CONFLICT_PRESERVED`) or `unreported` boundaries are honestly documented.

---

## 3. Minimal Calling Example (EXAMPLE ONLY — Fictional Data)

```json
{
  "nodes": [
    {
      "node_id": "N-001",
      "canonical_name": "Ni-N-C",
      "entity_type": "Material_or_Chemical",
      "aliases": ["Nickel-Nitrogen-Carbon"],
      "source_ids": ["primary"],
      "context_scope": "900 C pyrolyzed electrocatalyst",
      "evidence": [
        {"source_id": "primary", "paragraph_id": "p-002", "verbatim_quote": "Nickel-Nitrogen-Carbon (Ni-N-C) was synthesized via pyrolysis at 900 C."}
      ]
    },
    {
      "node_id": "N-002",
      "canonical_name": "CO Faradaic efficiency",
      "entity_type": "Metric_or_Property",
      "source_ids": ["primary"],
      "context_scope": "-0.7 V vs. RHE",
      "evidence": [
        {"source_id": "primary", "paragraph_id": "p-003", "verbatim_quote": "Ni-N-C increases CO Faradaic efficiency to 92.4% at -0.7 V vs. RHE."}
      ]
    }
  ],
  "edges": [
    {
      "edge_id": "E-001",
      "subject_id": "N-001",
      "relation": "increases",
      "object_id": "N-002",
      "epistemic_status": "direct_experimental",
      "polarity": "positive",
      "quantitative_value": "92.4%",
      "qualifiers": {"potential": "-0.7 V vs. RHE"},
      "source_id": "primary",
      "evidence": [
        {"source_id": "primary", "paragraph_id": "p-003", "verbatim_quote": "Ni-N-C increases CO Faradaic efficiency to 92.4% at -0.7 V vs. RHE."}
      ]
    }
  ],
  "non_merge_decisions": [],
  "conflicts": [],
  "queries": [
    {
      "query_id": "Q1",
      "query_type": "single_hop_fact",
      "question": "How does Ni-N-C affect CO Faradaic efficiency?",
      "target_entities": ["N-001"]
    }
  ]
}
```

---

## 4. Output Deliverables (`/app/output`)

- `result.json` — Preserved `case_id` & `output_contract`, honest `status`, `documents`, `kg_summary`, `queries`, `merge_decisions`, `non_merge_decisions`, `conflicts`, `incremental_update_diff`, `limitations_and_unreported`, `audit_summary`
- `knowledge_graph.json` — Full aligned graph JSON with `v1 → v2` diff
- `knowledge_graph.graphml` — Valid GraphML 1.0 XML (`edgedefault="directed"`)
- `nodes.csv` & `edges.csv` — UTF-8 (no BOM) tabular exports with verbatim quotes
- `knowledge_graph_report.md` — Bilingual analytical report with Mermaid subgraph & audit breakdown
- `kg_overview.svg` & `kg_overview.png` — Layered visual overview (`kg_overview.png` rendered via 3-tier fallback: `pymupdf` → `Pillow` → built-in stdlib rasterizer)
