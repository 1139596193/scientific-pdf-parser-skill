#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case_ingestor.py - Multi-Format Academic Manuscript Ingestion, Mode Routing,
Anomaly Detection, Academic Sentence Segmentation & Lossless Reconstruction.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import argparse
import hashlib
import html.parser
import json
import math
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(SCRIPT_DIR)
RESOURCES_DIR = os.path.join(SKILL_ROOT, "resources")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


# ----------------------------------------------------------------------
# 1. Resource Loaders & Language / Text Utilities
# ----------------------------------------------------------------------

def load_json_safe(path, default=None):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def load_style_profiles():
    return load_json_safe(os.path.join(RESOURCES_DIR, "style_profiles.json"), {"profiles": {}})


def detect_language(text):
    """Classify dominant language of text: en, zh, mixed, or jp_mixed."""
    if not text:
        return "en"
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    jp_kana = len(re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", text))
    en_letters = len(re.findall(r"[A-Za-z]", text))
    total = zh_chars + jp_kana + en_letters
    if total == 0:
        return "en"
    if jp_kana >= 2 or (jp_kana > 0 and jp_kana / max(total, 1) > 0.02):
        return "mixed_cjk_jp"
    if zh_chars > 0 and en_letters > 0:
        zh_ratio = zh_chars / total
        if 0.15 <= zh_ratio <= 0.70:
            return "mixed"
        return "zh" if zh_ratio > 0.70 else "en"
    return "zh" if zh_chars > en_letters else "en"


def estimate_tokens(text):
    if not text:
        return 0
    zh = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff]", text))
    ascii_words = len(re.findall(r"[A-Za-z0-9_]+", text))
    return int(zh * 1.5 + ascii_words * 1.35) + 1


def normalize_text_newlines(text):
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


# ----------------------------------------------------------------------
# 2. Task Mode, Intensity, Style & Scope Extraction
# ----------------------------------------------------------------------

_STYLE_ALIASES = [
    (r"\b(?:nature|science|cell|pnas)\b", "nature_science"),
    (r"\b(?:ieee|acm|icml|neurips|cvpr|sigcomm)\b", "ieee_acm"),
    (r"\b(?:elsevier|springer|wiley|mdpi)\b", "elsevier_general"),
    (r"\b(?:nejm|lancet|jama|bmj|medical|clinical|consort|strobe)\b", "medical_clinical"),
    (r"(?:中文核心|核心期刊|自然科学基金|国家基金|nsfc|学位论文|中文论文|zh_core_journal)", "zh_core_journal"),
    (r"\bnature_science\b", "nature_science"),
    (r"\bieee_acm\b", "ieee_acm"),
    (r"\belsevier_general\b", "elsevier_general"),
    (r"\bmedical_clinical\b", "medical_clinical"),
    (r"\bgeneral_academic\b", "general_academic"),
]


def parse_task_directives(task_text, case_data=None):
    """
    Determine execution_mode, edit_intensity, target_style, and scope_constraints
    from task.md and case.json.
    """
    case_data = case_data or {}
    combined_parts = []
    if task_text:
        combined_parts.append(task_text)
    for key in ("instruction", "prompt", "task", "task_description", "query", "mode", "style", "target_style", "journal", "intensity", "edit_intensity", "scope"):
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            combined_parts.append(val.strip())
        elif isinstance(val, dict):
            combined_parts.append(json.dumps(val, ensure_ascii=False))
    combined = "\n".join(combined_parts)
    low = combined.lower()

    # 1. Execution mode routing
    polish_intent = bool(re.search(
        r"(?:\b(?:polish|revise|proofread|copyedit|edit|rewrite|improve\s+(?:the\s+)?(?:language|writing|grammar|style|english)|language\s+editing|academic\s+editing)\b|润色|修改|改写|校对|精修|语言优化|降重|润饰)",
        low,
    ))
    bilingual_intent = bool(re.search(
        r"(?:\b(?:bilingual|zh[-_ ]?to[-_ ]?en|en[-_ ]?to[-_ ]?zh|parallel\s+polish)\b|双语|中英对照|英汉对照|中译英润色)",
        low,
    ))
    audit_only_intent = bool(re.search(
        r"(?:\b(?:audit[\s_-]*only|diagnose[\s_-]*only|review[\s_-]*only|do\s+not\s+(?:rewrite|edit|modify)|check\s+only)\b|仅审校|仅诊断|只审校|只检查|不要改写|不修改正文)",
        low,
    ))
    qa_intent = bool(re.search(
        r"(?:\b(?:what\s+is|who\s+are|explain|summarize|answer\s+the\s+question|how\s+many|does\s+the\s+paper)\b|请问|什么是|请解释|总结一下|回答问题|这篇论文讲了什么)",
        low,
    ))

    # Explicit field override from case.json
    explicit_mode = str(case_data.get("execution_mode", case_data.get("mode", ""))).strip().lower()
    if explicit_mode in ("polish", "bilingual_polish", "audit_only", "qa_guard"):
        execution_mode = explicit_mode
    elif audit_only_intent:
        execution_mode = "audit_only"
    elif bilingual_intent:
        execution_mode = "bilingual_polish"
    elif qa_intent and not polish_intent:
        execution_mode = "qa_guard"
    else:
        execution_mode = "polish"

    # 2. Edit intensity
    explicit_intensity = str(case_data.get("edit_intensity", case_data.get("intensity", ""))).strip().lower()
    if explicit_intensity in ("minimal", "standard", "deep"):
        edit_intensity = explicit_intensity
    elif re.search(r"(?:\b(?:minimal\s+edit|light\s+edit|conservative|minimal_preserve|lightly\s+polish)\b|最小编辑|微调|轻度润色|只改错)", low):
        edit_intensity = "minimal"
    elif re.search(r"(?:\b(?:deep\s+polish|heavy\s+edit|thorough\s+rewrite|comprehensive\s+polish)\b|深度润色|大幅改写|深度优化|全面重构)", low):
        edit_intensity = "deep"
    else:
        edit_intensity = "standard"

    # 3. Target style
    profiles = load_style_profiles().get("profiles", {})
    explicit_style = str(case_data.get("target_style", case_data.get("style", case_data.get("journal", "")))).strip()
    target_style = None
    if explicit_style in profiles:
        target_style = explicit_style
    else:
        search_target = (explicit_style + "\n" + combined).strip()
        for pat, prof_key in _STYLE_ALIASES:
            if re.search(pat, search_target, re.I):
                target_style = prof_key
                break

    style_unspecified = target_style is None
    if style_unspecified:
        target_style = "general_academic"

    # 4. Scope constraints
    scope = extract_scope_constraints(task_text, case_data)

    return {
        "execution_mode": execution_mode,
        "edit_intensity": edit_intensity,
        "target_style": target_style,
        "style_unspecified": style_unspecified,
        "scope_constraints": scope,
    }


