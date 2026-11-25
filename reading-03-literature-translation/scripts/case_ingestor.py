#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case_ingestor.py - Universal Scientific Material Ingestion & Adaptive Batch Slicing Engine
Part of the Scientific Translation Skill (task_03_translation).
Strict zero-crash design: top-level imports are standard libraries only.
"""

import os
import sys
import json
import re
import math
import shutil
import subprocess
import argparse
import collections
import glob
import fnmatch
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
    """Detect whether dominant language is English or Chinese."""
    if not text:
        return "en"
    cjk_count = len(CJK_REGEX.findall(text))
    words = WORD_SPLIT_REGEX.findall(text)
    en_word_count = len(words)

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
        "translate_references": False,
        "raw_directives": []
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

    sec_keywords = ["Abstract", "Introduction", "Methodology", "Related Work", "Conclusion", "摘要", "引言", "结论"]
    for kw in sec_keywords:
        if re.search(rf"\b(?:only|just|聚焦|仅翻译|只翻译)\s*(?:the\s*)?{kw}\b", combined_text, re.I):
            scope["is_constrained"] = True
            scope["target_sections"].append(kw)
            scope["raw_directives"].append(f"section: {kw}")

    if re.search(r"(?:translate\s+(?:the\s+)?references|翻译参考文献)", combined_text, re.I):
        scope["translate_references"] = True
        scope["raw_directives"].append("translate_references=True")

    return scope


def is_unit_in_scope(unit, scope):
    """Check if unit satisfies scope constraints."""
    if not scope.get("is_constrained"):
        return True

    page_range = scope.get("page_range")
    if page_range:
        u_start, u_end = unit.get("page_span", [1, 1])
        if u_end < page_range[0] or u_start > page_range[1]:
            return False

    target_sections = scope.get("target_sections")
    if target_sections:
        sec_path = unit.get("section_path", "").lower()
        matched = False
        for sec in target_sections:
            if sec.lower() in sec_path:
                matched = True
                break
        if not matched:
            return False

    return True


# ----------------------------------------------------------------------
# 3. Monotonic Section Hierarchy Tracker
# ----------------------------------------------------------------------

def infer_heading_level(heading_text, b_type=None):
    """
    Determine logical heading depth (1, 2, 3) based strictly on numbering structure
    first, falling back to typographic size class. Prevents stack push bugs.
    """
    t = heading_text.strip()
    clean_lower = re.sub(r"[^a-z\u4e00-\u9fa5\s]", "", t.lower()).strip()

    # 1. Explicit standalone top-level headings
    if clean_lower in STANDALONE_HEADINGS:
        return 1

    # 2. Multi-level dotted numbers: 2.1.1 -> L3, 2.1 -> L2, 2. -> L1
    if re.match(r"^(?:[0-9]+\.[0-9]+\.[0-9]+)\b", t):
        return 3
    if re.match(r"^(?:[0-9]+\.[0-9]+)\b", t):
        return 2
    if re.match(r"^(?:[0-9]+\.?\s+[A-Z\u4e00-\u9fa5])", t):
        return 1

    # 3. Roman numerals -> L1; IEEE Subsections (A. Contributions) -> L2
    if re.match(r"^[IVXLCDM]+\.\s+", t):
        return 1
    if re.match(r"^[A-Z]\.\s+", t):
        return 2

    # 4. Chinese numbering hierarchy
    if re.match(r"^[一二三四五六七八九十]+[、\.]", t):
        return 1
    if re.match(r"^（[一二三四五六七八九十]+）", t):
        return 2
    if re.match(r"^\([0-9]+\)", t):
        return 3

    # 5. Typographic fallback
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
        # Pop any active headings at same or deeper level
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, clean_h))

    def get_path(self, default="Main"):
        if not self.stack:
            return default
        return " > ".join(item[1] for item in self.stack)


# ----------------------------------------------------------------------
# 4. Document Extractors (PDF + OCR Fallback, DOCX, MD, TXT)
# ----------------------------------------------------------------------

def attempt_scanned_page_ocr(pdf_path, page_num, crops_dir, printed_page):
    """
    Attempt full-page OCR via fitz render + pytesseract/tesseract CLI.
    Returns (extracted_text, confidence) or ("", 0.0) if unavailable or garbled.
    """
    if not render_page_crop_fitz or not run_ocr_on_image:
        return "", 0.0

    # Render full page at 220 DPI
    try:
        import fitz
        doc = fitz.open(pdf_path)
        rect = doc[page_num - 1].rect
        bbox = [0, 0, float(rect.width), float(rect.height)]
        doc.close()
    except Exception:
        bbox = [0, 0, 595, 842]

    out_png = os.path.join(crops_dir, f"ocr_page_{page_num}.png")
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


def extract_units_from_pdf(pdf_path, work_dir, scope=None):
    """Process PDF into standardized translation units with OCR fallback."""
    units = []
    crops_dir = os.path.join(work_dir, "crops")
    translate_refs = scope.get("translate_references", False) if scope else False

    if diagnose_pdf:
        diagnosis = diagnose_pdf(pdf_path)
    else:
        diagnosis = {"status": "fallback", "pages": [], "printed_page_offset": 0}

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
            ocr_text, ocr_conf = attempt_scanned_page_ocr(pdf_path, p, crops_dir, p_printed)
            if ocr_text:
                # Split OCR paragraphs
                paras = [para.strip() for para in re.split(r"\n{2,}", ocr_text) if para.strip()]
                for para in paras:
                    w_cnt, c_cnt, s_cnt = count_words_and_sentences(para)
                    u_id = f"U{unit_seq:04d}"
                    unit_seq += 1
                    units.append({
                        "unit_id": u_id,
                        "unit_type": "paragraph",
                        "section_path": f"Page {p} (OCR Extracted)",
                        "page_span": [p, p],
                        "printed_page_span": [p_printed, p_printed],
                        "source_text": para,
                        "word_count": w_cnt,
                        "char_count": c_cnt,
                        "sentence_count": s_cnt,
                        "translate_policy": "translate",
                        "source_warnings": ["ocr_extracted"],
                        "uncertainty_flag": True,
                        "evidence_anchors": [{"page": p, "printed_page": p_printed, "ocr_confidence": ocr_conf}],
                    })
            else:
                u_id = f"U{unit_seq:04d}"
                unit_seq += 1
                units.append({
                    "unit_id": u_id,
                    "unit_type": "untranslatable_asset",
                    "section_path": f"Page {p} (Scanned Image)",
                    "page_span": [p, p],
                    "printed_page_span": [p_printed, p_printed],
                    "source_text": "[Scanned page image without native text layer; OCR unavailable or unreadable - requires manual inspection]",
                    "word_count": 0,
                    "char_count": 0,
                    "sentence_count": 0,
                    "translate_policy": "manual_review_asset",
                    "source_warnings": ["native_text_layer_missing", "ocr_unavailable_or_failed"],
                    "uncertainty_flag": True,
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

        # Track sections via monotonic stack
        if b_type in ("heading_h1", "heading_h2", "heading_h3"):
            h_level = infer_heading_level(raw_text, b_type)
            sec_tracker.push_heading(raw_text, level=h_level)
        elif b_type == "reference_entry":
            # Ensure References sits on top of stack so continuation lines inherit References
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
        t_policy = "translate"
        u_warnings = []

        if b_type == "title":
            u_type = "title"
            t_policy = "translate"
        elif b_type == "metadata":
            u_type = "metadata"
            t_policy = "preserve_source"
        elif b_type in ("heading_h1", "heading_h2", "heading_h3"):
            u_type = "heading"
            t_policy = "translate"
        elif b_type == "abstract":
            u_type = "abstract"
            t_policy = "translate"
        elif b_type == "figure_caption":
            u_type = "figure_caption"
            t_policy = "translate"
        elif b_type == "table_caption":
            u_type = "table_caption"
            t_policy = "translate"
        elif b_type == "table_content":
            u_type = "table_content"
            t_policy = "translate"
        elif b_type == "formula":
            u_type = "display_equation"
            t_policy = "preserve_source"
        elif b_type == "reference_entry":
            u_type = "reference"
            t_policy = "translate" if translate_refs else "preserve_source"
        elif b_type == "footnote":
            u_type = "footnote"
            t_policy = "translate"

        if b.get("stitched_types"):
            for st in b["stitched_types"]:
                u_warnings.append(f"stitched_{st}")

        if b.get("needs_visual_check"):
            u_warnings.append("potential_font_garble")

        w_cnt, c_cnt, s_cnt = count_words_and_sentences(raw_text)

        unit_id = f"U{unit_seq:04d}"
        unit_seq += 1

        units.append({
            "unit_id": unit_id,
            "unit_type": u_type,
            "section_path": sec_path,
            "page_span": p_span,
            "printed_page_span": print_span,
            "source_text": raw_text,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt,
            "translate_policy": t_policy,
            "source_warnings": u_warnings,
            "uncertainty_flag": bool(u_warnings),
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
            "unit_id": "U0001",
            "unit_type": "untranslatable_asset",
            "section_path": "Unextractable PDF Asset",
            "page_span": [1, p_cnt],
            "printed_page_span": ["1", str(p_cnt)],
            "source_text": "[PDF yielded 0 extractable text units; requires manual visual inspection]",
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "translate_policy": "manual_review_asset",
            "source_warnings": ["zero_extractable_units", "requires_manual_inspection"],
            "uncertainty_flag": True,
            "evidence_anchors": [{"page": 1, "printed_page": "1", "bbox": [0, 0, 595, 842]}],
        })

    return units, diagnosis, struct_doc


def extract_units_from_markdown(md_path):
    """Process Markdown / TXT into standardized translation units."""
    with open(md_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

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
        u_id = f"U{unit_seq:04d}"
        unit_seq += 1

        is_table = all(l.strip().startswith("|") for l in p_text.splitlines() if l.strip())

        units.append({
            "unit_id": u_id,
            "unit_type": "table_content" if is_table else "paragraph",
            "section_path": sec_tracker.get_path(),
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": p_text,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt,
            "translate_policy": "translate",
            "source_warnings": [],
            "uncertainty_flag": False,
            "evidence_anchors": [{"line_anchor": p_text[:40]}],
        })

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                curr_para.append(line)
                c_text = "\n".join(curr_para).strip()
                curr_para = []
                u_id = f"U{unit_seq:04d}"
                unit_seq += 1
                w_cnt, c_cnt, s_cnt = count_words_and_sentences(c_text)
                units.append({
                    "unit_id": u_id,
                    "unit_type": "code_block",
                    "section_path": sec_tracker.get_path(),
                    "page_span": [1, 1],
                    "printed_page_span": ["1", "1"],
                    "source_text": c_text,
                    "word_count": w_cnt,
                    "char_count": c_cnt,
                    "sentence_count": 1,
                    "translate_policy": "preserve_source",
                    "source_warnings": [],
                    "uncertainty_flag": False,
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
            u_id = f"U{unit_seq:04d}"
            unit_seq += 1
            w_cnt, c_cnt, _ = count_words_and_sentences(stripped)
            units.append({
                "unit_id": u_id,
                "unit_type": "display_equation",
                "section_path": sec_tracker.get_path(),
                "page_span": [1, 1],
                "printed_page_span": ["1", "1"],
                "source_text": stripped,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": 1,
                "translate_policy": "preserve_source",
                "source_warnings": [],
                "uncertainty_flag": False,
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
            u_id = f"U{unit_seq:04d}"
            unit_seq += 1
            units.append({
                "unit_id": u_id,
                "unit_type": "heading",
                "section_path": sec_tracker.get_path(),
                "page_span": [1, 1],
                "printed_page_span": ["1", "1"],
                "source_text": h_text,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": s_cnt,
                "translate_policy": "translate",
                "source_warnings": [],
                "uncertainty_flag": False,
                "evidence_anchors": [],
            })
            continue

        if not stripped:
            flush_para()
        else:
            curr_para.append(line)

    flush_para()
    return units


def extract_units_from_docx(docx_path):
    """Extract units from DOCX using pure standard library zipfile/xml parsing."""
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
                if "heading 1" in style_val.lower() or style_val == "1":
                    u_type = "heading"
                    sec_tracker.push_heading(p_text, level=1)
                elif "heading 2" in style_val.lower() or style_val == "2":
                    u_type = "heading"
                    sec_tracker.push_heading(p_text, level=2)

                w_cnt, c_cnt, s_cnt = count_words_and_sentences(p_text)
                u_id = f"U{unit_seq:04d}"
                unit_seq += 1

                units.append({
                    "unit_id": u_id,
                    "unit_type": u_type,
                    "section_path": sec_tracker.get_path(),
                    "page_span": [1, 1],
                    "printed_page_span": ["1", "1"],
                    "source_text": p_text,
                    "word_count": w_cnt,
                    "char_count": c_cnt,
                    "sentence_count": s_cnt,
                    "translate_policy": "translate",
                    "source_warnings": [],
                    "uncertainty_flag": False,
                    "evidence_anchors": [],
                })
    except Exception as e:
        units.append({
            "unit_id": "U0001",
            "unit_type": "untranslatable_asset",
            "section_path": "DOCX Parser Error",
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": f"Error parsing docx: {str(e)}",
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "translate_policy": "manual_review_asset",
            "source_warnings": [str(e)],
            "uncertainty_flag": True,
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


def extract_units_from_latex(tex_path):
    """Parse .tex source into structured translation units preserving math/bib/captions/sections."""
    with open(tex_path, "r", encoding="utf-8", errors="replace") as f:
        raw_content = f.read()

    # Strip unescaped LaTeX comments while preserving \%
    cleaned_lines = [re.sub(r"(^|[^\\](?:\\\\)*)%.*$", r"\1", ln) for ln in raw_content.splitlines()]
    full_text = "\n".join(cleaned_lines)

    units = []
    sec_tracker = SectionTracker()
    unit_seq = 1

    def add_unit(u_type, sec_path, text, policy="translate"):
        nonlocal unit_seq
        t = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not t:
            return
        w_cnt, c_cnt, s_cnt = count_words_and_sentences(t)
        u_id = f"U{unit_seq:04d}"
        unit_seq += 1
        units.append({
            "unit_id": u_id,
            "unit_type": u_type,
            "section_path": sec_path,
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": t,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt if policy == "translate" else 1,
            "translate_policy": policy,
            "source_warnings": [],
            "uncertainty_flag": False,
            "evidence_anchors": [{"line_anchor": t[:45]}],
        })

    # Extract \title{...} if present
    m_title = re.search(r"\\title\*?(?:\[[^\]]*\])?\s*\{", full_text)
    if m_title:
        title_txt, _ = _extract_braced_arg(full_text, m_title.end() - 1)
        title_clean = re.sub(r"\s+", " ", re.sub(r"\\\\", " ", title_txt)).strip()
        if title_clean:
            add_unit("title", "Title", title_clean, "translate")

    # Narrow to document body if \begin{document} exists
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
        # Skip pure command-only noise lines
        if re.match(r"^(?:\\(?:maketitle|tableofcontents|clearpage|newpage|centering|bibliographystyle\{[^}]*\}|bibliography\{[^}]*\}|label\{[^}]*\}|vspace\*?\{[^}]*\}|hspace\*?\{[^}]*\}|includegraphics(?:\[[^\]]*\])?\{[^}]*\}|begin\{(?:figure|table|center|itemize|enumerate)\*?\}|end\{(?:figure|table|center|itemize|enumerate)\*?\}))+$", p_txt):
            return
        add_unit("paragraph", sec_tracker.get_path(), p_txt, "translate")

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
                    add_unit("abstract", "Abstract", inner, "translate")
                elif mode_done == "math":
                    add_unit("display_equation", sec_tracker.get_path(), block_txt, "preserve_source")
                elif mode_done == "code":
                    add_unit("code_block", sec_tracker.get_path(), block_txt, "preserve_source")
                elif mode_done == "table":
                    add_unit("table_content", sec_tracker.get_path(), block_txt, "translate")
                elif mode_done == "bib":
                    sec_tracker.push_heading("References", level=1)
                    inner = re.sub(r"\\begin\{thebibliography\}(?:\{[^}]*\})?|\\end\{thebibliography\}", "", block_txt).strip()
                    bib_chunks = re.split(r"(?=\\bibitem)", inner)
                    for bc in bib_chunks:
                        bc_clean = re.sub(r"\s+", " ", bc).strip()
                        if bc_clean:
                            add_unit("reference", "References", bc_clean, "preserve_source")
            idx += 1
            continue

        # Track outer figure/table floats for caption classification
        if re.search(r"\\begin\{table\*?\}", stripped):
            float_ctx = "table"
        elif re.search(r"\\begin\{figure\*?\}", stripped):
            float_ctx = "figure"
        elif re.search(r"\\end\{(?:table|figure)\*?\}", stripped):
            float_ctx = None

        # Detect start of dedicated environments
        m_env = re.match(r"^\\begin\{([a-zA-Z*]+)\}", stripped)
        if m_env:
            env_name = m_env.group(1)
            if env_name == "abstract":
                flush_para()
                end_p = re.compile(r"\\end\{abstract\}")
                if end_p.search(line):
                    inner = re.sub(r"\\begin\{abstract\}|\\end\{abstract\}", "", stripped).strip()
                    add_unit("abstract", "Abstract", re.sub(r"\s+", " ", inner), "translate")
                else:
                    env_mode, env_end_pat, env_buf = "abstract", end_p, [line]
                idx += 1
                continue
            elif env_name in math_envs:
                flush_para()
                end_p = re.compile(r"\\end\{" + re.escape(env_name) + r"\}")
                if end_p.search(line):
                    add_unit("display_equation", sec_tracker.get_path(), stripped, "preserve_source")
                else:
                    env_mode, env_end_pat, env_buf = "math", end_p, [line]
                idx += 1
                continue
            elif env_name in code_envs:
                flush_para()
                end_p = re.compile(r"\\end\{" + re.escape(env_name) + r"\}")
                if end_p.search(line):
                    add_unit("code_block", sec_tracker.get_path(), stripped, "preserve_source")
                else:
                    env_mode, env_end_pat, env_buf = "code", end_p, [line]
                idx += 1
                continue
            elif env_name == "tabular":
                flush_para()
                end_p = re.compile(r"\\end\{tabular\}")
                if end_p.search(line):
                    add_unit("table_content", sec_tracker.get_path(), stripped, "translate")
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

        # Bracket display math \[ ... \] or $$ ... $$
        if stripped.startswith(r"\["):
            flush_para()
            end_p = re.compile(r"\\\]")
            if end_p.search(stripped[2:]):
                add_unit("display_equation", sec_tracker.get_path(), stripped, "preserve_source")
            else:
                env_mode, env_end_pat, env_buf = "math", end_p, [line]
            idx += 1
            continue
        if stripped.startswith("$$"):
            flush_para()
            if stripped.endswith("$$") and len(stripped) > 4:
                add_unit("display_equation", sec_tracker.get_path(), stripped, "preserve_source")
            else:
                env_mode, env_end_pat, env_buf = "math", re.compile(r"\$\$\s*$"), [line]
            idx += 1
            continue

        # Headings: \section, \subsection, \subsubsection, \paragraph
        m_sec = re.match(r"^\\(section|subsection|subsubsection|paragraph)\*?(?:\[[^\]]*\])?\s*\{", stripped)
        if m_sec:
            flush_para()
            cmd = m_sec.group(1)
            lvl = {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}[cmd]
            h_txt, _ = _extract_braced_arg(stripped, m_sec.end() - 1)
            h_txt = re.sub(r"\s+", " ", h_txt).strip()
            if h_txt:
                sec_tracker.push_heading(h_txt, level=lvl)
                add_unit("heading", sec_tracker.get_path(), h_txt, "translate")
            idx += 1
            continue

        # Captions: \caption{...}
        m_cap = re.match(r"^\\caption\*?(?:\[[^\]]*\])?\s*\{", stripped)
        if m_cap:
            flush_para()
            cap_txt, _ = _extract_braced_arg(stripped, m_cap.end() - 1)
            cap_txt = re.sub(r"\s+", " ", cap_txt).strip()
            if cap_txt:
                u_t = "table_caption" if float_ctx == "table" else "figure_caption"
                add_unit(u_t, sec_tracker.get_path(), cap_txt, "translate")
            idx += 1
            continue

        # Skip pure boilerplate lines
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
        self.in_title = False
        self.active_tag = None
        self.buf = []
        self.in_table = False
        self.table_rows = []
        self.curr_row = []
        self.in_cell = False
        self.cell_buf = []
        self.items = []  # list of (tag_type, text)

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            if t == "head":
                # Allow <title> inside <head> by tracking only script/style/noscript/svg as skip
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
            elif t == "caption" and self.table_rows is not None:
                pass
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
            if self.active_tag == "pre":
                txt = raw.strip()
            else:
                txt = re.sub(r"\s+", " ", raw).strip()
            if txt:
                self.items.append((self.active_tag, txt))
        self.active_tag = None
        self.buf = []


def extract_units_from_html(html_path):
    """Extract structured translation units from HTML using standard library html.parser."""
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        raw_html = f.read()

    parser = _ScientificHTMLParser()
    parser.feed(raw_html)
    parser.close()
    parser._flush_active()

    units = []
    sec_tracker = SectionTracker()
    unit_seq = 1

    for tag, text in parser.items:
        u_type = "paragraph"
        policy = "translate"
        if tag == "title":
            u_type = "title"
            sec_path = "Title"
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lvl = int(tag[1])
            sec_tracker.push_heading(text, level=lvl)
            u_type = "heading"
            sec_path = sec_tracker.get_path()
        elif tag == "pre":
            u_type = "code_block"
            policy = "preserve_source"
            sec_path = sec_tracker.get_path()
        elif tag == "table":
            u_type = "table_content"
            sec_path = sec_tracker.get_path()
        elif tag == "figcaption":
            u_type = "figure_caption"
            sec_path = sec_tracker.get_path()
        elif tag == "caption":
            u_type = "table_caption"
            sec_path = sec_tracker.get_path()
        else:
            sec_path = sec_tracker.get_path()

        w_cnt, c_cnt, s_cnt = count_words_and_sentences(text)
        u_id = f"U{unit_seq:04d}"
        unit_seq += 1
        units.append({
            "unit_id": u_id,
            "unit_type": u_type,
            "section_path": sec_path,
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": text,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "sentence_count": s_cnt if policy == "translate" else 1,
            "translate_policy": policy,
            "source_warnings": [],
            "uncertainty_flag": False,
            "evidence_anchors": [{"line_anchor": text[:45]}],
        })

    # Fallback for bare-body HTML without semantic tags
    if not units:
        cleaned = re.sub(r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>", " ", raw_html, flags=re.I | re.S)
        cleaned = re.sub(r"<[^>]+>", "\n", cleaned)
        for para in [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]:
            w_cnt, c_cnt, s_cnt = count_words_and_sentences(para)
            u_id = f"U{unit_seq:04d}"
            unit_seq += 1
            units.append({
                "unit_id": u_id,
                "unit_type": "paragraph",
                "section_path": "Main",
                "page_span": [1, 1],
                "printed_page_span": ["1", "1"],
                "source_text": para,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": s_cnt,
                "translate_policy": "translate",
                "source_warnings": ["html_fallback_strip"],
                "uncertainty_flag": False,
                "evidence_anchors": [],
            })

    return units


# ----------------------------------------------------------------------
# 5. Token-Budget Adaptive Batch Slicing (Guarding 64-Request Harbor Limit)
# ----------------------------------------------------------------------

def _pack_batches_with_limits(trans_units, max_words, max_units):
    """Internal helper to greedily pack translatable units into batches."""
    batches = []
    curr_batch_units = []
    curr_batch_words = 0
    batch_idx = 1
    recent_context = []

    for u in trans_units:
        u_words = u.get("word_count", 0)
        would_exceed_words = (curr_batch_words + u_words > max_words) and curr_batch_units
        would_exceed_units = (len(curr_batch_units) >= max_units)

        if would_exceed_words or would_exceed_units:
            batch_id = f"batch_{batch_idx:03d}"
            batches.append({
                "batch_id": batch_id,
                "batch_index": batch_idx,
                "unit_count": len(curr_batch_units),
                "total_words": curr_batch_words,
                "context_tail": list(recent_context),
                "units": curr_batch_units,
            })
            batch_idx += 1
            recent_context = [{
                "unit_id": cu["unit_id"],
                "section_path": cu["section_path"],
                "source_text": cu["source_text"][:250],
                "role": "context_only_do_not_retranslate"
            } for cu in curr_batch_units[-2:]]

            curr_batch_units = [u]
            curr_batch_words = u_words
        else:
            curr_batch_units.append(u)
            curr_batch_words += u_words

    if curr_batch_units:
        batch_id = f"batch_{batch_idx:03d}"
        batches.append({
            "batch_id": batch_id,
            "batch_index": batch_idx,
            "unit_count": len(curr_batch_units),
            "total_words": curr_batch_words,
            "context_tail": list(recent_context),
            "units": curr_batch_units,
        })

    return batches


def slice_units_into_batches(units, scope, max_words=1600, max_units=25, target_max_batches=18):
    """
    Chunk translatable units into semantic batches with overlap context tail.
    Automatically scales batch limits if total volume would exceed target_max_batches,
    guaranteeing adherence to the 64 model request budget.
    """
    trans_units = []
    for u in units:
        in_scope = is_unit_in_scope(u, scope)
        u["in_scope"] = in_scope
        if not in_scope:
            u["translate_policy"] = "scope_excluded"
            continue
        if u["translate_policy"] == "translate":
            trans_units.append(u)

    if not trans_units:
        return [], max_words, max_units

    eff_words = max_words
    eff_units = max_units
    batches = _pack_batches_with_limits(trans_units, eff_words, eff_units)

    # Adaptive scaling if initial packing exceeds target_max_batches
    if len(batches) > target_max_batches and target_max_batches > 0:
        total_w = sum(u.get("word_count", 0) for u in trans_units)
        total_u = len(trans_units)

        eff_words = max(max_words, math.ceil(total_w / max(1, target_max_batches - 1)) + 150)
        eff_units = max(max_units, math.ceil(total_u / max(1, target_max_batches - 1)) + 3)
        batches = _pack_batches_with_limits(trans_units, eff_words, eff_units)

        # Iterative step-up if greedy bin packing still overshoots slightly
        retries = 0
        while len(batches) > target_max_batches and retries < 6:
            eff_words = int(eff_words * 1.15)
            eff_units = int(eff_units * 1.15) + 1
            batches = _pack_batches_with_limits(trans_units, eff_words, eff_units)
            retries += 1

    return batches, eff_words, eff_units


# ----------------------------------------------------------------------
# 6. Glossary Discovery & Main Orchestrator Flow
# ----------------------------------------------------------------------

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


def discover_glossary_files(input_dir, exclude_dirs=None):
    """Recursively find user-provided or benchmark-provided terminology tables."""
    glossary_files = []
    patterns = [
        "*glossary*.csv", "*glossary*.tsv", "*glossary*.json", "*glossary*.md",
        "*terms*.json", "*terms*.csv", "*terminology*.csv", "*terminology*.json",
        "*术语*.csv", "*词表*.csv", "*词汇*.json", "*术语*.md", "*词表*.md"
    ]
    for _depth, _rel, fpath, fname in _walk_input_files(input_dir, exclude_dirs=exclude_dirs):
        low_fn = fname.lower()
        if any(fnmatch.fnmatch(low_fn, pat.lower()) for pat in patterns):
            glossary_files.append(fpath)

    return sorted(list(set(glossary_files)))


def _derive_semantic_case_id(case_data, task_text, chosen_file, input_dir):
    """Resolve human-meaningful case_id when case.json omits it (avoiding 'input')."""
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

    if chosen_file:
        stem = os.path.splitext(os.path.basename(chosen_file))[0]
        slug = re.sub(r"[^\w\u4e00-\u9fa5\-]+", "_", stem).strip("_")[:48]
        if slug:
            return f"{slug}_case"

    base_dir = os.path.basename(os.path.abspath(input_dir))
    if base_dir and base_dir.lower() not in ("input", "in", "data", "materials", ".", "work"):
        return base_dir
    return "translation_case"


def ingest_case(input_dir, work_dir, single_file=None, max_batch_words=1600, max_batch_units=25, target_max_batches=18):
    """Universal ingestion pipeline for task materials."""
    os.makedirs(work_dir, exist_ok=True)
    batches_dir = os.path.join(work_dir, "batches")
    # Clean stale batches if re-running in same work_dir
    if os.path.exists(batches_dir):
        for old_b in glob.glob(os.path.join(batches_dir, "batch_*.json")):
            try:
                os.remove(old_b)
            except Exception:
                pass
    os.makedirs(batches_dir, exist_ok=True)

    case_json_path = os.path.join(input_dir, "case.json")
    task_md_path = os.path.join(input_dir, "task.md")

    case_data = {}
    if os.path.exists(case_json_path):
        try:
            with open(case_json_path, "r", encoding="utf-8") as f:
                case_data = json.load(f)
        except Exception:
            pass

    task_text = ""
    if os.path.exists(task_md_path):
        try:
            with open(task_md_path, "r", encoding="utf-8") as f:
                task_text = f.read()
        except Exception:
            pass

    output_contract = case_data.get("output_contract", {})
    scope = extract_scope_constraints(task_text, case_data)

    exclude_dirs = [work_dir]
    glossary_files = discover_glossary_files(input_dir, exclude_dirs=exclude_dirs)
    glossary_set = {os.path.abspath(g) for g in glossary_files}

    EXT_PRIORITY = {".pdf": 0, ".docx": 1, ".tex": 2, ".html": 3, ".htm": 3, ".md": 4, ".txt": 5}
    SKIP_META_FILES = {"task.md", "readme.md", "case.json", "result.json"}

    target_candidates = []
    if single_file and os.path.exists(single_file):
        abs_sf = os.path.abspath(single_file)
        ext = os.path.splitext(abs_sf)[1].lower()
        target_candidates = [(0, EXT_PRIORITY.get(ext, 9), abs_sf, abs_sf)]
    else:
        for depth, rel_p, fpath, fname in _walk_input_files(input_dir, exclude_dirs=exclude_dirs):
            if fname.lower() in SKIP_META_FILES or fpath in glossary_set:
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXT_PRIORITY:
                target_candidates.append((depth, EXT_PRIORITY[ext], rel_p, fpath))
        target_candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    chosen_file = target_candidates[0][3] if target_candidates else None
    case_id = _derive_semantic_case_id(case_data, task_text, chosen_file, input_dir)

    all_units = []
    diag_summary = None
    dominant_format = "unknown"

    if chosen_file:
        ext = os.path.splitext(chosen_file)[1].lower()
        if ext == ".pdf":
            dominant_format = "pdf"
            units, diagnosis, _struct_doc = extract_units_from_pdf(chosen_file, work_dir, scope=scope)
            all_units.extend(units)
            diag_summary = diagnosis
        elif ext == ".docx":
            dominant_format = "docx"
            all_units.extend(extract_units_from_docx(chosen_file))
        elif ext == ".tex":
            dominant_format = "latex"
            all_units.extend(extract_units_from_latex(chosen_file))
        elif ext in (".html", ".htm"):
            dominant_format = "html"
            all_units.extend(extract_units_from_html(chosen_file))
        elif ext in (".md", ".txt"):
            dominant_format = "markdown"
            all_units.extend(extract_units_from_markdown(chosen_file))

    if not all_units:
        warn_reason = "no_supported_source_files_found" if not chosen_file else "zero_extractable_units"
        all_units.append({
            "unit_id": "U0001",
            "unit_type": "untranslatable_asset",
            "section_path": "Ingestion Fallback",
            "page_span": [1, 1],
            "printed_page_span": ["1", "1"],
            "source_text": f"[Ingestion yielded 0 translatable units ({warn_reason}) - manual inspection required]",
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "translate_policy": "manual_review_asset",
            "source_warnings": [warn_reason],
            "uncertainty_flag": True,
            "evidence_anchors": [],
        })

    sample_text = " ".join([u["source_text"] for u in all_units[:10]])
    src_lang = detect_language(sample_text)
    tgt_lang = "zh" if src_lang == "en" else "en"

    # Adaptive Batch Slicing
    batches, eff_words, eff_units = slice_units_into_batches(
        all_units, scope,
        max_words=max_batch_words,
        max_units=max_batch_units,
        target_max_batches=target_max_batches,
    )

    for b in batches:
        b_path = os.path.join(batches_dir, f"{b['batch_id']}.json")
        with open(b_path, "w", encoding="utf-8") as f:
            json.dump(b, f, ensure_ascii=False, indent=2)

    source_units_path = os.path.join(work_dir, "source_units.json")
    with open(source_units_path, "w", encoding="utf-8") as f:
        json.dump({
            "case_id": case_id,
            "source_language": src_lang,
            "target_language": tgt_lang,
            "total_units": len(all_units),
            "units": all_units
        }, f, ensure_ascii=False, indent=2)

    type_counts = collections.Counter(u.get("unit_type") for u in all_units)
    policy_counts = collections.Counter(u.get("translate_policy") for u in all_units)

    manifest = {
        "status": "success",
        "case_id": case_id,
        "input_dir": os.path.abspath(input_dir),
        "dominant_format": dominant_format,
        "language_direction": f"{src_lang}->{tgt_lang}",
        "source_language": src_lang,
        "target_language": tgt_lang,
        "scope_constraint": scope,
        "glossary_files_detected": glossary_files,
        "total_units": len(all_units),
        "unit_type_breakdown": dict(type_counts),
        "translate_policy_breakdown": dict(policy_counts),
        "batch_count": len(batches),
        "configured_max_batch_words": max_batch_words,
        "configured_max_batch_units": max_batch_units,
        "effective_max_batch_words": eff_words,
        "effective_max_batch_units": eff_units,
        "target_max_batches": target_max_batches,
        "untranslatable_asset_count": type_counts.get("untranslatable_asset", 0),
        "total_warnings_count": sum(len(u.get("source_warnings", [])) for u in all_units),
        "diagnosis_summary": {
            "dominant_route": diag_summary.get("dominant_route") if diag_summary else "n/a",
            "page_count": diag_summary.get("page_count") if diag_summary else 1,
            "printed_page_offset": diag_summary.get("printed_page_offset", 0) if diag_summary else 0,
        } if diag_summary else None,
        "output_contract": output_contract,
    }

    manifest_path = os.path.join(work_dir, "ingest_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[case_ingestor] Ingestion complete: {len(all_units)} units -> {len(batches)} batches (eff_words={eff_words}, eff_units={eff_units}) in {work_dir}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Universal Scientific Material Ingestion Engine")
    parser.add_argument("--input-dir", default=".", help="Directory containing input case files")
    parser.add_argument("--work-dir", required=True, help="Directory to save ingested intermediate artifacts")
    parser.add_argument("--file", default=None, help="Optional single file mode for debugging")
    parser.add_argument("--max-batch-words", type=int, default=1600, help="Maximum word budget per batch")
    parser.add_argument("--max-batch-units", type=int, default=25, help="Maximum unit count per batch")
    parser.add_argument("--target-max-batches", type=int, default=18, help="Target upper bound on batch count")
    args = parser.parse_args()

    input_d = args.input_dir
    if args.file and args.input_dir == ".":
        input_d = os.path.dirname(os.path.abspath(args.file))

    ingest_case(
        input_dir=input_d,
        work_dir=args.work_dir,
        single_file=args.file,
        max_batch_words=args.max_batch_words,
        max_batch_units=args.max_batch_units,
        target_max_batches=args.target_max_batches,
    )


if __name__ == "__main__":
    main()
