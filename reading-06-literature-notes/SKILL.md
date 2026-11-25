---
name: literature-notes-evidence-anchored
description: >-
  Evidence-anchored scientific literature summarization and structured note-taking skill.
  Use whenever asked to read, summarize, extract paper cards, build section-aligned notes,
  extract atomic knowledge points, compare multiple papers, or export verifiable Markdown/JSON
  literature notes with exact page/section/block provenance (文献总结、论文精读、结构化笔记、
  论文卡片、知识点提取、来源锚点校验、Obsidian/Zotero 导出). Handles single/multi-column PDFs,
  LaTeX, DOCX, HTML, Markdown, scanned fallback, and custom nested output_contract schemas.
---

# Literature Notes — Evidence-Anchored Workflow

Produce verifiable, section-aligned literature summaries, unified paper cards, and atomic knowledge points where every substantive claim is bound to immutable source anchors (`[P3-B014]` or `[D1-P3-B014]`), audited by deterministic code before delivery.

## Environment & Path Convention

1. Locate the installed skill directory (read-only):
   ```bash
   SKILL_DIR="/skills/weekly-skill"
   [ -d "$SKILL_DIR/scripts" ] || SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
   ```
   (Fallback: `find / -maxdepth 4 -name SKILL.md 2>/dev/null | head -1 | xargs dirname`.)
2. **Read-Only Skill Discipline**: Never write temporary files, patches, or `__pycache__` into `$SKILL_DIR`. Always invoke with `python3 -B "$SKILL_DIR/scripts/..."`.
3. **Runtime Paths**: Read inputs from `/input/case.json`, `/input/task.md`, source docs under `/input/`. Write intermediate artifacts to `/app/work/` and final deliverables (`result.json`, `literature_notes.md`) to `/app/output/`.

## 5-Step Deterministic SOP (Harbor Budget: 64 Requests / 900s / 16,384 Tokens)

### Step 1 — Index Document Skeleton & Build Reading Packets

```bash
python3 -B "$SKILL_DIR/scripts/structure_indexer.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```

Inspect stdout summary and read:
- `/app/work/reading_packets/manifest.json` → packet list, word budgets, `task_guidance` (expected language & focus from task.md/case.json)
- `/app/work/paper_signals.json` → metadata, figures, tables, equations, `limitation_clues`, `quality_risk`, `quality_notices`
- `/app/work/draft_skeleton.json` → early-saved partial skeleton + `output_contract`

### Step 2 — Read Packets Incrementally & Emit Compact JSON Patches

Read `/app/work/reading_packets/packet_001.md`, `packet_002.md`, ... sequentially (merge small adjacent packets < 1,200 words). Each block is tagged `[P{page}-B{seq}]` or `[D{doc}-P{page}-B{seq}]` (multi-doc). Reference-locked sections are excluded from packets.
- **Appendix Summary Tables**: Do NOT skip trailing `Appendix` packets (e.g. `packet_005`/`packet_006`); inspect them for key summary tables (`Table 4`, parameter bounds, deferred ablations) and include relevant empirical rows/KPs.
- **Merged Cells & PUA Formula Warnings**: When `quality_notices` flags `formula_pua_glyphs` or a table contains multi-row merged cells, cross-check `paper_signals.json -> tables` bindings and, when available, inspect rendered page crops via `table_formula_ocr.py` / `fitz`.

Write compact JSON patches into `/app/work/patches/` (e.g. `01_paper_card.json`, `02_sections_p1.json`, `03_knowledge_points.json`).

**Mandatory patch fields:**

| Field | Requirement |
|-|-|
| `anchor_ids` | ≥ 1 real anchor_id observed in packets for every substantive claim |
| `verbatim_quote` | Exact excerpt in **source document's original language**, 8–25 words |
| `provenance_status` | `"not_reported_in_source"` for any unstated fact (venue/DOI/code/baseline/limit) |
| `section_alignment` | Cover all non-locked body zones; check `limitation_clues` in paper_signals |

**Anti-Thin-Note Floor (enforced by auditor — do NOT undercut these):**

- `key_results`: ≥ 3 entries, each with `metric`, `value`, `baseline`, `condition`, `summary`, ≥ 1 `anchor_id`, `verbatim_quote` (or mark `not_reported_in_source` only if truly absent from source)
- `section_summaries[*].key_points`: ≥ 2 key_points per summarized body section, each with `verbatim_quote` and ≥ 1 anchor_id
- `knowledge_points`: 8–15 atomic KPs spanning the 7 canonical categories (`concept_definition`, `method_mechanism`, `formula_algorithm`, `empirical_finding`, `dataset_metric`, `limitation_risk`, `future_direction`)
- Prioritize directives in `manifest.json → task_guidance` (custom user focus) first

