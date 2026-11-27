#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case_ingestor.py - Dual-Mode Scientific Ingestion Engine (Mode A: JATS Dual-Paper Corpus | Mode B: PDF/DOCX/LaTeX/HTML/MD/TXT)
Part of the Literature Knowledge Graph Skill (task_10_kg).
Strict zero-crash design: top-level imports are standard libraries only.
"""

import os
import sys
import json
import re
import hashlib
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
    from pdf_engine.table_formula_ocr import (
        process_elements,
        render_page_crop_fitz,
        render_page_png,
        run_ocr_on_image,
        run_ocr_on_image_detailed,
    )
except ImportError:
    try:
        from .pdf_engine.pdf_diagnose import diagnose_pdf, calculate_garbled_metrics
        from .pdf_engine.layout_parser import parse_layout, STANDALONE_HEADINGS
        from .pdf_engine.table_formula_ocr import (
            process_elements,
            render_page_crop_fitz,
            render_page_png,
            run_ocr_on_image,
            run_ocr_on_image_detailed,
        )
    except Exception:
        diagnose_pdf = None
        calculate_garbled_metrics = None
        parse_layout = None
        process_elements = None
        render_page_crop_fitz = None
        render_page_png = None
        run_ocr_on_image = None
        run_ocr_on_image_detailed = None
        STANDALONE_HEADINGS = set()

_ORIG_CASE_RUN_OCR = run_ocr_on_image


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


def _sha256_text(text):
    norm = re.sub(r"\s+", " ", (text or "").strip()).lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _local_tag(tag):
    if not tag:
        return ""
    return tag.split("}", 1)[-1].lower()


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
    if page_range and unit.get("page_span"):
        u_start, u_end = unit.get("page_span")
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
    """Determine logical heading depth (1, 2, 3) based strictly on numbering first."""
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
# 4. Mode A: JATS Dual-Paper Structured Corpus Ingestion
# ----------------------------------------------------------------------

def _coerce_authors_list(raw_authors):
    """Normalize various JSON/XML author representations into a clean list of strings."""
    out = []
    if not raw_authors:
        return out
    if isinstance(raw_authors, str):
        for part in re.split(r"\s*(?:;|,|\band\b|、)\s*", raw_authors.strip()):
            if part.strip():
                out.append(part.strip())
        return out
    if isinstance(raw_authors, list):
        for item in raw_authors:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("full_name")
                if not name:
                    given = (item.get("given_names") or item.get("given") or item.get("first") or "").strip()
                    sur = (item.get("surname") or item.get("family") or item.get("last") or "").strip()
                    name = f"{given} {sur}".strip() if (given or sur) else ""
                if name:
                    out.append(str(name).strip())
    return out


def _normalize_manifest_entry(entry, idx, default_role=None):
    """Normalize a single paper entry from corpus-manifest.json."""
    if not isinstance(entry, dict):
        return None
    role = (
        entry.get("role")
        or entry.get("paper_role")
        or default_role
        or ("primary" if idx == 0 else ("secondary" if idx == 1 else f"doc_{idx + 1}"))
    )
    role_low = str(role).strip().lower()
    source_id = str(
        entry.get("source_id")
        or entry.get("paper_id")
        or entry.get("id")
        or role_low
    ).strip()

    files = []
    for k in ("files", "paths", "artifacts"):
        val = entry.get(k)
        if isinstance(val, list):
            for v in val:
                if isinstance(v, str):
                    files.append(v)
                elif isinstance(v, dict) and (v.get("path") or v.get("file")):
                    files.append(v.get("path") or v.get("file"))
    for k in ("file", "path", "material_file", "material_json", "publisher_xml", "xml_file", "json_file"):
        val = entry.get(k)
        if isinstance(val, str) and val.strip():
            files.append(val.strip())

    year_raw = entry.get("year") or entry.get("pub_year")
    year_val = int(year_raw) if (year_raw is not None and str(year_raw).isdigit()) else None

    return {
        "source_id": source_id,
        "role": role_low,
        "title": (entry.get("title") or "").strip() or None,
        "doi": (entry.get("doi") or "").strip() or None,
        "authors": _coerce_authors_list(entry.get("authors")),
        "year": year_val,
        "venue": (entry.get("venue") or entry.get("journal") or "").strip() or None,
        "license": (entry.get("license") or entry.get("licence") or "").strip() or None,
        "source_hash": (entry.get("source_hash") or entry.get("sha256") or entry.get("hash") or "").strip() or None,
        "files": list(dict.fromkeys(files)),
    }


def _parse_corpus_manifest(manifest_path):
    """Elastic parser for corpus-manifest.json (list or dict variants)."""
    if not manifest_path or not os.path.isfile(manifest_path):
        return []
    try:
        with open(manifest_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception:
        return []

    entries = []
    if isinstance(data, list):
        for idx, item in enumerate(data):
            norm = _normalize_manifest_entry(item, idx)
            if norm:
                entries.append(norm)
        return entries

    if isinstance(data, dict):
        for key in ("documents", "sources", "papers", "materials", "items", "corpus"):
            val = data.get(key)
            if isinstance(val, list):
                for idx, item in enumerate(val):
                    norm = _normalize_manifest_entry(item, idx)
                    if norm:
                        entries.append(norm)
                if entries:
                    return entries
            elif isinstance(val, dict):
                for idx, (sub_k, sub_v) in enumerate(val.items()):
                    if isinstance(sub_v, dict):
                        norm = _normalize_manifest_entry(sub_v, idx, default_role=sub_k)
                        if norm:
                            entries.append(norm)
                if entries:
                    return entries

        # Handle {"primary": {...}, "secondary": {...}}
        for idx, role_k in enumerate(("primary", "secondary")):
            sub = data.get(role_k)
            if isinstance(sub, dict):
                norm = _normalize_manifest_entry(sub, idx, default_role=role_k)
                if norm:
                    entries.append(norm)
        if entries:
            return entries

        # Fallback: single dict manifest entry
        if data.get("doi") or data.get("title") or data.get("source_id"):
            norm = _normalize_manifest_entry(data, 0)
            if norm:
                entries.append(norm)
    return entries


def _extract_embedded_json_meta(data):
    """Extract paper-level metadata if embedded inside a material JSON."""
    if not isinstance(data, dict):
        return {}
    meta_src = data.get("metadata") if isinstance(data.get("metadata"), dict) else data
    year_raw = meta_src.get("year") or meta_src.get("pub_year") or data.get("year")
    year_val = int(year_raw) if (year_raw is not None and str(year_raw).isdigit()) else None
    return {
        "source_id": (meta_src.get("source_id") or data.get("source_id") or "").strip() or None,
        "title": (meta_src.get("title") or data.get("title") or "").strip() or None,
        "doi": (meta_src.get("doi") or data.get("doi") or "").strip() or None,
        "authors": _coerce_authors_list(meta_src.get("authors") or data.get("authors")),
        "year": year_val,
        "venue": (meta_src.get("venue") or meta_src.get("journal") or data.get("venue") or "").strip() or None,
        "license": (meta_src.get("license") or data.get("license") or "").strip() or None,
        "source_hash": (meta_src.get("source_hash") or meta_src.get("sha256") or data.get("source_hash") or "").strip() or None,
    }


def _collect_json_paragraphs_recursive(obj, default_source_id, section_stack=None):
    """Recursively flatten paragraphs/sections/blocks from elastic JATS JSON structures."""
    section_stack = section_stack or []
    records = []

    def _add_para_obj(p_obj, sec_titles, sec_role_hint=None, item_source_id=None):
        if isinstance(p_obj, str):
            txt = p_obj.strip()
            if not txt:
                return
            pid = f"p-{len(records) + 1:03d}"
            records.append({
                "source_id": item_source_id or default_source_id,
                "paragraph_id": pid,
                "section_title": sec_titles[-1] if sec_titles else "Main Body",
                "section_path": " > ".join(sec_titles) if sec_titles else "Main Body",
                "section_role_hint": sec_role_hint,
                "block_type": "paragraph",
                "text": txt,
            })
            return
        if not isinstance(p_obj, dict):
            return

        txt = (
            p_obj.get("text")
            or p_obj.get("paragraph_text")
            or p_obj.get("content")
            or p_obj.get("body")
            or ""
        )
        if isinstance(txt, list):
            txt = " ".join(str(x).strip() for x in txt if str(x).strip())
        txt = str(txt).strip()
        if not txt:
            return

        sid = str(
            p_obj.get("source_id")
            or p_obj.get("paper_id")
            or p_obj.get("doc_source_id")
            or default_source_id
        ).strip()
        pid = str(
            p_obj.get("paragraph_id")
            or p_obj.get("pid")
            or p_obj.get("jats_id")
            or p_obj.get("id")
            or f"p-{len(records) + 1:03d}"
        ).strip()
        sec_t = (
            p_obj.get("section_title")
            or p_obj.get("section")
            or p_obj.get("heading")
            or (sec_titles[-1] if sec_titles else "Main Body")
        )
        sec_p = p_obj.get("section_path") or (
            " > ".join(sec_titles) if sec_titles else str(sec_t)
        )
        role_h = p_obj.get("section_role") or p_obj.get("role") or sec_role_hint
        btype = p_obj.get("block_type") or p_obj.get("type") or "paragraph"
        records.append({
            "source_id": sid,
            "paragraph_id": pid,
            "section_title": str(sec_t).strip(),
            "section_path": str(sec_p).strip(),
            "section_role_hint": str(role_h).strip() if role_h else None,
            "block_type": str(btype).strip(),
            "text": txt,
        })

    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and any(k in item for k in ("paragraphs", "blocks", "subsections", "sections")) and "text" not in item:
                records.extend(_collect_json_paragraphs_recursive(item, default_source_id, section_stack))
            else:
                _add_para_obj(item, section_stack)
        return records

    if not isinstance(obj, dict):
        return records

    # Section node
    sec_title = obj.get("section_title") or obj.get("title") or obj.get("heading")
    sec_role_hint = obj.get("section_role") or obj.get("sec_type")
    new_stack = list(section_stack)
    if sec_title and any(k in obj for k in ("paragraphs", "blocks", "subsections", "sections", "content", "items")):
        new_stack.append(str(sec_title).strip())

    # Abstract handling if string or dict at root
    if "abstract" in obj and obj["abstract"] and not section_stack:
        abs_val = obj["abstract"]
        if isinstance(abs_val, str) and abs_val.strip():
            _add_para_obj({"paragraph_id": "abs-001", "section_title": "Abstract", "section_role": "abstract", "block_type": "abstract", "text": abs_val}, ["Abstract"], "abstract")
        elif isinstance(abs_val, list):
            for idx_a, ap in enumerate(abs_val, 1):
                if isinstance(ap, str):
                    _add_para_obj({"paragraph_id": f"abs-{idx_a:03d}", "section_title": "Abstract", "section_role": "abstract", "block_type": "abstract", "text": ap}, ["Abstract"], "abstract")
                elif isinstance(ap, dict):
                    ap_copy = dict(ap)
                    ap_copy.setdefault("section_title", "Abstract")
                    ap_copy.setdefault("section_role", "abstract")
                    ap_copy.setdefault("block_type", "abstract")
                    _add_para_obj(ap_copy, ["Abstract"], "abstract")

    for k in ("paragraphs", "blocks", "items", "body", "content"):
        val = obj.get(k)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and any(sk in item for sk in ("paragraphs", "blocks", "subsections", "sections")) and "text" not in item:
                    records.extend(_collect_json_paragraphs_recursive(item, default_source_id, new_stack))
                else:
                    _add_para_obj(item, new_stack, sec_role_hint=sec_role_hint)
        elif isinstance(val, str) and val.strip() and k in ("body", "content") and not obj.get("paragraphs"):
            for idx_p, seg in enumerate(re.split(r"\n{2,}", val.strip()), 1):
                if seg.strip():
                    _add_para_obj({"paragraph_id": f"p-{idx_p:03d}", "text": seg.strip()}, new_stack, sec_role_hint=sec_role_hint)

    for sk in ("sections", "subsections"):
        sval = obj.get(sk)
        if isinstance(sval, list):
            for sub in sval:
                records.extend(_collect_json_paragraphs_recursive(sub, default_source_id, new_stack))

    return records


def _parse_jats_material_json(json_path, default_source_id="primary"):
    """Parse primary-material.json / secondary-material.json / material.json."""
    try:
        with open(json_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as e:
        return {}, [], [f"json_parse_error:{os.path.basename(json_path)}:{e}"]

    meta = _extract_embedded_json_meta(data)
    sid = meta.get("source_id") or default_source_id

    # Handle material.json structured by {"primary": {...}, "secondary": {...}} or {"documents": [...]}
    records = []
    if isinstance(data, dict):
        handled_multi = False
        for role_k in ("primary", "secondary"):
            if isinstance(data.get(role_k), (dict, list)):
                handled_multi = True
                records.extend(_collect_json_paragraphs_recursive(data[role_k], role_k))
        for dk in ("documents", "papers", "materials", "sources"):
            if isinstance(data.get(dk), list):
                handled_multi = True
                for idx_d, d_obj in enumerate(data[dk]):
                    d_sid = (d_obj.get("source_id") or d_obj.get("role") or f"doc_{idx_d + 1}") if isinstance(d_obj, dict) else sid
                    records.extend(_collect_json_paragraphs_recursive(d_obj, str(d_sid)))
        if not handled_multi:
            records = _collect_json_paragraphs_recursive(data, sid)
    else:
        records = _collect_json_paragraphs_recursive(data, sid)

    return meta, records, []


def _parse_jats_xml(xml_path):
    """
    Parse JATS publisher XML (e.g. secondary-publisher.xml) using stdlib xml.etree.ElementTree.
    Namespace-agnostic via _local_tag().
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        return {}, [], [f"xml_parse_error:{os.path.basename(xml_path)}:{e}"]

    meta = {
        "title": None,
        "doi": None,
        "authors": [],
        "year": None,
        "venue": None,
        "license": None,
        "funding": None,
    }
    paragraphs = []

    def _iter_text(elem):
        return re.sub(r"\s+", " ", "".join(elem.itertext())).strip()

    def _get_pid(elem, fallback_prefix, idx):
        for k, v in elem.attrib.items():
            if k == "id" or k.endswith("}id") or k == "pid":
                if str(v).strip():
                    return str(v).strip()
        return f"{fallback_prefix}-{idx:03d}"

    # 1. Article Metadata
    for elem in root.iter():
        lt = _local_tag(elem.tag)
        if lt == "article-id" and not meta["doi"]:
            id_type = (elem.attrib.get("pub-id-type") or "").lower()
            txt = _iter_text(elem)
            if id_type == "doi" or re.match(r"^10\.\d{4,9}/", txt):
                meta["doi"] = txt
        elif lt == "article-title" and not meta["title"]:
            t = _iter_text(elem)
            if t:
                meta["title"] = t
        elif lt == "journal-title" and not meta["venue"]:
            t = _iter_text(elem)
            if t:
                meta["venue"] = t
        elif lt == "pub-date" and meta["year"] is None:
            for ch in elem:
                if _local_tag(ch.tag) == "year":
                    ytxt = _iter_text(ch)
                    if ytxt.isdigit():
                        meta["year"] = int(ytxt)
                        break
        elif lt == "contrib" and (elem.attrib.get("contrib-type") or "author").lower() == "author":
            surname = ""
            given = ""
            str_name = ""
            for ch in elem.iter():
                clt = _local_tag(ch.tag)
                if clt == "surname":
                    surname = _iter_text(ch)
                elif clt == "given-names":
                    given = _iter_text(ch)
                elif clt == "string-name":
                    str_name = _iter_text(ch)
            full_name = f"{given} {surname}".strip() if (given or surname) else str_name
            if full_name and full_name not in meta["authors"]:
                meta["authors"].append(full_name)
        elif lt == "license" and not meta["license"]:
            lic_ref = elem.attrib.get("{http://www.w3.org/1999/xlink}href") or elem.attrib.get("license-type") or ""
            lic_txt = _iter_text(elem)
            meta["license"] = (lic_ref or lic_txt)[:120] or None
        elif lt in ("award-id", "funding-source") and not meta.get("funding"):
            f_txt = _iter_text(elem)
            if f_txt:
                meta["funding"] = f_txt[:120]

    # 2. Abstract & Body paragraphs
    p_seq = 0
    for elem in root.iter():
        if _local_tag(elem.tag) == "abstract":
            for p in elem.iter():
                if _local_tag(p.tag) == "p":
                    txt = _iter_text(p)
                    if txt:
                        p_seq += 1
                        paragraphs.append({
                            "paragraph_id": _get_pid(p, "xml-abs", p_seq),
                            "section_title": "Abstract",
                            "section_path": "Abstract",
                            "section_role_hint": "abstract",
                            "text": txt,
                        })
            break

    body_elem = None
    for elem in root.iter():
        if _local_tag(elem.tag) == "body":
            body_elem = elem
            break

    def _walk_sec(node, sec_stack):
        nonlocal p_seq
        sec_title = None
        sec_type = node.attrib.get("sec-type")
        for ch in node:
            if _local_tag(ch.tag) == "title":
                sec_title = _iter_text(ch)
                break
        new_stack = list(sec_stack)
        if sec_title:
            new_stack.append(sec_title)

        for ch in node:
            clt = _local_tag(ch.tag)
            if clt == "p":
                txt = _iter_text(ch)
                if txt:
                    p_seq += 1
                    paragraphs.append({
                        "paragraph_id": _get_pid(ch, "xml-p", p_seq),
                        "section_title": new_stack[-1] if new_stack else "Main Body",
                        "section_path": " > ".join(new_stack) if new_stack else "Main Body",
                        "section_role_hint": sec_type,
                        "text": txt,
                    })
            elif clt == "sec":
                _walk_sec(ch, new_stack)

    if body_elem is not None:
        _walk_sec(body_elem, [])

    return meta, paragraphs, []


