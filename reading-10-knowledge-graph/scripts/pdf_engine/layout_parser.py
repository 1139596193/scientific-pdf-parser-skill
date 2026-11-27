#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
layout_parser.py - Scientific PDF Layout Analysis, Fragment Coalescing & Paragraph Stitching
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
import collections
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

# ----------------------------------------------------------------------
# 1. Ligatures, Typography & Math Heuristics
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
    "high-dimensional", "low-rank", "closed-loop", "open-source", "real-world",
    "peer-to-peer", "time-varying", "large-scale", "fine-grained", "coarse-grained",
    "non-markovian", "privacy-preserving", "differentially-private"
}

HEADING_REGEX = re.compile(
    r"^(?:(?:[0-9]+(?:\.[0-9]+)*\.?|[IVXLCDM]+\.|[A-Z]\.)\s+|(?:\b(?:Section|Chapter|Appendix)\s+[A-Z0-9]+[:.]?\s+))"
    r"([A-Z\u4e00-\u9fa5][^\n]{2,90})$"
)

STANDALONE_HEADINGS = {
    "abstract", "introduction", "related work", "preliminaries", "background",
    "methodology", "methods", "experiments", "evaluation", "results",
    "discussion", "conclusion", "conclusions", "acknowledgment", "acknowledgments",
    "references", "appendix", "contents", "index terms",
    "摘要", "引言", "相关工作", "背景", "系统模型", "方法", "实验", "讨论", "结论", "结语", "参考文献", "致谢", "附录"
}

CHINESE_HEADING_REGEX = re.compile(
    r"^(?:"
    r"[0-9]+(?:\.[0-9]+)*\s*[\u4e00-\u9fa5A-Za-z][^\n]{1,60}|"
    r"[一二三四五六七八九十]+[、\.]\s*[\u4e00-\u9fa5][^\n]{1,60}|"
    r"（[一二三四五六七八九十]+）\s*[\u4e00-\u9fa5][^\n]{1,60}|"
    r"\([0-9]+\)\s*[\u4e00-\u9fa5][^\n]{1,60}|"
    r"引\s*言|结\s*语|结\s*论|背\s*景|概\s*述|参\s*考\s*文\s*献|摘\s*要"
    r")\s*$"
)

CAPTION_REGEX = re.compile(
    r"^(?:(?:Fig(?:ure)?\.?|Table|Algorithm|图|表|续表|算法)\s*[0-9A-Za-z一二三四五六七八九十]+(?:\.[0-9]+)*[:.\s\u2014\-])\s*(.*)",
    re.IGNORECASE,
)

PROSE_CAPTION_REF_REGEX = re.compile(
    r"^(?:"
    r"(?:Tables?|Tabs?\.?|Fig(?:ure)?s?\.?|Algorithms?)\s*[A-Za-z]?\d+(?:\.\d+)*\s+"
    r"(?:lists|shows|summarizes|presents|compares|reports|illustrates|depicts|gives|provides|demonstrates|contains|indicates|highlights|displays|is|are|was|were|can|will|may|might|has|have|in|and|or|as|also|further|clearly)\b"
    r"|"
    r"(?:图|表|续表|算法)\s*[A-Za-z]?\d+(?:\.\d+)*\s*(?:展示|给出|列出|对比|显示|总结|表明|说明|可见|中|的|为|是|如)"
    r")",
    re.IGNORECASE,
)

TOC_TITLE_REGEX = re.compile(r"^(?:contents|table\s+of\s+contents|目\s*录)\s*[:：]?$", re.IGNORECASE)
TOC_ENTRY_TAIL_REGEX = re.compile(r"(?:(?:\.\s*){2,}\d{1,4}|\s+\d{1,4})\s*$")
FORMULA_PUA_REGEX = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd]")

FORMULA_NUMBER_REGEX = re.compile(
    r"^\s*[\(\[]?(?:(?:Eq(?:uation)?\.?\s*)?[0-9]+(?:\.[0-9]+)*|[a-zA-Z]-[0-9]+)[\)\]]?\s*[\.,;]?\s*$"
)

COMMON_ABBREVIATIONS = {
    "e.g.", "i.e.", "et al.", "fig.", "figs.", "tab.", "tabs.", "ref.", "refs.",
    "eq.", "eqs.", "sec.", "secs.", "vs.", "approx.", "dept.", "prof.", "dr.", "no.", "resp."
}

MATH_SYMBOL_CHARS = set("=+-−×÷≤≥<>∈∉∑∏∫√⟨⟩|∥⊗⊕∂∇∀∃≈≠≡→↦←↔^_{}\\αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")
CONTROL_CHAR_REGEX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text_spans(text):
    """Normalize font ligatures while preserving scientific unicode."""
    if not text:
        return ""
    for k, v in LIGATURE_MAP.items():
        text = text.replace(k, v)
    return text


def is_junk_control_block(text):
    """Detect pure control-character artifacts from PDF math fonts (e.g., '\\x00', '\\x12')."""
    if not text:
        return True
    stripped = CONTROL_CHAR_REGEX.sub("", text).strip()
    if not stripped:
        return True
    # Single stray punctuation or math delimiter without alphanumeric/CJK context
    if len(stripped) == 1 and stripped in ".,;:()[]{}|/\\`'\"":
        return True
    return False


def repair_intra_block_hyphenation(text):
    """Repair line-ending broken words inside a text block."""
    def _hyphen_replace(match):
        w1 = match.group(1)
        w2 = match.group(2)
        candidate = f"{w1.lower()}-{w2.lower()}"
        if candidate in KNOWN_COMPOUNDS:
            return f"{w1}-{w2}"
        return f"{w1}{w2}"

    cleaned = re.sub(r"\b([a-zA-Z]{2,})[-–—]\s*\n\s*([a-zA-Z]{2,})\b", _hyphen_replace, text)
    return cleaned


def normalize_paragraph_text(text, block_type):
    """
    Clean PDF soft physical linebreaks for prose types while preserving newlines
    for display equations, tables, and code blocks.
    """
    if not text:
        return ""
    # Strip stray invisible control chars that aren't newlines/tabs
    text = CONTROL_CHAR_REGEX.sub("", text).strip()
    if not text:
        return ""

    if block_type in ("formula", "display_equation", "table_content", "code_block"):
        return text

    # 1. Repair hyphenation across linebreaks
    text = repair_intra_block_hyphenation(text)

    # 2. Merge soft newlines smartly based on CJK vs Latin boundary
    lines = text.splitlines()
    if len(lines) <= 1:
        return re.sub(r"[ \t]{2,}", " ", text).strip()

    merged = lines[0].rstrip()
    for nxt_line in lines[1:]:
        nxt_s = nxt_line.lstrip()
        if not nxt_s:
            continue
        if not merged:
            merged = nxt_s
            continue
        last_ch = merged[-1]
        first_ch = nxt_s[0]
        is_cjk_left = ('\u4e00' <= last_ch <= '\u9fff') or (last_ch in "，。；：、（）【】《》“”")
        is_cjk_right = ('\u4e00' <= first_ch <= '\u9fff') or (first_ch in "，。；：、（）【】《》“”")

        if is_cjk_left or is_cjk_right:
            merged += nxt_s
        else:
            merged += " " + nxt_s

    return re.sub(r"[ \t]{2,}", " ", merged).strip()


