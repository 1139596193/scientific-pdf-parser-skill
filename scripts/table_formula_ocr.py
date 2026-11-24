#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
table_formula_ocr.py - Advanced Academic Table Reconstruction, Multi-Page Merging & Targeted OCR
Part of the Scientific Document Parser Skill for Harbor / DeepSeek Harness.
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import math
import shutil
import subprocess
import argparse
import warnings

# Silence PyMuPDF deprecation warnings
warnings.filterwarnings("ignore", category=FutureWarning)

GATE_SYMBOLS = {"h", "x", "y", "z", "u", "m", "s", "t", "r", "cnot", "cz", "swap", "bell"}
CONTINUED_REGEX = re.compile(r"(?:continued|\(cont\.?\)|\(续\)|（续）|续\s*表)", re.IGNORECASE)


# ----------------------------------------------------------------------
# 1. Table Data Formatting (Markdown & HTML with Colspan/Rowspan)
# ----------------------------------------------------------------------

def table_to_markdown(matrix):
    """Convert 2D table grid (list of list of strings) to standard Markdown."""
    if not matrix or not any(matrix):
        return ""
    clean_grid = [[str(cell or "").strip().replace("\n", " ") for cell in row] for row in matrix]

    num_cols = max(len(row) for row in clean_grid)
    if num_cols == 0:
        return ""

    for row in clean_grid:
        while len(row) < num_cols:
            row.append("")

    col_widths = [3] * num_cols
    for row in clean_grid:
        for idx, cell in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(cell))

    lines = []
    header = clean_grid[0]
    header_str = "| " + " | ".join(header[i].ljust(col_widths[i]) for i in range(num_cols)) + " |"
    sep_str = "| " + " | ".join("-" * col_widths[i] for i in range(num_cols)) + " |"
    lines.append(header_str)
    lines.append(sep_str)

    for row in clean_grid[1:]:
        row_str = "| " + " | ".join(row[i].ljust(col_widths[i]) for i in range(num_cols)) + " |"
        lines.append(row_str)

    return "\n".join(lines)


def table_to_html(matrix, table_id="", caption=""):
    """Convert 2D table grid to structured HTML table with cell spans."""
    if not matrix:
        return ""
    html_lines = [f'<table id="{table_id}" class="scientific-table">']
    if caption:
        html_lines.append(f'  <caption>{caption}</caption>')

    # Header
    html_lines.append("  <thead>\n    <tr>")
    for cell in matrix[0]:
        c_text = str(cell or "").strip().replace("\n", " ")
        html_lines.append(f"      <th>{c_text}</th>")
    html_lines.append("    </tr>\n  </thead>")

    # Body
    html_lines.append("  <tbody>")
    for row in matrix[1:]:
        html_lines.append("    <tr>")
        col_idx = 0
        while col_idx < len(row):
            c_text = str(row[col_idx] or "").strip().replace("\n", " ")
            # Check for trailing empty cells in this row to form colspan
            span = 1
            if not c_text:
                while (col_idx + span) < len(row) and not str(row[col_idx + span] or "").strip():
                    span += 1
            if span > 1:
                html_lines.append(f'      <td colspan="{span}">{c_text}</td>')
                col_idx += span
            else:
                html_lines.append(f"      <td>{c_text}</td>")
                col_idx += 1
        html_lines.append("    </tr>")
    html_lines.append("  </tbody>\n</table>")
    return "\n".join(html_lines)


# ----------------------------------------------------------------------
# 2. Table Validation & False Positive Filtering
# ----------------------------------------------------------------------

