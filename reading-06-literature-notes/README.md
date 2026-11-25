# Literature Notes Skill — Evidence-Anchored Architecture

A deterministic-plus-LLM skill for producing **section-aligned, field-standardized, anchor-verifiable, and directly reusable** scientific literature notes (`result.json` + Obsidian/Zotero-ready `literature_notes.md`).

## Core Design Principles

1. **Structural Alignment**: Uses a multi-backend PDF layout parser (`pdf_engine`) + bilingual academic heading classifier to build a hierarchical section tree (`Abstract`, `Introduction`, `Method`, `Experiments`, `Limitations`, `Conclusion`, locked `References`, `Appendix`), preventing generic flat summaries.
2. **Immutable Evidence Anchors**: Assigns deterministic block IDs (`P{page}-B{seq:03d}` for single documents; `D{doc}-P{page}-B{seq:03d}` for multi-document cases) in `evidence_index.json`.
3. **Budget-Safe Incremental Assembly**: Splits papers along section boundaries into ~3,200-word `reading_packets/`, excluding locked bibliographies by default, and merges compact JSON patches via `note_assembler.py` with automatic provenance backfill (`resolved_provenance`).
4. **4-Fold Deterministic Code Audit**: `note_auditor.py` and `entity_guard.py` verify every claim against its cited source anchors:
   - Ghost anchor & missing-anchor blocking (`critical` / `high`)
   - Normalized `verbatim_quote` substring/overlap verification
   - Numeric rounding tolerance, decimal ↔ percentage equivalence (`0.85` ↔ `85%`), compound unit consistency (`s` vs `ms`), ghost figure/table reference detection, and cross-lingual negation polarity alignment (`EN ↔ ZH`)
   - Non-locked body section coverage, limitation clue coverage, scanned/garbled honesty disclosure, and template placeholder leak scanning
5. **Hard Output Contract & Honesty Gate**: `render_deliverables.py` and `validate_output.py --strict` preserve arbitrary nested `output_contract` schemas and reject dishonest `status="success"` outputs whenever unresolved blocking audit errors, unreadable scanned pages, or `<85%` body coverage exist.

## Repository Structure

```text
outputs/
├── SKILL.md                             # Agent execution SOP & discipline
├── README.md                            # Architecture overview & CLI quickstart
├── references/
│   ├── schema_and_patches.md            # JSON patch templates & enum dictionary
│   └── edge_cases_and_recovery.md       # Scanned/garbled/cross-lingual/multi-doc rules
├── resources/
│   └── academic_invariants.json         # Bilingual logic markers & SI/academic units
└── scripts/
    ├── pdf_engine/                      # Multi-backend PDF diagnose, layout & OCR fallback
    ├── case_ingestor.py                 # Multi-format & multi-document recursive ingestor
    ├── entity_guard.py                  # Deterministic entity, unit & polarity fingerprint guard
    ├── structure_indexer.py             # Section tree, signal pre-extractor & packet builder
    ├── note_assembler.py                # Incremental patch merger & provenance backfill
    ├── note_auditor.py                  # 4-fold claim-to-anchor & honesty auditor
    ├── render_deliverables.py           # Dynamic contract mapper & Obsidian MD renderer
    ├── validate_output.py               # Strict contract & anti-dishonest-success gate
    └── self_test.py                     # Zero-dependency 18-case offline regression suite
```

All Python modules restrict top-level imports strictly to the Python standard library (with lazy optional imports for `fitz`, `pdfplumber`, and `pytesseract`) and enforce `encoding="utf-8"`.

## Quick Verification & CLI Usage

### 1. Run Zero-Dependency Self-Test (18 Cases)
```bash
python3 -B scripts/self_test.py
```

### 2. Full End-to-End Pipeline Example
```bash
# Step 1: Index input materials (/input -> /app/work)
python3 -B scripts/structure_indexer.py --input-dir /input --work-dir /app/work --output-dir /app/output

# Step 2: (Agent reads /app/work/reading_packets/*.md and writes JSON patches to /app/work/patches/)

# Step 3: Assemble patches & backfill provenance
python3 -B scripts/note_assembler.py --work-dir /app/work --patches-dir /app/work/patches

# Step 4: Audit claims against source anchors (inspect /app/work/repair_queue.json if issues found)
python3 -B scripts/note_auditor.py --work-dir /app/work

# Step 5: Render deliverables & validate strictly
python3 -B scripts/render_deliverables.py --input-dir /input --work-dir /app/work --output-dir /app/output
python3 -B scripts/validate_output.py --output-dir /app/output --input-dir /input --work-dir /app/work --strict
```
