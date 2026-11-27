# Academic Language Polishing Skill (`academic-language-polish`)

Deterministic, verifiable, zero-fabrication academic language polishing skill built for Harbor / DeepSeek Harness (`writing-02` / `product-arena-02-7051d92d`).

---

## 1. Architecture Overview

```text
+-----------------------------------------------------------------------------+
|                        /input (case.json, task.md, docs)                    |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
| 1. bootstrap (polish_pipeline.py -> case_ingestor.py + pdf_engine)          |
|   - Multi-format ingest: PDF, DOCX (pure zipfile), LaTeX, MD, HTML, TXT     |
|   - Task routing: polish | bilingual_polish | audit_only | qa_guard         |
|   - Adaptive budget packet slicer (reading_packets/batch_01.json ...)       |
|   - Anomaly probes: encoding, CJK+JP mix, placeholders, glossary conflicts  |
|   - Early-drop fallback result.json staged immediately in /app/output       |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
| 2. inspect -> 3. patch (incremental batch polishing + real-time guard)      |
|   - epistemic_entity_guard.py : 5-tier hedging ladder, polarity, invariants |
|   - glossary_abbr_guard.py    : locked terms, conflicts, Abstract/Body abbr |
|   - diff_engine.py            : mixed EN/ZH/LaTeX diff_ops & ledger checks  |
|   - style_linter.py           : register, Chinglish, punctuation, meta-leak |
+--------------------------------------+--------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
| 4. finish (polish_verifier.py + render_deliverables.py + validate_output.py)|
|   - 8-dimension deterministic audit report & honest status gatekeeper       |
|   - Deliverables: result.json, polished_manuscript.<ext>, revision_ledger.* |
|                   redline_diff.html, polished_clean.docx,                   |
|                   polished_redline.docx (OOXML <w:del>/<w:ins> + ledger)    |
|   - 4-tier PDF/PNG visual previews (LibreOffice -> PyMuPDF -> PIL -> stdlib)|
+-----------------------------------------------------------------------------+
```

---

## 2. 8-Dimension Deterministic Audit (`polish_verifier.py`)

| Dimension | Focus | Key Issue Codes |
|---|---|---|
| 1. Coverage & Scope | Full `in_scope` coverage, no unauthorized `qa_guard`/`audit_only` edits | `COVERAGE_INCOMPLETE`, `OUT_OF_SCOPE_MODIFIED`, `QA_GUARD_VIOLATION` |
| 2. Invariants | Numbers, stats (`p = 0.042`), units, citations, cross-refs, `$...$` balance | `NUMBER_UNIT_MISSING`, `CITATION_MISSING`, `CROSS_REF_MISSING`, `MATH_LATEX_MISSING`, `MATH_DELIMITER_BROKEN` |
| 3. Epistemic Fidelity | 5-tier hedging ladder, negation polarity (with safe double-neg whitelist), scope bounds, comparison direction | `CLAIM_STRENGTH_ESCALATION`, `CLAIM_STRENGTH_WEAKENING`, `POLARITY_FLIP`, `SCOPE_BOUND_DROPPED`, `DIRECTION_FLIP` |
| 4. Terminology & Spelling | Locked terms, forbidden terms, internal glossary conflict escalation, `Fig.`/`Figure` & US/UK spelling consistency | `GLOSSARY_CONFLICT_MULTI_TARGET`, `GLOSSARY_CONFLICT_SELF_CONTRADICTION`, `GLOSSARY_CONFLICT_ABBR`, `FORBIDDEN_TERM_USED`, `LOCKED_TERM_MISMATCH`, `SPELLING_VARIANT_MIXED` |
| 5. Abbreviation Lifecycle | Separate Abstract/Body first-mention `Full Term (ABBR)` tracking with scientific whitelist | `ABBR_UNDEFINED`, `ABBR_REDEFINED`, `ABBR_CONFLICT_EXPANSION`, `ABBR_FULL_FORM_REPEATED` |
| 6. Diff & Edit Ledger | Real token diff alignment, anti-phantom edit check, `minimal`/`standard` edit-ratio guard | `UNLOGGED_CHANGE`, `PHANTOM_EDIT_LOG`, `OVER_EDIT_RISK`, `INVALID_EDIT_TYPE` |
| 7. Register & Hygiene | Colloquialisms, Chinglish (`Because...so`), punctuation width, zero LLM meta-leakage | `META_LEAKAGE`, `PUNCT_WIDTH_MISMATCH`, `EN_*`, `ZH_*` |
| 8. Bilingual & Uncertainty | Bilingual alignment completeness & explicit escalation of unresolved anomalies | `BILINGUAL_ALIGNMENT_MISSING`, `PLACEHOLDER_UNACKNOWLEDGED` |

