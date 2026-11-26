#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
structure_indexer.py - Deterministic Document Skeleton, Bilingual Section Tree & Evidence Anchor Engine
Part of the Scientific Literature Notes Skill (task_06_notes).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import math
import time
import glob
import argparse
import collections

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from case_ingestor import ingest_case, detect_language, count_words_and_sentences, infer_heading_level
except ImportError:
    from .case_ingestor import ingest_case, detect_language, count_words_and_sentences, infer_heading_level


# ----------------------------------------------------------------------
# 1. Bilingual Academic Section Dictionary & Role Normalizer
# ----------------------------------------------------------------------

SECTION_ROLE_PATTERNS = [
    ("toc", re.compile(r"^(?:contents|table\s+of\s+contents|目\s*录)\s*[:：]?$", re.I)),
    ("abstract", re.compile(r"^(?:abstract|摘\s*要)\b", re.I)),
    ("references", re.compile(r"^(?:references|bibliography|works\s+cited|参\s*考\s*文\s*献)\b", re.I)),
    ("appendix", re.compile(r"^(?:appendix|appendices|supplementary(?:\s+(?:material|information|note|data))?|extended\s+data|附\s*录|补充材料)\b", re.I)),
    ("acknowledgments", re.compile(r"^(?:acknowledg(?:e)?ments?|funding|致\s*谢|基金资助)\b", re.I)),
    ("limitations", re.compile(r"(?:\blimitations?\b|\bthreats?\s+to\s+validity\b|\bethical\s+considerations?\b|\bbroader\s+impacts?\b|局限性|研究局限|不足与展望)", re.I)),
    ("conclusion", re.compile(r"(?:\bconclusions?\b|\bconcluding\s+remarks\b|\bsummary\s+and\s+outlook\b|结论|结语|展望|总结与展望|结论与展望)", re.I)),
    ("discussion", re.compile(r"(?:\bdiscussion\b|\bgeneral\s+discussion\b|讨论|结果讨论)", re.I)),
    ("results", re.compile(r"(?:\bexperimental\s+results?\b|\bempirical\s+results?\b|\bresults?\s+and\s+(?:analysis|discussion)\b|\bresults?\b|\bfindings?\b|\bmain\s+results?\b|实验结果|结果与分析|实验分析|结果|主要发现)", re.I)),
    ("ablation", re.compile(r"(?:\bablation\b|消融实验|消融分析)", re.I)),
    ("experiments", re.compile(r"(?:\bexperiments?\b|\bexperimental\s+(?:setup|evaluation|design)\b|\bevaluation\b|\bempirical\s+study\b|实验|实验设计|实验设置|仿真|消融|性能测试|性能评估|实验评估)", re.I)),
    ("method", re.compile(r"(?:\bmethod(?:s|ology)?\b|\bapproach\b|\bframework\b|\barchitecture\b|\bmodel\b|\balgorithm\b|\bproposed\s+method\b|\bsystem\s+design\b|\bthreat\s+model\b|\bmaterials?\s+and\s+methods?\b|方法|方法论|研究方法|系统模型|理论推导|问题建模|算法设计|模型构建|模型设计|架构设计|系统架构|算法)", re.I)),
    ("related_work", re.compile(r"(?:\brelated\s+work\b|\bliterature\s+review\b|\bprior\s+work\b|相关工作|文献综述)", re.I)),
    ("background", re.compile(r"(?:\bbackground\b|\bpreliminaries\b|\bproblem\s+(?:formulation|statement|definition)\b|背景|预备知识|问题定义)", re.I)),
    ("introduction", re.compile(r"(?:\bintroduction\b|\boverview\b|引言|绪论|概述)", re.I)),
]

TOC_ENTRY_TAIL_REGEX = re.compile(r"(?:(?:\.\s*){2,}\d{1,4}|\s+\d{1,4})\s*$")
FORMULA_PUA_REGEX = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd]")

STANDALONE_BILINGUAL_HEADINGS = {
    "abstract", "introduction", "related work", "preliminaries", "background",
    "methodology", "methods", "method", "approach", "experiments", "evaluation",
    "results", "ablation", "ablation study", "discussion", "limitations",
    "conclusion", "conclusions", "acknowledgment", "acknowledgments",
    "references", "bibliography", "appendix", "appendices", "broader impact",
    "ethical considerations", "threats to validity", "future work",
    "摘要", "引言", "绪论", "相关工作", "文献综述", "背景", "预备知识",
    "研究方法", "方法", "系统模型", "实验", "实验结果", "消融实验",
    "结果", "讨论", "局限性", "结论", "结语", "致谢", "参考文献", "附录",
}

NUMBERED_HEADING_REGEX = re.compile(
    r"^(?:"
    r"(?:\d+(?:\.\d+)*\.?\s+[A-Z\u4e00-\u9fa5][^\n]{1,90})"
    r"|(?:[IVXLCDM]+\.\s+[A-Z][^\n]{1,90})"
    r"|(?:[A-Z]\.\s+[A-Z][^\n]{1,90})"
    r"|(?:[一二三四五六七八九十]+[、\.]\s*[\u4e00-\u9fa5A-Za-z][^\n]{1,70})"
    r"|(?:（[一二三四五六七八九十]+）\s*[\u4e00-\u9fa5A-Za-z][^\n]{1,70})"
    r"|(?:Appendix\s+[A-Z0-9]+[:.\s]+[^\n]{1,90})"
    r")$"
)

LIMITATION_CUE_PATTERNS = [
    ("limitation", re.compile(r"\blimitations?\b|局限性?|研究局限|受限于", re.I)),
    ("assumption", re.compile(r"\b(?:assume\s+that|under\s+the\s+assumption|key\s+assumptions?|assuming\s+that)\b|假设|前提条件", re.I)),
    ("weakness_caveat", re.compile(r"\b(?:drawbacks?|shortcomings?|weakness(?:es)?|caveats?|threats?\s+to\s+validity)\b|不足之处|缺陷|有效性威胁", re.I)),
    ("future_work", re.compile(r"\b(?:future\s+work|in\s+(?:the\s+)?future|remain(?:s)?\s+open|open\s+questions?)\b|未来工作|下一步|尚待|仍有待", re.I)),
    ("failure_boundary", re.compile(r"\b(?:fails?\s+to|cannot\s+handle|does\s+not\s+generalize|only\s+validated\s+on|restricted\s+to)\b|未能|无法处理|仅适用于", re.I)),
]

DOI_REGEX = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_REGEX = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
CODE_URL_REGEX = re.compile(r"https?://(?:github\.com|gitlab\.com|huggingface\.co|zenodo\.org|osf\.io)/[^\s\)\],;>\"']+", re.I)
YEAR_REGEX = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")

FIG_CAPTION_PREFIX = re.compile(r"^(?:Fig(?:ure)?s?\.?|图)\s*([A-Za-z]?\d+(?:\.\d+)*)", re.I)
TAB_CAPTION_PREFIX = re.compile(r"^(?:Tables?|Tabs?\.?|表|续表)\s*([A-Za-z]?\d+(?:\.\d+)*)", re.I)
EQ_NUM_REGEX = re.compile(r"(?:\(([A-Za-z]?\d+(?:\.\d+)*)\)|\[([A-Za-z]?\d+(?:\.\d+)*)\]|Eq\.?\s*\(?([A-Za-z]?\d+(?:\.\d+)*)\)?)\s*$")


