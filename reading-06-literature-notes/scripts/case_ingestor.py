#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case_ingestor.py - Universal Scientific Material Ingestion & Multi-Document Parsing Engine
Part of the Scientific Literature Notes Skill (task_06_notes).
Strict zero-crash design: top-level imports are standard libraries only.
"""

import os
import sys
import json
import re
import argparse
import collections
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from pdf_engine.pdf_diagnose import diagnose_pdf, calculate_garbled_metrics
    from pdf_engine.layout_parser import parse_layout, STANDALONE_HEADINGS
    from pdf_engine.table_formula_ocr import process_elements, render_page_crop_fitz, run_ocr_on_image
except ImportError:
    try:
        from .pdf_engine.pdf_diagnose import diagnose_pdf, calculate_garbled_metrics
        from .pdf_engine.layout_parser import parse_layout, STANDALONE_HEADINGS
        from .pdf_engine.table_formula_ocr import process_elements, render_page_crop_fitz, run_ocr_on_image
    except Exception:
        diagnose_pdf = None
        calculate_garbled_metrics = None
        parse_layout = None
        process_elements = None
        render_page_crop_fitz = None
        run_ocr_on_image = None
        STANDALONE_HEADINGS = set()


# ----------------------------------------------------------------------
# 1. Text, Language & Metrics Utilities
# ----------------------------------------------------------------------

CJK_REGEX = re.compile(r"[\u4e00-\u9fa5]")
WORD_SPLIT_REGEX = re.compile(r"\b[a-zA-Z0-9_\-']+\b")
SENTENCE_SPLIT_REGEX = re.compile(r"[.?!。！？；]+")


def detect_language(text):
    """Detect whether dominant language is English ('en'), Chinese ('zh'), or mixed ('mixed')."""
    if not text:
        return "en"
    cjk_count = len(CJK_REGEX.findall(text))
    en_word_count = len(WORD_SPLIT_REGEX.findall(text))

    if cjk_count > 10 and en_word_count > 10:
        ratio = cjk_count / max(1, en_word_count)
        if 0.25 <= ratio <= 2.5:
            return "mixed"
    if cjk_count > en_word_count * 0.5 and cjk_count > 5:
        return "zh"
    return "en"


def count_words_and_sentences(text):
    """
    Compute exact multilingual token/word count (English/numeric words + CJK chars),
    character count, and sentence count.
    """
    if not text:
        return 0, 0, 0

    en_words = len(WORD_SPLIT_REGEX.findall(text))
    cjk_chars = len(CJK_REGEX.findall(text))
    word_count = en_words + cjk_chars
    char_count = len(text)

    sentences = [s for s in SENTENCE_SPLIT_REGEX.split(text) if s.strip()]
    sentence_count = max(1, len(sentences)) if word_count > 0 else 0

    return word_count, char_count, sentence_count


# ----------------------------------------------------------------------
# 2. Scope & Constraint Parser
# ----------------------------------------------------------------------

def extract_scope_constraints(task_text, case_data=None):
    """
    Parse task.md or case.json instructions to detect page, section, or reference policy scopes.
    """
    scope = {
        "is_constrained": False,
        "page_range": None,
        "target_sections": [],
        "include_references": False,
        "raw_directives": [],
    }

    combined_text = ""
    if task_text:
        combined_text += task_text + "\n"
    if case_data:
        combined_text += json.dumps(case_data, ensure_ascii=False)

    if not combined_text:
        return scope

    p_match = re.search(r"(?:pages?|第)\s*([0-9]+)\s*(?:-|to|到|至)\s*([0-9]+)\s*(?:pages?|页)?", combined_text, re.I)
    if p_match:
        start_p = int(p_match.group(1))
        end_p = int(p_match.group(2))
        scope["is_constrained"] = True
        scope["page_range"] = [start_p, end_p]
        scope["raw_directives"].append(f"page_range: {start_p}-{end_p}")

    p_first = re.search(r"(?:first|前)\s*([0-9]+)\s*(?:pages?|页)", combined_text, re.I)
    if p_first and not scope["page_range"]:
        n = int(p_first.group(1))
        scope["is_constrained"] = True
        scope["page_range"] = [1, n]
        scope["raw_directives"].append(f"first_{n}_pages")

    sec_keywords = [
        "Abstract", "Introduction", "Methodology", "Methods", "Related Work",
        "Experiments", "Evaluation", "Discussion", "Limitations", "Conclusion",
        "摘要", "引言", "方法", "实验", "讨论", "局限性", "结论",
    ]
    for kw in sec_keywords:
        if re.search(rf"(?:only|just|聚焦|仅分析|仅总结|只总结|只看)\s*(?:the\s*)?{re.escape(kw)}\b", combined_text, re.I):
            scope["is_constrained"] = True
            scope["target_sections"].append(kw)
            scope["raw_directives"].append(f"section: {kw}")

    if re.search(r"(?:analyze\s+(?:the\s+)?references|include\s+references|分析参考文献|提取参考文献)", combined_text, re.I):
        scope["include_references"] = True
        scope["raw_directives"].append("include_references=True")

    return scope


def is_unit_in_scope(unit, scope):
    """Check if unit satisfies scope constraints."""
    if not scope or not scope.get("is_constrained"):
        return True

    page_range = scope.get("page_range")
    if page_range:
        u_start, u_end = unit.get("page_span", [1, 1])
        if u_end < page_range[0] or u_start > page_range[1]:
            return False

    target_sections = scope.get("target_sections")
    if target_sections:
        sec_path = unit.get("section_path", "").lower()
        if not any(sec.lower() in sec_path for sec in target_sections):
            return False

    return True


# ----------------------------------------------------------------------
# 3. Monotonic Section Hierarchy Tracker
# ----------------------------------------------------------------------

def infer_heading_level(heading_text, b_type=None):
    """
    Determine logical heading depth (1, 2, 3) based strictly on numbering structure
    first, falling back to typographic size class.
    """
    t = heading_text.strip()
    clean_lower = re.sub(r"[^a-z\u4e00-\u9fa5\s]", "", t.lower()).strip()

    if clean_lower in STANDALONE_HEADINGS:
        return 1

    if re.match(r"^(?:[0-9]+\.[0-9]+\.[0-9]+)\b", t):
        return 3
    if re.match(r"^(?:[0-9]+\.[0-9]+)\b", t):
        return 2
    if re.match(r"^(?:[0-9]+\.?\s+[A-Z\u4e00-\u9fa5])", t):
        return 1

    if re.match(r"^[IVXLCDM]+\.\s+", t):
        return 1
    if re.match(r"^[A-Z]\.\s+", t):
        return 2

    if re.match(r"^[一二三四五六七八九十]+[、\.]", t):
        return 1
    if re.match(r"^（[一二三四五六七八九十]+）", t):
        return 2
    if re.match(r"^\([0-9]+\)", t):
        return 3

    if b_type == "heading_h1":
        return 1
    elif b_type == "heading_h2":
        return 2
    elif b_type == "heading_h3":
        return 3
    return 1


class SectionTracker:
    """Maintains monotonic stack of active (level, heading_title) pairs."""

    def __init__(self):
        self.stack = []

    def push_heading(self, heading_text, level=1):
        clean_h = re.sub(r"\s+", " ", heading_text.strip())
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, clean_h))

    def get_path(self, default="Main"):
        if not self.stack:
            return default
        return " > ".join(item[1] for item in self.stack)


# ----------------------------------------------------------------------
# 4. Document Extractors (PDF + OCR Fallback, DOCX, LaTeX, HTML, MD/TXT)
# ----------------------------------------------------------------------

def _infer_bold_from_block(block):
    """Detect whether a parsed PDF block is predominantly bold from span flags/font names."""
    fonts = block.get("fonts", []) or []
    spans = block.get("spans", []) or []
    if spans:
        bold_chars = 0
        total_chars = 0
        for sp in spans:
            txt = sp.get("raw_text") or sp.get("text") or ""
            n = len(txt.strip())
            if n == 0:
                continue
            total_chars += n
            flags = sp.get("flags", 0)
            fname = (sp.get("font") or "").lower()
            if (flags & 16) or any(k in fname for k in ("bold", "demi", "heavy", "black", "medi", "bx")):
                bold_chars += n
        if total_chars > 0:
            return (bold_chars / total_chars) >= 0.6
    for fn in fonts:
        low = fn.lower()
        if any(k in low for k in ("bold", "demi", "heavy", "black", "bx")):
            return True
    return False


def attempt_scanned_page_ocr(pdf_path, page_num, crops_dir, printed_page, doc_id="D1"):
    """
    Attempt full-page OCR via fitz render + pytesseract/tesseract CLI.
    Returns (extracted_text, confidence) or ("", 0.0) if unavailable or garbled.
    """
    if not render_page_crop_fitz or not run_ocr_on_image:
        return "", 0.0

    try:
        import fitz
        doc = fitz.open(pdf_path)
        rect = doc[page_num - 1].rect
        bbox = [0, 0, float(rect.width), float(rect.height)]
        doc.close()
    except Exception:
        bbox = [0, 0, 595, 842]

    os.makedirs(crops_dir, exist_ok=True)
    out_png = os.path.join(crops_dir, f"{doc_id}_ocr_page_{page_num}.png")
    ok = render_page_crop_fitz(pdf_path, page_num, bbox, out_png, dpi=220)
    if not ok:
        return "", 0.0

    txt, conf = run_ocr_on_image(out_png, lang="eng+chi_sim")
    if not txt:
        txt, conf = run_ocr_on_image(out_png, lang="eng")

    if not txt or len(txt.strip()) < 30:
        return "", 0.0

    if calculate_garbled_metrics:
        _, garbled_ratio = calculate_garbled_metrics(txt)
        if garbled_ratio > 0.15:
            return "", 0.0

    return txt.strip(), conf


def extract_units_from_pdf(pdf_path, work_dir, scope=None, doc_id="D1", rel_path=None):
    """Process PDF into standardized reading units with layout signals and OCR fallback."""
    units = []
    crops_dir = os.path.join(work_dir, "crops")
    include_refs = scope.get("include_references", False) if scope else False
    src_name = rel_path or os.path.basename(pdf_path)

    if diagnose_pdf:
        diagnosis = diagnose_pdf(pdf_path)
    else:
        diagnosis = {"status": "fallback", "pages": [], "printed_page_offset": 0, "page_count": 1}

    dominant_route = diagnosis.get("dominant_route", "native_clean")
    is_scanned = dominant_route.startswith("scanned")

    if parse_layout:
        struct_doc, _ = parse_layout(pdf_path)
    else:
        struct_doc = {"blocks": [], "title": None, "abstract": None, "metadata": {}}

    blocks = struct_doc.get("blocks", [])

    # Handle Scanned / Image-Only PDFs: Attempt OCR first, gracefully degrade if unavailable/unreadable
    if not blocks and is_scanned:
        page_count = diagnosis.get("page_count", 1)
        offset = diagnosis.get("printed_page_offset", 0)
        unit_seq = 1

        for p in range(1, page_count + 1):
            p_printed = str(p + offset)
            ocr_text, ocr_conf = attempt_scanned_page_ocr(pdf_path, p, crops_dir, p_printed, doc_id=doc_id)
            if ocr_text:
                paras = [para.strip() for para in re.split(r"\n{2,}", ocr_text) if para.strip()]
                for para in paras:
                    w_cnt, c_cnt, s_cnt = count_words_and_sentences(para)
                    u_id = f"{doc_id}-U{unit_seq:04d}"
                    unit_seq += 1
                    units.append({
                        "unit_id": u_id,
                        "doc_id": doc_id,
                        "source_file": src_name,
                        "unit_type": "paragraph",
                        "raw_block_type": "paragraph",
                        "section_path": f"Page {p} (OCR Extracted)",
                        "page_span": [p, p],
                        "printed_page_span": [p_printed, p_printed],
                        "source_text": para,
                        "word_count": w_cnt,
                        "char_count": c_cnt,
                        "sentence_count": s_cnt,
                        "reading_policy": "analyze",
                        "source_warnings": ["ocr_extracted"],
                        "uncertainty_flag": True,
                        "avg_size": 10.0,
                        "is_bold": False,
                        "fonts": [],
                        "bbox": [0, 0, 595, 842],
                        "column": "full",
                        "evidence_anchors": [{"page": p, "printed_page": p_printed, "ocr_confidence": ocr_conf}],
                    })
            else:
                u_id = f"{doc_id}-U{unit_seq:04d}"
                unit_seq += 1
                units.append({
                    "unit_id": u_id,
                    "doc_id": doc_id,
                    "source_file": src_name,
                    "unit_type": "unextractable_asset",
                    "raw_block_type": "unextractable_asset",
                    "section_path": f"Page {p} (Scanned Image)",
                    "page_span": [p, p],
                    "printed_page_span": [p_printed, p_printed],
                    "source_text": "[Scanned page image without native text layer; OCR unavailable or unreadable - requires manual visual inspection]",
                    "word_count": 0,
                    "char_count": 0,
                    "sentence_count": 0,
                    "reading_policy": "manual_review_asset",
                    "source_warnings": ["native_text_layer_missing", "ocr_unavailable_or_failed"],
                    "uncertainty_flag": True,
                    "avg_size": 0.0,
                    "is_bold": False,
                    "fonts": [],
                    "bbox": [0, 0, 595, 842],
                    "column": "full",
                    "evidence_anchors": [{"page": p, "printed_page": p_printed, "bbox": [0, 0, 595, 842]}],
                })
        return units, diagnosis, struct_doc

    # Process native blocks
    sec_tracker = SectionTracker()
    unit_seq = 1

    for b in blocks:
        b_type = b.get("type", "paragraph")
        raw_text = b.get("text", "").strip()
        if not raw_text:
            continue

        p_span = b.get("page_span", [b.get("page", 1), b.get("page", 1)])
        print_span = b.get("printed_page_span", [b.get("printed_page", str(p_span[0])), b.get("printed_page", str(p_span[1]))])

        if b_type in ("heading_h1", "heading_h2", "heading_h3"):
            h_level = infer_heading_level(raw_text, b_type)
            sec_tracker.push_heading(raw_text, level=h_level)
        elif b_type == "reference_entry":
            if not sec_tracker.stack or sec_tracker.stack[-1][1].lower() != "references":
                sec_tracker.push_heading("References", level=1)

        sec_path = sec_tracker.get_path(default="Main")
        if b_type == "title":
            sec_path = "Title"
        elif b_type == "metadata":
            sec_path = "Metadata"
        elif b_type == "abstract":
            sec_path = "Abstract"
        elif b_type == "reference_entry":
            sec_path = "References"

        u_type = "paragraph"
        r_policy = "analyze"
        u_warnings = []

        if b_type == "title":
            u_type = "title"
            r_policy = "analyze"
        elif b_type == "metadata":
            u_type = "metadata"
            r_policy = "preserve_source"
        elif b_type in ("heading_h1", "heading_h2", "heading_h3"):
            u_type = "heading"
            r_policy = "analyze"
        elif b_type == "abstract":
            u_type = "abstract"
            r_policy = "analyze"
        elif b_type == "figure_caption":
            u_type = "figure_caption"
            r_policy = "analyze"
        elif b_type == "table_caption":
            u_type = "table_caption"
            r_policy = "analyze"
        elif b_type == "table_content":
            u_type = "table_content"
            r_policy = "analyze"
        elif b_type == "formula":
            u_type = "display_equation"
            r_policy = "preserve_source"
        elif b_type == "reference_entry":
            u_type = "reference"
            r_policy = "analyze" if include_refs else "reference_locked"
        elif b_type == "footnote":
            u_type = "footnote"
            r_policy = "analyze"

        if b.get("stitched_types"):
            for st in b["stitched_types"]:
                u_warnings.append(f"stitched_{st}")

        if b.get("needs_visual_check"):
            u_warnings.append("potential_font_garble")
        if b.get("has_formula_pua"):
            u_warnings.append("formula_pua_glyphs")

        w_cnt, c_cnt, s_cnt = count_words_and_sentences(raw_text)
        unit_id = f"{doc_id}-U{unit_seq:04d}"
        unit_seq += 1

        units.append({
            "unit_id": unit_id,
            "doc_id": doc_id,
            "source_file": src_name,
            "unit_type": u_type,
            "raw_block_type": b_type,
            "section_path": sec_path,
            "page_span": p_span,
            "printed_page_span": print_span,
            "source_text": raw_text,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt,
            "reading_policy": r_policy,
            "source_warnings": u_warnings,
            "uncertainty_flag": bool(b.get("needs_visual_check") or b.get("stitched_types")),
            "avg_size": b.get("avg_size", 10.0),
            "is_bold": _infer_bold_from_block(b),
            "fonts": b.get("fonts", []),
            "bbox": b.get("bbox", [0, 0, 0, 0]),
            "column": b.get("column", "full"),
            "evidence_anchors": b.get("evidence_anchors", [{
                "page": p_span[0],
                "printed_page": print_span[0],
                "block_id": b.get("block_id", unit_id),
                "bbox": b.get("bbox", [0, 0, 0, 0]),
            }]),
        })

    if not units:
        p_cnt = max(1, diagnosis.get("page_count", 1))
        units.append({
            "unit_id": f"{doc_id}-U0001",
            "doc_id": doc_id,
            "source_file": src_name,
            "unit_type": "unextractable_asset",
            "raw_block_type": "unextractable_asset",
            "section_path": "Unextractable PDF Asset",
            "page_span": [1, p_cnt],
            "printed_page_span": ["1", str(p_cnt)],
            "source_text": "[PDF yielded 0 extractable text units; requires manual visual inspection]",
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "reading_policy": "manual_review_asset",
            "source_warnings": ["zero_extractable_units", "requires_manual_inspection"],
            "uncertainty_flag": True,
            "avg_size": 0.0,
            "is_bold": False,
            "fonts": [],
            "bbox": [0, 0, 595, 842],
            "column": "full",
            "evidence_anchors": [{"page": 1, "printed_page": "1", "bbox": [0, 0, 595, 842]}],
        })

    return units, diagnosis, struct_doc


def extract_units_from_markdown(md_path, doc_id="D1", rel_path=None):
    """Process Markdown / TXT into standardized reading units."""
    with open(md_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    src_name = rel_path or os.path.basename(md_path)
    units = []
    lines = content.splitlines()
    sec_tracker = SectionTracker()
    unit_seq = 1
    curr_para = []
    in_code_block = False

    def flush_para():
        nonlocal curr_para, unit_seq
        if not curr_para:
            return
        p_text = "\n".join(curr_para).strip()
        curr_para = []
        if not p_text:
            return

        w_cnt, c_cnt, s_cnt = count_words_and_sentences(p_text)
        u_id = f"{doc_id}-U{unit_seq:04d}"
        unit_seq += 1

        is_table = all(ln.strip().startswith("|") for ln in p_text.splitlines() if ln.strip())
        u_type = "table_content" if is_table else "paragraph"
        in_refs = sec_tracker.get_path().lower().startswith("references") or "参考文献" in sec_tracker.get_path()
        if in_refs and u_type == "paragraph":
            u_type = "reference"
            r_policy = "reference_locked"
        else:
            r_policy = "analyze"

        units.append({
            "unit_id": u_id,
            "doc_id": doc_id,
            "source_file": src_name,
            "unit_type": u_type,
            "raw_block_type": u_type,
            "section_path": sec_tracker.get_path(),
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": p_text,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt,
            "reading_policy": r_policy,
            "source_warnings": [],
            "uncertainty_flag": False,
            "avg_size": 10.0,
            "is_bold": False,
            "fonts": [],
            "bbox": [0, 0, 0, 0],
            "column": "full",
            "evidence_anchors": [{"line_anchor": p_text[:45]}],
        })

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                curr_para.append(line)
                c_text = "\n".join(curr_para).strip()
                curr_para = []
                u_id = f"{doc_id}-U{unit_seq:04d}"
                unit_seq += 1
                w_cnt, c_cnt, _ = count_words_and_sentences(c_text)
                units.append({
                    "unit_id": u_id,
                    "doc_id": doc_id,
                    "source_file": src_name,
                    "unit_type": "code_block",
                    "raw_block_type": "code_block",
                    "section_path": sec_tracker.get_path(),
                    "page_span": [1, 1],
                    "printed_page_span": ["1", "1"],
                    "source_text": c_text,
                    "word_count": w_cnt,
                    "char_count": c_cnt,
                    "sentence_count": 1,
                    "reading_policy": "preserve_source",
                    "source_warnings": [],
                    "uncertainty_flag": False,
                    "avg_size": 10.0,
                    "is_bold": False,
                    "fonts": [],
                    "bbox": [0, 0, 0, 0],
                    "column": "full",
                    "evidence_anchors": [],
                })
                in_code_block = False
            else:
                flush_para()
                in_code_block = True
                curr_para.append(line)
            continue

        if in_code_block:
            curr_para.append(line)
            continue

        if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
            flush_para()
            u_id = f"{doc_id}-U{unit_seq:04d}"
            unit_seq += 1
            w_cnt, c_cnt, _ = count_words_and_sentences(stripped)
            units.append({
                "unit_id": u_id,
                "doc_id": doc_id,
                "source_file": src_name,
                "unit_type": "display_equation",
                "raw_block_type": "formula",
                "section_path": sec_tracker.get_path(),
                "page_span": [1, 1],
                "printed_page_span": ["1", "1"],
                "source_text": stripped,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": 1,
                "reading_policy": "preserve_source",
                "source_warnings": [],
                "uncertainty_flag": False,
                "avg_size": 10.0,
                "is_bold": False,
                "fonts": [],
                "bbox": [0, 0, 0, 0],
                "column": "full",
                "evidence_anchors": [],
            })
            continue

        h_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if h_match:
            flush_para()
            h_level = len(h_match.group(1))
            h_text = h_match.group(2).strip()
            sec_tracker.push_heading(h_text, level=h_level)
            w_cnt, c_cnt, s_cnt = count_words_and_sentences(h_text)
            u_id = f"{doc_id}-U{unit_seq:04d}"
            unit_seq += 1
            u_type = "title" if (h_level == 1 and unit_seq == 2) else "heading"
            units.append({
                "unit_id": u_id,
                "doc_id": doc_id,
                "source_file": src_name,
                "unit_type": u_type,
                "raw_block_type": f"heading_h{min(3, h_level)}",
                "section_path": "Title" if u_type == "title" else sec_tracker.get_path(),
                "page_span": [1, 1],
                "printed_page_span": ["1", "1"],
                "source_text": h_text,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": s_cnt,
                "reading_policy": "analyze",
                "source_warnings": [],
                "uncertainty_flag": False,
                "avg_size": 16.0 if h_level == 1 else (13.0 if h_level == 2 else 11.5),
                "is_bold": True,
                "fonts": [],
                "bbox": [0, 0, 0, 0],
                "column": "full",
                "evidence_anchors": [],
            })
            continue

        if not stripped:
            flush_para()
        else:
            curr_para.append(line)

    flush_para()
    return units


def extract_units_from_docx(docx_path, doc_id="D1", rel_path=None):
    """Extract units from DOCX using pure standard library zipfile/xml parsing."""
    src_name = rel_path or os.path.basename(docx_path)
    units = []
    unit_seq = 1
    sec_tracker = SectionTracker()

    try:
        with zipfile.ZipFile(docx_path) as z:
            xml_content = z.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

            for p in tree.iter(f"{{{ns['w']}}}p"):
                texts = [node.text for node in p.iter(f"{{{ns['w']}}}t") if node.text]
                p_text = "".join(texts).strip()
                if not p_text:
                    continue

                p_style = p.find(f".//{{{ns['w']}}}pStyle")
                style_val = p_style.attrib.get(f"{{{ns['w']}}}val", "") if p_style is not None else ""

                u_type = "paragraph"
                raw_bt = "paragraph"
                is_bold = False
                if "title" in style_val.lower():
                    u_type = "title"
                    raw_bt = "title"
                    is_bold = True
                elif "heading 1" in style_val.lower() or style_val == "1":
                    u_type = "heading"
                    raw_bt = "heading_h1"
                    is_bold = True
                    sec_tracker.push_heading(p_text, level=1)
                elif "heading 2" in style_val.lower() or style_val == "2":
                    u_type = "heading"
                    raw_bt = "heading_h2"
                    is_bold = True
                    sec_tracker.push_heading(p_text, level=2)
                elif "heading 3" in style_val.lower() or style_val == "3":
                    u_type = "heading"
                    raw_bt = "heading_h3"
                    is_bold = True
                    sec_tracker.push_heading(p_text, level=3)

                w_cnt, c_cnt, s_cnt = count_words_and_sentences(p_text)
                u_id = f"{doc_id}-U{unit_seq:04d}"
                unit_seq += 1

                units.append({
                    "unit_id": u_id,
                    "doc_id": doc_id,
                    "source_file": src_name,
                    "unit_type": u_type,
                    "raw_block_type": raw_bt,
                    "section_path": "Title" if u_type == "title" else sec_tracker.get_path(),
                    "page_span": [1, 1],
                    "printed_page_span": ["1", "1"],
                    "source_text": p_text,
                    "word_count": w_cnt,
                    "char_count": c_cnt,
                    "sentence_count": s_cnt,
                    "reading_policy": "analyze",
                    "source_warnings": [],
                    "uncertainty_flag": False,
                    "avg_size": 14.0 if u_type == "heading" else 10.0,
                    "is_bold": is_bold,
                    "fonts": [],
                    "bbox": [0, 0, 0, 0],
                    "column": "full",
                    "evidence_anchors": [{"line_anchor": p_text[:45]}],
                })
    except Exception as e:
        units.append({
            "unit_id": f"{doc_id}-U0001",
            "doc_id": doc_id,
            "source_file": src_name,
            "unit_type": "unextractable_asset",
            "raw_block_type": "unextractable_asset",
            "section_path": "DOCX Parser Error",
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": f"Error parsing docx: {str(e)}",
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "reading_policy": "manual_review_asset",
            "source_warnings": [str(e)],
            "uncertainty_flag": True,
            "avg_size": 0.0,
            "is_bold": False,
            "fonts": [],
            "bbox": [0, 0, 0, 0],
            "column": "full",
            "evidence_anchors": [],
        })

    return units


def _extract_braced_arg(text, brace_open_idx):
    """Extract balanced {...} argument starting at brace_open_idx, returning (inner, end_idx)."""
    if brace_open_idx < 0 or brace_open_idx >= len(text) or text[brace_open_idx] != "{":
        return "", brace_open_idx
    depth = 0
    i = brace_open_idx
    buf = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in ("{", "}", "\\"):
            buf.append(text[i:i + 2])
            i += 2
            continue
        if ch == "{":
            if depth > 0:
                buf.append(ch)
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(buf).strip(), i + 1
            buf.append(ch)
        else:
            if depth > 0:
                buf.append(ch)
        i += 1
    return "".join(buf).strip(), len(text)


def extract_units_from_latex(tex_path, doc_id="D1", rel_path=None):
    """Parse .tex source into structured reading units preserving math/bib/captions/sections."""
    with open(tex_path, "r", encoding="utf-8", errors="replace") as f:
        raw_content = f.read()

    src_name = rel_path or os.path.basename(tex_path)
    cleaned_lines = [re.sub(r"(^|[^\\](?:\\\\)*)%.*$", r"\1", ln) for ln in raw_content.splitlines()]
    full_text = "\n".join(cleaned_lines)

    units = []
    sec_tracker = SectionTracker()
    unit_seq = 1

    def add_unit(u_type, raw_bt, sec_path, text, policy="analyze", is_bold=False):
        nonlocal unit_seq
        t = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not t:
            return
        w_cnt, c_cnt, s_cnt = count_words_and_sentences(t)
        u_id = f"{doc_id}-U{unit_seq:04d}"
        unit_seq += 1
        units.append({
            "unit_id": u_id,
            "doc_id": doc_id,
            "source_file": src_name,
            "unit_type": u_type,
            "raw_block_type": raw_bt,
            "section_path": sec_path,
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": t,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt if policy == "analyze" else 1,
            "reading_policy": policy,
            "source_warnings": [],
            "uncertainty_flag": False,
            "avg_size": 14.0 if u_type in ("title", "heading") else 10.0,
            "is_bold": is_bold,
            "fonts": [],
            "bbox": [0, 0, 0, 0],
            "column": "full",
            "evidence_anchors": [{"line_anchor": t[:45]}],
        })

    m_title = re.search(r"\\title\*?(?:\[[^\]]*\])?\s*\{", full_text)
    if m_title:
        title_txt, _ = _extract_braced_arg(full_text, m_title.end() - 1)
        title_clean = re.sub(r"\s+", " ", re.sub(r"\\\\", " ", title_txt)).strip()
        if title_clean:
            add_unit("title", "title", "Title", title_clean, "analyze", is_bold=True)

    m_begin_doc = re.search(r"\\begin\{document\}", full_text)
    body_text = full_text[m_begin_doc.end():] if m_begin_doc else full_text
    m_end_doc = re.search(r"\\end\{document\}", body_text)
    if m_end_doc:
        body_text = body_text[:m_end_doc.start()]

    lines = body_text.splitlines()
    para_buf = []
    env_mode = None
    env_end_pat = None
    env_buf = []
    float_ctx = None

    def flush_para():
        nonlocal para_buf
        if not para_buf:
            return
        p_txt = " ".join(x.strip() for x in para_buf if x.strip())
        para_buf = []
        p_txt = re.sub(r"\s+", " ", p_txt).strip()
        if not p_txt:
            return
        if re.match(r"^(?:\\(?:maketitle|tableofcontents|clearpage|newpage|centering|bibliographystyle\{[^}]*\}|bibliography\{[^}]*\}|label\{[^}]*\}|vspace\*?\{[^}]*\}|hspace\*?\{[^}]*\}|includegraphics(?:\[[^\]]*\])?\{[^}]*\}|begin\{(?:figure|table|center|itemize|enumerate)\*?\}|end\{(?:figure|table|center|itemize|enumerate)\*?\}))+$", p_txt):
            return
        add_unit("paragraph", "paragraph", sec_tracker.get_path(), p_txt, "analyze")

    math_envs = {"equation", "equation*", "align", "align*", "gather", "gather*", "eqnarray", "eqnarray*", "multline", "multline*"}
    code_envs = {"verbatim", "lstlisting", "minted", "algorithmic"}

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        if env_mode is not None:
            env_buf.append(line)
            if env_end_pat.search(line):
                block_txt = "\n".join(env_buf).strip()
                env_buf = []
                mode_done = env_mode
                env_mode = None
                env_end_pat = None
                if mode_done == "abstract":
                    inner = re.sub(r"\\begin\{abstract\}|\\end\{abstract\}", "", block_txt).strip()
                    inner = re.sub(r"\s+", " ", inner)
                    add_unit("abstract", "abstract", "Abstract", inner, "analyze")
                elif mode_done == "math":
                    add_unit("display_equation", "formula", sec_tracker.get_path(), block_txt, "preserve_source")
                elif mode_done == "code":
                    add_unit("code_block", "code_block", sec_tracker.get_path(), block_txt, "preserve_source")
                elif mode_done == "table":
                    add_unit("table_content", "table_content", sec_tracker.get_path(), block_txt, "analyze")
                elif mode_done == "bib":
                    sec_tracker.push_heading("References", level=1)
                    inner = re.sub(r"\\begin\{thebibliography\}(?:\{[^}]*\})?|\\end\{thebibliography\}", "", block_txt).strip()
                    bib_chunks = re.split(r"(?=\\bibitem)", inner)
                    for bc in bib_chunks:
                        bc_clean = re.sub(r"\s+", " ", bc).strip()
                        if bc_clean:
                            add_unit("reference", "reference_entry", "References", bc_clean, "reference_locked")
            idx += 1
            continue

        if re.search(r"\\begin\{table\*?\}", stripped):
            float_ctx = "table"
        elif re.search(r"\\begin\{figure\*?\}", stripped):
            float_ctx = "figure"
        elif re.search(r"\\end\{(?:table|figure)\*?\}", stripped):
            float_ctx = None

        m_env = re.match(r"^\\begin\{([a-zA-Z*]+)\}", stripped)
        if m_env:
            env_name = m_env.group(1)
            if env_name == "abstract":
                flush_para()
                end_p = re.compile(r"\\end\{abstract\}")
                if end_p.search(line):
                    inner = re.sub(r"\\begin\{abstract\}|\\end\{abstract\}", "", stripped).strip()
                    add_unit("abstract", "abstract", "Abstract", re.sub(r"\s+", " ", inner), "analyze")
                else:
                    env_mode, env_end_pat, env_buf = "abstract", end_p, [line]
                idx += 1
                continue
            elif env_name in math_envs:
                flush_para()
                end_p = re.compile(r"\\end\{" + re.escape(env_name) + r"\}")
                if end_p.search(line):
                    add_unit("display_equation", "formula", sec_tracker.get_path(), stripped, "preserve_source")
                else:
                    env_mode, env_end_pat, env_buf = "math", end_p, [line]
                idx += 1
                continue
            elif env_name in code_envs:
                flush_para()
                end_p = re.compile(r"\\end\{" + re.escape(env_name) + r"\}")
                if end_p.search(line):
                    add_unit("code_block", "code_block", sec_tracker.get_path(), stripped, "preserve_source")
                else:
                    env_mode, env_end_pat, env_buf = "code", end_p, [line]
                idx += 1
                continue
            elif env_name == "tabular":
                flush_para()
                end_p = re.compile(r"\\end\{tabular\}")
                if end_p.search(line):
                    add_unit("table_content", "table_content", sec_tracker.get_path(), stripped, "analyze")
                else:
                    env_mode, env_end_pat, env_buf = "table", end_p, [line]
                idx += 1
                continue
            elif env_name == "thebibliography":
                flush_para()
                end_p = re.compile(r"\\end\{thebibliography\}")
                env_mode, env_end_pat, env_buf = "bib", end_p, [line]
                idx += 1
                continue
            elif env_name in ("figure", "figure*", "table", "table*", "document", "center"):
                flush_para()
                idx += 1
                continue

        if stripped.startswith(r"\["):
            flush_para()
            end_p = re.compile(r"\\\]")
            if end_p.search(stripped[2:]):
                add_unit("display_equation", "formula", sec_tracker.get_path(), stripped, "preserve_source")
            else:
                env_mode, env_end_pat, env_buf = "math", end_p, [line]
            idx += 1
            continue
        if stripped.startswith("$$"):
            flush_para()
            if stripped.endswith("$$") and len(stripped) > 4:
                add_unit("display_equation", "formula", sec_tracker.get_path(), stripped, "preserve_source")
            else:
                env_mode, env_end_pat, env_buf = "math", re.compile(r"\$\$\s*$"), [line]
            idx += 1
            continue

        m_sec = re.match(r"^\\(section|subsection|subsubsection|paragraph)\*?(?:\[[^\]]*\])?\s*\{", stripped)
        if m_sec:
            flush_para()
            cmd = m_sec.group(1)
            lvl = {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}[cmd]
            h_txt, _ = _extract_braced_arg(stripped, m_sec.end() - 1)
            h_txt = re.sub(r"\s+", " ", h_txt).strip()
            if h_txt:
                sec_tracker.push_heading(h_txt, level=lvl)
                add_unit("heading", f"heading_h{min(3, lvl)}", sec_tracker.get_path(), h_txt, "analyze", is_bold=True)
            idx += 1
            continue

        m_cap = re.match(r"^\\caption\*?(?:\[[^\]]*\])?\s*\{", stripped)
        if m_cap:
            flush_para()
            cap_txt, _ = _extract_braced_arg(stripped, m_cap.end() - 1)
            cap_txt = re.sub(r"\s+", " ", cap_txt).strip()
            if cap_txt:
                u_t = "table_caption" if float_ctx == "table" else "figure_caption"
                add_unit(u_t, u_t, sec_tracker.get_path(), cap_txt, "analyze")
            idx += 1
            continue

        if re.match(r"^\\(?:maketitle|tableofcontents|clearpage|newpage|centering|end\{(?:figure|table|center|document)\*?\}|label\{[^}]*\}|includegraphics(?:\[[^\]]*\])?\{[^}]*\}|bibliographystyle\{[^}]*\}|bibliography\{[^}]*\})\s*$", stripped):
            idx += 1
            continue
        if re.match(r"^\\title\*?(?:\[[^\]]*\])?\s*\{", stripped):
            idx += 1
            continue

        if not stripped:
            flush_para()
        else:
            para_buf.append(line)
        idx += 1

    flush_para()
    return units


class _ScientificHTMLParser(HTMLParser):
    """Stdlib HTMLParser extracting semantic blocks from HTML documents."""
    SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}
    BLOCK_TAGS = {"title", "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "figcaption", "caption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.active_tag = None
        self.buf = []
        self.in_table = False
        self.table_rows = []
        self.curr_row = []
        self.in_cell = False
        self.cell_buf = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            if t == "head":
                return
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return
        if t == "table":
            self._flush_active()
            self.in_table = True
            self.table_rows = []
        elif self.in_table and t == "tr":
            self.curr_row = []
        elif self.in_table and t in ("td", "th"):
            self.in_cell = True
            self.cell_buf = []
        elif t in self.BLOCK_TAGS and not self.in_table:
            self._flush_active()
            self.active_tag = t
            self.buf = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg") and self.skip_depth > 0:
            self.skip_depth -= 1
            return
        if self.skip_depth > 0:
            return
        if self.in_table:
            if t in ("td", "th") and self.in_cell:
                cell_txt = re.sub(r"\s+", " ", "".join(self.cell_buf)).strip()
                self.curr_row.append(cell_txt)
                self.cell_buf = []
                self.in_cell = False
            elif t == "tr":
                if any(self.curr_row):
                    self.table_rows.append(" | ".join(self.curr_row))
                self.curr_row = []
            elif t == "table":
                if self.table_rows:
                    self.items.append(("table", "\n".join(self.table_rows)))
                self.table_rows = []
                self.in_table = False
        elif t == self.active_tag:
            self._flush_active()

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        if self.in_table:
            if self.in_cell:
                self.cell_buf.append(data)
        elif self.active_tag:
            self.buf.append(data)

    def _flush_active(self):
        if self.active_tag and self.buf:
            raw = "".join(self.buf)
            txt = raw.strip() if self.active_tag == "pre" else re.sub(r"\s+", " ", raw).strip()
            if txt:
                self.items.append((self.active_tag, txt))
        self.active_tag = None
        self.buf = []


def extract_units_from_html(html_path, doc_id="D1", rel_path=None):
    """Extract structured reading units from HTML using standard library html.parser."""
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        raw_html = f.read()

    src_name = rel_path or os.path.basename(html_path)
    parser = _ScientificHTMLParser()
    parser.feed(raw_html)
    parser.close()
    parser._flush_active()

    units = []
    sec_tracker = SectionTracker()
    unit_seq = 1

    for tag, text in parser.items:
        u_type = "paragraph"
        raw_bt = "paragraph"
        policy = "analyze"
        is_bold = False
        avg_size = 10.0

        if tag == "title":
            u_type = "title"
            raw_bt = "title"
            sec_path = "Title"
            is_bold = True
            avg_size = 16.0
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lvl = int(tag[1])
            sec_tracker.push_heading(text, level=lvl)
            u_type = "heading"
            raw_bt = f"heading_h{min(3, lvl)}"
            sec_path = sec_tracker.get_path()
            is_bold = True
            avg_size = 14.0 if lvl == 1 else (12.0 if lvl == 2 else 11.0)
        elif tag == "pre":
            u_type = "code_block"
            raw_bt = "code_block"
            policy = "preserve_source"
            sec_path = sec_tracker.get_path()
        elif tag == "table":
            u_type = "table_content"
            raw_bt = "table_content"
            sec_path = sec_tracker.get_path()
        elif tag == "figcaption":
            u_type = "figure_caption"
            raw_bt = "figure_caption"
            sec_path = sec_tracker.get_path()
        elif tag == "caption":
            u_type = "table_caption"
            raw_bt = "table_caption"
            sec_path = sec_tracker.get_path()
        else:
            sec_path = sec_tracker.get_path()

        w_cnt, c_cnt, s_cnt = count_words_and_sentences(text)
        u_id = f"{doc_id}-U{unit_seq:04d}"
        unit_seq += 1
        units.append({
            "unit_id": u_id,
            "doc_id": doc_id,
            "source_file": src_name,
            "unit_type": u_type,
            "raw_block_type": raw_bt,
            "section_path": sec_path,
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": text,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt if policy == "analyze" else 1,
            "reading_policy": policy,
            "source_warnings": [],
            "uncertainty_flag": False,
            "avg_size": avg_size,
            "is_bold": is_bold,
            "fonts": [],
            "bbox": [0, 0, 0, 0],
            "column": "full",
            "evidence_anchors": [{"line_anchor": text[:45]}],
        })

    if not units:
        cleaned = re.sub(r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>", " ", raw_html, flags=re.I | re.S)
        cleaned = re.sub(r"<[^>]+>", "\n", cleaned)
        for para in [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]:
            w_cnt, c_cnt, s_cnt = count_words_and_sentences(para)
            u_id = f"{doc_id}-U{unit_seq:04d}"
            unit_seq += 1
            units.append({
                "unit_id": u_id,
                "doc_id": doc_id,
                "source_file": src_name,
                "unit_type": "paragraph",
                "raw_block_type": "paragraph",
                "section_path": "Main",
                "page_span": [1, 1],
                "printed_page_span": ["1", "1"],
                "source_text": para,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": s_cnt,
                "reading_policy": "analyze",
                "source_warnings": ["html_fallback_strip"],
                "uncertainty_flag": False,
                "avg_size": 10.0,
                "is_bold": False,
                "fonts": [],
                "bbox": [0, 0, 0, 0],
                "column": "full",
                "evidence_anchors": [],
            })

    return units


# ----------------------------------------------------------------------
# 5. File Discovery & Multi-Document Ingestion Orchestrator
# ----------------------------------------------------------------------

EXT_PRIORITY = {".pdf": 0, ".docx": 1, ".tex": 2, ".html": 3, ".htm": 3, ".md": 4, ".txt": 5}
SKIP_META_FILES = {"task.md", "case.json", "result.json"}


def _walk_input_files(input_dir, exclude_dirs=None):
    """Recursively enumerate visible files under input_dir sorted by (depth, rel_path)."""
    abs_in = os.path.abspath(input_dir)
    ex_set = {os.path.abspath(d) for d in (exclude_dirs or []) if d}
    found = []
    for root, dirs, files in os.walk(abs_in):
        dirs[:] = sorted([
            d for d in dirs
            if not d.startswith(".") and d != "__pycache__"
            and os.path.abspath(os.path.join(root, d)) not in ex_set
        ])
        rel_root = os.path.relpath(root, abs_in)
        depth = 0 if rel_root == "." else len(rel_root.split(os.sep))
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = os.path.abspath(os.path.join(root, fname))
            rel_p = os.path.relpath(fpath, abs_in)
            found.append((depth, rel_p, fpath, fname))
    return found


def _collect_case_declared_paths(case_data, input_dir):
    """Extract explicitly referenced document paths from case.json if present."""
    declared = []
    if not isinstance(case_data, dict):
        return declared
    candidate_keys = ("files", "documents", "papers", "input_files", "sources", "pdf", "pdf_path", "document", "paper", "source_file")
    for k in candidate_keys:
        val = case_data.get(k)
        items = val if isinstance(val, list) else ([val] if isinstance(val, str) else [])
        for it in items:
            raw_p = it.get("path") or it.get("file") if isinstance(it, dict) else (it if isinstance(it, str) else None)
            if not raw_p:
                continue
            cand_p = raw_p if os.path.isabs(raw_p) else os.path.abspath(os.path.join(input_dir, raw_p))
            if os.path.isfile(cand_p):
                declared.append(cand_p)
    return declared


def _derive_semantic_case_id(case_data, task_text, primary_file, input_dir):
    """Resolve human-meaningful case_id preserving original case.json case_id if present."""
    explicit = case_data.get("case_id") or case_data.get("id")
    if explicit and str(explicit).strip():
        return str(explicit).strip()

    if task_text:
        m_h = re.search(r"^\s*#\s+(.+)$", task_text, re.M)
        if m_h:
            raw_t = re.sub(r"^(?:case|task|任务|题目)\s*[:：]\s*", "", m_h.group(1).strip(), flags=re.I)
            slug = re.sub(r"[^\w\u4e00-\u9fa5\-]+", "_", raw_t).strip("_")[:48]
            if slug and slug.lower() not in ("input", "task", "readme"):
                return slug

    if primary_file:
        stem = os.path.splitext(os.path.basename(primary_file))[0]
        slug = re.sub(r"[^\w\u4e00-\u9fa5\-]+", "_", stem).strip("_")[:48]
        if slug:
            return f"{slug}_case"

    base_dir = os.path.basename(os.path.abspath(input_dir))
    if base_dir and base_dir.lower() not in ("input", "in", "data", "materials", ".", "work"):
        return base_dir
    return "literature_notes_case"


def extract_task_guidance(task_text, case_data=None):
    """Detect expected output language ('zh'/'en'/'auto') and focus directives from task.md / case.json."""
    combined = (task_text or "") + "\n" + (json.dumps(case_data, ensure_ascii=False) if case_data else "")
    expected_lang = "auto"
    if re.search(r"(?:用中文|中文输出|中文结构化笔记|中文笔记|输出语言为中文|简体中文|in\s+Chinese|output\s+in\s+Chinese)", combined, re.I):
        expected_lang = "zh"
    elif re.search(r"(?:output\s+in\s+English|write\s+in\s+English|英文输出|用英文)", combined, re.I):
        expected_lang = "en"
    elif task_text and task_text.strip():
        cjk_n = len(CJK_REGEX.findall(task_text))
        en_n = len(WORD_SPLIT_REGEX.findall(task_text))
        if cjk_n > 15 and cjk_n >= en_n * 0.4:
            expected_lang = "zh"
        elif en_n > 10:
            expected_lang = "en"

    focus = []
    focus_rules = [
        ("paper_card", r"(?:paper\s*card|论文卡片|统一字段)"),
        ("section_summaries", r"(?:section|章节摘要|章节对齐|逐节)"),
        ("knowledge_points", r"(?:knowledge\s*points?|知识点|概念|变量)"),
        ("evidence_anchors", r"(?:anchor|provenance|页码|段落|锚点|回链|原文证据)"),
        ("limitations", r"(?:limitation|assumption|局限|假设|边界)"),
        ("cross_document_comparison", r"(?:compare|comparison|multi-?document|多篇|跨文献|对比)"),
    ]
    for tag, pat in focus_rules:
        if re.search(pat, combined, re.I):
            focus.append(tag)

    return {
        "expected_output_language": expected_lang,
        "focus_directives": focus,
        "task_excerpt": (task_text or "").strip()[:400],
    }


def ingest_case(input_dir, work_dir, single_file=None):
    """
    Universal single/multi-document ingestion pipeline for literature summarization & notes.
    Reads case.json and task.md, discovers documents recursively, extracts layout-aware units,
    and writes source_units.json + ingest_manifest.json in work_dir.
    """
    os.makedirs(work_dir, exist_ok=True)
    input_abs = os.path.abspath(input_dir)
    work_abs = os.path.abspath(work_dir)

    case_json_path = os.path.join(input_abs, "case.json")
    task_md_path = os.path.join(input_abs, "task.md")

    case_data = {}
    if os.path.exists(case_json_path):
        try:
            with open(case_json_path, "r", encoding="utf-8") as f:
                case_data = json.load(f)
        except Exception:
            case_data = {}

    task_text = ""
    if os.path.exists(task_md_path):
        try:
            with open(task_md_path, "r", encoding="utf-8") as f:
                task_text = f.read()
        except Exception:
            task_text = ""

    output_contract = case_data.get("output_contract", {})
    scope = extract_scope_constraints(task_text, case_data)
    task_guidance = extract_task_guidance(task_text, case_data)

    exclude_dirs = [work_abs]
    target_files = []

    if single_file and os.path.exists(single_file):
        abs_sf = os.path.abspath(single_file)
        rel_sf = os.path.basename(abs_sf)
        target_files = [(abs_sf, rel_sf)]
    else:
        seen_paths = set()
        for dp in _collect_case_declared_paths(case_data, input_abs):
            ext = os.path.splitext(dp)[1].lower()
            if ext in EXT_PRIORITY and dp not in seen_paths:
                seen_paths.add(dp)
                target_files.append((dp, os.path.relpath(dp, input_abs)))

        discovered = []
        non_readme = []
        for depth, rel_p, fpath, fname in _walk_input_files(input_abs, exclude_dirs=exclude_dirs):
            low_fn = fname.lower()
            if low_fn in SKIP_META_FILES or fpath in seen_paths:
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXT_PRIORITY:
                item = (depth, EXT_PRIORITY[ext], rel_p, fpath, low_fn == "readme.md")
                discovered.append(item)
                if low_fn != "readme.md":
                    non_readme.append(item)

        chosen_pool = non_readme if non_readme else discovered
        chosen_pool.sort(key=lambda x: (x[0], x[1], x[2]))
        for _d, _p, rel_p, fpath, _is_rm in chosen_pool:
            if fpath not in seen_paths:
                seen_paths.add(fpath)
                target_files.append((fpath, rel_p))

    primary_file = target_files[0][0] if target_files else None
    case_id = _derive_semantic_case_id(case_data, task_text, primary_file, input_abs)

    all_units = []
    documents_meta = []

    for idx, (fpath, rel_p) in enumerate(target_files, start=1):
        doc_id = f"D{idx}"
        ext = os.path.splitext(fpath)[1].lower()
        doc_units = []
        diag_summary = None
        doc_format = "unknown"

        if ext == ".pdf":
            doc_format = "pdf"
            doc_units, diagnosis, _struct_doc = extract_units_from_pdf(
                fpath, work_abs, scope=scope, doc_id=doc_id, rel_path=rel_p
            )
            diag_summary = {
                "dominant_route": diagnosis.get("dominant_route", "unknown"),
                "page_count": diagnosis.get("page_count", 1),
                "printed_page_offset": diagnosis.get("printed_page_offset", 0),
                "pages": diagnosis.get("pages", []),
            }
        elif ext == ".docx":
            doc_format = "docx"
            doc_units = extract_units_from_docx(fpath, doc_id=doc_id, rel_path=rel_p)
        elif ext == ".tex":
            doc_format = "latex"
            doc_units = extract_units_from_latex(fpath, doc_id=doc_id, rel_path=rel_p)
        elif ext in (".html", ".htm"):
            doc_format = "html"
            doc_units = extract_units_from_html(fpath, doc_id=doc_id, rel_path=rel_p)
        elif ext in (".md", ".txt"):
            doc_format = "markdown" if ext == ".md" else "txt"
            doc_units = extract_units_from_markdown(fpath, doc_id=doc_id, rel_path=rel_p)

        for u in doc_units:
            u["in_scope"] = is_unit_in_scope(u, scope)
            if not u["in_scope"]:
                u["reading_policy"] = "scope_excluded"

        sample_txt = " ".join(u.get("source_text", "") for u in doc_units[:12])
        doc_lang = detect_language(sample_txt)
        all_units.extend(doc_units)

        documents_meta.append({
            "doc_id": doc_id,
            "source_file": os.path.basename(fpath),
            "rel_path": rel_p.replace("\\", "/"),
            "abs_path": fpath,
            "format": doc_format,
            "language": doc_lang,
            "unit_count": len(doc_units),
            "page_count": diag_summary["page_count"] if diag_summary else 1,
            "diagnosis_summary": diag_summary,
        })

    if not all_units:
        warn_reason = "no_supported_source_files_found" if not target_files else "zero_extractable_units"
        all_units.append({
            "unit_id": "D1-U0001",
            "doc_id": "D1",
            "source_file": os.path.basename(primary_file) if primary_file else "none",
            "unit_type": "unextractable_asset",
            "raw_block_type": "unextractable_asset",
            "section_path": "Ingestion Fallback",
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": f"[Ingestion yielded 0 extractable units ({warn_reason}) - manual inspection required]",
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "reading_policy": "manual_review_asset",
            "source_warnings": [warn_reason],
            "uncertainty_flag": True,
            "avg_size": 0.0,
            "is_bold": False,
            "fonts": [],
            "bbox": [0, 0, 0, 0],
            "column": "full",
            "in_scope": True,
            "evidence_anchors": [],
        })

    sample_all = " ".join(u.get("source_text", "") for u in all_units[:15])
    overall_lang = detect_language(sample_all)

    source_units_path = os.path.join(work_abs, "source_units.json")
    with open(source_units_path, "w", encoding="utf-8") as f:
        json.dump({
            "case_id": case_id,
            "doc_count": len(documents_meta),
            "documents": documents_meta,
            "overall_language": overall_lang,
            "total_units": len(all_units),
            "units": all_units,
        }, f, ensure_ascii=False, indent=2)

    type_counts = collections.Counter(u.get("unit_type") for u in all_units)
    policy_counts = collections.Counter(u.get("reading_policy") for u in all_units)

    manifest = {
        "status": "success" if target_files else "partial",
        "case_id": case_id,
        "input_dir": input_abs,
        "doc_count": len(documents_meta),
        "is_multi_document": len(documents_meta) > 1,
        "documents": [
            {
                "doc_id": d["doc_id"],
                "source_file": d["source_file"],
                "rel_path": d["rel_path"],
                "format": d["format"],
                "language": d["language"],
                "unit_count": d["unit_count"],
                "page_count": d["page_count"],
                "dominant_route": d["diagnosis_summary"]["dominant_route"] if d["diagnosis_summary"] else "n/a",
            }
            for d in documents_meta
        ],
        "overall_language": overall_lang,
        "scope_constraint": scope,
        "total_units": len(all_units),
        "unit_type_breakdown": dict(type_counts),
        "reading_policy_breakdown": dict(policy_counts),
        "unextractable_asset_count": type_counts.get("unextractable_asset", 0),
        "total_warnings_count": sum(len(u.get("source_warnings", [])) for u in all_units),
        "case_metadata": {k: v for k, v in case_data.items() if k != "output_contract"},
        "task_md_present": bool(task_text),
        "task_guidance": task_guidance,
        "output_contract": output_contract,
    }

    manifest_path = os.path.join(work_abs, "ingest_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(
        f"[case_ingestor] case_id={case_id} | docs={len(documents_meta)} | units={len(all_units)} "
        f"| warnings={manifest['total_warnings_count']} -> {work_abs}"
    )
    return manifest


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Universal Scientific Material Ingestion Engine")
    parser.add_argument("--input-dir", default=".", help="Directory containing input case files")
    parser.add_argument("--work-dir", required=True, help="Directory to save ingested intermediate artifacts")
    parser.add_argument("--file", default=None, help="Optional single file mode for debugging")
    args = parser.parse_args()

    input_d = args.input_dir
    if args.file and args.input_dir == ".":
        input_d = os.path.dirname(os.path.abspath(args.file))

    ingest_case(
        input_dir=input_d,
        work_dir=args.work_dir,
        single_file=args.file,
    )


if __name__ == "__main__":
    main()
