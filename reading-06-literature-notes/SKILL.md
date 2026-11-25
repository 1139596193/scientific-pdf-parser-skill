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
   (Fallback: locate `SKILL.md` via `find / -maxdepth 4 -name SKILL.md 2>/dev/null`.)
2. **Read-Only Skill Discipline**: Never write temporary files, patches, or `__pycache__` into `$SKILL_DIR`. Always invoke Python with `python3 -B "$SKILL_DIR/scripts/..."`.
3. **Runtime Paths**: Read inputs from `/input/case.json`, `/input/task.md`, and source documents under `/input/`. Write all intermediate artifacts to `/app/work/` and final deliverables (`result.json`, `literature_notes.md`) to `/app/output/`.

## 5-Step Deterministic SOP (Harbor Budget: 64 Requests / 900s / 16,384 Tokens)

### Step 1: Index Document Skeleton & Build Reading Packets (Early Fallback)
Run `structure_indexer.py` first. Never paste an entire raw PDF into your context window.

```bash
python3 -B "$SKILL_DIR/scripts/structure_indexer.py" \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```

Inspect the compact stdout summary and read:
- `/app/work/reading_packets/manifest.json` (packet list, word budgets, `task_guidance` expected output language & focus)
- `/app/work/paper_signals.json` (cleaned metadata, figures, tables, equations, `limitation_clues`, `quality_risk`, `initial_uncertainty`, `quality_notices`)
- `/app/work/draft_skeleton.json` (early-saved partial skeleton + `output_contract`)

### Step 2: Read Packets Incrementally & Emit Compact JSON Patches
Read `/app/work/reading_packets/packet_001.md`, `packet_002.md`, ... sequentially (or merge small adjacent packets < 1,200 words). Each block is tagged `[P{page}-B{seq}]` (or `[D{doc}-P{page}-B{seq}]` for multi-document cases). Locked reference sections are excluded from packets by default so model budget is spent on substantive content.

Write compact JSON patches into `/app/work/patches/` (e.g. `01_paper_card.json`, `02_sections_p1.json`, `03_knowledge_points.json`, `04_cross_doc.json`). See `$SKILL_DIR/references/schema_and_patches.md` only if you need exact field templates.

Rules while drafting patches:
- **Strict Anchor Binding**: Every substantive field in `paper_card`, `key_results`, `section_summaries[*].key_points`, and `knowledge_points` MUST cite ≥ 1 real `anchor_id` observed in the packets.
- **Short Verbatim Quotes**: When providing `verbatim_quote`, copy a short exact snippet in the **source document's original language** (even when writing Chinese notes for an English paper) so deterministic quote matching succeeds.
- **Zero Fabrication**: For unstated venue/DOI/code/baselines/limitations, set `provenance_status: "not_reported_in_source"` or `"not_reported_in_source"`. Never guess from domain labels or training memory.
- **Section Alignment**: Cover all non-locked body sections (`zone` not in `references_locked`, `toc`, `front_matter`, `appendix`). Always check `paper_signals.json -> limitation_clues` so limitations and assumptions are never missed.

### Step 3: Assemble Notes & Backfill Provenance Deterministically
Merge all patches and auto-populate `resolved_provenance` (file, page, section, excerpt) from `evidence_index.json`:

```bash
python3 -B "$SKILL_DIR/scripts/note_assembler.py" \
  --work-dir /app/work \
  --patches-dir /app/work/patches
```

### Step 4: Run 4-Fold Code Audit & Targeted Repair (Max 2 Rounds)
Run `note_auditor.py` to check: (1) ghost anchors & quote fidelity, (2) numbers/units/cross-refs/cross-lingual negation polarity via `entity_guard.py`, (3) non-locked body section & limitation coverage, (4) scanned/garbled honesty & placeholder leaks.

```bash
python3 -B "$SKILL_DIR/scripts/note_auditor.py" --work-dir /app/work
```

If `blocking_issues > 0`, inspect `/app/work/repair_queue.json` (capped at 15 prioritized items), write a targeted repair patch (e.g. `/app/work/repair_01.json`) fixing only the flagged `section_id`, `kp_id`, or `result_id`, then re-run:

```bash
python3 -B "$SKILL_DIR/scripts/note_assembler.py" --work-dir /app/work --patch /app/work/repair_01.json
python3 -B "$SKILL_DIR/scripts/note_auditor.py" --work-dir /app/work
```

Do at most 2 repair rounds. If source defects (e.g., unreadable scanned pages) remain, document them honestly in `uncertainties` and `manual_review_checklist`.

### Step 5: Render Deliverables & Enforce Hard Validation Gate
Generate `/app/output/result.json` (preserving exact `case_id` and all nested `output_contract` fields) and `/app/output/literature_notes.md` (with YAML frontmatter and verifiable Evidence Anchor Appendix):

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
- **Early Fallback**: `draft_skeleton.json` is saved in Step 1; run Step 3 + Step 5 early if nearing the 900s / 64-request budget so honest `partial` output is always preserved.
- **No Fake `success`**: Never override `status` to `"success"` when `has_unreadable_source=true`, `blocking_issues > 0`, or `body_section_coverage < 0.85`. `validate_output.py --strict` rejects dishonest `status="success"` with a non-zero exit code.
- **On-Demand Reference Loading**: Read `$SKILL_DIR/references/schema_and_patches.md` for JSON patch formats or `$SKILL_DIR/references/edge_cases_and_recovery.md` only when encountering scanned PDFs, garbled fonts, cross-lingual quotes, >30-page budgets, or multi-document collections.