AUTHOR_NOISE_PATTERNS = [
    re.compile(r"^arXiv:\s*\d{4}\.", re.I),
    re.compile(r"^(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}$", re.I),
    re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"),
    re.compile(r"(?:@|https?://|www\.)", re.I),
    re.compile(r"\b(?:University|Institute|Department|Dept\.?|Laboratory|Lab\b|College|Academy|School\s+of|Center\s+for|Centre\s+for|Preprint|Submitted\s+to|Published\s+in|Keywords|Index\s+Terms)\b", re.I),
    re.compile(r"(?:大学|学院|研究所|实验室|研究院|关键词|通信作者|收稿日期|基金项目)"),
]


def _clean_author_clues(raw_clues):
    """Filter out arXiv stamps, dates, emails, institutions, and strip author superscripts."""
    cleaned_names = []
    cleaned_clues = []
    seen = set()

    for clue in raw_clues or []:
        aid = clue.get("anchor_id", "")
        raw_txt = clue.get("text", "")
        kept_lines = []
        for ln in raw_txt.splitlines():
            s = ln.strip()
            if not s or len(s) > 180:
                continue
            if any(pat.search(s) for pat in AUTHOR_NOISE_PATTERNS):
                continue
            # Reject prose sentences with >=15 words ending with period
            if s.endswith((".", "。")) and len(s.split()) >= 12:
                continue

            # Clean LaTeX/PDF superscript markers attached to author names while preserving separator commas
            s_clean = re.sub(r"[\^_]\{[^}]*\}", "", s)
            s_clean = re.sub(r"([\*∗†‡§¶\d])\s*(and\b)", r" \2", s_clean)
            s_clean = re.sub(r"(?<=[A-Za-z\u4e00-\u9fa5])(?:\d+(?:,\d+)*|[\*∗†‡§¶]+)+", "", s_clean)
            s_clean = re.sub(r"[\*∗†‡§¶]+", "", s_clean)
            s_clean = re.sub(r"\s*,\s*,+", ",", s_clean)
            s_clean = re.sub(r"\s{2,}", " ", s_clean).strip(" ,;.")
            if not s_clean or len(s_clean) < 2:
                continue
            kept_lines.append(s_clean)

            # Split into individual names
            parts = re.split(r"\s*(?:,|\band\b|&|、)\s*", s_clean)
            for p in parts:
                name = p.strip(" ,;.")
                if name and len(name) >= 2 and not re.match(r"^\d+$", name):
                    if name not in seen:
                        seen.add(name)
                        cleaned_names.append(name)

        if kept_lines:
            cleaned_clues.append({"anchor_id": aid, "text": "; ".join(kept_lines)})

    return cleaned_names, cleaned_clues


ALLOWED_SECTION_ROLES = (
    "title_meta",
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "appendix",
    "references",
    "body_unclassified",
)


def normalize_section_role(raw_role, unit_type=None, zone=None):
    """Map fine-grained role/zone into the strict 10-value Nanopub section_role enum."""
    if unit_type in ("title", "metadata") or zone == "front_matter" or raw_role in ("front_matter", "title_meta", "toc", "acknowledgments"):
        return "title_meta"
    if unit_type == "abstract" or raw_role == "abstract":
        return "abstract"
    if unit_type == "reference" or zone == "references_locked" or raw_role == "references":
        return "references"
    if zone == "appendix" or raw_role == "appendix":
        return "appendix"
    if raw_role in ("introduction", "related_work", "background"):
        return "introduction"
    if raw_role in ("method", "methods", "experiments", "ablation"):
        return "methods"
    if raw_role == "results":
        return "results"
    if raw_role in ("discussion", "limitations"):
        return "discussion"
    if raw_role == "conclusion":
        return "conclusion"
    return "body_unclassified"


CITATION_MARKER_REGEX = re.compile(
    r"(?:"
    r"\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\]"
    r"|\\(?:cite[tp]?|Cite[tp]?|textcite|parencite|autocite)\*?(?:\[[^\]]*\])?\{[^}]+\}"
    r"|\([A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?(?:;\s*[A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?)*\)"
    r")"
)

QUANT_SIGNAL_REGEX = re.compile(
    r"(?:"
    r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|‰|°[CF]?|mg/kg|mg/mL|μg/mL|ug/mL|mM|μM|uM|nM|pM|kDa|Da|Hz|kHz|MHz|GHz|ms|μs|ns|ps|fs|keV|MeV|GeV|eV|nm|μm|um|mm|cm|km|kg|mg|μg|ng|pg|mol|mmol|μmol|mL|μL|L|Pa|kPa|MPa|GPa|mV|kV|mA|μA|dB|fps|qubits?|ebits?|hours?|hrs?|mins?|days?|weeks?|months?|years?)\b"
    r"|\d+(?:\.\d+)?\s*%"
    r"|(?:p|P)\s*[<≤=>]\s*0?\.\d+(?:\s*[×x*]\s*10\^\{?[+-]?\d+\}?|[eE][+-]?\d+)?"
    r"|[±]\s*\d+(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?\s*(?:fold|times|×|x)\b"
    r"|(?:倍数|提升\s*\d|降低\s*\d|增长\s*\d|下降\s*\d|约\s*\d+(?:\.\d+)?\s*%)"
    r"|\bCI\b\s*[:=]?\s*[\[\(]?\s*[+-]?\d"
    r")",
    re.I,
)

ASSET_REF_FIG_REGEX = re.compile(r"(?:\bFig(?:ure)?s?\.?|图)\s*([A-Za-z]?\d+(?:\.\d+)*)", re.I)
ASSET_REF_TAB_REGEX = re.compile(r"(?:\bTables?|\bTabs?\.?|表|续表)\s*([A-Za-z]?\d+(?:\.\d+)*)", re.I)
ASSET_REF_EQ_REGEX = re.compile(r"(?:\bEq(?:uation)?s?\.?|公式|式)\s*\(?\s*([A-Za-z]?\d+(?:\.\d+)*)\s*\)?", re.I)


def extract_block_signals(text):
    """Extract deterministic citation, quantitative, and referenced-asset signals for a block."""
    txt = text or ""
    has_cites = bool(CITATION_MARKER_REGEX.search(txt))
    has_quant = bool(QUANT_SIGNAL_REGEX.search(txt))

    assets = []
    seen = set()
    for prefix_tag, pat in (("FIG", ASSET_REF_FIG_REGEX), ("TAB", ASSET_REF_TAB_REGEX), ("EQ", ASSET_REF_EQ_REGEX)):
        for m in pat.finditer(txt):
            lbl = m.group(1).strip()
            aid = f"{prefix_tag}-{lbl}"
            if aid not in seen:
                seen.add(aid)
                assets.append(aid)

    return has_cites, has_quant, assets


def classify_section_role(title_text):
    """Map a raw section heading to a canonical bilingual role tag."""
    t = re.sub(r"^\s*(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.|[A-Z]\.|[一二三四五六七八九十]+[、\.])\s*", "", title_text or "").strip()
    for role, pat in SECTION_ROLE_PATTERNS:
        if pat.search(t) or pat.search(title_text or ""):
            return role
    return "body"


def _strip_heading_numbering(text):
    t = re.sub(r"^\s*(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.|[A-Z]\.|[一二三四五六七八九十]+[、\.])\s*", "", text.strip())
    return re.sub(r"[^a-z\u4e00-\u9fa5\s]", "", t.lower()).strip()