def extract_scope_constraints(task_text, case_data=None):
    """Detect section, page, or reference inclusion/exclusion directives."""
    scope = {
        "is_constrained": False,
        "page_range": None,
        "target_sections": [],
        "excluded_sections": ["references", "bibliography", "acknowledgments", "acknowledgements"],
        "polish_references": False,
        "raw_directives": [],
    }
    parts = []
    if task_text:
        parts.append(task_text)
    if case_data:
        for k in ("scope", "target_sections", "pages", "instruction", "task"):
            if k in case_data:
                parts.append(json.dumps(case_data[k], ensure_ascii=False) if not isinstance(case_data[k], str) else case_data[k])
    combined = "\n".join(parts)
    if not combined:
        return scope

    p_match = re.search(r"(?:pages?|第)\s*([0-9]+)\s*(?:-|to|到|至)\s*([0-9]+)\s*(?:pages?|页)?", combined, re.I)
    if p_match:
        s_p, e_p = int(p_match.group(1)), int(p_match.group(2))
        scope["is_constrained"] = True
        scope["page_range"] = [s_p, e_p]
        scope["raw_directives"].append(f"page_range:{s_p}-{e_p}")

    sec_names = [
        ("abstract", r"(?:abstract|摘要)"),
        ("introduction", r"(?:introduction|引言|前言)"),
        ("related_work", r"(?:related\s+work|文献综述|相关工作)"),
        ("methods", r"(?:method(?:ology|s)?|materials?\s+and\s+methods?|实验方法|研究方法|方法)"),
        ("results", r"(?:results?(?:\s+and\s+discussion)?|实验结果|结果)"),
        ("discussion", r"(?:discussion|讨论)"),
        ("conclusion", r"(?:conclusions?|总结与展望|结论)"),
    ]
    for canon, pat in sec_names:
        if re.search(rf"(?:\b(?:only|just|focus\s+on|limit\s+to)\s+(?:the\s+)?{pat}\b|(?:仅|只|聚焦)(?:润色|修改|审校)?\s*{pat})", combined, re.I):
            scope["is_constrained"] = True
            if canon not in scope["target_sections"]:
                scope["target_sections"].append(canon)
                scope["raw_directives"].append(f"only_section:{canon}")

    if re.search(r"(?:polish|revise|edit|润色|修改)\s+(?:the\s+)?(?:references|bibliography|参考文献)", combined, re.I):
        scope["polish_references"] = True
        scope["excluded_sections"] = [s for s in scope["excluded_sections"] if s not in ("references", "bibliography")]
        scope["raw_directives"].append("polish_references=True")

    return scope


# ----------------------------------------------------------------------
# 3. Section Role Inference & Anomaly Detection
# ----------------------------------------------------------------------

def classify_section_role(title_text):
    t = (title_text or "").strip().lower()
    if not t:
        return "body"
    if re.search(r"\babstract\b|摘要", t):
        return "abstract"
    if re.search(r"\bintroduction\b|引言|前言|背景", t):
        return "introduction"
    if re.search(r"\brelated\s+work\b|\bliterature\s+review\b|相关工作|文献综述", t):
        return "related_work"
    if re.search(r"\b(?:method(?:ology|s)?|experimental|materials?\s+and\s+methods?|approach|framework|architecture)\b|方法|实验设计|模型构建", t):
        return "methods"
    if re.search(r"\bresults?\b|实验结果|结果分析", t):
        if "discussion" in t or "讨论" in t:
            return "results_discussion"
        return "results"
    if re.search(r"\bdiscussion\b|讨论", t):
        return "discussion"
    if re.search(r"\bconclusion|concluding|summary|结论|总结|展望", t):
        return "conclusion"
    if re.search(r"\b(?:references?|bibliography|works\s+cited)\b|参考文献", t):
        return "references"
    if re.search(r"\backnowledg(?:e)?ments?\b|致谢", t):
        return "acknowledgments"
    if re.search(r"\bappendix\b|\bsupplementary\b|附录|补充材料", t):
        return "appendix"
    return "body"


def infer_heading_level(heading_text, b_type=None):
    t = (heading_text or "").strip()
    if re.match(r"^(?:[0-9]+\.[0-9]+\.[0-9]+)\b", t):
        return 3
    if re.match(r"^(?:[0-9]+\.[0-9]+)\b", t):
        return 2
    if re.match(r"^(?:[0-9]+\.?\s+[A-Z\u4e00-\u9fff])", t):
        return 1
    if b_type == "heading_h1":
        return 1
    if b_type == "heading_h2":
        return 2
    if b_type == "heading_h3":
        return 3
    return 1


def detect_text_anomalies(full_text, case_data=None):
    """Detect encoding mojibake, CJK/JP mixing, placeholders, and budget risks."""
    anomalies = []
    if not full_text:
        return anomalies

    # 1. Replacement chars & mojibake
    rep_cnt = full_text.count("\ufffd")
    mojibake_hits = re.findall(r"(?:Ã[\x80-\xbf]|â€[™œ”˜“]|ï»¿|锘)", full_text)
    ctrl_hits = re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", full_text)
    if rep_cnt > 0 or mojibake_hits or ctrl_hits:
        anomalies.append({
            "code": "ENCODING_GARBLED",
            "severity": "HIGH",
            "detail": f"Detected replacement={rep_cnt}, mojibake_hits={len(mojibake_hits)}, control_chars={len(ctrl_hits)}.",
            "requires_human_confirmation": True,
        })

    # 2. Ligatures
    ligs = re.findall(r"[ﬁﬂﬀﬃﬄ]", full_text)
    if ligs:
        anomalies.append({
            "code": "PDF_LIGATURE_PRESENT",
            "severity": "LOW",
            "detail": f"Detected {len(ligs)} typographic ligatures (e.g., fi/fl); normalized during indexing.",
            "requires_human_confirmation": False,
        })

    # 3. Mixed CJK + Japanese Kana
    jp_kana = re.findall(r"[\u3040-\u309f\u30a0-\u30ff]", full_text)
    if jp_kana:
        anomalies.append({
            "code": "MIXED_CJK_JP",
            "severity": "MEDIUM",
            "detail": f"Detected {len(jp_kana)} Japanese Hiragana/Katakana characters mixed in text; verify whether intentional citation/term or encoding/language contamination.",
            "requires_human_confirmation": True,
        })

    # 4. Missing material / placeholders
    placeholder_pats = [
        r"\[(?:TBD|TODO|citation\s+needed|insert\s+[^\]]+|missing[^\]]*|待补充|待定|引用待补)\]",
        r"\?\?\?",
        r"\b(?:XX+|YY+)\s*%",
        r"\bTBD\b",
    ]
    placeholders = []
    for pat in placeholder_pats:
        for m in re.finditer(pat, full_text, re.I):
            placeholders.append(m.group(0))
    if placeholders:
        anomalies.append({
            "code": "MISSING_MATERIAL_PLACEHOLDER",
            "severity": "HIGH",
            "detail": f"Found {len(placeholders)} unresolved placeholder(s): {placeholders[:8]}. Must NOT fabricate missing data.",
            "spans": placeholders[:20],
            "requires_human_confirmation": True,
        })

    # 5. Oversized input warning
    est_tok = estimate_tokens(full_text)
    if est_tok > 14000:
        anomalies.append({
            "code": "OVERSIZED_INPUT_BUDGET_RISK",
            "severity": "MEDIUM",
            "detail": f"Estimated tokens={est_tok} (>14,000); sliced into incremental batches to respect 64-request / 900s budget.",
            "requires_human_confirmation": False,
        })

    return anomalies


# ----------------------------------------------------------------------
# 4. Academic-Aware Sentence Splitter & Lossless Block Schema
# ----------------------------------------------------------------------

