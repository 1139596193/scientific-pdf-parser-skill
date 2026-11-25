---
name: academic-literature-translation
description: >
  Deterministic, verifiable scientific literature translation workflow (科研文献翻译、术语全篇锁定、跨页跨栏双语段落与句子级对照、公式图表引用保护、译文六维确定性核验与修订) for Harbor / DeepSeek Harness.
  Use when translating academic papers (PDF, DOCX, Markdown, LaTeX, HTML, TXT) between English and Chinese,
  verifying existing translations, enforcing document-wide glossary consistency, stitching cross-page/cross-column
  paragraphs, protecting equations/citations/numbers/logic polarity, and generating traceable bilingual
  parallel deliverables (with sentence-level `[S1]`/`[S2]` anchors) and automated 6-dimension quality auditing.
---

# Academic Literature Translation Skill

Execute reproducible, high-fidelity scientific translation under strict execution budgets (**64 model requests / 900 seconds**). Every translated unit remains 1:1 anchored to its original page, column, section path, and bounding box, with deterministic verification of terminology, numbers, citations, cross-references, and logical qualifiers.

---

## 1. Environment Constants & Path Convention

Set standard runtime directories before executing commands (Linux/Harbor uses `python3`; Windows local runs may substitute `python`):

```bash
export SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
export INPUT_DIR="${INPUT_DIR:-/input}"
export WORK_DIR="${WORK_DIR:-/app/work}"
export OUTPUT_DIR="${OUTPUT_DIR:-/app/output}"
```

---

## 2. Task Mode Routing (Inspect Inputs First)

Always inspect `$INPUT_DIR/case.json`, `$INPUT_DIR/task.md`, and source files before planning execution:

| Mode | Trigger Condition | Execution Route |
| :--- | :--- | :--- |
| **Mode A: Full Document** (Default) | No page/section restrictions in `case.json`/`task.md`. | Run full Step 1 → Step 5 pipeline. |
| **Mode B: Scoped Translation** | `task.md`/`case.json` specifies pages (e.g., `pages 1-2`) or sections (`Abstract`, `Section 2`). | `case_ingestor.py` auto-filters in-scope units; translate only `in_scope == true` batches. |
| **Mode C: Audit & Repair Existing Translation** | Input already provides both source text and candidate translation to review/verify/revise. | Run Step 1 (`case_ingestor.py`) → optional glossary compile + `entity_guard.py --work-dir "$WORK_DIR"` → merge draft via `merge_checkpoint.py --add-batch` → Step 4 (`translation_verifier.py` + repair) → Step 5. |
| **Mode D: ZH→EN / Mixed Language** | Source document is Chinese or mixed ZH/EN. | `case_ingestor.py` auto-detects `zh->en` direction; maintain identical glossary lock & 6-dimension audit rules. |

**Mode C Direct Execution Shortcut** (when candidate draft translations are already supplied in input):
```bash
python3 "$SKILL_DIR/scripts/case_ingestor.py" --input-dir "$INPUT_DIR" --work-dir "$WORK_DIR"
python3 "$SKILL_DIR/scripts/entity_guard.py" --work-dir "$WORK_DIR"
# Map supplied draft translations to [{"unit_id": ..., "translated_text": ...}] in $WORK_DIR/draft_out.json, then:
python3 "$SKILL_DIR/scripts/merge_checkpoint.py" --work-dir "$WORK_DIR" --output-dir "$OUTPUT_DIR" --input-dir "$INPUT_DIR" --add-batch "$WORK_DIR/draft_out.json"
# Proceed straight to Step 4 (translation_verifier.py + surgical repair) and Step 5.
```

---

## 3. Six Inviolable Rules

