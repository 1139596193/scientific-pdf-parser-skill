#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layout_parser.py - Multi-Column Scientific PDF Layout Analysis & Reading Order Reconstruction
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
import collections
import warnings

# Silence PyMuPDF deprecation warnings
warnings.filterwarnings("ignore", category=FutureWarning)

# ----------------------------------------------------------------------
# 1. Ligatures & Text Normalization
# ----------------------------------------------------------------------

LIGATURE_MAP = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "ft",
    "\ufb06": "st",
    "—": "--",
    "–": "-",
}

KNOWN_COMPOUNDS = {
    "fault-tolerance", "fault-tolerant", "multi-tenant", "state-of-the-art",
    "cross-entropy", "zero-shot", "few-shot", "end-to-end", "well-known",
    "high-dimensional", "low-rank", "closed-loop", "open-source", "real-world"
}

HEADING_REGEX = re.compile(
    r"^(?:(?:[0-9]+|[IVXLCDM]+)(?:\.[0-9]+)*\.?\s+|(?:\b(?:Section|Chapter|Appendix)\s+[A-Z0-9]+[:.]?\s+))"
    r"([A-Z\u4e00-\u9fa5][^\n]{2,80})$",
    re.IGNORECASE,
)

CHINESE_HEADING_REGEX = re.compile(
    r"^(?:"
    r"[0-9]+(?:\.[0-9]+)*\s*[\u4e00-\u9fa5A-Za-z]+|"       # 1 引言, 1.1 系统架构
    r"[一二三四五六七八九十]+[、\.]\s*[\u4e00-\u9fa5]+|"   # 一、 实验设计与仿真平台
    r"（[一二三四五六七八九十]+）\s*[\u4e00-\u9fa5]+|"     # （一） 信道参数设置
    r"\([0-9]+\)\s*[\u4e00-\u9fa5]+|"                      # (1) 实验步骤
    r"引\s*言|结\s*语|结\s*论|背\s*景|概\s*述|参\s*考\s*文\s*献" # 独立词
    r")\s*$"
)

CAPTION_REGEX = re.compile(
    r"^(?:(?:Fig(?:ure)?\.?|Table|图|表|续表)\s*[0-9A-Za-z一二三四五六七八九十]+[:.\s\u2014\-])\s*(.*)",
    re.IGNORECASE,
)

FORMULA_NUMBER_REGEX = re.compile(r"^\s*[\(\[]?(?:(?:Eq(?:uation)?\.?\s*)?[0-9]+(?:\.[0-9]+)*|[a-zA-Z]-[0-9]+)[\)\]]?\s*[\.,;]?\s*$")


def clean_text_spans(text):
    """Normalize common font ligatures while preserving scientific unicode."""
    if not text:
        return ""
    for k, v in LIGATURE_MAP.items():
        text = text.replace(k, v)
    return text


def repair_intra_block_hyphenation(text):
    """
    Repair line-ending broken words inside a text block (e.g. 'be-\nlow' -> 'below').
    Protects established hyphenated compounds like 'fault-tolerance'.
    """
    def _hyphen_replace(match):
        w1 = match.group(1)
        w2 = match.group(2)
        candidate = f"{w1.lower()}-{w2.lower()}"
        if candidate in KNOWN_COMPOUNDS:
            return f"{w1}-{w2}"
        return f"{w1}{w2}"

    # Match word + hyphen + newline + next word part
    cleaned = re.sub(r"\b([a-zA-Z]{2,})[-–—]\n([a-zA-Z]{2,})\b", _hyphen_replace, text)
    return cleaned


# ----------------------------------------------------------------------
# 2. Multi-Backend Raw Block Extractor
# ----------------------------------------------------------------------