---

## 3. CLI Usage & Output Deliverables

All commands are invoked via `scripts/polish_pipeline.py` using `python3 -B`:

- `bootstrap --input <case_or_file> --work-dir <dir> --out-dir <dir> [--task <task.md>] [--glossary <file>] [--style <profile>] [--intensity <minimal|standard|deep>] [--mode <auto|polish|bilingual_polish|audit_only|qa_guard>]`
- `inspect --work-dir <dir> --view <overview|batch|issues|glossary|sentences> [--batch-id <id>] [--ids <S001,S002>]`
- `patch --work-dir <dir> [--file <patch.json> | --stdin] [--replaces-id <S001>]`
- `finish --work-dir <dir> --out-dir <dir> [--allow-partial]`

Standalone output validator:
- `python3 -B scripts/validate_output.py --out-dir <dir> [--case <case.json>] [--strict]`

Generated outputs in `--out-dir`:
1. `result.json` — preserves `case_id`, nested `output_contract`, honest `status`, `polished_text`, `edit_log`, `audit_report`, `glossary_conflicts`, `uncertainty`, `sources`, `preview_render_tier`, `deliverables`.
2. `polished_manuscript.<ext>` — lossless reconstructed `.md` / `.tex` / `.txt`.
3. `revision_ledger.md` & `revision_ledger.csv` — section-grouped & spreadsheet-ready sentence audit tables.
4. `redline_diff.html` — self-contained offline redline review page (zero external CDNs).
5. `polished_clean.docx` & `polished_redline.docx` — pure-stdlib OOXML Word files (`<w:del>` / `<w:ins>` Track Changes + embedded revision ledger table).
6. `audit_report.json` — complete 8-dimension audit breakdown.
7. `redline_preview.pdf` (Tier 1–2) & `redline_preview_page1.png` (Tier 1 / 2 / 2.5 / 3 / 4 fallback chain).

---

## 4. Customization & Resource Extension

- **External Glossaries**: Supply `.csv`, `.tsv`, `.json`, or `.md` tables following [resources/glossary_template.md](resources/glossary_template.md) (`source_term`, `preferred_term`, `forbidden_terms`, `do_not_edit`, `abbr`, `notes`).
- **Journal Style Profiles**: Extend or select profiles in [resources/style_profiles.json](resources/style_profiles.json) (`nature_science`, `ieee_acm`, `elsevier_general`, `medical_clinical`, `zh_core_journal`, `general_academic`).
- **Epistemic Ladder & Edit Taxonomy**: Configured in [resources/epistemic_hedging_ladder.json](resources/epistemic_hedging_ladder.json) and [resources/edit_taxonomy.json](resources/edit_taxonomy.json).

---

## 5. Scope & Known Limitations

- **Zero External Dependency Core**: All Python modules use only standard-library top-level imports. Optional libraries (`pymupdf`, `pdfplumber`, `PIL`) and CLIs (`soffice`/`libreoffice`, headless Chromium/Edge) are probed dynamically at runtime with graceful fallbacks.
- **Scanned PDFs Without Text Layer**: If OCR tools are unavailable and a PDF lacks an extractable text layer, the pipeline halts safely with `status="blocked"` and `NO_EDITABLE_SENTENCES` without fabricating content.
- **Double-Negation Simplification**: Standard academic double-negation simplifications (e.g., `not uncommon -> common`) and Chinese non-negation lexicalized words (`非常不错`, `不仅如此`, `未来`) are handled without false `POLARITY_FLIP` alerts; non-standard polarity rewrites require explicit `"double_neg_allowed": true` with justification.

---

## Local & Independent Evaluation (Non-Official)

> **Important Note (Non-Official)**: All measurements below are from local offline regression tests, real-corpus stress benchmarks, and independent external cold-start A/B evaluations. They are **not** official Harbor evaluation scores.

