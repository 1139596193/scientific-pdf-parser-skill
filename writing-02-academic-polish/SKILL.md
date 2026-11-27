---
name: academic-language-polish
description: >-
  Deterministic, verifiable academic language polishing skill for English and Chinese
  research manuscripts, grant proposals, and reviewer responses (学术语言润色、期刊语域改写、
  术语与缩写全篇一致化、5 级认识论限定强度守恒、逐句可核验修改台账、纯标准库 Word 修订红线稿与四级预览).
  Use when asked to polish, copyedit, proofread, or audit academic prose across PDF, DOCX,
  LaTeX, Markdown, HTML, or TXT inputs with sentence-level diff ledgers and Track Changes DOCX.
  Do NOT trigger prose rewriting when the user asks a pure content Q&A (qa_guard) or requests
  review/diagnosis only without altering original text (audit_only).
---

# Academic Language Polishing Skill

Deliver publication-grade, source-faithful academic polishing under Harbor's execution budget (**64 model requests / 900 seconds / 16,384 tokens**). Every edited sentence produces a deterministic token diff, structured edit taxonomy tags, concrete rationale, and zero-tampering verification against numbers, units, citations, cross-references, formulas, and epistemic hedging strength.

---

## 1. Environment & Path Convention

Use `python3 -B` on Linux/Harbor (`python -B` on Windows) to prevent `__pycache__` pollution:

```bash
export SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
export INPUT_DIR="${INPUT_DIR:-/input}"
export WORK_DIR="${WORK_DIR:-/app/work}"
export OUTPUT_DIR="${OUTPUT_DIR:-/app/output}"
```

---

## 2. Four Iron Laws & Mode Routing

### Four Iron Laws
1. **改得对 (Accuracy)**: Fix real grammar, syntax, collocation, punctuation, and Chinglish errors using `resources/edit_taxonomy.json` tags rather than cosmetic paraphrasing.
2. **说得像 (Register & Consistency)**: Align with the target profile in `resources/style_profiles.json` (`nature_science`, `ieee_acm`, `elsevier_general`, `medical_clinical`, `zh_core_journal`, `general_academic`), enforce locked terms, and govern `Full Term (ABBR)` separately across Abstract and Body.
3. **没改坏 (Zero-Tampering Invariants)**: Never alter numbers, statistical values (`p = 0.042`), units, citations (`\cite{...}`, `[1]`), cross-references (`Fig. 2b`, `Eq. (3)`), math delimiters (`$...$`), negation polarity, scope bounds (`respectively`, `only`, `仅`, `分别`), comparison direction, or the **5-tier epistemic hedging ladder** (`resources/epistemic_hedging_ladder.json`).
4. **讲得清 (Traceability)**: Every modified sentence must declare non-empty `edit_types` and `reason` matching the true token diff; unedited sentences remain verbatim (`minimal_preserve`).

### Mode Routing (Auto-Detected in `bootstrap`)
| Mode | When Activated | Required Behavior |
| :--- | :--- | :--- |
| `polish` | Default academic polishing task | Edit `in_scope` sentences per `edit_intensity` (`minimal`/`standard`/`deep`). |
| `bilingual_polish` | Bilingual / parallel EN-ZH polishing requested | Preserve alignment and populate `bilingual_counterpart` per sentence. |
| `audit_only` | Review/diagnose only ("仅审校", "do not rewrite") | Keep `polished_text` verbatim; record suggestions in `suggested_text` / `audit_notes`. |
| `qa_guard` | Pure content Q&A without edit directive | Do NOT rewrite manuscript; answer question directly from source material. |

---

## 3. Fast 4-Step Execution Protocol

Use `scripts/polish_pipeline.py` as the single CLI driver (`bootstrap` → `inspect` → `patch` → `finish`).

### Step 1 — `bootstrap` (Ingest, Audit Glossary, Slice Packets & Stage Early Fallback)
```bash
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" bootstrap \
  --input "$INPUT_DIR" \
  --work-dir "$WORK_DIR" \
  --out-dir "$OUTPUT_DIR"
```
Optional overrides when explicitly specified by user or `task.md`: `--task`, `--glossary`, `--style`, `--intensity` (`minimal|standard|deep`), `--mode` (`auto|polish|bilingual_polish|audit_only|qa_guard`).
- Inspect the compact JSON printed to stdout: verify `execution_mode`, `target_style`, `in_scope_sentences`, `batch_count`, `glossary_conflict_count`, and `anomaly_codes`.
- A fallback `result.json` (`status="partial"` or `"blocked"`) is immediately staged in `$OUTPUT_DIR`.
- If `in_scope_sentences == 0` or `execution_mode` is `qa_guard`, jump straight to Step 4 (`finish`).