def extract_raw_blocks(pdf_path, diagnosis=None):
    """
    Extract raw text blocks with bounding boxes and font stats.
    Priority: fitz (PyMuPDF) -> pdfplumber -> pdftotext -> raw fallback.
    """
    # 1. Try PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open(pdf_path)
        pages_blocks = []
        for p_idx in range(len(doc)):
            page = doc[p_idx]
            rect = page.rect
            p_width = float(rect.width)
            p_height = float(rect.height)

            raw_dict = page.get_text("dict")
            page_items = []
            for b in raw_dict.get("blocks", []):
                if b.get("type") != 0:  # non-text block
                    continue

                bbox = [round(c, 2) for c in b.get("bbox", [0, 0, 0, 0])]
                lines_text = []
                spans_info = []

                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue

                    # Analyze line dominant size and baseline
                    line_sizes = [s.get("size", 10.0) for s in spans if s.get("text", "").strip()]
                    dom_size = collections.Counter(line_sizes).most_common(1)[0][0] if line_sizes else 10.0
                    
                    # Dominant baseline from largest span
                    dom_baseline = 0.0
                    max_s = -1
                    for s in spans:
                        sz = s.get("size", 10.0)
                        if sz > max_s:
                            max_s = sz
                            dom_baseline = s.get("origin", (0, 0))[1]

                    line_str = ""
                    for s in spans:
                        raw_t = clean_text_spans(s.get("text", ""))
                        if not raw_t:
                            continue
                        
                        sz = s.get("size", 10.0)
                        origin_y = s.get("origin", (0, 0))[1]
                        
                        # Subscript / Superscript detection
                        formatted_t = raw_t
                        if dom_size >= 7.0 and sz <= dom_size * 0.84:
                            y_diff = origin_y - dom_baseline
                            if y_diff > 1.2:  # Shifted down -> Subscript
                                if len(raw_t.strip()) == 1:
                                    formatted_t = f"_{{{raw_t.strip()}}}"
                                else:
                                    formatted_t = f"_{{{raw_t}}}"
                            elif y_diff < -1.2:  # Shifted up -> Superscript
                                if len(raw_t.strip()) == 1:
                                    formatted_t = f"^{{{raw_t.strip()}}}"
                                else:
                                    formatted_t = f"^{{{raw_t}}}"

                        spans_info.append({
                            "text": formatted_t,
                            "raw_text": raw_t,
                            "size": round(sz, 1),
                            "font": s.get("font", ""),
                            "flags": s.get("flags", 0),
                            "baseline": round(origin_y, 2),
                        })
                        line_str += formatted_t

                    lines_text.append(line_str)

                combined_text = "\n".join(lines_text).strip()
                # Repair intra-block line-ending hyphenation
                combined_text = repair_intra_block_hyphenation(combined_text)

                if combined_text:
                    avg_size = 10.0
                    if spans_info:
                        avg_size = round(sum(s["size"] for s in spans_info) / len(spans_info), 1)

                    page_items.append({
                        "bbox": bbox,
                        "text": combined_text,
                        "spans": spans_info,
                        "avg_size": avg_size,
                        "page": p_idx + 1,
                    })

            pages_blocks.append({
                "page": p_idx + 1,
                "width": p_width,
                "height": p_height,
                "blocks": page_items,
            })
        doc.close()
        return pages_blocks, "fitz"
    except Exception:
        pass

    # 2. Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages_blocks = []
            for p_idx, page in enumerate(pdf.pages):
                p_width = float(page.width)
                p_height = float(page.height)
                words = page.extract_words(extra_attrs=["fontname", "size"]) or []

                lines = cluster_words_into_lines(words)
                blocks = cluster_lines_into_blocks(lines, p_idx + 1)

                pages_blocks.append({
                    "page": p_idx + 1,
                    "width": p_width,
                    "height": p_height,
                    "blocks": blocks,
                })
            return pages_blocks, "pdfplumber"
    except Exception:
        pass

    # 3. Fallback CLI tools (pdftotext)
    pdftotext_path = shutil.which("pdftotext")
    if pdftotext_path:
        try:
            pages_blocks = extract_with_pdftotext(pdf_path, pdftotext_path)
            if pages_blocks:
                return pages_blocks, "pdftotext_layout"
        except Exception:
            pass

    return [], "none"


