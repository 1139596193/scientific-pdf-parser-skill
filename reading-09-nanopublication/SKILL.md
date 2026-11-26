---
name: nanopublication-scientific-qa
description: >-
  High-precision scientific literature reading, Nanopublication atomic assertion
  decomposition, verbatim provenance alignment, and grounded QA synthesis. Use when
  answering research questions from PDF/LaTeX/DOCX/HTML/Markdown papers with strict
  evidence traceability, cross-document conflict detection, W3C JSON-LD/TriG export,
  and anti-hallucination quality gates.
---

# Nanopublication Scientific Reading & Grounded QA

Extract verifiable atomic **Nanopublications** (`head`, `assertion`, `provenance`, `pubinfo`) and **evidence-backed QA answers** from single or multiple scientific documents, without fabricating citations, metadata, numbers, or causal claims.

> **Constraint Reminders**: Execute Python commands with `python -B`. Always inspect references before finalizing predicates/schema: [`references/relation_vocab.json`](references/relation_vocab.json), [`references/schema_dictionary.md`](references/schema_dictionary.md), [`references/evidence_tiers.md`](references/evidence_tiers.md), [`references/nanopub_examples.json`](references/nanopub_examples.json).

---

## Recommended 3-Step Fast Path

For almost all tasks, use the 2-command composite driver [`scripts/nanopub_pipeline.py`](scripts/nanopub_pipeline.py) plus incremental `patch` calls:

### Step 1 — Bootstrap (`ingest` + `index` + `OCR/PNG render` + `pubinfo` + early fallback `result.json`)

```bash
python -B <SKILL_DIR>/scripts/nanopub_pipeline.py bootstrap \
  --input-dir <INPUT_DIR> \
  --work-dir <WORK_DIR> \
  --output-dir <OUTPUT_DIR> \
  --max-packet-tokens 4000
```

**Read the printed single-screen JSON summary**:
- Inspect `anchors_compact_path` (`<WORK_DIR>/anchors_compact.txt`) for quick grep/verbatim quote copying, and read targeted `packet_files` (`<WORK_DIR>/reading_packets/`), `paper_signals.json`, and `pubinfo.json`.
- If a PDF page is scanned, garbled, or visually complex, inspect `rendered_pages` (`<WORK_DIR>/rendered_pages/{doc_id}_p{page:03d}.png`). Anchors extracted via Tesseract carry `src=ocr` in `anchors_compact.txt` and `ocr_warning`; double-check critical numbers against the rendered PNG.
- An honest `partial` `result.json` is already staged in `<OUTPUT_DIR>` for fail-safe delivery.

### Step 2 — Extract Atomic Claims & Patch Incrementally

Create one or more JSON patch files (`<WORK_DIR>/patch_01.json`, `patch_02.json`, ...) using the canonical skeleton below:

```json
{
  "nanopublications": [
    {
      "np_id": "TEMP-01",
      "doc_id": "D1",
      "block_id": "D1-P3-B004",
      "subject": {"label": "Method X", "entity_type": "method"},
      "predicate": "outperforms",
      "object": {"label": "Baseline Y (+3.2% F1)", "entity_type": "metric", "value": 3.2, "unit": "%"},
      "polarity": "affirmed",
      "qualifiers": {
        "conditions": ["5-fold CV"],
        "population_or_dataset": "CIFAR-10",
        "sample_size": "n=50000",
        "statistics": "p < 0.01",
        "temporal_or_dose": null
      },
      "statement": "Method X outperforms Baseline Y by +3.2% F1 on CIFAR-10 (n=50000, 5-fold CV, p < 0.01).",
      "evidence_quote": "Verbatim exact substring copied from block D1-P3-B004.",
      "evidence_type": "direct_empirical",
      "replaces_id": null
    }
  ],
  "remove_np_ids": [],
  "qa_answers": [
    {
      "question_id": "Q1",
      "question": "How does Method X compare to Baseline Y?",
      "answer_status": "answered",
      "answer": "Method X achieves +3.2% higher F1 than Baseline Y on CIFAR-10 (p < 0.01).",
      "supporting_nanopub_ids": ["TEMP-01"],
      "uncertainty_note": null
    }
  ],
  "uncertainty": []
}
```

- Extract table metrics and narrative conclusions as separate atomic claims so the `finish` quantitative conflict graph automatically catches intra-document prose-vs-table discrepancies.

Apply each patch (supports hotfixes via `replaces_id` on an updated claim or `remove_np_ids` to prune erroneous claims):

```bash
python -B <SKILL_DIR>/scripts/nanopub_assembler.py patch \
  --work-state <WORK_DIR>/work_state.json \
  --patch-json <WORK_DIR>/patch_01.json \
  --structure-index <WORK_DIR>/evidence_index.json
```

### Step 3 — Finish (`finalize` + `8-dim audit` + `multi-format render` + `validate`)