def _compare_twin_xml_to_json(json_meta, json_records, xml_meta, xml_paragraphs, xml_filename):
    """Verify secondary-publisher.xml against secondary-material.json and surface any discrepancy."""
    notes = []
    if xml_meta.get("doi") and json_meta.get("doi") and xml_meta["doi"].lower() != json_meta["doi"].lower():
        notes.append(f"DOI mismatch between JSON ({json_meta['doi']}) and {xml_filename} ({xml_meta['doi']}).")
    elif xml_meta.get("doi") and not json_meta.get("doi"):
        notes.append(f"DOI ({xml_meta['doi']}) recovered from verification twin {xml_filename}.")

    if xml_meta.get("title") and json_meta.get("title"):
        t_j = re.sub(r"\s+", " ", json_meta["title"]).strip().lower()
        t_x = re.sub(r"\s+", " ", xml_meta["title"]).strip().lower()
        if t_j != t_x and t_j not in t_x and t_x not in t_j:
            notes.append(f"Title minor/lexical discrepancy between JSON and {xml_filename}.")

    if xml_meta.get("year") and json_meta.get("year") and str(xml_meta["year"]) != str(json_meta["year"]):
        notes.append(
            f"metadata_discrepancy: Publication year differs between JSON ({json_meta['year']}) and {xml_filename} ({xml_meta['year']})."
        )
    if xml_meta.get("funding") and json_meta.get("funding"):
        fj = re.sub(r"\s+", " ", str(json_meta["funding"])).strip().lower()
        fx = re.sub(r"\s+", " ", str(xml_meta["funding"])).strip().lower()
        if fj != fx:
            notes.append(
                f"metadata_discrepancy: Funding award differs between JSON ({json_meta['funding']}) and {xml_filename} ({xml_meta['funding']})."
            )

    json_by_pid = {r["paragraph_id"]: r["text"] for r in json_records if r.get("paragraph_id")}
    xml_by_pid = {r["paragraph_id"]: r["text"] for r in xml_paragraphs if r.get("paragraph_id")}

    shared_pids = set(json_by_pid) & set(xml_by_pid)
    mismatched_pids = []
    for pid in sorted(shared_pids):
        nj = re.sub(r"\s+", " ", json_by_pid[pid]).strip()
        nx = re.sub(r"\s+", " ", xml_by_pid[pid]).strip()
        if nj != nx:
            mismatched_pids.append(pid)

    if mismatched_pids:
        notes.append(
            f"{len(mismatched_pids)} paragraph(s) differ slightly (e.g. linearized math/markup) between JSON and {xml_filename}: {mismatched_pids[:5]}"
        )
    notes.append(
        f"Verification twin {xml_filename} checked: json_paras={len(json_records)}, xml_paras={len(xml_paragraphs)}, shared_pids={len(shared_pids)}."
    )
    return notes