1. **Read Real Inputs First**: Never infer scientific data, titles, or facts from filenames or domain labels. Inspect `$INPUT_DIR/case.json`, `$INPUT_DIR/task.md`, and actual extracted units.
2. **Faithful Sentence-by-Sentence Translation**: Never substitute summaries, paraphrases, or "gist" for complete translation of every sentence in each unit.
3. **Hard Enforcement of Glossary & Entity Fingerprints**: Strictly follow canonical targets in `/app/work/glossary.json` and preserve every token listed in each unit's `guard_hints` (`must_keep_numbers_units`, `must_keep_citations`, `must_keep_cross_refs`, `must_keep_inline_math`, `high_risk_logic`).
4. **Zero Fabrication on Unreadable Assets**: When encountering missing text layers, blurry scans, image-only formulas, or garbled fonts (`manual_review_asset`), never invent content; retain `untranslatable_asset` markers and declare honest `uncertainty`.
5. **Early-Drop Checkpoint After Every Batch**: Immediately run `merge_checkpoint.py` after finishing each batch so `$OUTPUT_DIR/result.json` always holds an up-to-date, valid deliverable.
6. **Respect Budget Ceiling (64 Requests / 900s)**: If remaining request budget drops low (< 8 requests left) or time exceeds 700s, stop translating new batches, run `translation_verifier.py` on completed units, render honest `status="partial"` deliverables, and pass `validate_output.py`.

---

## 4. Five-Step Execution Workflow

### Step 1: Ingest, Diagnose & Adaptive Batch Slicing

Extract standardized atomic units, resolve cross-page/cross-column paragraphs, detect external glossaries, and pack batches (auto-capped to `<= 18` batches to protect the 64-request budget):

```bash
python3 "$SKILL_DIR/scripts/case_ingestor.py" \
  --input-dir "$INPUT_DIR" \
  --work-dir "$WORK_DIR" \
  --max-batch-words 1600 \
  --max-batch-units 25 \
  --target-max-batches 18
```

Inspect `$WORK_DIR/ingest_manifest.json` immediately to verify `language_direction`, `scope_constraint`, `batch_count`, `glossary_files_detected`, and `diagnosis_summary`.
- **Fast-Finish Shortcut (`batch_count == 0`)**: If `batch_count == 0` (e.g., unreadable scan or pure `manual_review_asset` fallback with no translatable units), skip Steps 2–4 and jump straight to **Step 5** (`render_deliverables.py --final` + `validate_output.py --strict`) to deliver honest `status="unable_to_process"` and `uncertainty` without wasting budget.

---

### Step 2: Candidate Term Extraction, Locking & Fingerprint Injection

1. **Extract candidate terms** (and automatically load any external `.csv`/`.tsv`/`.json`/`.md` glossary as `external_locked`):

```bash
python3 "$SKILL_DIR/scripts/term_extractor.py" extract \
  --work-dir "$WORK_DIR" \
  --max-terms 60
```

2. **Confirm & Lock Glossary (`$WORK_DIR/glossary.json`)**:
   Read `$WORK_DIR/term_candidates.json` (and `$WORK_DIR/glossary.json` if pre-populated by an external glossary). In **one single decision pass**, write/update `$WORK_DIR/glossary.json` conforming to `$SKILL_DIR/templates/glossary_template.json` for high-frequency core domain terms (typically 15–35 terms). Ensure every entry specifies `term_id`, `source_term`, `aliases`, `canonical_target`, `forbidden_targets`, `do_not_translate`, and `rationale`.

3. **Compile Glossary & Enrich Batches**:

```bash
python3 "$SKILL_DIR/scripts/term_extractor.py" compile \
  --work-dir "$WORK_DIR" \
  --glossary "$WORK_DIR/glossary.json"

python3 "$SKILL_DIR/scripts/entity_guard.py" \
  --work-dir "$WORK_DIR" \
  --enrich-batches
```

---

### Step 3: Batch-by-Batch Faithful Translation & Immediate Checkpointing

Process each `$WORK_DIR/batches/batch_XXX.json` sequentially:
- **Context & Sentence-Boundary Rules**: Read `context_tail` only for semantic continuity; **DO NOT** include `context_tail` units in your output JSON. Read `batch_glossary` and each unit's `guard_hints` carefully, and preserve clean 1:1 sentence boundaries so multi-sentence units align cleanly at `[S1]`..`[Sn]`.
- **Output Schema**: Write `$WORK_DIR/batch_XXX_out.json` as a clean JSON array (no conversational prefixes like "Here is the translation"):

