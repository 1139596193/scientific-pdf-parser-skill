#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_diagnose.py - PDF Document Diagnostics & Environment Prober
Clean-ported for Scientific Literature Notes Task (task_06_notes).
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
import importlib
import traceback
import collections

# ----------------------------------------------------------------------
# 1. Environment & Capability Prober
# ----------------------------------------------------------------------

def probe_python_package(package_name, import_name=None):
    """Probe if a Python library is importable and get its version."""
    if import_name is None:
        import_name = package_name
    try:
        mod = importlib.import_module(import_name)
        ver = getattr(mod, "__version__", None)
        if ver is None and hasattr(mod, "version"):
            ver = getattr(mod, "version")
        if ver is None and hasattr(mod, "VERSION"):
            ver = getattr(mod, "VERSION")
        return {"available": True, "version": str(ver) if ver else "unknown"}
    except Exception:
        return {"available": False, "version": None}


def probe_cli_tool(tool_name, version_flags=("-v", "--version", "-version", "-V")):
    """Probe if a CLI binary is available on PATH and get its version text."""
    tool_path = shutil.which(tool_name)
    if not tool_path:
        return {"available": False, "path": None, "version": None}

    version_str = "unknown"
    for flag in version_flags:
        try:
            res = subprocess.run(
                [tool_path, flag],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            out = (res.stdout or res.stderr).strip()
            if out:
                first_line = out.splitlines()[0][:120]
                version_str = first_line
                break
        except Exception:
            continue

    return {"available": True, "path": tool_path, "version": version_str}


def get_tesseract_languages():
    """Retrieve installed languages for Tesseract OCR if available."""
    tess_path = shutil.which("tesseract")
    if not tess_path:
        return []
    try:
        res = subprocess.run(
            [tess_path, "--list-langs"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        langs = [l for l in lines if not l.startswith("List of") and not l.endswith(":")]
        return langs
    except Exception:
        return []


def probe_environment():
    """Probe all potential PDF, rendering, OCR, and font tools."""
    py_libs = {
        "fitz": probe_python_package("pymupdf", "fitz"),
        "pdfplumber": probe_python_package("pdfplumber"),
        "pypdf": probe_python_package("pypdf"),
        "pypdf2": probe_python_package("pypdf2"),
        "PIL": probe_python_package("pillow", "PIL"),
        "cv2": probe_python_package("opencv-python", "cv2"),
        "numpy": probe_python_package("numpy"),
        "pytesseract": probe_python_package("pytesseract"),
        "docx": probe_python_package("python-docx", "docx"),
    }

    cli_tools = {
        "pdftotext": probe_cli_tool("pdftotext"),
        "pdftohtml": probe_cli_tool("pdftohtml"),
        "pdftoppm": probe_cli_tool("pdftoppm"),
        "pdffonts": probe_cli_tool("pdffonts"),
        "pdfinfo": probe_cli_tool("pdfinfo"),
        "tesseract": probe_cli_tool("tesseract"),
        "gs": probe_cli_tool("gs"),
        "magick": probe_cli_tool("magick"),
    }

    tess_langs = get_tesseract_languages()
    if cli_tools["tesseract"]["available"]:
        cli_tools["tesseract"]["installed_languages"] = tess_langs

    return {
        "python_libraries": py_libs,
        "cli_tools": cli_tools,
        "preferred_pdf_backend": _select_preferred_backend(py_libs, cli_tools),
    }


def _select_preferred_backend(py_libs, cli_tools):
    """Determine highest-priority available extraction engine."""
    if py_libs["fitz"]["available"]:
        return "fitz"
    elif py_libs["pdfplumber"]["available"]:
        return "pdfplumber"
    elif cli_tools["pdftohtml"]["available"]:
        return "pdftohtml_xml"
    elif cli_tools["pdftotext"]["available"]:
        return "pdftotext_layout"
    elif py_libs["pypdf"]["available"]:
        return "pypdf"
    elif py_libs["pypdf2"]["available"]:
        return "pypdf2"
    return "raw_fallback"


# ----------------------------------------------------------------------
# 2. PDF Integrity & Layout Analysis
# ----------------------------------------------------------------------

CID_PATTERN = re.compile(r"\(cid:\d+\)")
PUA_PATTERN = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd]")


def calculate_garbled_metrics(text):
    """Compute count and ratio of CID, replacement, or private-use characters."""
    if not text:
        return 0, 0.0

    total_len = len(text)
    cid_matches = len(CID_PATTERN.findall(text)) * 5  # weight for cid tags
    pua_matches = len(PUA_PATTERN.findall(text))
    fffd_matches = text.count("\ufffd")

    bad_chars = cid_matches + pua_matches + fffd_matches
    ratio = bad_chars / max(1, total_len)
    return bad_chars, round(ratio, 4)


def extract_page_info_fitz(pdf_path, env_info):
    """Extract per-page metrics using PyMuPDF (fitz)."""
    import fitz

    doc = fitz.open(pdf_path)
    if doc.is_encrypted:
        return {
            "encrypted": True,
            "corrupted": False,
            "page_count": len(doc),
            "pages": [],
            "error": "Document is encrypted/password protected.",
        }

    pages_data = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        rect = page.rect
        width = float(rect.width)
        height = float(rect.height)

        text_page = page.get_text("blocks")
        full_text = page.get_text("text")

        char_count = len(full_text.strip())
        bad_chars, garbled_ratio = calculate_garbled_metrics(full_text)

        image_list = page.get_images(full=True)
        img_coverage = 0.0
        for img_info in image_list:
            try:
                img_rects = page.get_image_rects(img_info[0])
                for r in img_rects:
                    img_area = r.width * r.height
                    img_coverage += img_area / max(1.0, width * height)
            except Exception:
                pass
        img_coverage = min(1.0, round(img_coverage, 3))

        text_bboxes = []
        for b in text_page:
            if len(b) >= 7 and b[6] == 0:  # text block
                text_bboxes.append((b[0], b[1], b[2], b[3], b[4]))

        layout_type = classify_layout(width, height, text_bboxes, char_count, img_coverage)
        route = recommend_route(layout_type, char_count, garbled_ratio, img_coverage)
        printed_folio = guess_printed_page(text_bboxes, page_idx + 1, height)

        pages_data.append({
            "page_num": page_idx + 1,
            "width": round(width, 2),
            "height": round(height, 2),
            "char_count": char_count,
            "image_count": len(image_list),
            "image_coverage": img_coverage,
            "garbled_ratio": garbled_ratio,
            "layout_type": layout_type,
            "recommended_route": route,
            "printed_page": printed_folio,
            "is_high_risk": (garbled_ratio > 0.08 or route.endswith("_needs_ocr")),
        })

    doc.close()
    return {
        "encrypted": False,
        "corrupted": False,
        "page_count": len(pages_data),
        "pages": pages_data,
        "error": None,
    }


def extract_page_info_pdfplumber(pdf_path, env_info):
    """Fallback extraction using pdfplumber."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        pages_data = []
        for page_idx, page in enumerate(pdf.pages):
            width = float(page.width)
            height = float(page.height)
            text = page.extract_text() or ""
            char_count = len(text.strip())
            bad_chars, garbled_ratio = calculate_garbled_metrics(text)

            images = page.images or []
            img_coverage = 0.0
            for img in images:
                try:
                    w = float(img.get("width", 0))
                    h = float(img.get("height", 0))
                    img_coverage += (w * h) / max(1.0, width * height)
                except Exception:
                    pass
            img_coverage = min(1.0, round(img_coverage, 3))

            words = page.extract_words() or []
            text_bboxes = [(w["x0"], w["top"], w["x1"], w["bottom"], w["text"]) for w in words]
            layout_type = classify_layout(width, height, text_bboxes, char_count, img_coverage)
            route = recommend_route(layout_type, char_count, garbled_ratio, img_coverage)
            printed_folio = guess_printed_page(text_bboxes, page_idx + 1, height)

            pages_data.append({
                "page_num": page_idx + 1,
                "width": round(width, 2),
                "height": round(height, 2),
                "char_count": char_count,
                "image_count": len(images),
                "image_coverage": img_coverage,
                "garbled_ratio": garbled_ratio,
                "layout_type": layout_type,
                "recommended_route": route,
                "printed_page": printed_folio,
                "is_high_risk": (garbled_ratio > 0.08 or route.endswith("_needs_ocr")),
            })

        return {
            "encrypted": False,
            "corrupted": False,
            "page_count": len(pages_data),
            "pages": pages_data,
            "error": None,
        }


def extract_page_info_cli(pdf_path, env_info):
    """Fallback extraction using pdftotext / pdfinfo CLI tools."""
    info_tool = env_info["cli_tools"]["pdfinfo"]["path"]
    totext_tool = env_info["cli_tools"]["pdftotext"]["path"]

    page_count = 1
    encrypted = False

    if info_tool:
        try:
            res = subprocess.run([info_tool, pdf_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            for line in res.stdout.splitlines():
                if line.startswith("Pages:"):
                    page_count = int(line.split(":")[1].strip())
                elif line.startswith("Encrypted:") and "yes" in line.lower():
                    encrypted = True
        except Exception:
            pass

    if encrypted:
        return {
            "encrypted": True,
            "corrupted": False,
            "page_count": page_count,
            "pages": [],
            "error": "Document is encrypted according to pdfinfo.",
        }

    pages_data = []
    for p_num in range(1, page_count + 1):
        char_count = 0
        garbled_ratio = 0.0
        text = ""
        if totext_tool:
            try:
                res = subprocess.run([totext_tool, "-f", str(p_num), "-l", str(p_num), pdf_path, "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
                text = res.stdout
                char_count = len(text.strip())
                _, garbled_ratio = calculate_garbled_metrics(text)
            except Exception:
                pass

        layout_type = "single_column" if char_count > 50 else "empty_or_scanned"
        route = recommend_route(layout_type, char_count, garbled_ratio, 0.0)

        pages_data.append({
            "page_num": p_num,
            "width": 595.0,
            "height": 842.0,
            "char_count": char_count,
            "image_count": 0,
            "image_coverage": 0.0,
            "garbled_ratio": garbled_ratio,
            "layout_type": layout_type,
            "recommended_route": route,
            "printed_page": str(p_num),
            "is_high_risk": (route.endswith("_needs_ocr") or garbled_ratio > 0.08),
        })

    return {
        "encrypted": False,
        "corrupted": False,
        "page_count": len(pages_data),
        "pages": pages_data,
        "error": None,
    }


def classify_layout(width, height, bboxes, char_count, img_coverage):
    """Classify page layout into single/double/multi/mixed/empty_or_scanned."""
    if char_count < 60 and img_coverage > 0.35:
        return "empty_or_scanned"
    if not bboxes:
        return "empty_or_scanned" if char_count < 50 else "single_column"

    mid_x = width / 2.0
    margin = width * 0.08

    body_blocks = [
        b for b in bboxes
        if (b[1] > height * 0.06 and b[3] < height * 0.94 and (b[2] - b[0]) > 20)
    ]

    if not body_blocks:
        return "single_column"

    left_col_count = 0
    right_col_count = 0
    full_width_count = 0

    for b in body_blocks:
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        block_w = x1 - x0

        if block_w > width * 0.65:
            full_width_count += 1
        elif x1 <= mid_x + margin:
            left_col_count += 1
        elif x0 >= mid_x - margin:
            right_col_count += 1

    total_cols = left_col_count + right_col_count
    if total_cols >= 4 and abs(left_col_count - right_col_count) <= max(left_col_count, right_col_count) * 0.6:
        if full_width_count >= 1:
            return "mixed"
        return "double_column"
    elif full_width_count > 0 and total_cols >= 2:
        return "mixed"
    return "single_column"


def recommend_route(layout_type, char_count, garbled_ratio, img_coverage):
    """Recommend best parsing pipeline route for this page."""
    if layout_type == "empty_or_scanned" or (char_count < 80 and img_coverage > 0.4):
        return "scanned_needs_ocr"
    if garbled_ratio > 0.06:
        return "garbled_font_needs_ocr"
    if layout_type in ("double_column", "multi_column", "mixed"):
        return "native_multicolumn"
    return "native_clean"


def guess_printed_page(bboxes, physical_page, height):
    """Identify printed folio/page number from bottom/top edge blocks strictly."""
    candidates = []
    for b in bboxes:
        y0, y1, text = b[1], b[3], b[4].strip()
        is_edge = (y0 < height * 0.07 or y1 > height * 0.93)
        if not is_edge:
            continue

        cleaned = re.sub(r"第\s*\d+\s*[卷期]|(?:Vol|No|pp)\.?\s*\d+|(?:19|20)\d{2}", "", text, flags=re.I)
        for line in cleaned.splitlines():
            line = line.strip()
            matches = re.finditer(r"(?:^|[\s·\-\(\[\{第])(\d{1,5})(?:[\s·\-\)\]\}页]|$)", line)
            for m in matches:
                val = int(m.group(1))
                if val > 0:
                    candidates.append((y1, val, line))

    if candidates:
        exact_num = [c for c in candidates if re.match(r"^\d+$", c[2])]
        if exact_num:
            return str(exact_num[0][1])
        return str(candidates[0][1])

    return str(physical_page)


# ----------------------------------------------------------------------
# 3. Main CLI Entry Point
# ----------------------------------------------------------------------

def diagnose_pdf(pdf_path):
    """Execute complete diagnosis of target PDF and environment."""
    env_info = probe_environment()
    backend = env_info["preferred_pdf_backend"]

    if not os.path.exists(pdf_path):
        return {
            "environment": env_info,
            "status": "error",
            "pdf_path": pdf_path,
            "error": f"PDF file not found: {pdf_path}",
        }

    try:
        with open(pdf_path, "rb") as f:
            header = f.read(10)
            if b"%PDF-" not in header:
                return {
                    "environment": env_info,
                    "status": "corrupted",
                    "pdf_path": pdf_path,
                    "error": "File does not start with valid %PDF- header.",
                }
    except Exception as e:
        return {
            "environment": env_info,
            "status": "unreadable",
            "pdf_path": pdf_path,
            "error": str(e),
        }

    analysis = None
    if backend == "fitz":
        try:
            analysis = extract_page_info_fitz(pdf_path, env_info)
        except Exception:
            analysis = None

    if analysis is None and env_info["python_libraries"]["pdfplumber"]["available"]:
        try:
            analysis = extract_page_info_pdfplumber(pdf_path, env_info)
        except Exception:
            analysis = None

    if analysis is None:
        try:
            analysis = extract_page_info_cli(pdf_path, env_info)
        except Exception as e:
            analysis = {
                "encrypted": False,
                "corrupted": True,
                "page_count": 0,
                "pages": [],
                "error": f"All extraction backends failed: {str(e)}",
            }

    pages = analysis.get("pages", [])
    high_risk_pages = [p["page_num"] for p in pages if p.get("is_high_risk")]

    deltas = []
    for p in pages:
        folio_str = p.get("printed_page")
        if folio_str and folio_str.isdigit():
            val = int(folio_str)
            p_num = p["page_num"]
            deltas.append(val - p_num)

    global_offset = 0
    if deltas:
        counts = collections.Counter(deltas)
        most_common_delta, freq = counts.most_common(1)[0]
        if most_common_delta != 0 and freq >= 1:
            global_offset = most_common_delta
        elif 0 in counts:
            global_offset = 0

    for p in pages:
        p["printed_page"] = str(p["page_num"] + global_offset)

    route_counts = {}
    for p in pages:
        r = p["recommended_route"]
        route_counts[r] = route_counts.get(r, 0) + 1

    doc_dominant_route = "native_clean"
    if route_counts:
        doc_dominant_route = max(route_counts, key=route_counts.get)

    result = {
        "status": "success",
        "pdf_path": os.path.abspath(pdf_path),
        "file_size_bytes": os.path.getsize(pdf_path),
        "environment": env_info,
        "is_encrypted": analysis.get("encrypted", False),
        "is_corrupted": analysis.get("corrupted", False),
        "page_count": analysis.get("page_count", len(pages)),
        "high_risk_pages": high_risk_pages,
        "dominant_route": doc_dominant_route,
        "route_breakdown": route_counts,
        "printed_page_offset": global_offset,
        "pages": pages,
        "error": analysis.get("error"),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Scientific PDF Diagnosis and Tooling Prober")
    parser.add_argument("--pdf", required=True, help="Path to target scientific PDF document")
    parser.add_argument("--out", required=True, help="Path to output diagnosis.json file")
    args = parser.parse_args()

    diagnosis = diagnose_pdf(args.pdf)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(diagnosis, f, ensure_ascii=False, indent=2)

    print(f"[pdf_diagnose] Diagnosis complete: {len(diagnosis.get('pages', []))} pages analyzed -> {args.out}")


if __name__ == "__main__":
    main()