def _should_promote_paragraph_to_heading(unit, body_size):
    """Conservative heuristic to recover academic headings missed by PDF layout classifier."""
    if unit.get("unit_type") != "paragraph":
        return False
    txt = unit.get("source_text", "").strip()
    if not txt or "\n" in txt or len(txt) > 95:
        return False
    words = txt.split()
    if len(words) > 12:
        return False
    if txt.endswith((".", "。", "?", "!", ";", "；", ",")):
        # Allow numbered headings like "1. Introduction" (does not end with .)
        return False
    if FIG_CAPTION_PREFIX.match(txt) or TAB_CAPTION_PREFIX.match(txt):
        return False

    clean_no_num = _strip_heading_numbering(txt)
    avg_sz = float(unit.get("avg_size", 10.0) or 10.0)
    is_bold = bool(unit.get("is_bold", False))

    if clean_no_num in STANDALONE_BILINGUAL_HEADINGS:
        if is_bold or avg_sz >= body_size * 1.02 or len(words) <= 4:
            return True

    if NUMBERED_HEADING_REGEX.match(txt):
        # Avoid promoting normal sentences or reference items starting with numbers
        if not re.match(r"^\d{4}\b", txt) and (is_bold or avg_sz >= body_size * 0.98):
            return True

    return False


def _normalize_block_type(unit_type):
    """Normalize unit_type into canonical evidence_index block_type vocabulary."""
    mapping = {
        "title": "title",
        "heading": "heading",
        "paragraph": "paragraph",
        "abstract": "paragraph",
        "metadata": "paragraph",
        "code_block": "paragraph",
        "unextractable_asset": "paragraph",
        "figure_caption": "caption",
        "table_caption": "caption",
        "table_content": "table_like",
        "display_equation": "formula",
        "reference": "reference",
        "footnote": "footnote",
    }
    return mapping.get(unit_type, "paragraph")


def _estimate_tokens(text):
    if not text:
        return 0
    en_w = len(re.findall(r"\b[a-zA-Z0-9_\-']+\b", text))
    cjk = len(re.findall(r"[\u4e00-\u9fa5]", text))
    other_sym = max(0, len(text) - en_w * 5 - cjk) // 4
    return max(1, int(round(en_w * 1.33 + cjk * 1.2 + other_sym)))


# ----------------------------------------------------------------------
# 2. Core Indexing Pipeline: Anchors, Section Tree, Signals, Packets
# ----------------------------------------------------------------------