_ABBR_PROTECTIONS = [
    (re.compile(r"\b(Figs?|Tabs?|Eqs?|Secs?|Refs?|Vols?|Nos?|Apps?|Algs?|Thms?|Props?|Defs?|Cors?|Dr|Prof|Mr|Mrs|Ms|St|Jr|Sr|vs|etc|approx|ca|al|cf|resp)\.", re.I), r"\1<DOT>"),
    (re.compile(r"\b(e\.g|i\.e)\.", re.I), lambda m: m.group(0).replace(".", "<DOT>")),
    (re.compile(r"(\d)\.(\d)"), r"\1<DOT>\2"),
]


def _protect_non_splitting_spans(text):
    """Mask decimals, academic abbreviations, inline math, LaTeX cmds, and URLs before splitting."""
    placeholders = []

    def stash(m):
        idx = len(placeholders)
        placeholders.append(m.group(0))
        return f"\x00SPAN{idx}\x00"

    masked = text
    # Protect display/inline math, LaTeX cite/ref/url, URLs, backtick code
    for pat in (
        re.compile(r"\$\$[\s\S]*?\$\$"),
        re.compile(r"\\\[[\s\S]*?\\\]"),
        re.compile(r"\\\([\s\S]*?\\\)"),
        re.compile(r"(?<!\\)\$[^$\n]+?(?<!\\)\$"),
        re.compile(r"\\(?:cite[tp]?|Cite[tp]?|textcite|parencite|autocite|eq|fig|tab|sec|auto|v|c|C)?ref\*?(?:\[[^\]]*\])?\{[^}]+\}"),
        re.compile(r"https?://[^\s\])>]+"),
        re.compile(r"`[^`\n]+`"),
    ):
        masked = pat.sub(stash, masked)

    # Ellipsis
    masked = masked.replace("...", "<ELLIPSIS>")

    # Abbreviations & decimals
    for pat, repl in _ABBR_PROTECTIONS:
        masked = pat.sub(repl, masked)

    # Initials like "J. K. Smith" or "A. Einstein"
    masked = re.sub(r"\b([A-Z])\.(?=\s+[A-Z])", r"\1<DOT>", masked)
    # Numbered heading prefixes like "1. Introduction" or "# 1.2 Methods"
    masked = re.sub(r"^(\s*(?:#{1,6}\s+)?\d+(?:\.\d+)*)\.(?=\s+)", r"\1<DOT>", masked)
    return masked, placeholders


def _restore_spans(text, placeholders):
    out = text.replace("<DOT>", ".").replace("<ELLIPSIS>", "...")
    for idx, orig in enumerate(placeholders):
        out = out.replace(f"\x00SPAN{idx}\x00", orig)
    return out


def split_academic_sentences_lossless(block_text):
    """
    Split a paragraph/block into sentences while preserving exact whitespace/separators
    so concatenating slots yields the exact original block_text.
    Returns list of dicts: {prefix_ws, core_text, suffix_ws, separator_after}.
    """
    if not block_text:
        return []

    masked, placeholders = _protect_non_splitting_spans(block_text)

    # Split after sentence-ending punctuation (. ! ? 。 ！ ？) optionally followed by closing quotes/parens/citations
    # Only split on ASCII .!? when followed by whitespace+start or end of string; split on CJK 。！？ directly (or with trailing quote/paren)
    boundary_pat = re.compile(
        r"(?:(?<=[.!?])(?:['\"\)\]\u201d\u2019]*)(?=\s|$)|(?<=[。！？])(?:['\"\)\]\u201d\u2019\u300d\u300f]*))"
    )

    pieces = []
    last = 0
    for m in boundary_pat.finditer(masked):
        end = m.end()
        if end > last:
            seg = masked[last:end]
            pieces.append(seg)
            last = end
    if last < len(masked):
        pieces.append(masked[last:])

    # Merge any piece that is pure whitespace/closing punct into previous piece's trailing separator
    raw_slots = []
    for seg in pieces:
        restored = _restore_spans(seg, placeholders)
        if not restored.strip() and raw_slots:
            raw_slots[-1]["separator_after"] += restored
        else:
            lead_len = len(restored) - len(restored.lstrip())
            trail_len = len(restored) - len(restored.rstrip())
            lead_ws = restored[:lead_len]
            trail_ws = restored[len(restored) - trail_len:] if trail_len > 0 else ""
            core = restored[lead_len:len(restored) - trail_len if trail_len > 0 else len(restored)]
            if raw_slots and lead_ws:
                raw_slots[-1]["separator_after"] += lead_ws
                lead_ws = ""
            raw_slots.append({
                "prefix_markup": lead_ws,
                "core_text": core,
                "suffix_markup": trail_ws,
                "separator_after": "",
            })

    return [s for s in raw_slots if s["core_text"] or s["prefix_markup"] or s["separator_after"]]


# ----------------------------------------------------------------------
# 5. Format-Specific Parsers (DOCX, LaTeX, MD/TXT, HTML, PDF, JSON)
# ----------------------------------------------------------------------

def _make_block(block_id, block_type, section_role, section_title, body_text,
                block_prefix="", block_suffix="", block_separator="\n\n",
                page_span=None, in_scope=True, protected=False, extra=None):
    return {
        "block_id": block_id,
        "block_type": block_type,
        "section_role": section_role,
        "section_title": section_title,
        "block_prefix": block_prefix,
        "body_text": body_text,
        "block_suffix": block_suffix,
        "block_separator": block_separator,
        "page_span": page_span or [1, 1],
        "in_scope": in_scope,
        "protected": protected,
        "extra": extra or {},
    }