```bash
python -B <SKILL_DIR>/scripts/nanopub_pipeline.py finish \
  --work-dir <WORK_DIR> \
  --output-dir <OUTPUT_DIR>
```

Check the printed JSON summary:
- If `top_high_issues` is non-empty or `validate_passed=false`, inspect the exact `target_id` / `fix_hint`, apply a targeted hotfix patch (`replaces_id` / `remove_np_ids`), and re-run `finish` until `HIGH=0` and `validate_passed=true`.

---

## Standalone Scripts Reference (Advanced / Fine-Grained Control)

When debugging a specific stage or inspecting a single file, invoke scripts individually:

```bash
python -B <SKILL_DIR>/scripts/case_ingestor.py --input-dir <INPUT_DIR> --work-dir <WORK_DIR>
python -B <SKILL_DIR>/scripts/structure_indexer.py --input-dir <INPUT_DIR> --work-dir <WORK_DIR> --output-dir <OUTPUT_DIR> --max-packet-words 2800
python -B <SKILL_DIR>/scripts/pubinfo_extractor.py --work-dir <WORK_DIR> --index <WORK_DIR>/evidence_index.json --case-json <INPUT_DIR>/case.json --out <WORK_DIR>/pubinfo.json
python -B <SKILL_DIR>/scripts/nanopub_assembler.py init --ingest-manifest <WORK_DIR>/case_manifest.json --structure-index <WORK_DIR>/evidence_index.json --pubinfo <WORK_DIR>/pubinfo.json --work-state <WORK_DIR>/work_state.json --output-dir <OUTPUT_DIR>
python -B <SKILL_DIR>/scripts/nanopub_assembler.py finalize --work-state <WORK_DIR>/work_state.json
python -B <SKILL_DIR>/scripts/evidence_aligner.py --index <WORK_DIR>/evidence_index.json --quote "<EXACT_QUOTE>" --block-id P1-B001 --doc-id D1
python -B <SKILL_DIR>/scripts/nanopub_auditor.py --work-state <WORK_DIR>/work_state.json --structure-index <WORK_DIR>/evidence_index.json --pubinfo <WORK_DIR>/pubinfo.json --out <WORK_DIR>/audit_report.json
python -B <SKILL_DIR>/scripts/render_deliverables.py --work-state <WORK_DIR>/work_state.json --audit-report <WORK_DIR>/audit_report.json --output-dir <OUTPUT_DIR>
python -B <SKILL_DIR>/scripts/validate_output.py --output-dir <OUTPUT_DIR> --ingest-manifest <WORK_DIR>/case_manifest.json
```

---

## Core Guardrails & Honest Status Rules

1. **Verbatim Provenance Only**: Every `evidence_quote` must be a clean, contiguous 1–4 sentence substring from `evidence_index.json` / `anchors_compact.txt` (do not dump full pages or paraphrase inside quotes). Unknown blocks or missing numbers must never be fabricated.
2. **Causal Restraint**: Only use causal predicates (`causes`, `increases`, `decreases`, `improves`, `degrades`, `inhibits`, `induces`) when the quote contains explicit causal language; observational co-variation must use correlational predicates (`correlates_with`, `associated_with`, etc.).
3. **Prior Work Separation**: Citations inside Introduction/Related Work attributed to `[Author, Year]` / `[12]` must be marked `evidence_type="secondary_citation"` (`T6_secondary_citation`) so prior-art claims are never conflated with primary study findings.
4. **Honest Status Semantics (`source_or_coverage_blocker` vs `analytical_finding`)**:
   - **Blockers** (`scanned_document_unreadable`, `formula_pua_unrecoverable`, `audit_high_issues`, unverified QAs, `QUOTE_NOT_FOUND`): must keep `status="partial"` or `"unanswerable"`.
   - **Analytical Scientific Conflicts** (`category="scientific_conflict"` / `"literature_conflict"` / `"analytical_note"` or recorded in `conflicts[]`): when sources are clean and every QA faithfully answers both sides with evidence, `status` may cleanly be `"success"`.
5. **Preserve Output Contracts**: If `case.json` defines `output_contract`, top-level required fields and nested aliases are automatically populated by `render_deliverables.py` and verified by `validate_output.py`.

---

## Output Deliverables (`<OUTPUT_DIR>`)

- `result.json` — Canonical Nanopubs, Grounded QA, PubInfo, Conflicts, Uncertainty & Contract fields
- `nanopubs.jsonld` — W3C JSON-LD Nanopublication graph export
- `nanopubs.trig` — RDF TriG named-graph export (`Head` / `assertion` / `provenance` / `pubinfo`)
- `nanopubs.csv` — Tabular SPO + qualifiers + alignment summary for human review
- `report.md` — Bilingual research report with 30-second verification guide
- `audit_report.json` — 8-dimension deterministic quality audit breakdown
