# Edge Cases & Failure Recovery Guide

## 1. Scanned PDFs Without OCR
- **Detection**: `pdf_diagnose.py` sets `dominant_route` to `scanned_needs_ocr`. `case_ingestor.py` attempts `tesseract` / `pytesseract` OCR at 220 DPI; if unavailable or garbled, it emits `manual_review_asset` placeholders and `paper_signals.json -> initial_uncertainty` (`severity: "high"`).
- **Action**: Do NOT invent title, methods, or numeric findings from the filename. Run `note_assembler.py`, `note_auditor.py`, and `render_deliverables.py` so `result.json` honestly delivers `status="partial"` with populated `uncertainty` and `manual_review_checklist` listing exact unreadable page numbers.

## 2. Font Garble (CID/PUA) vs. Math Formula PUA Glyphs
- **Body Garble**: `(cid:N)` or `\ufffd` in prose triggers `potential_font_garble` (`severity: "medium"` in `initial_uncertainty`). Record affected pages in `manual_review_checklist` and avoid guessing obscured tokens.
- **Formula PUA Glyphs**: TeX/Computer Modern large-brace glyphs (`\uf8f0–\uf8ff`) inside `formula` blocks are classified separately as `formula_pua_glyphs` (`severity: "low"` in `quality_notices`). Prose remains reliable, though formula symbol recovery should be visually double-checked if quoted.

## 3. Cross-Lingual Notes (English Paper → Chinese Structured Notes)
- Check `reading_packets/manifest.json -> task_guidance.expected_output_language`.
- Write `summary`, `claim`, `paper_card` values, and `key_points[*].text` in the target language (e.g., Chinese).
- **Critical Rule for `verbatim_quote`**: Always copy `verbatim_quote` in the **source document's original language** (e.g., English) because `note_auditor.py` matches `verbatim_quote` against the indexed source anchor text.
- **Polarity & Numbers**: `entity_guard.py` aligns English and Chinese strong negation polarity (`not` / `does not` / `cannot` / `without` ↔ `不` / `未` / `无` / `没有` / `无法` / `未能` / `并非`) and recognizes decimal ↔ percentage equivalence (`0.85` ↔ `85%`). Keep negation polarity and units (`s` vs `ms`) strictly faithful to the anchor.

## 4. Ultra-Long Papers (> 30 Pages) Under 64-Request / 900s Budget
- `structure_indexer.py` automatically locks `References` (`zone="references_locked"`) out of `reading_packets/` and packs body sections into ~3,200-word packets without mid-sentence splitting.
- If `packet_count > 10`:
  1. Prioritize packets covering `abstract`, `introduction`, `method`, `experiments`, `limitations`, and `conclusion` first.
  2. Emit patches incrementally after every 2–3 packets and run `note_assembler.py` early so partial progress is safely persisted on disk.
  3. Group related sections cleanly into fewer, denser patches (`02_sections_p1.json`, `03_sections_p2.json`) to stay well under 64 requests.

## 5. Multi-Document Collections & Comparative Notes
- Place all documents under `/input/` (recursive subdirectories supported: PDF, DOCX, LaTeX, HTML, Markdown, TXT).
- `structure_indexer.py` assigns deterministic document prefixes `D1`, `D2`, ... (`D1-P1-B001`, `D2-P1-B001`).
- Always include the `D{n}-` prefix when citing anchors in multi-document patches, and add `cross_document_synthesis` (`comparison_table` + `synthesis_notes`) to synthesize similarities, differences, and conflicts across papers.

## 6. Custom Nested `output_contract` Adaptation
- `render_deliverables.py` inspects `/input/case.json -> output_contract` dynamically (JSON Schema `properties`/`required` or nested type dicts) and coerces mapped fields to their declared types (`object` vs `array` vs `string`).
- Never rename or drop `case_id` or declared contract keys. Always finish with `validate_output.py --strict` to verify 100% contract compliance.