def is_valid_scientific_table(matrix, nearest_caption=""):
    """
    Filter out circuit diagrams, vector plots, and false positives.
    Returns True if table is a genuine data table, False if diagram/circuit.
    """
    if not matrix or len(matrix) < 1:
        return False

    # Check 1: If nearest caption is explicitly Fig / Figure / 图, it's a diagram!
    if nearest_caption:
        cap_low = nearest_caption.strip().lower()
        if cap_low.startswith("fig") or cap_low.startswith("图"):
            return False

    # Check 2: Analyze cell content and gate symbols
    non_empty_cells = []
    for row in matrix:
        for cell in row:
            val = str(cell or "").strip()
            if val:
                non_empty_cells.append(val)

    if not non_empty_cells:
        return False

    avg_len = sum(len(c) for c in non_empty_cells) / len(non_empty_cells)
    
    # Check percentage of single-char / quantum gate symbols
    gate_like_count = 0
    for c in non_empty_cells:
        clean_c = c.lower().strip(" =+-/*_^{}")
        if len(clean_c) <= 2 and (clean_c in GATE_SYMBOLS or clean_c.isalpha()):
            gate_like_count += 1

    gate_ratio = gate_like_count / len(non_empty_cells)
    
    # Circuit diagrams typically have short gate labels (H, X, U) in > 50% of cells
    if avg_len < 2.8 and gate_ratio > 0.45:
        return False

    # If table has only 1 row or 1 column with trivial text, skip
    if len(matrix) == 1 and len(matrix[0]) <= 2:
        return False

    return True


# ----------------------------------------------------------------------
# 3. Table Extraction Engines
# ----------------------------------------------------------------------

def extract_three_line_tables(page, p_idx):
    """Detect and parse three-line academic tables without vertical lines using geometric projection."""
    h_lines = []
    for l in page.lines:
        is_edge = (l["top"] < float(page.height) * 0.075 or l["bottom"] > float(page.height) * 0.935)
        if not is_edge and abs(l["top"] - l["bottom"]) < 3 and (l["x1"] - l["x0"]) > (float(page.width) * 0.35):
            h_lines.append(l)

    h_lines.sort(key=lambda l: l["top"])
    if len(h_lines) < 2:
        return []

    tables = []
    i = 0
    while i < len(h_lines) - 1:
        y_top = h_lines[i]["top"]
        # Candidates for bottom rule (must be within reasonable table height: 30 to 450 pt)
        candidates_bot = [l for l in h_lines[i+1:] if 30 < (l["top"] - y_top) < 450]
        if not candidates_bot:
            i += 1
            continue

        y_bot = candidates_bot[-1]["top"]
        x0 = min(h_lines[i]["x0"], candidates_bot[-1]["x0"])
        x1 = max(h_lines[i]["x1"], candidates_bot[-1]["x1"])

        # Header rule: closest line below y_top (within 45 pt)
        header_rules = [l for l in candidates_bot if (l["top"] - y_top) <= 45]
        y_header_div = header_rules[0]["top"] if header_rules else (y_top + 28)

        try:
            cropped = page.crop((x0, y_top - 2, x1, y_bot + 2))
            words = cropped.extract_words()
            if not words:
                i = h_lines.index(candidates_bot[-1]) + 1
                continue

            h_words = [w for w in words if w["top"] <= y_header_div + 2]
            b_words = [w for w in words if w["top"] > y_header_div + 2]

            if not h_words:
                i += 1
                continue

            # Cluster header words into columns
            h_words.sort(key=lambda w: w["x0"])
            cols = []
            for w in h_words:
                if not cols or (w["x0"] - cols[-1]["x1"]) > 10:
                    cols.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
                else:
                    cols[-1]["text"] += " " + w["text"]
                    cols[-1]["x1"] = max(cols[-1]["x1"], w["x1"])

            if len(cols) < 2:
                i += 1
                continue

            bounds = [x0]
            for c_i in range(len(cols) - 1):
                bounds.append((cols[c_i]["x1"] + cols[c_i+1]["x0"]) / 2.0)
            bounds.append(x1)

            # Group body words by row
            b_words.sort(key=lambda w: w["top"])
            row_groups = []
            for w in b_words:
                if not row_groups or abs(w["top"] - row_groups[-1][0]["top"]) > 7:
                    row_groups.append([w])
                else:
                    row_groups[-1].append(w)

            matrix = [[c["text"] for c in cols]]
            for r in row_groups:
                row_cells = [""] * len(cols)
                for w in r:
                    w_mid = (w["x0"] + w["x1"]) / 2.0
                    for c_idx in range(len(cols)):
                        if bounds[c_idx] <= w_mid < bounds[c_idx+1]:
                            row_cells[c_idx] = (row_cells[c_idx] + " " + w["text"]).strip()
                            break
                if any(row_cells):
                    matrix.append(row_cells)

            if len(matrix) >= 2:
                tables.append({
                    "page": p_idx + 1,
                    "bbox": [round(x0, 2), round(y_top, 2), round(x1, 2), round(y_bot, 2)],
                    "matrix": matrix,
                    "backend": "pdfplumber_three_line",
                })
                i = h_lines.index(candidates_bot[-1]) + 1
                continue
        except Exception:
            pass
        i += 1
    return tables