def _build_document_index(doc_meta, doc_units, multi_doc=False):
    """Build anchors, hierarchical section tree, and pre-extracted signals for one document."""
    doc_id = doc_meta["doc_id"]
    src_file = doc_meta["source_file"]
    diag = doc_meta.get("diagnosis_summary") or {}
    dominant_route = diag.get("dominant_route", "native_clean")
    is_scanned = str(dominant_route).startswith("scanned")

    # Estimate body font size for heading refinement
    para_sizes = [
        float(u.get("avg_size", 10.0))
        for u in doc_units
        if u.get("unit_type") == "paragraph" and float(u.get("avg_size", 0.0) or 0.0) > 4.0
    ]
    body_size = collections.Counter(para_sizes).most_common(1)[0][0] if para_sizes else 10.0

    # First pass: refine headings and reference zone state (with TOC guard)
    refined_units = []
    in_ref_zone = False
    in_toc_zone = False
    for u in doc_units:
        ru = dict(u)
        txt_pre = ru.get("source_text", "").strip()
        if not in_toc_zone and _should_promote_paragraph_to_heading(ru, body_size):
            if not TOC_ENTRY_TAIL_REGEX.search(txt_pre):
                ru["unit_type"] = "heading"
                ru["raw_block_type"] = "heading_refined"

        u_type = ru.get("unit_type", "paragraph")
        txt = ru.get("source_text", "").strip()

        if u_type == "heading":
            role = classify_section_role(txt)
            if in_ref_zone and role == "body" and re.match(r"^[A-Z](?:\.\d+)*\.?\s+[A-Z]", txt):
                role = "appendix"
                ru["forced_role"] = "appendix"
            if role == "toc":
                in_toc_zone = True
            elif role != "body" and not TOC_ENTRY_TAIL_REGEX.search(txt):
                in_toc_zone = False

            if not in_toc_zone and role == "references" and not TOC_ENTRY_TAIL_REGEX.search(txt):
                in_ref_zone = True
            elif role == "appendix" and not TOC_ENTRY_TAIL_REGEX.search(txt):
                in_ref_zone = False
                if ru.get("reading_policy") == "reference_locked":
                    ru["reading_policy"] = "analyze"
            elif in_ref_zone and role not in ("references", "body"):
                in_ref_zone = False
        elif u_type == "reference" and not in_toc_zone:
            in_ref_zone = True

        if in_ref_zone and u_type in ("paragraph", "reference"):
            ru["unit_type"] = "reference"
            if ru.get("reading_policy") == "analyze":
                ru["reading_policy"] = "reference_locked"

        refined_units.append(ru)

    # Second pass: assign deterministic anchor IDs and section tree nodes
    page_seq = collections.Counter()
    anchors = []

    sec_flat = []
    sec_by_id = {}
    stack = []  # list of (level, section_id)
    sec_counter = 0

    def _make_sec_id(level):
        nonlocal sec_counter
        sec_counter += 1
        prefix = f"{doc_id}-" if multi_doc else ""
        return f"{prefix}S{sec_counter:02d}"

    def _ensure_section(title, level, role, zone, page):
        parent_id = None
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            parent_id = stack[-1][1]
        sid = _make_sec_id(level)
        node = {
            "section_id": sid,
            "doc_id": doc_id,
            "parent_id": parent_id,
            "level": level,
            "title": title,
            "canonical_role": role,
            "zone": zone,
            "page_span": [page, page],
            "block_count": 0,
            "word_count": 0,
            "char_count": 0,
            "estimated_tokens": 0,
            "anchor_ids": [],
            "children": [],
        }
        sec_flat.append(node)
        sec_by_id[sid] = node
        if parent_id and parent_id in sec_by_id:
            sec_by_id[parent_id]["children"].append(sid)
        stack.append((level, sid))
        return node

    front_sec = None
    abstract_sec = None
    ref_sec = None
    active_sec = None

    for ru in refined_units:
        p_span = ru.get("page_span", [1, 1])
        start_page = max(1, int(p_span[0] if p_span else 1))
        page_seq[start_page] += 1
        b_seq = page_seq[start_page]

        if multi_doc:
            anchor_id = f"{doc_id}-P{start_page}-B{b_seq:03d}"
        else:
            anchor_id = f"P{start_page}-B{b_seq:03d}"

        u_type = ru.get("unit_type", "paragraph")
        raw_bt = ru.get("raw_block_type", u_type)
        txt = ru.get("source_text", "").strip()
        c_cnt = int(ru.get("char_count", len(txt)))
        w_cnt = int(ru.get("word_count", 0))

        # Section assignment
        if u_type in ("title", "metadata"):
            if front_sec is None:
                front_sec = _ensure_section("Title & Metadata", 1, "front_matter", "front_matter", start_page)
            target_sec = front_sec
        elif u_type == "abstract":
            if abstract_sec is None:
                abstract_sec = _ensure_section("Abstract", 1, "abstract", "body", start_page)
            target_sec = abstract_sec
            active_sec = abstract_sec
        elif u_type == "heading":
            lvl = infer_heading_level(txt, raw_bt)
            role = ru.get("forced_role") or classify_section_role(txt)
            # Inherit parent section role for level>=2 subsections with domain-specific titles
            if role == "body" and lvl > 1 and stack:
                for p_lvl, p_sid in reversed(stack):
                    if p_lvl < lvl and p_sid in sec_by_id:
                        p_sec = sec_by_id[p_sid]
                        if p_sec.get("zone") == "body" and p_sec.get("canonical_role") not in ("body", "abstract"):
                            role = p_sec["canonical_role"]
                        break
            if role == "toc":
                zone = "toc"
            elif role == "references":
                zone = "references_locked"
            elif role == "appendix" or (active_sec and active_sec.get("zone") == "appendix" and role == "body"):
                zone = "appendix"
                if role == "body":
                    role = "appendix"
            else:
                zone = "body"
            target_sec = _ensure_section(re.sub(r"\s+", " ", txt), lvl, role, zone, start_page)
            active_sec = target_sec
            if role == "references":
                ref_sec = target_sec
        elif u_type == "reference":
            if ref_sec is None:
                ref_sec = _ensure_section("References", 1, "references", "references_locked", start_page)
            target_sec = ref_sec
            active_sec = ref_sec
        else:
            # Keep page-1 pre-abstract affiliation/email/stamp blocks in front_matter instead of creating premature Main Body
            is_front_affil = (
                start_page == 1
                and abstract_sec is None
                and active_sec is None
                and (
                    re.search(r"(?:\^\{?\d+\}?|@|\b(?:University|Institute|Laboratory|Department|School\s+of|Academy\s+of|arXiv:)\b)", txt, re.I)
                    or len(txt) < 250
                )
            )
            if is_front_affil:
                if front_sec is None:
                    front_sec = _ensure_section("Title & Metadata", 1, "front_matter", "front_matter", start_page)
                target_sec = front_sec
            else:
                if active_sec is None:
                    active_sec = _ensure_section("Main Body", 1, "body", "body", start_page)
                target_sec = active_sec

        # Update section stats
        target_sec["page_span"][0] = min(target_sec["page_span"][0], start_page)
        end_p = max(start_page, int(p_span[1] if len(p_span) > 1 else start_page))
        target_sec["page_span"][1] = max(target_sec["page_span"][1], end_p)
        target_sec["block_count"] += 1
        target_sec["word_count"] += w_cnt
        target_sec["char_count"] += c_cnt
        target_sec["estimated_tokens"] += _estimate_tokens(txt)
        target_sec["anchor_ids"].append(anchor_id)

        q_flags = list(ru.get("source_warnings", []))
        if u_type == "reference" and "reference_locked" not in q_flags:
            q_flags.append("reference_locked")
        if u_type == "unextractable_asset" and "unextractable_asset" not in q_flags:
            q_flags.append("unextractable_asset")

        canonical_bt = _normalize_block_type(u_type)
        norm_role = normalize_section_role(target_sec["canonical_role"], unit_type=u_type, zone=target_sec["zone"])
        has_cites, has_quant, ref_assets = extract_block_signals(txt)
        ext_method = ru.get("extraction_method") or ("ocr_tesseract" if "ocr_extracted" in q_flags else "native")
        anc_rec = {
            "anchor_id": anchor_id,
            "doc_id": doc_id,
            "source_file": src_file,
            "page": start_page,
            "page_span": [start_page, end_p],
            "printed_page_span": ru.get("printed_page_span", [str(start_page), str(end_p)]),
            "section_id": target_sec["section_id"],
            "section_title": target_sec["title"],
            "section_role": norm_role,
            "raw_section_role": target_sec["canonical_role"],
            "zone": target_sec["zone"],
            "block_type": canonical_bt,
            "raw_block_type": raw_bt,
            "reading_policy": ru.get("reading_policy", "analyze"),
            "extraction_method": ext_method,
            "text": txt,
            "word_count": w_cnt,
            "char_count": c_cnt,
            "bbox": ru.get("bbox", [0, 0, 0, 0]),
            "column": ru.get("column", "full"),
            "quality_flags": q_flags,
            "has_citation_markers": has_cites,
            "has_quantitative_signals": has_quant,
            "referenced_assets": ref_assets,
        }
        if ru.get("ocr_engine"):
            anc_rec["ocr_engine"] = ru["ocr_engine"]
        if ru.get("ocr_warning"):
            anc_rec["ocr_warning"] = ru["ocr_warning"]
        if ru.get("rendered_page_png"):
            anc_rec["rendered_page_png"] = ru["rendered_page_png"]
        anchors.append(anc_rec)

    # Build nested section_tree representation
    def _build_tree_node(sec_id):
        n = dict(sec_by_id[sec_id])
        child_ids = n.pop("children", [])
        n["anchor_range"] = [n["anchor_ids"][0], n["anchor_ids"][-1]] if n["anchor_ids"] else []
        n.pop("anchor_ids", None)
        n["children"] = [_build_tree_node(cid) for cid in child_ids]
        return n

    top_sections = [s["section_id"] for s in sec_flat if s["parent_id"] is None]
    section_tree = [_build_tree_node(sid) for sid in top_sections]

    # Pre-extracted signals for this document
    figures, tables, equations, limitation_clues = [], [], [], []
    fig_seq, tab_seq, eq_seq = 0, 0, 0
    prefix = f"{doc_id}-" if multi_doc else ""
    bound_table_anchors = set()

    candidate_title = None
    author_clues = []
    dois, arxiv_ids, code_urls, years = set(), set(), set(), set()

    for idx_a, anc in enumerate(anchors):
        txt = anc["text"]
        bt = anc["block_type"]
        raw_bt = anc["raw_block_type"]

        # Meta info from front matter / page 1-2
        if anc["page"] <= 2:
            if bt == "title" and not candidate_title:
                candidate_title = txt
            if raw_bt == "metadata":
                author_clues.append({"anchor_id": anc["anchor_id"], "text": txt[:300]})
            for m in DOI_REGEX.finditer(txt):
                dois.add(m.group(0).rstrip(".,;)"))
            for m in ARXIV_REGEX.finditer(txt):
                arxiv_ids.add(m.group(1))
            for m in CODE_URL_REGEX.finditer(txt):
                code_urls.add(m.group(0).rstrip(".,;)"))
            if anc["page"] == 1:
                for m in YEAR_REGEX.finditer(txt):
                    years.add(m.group(1))
        else:
            for m in CODE_URL_REGEX.finditer(txt):
                code_urls.add(m.group(0).rstrip(".,;)"))

        # Figures & Tables (bind up to 3 subsequent same-page header/table_like blocks and deduplicate)
        if bt == "caption":
            m_fig = FIG_CAPTION_PREFIX.match(txt)
            m_tab = TAB_CAPTION_PREFIX.match(txt)
            if raw_bt == "figure_caption" or (m_fig and raw_bt != "table_caption"):
                fig_seq += 1
                figures.append({
                    "figure_id": f"{prefix}FIG-{fig_seq}",
                    "label": m_fig.group(1) if m_fig else str(fig_seq),
                    "anchor_id": anc["anchor_id"],
                    "doc_id": doc_id,
                    "page": anc["page"],
                    "section_id": anc["section_id"],
                    "caption": txt,
                })
            else:
                tab_seq += 1
                content_anchor_ids = []
                for probe in range(idx_a + 1, min(len(anchors), idx_a + 4)):
                    cand = anchors[probe]
                    if cand["page"] != anc["page"]:
                        break
                    c_bt = cand["block_type"]
                    if c_bt in ("heading", "caption", "title", "reference"):
                        break
                    c_txt = cand["text"].strip()
                    if c_bt == "table_like":
                        content_anchor_ids.append(cand["anchor_id"])
                        bound_table_anchors.add(cand["anchor_id"])
                    elif c_bt == "paragraph":
                        # Bind short table header / inline open-table body immediately under caption
                        looks_like_table_body = (
                            not c_txt.endswith((".", "。", "?", "!", ";"))
                            and len(c_txt) <= 320
                            and not re.match(r"^(?:We |In |For |This |The |By |由|在|如图|由表)", c_txt)
                        )
                        if looks_like_table_body:
                            content_anchor_ids.append(cand["anchor_id"])
                            bound_table_anchors.add(cand["anchor_id"])
                        else:
                            break
                    else:
                        break
                if not content_anchor_ids and idx_a - 1 >= 0:
                    prev_a = anchors[idx_a - 1]
                    if prev_a["page"] == anc["page"] and prev_a["block_type"] == "table_like" and prev_a["anchor_id"] not in bound_table_anchors:
                        content_anchor_ids.append(prev_a["anchor_id"])
                        bound_table_anchors.add(prev_a["anchor_id"])

                tables.append({
                    "table_id": f"{prefix}TAB-{tab_seq}",
                    "label": m_tab.group(1) if m_tab else str(tab_seq),
                    "anchor_id": anc["anchor_id"],
                    "content_anchor_id": content_anchor_ids[0] if content_anchor_ids else None,
                    "content_anchor_ids": content_anchor_ids,
                    "doc_id": doc_id,
                    "page": anc["page"],
                    "section_id": anc["section_id"],
                    "caption": txt,
                })
        elif bt == "table_like":
            if anc["anchor_id"] not in bound_table_anchors:
                has_adj_caption = (
                    idx_a + 1 < len(anchors)
                    and anchors[idx_a + 1]["page"] == anc["page"]
                    and anchors[idx_a + 1]["block_type"] == "caption"
                    and anchors[idx_a + 1]["raw_block_type"] == "table_caption"
                )
                if not has_adj_caption:
                    tab_seq += 1
                    bound_table_anchors.add(anc["anchor_id"])
                    tables.append({
                        "table_id": f"{prefix}TAB-{tab_seq}",
                        "label": str(tab_seq),
                        "anchor_id": anc["anchor_id"],
                        "content_anchor_id": anc["anchor_id"],
                        "content_anchor_ids": [anc["anchor_id"]],
                        "doc_id": doc_id,
                        "page": anc["page"],
                        "section_id": anc["section_id"],
                        "caption": txt[:200],
                    })

        # Equations
        if bt == "formula":
            eq_seq += 1
            m_eq = EQ_NUM_REGEX.search(txt)
            eq_num = next((g for g in m_eq.groups() if g), None) if m_eq else None
            equations.append({
                "eq_id": f"{prefix}EQ-{eq_seq}",
                "equation_number": eq_num,
                "anchor_id": anc["anchor_id"],
                "doc_id": doc_id,
                "page": anc["page"],
                "section_id": anc["section_id"],
                "text": txt[:300],
            })

        # Limitation / Assumption / Future work cues (skip references)
        if anc["zone"] != "references_locked" and bt in ("paragraph", "heading") and anc["reading_policy"] == "analyze":
            matched_cues = []
            for cue_name, cue_pat in LIMITATION_CUE_PATTERNS:
                if cue_pat.search(txt):
                    matched_cues.append(cue_name)
            if matched_cues or anc["section_role"] == "limitations":
                limitation_clues.append({
                    "anchor_id": anc["anchor_id"],
                    "doc_id": doc_id,
                    "page": anc["page"],
                    "section_id": anc["section_id"],
                    "section_title": anc["section_title"],
                    "matched_cues": matched_cues if matched_cues else ["limitations_section"],
                    "snippet": txt[:260],
                })

    if not candidate_title and anchors:
        for anc in anchors[:5]:
            if anc["block_type"] in ("title", "heading", "paragraph") and anc["word_count"] >= 2:
                candidate_title = anc["text"][:200]
                break

    # PDF Quality & Risk summary
    page_count = max(1, int(doc_meta.get("page_count", 1)))
    page_words = collections.Counter()
    garbled_pages = set()
    formula_pua_pages = set()
    ocr_pages = set()
    unextractable_pages = set()

    for anc in anchors:
        p = anc["page"]
        page_words[p] += anc["word_count"]
        if "potential_font_garble" in anc["quality_flags"]:
            garbled_pages.add(p)
        if "formula_pua_glyphs" in anc["quality_flags"] or (anc["block_type"] == "formula" and FORMULA_PUA_REGEX.search(anc["text"])):
            if "formula_pua_glyphs" not in anc["quality_flags"]:
                anc["quality_flags"].append("formula_pua_glyphs")
            formula_pua_pages.add(p)
        if "ocr_extracted" in anc["quality_flags"]:
            ocr_pages.add(p)
        if "unextractable_asset" in anc["quality_flags"] or anc["reading_policy"] == "manual_review_asset":
            unextractable_pages.add(p)

    low_density_pages = [
        p for p in range(1, page_count + 1)
        if page_words[p] < 20 and p not in unextractable_pages
    ]

    rendered_pages = (
        doc_meta.get("rendered_pages")
        or (doc_meta.get("diagnosis_summary") or {}).get("rendered_pages")
        or []
    )
    rendered_by_page = {int(r["page"]): r.get("png_path") for r in rendered_pages if r.get("page") and r.get("png_path")}

    uncertainty_records = []
    quality_notices = []
    if is_scanned or unextractable_pages or ocr_pages:
        if unextractable_pages:
            rec_u = {
                "doc_id": doc_id,
                "category": "scanned_document_unreadable",
                "severity": "high",
                "pages": sorted(unextractable_pages),
                "message": f"Document {src_file} appears scanned ({dominant_route}); native text missing and OCR failed/unavailable on pages {sorted(unextractable_pages)}.",
            }
            rel_pngs = [rendered_by_page[p] for p in sorted(unextractable_pages) if p in rendered_by_page]
            if rel_pngs:
                rec_u["rendered_pages"] = rel_pngs
            uncertainty_records.append(rec_u)
        if ocr_pages:
            rec_o = {
                "doc_id": doc_id,
                "category": "ocr_extracted_text",
                "severity": "medium",
                "pages": sorted(ocr_pages),
                "message": f"Document {src_file} extracted via OCR on pages {sorted(ocr_pages)}; verify critical numbers against rendered page image.",
            }
            rel_pngs = [rendered_by_page[p] for p in sorted(ocr_pages) if p in rendered_by_page]
            if rel_pngs:
                rec_o["rendered_pages"] = rel_pngs
            uncertainty_records.append(rec_o)
    if garbled_pages:
        rec_g = {
            "doc_id": doc_id,
            "category": "font_garble_or_cid",
            "severity": "medium",
            "pages": sorted(garbled_pages),
            "message": f"Potential CID/PUA font garble detected on pages {sorted(garbled_pages)} in {src_file}.",
        }
        rel_pngs = [rendered_by_page[p] for p in sorted(garbled_pages) if p in rendered_by_page]
        if rel_pngs:
            rec_g["rendered_pages"] = rel_pngs
        uncertainty_records.append(rec_g)
    if formula_pua_pages:
        quality_notices.append({
            "doc_id": doc_id,
            "category": "formula_pua_glyphs",
            "severity": "low",
            "pages": sorted(formula_pua_pages),
            "message": f"Formula blocks contain TeX/CM math PUA glyphs on pages {sorted(formula_pua_pages)} in {src_file}; prose text unaffected.",
        })
    if low_density_pages:
        quality_notices.append({
            "doc_id": doc_id,
            "category": "low_text_density_pages",
            "severity": "low",
            "pages": low_density_pages,
            "message": f"Pages {low_density_pages} have very low extracted word density (<20 words); may contain full-page figures/scans.",
        })

    clean_authors, cleaned_author_clues = _clean_author_clues(author_clues)

    doc_signals = {
        "doc_id": doc_id,
        "source_file": src_file,
        "format": doc_meta.get("format", "unknown"),
        "language": doc_meta.get("language", "en"),
        "page_count": page_count,
        "meta_clues": {
            "candidate_title": candidate_title or "Untitled",
            "authors_cleaned": clean_authors,
            "author_clues": cleaned_author_clues[:4],
            "years": sorted(years),
            "dois": sorted(dois),
            "arxiv_ids": sorted(arxiv_ids),
            "code_urls": sorted(code_urls),
        },
        "figures": figures,
        "tables": tables,
        "equations": equations,
        "limitation_clues": limitation_clues,
        "quality_risk": {
            "dominant_route": dominant_route,
            "is_scanned": is_scanned,
            "garbled_pages": sorted(garbled_pages),
            "formula_pua_pages": sorted(formula_pua_pages),
            "ocr_pages": sorted(ocr_pages),
            "unextractable_pages": sorted(unextractable_pages),
            "low_density_pages": low_density_pages,
            "rendered_pages": rendered_pages,
            "has_references_zone": ref_sec is not None,
        },
        "initial_uncertainty": uncertainty_records,
        "quality_notices": quality_notices,
    }

    # ----------------------------------------------------------------
    # Unnumbered-Prose Fallback: inject synthetic body section when
    # the document has substantive content but no body-zone section
    # (e.g. white papers, plain-text reports without heading numbering).
    # ----------------------------------------------------------------
    has_explicit_body = any(s["zone"] == "body" for s in sec_flat)
    if not has_explicit_body and not is_scanned:
        # Collect substantive prose/table/formula anchors trapped in non-body zones (e.g. after TOC/front_matter)
        body_candidate_anchors = [
            a for a in anchors
            if a.get("zone") not in ("references_locked", "appendix")
            and a.get("reading_policy") in ("analyze", None)
            and a.get("block_type") not in ("heading", "reference", "title")
            and not (a.get("block_type") == "metadata" and a.get("word_count", 0) < 25)
            and not (a.get("zone") == "toc" and TOC_ENTRY_TAIL_REGEX.search(a.get("text", "").strip()))
        ]
        total_cand_wc = sum(a.get("word_count", 0) for a in body_candidate_anchors)
        total_cand_cc = sum(a.get("char_count", 0) for a in body_candidate_anchors)
        # Threshold: >=120 English words OR >=200 CJK characters -> inject fallback
        if total_cand_wc >= 120 or total_cand_cc >= 200:
            sec_counter += 1
            prefix_fb = f"{doc_id}-" if multi_doc else ""
            synth_sid = f"{prefix_fb}S{sec_counter:02d}"
            first_page = body_candidate_anchors[0]["page"] if body_candidate_anchors else 1
            last_page = body_candidate_anchors[-1]["page"] if body_candidate_anchors else first_page
            synth_tokens = sum(_estimate_tokens(a.get("text", "")) for a in body_candidate_anchors)
            synth_node = {
                "section_id": synth_sid,
                "doc_id": doc_id,
                "parent_id": None,
                "level": 1,
                "title": "Main Body",
                "canonical_role": "body",
                "zone": "body",
                "page_span": [first_page, last_page],
                "block_count": len(body_candidate_anchors),
                "word_count": total_cand_wc,
                "char_count": total_cand_cc,
                "estimated_tokens": synth_tokens,
                "anchor_ids": [a["anchor_id"] for a in body_candidate_anchors],
                "children": [],
            }
            sec_flat.append(synth_node)
            sec_by_id[synth_sid] = synth_node
            # Back-assign anchors to synthetic section
            for a in body_candidate_anchors:
                old_sid = a.get("section_id")
                if old_sid and old_sid in sec_by_id and old_sid != synth_sid:
                    old_s = sec_by_id[old_sid]
                    old_s["block_count"] = max(0, old_s["block_count"] - 1)
                    old_s["word_count"] = max(0, old_s["word_count"] - a.get("word_count", 0))
                    old_s["char_count"] = max(0, old_s["char_count"] - a.get("char_count", 0))
                    if a["anchor_id"] in old_s["anchor_ids"]:
                        old_s["anchor_ids"].remove(a["anchor_id"])
                a["section_id"] = synth_sid
                a["section_title"] = "Main Body"
                a["section_role"] = "body_unclassified"
                a["raw_section_role"] = "body"
                a["zone"] = "body"
            # Rebuild section_tree to include synthetic node
            top_sections = [s["section_id"] for s in sec_flat if s["parent_id"] is None]
            section_tree = [_build_tree_node(sid) for sid in top_sections]

    sec_summary_flat = [
        {
            "section_id": s["section_id"],
            "doc_id": s["doc_id"],
            "parent_id": s["parent_id"],
            "level": s["level"],
            "title": s["title"],
            "canonical_role": s["canonical_role"],
            "section_role": normalize_section_role(s["canonical_role"], zone=s["zone"]),
            "zone": s["zone"],
            "page_span": s["page_span"],
            "block_count": s["block_count"],
            "word_count": s["word_count"],
            "char_count": s["char_count"],
            "estimated_tokens": s["estimated_tokens"],
        }
        for s in sec_flat
    ]

    return anchors, sec_summary_flat, section_tree, doc_signals