def parse_markdown_or_text(raw_text, is_markdown=True):
    """Parse Markdown or plain text into lossless blocks preserving exact separators."""
    text = normalize_text_newlines(raw_text)
    if not text.strip():
        return []

    blocks = []
    b_seq = 1
    current_role = "body"
    current_title = "Body"

    # Split preserving exact blank-line separators
    lines = text.splitlines(keepends=True)
    i = 0
    n = len(lines)

    def next_bid():
        nonlocal b_seq
        bid = f"B{b_seq:04d}"
        b_seq += 1
        return bid

    while i < n:
        ln = lines[i]
        # Blank line(s): attach to previous block separator or leading prefix
        if not ln.strip():
            ws_buf = []
            while i < n and not lines[i].strip():
                ws_buf.append(lines[i])
                i += 1
            ws_str = "".join(ws_buf)
            if blocks:
                blocks[-1]["block_separator"] += ws_str
            else:
                blocks.append(_make_block(next_bid(), "whitespace", "front", "Front", "", block_prefix="", block_suffix="", block_separator=ws_str, in_scope=False, protected=True))
            continue

        # Fenced code block
        if is_markdown and re.match(r"^\s*(?:```|~~~)", ln):
            fence = ln.strip()[:3]
            buf = [ln]
            i += 1
            while i < n:
                buf.append(lines[i])
                if lines[i].strip().startswith(fence):
                    i += 1
                    break
                i += 1
            blocks.append(_make_block(
                next_bid(), "code_block", current_role, current_title,
                "".join(buf), block_separator="", in_scope=False, protected=True
            ))
            continue

        # Display math block $$...$$
        if ln.strip().startswith("$$"):
            buf = [ln]
            i += 1
            if not (ln.strip().endswith("$$") and len(ln.strip()) > 2):
                while i < n:
                    buf.append(lines[i])
                    if lines[i].strip().endswith("$$"):
                        i += 1
                        break
                    i += 1
            blocks.append(_make_block(
                next_bid(), "math_display", current_role, current_title,
                "".join(buf), block_separator="", in_scope=False, protected=True
            ))
            continue

        # Markdown heading
        hm = re.match(r"^(\s{0,3}#{1,6}\s+)(.*?)(\s*\n?)$", ln) if is_markdown else None
        if hm:
            prefix, title_core, suffix = hm.group(1), hm.group(2), hm.group(3)
            current_title = title_core.strip()
            current_role = classify_section_role(current_title)
            blocks.append(_make_block(
                next_bid(), "heading", current_role, current_title,
                title_core, block_prefix=prefix, block_suffix=suffix, block_separator="",
                in_scope=True, protected=False
            ))
            i += 1
            continue

        # Markdown list item or blockquote line-by-line paragraph
        lm = re.match(r"^(\s*(?:[-*+]|\d+\.|>)\s+)(.*?)(\n?)$", ln) if is_markdown else None
        if lm:
            prefix, item_core, suffix = lm.group(1), lm.group(2), lm.group(3)
            blocks.append(_make_block(
                next_bid(), "list_item", current_role, current_title,
                item_core, block_prefix=prefix, block_suffix=suffix, block_separator="",
                in_scope=True, protected=False
            ))
            i += 1
            continue

        # Markdown table block
        if is_markdown and "|" in ln and i + 1 < n and re.match(r"^\s*\|?[\s:-]+\|[\s|:-]*$", lines[i + 1]):
            buf = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                buf.append(lines[i])
                i += 1
            blocks.append(_make_block(
                next_bid(), "table", current_role, current_title,
                "".join(buf), block_separator="", in_scope=False, protected=True
            ))
            continue

        # Regular paragraph: consume until blank line, heading, fence, list, or $$
        buf = []
        while i < n and lines[i].strip():
            cur = lines[i]
            if is_markdown and (
                re.match(r"^\s{0,3}#{1,6}\s+", cur)
                or re.match(r"^\s*(?:```|~~~)", cur)
                or cur.strip().startswith("$$")
                or re.match(r"^\s*(?:[-*+]|\d+\.|>)\s+", cur)
            ):
                break
            buf.append(cur)
            i += 1
        para_raw = "".join(buf)
        trail_nl = "\n" if para_raw.endswith("\n") else ""
        para_body = para_raw[:-1] if trail_nl else para_raw
        # Check if single-line standalone title like "Abstract" or "1. Introduction"
        if not is_markdown and len(para_body.splitlines()) == 1 and len(para_body) < 90 and re.match(r"^(?:Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|References?|Acknowledgments?|\d+(?:\.\d+)*\s+[A-Z\u4e00-\u9fff]|摘要|引言|方法|结果|讨论|结论|参考文献)\b", para_body.strip(), re.I):
            current_title = para_body.strip()
            current_role = classify_section_role(current_title)
            b_type = "heading"
        else:
            b_type = "paragraph"
        blocks.append(_make_block(
            next_bid(), b_type, current_role, current_title,
            para_body, block_prefix="", block_suffix=trail_nl, block_separator="",
            in_scope=True, protected=False
        ))

    return blocks


def parse_latex(raw_text):
    """Parse LaTeX manuscript into blocks, protecting preamble, math environments, and bib."""
    text = normalize_text_newlines(raw_text)
    if not text.strip():
        return []

    blocks = []
    b_seq = 1
    current_role = "body"
    current_title = "Body"

    def next_bid():
        nonlocal b_seq
        bid = f"B{b_seq:04d}"
        b_seq += 1
        return bid

    lines = text.splitlines(keepends=True)
    i = 0
    n = len(lines)
    in_preamble = bool(re.search(r"\\begin\{document\}", text))
    protected_envs = {"equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*", "verbatim", "lstlisting", "minted", "thebibliography", "tabular", "tabular*"}

    while i < n:
        ln = lines[i]
        if in_preamble:
            buf = []
            while i < n:
                buf.append(lines[i])
                if re.search(r"\\begin\{document\}", lines[i]):
                    i += 1
                    in_preamble = False
                    break
                i += 1
            blocks.append(_make_block(next_bid(), "latex_preamble", "front", "Preamble", "".join(buf), block_separator="", in_scope=False, protected=True))
            continue

        if re.search(r"\\end\{document\}", ln):
            blocks.append(_make_block(next_bid(), "latex_epilogue", "back", "End", "".join(lines[i:]), block_separator="", in_scope=False, protected=True))
            break

        if not ln.strip():
            ws = []
            while i < n and not lines[i].strip():
                ws.append(lines[i])
                i += 1
            if blocks:
                blocks[-1]["block_separator"] += "".join(ws)
            else:
                blocks.append(_make_block(next_bid(), "whitespace", "front", "Front", "", block_separator="".join(ws), in_scope=False, protected=True))
            continue

        # Comment line
        if re.match(r"^\s*%", ln):
            buf = []
            while i < n and re.match(r"^\s*%", lines[i]):
                buf.append(lines[i])
                i += 1
            blocks.append(_make_block(next_bid(), "latex_comment", current_role, current_title, "".join(buf), block_separator="", in_scope=False, protected=True))
            continue

        # Section command
        sm = re.match(r"^(\s*\\(?:sub)*section\*?\{)([^}]+)(\}.*\n?)$", ln)
        if sm:
            pre, title_core, suf = sm.group(1), sm.group(2), sm.group(3)
            current_title = title_core.strip()
            current_role = classify_section_role(current_title)
            blocks.append(_make_block(next_bid(), "heading", current_role, current_title, title_core, block_prefix=pre, block_suffix=suf, block_separator="", in_scope=True, protected=False))
            i += 1
            continue

        # \begin{abstract} ... \end{abstract}
        if re.match(r"^\s*\\begin\{abstract\}", ln):
            current_role = "abstract"
            current_title = "Abstract"
            blocks.append(_make_block(next_bid(), "latex_env_marker", current_role, current_title, ln, block_separator="", in_scope=False, protected=True))
            i += 1
            continue
        if re.match(r"^\s*\\end\{abstract\}", ln):
            blocks.append(_make_block(next_bid(), "latex_env_marker", current_role, current_title, ln, block_separator="", in_scope=False, protected=True))
            current_role = "body"
            current_title = "Body"
            i += 1
            continue

        # Protected math / table / bib environments
        em = re.match(r"^\s*\\begin\{([^}]+)\}", ln)
        if em and em.group(1).strip() in protected_envs:
            env = em.group(1).strip()
            buf = [ln]
            i += 1
            end_pat = re.compile(rf"\\end\{{{re.escape(env)}\}}")
            while i < n:
                buf.append(lines[i])
                if end_pat.search(lines[i]):
                    i += 1
                    break
                i += 1
            b_type = "references" if env == "thebibliography" else "math_display"
            blocks.append(_make_block(next_bid(), b_type, current_role, current_title, "".join(buf), block_separator="", in_scope=False, protected=True))
            continue

        # Display math \[ ... \] or $$ ... $$
        if re.match(r"^\s*(?:\\\[|\$\$)", ln):
            closer = r"\\\]" if ln.lstrip().startswith(r"\[") else r"\$\$"
            buf = [ln]
            i += 1
            if not re.search(closer, ln.strip()[2:]):
                while i < n:
                    buf.append(lines[i])
                    if re.search(closer, lines[i]):
                        i += 1
                        break
                    i += 1
            blocks.append(_make_block(next_bid(), "math_display", current_role, current_title, "".join(buf), block_separator="", in_scope=False, protected=True))
            continue

        # Prose paragraph
        buf = []
        while i < n and lines[i].strip():
            cur = lines[i]
            if (
                re.match(r"^\s*%", cur)
                or re.match(r"^\s*\\(?:sub)*section\*?\{", cur)
                or re.match(r"^\s*\\(?:begin|end)\{", cur)
                or re.match(r"^\s*(?:\\\[|\$\$)", cur)
                or re.search(r"\\end\{document\}", cur)
            ):
                break
            buf.append(cur)
            i += 1
        para_raw = "".join(buf)
        trail_nl = "\n" if para_raw.endswith("\n") else ""
        para_body = para_raw[:-1] if trail_nl else para_raw
        blocks.append(_make_block(next_bid(), "paragraph", current_role, current_title, para_body, block_suffix=trail_nl, block_separator="", in_scope=True, protected=False))

    return blocks