```json
[
  {
    "unit_id": "U0005",
    "translated_text": "摘要——差分隐私已成功扩展到量子计算与信息处理领域……",
    "rationale": "Locked 'differential privacy' -> '差分隐私'; preserved negation '无法' and qualifier '仅'.",
    "uncertainty": ""
  }
]
```

- **Checkpoint Immediately After Every Batch**:

```bash
python3 "$SKILL_DIR/scripts/merge_checkpoint.py" \
  --work-dir "$WORK_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --input-dir "$INPUT_DIR" \
  --add-batch "$WORK_DIR/batch_001_out.json"
```

---

### Step 4: Deterministic 6-Dimension Audit & Surgical Repair (Max 2 Rounds)

Once all batches (or budgeted subset) are merged, audit `$WORK_DIR/merged_translations.json`:

```bash
python3 "$SKILL_DIR/scripts/translation_verifier.py" \
  --work-dir "$WORK_DIR" \
  --translations "$WORK_DIR/merged_translations.json"
```

If `$WORK_DIR/verification_report.json` reports `error_count > 0` or high-risk `LOGIC_NEGATION_RISK` / `LOGIC_QUALIFIER_RISK` warnings:
1. Read `$WORK_DIR/repair_queue.json` (contains only failing units with `issues`, `required_terms`, and `required_entities`).
2. Write targeted fixes to `$WORK_DIR/repair_patch_r1.json` (same JSON array schema; optionally include `"trigger_codes": [...]` and `"repair_reason": "..."`).
3. Apply patch & re-verify (limit to max 2 repair rounds):

```bash
python3 "$SKILL_DIR/scripts/merge_checkpoint.py" \
  --work-dir "$WORK_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --input-dir "$INPUT_DIR" \
  --apply-repair "$WORK_DIR/repair_patch_r1.json"

python3 "$SKILL_DIR/scripts/translation_verifier.py" \
  --work-dir "$WORK_DIR" \
  --translations "$WORK_DIR/merged_translations.json"
```

---

### Step 5: Final Rendering & Strict Contract Validation

Generate final multi-view deliverables (`result.json`, `bilingual_parallel.md`, `glossary.md`, `glossary.json`, `verification_report.md`) and pass hard validation:

```bash
python3 "$SKILL_DIR/scripts/render_deliverables.py" \
  --work-dir "$WORK_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --input-dir "$INPUT_DIR" \
  --final

python3 "$SKILL_DIR/scripts/validate_output.py" \
  --output-dir "$OUTPUT_DIR" \
  --input-dir "$INPUT_DIR" \
  --strict
```

Ensure `validate_output.py` exits with code `0` before completing the task.
- **Check Extra Case Deliverables**: Inspect `case.json` / `task.md` to confirm whether any additional standalone output filename or Q&A field was explicitly requested beyond the standard deliverables; if so, write and deliver those under `$OUTPUT_DIR` as well.

---

## 5. Exception Handling Playbook

| Anomaly / Edge Condition | Automated Detection Signal | Mandatory Action |
| :--- | :--- | :--- |
| **Scanned PDF / Missing Text Layer** | `dominant_route == "scanned_needs_ocr"` | `case_ingestor.py` attempts local OCR first; if unavailable or garbled, outputs `untranslatable_asset` (`manual_review_asset`). **Do not fabricate text**; report honestly in `uncertainty` & `status`. |
| **Garbled / Custom Font Encoding** | `source_warnings` contains `potential_font_garble` | Translate surrounding context carefully, flag uncertain symbols in unit `uncertainty`, and list exact page/unit in manual review checklist. |
| **Image-Only Equations / Tables** | Missing math body or `manual_review_asset` | Preserve visible equation IDs/captions; note visual-only asset limitation without inventing formulas. |
| **Ultra-Long Paper (> 25k words)** | `batch_count` auto-scaled to `<= 18` | Translate normally; monitor request budget. If budget nears limit, stop early and run Step 4+5 to yield `status="partial"` cleanly. |
| **References / Display Equations** | `translate_policy == "preserve_source"` | Never re-translate display equations or reference entries unless explicitly requested in `task.md`/`case.json`. |
| **Mixed Language Source** | EN terms inside ZH or ZH abstracts in EN papers | Keep `glossary.json` aligned with dominant direction; preserve ASCII standard acronyms (`do_not_translate: true`). |