### Step 2 — `inspect` (Read-Only Packet & Glossary Lookup)
```bash
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" inspect \
  --work-dir "$WORK_DIR" \
  --view overview
```
Inspect each reading packet (`batch_01`, `batch_02`, ...) and glossary audit:
```bash
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" inspect \
  --work-dir "$WORK_DIR" \
  --view batch \
  --batch-id batch_01

python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" inspect \
  --work-dir "$WORK_DIR" \
  --view glossary
```
Supported `--view` values: `overview`, `batch` (with `--batch-id`), `issues`, `glossary`, `sentences` (with `--ids S001,S002`).

### Step 3 — `patch` (Submit Batch Edits & Real-Time Guard Check)
For each packet, produce a clean JSON array (or `{"edits": [...]}`) containing only `sentence_id`, `polished_text`, `edit_types`, `reason` (plus optional `needs_human_review`, `human_review_note`, `bilingual_counterpart`, `double_neg_allowed`):
```json
[
  {
    "sentence_id": "S001",
    "polished_text": "These results may partly explain the observed resistance.",
    "edit_types": ["academic_register", "collocation"],
    "reason": "Replaced informal phrasing while preserving Tier-2 'may partly' hedging."
  }
]
```
Submit via `--file` or `--stdin`:
```bash
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" patch \
  --work-dir "$WORK_DIR" \
  --file "$WORK_DIR/patch_01.json"
```
Check `immediate_high_issues` immediately. If budget runs low (`< 8` requests or `> 700s`), stop patching new batches and run `finish --allow-partial`.

### Step 4 — `finish` (8-Dim Audit, Render Deliverables & Previews, Validate)
```bash
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" finish \
  --work-dir "$WORK_DIR" \
  --out-dir "$OUTPUT_DIR"
```
- If `actionable_high_count > 0` or `validation_passed == false`, inspect `actionable_top_high_issues` (`sentence_id`, `code`, `repair_hint`), apply **at most 1 targeted hotfix round** with `patch --work-dir "$WORK_DIR" --file "$WORK_DIR/hotfix.json" --replaces-id S001,S002`, and re-run `finish`.
- For `non_repairable_advisories` (`repairable: false`, such as document-level `GLOSSARY_CONFLICT_*` internal conflicts), **do NOT** fabricate a resolution or overwrite source terms; leave them surfaced in `uncertainty` / `human_confirmation_items` for human decision.
- Add `--allow-partial` only when exiting early under budget limits.

---

## 4. Exception Boundaries (Zero Fabrication Rule)

- **Conflicting Glossary Entries**: Never silently overwrite `GLOSSARY_CONFLICT_MULTI_TARGET`, `GLOSSARY_CONFLICT_SELF_CONTRADICTION`, or `GLOSSARY_CONFLICT_ABBR`. Surface conflicts in `uncertainty` (`needs_human_review: true`).
- **Missing Material / Placeholders**: Never invent data for `[TBD]`, `[TODO]`, `???`, or `XX%`. Keep placeholders intact and set `needs_human_review: true`.
- **Garbled Encoding / CJK-JP Mixing / Unspecified Style**: Retain `ENCODING_GARBLED` / `MIXED_CJK_JP` / `TARGET_STYLE_UNSPECIFIED` flags in `uncertainty`; never guess unreadable spans.
- **No LLM Meta-Leakage**: Never prepend conversational text ("Here is the polished sentence:") inside `polished_text`.

---

## 5. Minimal Calling Example (EXAMPLE ONLY — Fictional Data)

```bash
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" bootstrap --input ./example_case --work-dir ./work --out-dir ./out --style ieee_acm --intensity standard
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" inspect --work-dir ./work --view batch --batch-id batch_01
cat <<'EOF' | python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" patch --work-dir ./work --stdin
[
  {"sentence_id": "S001", "polished_text": "We conducted experiments to evaluate model latency.", "edit_types": ["collocation"], "reason": "Replaced 'did experiments' with formal 'conducted experiments'."}
]
EOF
python3 -B "$SKILL_DIR/scripts/polish_pipeline.py" finish --work-dir ./work --out-dir ./out
```