def parse_docx(docx_path):
    """
    Parse DOCX paragraphs, tables, and optional comments using pure stdlib zipfile + ElementTree.
    Zero python-docx dependency.
    """
    blocks = []
    b_seq = 1
    current_role = "body"
    current_title = "Body"
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    W = f"{{{ns['w']}}}"

    def next_bid():
        nonlocal b_seq
        bid = f"B{b_seq:04d}"
        b_seq += 1
        return bid

    comments_map = {}
    try:
        with zipfile.ZipFile(docx_path, "r") as z:
            if "word/comments.xml" in z.namelist():
                try:
                    c_tree = ET.fromstring(z.read("word/comments.xml"))
                    for c_el in c_tree.findall(f".//{W}comment"):
                        cid = c_el.attrib.get(f"{W}id", "")
                        ctext = "".join(t.text for t in c_el.iter(f"{W}t") if t.text)
                        if cid:
                            comments_map[cid] = ctext
                except Exception:
                    pass

            doc_xml = z.read("word/document.xml")
            root = ET.fromstring(doc_xml)
            body = root.find(f"{W}body")
            if body is None:
                body = root

            for child in body:
                tag = child.tag.split("}")[-1]
                if tag == "p":
                    # Collect visible text (excluding w:delText inside w:del)
                    parts = []
                    has_tracked_changes = False
                    for el in child.iter():
                        etag = el.tag.split("}")[-1]
                        if etag in ("ins", "del"):
                            has_tracked_changes = True
                        elif etag == "t" and el.text:
                            parts.append(el.text)
                    p_text = normalize_text_newlines("".join(parts)).strip()
                    if not p_text:
                        continue
                    p_style = child.find(f".//{W}pStyle")
                    style_val = p_style.attrib.get(f"{W}val", "") if p_style is not None else ""
                    is_heading = ("heading" in style_val.lower()) or (style_val in ("1", "2", "3", "Title", "Subtitle"))
                    if not is_heading and len(p_text) < 80 and re.match(r"^(?:Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|References?|摘要|引言|方法|结果|讨论|结论|参考文献)\b", p_text, re.I):
                        is_heading = True
                    if is_heading:
                        current_title = p_text
                        current_role = classify_section_role(p_text)
                        b_type = "heading"
                    else:
                        b_type = "paragraph"

                    ref_cids = [cr.attrib.get(f"{W}id", "") for cr in child.findall(f".//{W}commentReference")]
                    attached_comments = [comments_map[c] for c in ref_cids if c in comments_map]
                    blocks.append(_make_block(
                        next_bid(), b_type, current_role, current_title, p_text,
                        block_separator="\n\n", in_scope=True, protected=False,
                        extra={"docx_style": style_val, "has_tracked_changes": has_tracked_changes, "existing_comments": attached_comments}
                    ))
                elif tag == "tbl":
                    rows_txt = []
                    for tr in child.findall(f"./{W}tr"):
                        cells = []
                        for tc in tr.findall(f"./{W}tc"):
                            cell_t = "".join(t.text for t in tc.iter(f"{W}t") if t.text).strip()
                            cells.append(cell_t)
                        rows_txt.append(" | ".join(cells))
                    tbl_text = normalize_text_newlines("\n".join(rows_txt)).strip()
                    if tbl_text:
                        blocks.append(_make_block(
                            next_bid(), "table", current_role, current_title, tbl_text,
                            block_separator="\n\n", in_scope=False, protected=True,
                            extra={"row_count": len(rows_txt)}
                        ))
    except Exception as e:
        blocks.append(_make_block(
            "B0001", "unreadable_asset", "error", "DOCX Parse Error",
            f"[DOCX_PARSE_ERROR] {e}", block_separator="", in_scope=False, protected=True,
            extra={"error": str(e)}
        ))
    return blocks


class _SimpleHTMLParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self._cur_tag = None
        self._buf = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("script", "style"):
            self._skip = True
        elif t in ("h1", "h2", "h3", "h4", "p", "li"):
            self._cur_tag = t
            self._buf = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style"):
            self._skip = False
        elif t == self._cur_tag:
            txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if txt:
                self.items.append((t, txt))
            self._cur_tag = None
            self._buf = []

    def handle_data(self, data):
        if not self._skip and self._cur_tag:
            self._buf.append(data)


def parse_html(raw_text):
    parser = _SimpleHTMLParser()
    parser.feed(raw_text or "")
    blocks = []
    b_seq = 1
    current_role, current_title = "body", "Body"
    for tag, txt in parser.items:
        bid = f"B{b_seq:04d}"
        b_seq += 1
        if tag in ("h1", "h2", "h3", "h4"):
            current_title = txt
            current_role = classify_section_role(txt)
            b_type = "heading"
        else:
            b_type = "paragraph"
        blocks.append(_make_block(bid, b_type, current_role, current_title, txt, block_separator="\n\n"))
    return blocks