**Minimal patch skeletons (copy-paste ready — matches `note_assembler.py` schema):**

`01_paper_card.json`:
```json
{
  "paper_card": {
    "one_line_takeaway": {"value": "...", "anchor_ids": ["P1-B002"]},
    "research_problem": {"value": "...", "anchor_ids": ["P1-B003"]},
    "methodology_summary": {"value": "...", "anchor_ids": ["P3-B010"]},
    "limitations_and_assumptions": {"value": "...", "anchor_ids": ["P8-B041"]},
    "key_results": [
      {"result_id": "RES-001", "metric": "F1", "value": "92.3%", "baseline": "BERT",
       "condition": "English NER", "summary": "...", "anchor_ids": ["P5-B020"], "verbatim_quote": "..."}
    ],
    "tags": ["topic-a", "method-b"]
  }
}
```

`02_sections.json`:
```json
{
  "section_summaries": [
    {"section_id": "S03", "summary": "Concise section narrative...",
     "key_points": [
       {"text": "Atomic claim...", "anchor_ids": ["P4-B015"], "verbatim_quote": "exact source excerpt"}
     ]}
  ]
}
```

`03_knowledge_points.json`:
```json
{
  "knowledge_points": [
    {"kp_id": "KP-001", "category": "method_mechanism", "title": "...", "claim": "...",
     "detail": "...", "anchor_ids": ["P3-B012"], "verbatim_quote": "exact source excerpt"}
  ]
}
```

**Language & Quote Rules:**
- Notes follow language in `task_guidance` (`zh` → Chinese prose; preserve English technical terms inline)
- `verbatim_quote` is ALWAYS in **source document's original language** regardless of note language
- For Chinese notes on English papers: write prose in Chinese, keep `verbatim_quote` in English

### Step 3 — Assemble Notes & Backfill Provenance

```bash
python3 -B "$SKILL_DIR/scripts/note_assembler.py" \
  --work-dir /app/work \
  --patches-dir /app/work/patches
```

For single-patch repair:
```bash
python3 -B "$SKILL_DIR/scripts/note_assembler.py" \
  --work-dir /app/work \
  --patch /app/work/repair_01.json
```

### Step 4 — Run 4-Fold Code Audit & Targeted Repair (Max 2 Rounds)

```bash
python3 -B "$SKILL_DIR/scripts/note_auditor.py" --work-dir /app/work
```

Checks: (1) ghost anchors & quote fidelity, (2) numbers/units/cross-refs/negation polarity, (3) non-locked body section & limitation coverage, (4) scanned/garbled honesty & placeholder leaks.

If `blocking_issues > 0`, read `/app/work/repair_queue.json` (≤ 15 prioritized items), write targeted repair patch fixing only the flagged `section_id`/`kp_id`/`result_id`, then re-run Steps 3–4. Max 2 repair rounds. Undeclared source defects (scanned pages) → document in `uncertainties` and `manual_review_checklist`.

### Step 5 — Render Deliverables & Enforce Hard Validation Gate

```bash
python3 -B "$SKILL_DIR/scripts/render_deliverables.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output

python3 -B "$SKILL_DIR/scripts/validate_output.py" \
  --output-dir /app/output \
  --input-dir /input \
  --work-dir /app/work \
  --strict
```

Do NOT finish until `validate_output.py --strict` exits with code `0`.

## Hard Discipline Checklist

- **Early Fallback**: `draft_skeleton.json` is saved in Step 1; if nearing budget (900s / 64 requests), run Steps 3 + 5 immediately so honest `partial` output is preserved.
- **No Fake `success`**: Never override `status` to `"success"` when `has_unreadable_source=true`, `blocking_issues > 0`, or `body_section_coverage < 0.85`. `validate_output.py --strict` rejects dishonest success with non-zero exit.
- **On-Demand Reference Loading**: Read `$SKILL_DIR/references/schema_and_patches.md` for JSON patch field templates or `$SKILL_DIR/references/edge_cases_and_recovery.md` only when encountering scanned PDFs, garbled fonts, cross-lingual quotes, > 30-page budgets, or multi-document collections.
- **Unnumbered Prose Fallback**: For white papers, reports, or single-column prose without numbered headings, `structure_indexer.py` auto-injects a synthetic body section so `body_section_coverage` can be non-zero; do not add synthetic headings in patches.
- **Same-Section Number Context**: The auditor accepts numbers found in sibling anchors (same section_id, ±3 positions) as low-severity (not blocking); only numbers absent from the entire section are high-severity.
