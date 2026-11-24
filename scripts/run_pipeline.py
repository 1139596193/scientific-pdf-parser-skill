#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_pipeline.py - One-Click Deterministic Scientific Document Parsing Pipeline
Part of the Scientific Document Parser Skill for Harbor / DeepSeek Harness.
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import time
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pdf_diagnose import diagnose_pdf
from layout_parser import parse_layout
from table_formula_ocr import process_elements, render_full_page_png, run_ocr_on_image, preprocess_image


def execute_pipeline(pdf_path, work_dir, case_json_path=None):
    """
    Run the end-to-end extraction pipeline:
    1. Diagnose document & environment
    2. Reconstruct layout & reading order with subscript/superscript retention
    3. Extract & filter tables + formula OCR
    4. Automatically backfill whole-page OCR for scanned pages
    5. Output pipeline_summary.json
    """
    t_start = time.time()
    os.makedirs(work_dir, exist_ok=True)
    crops_dir = os.path.join(work_dir, "crops")
    os.makedirs(crops_dir, exist_ok=True)

    # 0. Load case info if provided
    case_info = {}
    if case_json_path and os.path.exists(case_json_path):
        try:
            with open(case_json_path, "r", encoding="utf-8") as f:
                case_info = json.load(f)
        except Exception:
            pass

    case_id = (
        case_info.get("case_id")
        or case_info.get("id")
        or case_info.get("challenge_id")
        or "unknown_case"
    )

    # 1. Step 1: Diagnose
    diag_path = os.path.join(work_dir, "diagnosis.json")
    diagnosis = diagnose_pdf(pdf_path)
    with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(diagnosis, f, ensure_ascii=False, indent=2)

    # 2. Step 2: Layout Parser
    doc_json_path = os.path.join(work_dir, "structured_doc.json")
    md_path = os.path.join(work_dir, "reading_order.md")
    structured_doc, reading_order_md = parse_layout(pdf_path, diag_path)

    # Save initial structured_doc for table processor
    with open(doc_json_path, "w", encoding="utf-8") as f:
        json.dump(structured_doc, f, ensure_ascii=False, indent=2)

    # 3. Step 3: Tables & Formulas
    enh_json_path = os.path.join(work_dir, "enhanced_elements.json")
    enhanced = process_elements(pdf_path, diag_path, doc_json_path, crops_dir)

    # 4. Step 4: Scanned Page OCR Backfill
    # If any page is diagnosed as scanned_needs_ocr and has 0 blocks in structured_doc:
    pages_diag = {p["page_num"]: p for p in diagnosis.get("pages", [])}
    doc_blocks = structured_doc.get("blocks", [])
    existing_pages = {b.get("page") for b in doc_blocks}

    ocr_backfilled_count = 0
    for p_num, p_info in pages_diag.items():
        if p_info.get("recommended_route") == "scanned_needs_ocr" or p_num not in existing_pages:
            # Render page image and perform full-page OCR
            page_png = os.path.join(crops_dir, f"scanned_page_{p_num}.png")
            if render_full_page_png(pdf_path, p_num, page_png, dpi=200):
                preprocess_image(page_png)
                ocr_text, ocr_conf = run_ocr_on_image(page_png)
                if ocr_text.strip():
                    new_block = {
                        "block_id": f"b_{p_num}_ocr_1",
                        "page": p_num,
                        "printed_page": str(p_num),
                        "column": "full",
                        "bbox": [50.0, 50.0, p_info.get("width", 595.0) - 50.0, p_info.get("height", 842.0) - 50.0],
                        "type": "paragraph",
                        "text": ocr_text,
                        "confidence": ocr_conf,
                        "is_ocr_fallback": True,
                        "evidence_anchors": [{
                            "page": p_num,
                            "block_id": f"b_{p_num}_ocr_1",
                            "bbox": [50.0, 50.0, p_info.get("width", 595.0) - 50.0, p_info.get("height", 842.0) - 50.0],
                        }],
                    }
                    doc_blocks.append(new_block)
                    reading_order_md += f"\n\n[p{p_num}|b_{p_num}_ocr_1] (OCR Fallback)\n{ocr_text}\n"
                    ocr_backfilled_count += 1

    # Re-save updated doc and md
    with open(doc_json_path, "w", encoding="utf-8") as f:
        json.dump(structured_doc, f, ensure_ascii=False, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(reading_order_md)

    with open(enh_json_path, "w", encoding="utf-8") as f:
        json.dump(enhanced, f, ensure_ascii=False, indent=2)

    elapsed_time = round(time.time() - t_start, 2)

    # 5. Generate Executive Summary
    summary = {
        "status": "success",
        "case_id": case_id,
        "pdf_path": os.path.abspath(pdf_path),
        "total_pages": diagnosis.get("page_count", 0),
        "dominant_route": diagnosis.get("dominant_route", "native_clean"),
        "high_risk_pages": diagnosis.get("high_risk_pages", []),
        "metadata": {
            "title": structured_doc.get("metadata", {}).get("title"),
            "abstract_present": bool(structured_doc.get("metadata", {}).get("abstract")),
            "sections_count": len(structured_doc.get("sections", [])),
            "footnote_count": len(structured_doc.get("footnotes", [])),
            "printed_page_offset": diagnosis.get("printed_page_offset", 0),
        },
        "extracted_elements": {
            "total_blocks": len(doc_blocks),
            "tables_count": enhanced.get("total_tables", 0),
            "filtered_diagrams_count": len(enhanced.get("filtered_non_tables", [])),
            "ocr_backfilled_pages": ocr_backfilled_count,
        },
        "artifacts": {
            "diagnosis_json": os.path.abspath(diag_path),
            "structured_doc_json": os.path.abspath(doc_json_path),
            "reading_order_md": os.path.abspath(md_path),
            "enhanced_elements_json": os.path.abspath(enh_json_path),
        },
        "wall_time_s": elapsed_time,
    }

    sum_path = os.path.join(work_dir, "pipeline_summary.json")
    with open(sum_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Deterministic End-to-End Scientific Document Pipeline")
    parser.add_argument("--pdf", required=True, help="Path to input PDF document")
    parser.add_argument("--work-dir", required=True, help="Output working directory for all parsed deliverables")
    parser.add_argument("--case-json", default=None, help="Optional path to /input/case.json")
    args = parser.parse_args()

    summary = execute_pipeline(args.pdf, args.work_dir, args.case_json)
    print(f"[run_pipeline] Successfully executed in {summary['wall_time_s']}s:")
    print(f"  - Case ID: {summary['case_id']}")
    print(f"  - Title: {summary['metadata']['title']}")
    print(f"  - Pages: {summary['total_pages']} (Route: {summary['dominant_route']})")
    print(f"  - Blocks: {summary['extracted_elements']['total_blocks']} | Tables: {summary['extracted_elements']['tables_count']} | Sections: {summary['metadata']['sections_count']}")
    print(f"  - Summary saved -> {os.path.join(args.work_dir, 'pipeline_summary.json')}")


if __name__ == "__main__":
    main()