def parse_pdf(pdf_path, work_dir):
    """Ingest PDF via pdf_engine (layout_parser + pdf_diagnose) with pymupdf fallback."""
    blocks = []
    diag = {}
    try:
        from pdf_engine import pdf_diagnose, layout_parser
        os.makedirs(work_dir, exist_ok=True)
        diag = pdf_diagnose.diagnose_pdf(pdf_path)
        diag_path = os.path.join(work_dir, "diagnosis.json")
        with open(diag_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(diag, f, ensure_ascii=False, indent=2)
            f.write("\n")
        structured, _ = layout_parser.parse_layout(pdf_path, diag_path)
        raw_blocks = structured.get("blocks", [])
        current_role = "body"
        current_title = "Body"
        b_seq = 1
        for el in raw_blocks:
            etype = el.get("type", "paragraph")
            text = normalize_text_newlines(el.get("text") or "").strip()
            if not text:
                continue
            p_span = el.get("page_span") or [el.get("page", 1), el.get("page", 1)]
            if isinstance(p_span, int):
                p_span = [p_span, p_span]
            if etype.startswith("heading"):
                current_title = text
                current_role = classify_section_role(text)
                b_type = "heading"
                in_scope, protected = True, False
            elif etype == "abstract":
                current_role = "abstract"
                current_title = "Abstract"
                b_type = "paragraph"
                in_scope, protected = True, False
            elif etype == "reference_entry":
                current_role = "references"
                current_title = "References"
                b_type = "reference_entry"
                in_scope, protected = False, True
            elif etype in ("table_content", "table", "figure", "equation", "formula"):
                b_type = etype
                in_scope, protected = False, True
            else:
                b_type = "paragraph"
                in_scope, protected = True, False
            blocks.append(_make_block(
                f"B{b_seq:04d}", b_type, current_role, current_title, text,
                block_separator="\n\n", page_span=p_span, in_scope=in_scope, protected=protected
            ))
            b_seq += 1
        return blocks, diag
    except Exception as e:
        diag["pdf_engine_error"] = str(e)

    # Lightweight pymupdf fallback
    try:
        import pymupdf  # noqa: dynamic import
        doc = pymupdf.open(pdf_path)
        b_seq = 1
        current_role, current_title = "body", "Body"
        for pno, page in enumerate(doc, 1):
            txt = normalize_text_newlines(page.get_text("text")).strip()
            if not txt:
                continue
            for para in re.split(r"\n{2,}", txt):
                p_clean = re.sub(r"(?<!\n)\n(?!\n)", " ", para).strip()
                if not p_clean:
                    continue
                if len(p_clean) < 90 and re.match(r"^(?:Abstract|Introduction|Methods?|Results?|Discussion|Conclusion|References?|\d+(?:\.\d+)*\s+[A-Z\u4e00-\u9fff]|摘要|引言|方法|结果|讨论|结论|参考文献)\b", p_clean, re.I):
                    current_title = p_clean
                    current_role = classify_section_role(p_clean)
                    b_type = "heading"
                else:
                    b_type = "paragraph"
                blocks.append(_make_block(f"B{b_seq:04d}", b_type, current_role, current_title, p_clean, block_separator="\n\n", page_span=[pno, pno]))
                b_seq += 1
    except Exception as e2:
        blocks.append(_make_block("B0001", "unreadable_asset", "error", "PDF Unreadable", f"[PDF_PARSE_ERROR] {e2}", block_separator="", in_scope=False, protected=True))
    return blocks, diag


# ----------------------------------------------------------------------
# 6. Reconstruction Engine (Lossless Round-Trip)
# ----------------------------------------------------------------------

def reconstruct_document(blocks, sentence_map=None):
    """
    Reassemble full document from ingested blocks and sentence_map ({sentence_id: polished_text}).
    When sentence_map is None or maps every sentence_id to its exact source_text,
    output matches normalized original text 100%.
    """
    sentence_map = sentence_map or {}
    out_parts = []
    for blk in blocks:
        out_parts.append(blk.get("block_prefix", ""))
        slots = blk.get("sentence_slots")
        if slots:
            for slot in slots:
                sid = slot.get("sentence_id")
                txt = sentence_map.get(sid, slot.get("source_text", "")) if sid else slot.get("source_text", "")
                out_parts.append(slot.get("prefix_markup", ""))
                out_parts.append(txt)
                out_parts.append(slot.get("suffix_markup", ""))
                out_parts.append(slot.get("separator_after", ""))
        else:
            out_parts.append(blk.get("body_text", ""))
        out_parts.append(blk.get("block_suffix", ""))
        out_parts.append(blk.get("block_separator", ""))
    return "".join(out_parts)


# ----------------------------------------------------------------------
# 7. Case Discovery & Main Ingestion Pipeline
# ----------------------------------------------------------------------

def _is_glossary_filename(fn_low):
    return any(k in fn_low for k in ("glossary", "terminology", "terms", "dict", "术语", "词表"))


def _extract_inline_text_from_case(case_data):
    for key in ("source_text", "manuscript_text", "draft_text", "text", "manuscript", "content", "document"):
        val = case_data.get(key)
        if isinstance(val, str) and len(val.strip()) > 10:
            return val, key
    return None, None


def _is_in_scope(blk, sent_text, scope):
    if blk.get("protected") or not blk.get("in_scope", True):
        return False
    role = blk.get("section_role", "body")
    if role in scope.get("excluded_sections", []):
        return False
    if scope.get("is_constrained"):
        pr = scope.get("page_range")
        if pr:
            ps = blk.get("page_span", [1, 1])
            if ps[1] < pr[0] or ps[0] > pr[1]:
                return False
        tsecs = scope.get("target_sections") or []
        if tsecs:
            sec_title_low = (blk.get("section_title") or "").lower()
            if role not in tsecs and not any(ts in sec_title_low for ts in tsecs):
                return False
    return True


def build_adaptive_batches(
    sentences,
    target_max_batches=16,
    max_batch_tokens=2800,
    min_batch_size=6,
    max_batch_size=36,
    locked_terms=None,
    target_style="general_academic",
    include_enrichment=True,
):
    """
    Pack only in-scope prose sentences into budget-aware reading packets.
    Automatically scales sentence count per batch so long manuscripts (700-1200 sentences)
    stay within <=24 batches unless token volume forces more splits, in which case
    LONG_DOCUMENT_BUDGET_ADVISORY is emitted without dropping any sentence.
    """
    in_scope_sents = [
        s for s in sentences
        if s.get("in_scope") and s.get("section_role") not in ("references", "math_display")
    ]
    if not in_scope_sents:
        return [], []

    n_in = len(in_scope_sents)
    total_toks = sum(estimate_tokens(s.get("source_text", "")) for s in in_scope_sents)
    ideal_count = max(min_batch_size, math.ceil(n_in / max(1, int(target_max_batches))))
    # Allow scaling above max_batch_size up to ceil(n_in/24) when total tokens fit comfortably in 24 batches
    if total_toks <= 24 * max_batch_tokens:
        eff_max_size = max(max_batch_size, math.ceil(n_in / 24))
    else:
        eff_max_size = max_batch_size
    target_sents_per_batch = max(min_batch_size, min(eff_max_size, ideal_count))

    # Lazy imports to keep top-level strictly stdlib
    ep_mod = None
    lint_mod = None
    if include_enrichment:
        try:
            import epistemic_entity_guard as ep_mod
        except Exception:
            ep_mod = None
        try:
            import style_linter as lint_mod
        except Exception:
            lint_mod = None

    locked_terms = locked_terms or []
    batches = []
    cur_items = []
    cur_tokens = 0

    def _compact_packet_item(s):
        src = s.get("source_text", "")
        item = {
            "sentence_id": s["sentence_id"],
            "section_role": s.get("section_role", "body"),
            "source_text": src,
        }
        if ep_mod is not None:
            inv = ep_mod.extract_invariants(src)
            ep = ep_mod.analyze_epistemic_profile(src, lang=s.get("language"))
            item["protected_invariants"] = {
                "numbers_units": [x["raw"] for x in inv["numbers_units"]],
                "citations": [x["raw"] for x in inv["citations"]],
                "cross_refs": [x["raw"] for x in inv["cross_refs"]],
                "inline_math_latex": inv["inline_math_latex"],
            }
            item["epistemic_tier"] = {
                "max_tier": ep["max_tier"],
                "negation_count": ep["negation_count"],
                "scope_bounds": ep["scope_bounds"],
            }
        else:
            item["protected_invariants"] = {}
            item["epistemic_tier"] = {}

        matched_terms = []
        src_low = src.lower()
        for t in locked_terms:
            st = (t.get("source_term") or "").strip()
            pt = (t.get("preferred_term") or "").strip()
            if (st and st.lower() in src_low) or (pt and pt.lower() in src_low):
                matched_terms.append({
                    "source_term": st,
                    "preferred_term": pt,
                    "forbidden_terms": t.get("forbidden_terms", []),
                    "do_not_edit": bool(t.get("do_not_edit")),
                    "abbr": t.get("abbr", ""),
                })
        item["locked_terms"] = matched_terms

        if lint_mod is not None:
            pre_hints = lint_mod.lint_sentence(src, lang=s.get("language"), target_style=target_style, mode="pre")
            item["pre_lint_hints"] = [
                {"code": h["code"], "severity": h["severity"], "hint": h.get("repair_hint", "")}
                for h in pre_hints
            ]
        else:
            item["pre_lint_hints"] = []
        return item

    for s in in_scope_sents:
        s_tok = estimate_tokens(s.get("source_text", ""))
        if cur_items and (len(cur_items) >= target_sents_per_batch or (cur_tokens + s_tok > max_batch_tokens)):
            batches.append(cur_items)
            cur_items = []
            cur_tokens = 0
        cur_items.append(_compact_packet_item(s))
        cur_tokens += s_tok

    if cur_items:
        batches.append(cur_items)

    advisories = []
    if len(batches) > 24:
        advisories.append({
            "code": "LONG_DOCUMENT_BUDGET_ADVISORY",
            "severity": "MEDIUM",
            "detail": f"Document has {n_in} in-scope sentences ({total_toks} est. tokens), requiring {len(batches)} batches under max_batch_tokens={max_batch_tokens}; prioritize high-impact sections or checkpoint early.",
            "requires_human_confirmation": False,
        })
    return batches, advisories


def ingest_case(
    input_path,
    work_dir,
    task_override=None,
    glossary_override=None,
    style_override=None,
    intensity_override=None,
    mode_override=None,
    target_max_batches=16,
    max_batch_tokens=2800,
    batch_size=None,
):
    """
    Ingest /input directory or single file, parse primary material, detect modes/anomalies/glossaries,
    segment sentences, slice adaptive reading packets, and save normalized manifests to work_dir.
    """
    os.makedirs(work_dir, exist_ok=True)
    input_abs = os.path.abspath(input_path)
    single_source_file = None
    if os.path.isfile(input_abs):
        input_dir = os.path.dirname(input_abs)
        if os.path.basename(input_abs).lower() == "case.json":
            case_path = input_abs
        else:
            case_path = os.path.join(input_dir, "case.json")
            single_source_file = input_abs
    else:
        input_dir = input_abs
        case_path = os.path.join(input_dir, "case.json")

    task_path = os.path.abspath(task_override) if task_override else os.path.join(input_dir, "task.md")
    case_data = load_json_safe(case_path, {})
    task_text = ""
    if os.path.exists(task_path):
        with open(task_path, "r", encoding="utf-8", errors="replace") as f:
            task_text = f.read()

    directives = parse_task_directives(task_text, case_data)
    if mode_override and mode_override != "auto":
        directives["execution_mode"] = mode_override
    if intensity_override:
        directives["edit_intensity"] = intensity_override
    if style_override:
        directives["target_style"] = style_override
        directives["style_unspecified"] = False

    # Scan input files
    source_files = []
    glossary_files = []
    if glossary_override and os.path.exists(glossary_override):
        glossary_files.append(os.path.abspath(glossary_override))

    if single_source_file:
        ext = os.path.splitext(single_source_file.lower())[1]
        source_files.append((single_source_file, ext))
    elif os.path.isdir(input_dir):
        for root, _, files in os.walk(input_dir):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, input_dir)
                fn_low = fn.lower()
                if rel in ("case.json", "task.md"):
                    continue
                if task_override and os.path.abspath(full) == os.path.abspath(task_override):
                    continue
                ext = os.path.splitext(fn_low)[1]
                if _is_glossary_filename(fn_low) and ext in (".csv", ".tsv", ".json", ".md", ".txt"):
                    if os.path.abspath(full) not in glossary_files:
                        glossary_files.append(os.path.abspath(full))
                elif ext in (".pdf", ".docx", ".tex", ".latex", ".md", ".txt", ".html", ".htm", ".json"):
                    source_files.append((full, ext))

    # Select primary document
    ext_priority = {".tex": 0, ".latex": 0, ".docx": 1, ".md": 2, ".txt": 3, ".html": 4, ".htm": 4, ".pdf": 5, ".json": 6}
    source_files.sort(key=lambda x: (ext_priority.get(x[1], 99), x[0]))

    blocks = []
    source_format = "none"
    source_path_used = None
    pdf_diag = {}

    if source_files:
        fpath, ext = source_files[0]
        source_path_used = os.path.relpath(fpath, input_dir)
        if ext == ".pdf":
            source_format = "pdf"
            blocks, pdf_diag = parse_pdf(fpath, os.path.join(work_dir, "pdf_diag"))
        elif ext == ".docx":
            source_format = "docx"
            blocks = parse_docx(fpath)
        elif ext in (".tex", ".latex"):
            source_format = "latex"
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                blocks = parse_latex(f.read())
        elif ext in (".html", ".htm"):
            source_format = "html"
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                blocks = parse_html(f.read())
        elif ext == ".json":
            source_format = "json_text"
            jdata = load_json_safe(fpath, {})
            inline_txt, _ = _extract_inline_text_from_case(jdata)
            if inline_txt:
                blocks = parse_markdown_or_text(inline_txt, is_markdown=True)
        else:
            source_format = "markdown" if ext == ".md" else "txt"
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                blocks = parse_markdown_or_text(f.read(), is_markdown=(ext == ".md"))
    else:
        inline_txt, inline_key = _extract_inline_text_from_case(case_data)
        if inline_txt:
            source_format = f"case_json:{inline_key}"
            source_path_used = "case.json"
            if "\\section{" in inline_txt or "\\begin{document}" in inline_txt:
                blocks = parse_latex(inline_txt)
                source_format = "latex"
            else:
                blocks = parse_markdown_or_text(inline_txt, is_markdown=True)

    # Segment sentences across blocks & build stable sentence table
    sentences = []
    s_seq = 1
    char_cursor = 0
    scope = directives["scope_constraints"]
    bilingual_pair_mode = directives["execution_mode"] == "bilingual_polish"

    for blk in blocks:
        char_cursor += len(blk.get("block_prefix", ""))
        if blk.get("protected") or blk.get("block_type") in ("whitespace", "code_block", "math_display", "table", "latex_preamble", "latex_epilogue", "latex_comment", "latex_env_marker", "unreadable_asset"):
            char_cursor += len(blk.get("body_text", "")) + len(blk.get("block_suffix", "")) + len(blk.get("block_separator", ""))
            continue

        body_txt = blk.get("body_text", "")
        if blk.get("block_type") == "heading" and body_txt.strip():
            lead_len = len(body_txt) - len(body_txt.lstrip())
            trail_len = len(body_txt) - len(body_txt.rstrip())
            core_h = body_txt[lead_len:len(body_txt) - trail_len if trail_len > 0 else len(body_txt)]
            slots = [{
                "prefix_markup": body_txt[:lead_len],
                "core_text": core_h,
                "suffix_markup": body_txt[len(body_txt) - trail_len:] if trail_len > 0 else "",
                "separator_after": "",
            }]
        else:
            slots = split_academic_sentences_lossless(body_txt)
        enriched_slots = []
        for slot in slots:
            char_cursor += len(slot["prefix_markup"])
            core = slot["core_text"]
            start_c = char_cursor
            end_c = start_c + len(core)
            char_cursor = end_c + len(slot["suffix_markup"]) + len(slot["separator_after"])

            if not core.strip():
                enriched_slots.append({
                    "sentence_id": None,
                    "source_text": core,
                    "prefix_markup": slot["prefix_markup"],
                    "suffix_markup": slot["suffix_markup"],
                    "separator_after": slot["separator_after"],
                })
                continue

            sid = f"S{s_seq:03d}"
            s_seq += 1
            in_scope = _is_in_scope(blk, core, scope)
            if directives["execution_mode"] in ("audit_only", "qa_guard"):
                in_scope_edit = False
            else:
                in_scope_edit = in_scope

            sent_lang = detect_language(core)
            sent_obj = {
                "sentence_id": sid,
                "block_id": blk["block_id"],
                "block_type": blk["block_type"],
                "section_role": blk["section_role"],
                "section_title": blk["section_title"],
                "page_span": blk["page_span"],
                "in_scope": in_scope_edit,
                "structural_in_scope": in_scope,
                "language": sent_lang,
                "leading_ws": slot["prefix_markup"],
                "trailing_ws": slot["suffix_markup"],
                "prefix_markup": blk.get("block_prefix", "") if len(enriched_slots) == 0 else "",
                "suffix_markup": blk.get("block_suffix", ""),
                "source_text": core,
                "char_span": [start_c, end_c],
            }
            sentences.append(sent_obj)
            enriched_slots.append({
                "sentence_id": sid,
                "source_text": core,
                "prefix_markup": slot["prefix_markup"],
                "suffix_markup": slot["suffix_markup"],
                "separator_after": slot["separator_after"],
            })

        blk["sentence_slots"] = enriched_slots
        char_cursor += len(blk.get("block_suffix", "")) + len(blk.get("block_separator", ""))

    full_reconstructed = reconstruct_document(blocks, None)
    anomalies = detect_text_anomalies(full_reconstructed, case_data)
    if not sentences and not any(a["code"] == "ENCODING_GARBLED" for a in anomalies):
        anomalies.append({
            "code": "NO_EDITABLE_SENTENCES",
            "severity": "HIGH",
            "detail": "No editable prose sentences extracted (missing input, scanned PDF without text layer, or unreadable asset).",
            "requires_human_confirmation": True,
        })
    if directives["style_unspecified"]:
        anomalies.append({
            "code": "TARGET_STYLE_UNSPECIFIED",
            "severity": "LOW",
            "detail": "No target journal/genre specified in input; defaulted to 'general_academic'.",
            "requires_human_confirmation": False,
        })

    # Load glossary (for compact reading packet hints) without failing if missing
    locked_terms = []
    try:
        import glossary_abbr_guard
        gb = glossary_abbr_guard.load_and_audit_glossary(glossary_files, case_data)
        locked_terms = gb.get("locked_terms", [])
    except Exception:
        locked_terms = []

    # Adaptive budget-aware reading packets
    in_scope_sents = [s for s in sentences if s["in_scope"]]
    if batch_size is not None:
        eff_max_bs = max(1, int(batch_size))
        eff_min_bs = min(6, eff_max_bs)
    else:
        eff_max_bs = 36
        eff_min_bs = 6

    packet_chunks, budget_advisories = build_adaptive_batches(
        sentences,
        target_max_batches=target_max_batches,
        max_batch_tokens=max_batch_tokens,
        min_batch_size=eff_min_bs,
        max_batch_size=eff_max_bs,
        locked_terms=locked_terms,
        target_style=directives["target_style"],
        include_enrichment=True,
    )
    anomalies.extend(budget_advisories)

    packets_dir = os.path.join(work_dir, "reading_packets")
    if os.path.exists(packets_dir):
        shutil.rmtree(packets_dir)
    os.makedirs(packets_dir, exist_ok=True)
    batch_files = []
    sent_idx_map = {s["sentence_id"]: idx for idx, s in enumerate(sentences)}

    for b_num, items in enumerate(packet_chunks, 1):
        b_id = f"batch_{b_num:02d}"
        b_name = f"{b_id}.json"
        b_path = os.path.join(packets_dir, b_name)
        first_idx = sent_idx_map.get(items[0]["sentence_id"], 0)
        ctx_prev = sentences[first_idx - 1]["source_text"] if first_idx > 0 else ""
        payload = {
            "batch_id": b_id,
            "execution_mode": directives["execution_mode"],
            "edit_intensity": directives["edit_intensity"],
            "target_style": directives["target_style"],
            "context_prev_sentence": ctx_prev,
            "sentences": items,
        }
        with open(b_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        batch_files.append(os.path.join("reading_packets", b_name).replace("\\", "/"))

    doc_lang = detect_language(full_reconstructed)
    rel_glossaries = []
    for g in glossary_files:
        try:
            rel_glossaries.append(os.path.relpath(g, input_dir).replace("\\", "/"))
        except ValueError:
            rel_glossaries.append(os.path.basename(g))

    manifest = {
        "case_id": case_data.get("case_id", os.path.basename(os.path.abspath(input_dir))),
        "case_data": case_data,
        "task_text": task_text,
        "source_format": source_format,
        "source_path": source_path_used,
        "document_language": doc_lang,
        "execution_mode": directives["execution_mode"],
        "edit_intensity": directives["edit_intensity"],
        "target_style": directives["target_style"],
        "style_unspecified": directives["style_unspecified"],
        "scope_constraints": scope,
        "glossary_files_detected": rel_glossaries,
        "glossary_paths_abs": glossary_files,
        "bilingual_mode": bilingual_pair_mode,
        "block_count": len(blocks),
        "sentence_count": len(sentences),
        "in_scope_sentence_count": len(in_scope_sents),
        "batch_count": len(batch_files),
        "batch_files": batch_files,
        "estimated_tokens": estimate_tokens(full_reconstructed),
        "anomalies": anomalies,
        "pdf_diagnosis": pdf_diag,
        "normalized_source_sha256": hashlib.sha256(full_reconstructed.encode("utf-8")).hexdigest(),
    }

    with open(os.path.join(work_dir, "ingest_manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(os.path.join(work_dir, "blocks.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(blocks, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(os.path.join(work_dir, "sentences.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(sentences, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(os.path.join(work_dir, "normalized_source.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(full_reconstructed)

    return manifest, blocks, sentences


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Ingest academic manuscript for polishing.")
    parser.add_argument("--input-dir", required=True, help="Input directory or file containing case.json / task.md / source docs")
    parser.add_argument("--work-dir", required=True, help="Working directory for intermediate manifests")
    parser.add_argument("--target-max-batches", type=int, default=16, help="Target max reading packets (default 16)")
    parser.add_argument("--max-batch-tokens", type=int, default=2800, help="Max estimated tokens per packet (default 2800)")
    args = parser.parse_args()
    manifest, _, _ = ingest_case(
        args.input_dir,
        args.work_dir,
        target_max_batches=args.target_max_batches,
        max_batch_tokens=args.max_batch_tokens,
    )
    compact = {
        "case_id": manifest["case_id"],
        "source_format": manifest["source_format"],
        "execution_mode": manifest["execution_mode"],
        "edit_intensity": manifest["edit_intensity"],
        "target_style": manifest["target_style"],
        "sentence_count": manifest["sentence_count"],
        "in_scope_sentence_count": manifest["in_scope_sentence_count"],
        "batch_count": manifest["batch_count"],
        "anomaly_codes": [a["code"] for a in manifest["anomalies"]],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