---

## 6. Dependencies, Bundled Scripts & Output Files

- **Zero-Crash Python Design**: All scripts under `$SKILL_DIR/scripts/` import **Python 3 standard libraries only at top level**. Optional PDF/OCR backends (`PyMuPDF/fitz`, `pdfplumber`, `pytesseract`, `tesseract`, `pdftotext`) are dynamically probed inside functions with graceful fallbacks.
- **Bundled Resources**:
  - `scripts/case_ingestor.py` + `scripts/pdf_engine/` (`pdf_diagnose.py`, `layout_parser.py`, `table_formula_ocr.py`)
  - `scripts/term_extractor.py`, `scripts/entity_guard.py`, `scripts/translation_verifier.py`
  - `scripts/merge_checkpoint.py`, `scripts/render_deliverables.py`, `scripts/validate_output.py`
  - `resources/academic_invariants.json` (negation/qualifier/concession dictionaries, SI units, hygiene patterns)
  - `templates/glossary_template.json` (reusable glossary schema)
- **Delivered Files under `$OUTPUT_DIR`**:
  - `result.json` — Machine-readable payload with `case_id`, all `output_contract` fields (including nested JSON Schema objects), `status`, `sources`, `uncertainty`, `change_records`, `glossary`, `verification_summary`, and `bilingual_alignment` (with per-unit `sentence_alignment`).
  - `bilingual_parallel.md` — Section-structured 1:1 parallel alignment document with page/anchor markers (`[U0009 | p.1 | paragraph]`), sentence-level `[S1]`/`[S2]` alignment pairs for multi-sentence units, locked terms, rationales, and explicit `[⚠️ 缺失文字层/模糊资产，需人工复核]` tags.
  - `glossary.md` & `glossary.json` — Document-wide locked terminology table with first-seen anchors and forbidden mistranslations.
  - `verification_report.md` — Quantitative coverage, term consistency, entity fidelity rates, revision history, and manual review checklist.

---

## 7. Minimal End-to-End Invocation Example (Illustrative Only)

Given a local test folder `/tmp/demo_case/input` containing a Markdown abstract `paper.md` and `case.json`:

```bash
WORK=/tmp/demo_case/work
OUT=/tmp/demo_case/output
IN=/tmp/demo_case/input

python3 "$SKILL_DIR/scripts/case_ingestor.py" --input-dir "$IN" --work-dir "$WORK"
python3 "$SKILL_DIR/scripts/term_extractor.py" extract --work-dir "$WORK"
# Write $WORK/glossary.json based on term_candidates.json, then compile & enrich:
python3 "$SKILL_DIR/scripts/term_extractor.py" compile --work-dir "$WORK" --glossary "$WORK/glossary.json"
python3 "$SKILL_DIR/scripts/entity_guard.py" --work-dir "$WORK" --enrich-batches
# After translating batch_001.json -> $WORK/batch_001_out.json:
python3 "$SKILL_DIR/scripts/merge_checkpoint.py" --work-dir "$WORK" --output-dir "$OUT" --input-dir "$IN" --add-batch "$WORK/batch_001_out.json"
python3 "$SKILL_DIR/scripts/translation_verifier.py" --work-dir "$WORK" --translations "$WORK/merged_translations.json"
python3 "$SKILL_DIR/scripts/render_deliverables.py" --work-dir "$WORK" --output-dir "$OUT" --input-dir "$IN" --final
python3 "$SKILL_DIR/scripts/validate_output.py" --output-dir "$OUT" --input-dir "$IN" --strict
```

Illustrative `result.json` snippet (example structure, not a benchmark score):

```json
{
  "case_id": "example_case_001",
  "status": "success",
  "progress": {"completed_units": 6, "total_translatable_units": 6, "total_units": 8},
  "sources": {"dominant_format": "markdown"},
  "uncertainty": [],
  "change_records": [],
  "verification_summary": {"audit_status": "PASS", "metrics": {"coverage_rate": 1.0, "term_consistency_rate": 1.0}}
}
```