def extract_tables_pdfplumber(pdf_path):
    """Extract complex tables using pdfplumber's geometric analysis and three-line fallback."""
    try:
        import pdfplumber
        tables_out = []
        with pdfplumber.open(pdf_path) as pdf:
            for p_idx, page in enumerate(pdf.pages):
                extracted = page.extract_tables() or []
                found = page.find_tables() or []
                page_tables = []
                for t_idx, t_data in enumerate(extracted):
                    bbox = [50.0, 100.0, float(page.width) - 50.0, 300.0]
                    if t_idx < len(found):
                        fb = found[t_idx].bbox
                        bbox = [round(c, 2) for c in fb]

                    if t_data and any(any(c for c in row if c) for row in t_data):
                        page_tables.append({
                            "page": p_idx + 1,
                            "bbox": bbox,
                            "matrix": t_data,
                            "backend": "pdfplumber",
                        })

                if not page_tables:
                    # Fallback to academic three-line table geometric extractor
                    page_tables = extract_three_line_tables(page, p_idx)

                tables_out.extend(page_tables)
        return tables_out
    except Exception:
        return []


def extract_tables_fitz(pdf_path):
    """Extract tables using PyMuPDF (fitz.find_tables) with sandboxed error protection."""
    try:
        # Pre-check onnxruntime to avoid C-level DLL warning print from MuPDF
        try:
            import onnxruntime
        except Exception:
            return []

        import fitz
        doc = fitz.open(pdf_path)
        tables_out = []
        for p_idx in range(len(doc)):
            page = doc[p_idx]
            try:
                tabs = page.find_tables()
                for tab in tabs:
                    df_data = tab.extract()
                    bbox = [round(c, 2) for c in tab.bbox]
                    if df_data and any(df_data):
                        tables_out.append({
                            "page": p_idx + 1,
                            "bbox": bbox,
                            "matrix": df_data,
                            "backend": "fitz",
                        })
            except Exception:
                # Silently catch optional onnxruntime issues inside fitz
                pass
        doc.close()
        return tables_out
    except Exception:
        return []


# ----------------------------------------------------------------------
# 4. Multi-Page Continuation Table Merging
# ----------------------------------------------------------------------

def merge_continuation_tables(raw_tables):
    """
    Detect and merge tables spanning multiple pages.
    Criteria: Consecutive pages, matching column counts, or 'continued' marker.
    """
    if not raw_tables:
        return []

    merged = []
    curr = dict(raw_tables[0])
    curr["source_pages"] = [curr["page"]]
    curr["source_bboxes"] = [{"page": curr["page"], "bbox": curr["bbox"]}]

    for nxt in raw_tables[1:]:
        p_diff = nxt["page"] - curr["source_pages"][-1]
        col_count_curr = len(curr["matrix"][0]) if curr["matrix"] else 0
        col_count_nxt = len(nxt["matrix"][0]) if nxt["matrix"] else 0

        is_consecutive = (p_diff == 1)
        same_cols = (col_count_curr == col_count_nxt and col_count_curr > 0)

        nxt_header = [str(c or "").strip() for c in nxt["matrix"][0]]
        curr_header = [str(c or "").strip() for c in curr["matrix"][0]]

        is_cont = False
        if is_consecutive and same_cols:
            if nxt_header == curr_header:
                is_cont = True
                rows_to_append = nxt["matrix"][1:]
            elif any(CONTINUED_REGEX.search(c) for c in nxt_header):
                is_cont = True
                rows_to_append = nxt["matrix"][1:]
            elif nxt["bbox"][1] < 150:
                is_cont = True
                rows_to_append = nxt["matrix"]

        if is_cont:
            curr["matrix"].extend(rows_to_append)
            curr["source_pages"].append(nxt["page"])
            curr["source_bboxes"].append({"page": nxt["page"], "bbox": nxt["bbox"]})
        else:
            merged.append(curr)
            curr = dict(nxt)
            curr["source_pages"] = [curr["page"]]
            curr["source_bboxes"] = [{"page": curr["page"], "bbox": curr["bbox"]}]

    merged.append(curr)
    return merged