# ----------------------------------------------------------------------
# 3. Section-Boundary Reading Packet Builder (DeepSeek 16K Budget Safe)
# ----------------------------------------------------------------------

def _split_oversized_block_by_sentences(text, max_words):
    """Fallback split only on sentence boundaries when a single block exceeds budget."""
    sents = [s.strip() for s in re.split(r"(?<=[.?!。！？])\s+", text) if s.strip()]
    if len(sents) <= 1:
        return [text]
    chunks = []
    buf = []
    buf_w = 0
    for s in sents:
        sw, _, _ = count_words_and_sentences(s)
        if buf and buf_w + sw > max_words:
            chunks.append(" ".join(buf))
            buf = [s]
            buf_w = sw
        else:
            buf.append(s)
            buf_w += sw
    if buf:
        chunks.append(" ".join(buf))
    return chunks


def _pack_reading_packets(anchors, sections_flat, packets_dir, max_words=3200, max_chars=22000, include_references=False, task_guidance=None):
    """
    Pack anchors into reading_packets/ along section boundaries.
    Never chops mid-sentence; locks out reference_locked blocks by default.
    """
    os.makedirs(packets_dir, exist_ok=True)
    for old in glob.glob(os.path.join(packets_dir, "packet_*")):
        try:
            os.remove(old)
        except Exception:
            pass

    # Group anchors by (doc_id, section_id) preserving document order
    grouped = []
    curr_key = None
    curr_list = []
    for anc in anchors:
        if anc.get("zone") == "references_locked" and not include_references:
            continue
        if anc.get("reading_policy") == "scope_excluded":
            continue
        key = (anc["doc_id"], anc["section_id"])
        if key != curr_key:
            if curr_list:
                grouped.append((curr_key, curr_list))
            curr_key = key
            curr_list = [anc]
        else:
            curr_list.append(anc)
    if curr_list:
        grouped.append((curr_key, curr_list))

    sec_lookup = {(s["doc_id"], s["section_id"]): s for s in sections_flat}

    packets = []
    curr_pkt_items = []
    curr_words = 0
    curr_chars = 0
    curr_secs = []

    def _flush_packet():
        nonlocal curr_pkt_items, curr_words, curr_chars, curr_secs
        if not curr_pkt_items:
            return
        pkt_idx = len(packets) + 1
        pkt_id = f"packet_{pkt_idx:03d}"
        fname = f"{pkt_id}.md"
        fpath = os.path.join(packets_dir, fname)

        lines = [
            f"# Reading Packet {pkt_id}",
            f"- Sections: {', '.join(dict.fromkeys(curr_secs))}",
            f"- Word Count: {curr_words} | Anchor Count: {len(curr_pkt_items)}",
            "",
        ]
        last_sec_hdr = None
        anchor_ids = []
        pages = set()
        for item in curr_pkt_items:
            sec_hdr = f"[{item['doc_id']}:{item['section_id']}] {item['section_title']} (role={item['section_role']})"
            if sec_hdr != last_sec_hdr:
                lines.append(f"## {sec_hdr}\n")
                last_sec_hdr = sec_hdr
            tag = f"[{item['anchor_id']}]"
            bt_note = f"({item['block_type']}) " if item["block_type"] not in ("paragraph", "heading") else ""
            sig_parts = []
            if item.get("has_citation_markers"):
                sig_parts.append("cites=true")
            if item.get("has_quantitative_signals"):
                sig_parts.append("quant=true")
            if item.get("referenced_assets"):
                sig_parts.append("assets=" + ",".join(item["referenced_assets"]))
            sig_note = f" {{{'; '.join(sig_parts)}}}" if sig_parts else ""
            lines.append(f"{tag}{sig_note} {bt_note}{item['text']}\n")
            anchor_ids.append(item["anchor_id"])
            pages.add(item["page"])

        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        packets.append({
            "packet_id": pkt_id,
            "file": fname,
            "section_ids": list(dict.fromkeys(curr_secs)),
            "pages": sorted(pages),
            "anchor_count": len(anchor_ids),
            "anchor_range": [anchor_ids[0], anchor_ids[-1]] if anchor_ids else [],
            "word_count": curr_words,
            "char_count": curr_chars,
        })
        curr_pkt_items = []
        curr_words = 0
        curr_chars = 0
        curr_secs = []

    for key, sec_anchors in grouped:
        sec_info = sec_lookup.get(key, {})
        sec_words = sum(a["word_count"] for a in sec_anchors)
        sec_chars = sum(a["char_count"] for a in sec_anchors)

        # If adding the whole section fits nicely within budget, keep the section intact
        if curr_pkt_items and (curr_words + sec_words > max_words or curr_chars + sec_chars > max_chars):
            _flush_packet()

        if sec_words <= max_words and sec_chars <= max_chars:
            curr_pkt_items.extend(sec_anchors)
            curr_words += sec_words
            curr_chars += sec_chars
            curr_secs.append(key[1])
            continue

        # Large section: pack block-by-block without mid-sentence chop
        for anc in sec_anchors:
            aw = anc["word_count"]
            ac = anc["char_count"]
            if aw > max_words:
                if curr_pkt_items:
                    _flush_packet()
                for part in _split_oversized_block_by_sentences(anc["text"], max_words):
                    pw, pc, _ = count_words_and_sentences(part)
                    sub_anc = dict(anc)
                    sub_anc["text"] = part
                    sub_anc["word_count"] = pw
                    sub_anc["char_count"] = pc
                    curr_pkt_items.append(sub_anc)
                    curr_words += pw
                    curr_chars += pc
                    curr_secs.append(key[1])
                    _flush_packet()
                continue

            if curr_pkt_items and (curr_words + aw > max_words or curr_chars + ac > max_chars):
                _flush_packet()
            curr_pkt_items.append(anc)
            curr_words += aw
            curr_chars += ac
            curr_secs.append(key[1])

    _flush_packet()

    manifest = {
        "packet_count": len(packets),
        "max_packet_words": max_words,
        "max_packet_chars": max_chars,
        "references_included": include_references,
        "task_guidance": task_guidance or {"expected_output_language": "auto", "focus_directives": [], "task_excerpt": ""},
        "packets": packets,
    }
    with open(os.path.join(packets_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


# ----------------------------------------------------------------------
# 4. Main Indexer Orchestrator
# ----------------------------------------------------------------------

def index_case(
    input_dir,
    work_dir,
    output_dir=None,
    single_file=None,
    max_packet_words=3200,
    max_packet_chars=22000,
    include_references=False,
):
    """Run ingestion + anchor indexing + section tree + signals + reading packets + early draft skeleton."""
    t0 = time.time()
    work_abs = os.path.abspath(work_dir)
    os.makedirs(work_abs, exist_ok=True)
    if output_dir:
        os.makedirs(os.path.abspath(output_dir), exist_ok=True)

    ingest_manifest = ingest_case(input_dir=input_dir, work_dir=work_abs, single_file=single_file)

    su_path = os.path.join(work_abs, "source_units.json")
    with open(su_path, "r", encoding="utf-8") as f:
        su_data = json.load(f)

    case_id = su_data.get("case_id", ingest_manifest.get("case_id", "literature_notes_case"))
    documents_meta = su_data.get("documents", [])
    all_units = su_data.get("units", [])
    multi_doc = len(documents_meta) > 1

    units_by_doc = collections.defaultdict(list)
    for u in all_units:
        units_by_doc[u.get("doc_id", "D1")].append(u)

    all_anchors = []
    all_sections_flat = []
    all_section_trees = {}
    all_doc_signals = []
    all_uncertainties = []
    all_quality_notices = []

    if not documents_meta and all_units:
        documents_meta = [{
            "doc_id": "D1",
            "source_file": all_units[0].get("source_file", "unknown"),
            "format": "unknown",
            "language": "en",
            "page_count": 1,
            "diagnosis_summary": None,
        }]

    for doc_meta in documents_meta:
        did = doc_meta["doc_id"]
        d_units = units_by_doc.get(did, [])
        anchors, sec_flat, sec_tree, doc_sig = _build_document_index(doc_meta, d_units, multi_doc=multi_doc)
        all_anchors.extend(anchors)
        all_sections_flat.extend(sec_flat)
        all_section_trees[did] = sec_tree
        all_doc_signals.append(doc_sig)
        all_uncertainties.extend(doc_sig["initial_uncertainty"])
        all_quality_notices.extend(doc_sig.get("quality_notices", []))

    # Write anchors_compact.txt (lightweight grep-ready & verbatim-copy table ordered by doc/page/seq)
    compact_lines = []
    for anc in all_anchors:
        did = anc.get("doc_id", "D1")
        aid = anc.get("anchor_id", "")
        role = anc.get("section_role", "body_unclassified")
        bt = anc.get("block_type", "paragraph")
        fl_list = []
        if anc.get("has_citation_markers"):
            fl_list.append("cite")
        if anc.get("has_quantitative_signals"):
            fl_list.append("quant")
        if anc.get("referenced_assets"):
            fl_list.append("assets=" + ",".join(anc["referenced_assets"]))
        fl_str = "/".join(fl_list) if fl_list else "none"
        src_tag = " | src=ocr" if anc.get("extraction_method") == "ocr_tesseract" else ""
        flat_txt = re.sub(r"\s*\n\s*", " ", anc.get("text", "")).strip()
        compact_lines.append(f"[{did} | {aid} | role={role} | type={bt}{src_tag} | flags={fl_str}] {flat_txt}")
    compact_path = os.path.join(work_abs, "anchors_compact.txt")
    with open(compact_path, "w", encoding="utf-8") as f:
        f.write("\n".join(compact_lines) + ("\n" if compact_lines else ""))

    all_rendered_pages = []
    for d in documents_meta:
        rps = d.get("rendered_pages") or (d.get("diagnosis_summary") or {}).get("rendered_pages") or []
        all_rendered_pages.extend(rps)

    # Write evidence_index.json
    evidence_index = {
        "case_id": case_id,
        "multi_document": multi_doc,
        "doc_count": len(documents_meta),
        "documents": [
            {
                "doc_id": d.get("doc_id"),
                "source_file": d.get("source_file"),
                "rel_path": d.get("rel_path"),
                "format": d.get("format"),
                "language": d.get("language"),
                "page_count": d.get("page_count"),
                "pdf_metadata": (d.get("diagnosis_summary") or {}).get("pdf_metadata", {}),
                "rendered_pages": d.get("rendered_pages") or (d.get("diagnosis_summary") or {}).get("rendered_pages") or [],
            }
            for d in documents_meta
        ],
        "artifacts": {
            "anchors_compact_path": compact_path,
            "rendered_pages": all_rendered_pages,
        },
        "total_anchors": len(all_anchors),
        "block_type_counts": dict(collections.Counter(a["block_type"] for a in all_anchors)),
        "section_role_counts": dict(collections.Counter(a["section_role"] for a in all_anchors)),
        "section_count": len(all_sections_flat),
        "sections": all_sections_flat,
        "section_trees": all_section_trees,
        "anchors": all_anchors,
    }
    ev_path = os.path.join(work_abs, "evidence_index.json")
    with open(ev_path, "w", encoding="utf-8") as f:
        json.dump(evidence_index, f, ensure_ascii=False, indent=2)

    # Write paper_signals.json
    paper_signals = {
        "case_id": case_id,
        "doc_count": len(all_doc_signals),
        "documents": all_doc_signals,
        "totals": {
            "figures": sum(len(d["figures"]) for d in all_doc_signals),
            "tables": sum(len(d["tables"]) for d in all_doc_signals),
            "equations": sum(len(d["equations"]) for d in all_doc_signals),
            "limitation_clues": sum(len(d["limitation_clues"]) for d in all_doc_signals),
            "initial_uncertainties": len(all_uncertainties),
            "quality_notices": len(all_quality_notices),
            "rendered_pages": len(all_rendered_pages),
            "ocr_pages": sum(len((d.get("quality_risk") or {}).get("ocr_pages", [])) for d in all_doc_signals),
        },
        "initial_uncertainty": all_uncertainties,
        "quality_notices": all_quality_notices,
    }
    sig_path = os.path.join(work_abs, "paper_signals.json")
    with open(sig_path, "w", encoding="utf-8") as f:
        json.dump(paper_signals, f, ensure_ascii=False, indent=2)

    # Build reading_packets/
    packets_dir = os.path.join(work_abs, "reading_packets")
    pkt_manifest = _pack_reading_packets(
        all_anchors,
        all_sections_flat,
        packets_dir,
        max_words=max_packet_words,
        max_chars=max_packet_chars,
        include_references=include_references,
        task_guidance=ingest_manifest.get("task_guidance"),
    )

    # Write early fallback draft_skeleton.json
    draft_skeleton = {
        "case_id": case_id,
        "status": "partial",
        "stage": "indexed_skeleton_only",
        "research_questions": ingest_manifest.get("research_questions", []),
        "target_formats": ingest_manifest.get("target_formats", ["json"]),
        "output_contract": ingest_manifest.get("output_contract", {}),
        "documents_meta": [
            {
                "doc_id": d["doc_id"],
                "source_file": d["source_file"],
                "language": d["language"],
                "page_count": d["page_count"],
                "meta_clues": d["meta_clues"],
                "quality_risk": d["quality_risk"],
            }
            for d in all_doc_signals
        ],
        "section_skeleton": [
            {
                "section_id": s["section_id"],
                "doc_id": s["doc_id"],
                "level": s["level"],
                "title": s["title"],
                "canonical_role": s["canonical_role"],
                "zone": s["zone"],
                "page_span": s["page_span"],
                "block_count": s["block_count"],
                "word_count": s["word_count"],
                "summary_status": "locked_references" if s["zone"] == "references_locked" else "pending",
            }
            for s in all_sections_flat
        ],
        "visual_and_math_inventory": {
            "figures": [fig for d in all_doc_signals for fig in d["figures"]],
            "tables": [tab for d in all_doc_signals for tab in d["tables"]],
            "equations_count": paper_signals["totals"]["equations"],
        },
        "limitation_clues": [clue for d in all_doc_signals for clue in d["limitation_clues"]],
        "uncertainty": all_uncertainties,
        "quality_notices": all_quality_notices,
        "reading_packets_manifest": pkt_manifest,
    }
    draft_path = os.path.join(work_abs, "draft_skeleton.json")
    with open(draft_path, "w", encoding="utf-8") as f:
        json.dump(draft_skeleton, f, ensure_ascii=False, indent=2)

    elapsed = round(time.time() - t0, 3)

    compact_summary = {
        "status": "ok",
        "case_id": case_id,
        "elapsed_sec": elapsed,
        "docs": len(documents_meta),
        "pages": sum(d["page_count"] for d in all_doc_signals),
        "anchors": len(all_anchors),
        "sections": len(all_sections_flat),
        "figures": paper_signals["totals"]["figures"],
        "tables": paper_signals["totals"]["tables"],
        "equations": paper_signals["totals"]["equations"],
        "limitation_clues": paper_signals["totals"]["limitation_clues"],
        "packets": pkt_manifest["packet_count"],
        "uncertainties": len(all_uncertainties),
        "work_dir": work_abs,
    }
    print("[structure_indexer] " + json.dumps(compact_summary, ensure_ascii=False))
    return compact_summary


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Deterministic Document Skeleton & Evidence Anchor Engine")
    parser.add_argument("--input-dir", default=".", help="Directory containing input case files")
    parser.add_argument("--work-dir", required=True, help="Directory to store indexed work artifacts")
    parser.add_argument("--output-dir", default=None, help="Optional output directory to initialize")
    parser.add_argument("--file", default=None, help="Optional single file path")
    parser.add_argument("--max-packet-words", type=int, default=3200, help="Target word budget per reading packet (2500-4000 recommended)")
    parser.add_argument("--max-packet-chars", type=int, default=22000, help="Hard character budget per reading packet")
    parser.add_argument("--include-references", action="store_true", help="Include locked references in reading packets")
    args = parser.parse_args()

    input_d = args.input_dir
    if args.file and args.input_dir == ".":
        input_d = os.path.dirname(os.path.abspath(args.file))

    index_case(
        input_dir=input_d,
        work_dir=args.work_dir,
        output_dir=args.output_dir,
        single_file=args.file,
        max_packet_words=args.max_packet_words,
        max_packet_chars=args.max_packet_chars,
        include_references=args.include_references,
    )


if __name__ == "__main__":
    main()
