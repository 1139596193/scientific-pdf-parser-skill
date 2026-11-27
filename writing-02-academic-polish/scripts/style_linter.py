#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
style_linter.py - Deterministic Academic Register, Chinglish, Punctuation Hygiene
& LLM Meta-Leakage Linter.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "resources")
STYLE_PROFILES_PATH = os.path.join(RESOURCES_DIR, "style_profiles.json")
LADDER_PATH = os.path.join(RESOURCES_DIR, "epistemic_hedging_ladder.json")


def _load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def lint_sentence(text, lang=None, target_style="general_academic", mode="post"):
    """
    Scan a sentence for colloquialisms, Chinglish, punctuation width mismatch,
    placeholders, and LLM meta-leakage.
    mode='pre' (before polishing, diagnostic hints) or 'post' (after polishing, audit).
    """
    issues = []
    text = text or ""
    if not text.strip():
        return issues

    if not lang:
        zh_cnt = len(re.findall(r"[\u4e00-\u9fff]", text))
        en_cnt = len(re.findall(r"[A-Za-z]", text))
        lang = "zh" if zh_cnt > en_cnt else "en"

    style_data = _load_json(STYLE_PROFILES_PATH)
    ladder_data = _load_json(LADDER_PATH)
    lint_cfg = style_data.get("colloquial_chinglish_lint", {})

    # 1. Meta-leakage (critical in post mode)
    for pat in ladder_data.get("meta_leakage_patterns", []):
        try:
            if re.search(pat, text, re.I):
                issues.append({
                    "code": "META_LEAKAGE",
                    "severity": "HIGH",
                    "detail": f"Detected LLM conversational/meta-text leakage matching '{pat}'.",
                    "repair_hint": "Strip assistant preamble/commentary and keep only the manuscript sentence.",
                })
                break
        except re.error:
            continue

    # 2. Strip math/code/URLs before register & punctuation checks
    clean = re.sub(r"\$[^$\n]+\$|`[^`\n]+`|https?://[^\s]+|\\[A-Za-z]+\{[^}]*\}", " ", text)

    # 3. Colloquial / Chinglish lint rules
    rules = []
    if lang in ("en", "mixed"):
        rules.extend(lint_cfg.get("en", []))
    if lang in ("zh", "mixed"):
        rules.extend(lint_cfg.get("zh", []))

    for rule in rules:
        pat = rule.get("pattern", "")
        if not pat:
            continue
        try:
            m = re.search(pat, clean, re.I if lang != "zh" else 0)
            if m:
                issues.append({
                    "code": rule.get("id", "STYLE_LINT"),
                    "severity": rule.get("severity", "LOW"),
                    "category": rule.get("category", "academic_register"),
                    "detail": f"Matched '{m.group(0)}' ({rule.get('category', '')}).",
                    "repair_hint": rule.get("suggestion", "Refine to formal academic register."),
                })
        except re.error:
            continue

    # 4. Fullwidth / Halfwidth punctuation mismatch
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", clean))
    en_words = len(re.findall(r"[A-Za-z]{2,}", clean))
    if zh_chars == 0 and en_words >= 3:
        if re.search(r"[，。；：！？（）【】《》]", clean):
            issues.append({
                "code": "PUNCT_WIDTH_MISMATCH",
                "severity": "MEDIUM",
                "detail": "Full-width CJK punctuation found inside pure English sentence.",
                "repair_hint": "Replace full-width CJK punctuation with standard ASCII punctuation.",
            })
    elif zh_chars >= 6 and en_words <= 2:
        # Check ASCII comma/semicolon directly between Chinese characters
        if re.search(r"[\u4e00-\u9fff]\s*[,;!?]\s*[\u4e00-\u9fff]", clean):
            issues.append({
                "code": "PUNCT_WIDTH_MISMATCH",
                "severity": "MEDIUM",
                "detail": "Half-width ASCII punctuation found between Chinese clauses.",
                "repair_hint": "Use standard Chinese full-width punctuation (，；！？).",
            })

    # 5. Unresolved placeholder check
    if re.search(r"\[(?:TBD|TODO|citation\s+needed|待补充|待定)\]|\?\?\?|\b(?:XX+|YY+)\s*%", text, re.I):
        issues.append({
            "code": "PLACEHOLDER_PRESENT",
            "severity": "LOW" if mode == "pre" else "INFO",
            "detail": "Sentence contains unresolved placeholder; retain and flag for human confirmation without fabricating data.",
            "repair_hint": "Keep placeholder intact and document in uncertainty / needs_human_confirmation.",
        })

    return issues