def _detect_jats_corpus_mode(input_abs, single_file=None):
    """Detect whether input_dir is a Mode A JATS dual-paper corpus bundle."""
    if single_file:
        return False, {}
    files_map = {}
    for _depth, rel_p, fpath, fname in _walk_input_files(input_abs):
        low = fname.lower()
        if low in SKIP_META_FILES:
            continue
        files_map.setdefault(low, []).append((rel_p, fpath, fname))

    has_manifest = "corpus-manifest.json" in files_map
    has_prim = "primary-material.json" in files_map or any(k.endswith("-material.json") for k in files_map)
    has_mat = "material.json" in files_map
    has_xml = any(k.endswith("-publisher.xml") for k in files_map)

    if has_manifest or has_prim or has_mat or has_xml:
        return True, files_map
    return False, files_map


def _ingest_jats_corpus(input_abs, work_abs, files_map, case_data, task_text, scope):
    """Mode A ingestion for JATS dual-paper corpus packages."""
    manifest_paths = [item[1] for item in files_map.get("corpus-manifest.json", [])]
    manifest_entries = _parse_corpus_manifest(manifest_paths[0]) if manifest_paths else []

    # Build role/source lookup from manifest
    entry_by_role = {}
    entry_by_sid = {}
    for idx, ent in enumerate(manifest_entries):
        entry_by_role[ent["role"]] = ent
        entry_by_sid[ent["source_id"]] = ent

    # Ensure primary & secondary slots exist
    ordered_roles = []
    for r in ("primary", "secondary"):
        if r in entry_by_role or f"{r}-material.json" in files_map:
            ordered_roles.append(r)
    for ent in manifest_entries:
        if ent["role"] not in ordered_roles:
            ordered_roles.append(ent["role"])
    if not ordered_roles:
        ordered_roles = ["primary"]

    # Map JSON material files per role
    mat_json_by_role = {}
    for role in ordered_roles:
        cand_name = f"{role}-material.json"
        if cand_name in files_map:
            mat_json_by_role[role] = files_map[cand_name][0]
        else:
            ent = entry_by_role.get(role)
            if ent:
                for mf in ent.get("files", []):
                    bn = os.path.basename(mf).lower()
                    if bn.endswith(".json") and bn in files_map:
                        mat_json_by_role[role] = files_map[bn][0]
                        break

    # Map XML verification twins per role
    xml_by_role = {}
    for low_fn, items in files_map.items():
        if low_fn.endswith(".xml"):
            if "secondary" in low_fn:
                xml_by_role["secondary"] = items[0]
            elif "primary" in low_fn:
                xml_by_role["primary"] = items[0]
            else:
                xml_by_role.setdefault("secondary" if "secondary" in ordered_roles else ordered_roles[0], items[0])

    # Load primary/secondary material JSONs first
    records_by_sid = collections.defaultdict(list)
    embedded_meta_by_sid = {}
    seen_pid_by_sid = collections.defaultdict(set)
    seen_hash_by_sid = collections.defaultdict(set)
    parse_warnings = []

    docs_registry = []
    for idx_r, role in enumerate(ordered_roles):
        ent = entry_by_role.get(role, {})
        sid = ent.get("source_id") or role
        doc_id = f"D{idx_r + 1}"
        rel_p, fpath, fname = mat_json_by_role.get(role, (None, None, None))
        emb_meta = {}
        if fpath:
            emb_meta, recs, errs = _parse_jats_material_json(fpath, default_source_id=sid)
            parse_warnings.extend(errs)
            embedded_meta_by_sid[sid] = emb_meta
            for r in recs:
                r_sid = r.get("source_id") or sid
                pid = r["paragraph_id"]
                th = _sha256_text(r["text"])
                if (pid not in seen_pid_by_sid[r_sid]) and (th not in seen_hash_by_sid[r_sid]):
                    seen_pid_by_sid[r_sid].add(pid)
                    seen_hash_by_sid[r_sid].add(th)
                    records_by_sid[r_sid].append(r)

        docs_registry.append({
            "doc_id": doc_id,
            "source_id": sid,
            "role": role,
            "source_file": fname or f"{role}-material.json",
            "rel_path": (rel_p or f"{role}-material.json").replace("\\", "/"),
            "abs_path": fpath,
            "format": "jats_json",
            "is_verification_twin": False,
            "manifest_meta": ent,
            "embedded_meta": emb_meta,
            "twin_meta": {},
        })

    # Process material.json summary index with strict deduplication
    dedup_skipped = 0
    if "material.json" in files_map:
        _rel_m, mat_path, _fn_m = files_map["material.json"][0]
        _m_meta, mat_recs, errs = _parse_jats_material_json(mat_path, default_source_id=docs_registry[0]["source_id"])
        parse_warnings.extend(errs)
        for r in mat_recs:
            r_sid = r.get("source_id") or docs_registry[0]["source_id"]
            pid = r["paragraph_id"]
            th = _sha256_text(r["text"])
            if (pid in seen_pid_by_sid[r_sid]) or (th in seen_hash_by_sid[r_sid]):
                dedup_skipped += 1
                continue
            seen_pid_by_sid[r_sid].add(pid)
            seen_hash_by_sid[r_sid].add(th)
            records_by_sid[r_sid].append(r)

    # Process XML verification twins (NEVER add as a 3rd independent document)
    verification_twins = []
    twin_notes_all = []
    for role, (rel_x, xml_path, xml_fn) in xml_by_role.items():
        target_doc = next((d for d in docs_registry if d["role"] == role), docs_registry[-1])
        xml_meta, xml_paras, x_errs = _parse_jats_xml(xml_path)
        parse_warnings.extend(x_errs)
        t_sid = target_doc["source_id"]
        json_recs = records_by_sid.get(t_sid, [])
        # If secondary-material.json was absent, populate paragraphs from XML while keeping doc count unique
        if not json_recs and xml_paras:
            for xp in xml_paras:
                xp_copy = dict(xp)
                xp_copy["source_id"] = t_sid
                records_by_sid[t_sid].append(xp_copy)
            json_recs = records_by_sid[t_sid]

        json_combined_meta = {
            **(target_doc.get("embedded_meta") or {}),
            **{k: v for k, v in (target_doc.get("manifest_meta") or {}).items() if v},
        }
        notes = _compare_twin_xml_to_json(json_combined_meta, json_recs, xml_meta, xml_paras, xml_fn)
        twin_notes_all.extend(notes)
        target_doc["twin_meta"] = xml_meta
        target_doc["verification_twin_file"] = xml_fn
        verification_twins.append({
            "twin_file": xml_fn,
            "rel_path": rel_x.replace("\\", "/"),
            "is_verification_twin": True,
            "twin_of_doc_id": target_doc["doc_id"],
            "twin_of_source_id": t_sid,
            "twin_of_role": target_doc["role"],
            "xml_paragraph_count": len(xml_paras),
            "xml_metadata": xml_meta,
            "twin_verification_notes": notes,
        })

    content_limitations_list = [
        "linearized_math_and_subscripts",
        "no_pdf_page_numbers",
        "no_image_pixels_or_supplementary_files",
        "no_raw_clinical_or_experimental_tables_beyond_text",
    ]
    # Convert records into standardized Mode A reading units (page_number=None, page_span=None)
    all_units = []
    documents_meta = []
    for d in docs_registry:
        did = d["doc_id"]
        sid = d["source_id"]
        recs = records_by_sid.get(sid, [])
        if not recs:
            parse_warnings.append(f"missing_declared_material:{d['role']}:{d['source_file']}")
            content_limitations_list.append(f"missing_material_{d['role']} ({d['source_file']})")
        doc_units = []
        for idx_u, r in enumerate(recs, 1):
            txt = r["text"]
            w_cnt, c_cnt, s_cnt = count_words_and_sentences(txt)
            btype = r.get("block_type", "paragraph")
            u_type = btype if btype in ("title", "heading", "abstract", "table_content", "table_caption", "figure_caption", "reference", "footnote") else "paragraph"
            r_policy = "reference_locked" if (u_type == "reference" and not scope.get("include_references")) else "analyze"
            unit_obj = {
                "unit_id": f"{did}-U{idx_u:04d}",
                "doc_id": did,
                "source_id": sid,
                "role": d["role"],
                "source_file": d["source_file"],
                "paragraph_id": r["paragraph_id"],
                "locator_type": "jats_paragraph",
                "unit_type": u_type,
                "raw_block_type": btype,
                "section_title": r.get("section_title", "Main Body"),
                "section_path": r.get("section_path", "Main Body"),
                "section_role_hint": r.get("section_role_hint"),
                "page_number": None,
                "page_span": None,
                "printed_page_span": None,
                "source_text": txt,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": s_cnt,
                "reading_policy": r_policy,
                "extraction_method": "jats_structured",
                "source_warnings": [],
                "uncertainty_flag": False,
                "avg_size": 10.0,
                "is_bold": False,
                "fonts": [],
                "bbox": [0, 0, 0, 0],
                "column": "full",
                "in_scope": True,
                "evidence_anchors": [{"source_id": sid, "paragraph_id": r["paragraph_id"]}],
            }
            doc_units.append(unit_obj)

        sample_txt = " ".join(u["source_text"] for u in doc_units[:12])
        doc_lang = detect_language(sample_txt)
        all_units.extend(doc_units)

        m_meta = d.get("manifest_meta") or {}
        src_hash = m_meta.get("source_hash") or (d.get("embedded_meta") or {}).get("source_hash") or (_sha256_file(d["abs_path"]) if d.get("abs_path") else None)
        documents_meta.append({
            "doc_id": did,
            "source_id": sid,
            "role": d["role"],
            "source_file": d["source_file"],
            "rel_path": d["rel_path"],
            "abs_path": d["abs_path"],
            "format": "jats_json",
            "language": doc_lang,
            "unit_count": len(doc_units),
            "page_count": None,
            "locator_scheme": "jats_paragraph",
            "is_verification_twin": False,
            "verification_twin_file": d.get("verification_twin_file"),
            "source_hash": src_hash,
            "manifest_meta": m_meta,
            "embedded_meta": d.get("embedded_meta") or {},
            "twin_meta": d.get("twin_meta") or {},
            "diagnosis_summary": None,
        })

    return {
        "ingest_mode": "jats_corpus",
        "locator_scheme": "jats_paragraph",
        "documents_meta": documents_meta,
        "all_units": all_units,
        "verification_twins": verification_twins,
        "twin_verification_notes": twin_notes_all,
        "dedup_skipped_from_material_index": dedup_skipped,
        "parse_warnings": parse_warnings,
        "content_limitations": content_limitations_list,
    }


