#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diff_engine.py - Bilingual/LaTeX-Aware Token Diff Engine & Edit Ledger Auditor.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import difflib
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "resources")
TAXONOMY_PATH = os.path.join(RESOURCES_DIR, "edit_taxonomy.json")

_TOKEN_RE = re.compile(
    r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?|"
    r"\$[^$\n]+\$|"
    r"\d+(?:,\d{3})*(?:\.\d+)?(?:%|°[CF]?|[eE][+-]?\d+)?|"
    r"[A-Za-z\u00C0-\u024F]+(?:[-'\u2019][A-Za-z\u00C0-\u024F]+)*|"
    r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]|"
    r"\s+|"
    r"[^\s]"
)


def load_edit_taxonomy():
    if os.path.exists(TAXONOMY_PATH):
        try:
            with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("taxonomy", {})
        except Exception:
            pass
    return {}


def tokenize_mixed(text):
    """Tokenize mixed EN/CJK/LaTeX text preserving exact reconstruction on ''.join(tokens)."""
    if not text:
        return []
    return _TOKEN_RE.findall(text)


def _content_tokens(tokens):
    return [t for t in tokens if not t.isspace()]


def compute_sentence_diff(source_text, polished_text):
    """
    Compute structured token-level diff between source_text and polished_text.
    Returns inline_diff ('[-del-]{+ins+}'), edit_ratio, counts, and diff_ops
    suitable for HTML redline and DOCX Track Changes (<w:del>/<w:ins>).
    """
    src = source_text or ""
    pol = polished_text if polished_text is not None else src
    is_changed = (src != pol)

    src_toks = tokenize_mixed(src)
    pol_toks = tokenize_mixed(pol)
    sm = difflib.SequenceMatcher(None, src_toks, pol_toks, autojunk=False)

    diff_ops = []
    inline_parts = []
    ins_cnt = 0
    del_cnt = 0
    rep_cnt = 0

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        s_chunk = "".join(src_toks[i1:i2])
        p_chunk = "".join(pol_toks[j1:j2])
        if tag == "equal":
            diff_ops.append({"op": "equal", "src": s_chunk, "pol": p_chunk})
            inline_parts.append(s_chunk)
        elif tag == "delete":
            diff_ops.append({"op": "delete", "src": s_chunk, "pol": ""})
            inline_parts.append(f"[-{s_chunk}-]")
            del_cnt += len(_content_tokens(src_toks[i1:i2]))
        elif tag == "insert":
            diff_ops.append({"op": "insert", "src": "", "pol": p_chunk})
            inline_parts.append(f"{{+{p_chunk}+}}")
            ins_cnt += len(_content_tokens(pol_toks[j1:j2]))
        elif tag == "replace":
            diff_ops.append({"op": "replace", "src": s_chunk, "pol": p_chunk})
            inline_parts.append(f"[-{s_chunk}-]{{+{p_chunk}+}}")
            rep_cnt += max(
                len(_content_tokens(src_toks[i1:i2])),
                len(_content_tokens(pol_toks[j1:j2])),
            )

    # Compute content-token edit_ratio (0.0 .. 1.0)
    src_c = _content_tokens(src_toks)
    pol_c = _content_tokens(pol_toks)
    if not is_changed:
        edit_ratio = 0.0
    elif not src_c and not pol_c:
        edit_ratio = 0.0
    else:
        c_sm = difflib.SequenceMatcher(None, src_c, pol_c, autojunk=False)
        edit_ratio = round(1.0 - c_sm.ratio(), 4)

    return {
        "is_changed": is_changed,
        "edit_ratio": edit_ratio,
        "token_insertions": ins_cnt,
        "token_deletions": del_cnt,
        "token_replacements": rep_cnt,
        "inline_diff": "".join(inline_parts),
        "diff_ops": diff_ops,
    }


def audit_edit_record(sentence_id, source_text, polished_text, edit_types, reason, edit_intensity="standard"):
    """
    Validate that the edit ledger truthfully matches actual text diff and respects
    edit_intensity minimal-edit guardrails.
    """
    issues = []
    taxonomy = load_edit_taxonomy()
    diff_info = compute_sentence_diff(source_text, polished_text)
    is_changed = diff_info["is_changed"]
    edit_ratio = diff_info["edit_ratio"]

    etypes = [str(t).strip() for t in (edit_types or []) if str(t).strip()]
    reason_str = (reason or "").strip()

    # Validate taxonomy tags
    if taxonomy:
        unknown = [t for t in etypes if t not in taxonomy]
        if unknown:
            issues.append({
                "sentence_id": sentence_id,
                "code": "INVALID_EDIT_TYPE",
                "severity": "MEDIUM",
                "detail": f"Unknown edit_type tag(s) {unknown} not in edit_taxonomy.json.",
                "repair_hint": "Use only valid tags from resources/edit_taxonomy.json.",
            })

    if is_changed:
        substantive_types = [t for t in etypes if t != "minimal_preserve"]
        if not substantive_types or not reason_str:
            issues.append({
                "sentence_id": sentence_id,
                "code": "UNLOGGED_CHANGE",
                "severity": "HIGH",
                "detail": f"Sentence was modified (edit_ratio={edit_ratio}) but edit_types={etypes} or reason='{reason_str}' is missing/trivial.",
                "repair_hint": "Provide accurate non-empty edit_types and concrete justification in reason.",
            })

        # Minimal-edit guardrail
        structural_excuses = {"syntax", "chinglish_fix", "cohesion", "bilingual_alignment"}
        has_structural_reason = any(t in structural_excuses for t in etypes)
        if edit_intensity == "minimal" and edit_ratio > 0.45:
            issues.append({
                "sentence_id": sentence_id,
                "code": "OVER_EDIT_RISK",
                "severity": "MEDIUM",
                "detail": f"Minimal edit intensity exceeded: edit_ratio={edit_ratio:.2f} (>0.45).",
                "repair_hint": "Perform surgical minimal edits instead of broad rewriting.",
            })
        elif edit_intensity == "standard" and edit_ratio > 0.72 and not has_structural_reason:
            issues.append({
                "sentence_id": sentence_id,
                "code": "OVER_EDIT_RISK",
                "severity": "MEDIUM",
                "detail": f"Standard edit intensity exceeded without syntax/chinglish_fix/cohesion tag: edit_ratio={edit_ratio:.2f} (>0.72).",
                "repair_hint": "Reduce unnecessary paraphrasing or justify structural rewrite explicitly.",
            })
    else:
        allowed_unchanged = {"minimal_preserve", "needs_human_review"}
        claimed_edits = [t for t in etypes if t not in allowed_unchanged]
        if claimed_edits:
            issues.append({
                "sentence_id": sentence_id,
                "code": "PHANTOM_EDIT_LOG",
                "severity": "MEDIUM",
                "detail": f"Source and polished text are identical, yet edit_types={claimed_edits} claims changes were made.",
                "repair_hint": "Either apply the stated fix or set edit_types=['minimal_preserve'].",
            })

    return diff_info, issues