# ----------------------------------------------------------------------
# 5. Targeted OCR & Visual Rendering
# ----------------------------------------------------------------------

def render_page_crop_fitz(pdf_path, page_num, bbox, out_png_path, dpi=300):
    """Render high-resolution crop of specific bounding box using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])
        rect.x0 = max(0, rect.x0 - 5)
        rect.y0 = max(0, rect.y0 - 5)
        rect.x1 = min(page.rect.width, rect.x1 + 5)
        rect.y1 = min(page.rect.height, rect.y1 + 5)

        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=rect)
        pix.save(out_png_path)
        doc.close()
        return True
    except Exception:
        return False


def render_full_page_png(pdf_path, page_num, out_png_path, dpi=200):
    """Render full page as image for whole-page OCR."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        pix.save(out_png_path)
        doc.close()
        return True
    except Exception:
        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm:
            try:
                prefix = out_png_path.replace(".png", "")
                subprocess.run(
                    [pdftoppm, "-png", "-r", str(dpi), "-f", str(page_num), "-l", str(page_num), pdf_path, prefix],
                    stdout=subprocess.PIPE,
                    check=True,
                    timeout=30,
                )
                return True
            except Exception:
                pass
        return False


def preprocess_image(png_path):
    """Enhance image contrast and apply binarization if PIL is available."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        img = Image.open(png_path).convert("L")
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.8)
        img = img.filter(ImageFilter.SHARPEN)
        img.save(png_path)
        return True
    except Exception:
        return False


def run_ocr_on_image(png_path):
    """Run Tesseract OCR via pytesseract or CLI binary."""
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(png_path), lang="eng")
        return text.strip(), 0.90
    except Exception:
        pass

    tess_bin = shutil.which("tesseract")
    if tess_bin:
        try:
            res = subprocess.run([tess_bin, png_path, "stdout", "-l", "eng"], stdout=subprocess.PIPE, text=True, timeout=20)
            return res.stdout.strip(), 0.85
        except Exception:
            pass

    return "", 0.0


# ----------------------------------------------------------------------
# 6. Main Execution Flow
# ----------------------------------------------------------------------

def process_elements(pdf_path, diagnosis_path, doc_json_path, crops_dir):
    """Extract tables, filter false-positive diagrams, and perform targeted visual OCR."""
    os.makedirs(crops_dir, exist_ok=True)

    diagnosis = {}
    if diagnosis_path and os.path.exists(diagnosis_path):
        try:
            with open(diagnosis_path, "r", encoding="utf-8") as f:
                diagnosis = json.load(f)
        except Exception:
            pass

    structured_doc = {}
    if doc_json_path and os.path.exists(doc_json_path):
        try:
            with open(doc_json_path, "r", encoding="utf-8") as f:
                structured_doc = json.load(f)
        except Exception:
            pass

    # Extract tables (pdfplumber first, then fitz)
    raw_tables = extract_tables_pdfplumber(pdf_path)
    if not raw_tables:
        raw_tables = extract_tables_fitz(pdf_path)

    merged_tables = merge_continuation_tables(raw_tables)

    doc_blocks = structured_doc.get("blocks", [])
    table_captions = [b for b in doc_blocks if b.get("type") == "table_caption"]
    figure_captions = [b for b in doc_blocks if b.get("type") == "figure_caption"]

    formatted_tables = []
    filtered_diagrams = []

    for idx, t in enumerate(merged_tables):
        t_id = f"table_{idx + 1}"
        t_page = t["page"]
        t_bbox = t["bbox"]

        # Find closest caption
        matched_caption = None
        min_dist = float("inf")
        is_fig_cap = False

        all_caps = [(c, "table") for c in table_captions if c["page"] == t_page] + \
                   [(c, "fig") for c in figure_captions if c["page"] == t_page]

        for cap, kind in all_caps:
            dist = abs(t_bbox[1] - cap["bbox"][3])
            if dist < min_dist:
                min_dist = dist
                matched_caption = cap["text"]
                is_fig_cap = (kind == "fig")

        # Table validation
        if not is_valid_scientific_table(t["matrix"], nearest_caption=matched_caption if is_fig_cap else ""):
            filtered_diagrams.append({
                "candidate_id": t_id,
                "page": t_page,
                "bbox": t_bbox,
                "reason": "Quantum circuit or diagram grid filtered out",
            })
            continue

        offset = diagnosis.get("printed_page_offset", 0)
        page_map = {p["page_num"]: str(p.get("printed_page", p["page_num"])) for p in diagnosis.get("pages", [])}

        source_pages = t.get("source_pages", [t_page])
        source_printed_pages = [page_map.get(sp, str(sp + offset)) for sp in source_pages]
        evidence_sources = t.get("source_bboxes", [{"page": t_page, "bbox": t_bbox}])
        for ev in evidence_sources:
            ev["printed_page"] = page_map.get(ev["page"], str(ev["page"] + offset))

        md_text = table_to_markdown(t["matrix"])
        html_text = table_to_html(t["matrix"], table_id=t_id, caption=matched_caption or "")

        formatted_tables.append({
            "table_id": t_id,
            "caption": matched_caption,
            "page": t_page,
            "printed_page": page_map.get(t_page, str(t_page + offset)),
            "source_pages": source_pages,
            "source_printed_pages": source_printed_pages,
            "evidence_sources": evidence_sources,
            "row_count": len(t["matrix"]),
            "col_count": len(t["matrix"][0]) if t["matrix"] else 0,
            "markdown": md_text,
            "html": html_text,
        })

    # Targeted OCR on needs_visual_check blocks
    ocr_results = []
    visual_check_blocks = [b for b in doc_blocks if b.get("needs_visual_check")]

    for b in visual_check_blocks:
        b_id = b["block_id"]
        p_num = b["page"]
        bbox = b["bbox"]

        crop_png = os.path.join(crops_dir, f"crop_{b_id}.png")
        rendered = render_page_crop_fitz(pdf_path, p_num, bbox, crop_png, dpi=300)
        if rendered:
            preprocess_image(crop_png)
            ocr_text, ocr_conf = run_ocr_on_image(crop_png)
            if ocr_text:
                ocr_results.append({
                    "block_id": b_id,
                    "page": p_num,
                    "bbox": bbox,
                    "original_text": b["text"],
                    "ocr_text": ocr_text,
                    "confidence": ocr_conf,
                    "crop_path": os.path.abspath(crop_png),
                    "is_ocr_fallback": True,
                })

    enhanced_output = {
        "pdf_path": os.path.abspath(pdf_path),
        "total_tables": len(formatted_tables),
        "tables": formatted_tables,
        "filtered_non_tables": filtered_diagrams,
        "ocr_fallbacks": ocr_results,
    }
    return enhanced_output


def main():
    parser = argparse.ArgumentParser(description="Academic Table Extraction and Targeted OCR")
    parser.add_argument("--pdf", required=True, help="Path to input PDF document")
    parser.add_argument("--diagnosis", default=None, help="Path to pdf_diagnose.py JSON")
    parser.add_argument("--doc-json", default=None, help="Path to layout_parser.py JSON")
    parser.add_argument("--out-json", required=True, help="Output path for enhanced_elements.json")
    parser.add_argument("--crops-dir", default="crops", help="Directory to save visual crops")
    args = parser.parse_args()

    results = process_elements(args.pdf, args.diagnosis, args.doc_json, args.crops_dir)

    out_dir = os.path.dirname(os.path.abspath(args.out_json))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"[table_formula_ocr] Extracted {results['total_tables']} valid tables, {len(results['filtered_non_tables'])} diagrams filtered -> {args.out_json}")


if __name__ == "__main__":
    main()
