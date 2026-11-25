#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
table_formula_ocr.py - Academic Table Reconstruction, Multi-Page Merging & Targeted OCR
Part of the Scientific Literature Notes Engine (task_06_notes).
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

    if nearest_caption:
        cap_low = nearest_caption.strip().lower()
        if cap_low.startswith("fig") or cap_low.startswith("图"):
            return False

    non_empty_cells = []
    for row in matrix:
        for cell in row:
            val = str(cell or "").strip()
            if val:
                non_empty_cells.append(val)

    if not non_empty_cells:
        return False

    avg_len = sum(len(c) for c in non_empty_cells) / len(non_empty_cells)
    
    gate_like_count = 0
    for c in non_empty_cells:
        clean_c = c.lower().strip(" =+-/*_^{}")
        if len(clean_c) <= 2 and (clean_c in GATE_SYMBOLS or clean_c.isalpha()):
            gate_like_count += 1

    gate_ratio = gate_like_count / len(non_empty_cells)
    
    if avg_len < 2.8 and gate_ratio > 0.45:
        return False

    if len(matrix) == 1 and len(matrix[0]) <= 2:
        return False

    return True


# ----------------------------------------------------------------------
# 3. Extraction Engines
# ----------------------------------------------------------------------

def extract_tables_pdfplumber(pdf_path):
    """Extract tables using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        return []

    tables_found = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for p_idx, page in enumerate(pdf.pages):
                extracted = page.extract_tables()
                bboxes = page.find_tables()
                for t_idx, matrix in enumerate(extracted):
                    bbox = [0, 0, float(page.width), float(page.height)]
                    if t_idx < len(bboxes) and bboxes[t_idx].bbox:
                        bbox = [round(c, 2) for c in bboxes[t_idx].bbox]

                    tables_found.append({
                        "page": p_idx + 1,
                        "matrix": matrix,
                        "bbox": bbox,
                        "backend": "pdfplumber",
                    })
    except Exception:
        pass
    return tables_found


def merge_continued_tables(tables):
    """Detect and stitch continued tables across page breaks."""
    if len(tables) <= 1:
        return tables

    merged = []
    skip_indices = set()

    for i in range(len(tables)):
        if i in skip_indices:
            continue
        curr = dict(tables[i])
        curr["source_pages"] = [curr["page"]]
        curr["source_bboxes"] = [{"page": curr["page"], "bbox": curr["bbox"]}]

        for j in range(i + 1, len(tables)):
            nxt = tables[j]
            if nxt["page"] == curr["source_pages"][-1] + 1:
                curr_cols = len(curr["matrix"][0]) if curr["matrix"] else 0
                nxt_cols = len(nxt["matrix"][0]) if nxt["matrix"] else 0

                if curr_cols > 0 and curr_cols == nxt_cols:
                    # Strip repeated header if identical
                    nxt_matrix = list(nxt["matrix"])
                    if nxt_matrix and nxt_matrix[0] == curr["matrix"][0]:
                        nxt_matrix = nxt_matrix[1:]

                    curr["matrix"].extend(nxt_matrix)
                    curr["source_pages"].append(nxt["page"])
                    curr["source_bboxes"].append({"page": nxt["page"], "bbox": nxt["bbox"]})
                    skip_indices.add(j)
                else:
                    break
            else:
                break

        merged.append(curr)

    return merged


# ----------------------------------------------------------------------
# 4. Image Crop & Targeted OCR
# ----------------------------------------------------------------------

def render_page_crop_fitz(pdf_path, page_num, bbox, out_png, dpi=300):
    """Render a bounding box region to high-resolution PNG."""
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
    except ImportError:
        return False

    try:
        doc = fitz.open(pdf_path)
        if page_num < 1 or page_num > len(doc):
            return False
        page = doc[page_num - 1]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        clip_rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])
        pix = page.get_pixmap(matrix=mat, clip=clip_rect, alpha=False)
        
        out_dir = os.path.dirname(os.path.abspath(out_png))
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
            
        pix.save(out_png)
        doc.close()
        return True
    except Exception:
        return False


def run_ocr_on_image(image_path, lang="eng"):
    """Run pytesseract OCR or CLI tesseract fallback."""
    # 1. Python pytesseract
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path)
        txt = pytesseract.image_to_string(img, lang=lang)
        return txt.strip(), 0.90
    except Exception:
        pass

    # 2. CLI tesseract
    tess_path = shutil.which("tesseract")
    if tess_path:
        try:
            res = subprocess.run(
                [tess_path, image_path, "stdout", "-l", lang],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            return res.stdout.strip(), 0.85
        except Exception:
            pass

    return "", 0.0


# ----------------------------------------------------------------------
# 5. Main Element Enhancement Flow
# ----------------------------------------------------------------------

def process_elements(pdf_path, diagnosis_path=None, layout_json_path=None, crops_dir="crops"):
    """Extract and validate tables, merge continuations, and run targeted OCR."""
    diagnosis = {}
    if diagnosis_path and os.path.exists(diagnosis_path):
        try:
            with open(diagnosis_path, "r", encoding="utf-8") as f:
                diagnosis = json.load(f)
        except Exception:
            pass

    doc_blocks = []
    if layout_json_path and os.path.exists(layout_json_path):
        try:
            with open(layout_json_path, "r", encoding="utf-8") as f:
                doc_blocks = json.load(f).get("blocks", [])
        except Exception:
            pass

    raw_tables = extract_tables_pdfplumber(pdf_path)
    merged_tables = merge_continued_tables(raw_tables)

    table_captions = [b for b in doc_blocks if b.get("type") == "table_caption"]
    figure_captions = [b for b in doc_blocks if b.get("type") == "figure_caption"]

    formatted_tables = []
    filtered_diagrams = []

    for idx, t in enumerate(merged_tables):
        t_id = f"table_{idx + 1}"
        t_page = t["page"]
        t_bbox = t["bbox"]

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

        if not is_valid_scientific_table(t["matrix"], nearest_caption=matched_caption if is_fig_cap else ""):
            filtered_diagrams.append({
                "candidate_id": t_id,
                "page": t_page,
                "bbox": t_bbox,
                "reason": "Diagram or non-table filtered",
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

    # OCR Fallback for garbled blocks if requested
    ocr_results = []
    visual_check_blocks = [b for b in doc_blocks if b.get("needs_visual_check")]

    for b in visual_check_blocks:
        b_id = b["block_id"]
        p_num = b["page"]
        bbox = b["bbox"]

        crop_png = os.path.join(crops_dir, f"crop_{b_id}.png")
        rendered = render_page_crop_fitz(pdf_path, p_num, bbox, crop_png, dpi=300)
        if rendered:
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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

    print(f"[table_formula_ocr] Extracted {results['total_tables']} tables, {len(results['filtered_non_tables'])} filtered -> {args.out_json}")


if __name__ == "__main__":
    main()