# ----------------------------------------------------------------------
# 5. Mode B: PDF + OCR Fallback, DOCX, LaTeX, HTML, MD/TXT Extractors
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


def _invoke_ocr_hook(image_path, lang):
    """Call case_ingestor.run_ocr_on_image if monkeypatched, else detailed engine-aware OCR."""
    if run_ocr_on_image is not None and run_ocr_on_image is not _ORIG_CASE_RUN_OCR:
        res = run_ocr_on_image(image_path, lang=lang)
        if isinstance(res, tuple) and len(res) >= 3:
            txt = str(res[0] or "").strip()
            return txt, float(res[1] or 0.0), str(res[2] or ("tesseract_cli" if txt else ""))
        if isinstance(res, tuple) and len(res) >= 2:
            txt = str(res[0] or "").strip()
            return txt, float(res[1] or 0.0), ("tesseract_cli" if txt else "")
        return "", 0.0, ""
    if run_ocr_on_image_detailed is not None:
        return run_ocr_on_image_detailed(image_path, lang=lang)
    if run_ocr_on_image is not None:
        txt, conf = run_ocr_on_image(image_path, lang=lang)
        txt_s = str(txt or "").strip()
        return txt_s, float(conf or 0.0), ("tesseract_cli" if txt_s else "")
    return "", 0.0, ""