### 1. Offline Zero-Dependency Regression (`scripts/self_test.py`)
- **54 / 54 PASS** (`T01`–`T54`) covering knowledge base integrity, lossless multi-format ingestion (TXT/MD/LaTeX/HTML/pure-stdlib DOCX), budget-aware adaptive batching (900 sentences compressed into `<=24` packets), 5-tier epistemic & polarity guards, invariant zero-tampering, multi-format glossary conflict detection, Abstract/Body abbreviation governance, Unicode-lossless diff engine, pure-stdlib OOXML Track Changes (`<w:del>`/`<w:ins>`), 5-tier PNG/PDF preview rendering, `SKILL.md`/`README.md` AST CLI zero-drift verification, and Harbor packaging compliance (`21` files, `<= 450 KB` total).

### 2. Real-Corpus High-Stress & Determinism Benchmarks
- **S1 — Real English arXiv PDF + Scope + Conflict + Hotfix + 3x `EXACT_MATCH`**: 3 independent end-to-end runs (`18.95s`, `18.38s`, `18.50s`; mean `18.61s`) achieved **3x SHA256 `EXACT_MATCH`** across `result.json`, `audit_report.json`, `polished_manuscript.md`, `revision_ledger.csv`, and normalized `polished_redline.docx::word/document.xml`, with round-1 escalation/number-drop interception and `--replaces-id` hotfix closure.
- **S2 — Real Chinese Academic PDF + CJK Visual Verification**: Verified full CJK preservation (`890` Han chars in clean DOCX, `2,656` in redline DOCX, `839` extracted back from `redline_preview.pdf`) and real red/green revision rendering (`1323x1872` PNG, non-white ratio `0.6334`, `5,918` red deletion & `5,623` green insertion pixels).
- **S3 — Complex LaTeX Structural Integrity**: `100.0` audit score, balanced braces/delimiters, and intact `\begin{equation}`, `\cite{...}`, `\ref{...}`, and `$...$`.
- **S4 — Adversarial PDFs & Mode Guardrails**: Corrupt/blank scanned PDFs halt safely as `blocked`; `qa_guard` and `audit_only` enforce 100% verbatim source preservation with explicit anomaly disclosure (`ENCODING_GARBLED`, `MIXED_CJK_JP`, `MISSING_MATERIAL_PLACEHOLDER`).

### 3. Independent External Cold-Start A/B Evaluation (WorkBuddy, 3 Cases × 6 Dimensions)

Evaluated using a symmetric 100-point objective rubric (`D1` Invariants 20 + `D2` Epistemic/Scope 15 + `D3` Edit Ledger 15 + `D4` Terminology/Style 15 + `D5` Boundaries/Anomalies 15 + `D6` Contract/Deliverables 20):

| Case ID | Baseline (No Skill) | Skill v1 / v2 | Delta | Key Baseline Gaps Observed |
|---|---:|---:|---:|---|
| `case_01_en_latex` | 76.0 | 100.0 | +24.0 | Mixed US/UK spelling, contraction `don't` left, out-of-scope References edited, 7 inconsistent `edit_log` rows |
| `case_02_zh_bilingual` | 90.0 | 100.0 | +10.0 | 5 inconsistent `edit_log` rows, over-optimistic `status` |
| `case_03_boundary_guard` | 84.0 | 100.0 | +16.0 | Missing `.docx`/preview deliverables, over-optimistic `status='completed'` |
| **Mean** | **83.33** | **100.0** | **+16.67** | — |

### 4. Engineering Hardening Driven by v1 → v2 Cold-Start Runs
- **Negation Contractions & Chinese Non-Negation Words**: Added full English negation contractions (with curly apostrophe normalization) and masked Chinese lexicalized non-negation phrases (`非常不错`, `不仅如此`, `未来`) to eliminate false `POLARITY_FLIP` alerts.
- **Abbreviation Regex & Placeholder Hygiene**: Required CJK presence in `_ZH_DEF_RE`, deduplicated `(abbr, span)`, and whitelisted missing-data placeholders (`XX`, `TBD`) from triggering `ABBR_UNDEFINED`.
- **Nested Contract & Actionable Issue Separation**: Prioritized semantic matching before empty `{}`/`[]` recursion in `output_contract` (`deliverables`, `audit_summary`, `human_confirmation_items`, `bilingual_abstract`), separated `repairable: false` document advisories from `actionable_high_count`, added `Suggested Revision` / `Audit Notes` columns for `audit_only` ledgers, and introduced **Tier 2.5 headless browser HTML→PNG** rendering (`1280x960` full CJK fidelity across all 3 v2 blind cases).