def cluster_words_into_lines(words):
    """Cluster pdfplumber word dicts into horizontal lines based on top coord."""
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (round(w["top"] / 3.0), w["x0"]))
    lines = []
    curr_line = [words_sorted[0]]
    for w in words_sorted[1:]:
        prev_w = curr_line[-1]
        if abs(w["top"] - prev_w["top"]) < 3.5:
            curr_line.append(w)
        else:
            lines.append(curr_line)
            curr_line = [w]
    if curr_line:
        lines.append(curr_line)
    return lines


def cluster_lines_into_blocks(lines, page_num):
    """Group lines into paragraph blocks based on vertical gap."""
    blocks = []
    if not lines:
        return []

    curr_block_lines = [lines[0]]
    for line in lines[1:]:
        prev_line = curr_block_lines[-1]
        prev_bottom = max(w["bottom"] for w in prev_line)
        curr_top = min(w["top"] for w in line)

        v_gap = curr_top - prev_bottom
        if 0 <= v_gap < 10.0:
            curr_block_lines.append(line)
        else:
            block = make_block_from_lines(curr_block_lines, page_num)
            if block:
                blocks.append(block)
            curr_block_lines = [line]

    if curr_block_lines:
        block = make_block_from_lines(curr_block_lines, page_num)
        if block:
            blocks.append(block)
    return blocks


def make_block_from_lines(lines, page_num):
    all_words = [w for line in lines for w in line]
    if not all_words:
        return None
    x0 = min(w["x0"] for w in all_words)
    y0 = min(w["top"] for w in all_words)
    x1 = max(w["x1"] for w in all_words)
    y1 = max(w["bottom"] for w in all_words)

    text_lines = [" ".join(w["text"] for w in line) for line in lines]
    text = repair_intra_block_hyphenation("\n".join(text_lines))
    avg_size = sum(w.get("size", 10.0) for w in all_words) / len(all_words)

    return {
        "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
        "text": clean_text_spans(text),
        "spans": [{"text": text, "size": round(avg_size, 1), "font": "unknown", "baseline": round(y1, 2)}],
        "avg_size": round(avg_size, 1),
        "page": page_num,
    }


def extract_with_pdftotext(pdf_path, tool_path):
    """CLI extraction fallback."""
    res = subprocess.run([tool_path, "-layout", pdf_path, "-"], stdout=subprocess.PIPE, text=True, timeout=20)
    pages_raw = res.stdout.split("\x0c")
    pages_data = []
    for p_idx, text in enumerate(pages_raw):
        if not text.strip():
            continue
        blocks = []
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        for b_idx, p in enumerate(paras):
            blocks.append({
                "bbox": [50.0, 100.0 + b_idx * 50.0, 500.0, 140.0 + b_idx * 50.0],
                "text": repair_intra_block_hyphenation(clean_text_spans(p)),
                "spans": [{"text": p, "size": 10.0, "font": "fallback", "baseline": 140.0}],
                "avg_size": 10.0,
                "page": p_idx + 1,
            })
        pages_data.append({"page": p_idx + 1, "width": 595.0, "height": 842.0, "blocks": blocks})
    return pages_data


# ----------------------------------------------------------------------
# 3. Headers, Footers & Folios Filter
# ----------------------------------------------------------------------