def attempt_scanned_page_ocr_details(
    pdf_path,
    page_num,
    crops_dir,
    printed_page,
    doc_id="D1",
    rendered_pages_dir=None,
    dpi=220,
):
    """
    Render full PDF page to PNG (pymupdf -> pdftoppm fallback) and attempt OCR
    (chi_sim+eng -> eng). Returns dict with text, confidence, engine, png_path, png_ok.
    """
    out_dir = rendered_pages_dir or crops_dir
    os.makedirs(out_dir, exist_ok=True)
    if rendered_pages_dir:
        out_png = os.path.join(rendered_pages_dir, f"{doc_id}_p{page_num:03d}.png")
    else:
        out_png = os.path.join(crops_dir, f"{doc_id}_ocr_page_{page_num}.png")

    png_ok = False
    if render_page_png is not None:
        png_ok = bool(render_page_png(pdf_path, page_num, out_png, dpi=dpi))
    if not png_ok and render_page_crop_fitz is not None:
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz
            doc = fitz.open(pdf_path)
            rect = doc[page_num - 1].rect
            bbox = [0, 0, float(rect.width), float(rect.height)]
            doc.close()
        except Exception:
            bbox = [0, 0, 595, 842]
        png_ok = bool(render_page_crop_fitz(pdf_path, page_num, bbox, out_png, dpi=dpi))

    if not png_ok:
        return {
            "text": "",
            "confidence": 0.0,
            "engine": "",
            "png_path": os.path.abspath(out_png),
            "png_ok": False,
        }

    txt, conf, engine_name = _invoke_ocr_hook(out_png, lang="chi_sim+eng")
    if not txt:
        txt, conf, engine_name = _invoke_ocr_hook(out_png, lang="eng")

    if not txt or len(txt.strip()) < 15:
        return {
            "text": "",
            "confidence": 0.0,
            "engine": engine_name,
            "png_path": os.path.abspath(out_png),
            "png_ok": True,
        }

    if calculate_garbled_metrics:
        _, garbled_ratio = calculate_garbled_metrics(txt)
        if garbled_ratio > 0.15:
            return {
                "text": "",
                "confidence": 0.0,
                "engine": engine_name,
                "png_path": os.path.abspath(out_png),
                "png_ok": True,
            }

    return {
        "text": txt.strip(),
        "confidence": conf,
        "engine": engine_name or "tesseract_cli",
        "png_path": os.path.abspath(out_png),
        "png_ok": True,
    }


def attempt_scanned_page_ocr(pdf_path, page_num, crops_dir, printed_page, doc_id="D1"):
    """Attempt full-page OCR returning (extracted_text, confidence)."""
    res = attempt_scanned_page_ocr_details(pdf_path, page_num, crops_dir, printed_page, doc_id=doc_id)
    return res["text"], res["confidence"]


def _build_scanned_or_fallback_units_for_page(
    pdf_path,
    page_num,
    p_printed,
    crops_dir,
    rendered_pages_dir,
    doc_id,
    src_name,
    unit_seq_start,
    rendered_pages_map,
    reason="scanned_or_image_only",
):
    """Run full-page render + OCR for one scanned/image page, returning (new_units, next_seq)."""
    ocr_res = attempt_scanned_page_ocr_details(
        pdf_path,
        page_num,
        crops_dir,
        p_printed,
        doc_id=doc_id,
        rendered_pages_dir=rendered_pages_dir,
        dpi=220,
    )
    png_abs = ocr_res["png_path"] if ocr_res["png_ok"] else None
    if ocr_res["png_ok"]:
        rec = rendered_pages_map.setdefault(
            page_num,
            {"doc_id": doc_id, "page": page_num, "png_path": ocr_res["png_path"], "reasons": []},
        )
        if reason not in rec["reasons"]:
            rec["reasons"].append(reason)

    out_units = []
    unit_seq = unit_seq_start
    ocr_text = ocr_res["text"]
    ocr_conf = ocr_res["confidence"]
    ocr_engine = ocr_res["engine"] or "tesseract_cli"

    if ocr_text:
        paras = [para.strip() for para in re.split(r"\n{2,}", ocr_text) if para.strip()]
        for para in paras:
            w_cnt, c_cnt, s_cnt = count_words_and_sentences(para)
            u_id = f"{doc_id}-U{unit_seq:04d}"
            unit_seq += 1
            out_units.append({
                "unit_id": u_id,
                "doc_id": doc_id,
                "source_id": doc_id,
                "source_file": src_name,
                "locator_type": "pdf_page_block",
                "unit_type": "paragraph",
                "raw_block_type": "paragraph",
                "section_path": f"Page {page_num} (OCR Extracted)",
                "page_number": page_num,
                "page_span": [page_num, page_num],
                "printed_page_span": [p_printed, p_printed],
                "source_text": para,
                "word_count": w_cnt,
                "char_count": c_cnt,
                "sentence_count": s_cnt,
                "reading_policy": "analyze",
                "extraction_method": "ocr_tesseract",
                "ocr_engine": ocr_engine,
                "ocr_warning": "OCR-extracted text; verify critical numbers against rendered page image",
                "rendered_page_png": png_abs,
                "source_warnings": ["ocr_extracted"],
                "uncertainty_flag": True,
                "avg_size": 10.0,
                "is_bold": False,
                "fonts": [],
                "bbox": [0, 0, 595, 842],
                "column": "full",
                "evidence_anchors": [{
                    "page": page_num,
                    "printed_page": p_printed,
                    "ocr_confidence": ocr_conf,
                    "ocr_engine": ocr_engine,
                    "rendered_page_png": png_abs,
                }],
            })
    else:
        u_id = f"{doc_id}-U{unit_seq:04d}"
        unit_seq += 1
        img_hint = f" Rendered page PNG saved at: {png_abs}" if png_abs else ""
        out_units.append({
            "unit_id": u_id,
            "doc_id": doc_id,
            "source_id": doc_id,
            "source_file": src_name,
            "locator_type": "pdf_page_block",
            "unit_type": "unextractable_asset",
            "raw_block_type": "unextractable_asset",
            "section_path": f"Page {page_num} (Scanned Image)",
            "page_number": page_num,
            "page_span": [page_num, page_num],
            "printed_page_span": [p_printed, p_printed],
            "source_text": f"[Scanned page image without native text layer; OCR unavailable or unreadable - requires manual visual inspection.{img_hint}]",
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "reading_policy": "manual_review_asset",
            "extraction_method": "ocr_unavailable",
            "rendered_page_png": png_abs,
            "source_warnings": ["native_text_layer_missing", "ocr_unavailable_or_failed"],
            "uncertainty_flag": True,
            "avg_size": 0.0,
            "is_bold": False,
            "fonts": [],
            "bbox": [0, 0, 595, 842],
            "column": "full",
            "evidence_anchors": [{
                "page": page_num,
                "printed_page": p_printed,
                "bbox": [0, 0, 595, 842],
                "rendered_page_png": png_abs,
            }],
        })
    return out_units, unit_seq