# ----------------------------------------------------------------------
# 2. Multi-Backend Raw Block Extractor
# ----------------------------------------------------------------------

def extract_raw_blocks(pdf_path, diagnosis=None):
    """
    Extract raw text blocks with bounding boxes and font stats.
    Priority: pymupdf/fitz -> pdfplumber -> pdftotext -> raw fallback.
    """
    try:
        try:
            import pymupdf as fitz
        except ImportError:
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
                if b.get("type") != 0:
                    continue

                bbox = [round(c, 2) for c in b.get("bbox", [0, 0, 0, 0])]
                lines_text = []
                spans_info = []

                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue

                    line_sizes = [s.get("size", 10.0) for s in spans if s.get("text", "").strip()]
                    dom_size = collections.Counter(line_sizes).most_common(1)[0][0] if line_sizes else 10.0

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

                        formatted_t = raw_t
                        if dom_size >= 7.0 and sz <= dom_size * 0.84:
                            y_diff = origin_y - dom_baseline
                            if y_diff > 1.2:
                                formatted_t = f"_{{{raw_t.strip()}}}" if len(raw_t.strip()) == 1 else f"_{{{raw_t}}}"
                            elif y_diff < -1.2:
                                formatted_t = f"^{{{raw_t.strip()}}}" if len(raw_t.strip()) == 1 else f"^{{{raw_t}}}"

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
                if is_junk_control_block(combined_text):
                    continue

                avg_size = 10.0
                if spans_info:
                    avg_size = round(sum(s["size"] for s in spans_info) / len(spans_info), 1)

                fonts_used = set(s.get("font", "") for s in spans_info)
                page_items.append({
                    "bbox": bbox,
                    "text": combined_text,
                    "spans": spans_info,
                    "fonts": list(fonts_used),
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

    # 4. Pure-stdlib zlib content-stream fallback
    try:
        pages_blocks = extract_with_stdlib_zlib(pdf_path)
        if pages_blocks:
            return pages_blocks, "stdlib_zlib"
    except Exception:
        pass

    return [], "none"


def cluster_words_into_lines(words):
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
    text = "\n".join(text_lines)
    if is_junk_control_block(text):
        return None
    avg_size = sum(w.get("size", 10.0) for w in all_words) / len(all_words)

    return {
        "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
        "text": clean_text_spans(text),
        "spans": [{"text": text, "size": round(avg_size, 1), "font": "unknown", "baseline": round(y1, 2)}],
        "fonts": [],
        "avg_size": round(avg_size, 1),
        "page": page_num,
    }


def extract_with_pdftotext(pdf_path, tool_path):
    res = subprocess.run([tool_path, "-layout", pdf_path, "-"], stdout=subprocess.PIPE, text=True, timeout=20)
    pages_raw = res.stdout.split("\x0c")
    pages_data = []
    for p_idx, text in enumerate(pages_raw):
        if not text.strip():
            continue
        blocks = []
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        for b_idx, p in enumerate(paras):
            if is_junk_control_block(p):
                continue
            blocks.append({
                "bbox": [50.0, 100.0 + b_idx * 50.0, 500.0, 140.0 + b_idx * 50.0],
                "text": clean_text_spans(p),
                "spans": [{"text": p, "size": 10.0, "font": "fallback", "baseline": 140.0}],
                "fonts": [],
                "avg_size": 10.0,
                "page": p_idx + 1,
            })
        pages_data.append({"page": p_idx + 1, "width": 595.0, "height": 842.0, "blocks": blocks})
    return pages_data


def extract_with_stdlib_zlib(pdf_path):
    """Pure-stdlib zlib FlateDecode stream fallback when fitz/pdfplumber/pdftotext are unavailable."""
    try:
        from pdf_diagnose import extract_pdf_pages_stdlib_zlib
    except ImportError:
        from .pdf_diagnose import extract_pdf_pages_stdlib_zlib

    fb = extract_pdf_pages_stdlib_zlib(pdf_path)
    pages_data = []
    for p_info in fb.get("pages", []):
        p_num = p_info.get("page_num", 1)
        raw_txt = (p_info.get("text") or "").strip()
        if not raw_txt:
            continue
        lines = [ln.strip() for ln in raw_txt.splitlines() if ln.strip()]
        # Coalesce short lines into paragraph-sized blocks cleanly
        blocks = []
        buf = []
        for ln in lines:
            buf.append(ln)
            if ln.endswith((".", "!", "?", "。", "！", "？")) and sum(len(x) for x in buf) >= 80:
                para = " ".join(buf)
                if not is_junk_control_block(para):
                    b_idx = len(blocks)
                    blocks.append({
                        "bbox": [50.0, 80.0 + b_idx * 45.0, 540.0, 118.0 + b_idx * 45.0],
                        "text": clean_text_spans(para),
                        "spans": [{"text": para, "size": 10.0, "font": "fallback", "baseline": 118.0}],
                        "fonts": [],
                        "avg_size": 10.0,
                        "page": p_num,
                    })
                buf = []
        if buf:
            para = " ".join(buf)
            if not is_junk_control_block(para):
                b_idx = len(blocks)
                blocks.append({
                    "bbox": [50.0, 80.0 + b_idx * 45.0, 540.0, 118.0 + b_idx * 45.0],
                    "text": clean_text_spans(para),
                    "spans": [{"text": para, "size": 10.0, "font": "fallback", "baseline": 118.0}],
                    "fonts": [],
                    "avg_size": 10.0,
                    "page": p_num,
                })
        if blocks:
            pages_data.append({"page": p_num, "width": 595.0, "height": 842.0, "blocks": blocks})
    return pages_data


# ----------------------------------------------------------------------
# 3. Headers, Footers & Folios Filter
# ----------------------------------------------------------------------

def filter_headers_footers(pages_blocks):
    """Cluster and isolate running headers, footers, and page numbers with single-page safety."""
    headers_footers = []
    filtered_pages = []
    total_pages = len(pages_blocks)

    edge_text_counter = collections.Counter()
    for pb in pages_blocks:
        p_height = pb["height"]
        for b in pb["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            if y0 < p_height * 0.10 or y1 > p_height * 0.90:
                norm_str = re.sub(r"\d+", "<NUM>", b["text"].strip())
                if len(norm_str) > 3:
                    edge_text_counter[norm_str] += 1

    for pb in pages_blocks:
        p_height = pb["height"]
        raw_blocks = pb["blocks"]
        clean_blocks = []
        candidate_dropped = []

        for b in raw_blocks:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            txt = b["text"].strip()
            norm_str = re.sub(r"\d+", "<NUM>", txt)

            is_edge = (y0 < p_height * 0.055 or y1 > p_height * 0.945)
            is_lone_number = bool(re.match(r"^\d{1,5}$", txt))
            is_repeating_header = (edge_text_counter.get(norm_str, 0) >= 2 and (y0 < p_height * 0.10 or y1 > p_height * 0.90))
            is_first_page_title = (pb["page"] == 1 and b.get("avg_size", 10.0) >= 13.0 and not is_lone_number and edge_text_counter.get(norm_str, 0) < 2)

            # Single-page document & first-page large title guards prevent dropping short CJK/EN titles as headers
            if total_pages <= 1:
                should_drop = is_edge and is_lone_number
            elif is_first_page_title:
                should_drop = False
            else:
                should_drop = is_repeating_header or (is_edge and (is_lone_number or len(txt) < 35))

            if should_drop:
                candidate_dropped.append((b, txt, y1))
            else:
                clean_blocks.append(b)

        # Page empty protection: never filter a non-empty page down to 0 blocks unless everything was a lone page number
        if not clean_blocks and raw_blocks:
            kept_rescue = []
            still_dropped = []
            for b, txt, y1 in candidate_dropped:
                if re.match(r"^\d{1,5}$", txt):
                    still_dropped.append((b, txt, y1))
                else:
                    kept_rescue.append(b)
            clean_blocks = kept_rescue if kept_rescue else list(raw_blocks)
            candidate_dropped = still_dropped if kept_rescue else []

        for b, txt, y1 in candidate_dropped:
            headers_footers.append({
                "page": b["page"],
                "bbox": b["bbox"],
                "text": txt,
                "kind": "footer" if y1 > p_height * 0.5 else "header",
            })

        filtered_pages.append({
            "page": pb["page"],
            "width": pb["width"],
            "height": pb["height"],
            "blocks": clean_blocks,
        })

    return filtered_pages, headers_footers


# ----------------------------------------------------------------------
# 4. Multi-Column Reading Order & Fragment Coalescing
# ----------------------------------------------------------------------

PROSE_LEADIN_REGEX = re.compile(
    r"^(?:Here,|Here\s|where\s|Where\s|In\s+the\s|Note\s+that|Thus,|Therefore,|Consequently,|We\s|This\s|The\s|For\s|其中|这里|此处)"
)
ENGLISH_PROSE_FUNC_WORDS = {
    "the", "is", "are", "was", "were", "of", "to", "in", "for", "with", "that",
    "which", "and", "from", "by", "as", "described", "later", "chosen", "denoted",
    "satisfies", "captures", "where", "here", "experimental", "measurement", "hardware"
}


def is_math_fragment_block(b):
    """
    Detect isolated mathematical expression fragments, font-split math operators,
    matrix rows, or short symbol labels from PDF equation extraction.
    Strictly exempts prose explanation sentences starting with 'Here,', 'where', etc.
    """
    txt = CONTROL_CHAR_REGEX.sub("", b["text"]).strip()
    if not txt:
        return True

    if b.get("type") == "formula":
        return True

    # Hard Exemption: explicit natural prose lead-ins or rich English function words
    if PROSE_LEADIN_REGEX.match(txt):
        return False
    all_alpha_words = [w.lower() for w in re.findall(r"\b[A-Za-z]{2,}\b", txt)]
    func_hits = sum(1 for w in all_alpha_words if w in ENGLISH_PROSE_FUNC_WORDS)
    if func_hits >= 3:
        return False

    fonts = " ".join(b.get("fonts", [])).lower()
    has_math_font = any(mf in fonts for mf in ("cmmi", "cmsy", "cmex", "msam", "msbm", "symbol", "math", "stix"))

    words = re.findall(r"[A-Za-z]{3,}", txt)
    natural_words = [w for w in words if w.lower() not in {"sin", "cos", "tan", "log", "exp", "max", "min", "sup", "inf", "lim", "arg", "det", "tr", "diag", "mod", "iff", "and", "or", "for"}]
    cjk_chars = re.findall(r"[\u4e00-\u9fa5]", txt)

    if len(natural_words) == 0 and len(cjk_chars) == 0:
        if len(txt) <= 80:
            return True

    math_char_count = sum(1 for c in txt if c in MATH_SYMBOL_CHARS)
    if len(txt) <= 100 and len(natural_words) <= 2 and len(cjk_chars) == 0:
        if math_char_count >= 2 or "_{" in txt or "^{" in txt or (has_math_font and len(txt) <= 50):
            return True

    if FORMULA_NUMBER_REGEX.match(txt):
        return True

    return False


def split_mixed_formula_and_prose(blocks):
    """
    Split blocks where an equation & number like '(1)' or '(3)' is immediately followed
    by natural prose explanation (e.g. '(1) Here, H(x)...', '(3) where ρ^{in}...',
    'B = Ex B(ρin_B). In the experimental hardware measurement...').
    """
    out = []
    split_pat = re.compile(
        r"(\(\s*(?:[A-Za-z]?\d+(?:\.\d+)?)\s*\)|\.)\s+(?=(?:Here,|Here\s|where\s|Where\s|In\s+the\s|We\s|This\s|Note\s+that|这里|其中))"
    )
    for b in blocks:
        if b.get("type") != "formula":
            out.append(b)
            continue

        txt = b["text"].strip()
        m = split_pat.search(txt)
        if not m:
            out.append(b)
            continue

        split_idx = m.end(1)
        formula_part = txt[:split_idx].strip()
        prose_part = txt[split_idx:].strip()

        # Verify prose part actually looks like natural text
        if len(re.findall(r"[A-Za-z\u4e00-\u9fa5]{2,}", prose_part)) >= 1 and len(prose_part) >= 4:
            f_blk = dict(b)
            f_blk["text"] = formula_part
            f_blk["type"] = "formula"
            out.append(f_blk)

            p_blk = dict(b)
            p_blk["text"] = prose_part
            p_blk["type"] = "paragraph"
            p_blk.pop("formula_id", None)
            out.append(p_blk)
        else:
            out.append(b)
    return out


def coalesce_adjacent_math_fragments(blocks):
    """
    Merge consecutive or horizontally overlapping math fragments within the same
    column/band into unified display_equation blocks instead of leaving dozens of tiny paragraphs.
    """
    if not blocks:
        return []

    coalesced = []
    i = 0
    n = len(blocks)

    while i < n:
        curr = blocks[i]
        curr_is_math = is_math_fragment_block(curr)

        if not curr_is_math:
            coalesced.append(curr)
            i += 1
            continue

        # Collect contiguous math fragments that belong to the same equation region
        group = [curr]
        j = i + 1
        while j < n:
            nxt = blocks[j]
            if not is_math_fragment_block(nxt):
                break
            prev = group[-1]
            # Vertical distance check or same horizontal band
            y_overlap = min(prev["bbox"][3], nxt["bbox"][3]) - max(prev["bbox"][1], nxt["bbox"][1])
            v_gap = nxt["bbox"][1] - prev["bbox"][3]
            same_col = (prev.get("column") == nxt.get("column")) or (prev.get("column") == "full") or (nxt.get("column") == "full")
            # Merge if they are on same line, vertically close (<30pt), or same math group
            if same_col and (y_overlap > -12 or abs(v_gap) < 30.0):
                group.append(nxt)
                j += 1
            else:
                break

        if len(group) == 1:
            txt_clean = CONTROL_CHAR_REGEX.sub("", curr["text"]).strip()
            # If single tiny symbol/number with zero natural words (like standalone 'X' or '0'), mark as formula_or_label
            words = re.findall(r"[A-Za-z]{2,}", txt_clean)
            cjk = re.findall(r"[\u4e00-\u9fa5]", txt_clean)
            if len(words) <= 1 and len(cjk) == 0 and len(txt_clean) <= 60:
                curr["type"] = "formula"
            coalesced.append(curr)
            i += 1
        else:
            # Merge group into a single unified formula block
            # Sort internally by top then left
            group.sort(key=lambda g: (round(g["bbox"][1] / 6.0), g["bbox"][0]))
            merged_texts = []
            for g in group:
                gt = CONTROL_CHAR_REGEX.sub("", g["text"]).strip()
                if gt:
                    merged_texts.append(gt)

            combined = " ".join(merged_texts)
            # Clean up spacing around equation number
            num_m = re.search(r"(\(\d+(?:\.\d+)?\))\s*$", combined)
            formula_id = num_m.group(1).strip("()") if num_m else None

            merged_block = dict(group[0])
            merged_block["text"] = combined
            merged_block["type"] = "formula"
            if formula_id:
                merged_block["formula_id"] = formula_id
            merged_block["bbox"] = [
                min(g["bbox"][0] for g in group),
                min(g["bbox"][1] for g in group),
                max(g["bbox"][2] for g in group),
                max(g["bbox"][3] for g in group),
            ]
            coalesced.append(merged_block)
            i = j

    return coalesced


def reconstruct_page_reading_order(page_blocks, width, height, is_first_page=False, body_size=10.0):
    """
    Reconstruct reading flow using vertical band decomposition:
    Full-width blocks create band cuts; within bands: Left -> Right Column.
    Forces large centered title lines on page 1 into 'full' band so authors cannot interleave.
    """
    if not page_blocks:
        return []

    mid_x = width / 2.0
    full_width_threshold = width * 0.65

    classified = []
    for b in page_blocks:
        x0, y0, x1, y1 = b["bbox"]
        w = x1 - x0
        avg_sz = b.get("avg_size", body_size)

        # Keep first-page large title lines (including short wrapped final lines) in 'full' band
        if is_first_page and y0 < height * 0.35 and avg_sz >= body_size * 1.25 and len(b.get("text", "")) < 220:
            col_pos = "full"
        elif w >= full_width_threshold:
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

    # Absorb right-aligned formula numbers, coalesce fragmented math expressions, & split trailing prose
    ordered_blocks = absorb_formula_numbers(ordered_blocks)
    ordered_blocks = coalesce_adjacent_math_fragments(ordered_blocks)
    ordered_blocks = split_mixed_formula_and_prose(ordered_blocks)
    return ordered_blocks


def absorb_formula_numbers(blocks):
    """Merge isolated right-aligned equation numbers like (1), (2) into formula bodies."""
    if len(blocks) <= 1:
        return blocks

    filtered = []
    skip_indices = set()

    for idx, b in enumerate(blocks):
        if idx in skip_indices:
            continue

        txt = b["text"].strip()
        if idx + 1 < len(blocks):
            nxt = blocks[idx + 1]
            nxt_txt = nxt["text"].strip()

            if FORMULA_NUMBER_REGEX.match(nxt_txt) and abs(b["bbox"][1] - nxt["bbox"][1]) <= 22.0:
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
            elif FORMULA_NUMBER_REGEX.search(txt) and len(txt.splitlines()) <= 3:
                # Avoid classifying prose ending in "(2)" as formula unless math chars exist
                math_chars = sum(1 for c in txt if c in MATH_SYMBOL_CHARS)
                if math_chars >= 1 or len(txt) < 60:
                    b["type"] = "formula"
                    num_m = re.search(r"\((\d+)\)", txt)
                    if num_m:
                        b["formula_id"] = num_m.group(1)

        filtered.append(b)

    return filtered


# ----------------------------------------------------------------------
# 5. Semantic Classification, Table Line Detection & Section Hierarchy
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


def is_table_row_heuristic(text):
    """
    Detect borderless / open table rows (multi-line numeric columns or multi-column spacing).
    Excludes prose sentences, multi-line hyphenated titles, and Supplementary Note headings.
    """
    t_strip = text.strip()
    if not t_strip:
        return False

    # Exemption 1: Chinese theorem / proof / prose sentence starters
    if re.match(r"^(?:定理|引理|命题|推论|证明|例|注|设|令|考虑|由表|如图|在|当|若|根据|为了|针对|因此|从而|其中)\b", t_strip):
        return False
    if re.search(r"^(?:定理|引理|命题|推论|证明)\s*\d+", t_strip):
        return False

    # Exemption 2: English narrative prose containing multiple function words
    en_func_hits = re.findall(r"\b(?:does not|do not|is|are|was|were|and|that|which|with|for|the|to|of|in|by|from|satisfies|denotes)\b", t_strip.lower())
    if len(en_func_hits) >= 2:
        return False

    # Exemption 3: Multi-line hyphenated headings/titles or Supplementary/Extended Data headings
    if re.search(r"[a-zA-Z]{2,}-\s*\n\s*[a-zA-Z]{2,}", t_strip):
        return False
    if re.match(r"^(?:Supplementary\s+Note|Supplementary\s+Information|Extended\s+Data|Ethical\s+and)\b", t_strip, re.I):
        return False

    lines = [l.strip() for l in t_strip.splitlines() if l.strip()]
    if not lines:
        return False

    # Case 1: Multiple short stacked lines with genuine numeric/metric data (>= 2 numeric/percentage cells)
    if len(lines) >= 2:
        has_num_col = sum(1 for l in lines if re.search(r"^\d+(?:\.\d+)?%?$", l))
        has_multi_space_cols = sum(1 for l in lines if re.search(r"\S\s{3,}\S", l))
        no_terminal = not any(l.endswith((".", "。", "?", "？", "!", "！")) for l in lines)
        if no_terminal and (has_num_col >= 2 or has_multi_space_cols >= 2):
            return True

    # Case 2: Dense numeric row with multiple floats separated by spaces
    floats = re.findall(r"\b\d+\.\d+\b", t_strip)
    if len(floats) >= 2 and len(t_strip) < 120 and not t_strip.endswith((".", "。")):
        return True

    return False


def split_mixed_heading_and_body(blocks):
    """
    Split blocks where PyMuPDF merged a Level-1/2 section heading line with the
    following body paragraph (e.g. 'IV. APPLICATIONS...\\nIn this section...').
    """
    split_head_pat = re.compile(
        r"^(?:"
        r"[IVXLCDM]+\.\s+[A-Z][^\n]{2,95}"
        r"|\d+(?:\.\d+)*\.?\s+[A-Z\u4e00-\u9fa5][^\n]{1,90}"
        r"|[A-Z]\.\s+[A-Z][^\n]{2,90}"
        r"|(?:[一二三四五六七八九十]+[、\.]|（[一二三四五六七八九十]+）)\s*[\u4e00-\u9fa5A-Za-z][^\n]{1,70}"
        r")$"
    )
    out = []
    for b in blocks:
        if b.get("type") == "formula":
            out.append(b)
            continue
        txt = (b.get("text") or "").strip()
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        if len(lines) < 2:
            out.append(b)
            continue

        split_idx = None
        head_candidate = ""
        # Try 2-line wrapped all-caps heading first, then 1-line heading
        if len(lines) >= 3 and len(lines[0]) <= 80 and len(lines[1]) <= 60:
            joined2 = f"{lines[0]} {lines[1]}"
            if (
                split_head_pat.match(joined2)
                and not joined2.endswith((".", "。", "?", "!", ";", "；"))
                and not TOC_ENTRY_TAIL_REGEX.search(joined2)
                and not CAPTION_REGEX.match(joined2)
                and not PROSE_CAPTION_REF_REGEX.match(joined2)
                and lines[0].isupper()
                and lines[1].isupper()
            ):
                split_idx = 2
                head_candidate = joined2

        if split_idx is None:
            l0 = lines[0]
            if (
                split_head_pat.match(l0)
                and not l0.endswith((".", "。", "?", "!", ";", "；"))
                and not TOC_ENTRY_TAIL_REGEX.search(l0)
                and not CAPTION_REGEX.match(l0)
                and not PROSE_CAPTION_REF_REGEX.match(l0)
                and not re.match(r"^\d{4}\b", l0)
            ):
                split_idx = 1
                head_candidate = l0

        if split_idx is None:
            out.append(b)
            continue

        rest_lines = lines[split_idx:]
        rest_text = "\n".join(rest_lines).strip()
        if len(rest_text) < 12 or TOC_ENTRY_TAIL_REGEX.search(rest_lines[0]):
            out.append(b)
            continue

        x0, y0, x1, y1 = b["bbox"]
        frac = split_idx / max(2, len(lines))
        y_mid = round(y0 + (y1 - y0) * frac, 2)

        h_blk = dict(b)
        h_blk["text"] = head_candidate
        h_blk["bbox"] = [x0, y0, x1, y_mid]
        h_blk["is_split_heading"] = True

        body_blk = dict(b)
        body_blk["text"] = rest_text
        body_blk["bbox"] = [x0, y_mid, x1, y1]
        out.append(h_blk)
        out.append(body_blk)

    return out


def classify_block_semantics(b, body_size, is_first_page, page_height=842.0, in_references=False, in_toc=False, seen_body_start=False):
    """Assign structural role cleanly without misclassifying authors/equations/tables/TOC/prose."""
    text = CONTROL_CHAR_REGEX.sub("", b["text"]).strip()
    avg_size = b.get("avg_size", body_size)
    y0 = b["bbox"][1]

    # Standalone Supplementary / Appendix titles always override references mode (unless inside TOC)
    if not in_toc and not TOC_ENTRY_TAIL_REGEX.search(text):
        if re.match(r"^(?:Appendix|Appendices|Supplementary\s+Note|Supplementary\s+Information|Extended\s+Data|附\s*录)\b", text, re.I) and len(text) < 120 and not text.endswith("."):
            return "heading_h1"

    # Preserve prose blocks explicitly split from formula tails
    if b.get("type") == "formula":
        return "formula"

    # TOC section protection: keep TOC entries as paragraphs so 'References 28' never locks references
    if TOC_TITLE_REGEX.match(text):
        return "heading_h1"
    if in_toc:
        return "paragraph"

    # References section mode: all entries & continuation lines stay reference_entry
    if in_references:
        return "reference_entry"

    # Caption vs prose sentence starting with 'Table 4 lists...' / 'Figure 2 shows...'
    if CAPTION_REGEX.match(text) and not PROSE_CAPTION_REF_REGEX.match(text):
        low = text.lower()
        if low.startswith("table") or text.startswith("表") or text.startswith("续表"):
            return "table_caption"
        return "figure_caption"

    # Footnote isolation (bottom region of page > 76% height, smaller font or academic metadata markers)
    is_bottom_region = (y0 > page_height * 0.76)
    has_fn_marker = bool(re.match(r"^[\*†‡•]|^(?:收稿日期|基金项目|作者简介|通信作者|E-mail|Email|Supported by|Correspondence to|Manuscript received|Digital Object Identifier)\b", text, re.I))
    is_small_font = (avg_size <= body_size * 0.88)
    if is_bottom_region and (has_fn_marker or is_small_font):
        return "footnote"

    # First page arXiv stamp, standalone date, title, author metadata & abstract
    if is_first_page:
        if re.match(r"^arXiv:\s*\d{4}\.\d+", text, re.I):
            return "metadata"
        if re.match(r"^(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}$", text, re.I):
            return "metadata"
        if avg_size >= body_size * 1.30 and y0 < page_height * 0.38 and len(text) < 220:
            return "title"
        if re.match(r"^(?:Abstract|摘\s*要)\b[:\s—\-]?", text, re.IGNORECASE):
            return "abstract"
        # Author / affiliation metadata before Abstract / Contents / Section 1
        if not seen_body_start and y0 < page_height * 0.55 and len(text) < 350:
            if not re.match(r"^(?:1\.|I\.|1\s+|一、)", text):
                return "metadata"
        if y0 < 380 and len(text) < 280 and not re.match(r"^(?:1\.|I\.|1\s+)", text):
            has_inst_kw = bool(re.search(r"(?:@|University|Institute|Laboratory|Dept\.|Department|学院|实验室|大学|关键词|Keywords|Index Terms)", text, re.I))
            has_en_author_sup = bool(re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+(?:\^\{?[\d,\*∗†‡]+\}?|[\d,\*∗†‡]+)", text)) and ("," in text or "and" in text or "∗" in text or "*" in text) and not text.endswith(".")
            has_zh_author_sup = bool(re.search(r"[\u4e00-\u9fa5]{2,4}\s*(?:\^\{?[\d,\*∗†‡]+\}?|[\d,\*∗†‡]+)", text)) and ("," in text or "，" in text or "*" in text or "∗" in text) and not text.endswith("。")
            has_std_author_line = (avg_size >= body_size * 1.02 and "," in text and "and" in text and not text.endswith("."))
            if has_inst_kw or has_en_author_sup or has_zh_author_sup or has_std_author_line:
                return "metadata"

    # References header (must NOT have trailing page number)
    if re.match(r"^(?:References|REFERENCES|Bibliography|参\s*考\s*文\s*献)\s*[:：]?$", text):
        return "heading_h1"

    # Explicit reference entries
    if re.match(r"^\[\d+\]\s+[A-Z\u4e00-\u9fa5]", text):
        return "reference_entry"

    # Standalone academic top headings (guard against TOC lines ending in digits)
    if not TOC_ENTRY_TAIL_REGEX.search(text):
        clean_lower = re.sub(r"[^a-z\u4e00-\u9fa5\s]", "", text.lower()).strip()
        if clean_lower in STANDALONE_HEADINGS and len(text) < 50:
            return "heading_h1"
        if re.match(r"^(?:Ethical\s+and\s+dual-use|Code\s+availability|Data\s+availability|Author\s+contributions|Competing\s+interests)\b", text, re.I) and len(text) < 60:
            return "heading_h1"

    # Chinese Headings
    if CHINESE_HEADING_REGEX.match(text):
        if re.match(r"^(?:[一二三四五六七八九十]+[、\.]|[0-9]+\s+[\u4e00-\u9fa5]|引\s*言|结\s*论|结\s*语|背\s*景|摘\s*要)", text):
            return "heading_h1"
        elif re.match(r"^（[一二三四五六七八九十]+）", text):
            return "heading_h2"
        return "heading_h3"

    # English Numbered Headings (with strict level hierarchy, excluding TOC lines)
    if HEADING_REGEX.match(text) and "\n" not in text and len(text) < 110 and not TOC_ENTRY_TAIL_REGEX.search(text):
        if re.match(r"^(?:[0-9]+\.[0-9]+\.[0-9]+)\b", text):
            return "heading_h3"
        if re.match(r"^(?:[0-9]+\.[0-9]+|[A-Z]\.)\b", text):
            return "heading_h2"
        return "heading_h1"

    # Large font short lines (excluding math expressions & TOC lines)
    if avg_size >= body_size * 1.15 and len(text) < 90 and "\n" not in text and not is_math_fragment_block(b) and not TOC_ENTRY_TAIL_REGEX.search(text):
        if not text.endswith((".", "。", "?", "!")):
            if avg_size >= body_size * 1.3:
                return "heading_h1"
            elif avg_size >= body_size * 1.15:
                return "heading_h2"
            return "heading_h3"

    # Open/borderless table rows
    if is_table_row_heuristic(text):
        return "table_content"

    return "paragraph"


# ----------------------------------------------------------------------
# 6. Coalescing & Intelligent Paragraph Stitching (Cross-column / Cross-page)
# ----------------------------------------------------------------------

TERMINAL_PUNCTUATION_REGEX = re.compile(
    r"""(?:\.|\?|\!|\:|\;|。|！|？|；|：|[\.?!][\'"”’\)]|[\.?!]\])$"""
)


def is_abbreviation_ending(text):
    lowered = text.strip().lower()
    for abbr in COMMON_ABBREVIATIONS:
        if lowered.endswith(abbr):
            return True
    return False


def coalesce_table_and_reference_blocks(blocks):
    """
    1. Merge adjacent multi-line title blocks on page 1.
    2. Merge consecutive table_content blocks into cohesive tables.
    3. Merge continuation lines of reference entries in References section.
    """
    if not blocks:
        return []

    out = []
    for b in blocks:
        b_type = b.get("type", "paragraph")
        if not out:
            out.append(b)
            continue

        prev = out[-1]
        prev_type = prev.get("type", "paragraph")

        # Coalesce adjacent title lines on page 1 with matching large font size
        if prev_type == "title" and b_type == "title" and prev["page"] == b["page"]:
            if abs(prev.get("avg_size", 10.0) - b.get("avg_size", 10.0)) <= 2.0:
                prev["text"] = prev["text"].rstrip() + " " + b["text"].lstrip()
                prev["bbox"] = [
                    min(prev["bbox"][0], b["bbox"][0]),
                    min(prev["bbox"][1], b["bbox"][1]),
                    max(prev["bbox"][2], b["bbox"][2]),
                    max(prev["bbox"][3], b["bbox"][3]),
                ]
                continue

        # Coalesce standalone 'Abstract' label with immediately following abstract body
        if prev_type == "abstract" and re.match(r"^(?:Abstract|摘\s*要)\s*[:：]?\s*$", prev["text"].strip(), re.I) and b_type in ("paragraph", "abstract"):
            prev["text"] = prev["text"].strip() + ": " + b["text"].strip()
            prev["type"] = "abstract"
            prev["bbox"] = [
                min(prev["bbox"][0], b["bbox"][0]),
                min(prev["bbox"][1], b["bbox"][1]),
                max(prev["bbox"][2], b["bbox"][2]),
                max(prev["bbox"][3], b["bbox"][3]),
            ]
            continue

        # Coalesce consecutive table rows on the same page
        if prev_type == "table_content" and b_type == "table_content" and prev["page"] == b["page"]:
            prev["text"] = prev["text"].rstrip() + "\n" + b["text"].lstrip()
            prev["bbox"] = [
                min(prev["bbox"][0], b["bbox"][0]),
                min(prev["bbox"][1], b["bbox"][1]),
                max(prev["bbox"][2], b["bbox"][2]),
                max(prev["bbox"][3], b["bbox"][3]),
            ]
            continue

        # Coalesce multi-block reference entry (when prev is reference_entry and curr does NOT start with [n])
        if prev_type == "reference_entry" and b_type == "reference_entry":
            txt_s = b["text"].strip()
            if not re.match(r"^\[\d+\]\s", txt_s) and not re.match(r"^\d+\.\s+[A-Z]", txt_s):
                prev["text"] = prev["text"].rstrip() + " " + txt_s
                prev["bbox"] = [
                    min(prev["bbox"][0], b["bbox"][0]),
                    min(prev["bbox"][1], b["bbox"][1]),
                    max(prev["bbox"][2], b["bbox"][2]),
                    max(prev["bbox"][3], b["bbox"][3]),
                ]
                continue

        out.append(b)
    return out


def can_stitch_blocks(curr, nxt):
    """
    Determine if block curr and block nxt should be stitched together.
    Enforces strict FENCES across display_equation, table_content, captions, headings,
    except when nxt starts with a dangling math subscript/superscript continuation (_{ or ^{).
    """
    curr_type = curr.get("type", "paragraph")
    nxt_type = nxt.get("type", "paragraph")

    curr_text = curr["text"].strip()
    nxt_text = nxt["text"].strip()

    if not curr_text or not nxt_text:
        return False, None

    # Patch 3: Dangling subscript / superscript continuation (e.g. 'ε^{∗}' + '_{EA}(E) = +∞...')
    if nxt_text.startswith(("_{", "^{")) and curr_type in ("paragraph", "abstract", "formula"):
        has_math_context = (curr_type == "formula") or ("_{" in curr_text) or ("^{" in curr_text) or any(c in MATH_SYMBOL_CHARS for c in curr_text[-20:])
        if has_math_context:
            return True, "subscript_continuation"

    # Strict Boundary Fence: never stitch across non-prose elements!
    if curr_type not in ("paragraph", "abstract"):
        return False, None
    if nxt_type not in ("paragraph", "abstract"):
        return False, None

    ends_with_hyphen = curr_text.endswith("-") or curr_text.endswith("\u00ad")
    ends_with_terminal = bool(TERMINAL_PUNCTUATION_REGEX.search(curr_text))
    is_abbr = is_abbreviation_ending(curr_text)

    if ends_with_terminal and not is_abbr:
        return False, None

    # Same-page vertical proximity check within same column (coalesce physical line-split paragraphs)
    same_page = (nxt["page"] == curr["page"])
    same_col = (curr.get("column") == nxt.get("column"))
    if same_page and same_col:
        v_gap = nxt["bbox"][1] - curr["bbox"][3]
        if 0 <= v_gap < 15.0:
            # Physical lines inside the same paragraph block
            stitch_type = "intra_column"
            return True, stitch_type

    starts_lower = len(nxt_text) > 0 and nxt_text[0].islower()
    starts_cjk = len(nxt_text) > 0 and ('\u4e00' <= nxt_text[0] <= '\u9fff')
    ends_cjk = len(curr_text) > 0 and ('\u4e00' <= curr_text[-1] <= '\u9fff')
    # Math variable split across inline equation (e.g. 'Here, H(x)' + 'A (t) is...' or '...O(k)' + 'B = ...')
    starts_var_cont = bool(re.match(r"^(?:[A-Z]\s*\([^\)]{1,6}\)|[A-Z]\s+(?:is|are|denotes?|represents?|and|=))", nxt_text)) and bool(re.search(r"[A-Za-z]\([^\)]{1,6}\)$", curr_text))

    should_stitch = (
        ends_with_hyphen
        or starts_lower
        or (ends_cjk and starts_cjk)
        or is_abbr
        or starts_var_cont
    )

    if not should_stitch:
        return False, None

    if nxt["page"] > curr["page"]:
        stitch_type = "cross_page"
    elif curr.get("column") != nxt.get("column"):
        stitch_type = "cross_column"
    else:
        stitch_type = "intra_column"

    return True, stitch_type


def _apply_merge_to_curr(curr, nxt, stitch_type):
    """Helper to append block nxt onto curr with hyphen/CJK/subscript spacing rules."""
    curr_text = curr["text"]
    nxt_text = nxt["text"]
    ends_with_hyphen = curr_text.endswith("-") or curr_text.endswith("\u00ad")
    is_cjk = ('\u4e00' <= curr_text[-1:] <= '\u9fff') or ('\u4e00' <= nxt_text[:1] <= '\u9fff')

    if stitch_type == "subscript_continuation":
        merged_txt = curr_text.rstrip() + nxt_text.lstrip()
    elif ends_with_hyphen:
        w_match = re.search(r"\b([a-zA-Z]{2,})[-–—]$", curr_text)
        nxt_m = re.match(r"^([a-zA-Z]{2,})\b", nxt_text)
        if w_match and nxt_m:
            w1 = w_match.group(1)
            w2 = nxt_m.group(1)
            candidate = f"{w1.lower()}-{w2.lower()}"
            if candidate in KNOWN_COMPOUNDS:
                merged_txt = curr_text + nxt_text
            else:
                merged_txt = curr_text[:-1] + nxt_text
        else:
            merged_txt = curr_text[:-1] + nxt_text
    elif is_cjk:
        merged_txt = curr_text + nxt_text
    else:
        merged_txt = curr_text + " " + nxt_text

    curr["text"] = merged_txt
    curr["page_span"] = [curr["page_span"][0], nxt["page"]]
    curr["printed_page_span"] = [curr["printed_page_span"][0], nxt.get("printed_page", str(nxt["page"]))]
    curr["stitched_types"].append(stitch_type)

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
        "column": nxt.get("column", "full"),
    })


def repair_and_stitch_paragraphs(blocks):
    """
    Coalesce table/ref continuations, stitch cross-column/cross-page/cross-float paragraphs,
    and clean physical PDF soft linebreaks.
    """
    if not blocks:
        return [], {"cross_column_stitch_count": 0, "cross_page_stitch_count": 0, "total_stitched": 0}

    blocks = coalesce_table_and_reference_blocks(blocks)

    merged = []
    curr = dict(blocks[0])
    curr["page_span"] = [curr["page"], curr["page"]]
    curr["printed_page_span"] = [curr.get("printed_page", str(curr["page"])), curr.get("printed_page", str(curr["page"]))]
    curr["evidence_anchors"] = [{
        "page": curr["page"],
        "printed_page": curr.get("printed_page", str(curr["page"])),
        "block_id": curr["block_id"],
        "bbox": curr["bbox"],
        "column": curr.get("column", "full"),
    }]
    curr["stitched_types"] = []

    cross_col_count = 0
    cross_page_count = 0
    total_stitched = 0
    consumed = set()

    for i in range(1, len(blocks)):
        if i in consumed:
            continue
        nxt = blocks[i]
        can_merge, stitch_type = can_stitch_blocks(curr, nxt)

        if can_merge:
            total_stitched += 1
            if stitch_type == "cross_page":
                cross_page_count += 1
            elif stitch_type == "cross_column":
                cross_col_count += 1
            _apply_merge_to_curr(curr, nxt, stitch_type)
            continue

        # Cross-float lookahead stitching: e.g. paragraph ending with 'It' interrupted by Figure 1 + labels before 'occupies...'
        curr_txt = curr.get("text", "").strip()
        if (
            curr.get("type") in ("paragraph", "abstract")
            and curr_txt
            and not TERMINAL_PUNCTUATION_REGEX.search(curr_txt)
            and nxt.get("type") in ("figure_caption", "table_caption", "table_content", "formula")
        ):
            seen_caption = nxt.get("type") in ("figure_caption", "table_caption")
            for j in range(i, min(len(blocks), i + 16)):
                if j in consumed:
                    continue
                cand = blocks[j]
                c_type = cand.get("type", "paragraph")
                if c_type in ("heading_h1", "heading_h2", "heading_h3", "reference_entry", "title", "metadata"):
                    break
                if c_type in ("figure_caption", "table_caption"):
                    seen_caption = True
                    continue
                c_txt = cand.get("text", "").strip()
                # Before exiting caption/float zone, allow lowercase prose continuation directly or skip figure diagram annotations
                if c_type == "paragraph" and len(c_txt.split()) >= 4:
                    if (c_txt and c_txt[0].islower()) or curr_txt.endswith(("-", "\u00ad")):
                        st_type = "cross_page" if cand["page"] > curr["page"] else "cross_column"
                        total_stitched += 1
                        if st_type == "cross_page":
                            cross_page_count += 1
                        else:
                            cross_col_count += 1
                        _apply_merge_to_curr(curr, cand, st_type)
                        consumed.add(j)
                        break
                    elif seen_caption or cand.get("page", 1) == curr.get("page", 1):
                        # Only abort lookahead once past figure/table caption (so diagram annotations don't prematurely stop scan)
                        break

        # Finalize curr and advance to nxt (which is the interleaved figure/formula or next distinct block)
        curr["text"] = normalize_paragraph_text(curr["text"], curr.get("type", "paragraph"))
        if curr["text"]:
            merged.append(curr)

        curr = dict(nxt)
        curr["page_span"] = [curr["page"], curr["page"]]
        curr["printed_page_span"] = [curr.get("printed_page", str(curr["page"])), curr.get("printed_page", str(curr["page"]))]
        curr["evidence_anchors"] = [{
            "page": curr["page"],
            "printed_page": curr.get("printed_page", str(curr["page"])),
            "block_id": curr["block_id"],
            "bbox": curr["bbox"],
            "column": curr.get("column", "full"),
        }]
        curr["stitched_types"] = []

    curr["text"] = normalize_paragraph_text(curr["text"], curr.get("type", "paragraph"))
    if curr["text"]:
        merged.append(curr)

    merged = split_trailing_prose_from_formulas(merged)

    stats = {
        "cross_column_stitch_count": cross_col_count,
        "cross_page_stitch_count": cross_page_count,
        "total_stitched": total_stitched,
    }
    return merged, stats


def split_trailing_prose_from_formulas(blocks):
    """
    Post-stitching pass: split formula/display_equation blocks containing a trailing
    unnumbered English narrative sentence (e.g. 'B = Ex B(ρin_{B}). In the experimental...').
    """
    out = []
    narrative_words = ENGLISH_PROSE_FUNC_WORDS | {
        "a", "an", "on", "at", "or", "if", "not", "we", "it", "this", "that", "these", "can", "may", "be", "have", "has"
    }
    # Find sentence boundary '. <Capitalized lower-tailed word> ...'
    boundary_pat = re.compile(r"\.\s+([A-Z][a-z]{1,20}\b)")
    for b in blocks:
        if b.get("type") != "formula":
            out.append(b)
            continue

        txt = b.get("text", "").strip()
        split_at = None
        for m in boundary_pat.finditer(txt):
            tail_raw = txt[m.start(1):].strip()
            tail_norm = normalize_paragraph_text(tail_raw, "paragraph")
            alpha_words = re.findall(r"\b[A-Za-z]{2,}\b", tail_norm)
            func_hits = sum(1 for w in alpha_words if w.lower() in narrative_words)
            # Exclude structural braces from math symbol domination check so inline ρ^{in} doesn't block prose split
            math_sym_cnt = sum(1 for c in tail_norm if c in MATH_SYMBOL_CHARS and c not in "{}()")
            eq_cnt = tail_norm.count("=")
            # Require genuine narrative domination (>=4 regular English words, >=2 narrative/function words, no '=' dominance)
            if (
                len(alpha_words) >= 4
                and func_hits >= 2
                and eq_cnt == 0
                and math_sym_cnt <= max(3, len(alpha_words) // 2)
            ):
                split_at = m.start() + 1  # Keep '.' at end of formula_part
                break

        if split_at is None:
            out.append(b)
            continue

        formula_part = txt[:split_at].strip()
        prose_raw = txt[split_at:].strip()
        prose_norm = normalize_paragraph_text(prose_raw, "paragraph")

        f_blk = dict(b)
        f_blk["text"] = formula_part
        f_blk["type"] = "formula"
        out.append(f_blk)

        p_blk = dict(b)
        p_blk["text"] = prose_norm
        p_blk["type"] = "paragraph"
        p_blk.pop("formula_id", None)
        if "page_span" in b:
            end_p = b["page_span"][-1]
            p_blk["page_span"] = [end_p, end_p]
        if "printed_page_span" in b:
            end_pp = b["printed_page_span"][-1]
            p_blk["printed_page_span"] = [end_pp, end_pp]
        p_blk["stitched_types"] = []
        out.append(p_blk)

    return out


# ----------------------------------------------------------------------
# 7. Main Layout Pipeline Execution
# ----------------------------------------------------------------------

def parse_layout(pdf_path, diagnosis_path=None):
    """Full layout parsing pipeline with fragmentation elimination & cross-page stitching."""
    diagnosis = None
    if diagnosis_path and os.path.exists(diagnosis_path):
        try:
            with open(diagnosis_path, "r", encoding="utf-8") as f:
                diagnosis = json.load(f)
        except Exception:
            pass

    pages_blocks, backend_used = extract_raw_blocks(pdf_path, diagnosis)
    clean_pages, headers_footers = filter_headers_footers(pages_blocks)

    all_raw_blocks = [b for p in clean_pages for b in p["blocks"]]
    body_size = infer_body_font_size(all_raw_blocks)

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

    in_references_section = False
    in_toc_section = False
    seen_body_start = False
    expect_abstract_body = False

    for p in clean_pages:
        p_num = p["page"]
        w = p["width"]
        h = p["height"]
        p_ordered = reconstruct_page_reading_order(p["blocks"], w, h, is_first_page=(p_num == 1), body_size=body_size)
        p_ordered = split_mixed_heading_and_body(p_ordered)
        printed_folio = page_map.get(p_num, str(p_num + offset))

        for b in p_ordered:
            b["block_id"] = f"b_{p_num}_{global_id}"
            b["page"] = p_num
            b["printed_page"] = printed_folio

            txt_pre = CONTROL_CHAR_REGEX.sub("", b.get("text", "")).strip()
            if in_toc_section and (
                re.match(r"^(?:1\.?\s+Introduction|I\.\s+INTRODUCTION|1\s+引言|一、)", txt_pre, re.I)
                and not TOC_ENTRY_TAIL_REGEX.search(txt_pre)
            ):
                in_toc_section = False

            b_type = classify_block_semantics(
                b,
                body_size,
                is_first_page=(p_num == 1),
                page_height=h,
                in_references=in_references_section,
                in_toc=in_toc_section,
                seen_body_start=seen_body_start,
            )
            txt_s = b["text"].strip()

            if b_type == "abstract":
                seen_body_start = True
                if len(txt_s) <= 20:
                    expect_abstract_body = True
            elif expect_abstract_body and b_type == "paragraph":
                b_type = "abstract"
                expect_abstract_body = False
            elif b_type in ("heading_h1", "heading_h2", "heading_h3"):
                expect_abstract_body = False
                seen_body_start = True
                if TOC_TITLE_REGEX.match(txt_s):
                    in_toc_section = True

            b["type"] = b_type

            # Enter References mode only outside TOC and without trailing page numbers
            if not in_toc_section and b_type in ("heading_h1", "heading_h2") and re.match(r"^(?:References|REFERENCES|Bibliography|参\s*考\s*文\s*献)\s*[:：]?$", txt_s):
                in_references_section = True
            elif not in_toc_section and not in_references_section and p_num > 1 and b_type == "reference_entry" and re.match(r"^\[1\]\s+", txt_s):
                in_references_section = True
            elif in_references_section and b_type == "heading_h1" and re.match(r"^(?:Appendix|Appendices|Supplementary\s+Note|Supplementary\s+Information|Extended\s+Data|附\s*录)\b", txt_s, re.I):
                in_references_section = False

            has_cid = "(cid:" in b["text"]
            has_ufffd = "\ufffd" in b["text"]
            has_pua = bool(FORMULA_PUA_REGEX.search(b["text"]))
            if b_type == "formula":
                b["has_formula_pua"] = has_pua
                b["needs_visual_check"] = bool(has_cid or has_ufffd)
            else:
                b["has_formula_pua"] = False
                b["needs_visual_check"] = bool(has_cid or has_ufffd)
            b["confidence"] = 0.50 if b["needs_visual_check"] else 0.95

            ordered_blocks_all.append(b)
            global_id += 1

    final_blocks, stitch_stats = repair_and_stitch_paragraphs(ordered_blocks_all)

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
        "stitch_stats": stitch_stats,
        "metadata": {
            "title": doc_title or "Untitled Scientific Document",
            "abstract": abstract_text or "",
            "section_count": len(sections),
            "footnote_count": len(footnotes),
            "cross_column_stitch_count": stitch_stats["cross_column_stitch_count"],
            "cross_page_stitch_count": stitch_stats["cross_page_stitch_count"],
        },
        "headers_footers": headers_footers,
        "footnotes": footnotes,
        "sections": sections,
        "blocks": final_blocks,
    }

    md_lines = [f"# {structured_doc['metadata']['title']}\n"]
    if structured_doc["metadata"]["abstract"]:
        md_lines.append(f"> **Abstract**: {structured_doc['metadata']['abstract']}\n")

    for b in final_blocks:
        b_id = b["block_id"]
        p_span_str = f"p{b['page_span'][0]}" if b["page_span"][0] == b["page_span"][1] else f"p{b['page_span'][0]}-p{b['page_span'][1]}"
        b_type = b.get("type", "paragraph")
        txt = b["text"]
        tag = f"[{p_span_str}|{b_id}]"

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
        elif b_type == "table_content":
            md_lines.append(f"\n```table\n{tag}\n{txt}\n```\n")
        elif b_type == "reference_entry":
            md_lines.append(f"- {tag} {txt}")
        elif b_type == "footnote":
            md_lines.append(f"\n> [^footnote {tag}] {txt}\n")
        else:
            md_lines.append(f"{tag} {txt}\n")

    reading_order_md = "\n".join(md_lines)
    return structured_doc, reading_order_md


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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

    print(f"[layout_parser] Parsed {len(structured_doc.get('blocks', []))} blocks (stitched: {structured_doc['stitch_stats']['total_stitched']}) -> {args.out_json}")


if __name__ == "__main__":
    main()