def filter_headers_footers(pages_blocks):
    """Cluster and isolate running headers, footers, and page numbers."""
    headers_footers = []
    filtered_pages = []

    edge_text_counter = collections.Counter()
    for pb in pages_blocks:
        p_height = pb["height"]
        for b in pb["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            if y0 < p_height * 0.08 or y1 > p_height * 0.92:
                norm_str = re.sub(r"\d+", "<NUM>", b["text"].strip())
                if len(norm_str) > 3:
                    edge_text_counter[norm_str] += 1

    for pb in pages_blocks:
        p_height = pb["height"]
        clean_blocks = []

        for b in pb["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            txt = b["text"].strip()
            norm_str = re.sub(r"\d+", "<NUM>", txt)

            is_edge = (y0 < p_height * 0.075 or y1 > p_height * 0.925)
            is_lone_number = bool(re.match(r"^\d{1,5}$", txt))
            is_repeating_header = (edge_text_counter.get(norm_str, 0) >= 2)

            if is_edge and (is_lone_number or is_repeating_header or len(txt) < 30):
                headers_footers.append({
                    "page": b["page"],
                    "bbox": b["bbox"],
                    "text": txt,
                    "kind": "footer" if y1 > p_height * 0.5 else "header",
                })
            else:
                clean_blocks.append(b)

        filtered_pages.append({
            "page": pb["page"],
            "width": pb["width"],
            "height": pb["height"],
            "blocks": clean_blocks,
        })

    return filtered_pages, headers_footers


# ----------------------------------------------------------------------
# 4. Multi-Column Reading Order & Formula Absorption
# ----------------------------------------------------------------------

def reconstruct_page_reading_order(page_blocks, width, height):
    """
    Reconstruct reading flow using vertical band decomposition:
    Full-width blocks create band cuts; within bands: Left -> Right Column.
    """
    if not page_blocks:
        return []

    mid_x = width / 2.0
    full_width_threshold = width * 0.65

    classified = []
    for b in page_blocks:
        x0, y0, x1, y1 = b["bbox"]
        w = x1 - x0

        if w >= full_width_threshold:
            col_pos = "full"
        elif x1 <= mid_x + (width * 0.06):
            col_pos = "left"
        elif x0 >= mid_x - (width * 0.06):
            col_pos = "right"
        else:
            col_pos = "full" if w > width * 0.45 else ("left" if (x0 + x1) / 2 < mid_x else "right")

        classified.append((col_pos, b))

    classified.sort(key=lambda item: item[1]["bbox"][1])

    bands = []
    current_band = []
    current_band_type = None

    for col_pos, b in classified:
        b_type = "full" if col_pos == "full" else "multi"
        if current_band_type is None:
            current_band_type = b_type
            current_band.append((col_pos, b))
        elif b_type == current_band_type:
            current_band.append((col_pos, b))
        else:
            bands.append((current_band_type, current_band))
            current_band = [(col_pos, b)]
            current_band_type = b_type

    if current_band:
        bands.append((current_band_type, current_band))

    ordered_blocks = []
    for b_type, items in bands:
        if b_type == "full":
            items.sort(key=lambda it: it[1]["bbox"][1])
            for col_pos, b in items:
                b["column"] = "full"
                ordered_blocks.append(b)
        else:
            left_col = [it[1] for it in items if it[0] == "left"]
            right_col = [it[1] for it in items if it[0] == "right"]
            others = [it[1] for it in items if it[0] not in ("left", "right")]

            left_col.sort(key=lambda b: b["bbox"][1])
            right_col.sort(key=lambda b: b["bbox"][1])

            for b in left_col:
                b["column"] = "left"
                ordered_blocks.append(b)
            for b in right_col:
                b["column"] = "right"
                ordered_blocks.append(b)
            for b in others:
                b["column"] = "mixed"
                ordered_blocks.append(b)

    # Absorb right-aligned formula numbers into formula bodies
    ordered_blocks = absorb_formula_numbers(ordered_blocks)
    return ordered_blocks


def absorb_formula_numbers(blocks):
    """
    Merge isolated right-aligned equation numbers like (1), (2) into their
    corresponding left/center formula expressions within the same column band.
    """
    if len(blocks) <= 1:
        return blocks

    filtered = []
    skip_indices = set()

    for idx, b in enumerate(blocks):
        if idx in skip_indices:
            continue

        txt = b["text"].strip()
        # Check if next block is a standalone equation number on same horizontal line
        if idx + 1 < len(blocks):
            nxt = blocks[idx + 1]
            nxt_txt = nxt["text"].strip()
            
            # Case A: current is formula body, next is number tag
            if FORMULA_NUMBER_REGEX.match(nxt_txt) and abs(b["bbox"][1] - nxt["bbox"][1]) <= 18.0:
                b["text"] = f"{b['text']}  \\quad {nxt_txt}"
                b["formula_id"] = nxt_txt.strip("().")
                b["bbox"] = [
                    min(b["bbox"][0], nxt["bbox"][0]),
                    min(b["bbox"][1], nxt["bbox"][1]),
                    max(b["bbox"][2], nxt["bbox"][2]),
                    max(b["bbox"][3], nxt["bbox"][3]),
                ]
                b["type"] = "formula"
                skip_indices.add(idx + 1)
            # Case B: current itself ends with formula number tag
            elif FORMULA_NUMBER_REGEX.search(txt) and len(txt.splitlines()) <= 3:
                b["type"] = "formula"
                num_m = re.search(r"\((\d+)\)", txt)
                if num_m:
                    b["formula_id"] = num_m.group(1)

        filtered.append(b)

    return filtered


# ----------------------------------------------------------------------
# 5. Semantic Classification & Formatting
# ----------------------------------------------------------------------

def infer_body_font_size(all_blocks):
    """Estimate standard body text font size."""
    size_freq = collections.Counter()
    for b in all_blocks:
        size = round(b.get("avg_size", 10.0), 0)
        size_freq[size] += len(b.get("text", ""))
    if size_freq:
        return size_freq.most_common(1)[0][0]
    return 10.0


def classify_block_semantics(b, body_size, is_first_page, page_height=842.0):
    """Assign structural role (title, heading, caption, formula, reference, footnote, etc.)."""
    text = b["text"].strip()
    avg_size = b.get("avg_size", body_size)
    y0 = b["bbox"][1]

    if b.get("type") == "formula":
        return "formula"

    # Caption (Figures / Tables / Continuations) - check first to prevent caption misclassification
    if CAPTION_REGEX.match(text):
        if text.lower().startswith("table") or text.startswith("表") or text.startswith("续表"):
            return "table_caption"
        return "figure_caption"

    # Footnote isolation (bottom region of page > 76% height, smaller font or academic metadata markers)
    is_bottom_region = (y0 > page_height * 0.76)
    has_fn_marker = bool(re.match(r"^[\*†‡•]|^(?:收稿日期|基金项目|作者简介|通信作者|E-mail|Email|Supported by|Correspondence to)\b", text, re.I))
    is_small_font = (avg_size <= body_size * 0.88)
    if is_bottom_region and (has_fn_marker or is_small_font):
        return "footnote"

    # First page title & abstract
    if is_first_page:
        if avg_size >= body_size * 1.35 and y0 < 320:
            return "title"
        if re.match(r"^(?:Abstract|摘\s*要)\b[:\s]?", text, re.IGNORECASE):
            return "abstract"

    # Chinese & English Headings
    if CHINESE_HEADING_REGEX.match(text):
        if re.match(r"^(?:[一二三四五六七八九十]+[、\.]|[0-9]+\s+[\u4e00-\u9fa5]|引\s*言|结\s*论|结\s*语|背\s*景)", text):
            return "heading_h1"
        elif re.match(r"^（[一二三四五六七八九十]+）", text):
            return "heading_h2"
        return "heading_h3"

    if HEADING_REGEX.match(text) or (avg_size >= body_size * 1.15 and len(text) < 100 and "\n" not in text):
        if avg_size >= body_size * 1.3:
            return "heading_h1"
        elif avg_size >= body_size * 1.15:
            return "heading_h2"
        return "heading_h3"

    # References
    if re.match(r"^\[\d+\]\s+[A-Z\u4e00-\u9fa5]", text):
        return "reference_entry"
    if re.match(r"^(?:References|REFERENCES|参\s*考\s*文\s*献)\s*[:]?$", text):
        return "heading_h1"

    return "paragraph"


def repair_hyphenation_and_merge(blocks):
    """
    Merge logically continuous paragraph blocks across line/column boundaries
    while preserving every physical evidence anchor and respecting Chinese typography.
    """
    if not blocks:
        return []

    merged = []
    curr = dict(blocks[0])
    curr["evidence_anchors"] = [{
        "page": curr["page"],
        "printed_page": curr.get("printed_page", str(curr["page"])),
        "block_id": curr["block_id"],
        "bbox": curr["bbox"],
    }]

    for nxt in blocks[1:]:
        curr_text = curr["text"]
        nxt_text = nxt["text"]

        ends_with_hyphen = curr_text.endswith("-") or curr_text.endswith("\u00ad")
        curr_type = curr.get("type", "paragraph")
        nxt_type = nxt.get("type", "paragraph")

        can_merge = (
            curr_type == "paragraph"
            and nxt_type == "paragraph"
            and not curr_text.endswith((".", "?", "!", ":", "。", "！", "？", "；", "："))
            and (
                ends_with_hyphen
                or (len(nxt_text) > 0 and nxt_text[0].islower())
                or (len(curr_text) > 0 and '\u4e00' <= curr_text[-1] <= '\u9fff')
            )
        )

        if can_merge:
            is_cjk = ('\u4e00' <= curr_text[-1:] <= '\u9fff') or ('\u4e00' <= nxt_text[:1] <= '\u9fff')
            if ends_with_hyphen:
                merged_txt = curr_text[:-1] + nxt_text
            elif is_cjk:
                merged_txt = curr_text + nxt_text
            else:
                merged_txt = curr_text + " " + nxt_text

            curr["text"] = merged_txt
            curr["bbox"] = [
                min(curr["bbox"][0], nxt["bbox"][0]),
                min(curr["bbox"][1], nxt["bbox"][1]),
                max(curr["bbox"][2], nxt["bbox"][2]),
                max(curr["bbox"][3], nxt["bbox"][3]),
            ]
            curr["evidence_anchors"].append({
                "page": nxt["page"],
                "printed_page": nxt.get("printed_page", str(nxt["page"])),
                "block_id": nxt["block_id"],
                "bbox": nxt["bbox"],
            })
        else:
            merged.append(curr)
            curr = dict(nxt)
            curr["evidence_anchors"] = [{
                "page": curr["page"],
                "printed_page": curr.get("printed_page", str(curr["page"])),
                "block_id": curr["block_id"],
                "bbox": curr["bbox"],
            }]

    merged.append(curr)
    return merged


# ----------------------------------------------------------------------
# 6. Main Layout Pipeline Execution
# ----------------------------------------------------------------------

def parse_layout(pdf_path, diagnosis_path=None):
    """Full layout parsing pipeline."""
    diagnosis = None
    if diagnosis_path and os.path.exists(diagnosis_path):
        try:
            with open(diagnosis_path, "r", encoding="utf-8") as f:
                diagnosis = json.load(f)
        except Exception:
            pass

    # Step 1: Raw extraction
    pages_blocks, backend_used = extract_raw_blocks(pdf_path, diagnosis)

    # Step 2: Separate headers, footers and folios
    clean_pages, headers_footers = filter_headers_footers(pages_blocks)

    # Step 3: Global body size estimation
    all_raw_blocks = [b for p in clean_pages for b in p["blocks"]]
    body_size = infer_body_font_size(all_raw_blocks)

    # Step 4: Page-level column ordering & formula absorption
    ordered_blocks_all = []
    global_id = 1

    offset = 0
    page_map = {}
    if diagnosis:
        offset = diagnosis.get("printed_page_offset", 0)
        for p_info in diagnosis.get("pages", []):
            page_map[p_info["page_num"]] = str(p_info.get("printed_page", p_info["page_num"]))

    for hf in headers_footers:
        hf["printed_page"] = page_map.get(hf["page"], str(hf["page"] + offset))

    for p in clean_pages:
        p_num = p["page"]
        w = p["width"]
        h = p["height"]
        p_ordered = reconstruct_page_reading_order(p["blocks"], w, h)
        printed_folio = page_map.get(p_num, str(p_num + offset))

        for b in p_ordered:
            b["block_id"] = f"b_{p_num}_{global_id}"
            b["page"] = p_num
            b["printed_page"] = printed_folio
            b["type"] = classify_block_semantics(b, body_size, is_first_page=(p_num == 1), page_height=h)

            has_cid = "(cid:" in b["text"]
            has_ufffd = "\ufffd" in b["text"]
            b["needs_visual_check"] = bool(has_cid or has_ufffd)
            b["confidence"] = 0.50 if (has_cid or has_ufffd) else 0.95

            ordered_blocks_all.append(b)
            global_id += 1

    # Step 5: Logical paragraph merging
    final_blocks = repair_hyphenation_and_merge(ordered_blocks_all)

    # Step 6: Document synthesis
    doc_title = None
    abstract_text = None
    sections = []
    current_sec = {"heading": "Main", "blocks": []}

    for b in final_blocks:
        b_type = b.get("type")
        if b_type == "title" and not doc_title:
            doc_title = b["text"]
        elif b_type == "abstract" and not abstract_text:
            abstract_text = b["text"]
        elif b_type in ("heading_h1", "heading_h2", "heading_h3"):
            if current_sec["blocks"] or current_sec["heading"] != "Main":
                sections.append(current_sec)
            current_sec = {"heading": b["text"], "blocks": []}
        else:
            current_sec["blocks"].append(b["block_id"])

    if current_sec["blocks"] or current_sec["heading"] != "Main":
        sections.append(current_sec)

    footnotes = [b for b in final_blocks if b.get("type") == "footnote"]

    structured_doc = {
        "pdf_path": os.path.abspath(pdf_path),
        "title": doc_title or "Untitled Scientific Document",
        "abstract": abstract_text or "",
        "backend_used": backend_used,
        "body_font_size": body_size,
        "printed_page_offset": offset,
        "metadata": {
            "title": doc_title or "Untitled Scientific Document",
            "abstract": abstract_text or "",
            "section_count": len(sections),
            "footnote_count": len(footnotes),
        },
        "headers_footers": headers_footers,
        "footnotes": footnotes,
        "sections": sections,
        "blocks": final_blocks,
    }

    # Step 7: Reading Order Markdown Generation
    md_lines = [f"# {structured_doc['metadata']['title']}\n"]
    if structured_doc["metadata"]["abstract"]:
        md_lines.append(f"> **Abstract**: {structured_doc['metadata']['abstract']}\n")

    for b in final_blocks:
        b_id = b["block_id"]
        p_num = b["page"]
        b_type = b.get("type", "paragraph")
        txt = b["text"]

        tag = f"[p{p_num}|{b_id}]"

        if b_type == "heading_h1":
            md_lines.append(f"\n## {tag} {txt}\n")
        elif b_type == "heading_h2":
            md_lines.append(f"\n### {tag} {txt}\n")
        elif b_type == "heading_h3":
            md_lines.append(f"\n#### {tag} {txt}\n")
        elif b_type == "formula":
            md_lines.append(f"\n$$\n{txt}  \\quad \\text{{{tag}}}\n$$\n")
        elif b_type in ("figure_caption", "table_caption"):
            md_lines.append(f"\n*{tag} {txt}*\n")
        elif b_type == "reference_entry":
            md_lines.append(f"- {tag} {txt}")
        elif b_type == "footnote":
            md_lines.append(f"\n> [^footnote {tag}] {txt}\n")
        else:
            md_lines.append(f"{tag} {txt}\n")

    reading_order_md = "\n".join(md_lines)
    return structured_doc, reading_order_md


def main():
    parser = argparse.ArgumentParser(description="Multi-Column Scientific Document Layout Parser")
    parser.add_argument("--pdf", required=True, help="Path to input PDF document")
    parser.add_argument("--diagnosis", default=None, help="Path to pdf_diagnose.py output JSON")
    parser.add_argument("--out-json", required=True, help="Output path for structured_doc.json")
    parser.add_argument("--out-md", required=True, help="Output path for reading_order.md")
    args = parser.parse_args()

    structured_doc, reading_order_md = parse_layout(args.pdf, args.diagnosis)

    out_json_dir = os.path.dirname(os.path.abspath(args.out_json))
    if out_json_dir and not os.path.exists(out_json_dir):
        os.makedirs(out_json_dir, exist_ok=True)

    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(structured_doc, f, ensure_ascii=False, indent=2)

    out_md_dir = os.path.dirname(os.path.abspath(args.out_md))
    if out_md_dir and not os.path.exists(out_md_dir):
        os.makedirs(out_md_dir, exist_ok=True)

    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write(reading_order_md)

    print(f"[layout_parser] Parsed {len(structured_doc.get('blocks', []))} blocks -> {args.out_json} & {args.out_md}")


if __name__ == "__main__":
    main()