def extract_units_from_pdf(pdf_path, work_dir, scope=None, doc_id="D1", rel_path=None):
    """Process PDF into standardized reading units with layout signals, auto PNG rendering, and OCR fallback."""
    units = []
    crops_dir = os.path.join(work_dir, "crops")
    rendered_pages_dir = os.path.join(work_dir, "rendered_pages")
    rendered_pages_map = {}
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

    if not blocks and is_scanned:
        page_count = diagnosis.get("page_count", 1)
        offset = diagnosis.get("printed_page_offset", 0)
        unit_seq = 1
        for p in range(1, page_count + 1):
            p_printed = str(p + offset)
            p_units, unit_seq = _build_scanned_or_fallback_units_for_page(
                pdf_path, p, p_printed, crops_dir, rendered_pages_dir,
                doc_id, src_name, unit_seq, rendered_pages_map, reason="scanned_or_image_only",
            )
            units.extend(p_units)
        diagnosis["rendered_pages"] = sorted(rendered_pages_map.values(), key=lambda x: x["page"])
        return units, diagnosis, struct_doc

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
        elif b_type == "metadata":
            u_type = "metadata"
            r_policy = "preserve_source"
        elif b_type in ("heading_h1", "heading_h2", "heading_h3"):
            u_type = "heading"
        elif b_type == "abstract":
            u_type = "abstract"
        elif b_type == "figure_caption":
            u_type = "figure_caption"
        elif b_type == "table_caption":
            u_type = "table_caption"
        elif b_type == "table_content":
            u_type = "table_content"
        elif b_type == "formula":
            u_type = "display_equation"
            r_policy = "preserve_source"
        elif b_type == "reference_entry":
            u_type = "reference"
            r_policy = "analyze" if include_refs else "reference_locked"
        elif b_type == "footnote":
            u_type = "footnote"

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
            "source_id": doc_id,
            "source_file": src_name,
            "locator_type": "pdf_page_block",
            "unit_type": u_type,
            "raw_block_type": b_type,
            "section_path": sec_path,
            "page_number": p_span[0],
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

    offset = diagnosis.get("printed_page_offset", 0)
    pages_with_native = {
        p
        for u in units
        if u.get("unit_type") != "unextractable_asset" and u.get("page_span")
        for p in range(u["page_span"][0], u["page_span"][-1] + 1)
    }
    units_by_page = collections.defaultdict(list)
    for u in units:
        if u.get("page_span"):
            units_by_page[u["page_span"][0]].append(u)

    added_hybrid = False
    for dp in diagnosis.get("pages", []):
        p_num = dp.get("page_num")
        if not p_num:
            continue
        route = dp.get("recommended_route", "")
        if route == "scanned_or_image_only" and p_num not in pages_with_native:
            p_printed = str(p_num + offset)
            p_units, unit_seq = _build_scanned_or_fallback_units_for_page(
                pdf_path, p_num, p_printed, crops_dir, rendered_pages_dir,
                doc_id, src_name, unit_seq, rendered_pages_map, reason="scanned_or_image_only",
            )
            units.extend(p_units)
            units_by_page[p_num].extend(p_units)
            added_hybrid = True
            continue

        page_units = units_by_page.get(p_num, [])
        reasons = []
        if route in ("scanned_or_image_only", "ocr_recommended"):
            reasons.append(route)
        if dp.get("garbled_ratio", 0.0) >= 0.05 or any("potential_font_garble" in u.get("source_warnings", []) for u in page_units):
            reasons.append("font_garble_or_cid")
        if any(u.get("unit_type") == "unextractable_asset" for u in page_units):
            reasons.append("unextractable_asset")
        has_fig_or_tab_cap = any(u.get("unit_type") in ("figure_caption", "table_caption") for u in page_units)
        has_tab_body = any(u.get("unit_type") == "table_content" for u in page_units)
        if (
            (dp.get("image_coverage", 0.0) >= 0.25 and dp.get("char_count", 0) < 200)
            or (has_fig_or_tab_cap and not has_tab_body and dp.get("image_coverage", 0.0) >= 0.15)
        ):
            reasons.append("unextracted_visual_asset")

        if reasons and render_page_png is not None:
            out_png = os.path.join(rendered_pages_dir, f"{doc_id}_p{p_num:03d}.png")
            png_ok = os.path.exists(out_png) or bool(render_page_png(pdf_path, p_num, out_png, dpi=220))
            if png_ok:
                abs_png = os.path.abspath(out_png)
                rec = rendered_pages_map.setdefault(
                    p_num,
                    {"doc_id": doc_id, "page": p_num, "png_path": abs_png, "reasons": []},
                )
                for r in reasons:
                    if r not in rec["reasons"]:
                        rec["reasons"].append(r)
                for u in page_units:
                    if not u.get("rendered_page_png"):
                        u["rendered_page_png"] = abs_png

    if added_hybrid:
        units.sort(key=lambda u: ((u["page_span"][0] if u.get("page_span") else 1), 0 if u.get("unit_type") != "unextractable_asset" else 1))

    if not units:
        p_cnt = max(1, diagnosis.get("page_count", 1))
        units.append({
            "unit_id": f"{doc_id}-U0001",
            "doc_id": doc_id,
            "source_id": doc_id,
            "source_file": src_name,
            "locator_type": "pdf_page_block",
            "unit_type": "unextractable_asset",
            "raw_block_type": "unextractable_asset",
            "section_path": "Unextractable PDF Asset",
            "page_number": 1,
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

    diagnosis["rendered_pages"] = sorted(rendered_pages_map.values(), key=lambda x: x["page"])
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
            "source_id": doc_id,
            "source_file": src_name,
            "locator_type": "text_block",
            "unit_type": u_type,
            "raw_block_type": u_type,
            "section_path": sec_tracker.get_path(),
            "page_number": 1,
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
                    "source_id": doc_id,
                    "source_file": src_name,
                    "locator_type": "text_block",
                    "unit_type": "code_block",
                    "raw_block_type": "code_block",
                    "section_path": sec_tracker.get_path(),
                    "page_number": 1,
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
                "source_id": doc_id,
                "source_file": src_name,
                "locator_type": "text_block",
                "unit_type": "display_equation",
                "raw_block_type": "formula",
                "section_path": sec_tracker.get_path(),
                "page_number": 1,
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
                "source_id": doc_id,
                "source_file": src_name,
                "locator_type": "text_block",
                "unit_type": u_type,
                "raw_block_type": f"heading_h{min(3, h_level)}",
                "section_path": "Title" if u_type == "title" else sec_tracker.get_path(),
                "page_number": 1,
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
                    "source_id": doc_id,
                    "source_file": src_name,
                    "locator_type": "text_block",
                    "unit_type": u_type,
                    "raw_block_type": raw_bt,
                    "section_path": "Title" if u_type == "title" else sec_tracker.get_path(),
                    "page_number": 1,
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
            "source_id": doc_id,
            "source_file": src_name,
            "locator_type": "text_block",
            "unit_type": "unextractable_asset",
            "raw_block_type": "unextractable_asset",
            "section_path": "DOCX Parser Error",
            "page_number": 1,
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
    """Extract balanced {...} argument starting at brace_open_idx."""
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
            "source_id": doc_id,
            "source_file": src_name,
            "locator_type": "text_block",
            "unit_type": u_type,
            "raw_block_type": raw_bt,
            "section_path": sec_path,
            "page_number": 1,
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
                    add_unit("abstract", "abstract", "Abstract", re.sub(r"\s+", " ", inner), "analyze")
                elif mode_done == "math":
                    add_unit("display_equation", "formula", sec_tracker.get_path(), block_txt, "preserve_source")
                elif mode_done == "code":
                    add_unit("code_block", "code_block", sec_tracker.get_path(), block_txt, "preserve_source")
                elif mode_done == "table":
                    add_unit("table_content", "table_content", sec_tracker.get_path(), block_txt, "analyze")
                elif mode_done == "bib":
                    sec_tracker.push_heading("References", level=1)
                    inner = re.sub(r"\\begin\{thebibliography\}(?:\{[^}]*\})?|\\end\{thebibliography\}", "", block_txt).strip()
                    for bc in re.split(r"(?=\\bibitem)", inner):
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
                env_mode, env_end_pat, env_buf = "bib", re.compile(r"\\end\{thebibliography\}"), [line]
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
            u_type, raw_bt, sec_path, is_bold, avg_size = "title", "title", "Title", True, 16.0
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lvl = int(tag[1])
            sec_tracker.push_heading(text, level=lvl)
            u_type, raw_bt, sec_path, is_bold = "heading", f"heading_h{min(3, lvl)}", sec_tracker.get_path(), True
            avg_size = 14.0 if lvl == 1 else (12.0 if lvl == 2 else 11.0)
        elif tag == "pre":
            u_type, raw_bt, policy, sec_path = "code_block", "code_block", "preserve_source", sec_tracker.get_path()
        elif tag == "table":
            u_type, raw_bt, sec_path = "table_content", "table_content", sec_tracker.get_path()
        elif tag == "figcaption":
            u_type, raw_bt, sec_path = "figure_caption", "figure_caption", sec_tracker.get_path()
        elif tag == "caption":
            u_type, raw_bt, sec_path = "table_caption", "table_caption", sec_tracker.get_path()
        else:
            sec_path = sec_tracker.get_path()

        w_cnt, c_cnt, s_cnt = count_words_and_sentences(text)
        u_id = f"{doc_id}-U{unit_seq:04d}"
        unit_seq += 1
        units.append({
            "unit_id": u_id,
            "doc_id": doc_id,
            "source_id": doc_id,
            "source_file": src_name,
            "locator_type": "text_block",
            "unit_type": u_type,
            "raw_block_type": raw_bt,
            "section_path": sec_path,
            "page_number": 1,
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
                "source_id": doc_id,
                "source_file": src_name,
                "locator_type": "text_block",
                "unit_type": "paragraph",
                "raw_block_type": "paragraph",
                "section_path": "Main",
                "page_number": 1,
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
# 6. File Discovery, Questions & Orchestrator
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
    return "literature_kg_case"


def _extract_pdf_doc_metadata(pdf_path):
    """Safely extract PDF document and XMP metadata via pymupdf without triggering fitz warnings."""
    meta_out = {}
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open(pdf_path)
        raw_meta = doc.metadata or {}
        for k, v in raw_meta.items():
            if v is not None and str(v).strip():
                meta_out[str(k)] = str(v).strip()
        try:
            xmp = doc.get_xml_metadata()
            if xmp and str(xmp).strip():
                meta_out["xmp_xml"] = str(xmp).strip()[:8000]
        except Exception:
            pass
        doc.close()
    except Exception:
        pass
    return meta_out


def extract_root_case_metadata(case_data):
    """
    Isolate top-level case.json doi/title/authors/year/venue/license so they ONLY apply
    to the primary paper (and NEVER leak onto secondary papers).
    """
    if not isinstance(case_data, dict):
        return {"applies_to_role": "primary_only"}
    return {
        "title": (case_data.get("title") or "").strip() or None,
        "doi": (case_data.get("doi") or "").strip() or None,
        "authors": _coerce_authors_list(case_data.get("authors")),
        "year": int(case_data["year"]) if (case_data.get("year") is not None and str(case_data.get("year")).isdigit()) else None,
        "venue": (case_data.get("venue") or case_data.get("journal") or "").strip() or None,
        "license": (case_data.get("license") or "").strip() or None,
        "applies_to_role": "primary_only",
    }


def extract_research_questions(case_data, task_md_text):
    """
    Deterministically extract explicit research questions / queries / prompts
    while filtering out delivery/workflow instruction noise.
    """
    questions = []
    seen_norm = set()
    used_ids = set()

    instruction_noise_re = re.compile(
        r"(?:完整阅读|严格按照|写入\s*Output|输出(?:必须|应)?包含|禁止修改|不得修改|禁止推测|不得推测|"
        r"交付物|同步导出|中间(?:过程)?文件|must\s+include|do\s+not\s+modify|forbidden\s+to\s+modify|"
        r"deliverables|output_contract|SKILL\.md|work_state\.json|result\.json|kg\.jsonld)",
        re.I,
    )
    q_section_heading_re = re.compile(
        r"^#{1,6}\s+(?:[\d.]+\s*)?(?:research\s+questions?|questions?|queries|待回答问题|待答问题|科研问题|问题清单|核心问题)\s*[:：]?\s*$",
        re.I,
    )

    def _add_q(q_text, src, q_id=None):
        txt = re.sub(r"\s+", " ", (q_text or "").strip())
        if not txt or len(txt) < 4:
            return
        norm = txt.lower().rstrip("?？.。")
        if norm in seen_norm:
            return
        seen_norm.add(norm)
        cand_id = str(q_id).strip() if q_id else f"Q{len(questions) + 1}"
        while cand_id in used_ids:
            cand_id = f"Q{len(questions) + 1}"
            if cand_id in used_ids:
                cand_id = f"{cand_id}_{len(questions) + 1}"
        used_ids.add(cand_id)
        questions.append({"question_id": cand_id, "question": txt, "source": src})

    if isinstance(case_data, dict):
        for key in ("research_questions", "questions", "queries", "qa_tasks", "prompts", "sub_questions", "qa_questions", "tasks"):
            val = case_data.get(key)
            if isinstance(val, str) and val.strip():
                _add_q(val, f"case_json:{key}")
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        _add_q(item, f"case_json:{key}")
                    elif isinstance(item, dict):
                        qt = item.get("question") or item.get("query") or item.get("prompt") or item.get("text") or item.get("title")
                        qid = item.get("question_id") or item.get("id") or item.get("qid")
                        if qt:
                            _add_q(str(qt), f"case_json:{key}", q_id=qid)
        for key in ("question", "query", "prompt"):
            val = case_data.get(key)
            if isinstance(val, str) and val.strip():
                _add_q(val, f"case_json:{key}")

    has_explicit_case_questions = len(questions) > 0

    if task_md_text:
        in_q_section = False
        for raw_line in task_md_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if re.match(r"^#{1,6}\s+", line):
                in_q_section = bool(q_section_heading_re.match(line))
                continue

            m_qprefix = re.match(r"^(?:[-*]\s*)?(?:\*\*)?(Q\d+|问题\s*\d+)\s*[:：.\)]\s*(?:\*\*)?\s*(.+)$", line, re.I)
            if m_qprefix:
                qid_raw = re.sub(r"\s+", "", m_qprefix.group(1))
                q_body = m_qprefix.group(2).strip()
                is_interrogative = q_body.endswith(("?", "？"))
                if has_explicit_case_questions:
                    has_colon = bool(re.match(r"^(?:[-*]\s*)?(?:\*\*)?(?:Q\d+|问题\s*\d+)\s*[:：]", line, re.I))
                    if has_colon and is_interrogative and len(q_body) >= 6:
                        _add_q(q_body, "task_md:explicit_q", q_id=qid_raw.upper() if qid_raw.lower().startswith("q") else None)
                else:
                    if len(q_body) >= 6 and (is_interrogative or not instruction_noise_re.search(q_body)):
                        _add_q(q_body, "task_md:explicit_q", q_id=qid_raw.upper() if qid_raw.lower().startswith("q") else None)
                continue

            if has_explicit_case_questions:
                continue

            m_item = re.match(r"^(?:[-*]|\d+[.)、])\s+(.+)$", line)
            cand = m_item.group(1).strip() if m_item else line
            if cand.startswith(("|", ">", "#")) or len(cand) < 8:
                continue
            is_interrogative = cand.endswith(("?", "？"))
            is_noise = bool(instruction_noise_re.search(cand))
            if is_interrogative:
                if not re.search(r"(?:评分|score|runtime|budget|harbor)", cand, re.I) and (not is_noise or len(cand) >= 15):
                    _add_q(cand, "task_md:interrogative")
            elif in_q_section and m_item and not is_noise:
                _add_q(cand, "task_md:question_section")

    return questions


def extract_target_formats(case_data, task_md_text):
    """Detect requested output formats (json, json-ld, graphml, csv, markdown)."""
    combined = (task_md_text or "") + "\n" + (json.dumps(case_data, ensure_ascii=False) if case_data else "")
    fmt_rules = [
        ("json", r"\bresult\.json\b|\bjson\b"),
        ("json-ld", r"\bjson-?ld\b|\b\.jsonld\b"),
        ("graphml", r"\bgraphml\b|\b\.graphml\b"),
        ("csv", r"\bcsv\b|\b\.csv\b"),
        ("markdown", r"\bmarkdown\b|\b\.md\b"),
    ]
    detected = []
    for name, pat in fmt_rules:
        if re.search(pat, combined, re.I):
            detected.append(name)
    if "json" not in detected:
        detected.insert(0, "json")
    return detected


def extract_task_guidance(task_text, case_data=None):
    """Detect expected output language and KG focus directives."""
    combined = (task_text or "") + "\n" + (json.dumps(case_data, ensure_ascii=False) if case_data else "")
    expected_lang = "auto"
    if re.search(r"(?:用中文|中文输出|简体中文|in\s+Chinese|output\s+in\s+Chinese)", combined, re.I):
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
        ("entity_recognition", r"(?:实体识别|entity\s+recognition|entities)"),
        ("entity_alignment", r"(?:实体对齐|别名|缩写|disambiguation|alignment)"),
        ("relation_extraction", r"(?:关系抽取|relation\s+extraction|edges)"),
        ("provenance_binding", r"(?:来源绑定|原文片段|paragraph_id|source_id|provenance)"),
        ("graph_query", r"(?:图谱查询|query|queries|子图)"),
        ("incremental_update", r"(?:增量更新|incremental|merge|conflict)"),
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
    Dual-mode scientific ingestion pipeline:
      - Mode A (priority): JATS dual-paper structured corpus (`corpus-manifest.json`, `primary-material.json`, `secondary-material.json`, `secondary-publisher.xml`, `material.json`)
      - Mode B (fallback): PDF / DOCX / LaTeX / HTML / MD / TXT
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
    root_case_metadata = extract_root_case_metadata(case_data)
    scope = extract_scope_constraints(task_text, case_data)
    task_guidance = extract_task_guidance(task_text, case_data)
    research_questions = extract_research_questions(case_data, task_text)
    target_formats = extract_target_formats(case_data, task_text)

    is_jats_mode, files_map = _detect_jats_corpus_mode(input_abs, single_file=single_file)

    verification_twins = []
    twin_notes = []
    dedup_skipped = 0
    parse_warnings = []
    content_limitations = []

    if is_jats_mode:
        jats_res = _ingest_jats_corpus(input_abs, work_abs, files_map, case_data, task_text, scope)
        ingest_mode = jats_res["ingest_mode"]
        locator_scheme = jats_res["locator_scheme"]
        documents_meta = jats_res["documents_meta"]
        all_units = jats_res["all_units"]
        verification_twins = jats_res["verification_twins"]
        twin_notes = jats_res["twin_verification_notes"]
        dedup_skipped = jats_res["dedup_skipped_from_material_index"]
        parse_warnings = jats_res["parse_warnings"]
        content_limitations = jats_res["content_limitations"]
        primary_file = documents_meta[0]["abs_path"] if documents_meta else None
        case_id = _derive_semantic_case_id(case_data, task_text, primary_file, input_abs)
    else:
        exclude_dirs = [work_abs]
        target_files = []
        if single_file and os.path.exists(single_file):
            abs_sf = os.path.abspath(single_file)
            target_files = [(abs_sf, os.path.basename(abs_sf))]
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
        has_pdf = False
        for idx, (fpath, rel_p) in enumerate(target_files, start=1):
            doc_id = f"D{idx}"
            role = "primary" if idx == 1 else ("secondary" if idx == 2 else f"doc_{idx}")
            ext = os.path.splitext(fpath)[1].lower()
            doc_units = []
            diag_summary = None
            doc_format = "unknown"

            if ext == ".pdf":
                has_pdf = True
                doc_format = "pdf"
                doc_units, diagnosis, _struct_doc = extract_units_from_pdf(
                    fpath, work_abs, scope=scope, doc_id=doc_id, rel_path=rel_p
                )
                pdf_meta = _extract_pdf_doc_metadata(fpath)
                diag_summary = {
                    "dominant_route": diagnosis.get("dominant_route", "unknown"),
                    "page_count": diagnosis.get("page_count", 1),
                    "printed_page_offset": diagnosis.get("printed_page_offset", 0),
                    "pages": diagnosis.get("pages", []),
                    "pdf_metadata": pdf_meta,
                    "rendered_pages": diagnosis.get("rendered_pages", []),
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
                u["role"] = role
                u["in_scope"] = is_unit_in_scope(u, scope)
                if not u["in_scope"]:
                    u["reading_policy"] = "scope_excluded"

            sample_txt = " ".join(u.get("source_text", "") for u in doc_units[:12])
            doc_lang = detect_language(sample_txt)
            all_units.extend(doc_units)

            documents_meta.append({
                "doc_id": doc_id,
                "source_id": doc_id,
                "role": role,
                "source_file": os.path.basename(fpath),
                "rel_path": rel_p.replace("\\", "/"),
                "abs_path": fpath,
                "format": doc_format,
                "language": doc_lang,
                "unit_count": len(doc_units),
                "page_count": diag_summary["page_count"] if diag_summary else 1,
                "locator_scheme": "pdf_page_block" if ext == ".pdf" else "text_block",
                "is_verification_twin": False,
                "source_hash": _sha256_file(fpath),
                "diagnosis_summary": diag_summary,
            })

        ingest_mode = "document_files"
        locator_scheme = "pdf_page_block" if has_pdf else "text_block"

        if not all_units:
            warn_reason = "no_supported_source_files_found" if not target_files else "zero_extractable_units"
            all_units.append({
                "unit_id": "D1-U0001",
                "doc_id": "D1",
                "source_id": "D1",
                "role": "primary",
                "source_file": os.path.basename(primary_file) if primary_file else "none",
                "locator_type": locator_scheme,
                "unit_type": "unextractable_asset",
                "raw_block_type": "unextractable_asset",
                "section_path": "Ingestion Fallback",
                "page_number": 1,
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
    readable_paper_count = sum(
        1 for d in documents_meta
        if d.get("unit_count", 0) > 0
        and any(u.get("unit_type") != "unextractable_asset" for u in all_units if u.get("doc_id") == d["doc_id"])
    )

    source_units_path = os.path.join(work_abs, "source_units.json")
    with open(source_units_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "case_id": case_id,
            "ingest_mode": ingest_mode,
            "locator_scheme": locator_scheme,
            "independent_paper_count": readable_paper_count,
            "doc_count": len(documents_meta),
            "documents": documents_meta,
            "verification_twins": verification_twins,
            "twin_verification_notes": twin_notes,
            "content_limitations": content_limitations,
            "overall_language": overall_lang,
            "total_units": len(all_units),
            "units": all_units,
        }, f, ensure_ascii=False, indent=2)

    type_counts = collections.Counter(u.get("unit_type") for u in all_units)
    policy_counts = collections.Counter(u.get("reading_policy") for u in all_units)

    manifest = {
        "status": "success" if all_units and readable_paper_count > 0 and not any(w.startswith("missing_declared_material:") for w in parse_warnings) else "partial",
        "case_id": case_id,
        "input_dir": input_abs,
        "ingest_mode": ingest_mode,
        "locator_scheme": locator_scheme,
        "independent_paper_count": readable_paper_count,
        "doc_count": len(documents_meta),
        "is_multi_document": readable_paper_count > 1,
        "documents": [
            {
                "doc_id": d["doc_id"],
                "source_id": d["source_id"],
                "role": d["role"],
                "source_file": d["source_file"],
                "rel_path": d["rel_path"],
                "format": d["format"],
                "language": d["language"],
                "unit_count": d["unit_count"],
                "page_count": d["page_count"],
                "locator_scheme": d["locator_scheme"],
                "is_verification_twin": False,
                "verification_twin_file": d.get("verification_twin_file"),
                "source_hash": d.get("source_hash"),
                "dominant_route": d["diagnosis_summary"]["dominant_route"] if d["diagnosis_summary"] else "jats_structured",
            }
            for d in documents_meta
        ],
        "verification_twins": verification_twins,
        "twin_verification_notes": twin_notes,
        "jats_summary": {
            "dedup_skipped_from_material_index": dedup_skipped,
            "parse_warnings": parse_warnings,
        } if is_jats_mode else None,
        "content_limitations": content_limitations,
        "overall_language": overall_lang,
        "scope_constraint": scope,
        "total_units": len(all_units),
        "unit_type_breakdown": dict(type_counts),
        "reading_policy_breakdown": dict(policy_counts),
        "unextractable_asset_count": type_counts.get("unextractable_asset", 0),
        "total_warnings_count": sum(len(u.get("source_warnings", [])) for u in all_units),
        "root_case_metadata": root_case_metadata,
        "case_metadata": {k: v for k, v in case_data.items() if k != "output_contract"},
        "task_md_present": bool(task_text),
        "task_guidance": task_guidance,
        "research_questions": research_questions,
        "target_formats": target_formats,
        "output_contract": output_contract,
    }

    for fname in ("ingest_manifest.json", "case_manifest.json"):
        with open(os.path.join(work_abs, fname), "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(
        f"[case_ingestor] mode={ingest_mode} | locator={locator_scheme} | case_id={case_id} "
        f"| independent_docs={len(documents_meta)} | twins={len(verification_twins)} | units={len(all_units)} -> {work_abs}"
    )
    return manifest


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Dual-Mode Scientific Material Ingestion Engine")
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
