#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_aligner.py - Dual-Locator Node/Edge Verbatim Quote Aligner & Cross-Paper Independence Verifier
Supports Mode A (JATS source_id + paragraph_id, page_number=None) and Mode B (PDF/Text anchor_id + page).
Part of the Literature Knowledge Graph Skill (task_10_kg).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import difflib
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from entity_guard import _normalize_ligatures
except ImportError:
    def _normalize_ligatures(text):
        if not text:
            return ""
        text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
        text = text.replace("\ufb00", "ff").replace("\ufb03", "ffi").replace("\ufb04", "ffl")
        text = text.replace("\ufb05", "st").replace("\ufb06", "st")
        return re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)


_SUB_SUP_TRANS = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")

CITATION_IN_QUOTE_REGEX = re.compile(
    r"(?:"
    r"\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\]"
    r"|\([A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?\)"
    r")"
)
ASSET_IN_QUOTE_REGEX = re.compile(
    r"(?:\b(?:Fig(?:ure)?s?\.?|Tables?|Tabs?\.?)\s*[A-Za-z]?\d+(?:\.\d+)*|(?:图|表|续表)\s*[A-Za-z]?\d+(?:\.\d+)*)",
    re.I,
)
SENTENCE_END_REGEX = re.compile(r"(?<=[.?!。！？])\s+(?=[A-Z0-9\u4e00-\u9fa5\(\[\"“])")


def normalize_for_alignment(text, join_hyphen_space=False, normalize_linear_math=True):
    """
    Normalize ligatures, soft hyphens, smart quotes, dashes, linearized subscripts/superscripts,
    and whitespace for robust verbatim quote alignment.
    """
    if not text:
        return ""
    s = _normalize_ligatures(text)
    s = s.replace("\u00ad", "")
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    if normalize_linear_math:
        s = s.translate(_SUB_SUP_TRANS)
        s = re.sub(r"([A-Za-z0-9])_\{([^}]+)\}", r"\1\2", s)
        s = re.sub(r"([A-Za-z0-9])_([0-9]+)", r"\1\2", s)
        s = s.replace("≤", "<=").replace("≥", ">=").replace("→", "->").replace("×", "x")
    if join_hyphen_space:
        s = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _count_sentences(text):
    if not text or not text.strip():
        return 0
    parts = [p.strip() for p in SENTENCE_END_REGEX.split(text.strip()) if p.strip()]
    return max(1, len(parts))


def _tokenize_mixed(text):
    norm = normalize_for_alignment(text, join_hyphen_space=True).lower()
    en_tokens = re.findall(r"[a-z0-9]+(?:[._\-/][a-z0-9]+)*", norm)
    cjk_chars = re.findall(r"[\u4e00-\u9fa5]", norm)
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return en_tokens + (cjk_bigrams if cjk_bigrams else cjk_chars)


def _fuzzy_score(quote_norm, cand_norm):
    q_toks = _tokenize_mixed(quote_norm)
    c_toks = _tokenize_mixed(cand_norm)
    if not q_toks or not c_toks:
        return 0.0
    c_set = set(c_toks)
    containment = sum(1 for t in q_toks if t in c_set) / max(1, len(q_toks))
    if containment < 0.45:
        return round(containment * 0.8, 4)

    if len(cand_norm) > len(quote_norm) * 1.4:
        q_len = len(quote_norm)
        step = max(20, q_len // 4)
        best_seq = 0.0
        win_len = min(len(cand_norm), int(q_len * 1.25))
        for start in range(0, max(1, len(cand_norm) - q_len // 2), step):
            sub = cand_norm[start : start + win_len]
            r = difflib.SequenceMatcher(None, quote_norm.lower(), sub.lower()).ratio()
            if r > best_seq:
                best_seq = r
        seq_ratio = best_seq
    else:
        seq_ratio = difflib.SequenceMatcher(None, quote_norm.lower(), cand_norm.lower()).ratio()

    return round(0.45 * containment + 0.55 * seq_ratio, 4)


def _try_exact_or_norm_in_text(quote, block_text):
    if not quote or not block_text:
        return None, None, None
    q_strip = quote.strip()
    idx = block_text.find(q_strip)
    if idx != -1:
        return "EXACT_MATCH", idx, idx + len(q_strip)

    for join_h in (False, True):
        q_norm = normalize_for_alignment(q_strip, join_hyphen_space=join_h)
        b_norm = normalize_for_alignment(block_text, join_hyphen_space=join_h)
        if not q_norm or not b_norm:
            continue
        idx_n = b_norm.find(q_norm)
        if idx_n != -1:
            return "NORMALIZED_MATCH", idx_n, idx_n + len(q_norm)
        idx_ci = b_norm.lower().find(q_norm.lower())
        if idx_ci != -1:
            return "NORMALIZED_MATCH", idx_ci, idx_ci + len(q_norm)
    return None, None, None


def _build_adjacent_windows(anchors):
    """Build 2-block and 3-block same-source/same-page sliding windows (handles page=None in JATS mode)."""
    windows = []
    by_group = {}
    for a in anchors:
        sid = a.get("source_id") or a.get("doc_id") or "D1"
        p_key = a.get("page") if a.get("page") is not None else "__jats__"
        by_group.setdefault((sid, p_key), []).append(a)

    for _key, blk_list in by_group.items():
        for w_size in (2, 3):
            for i in range(len(blk_list) - w_size + 1):
                chunk = blk_list[i : i + w_size]
                joined = " ".join(c.get("text", "").strip() for c in chunk if c.get("text", "").strip())
                windows.append((chunk, joined))
    return windows


def _compute_sufficiency_flags(quote, matched_anchors, claimed_locator=None, alignment_status="QUOTE_NOT_FOUND"):
    flags = []
    q_clean = normalize_for_alignment(quote)
    q_len = len(q_clean)
    s_cnt = _count_sentences(q_clean)

    if q_len < 20:
        flags.append("TOO_SHORT_FRAGMENT")

    combined_block_len = sum(len(normalize_for_alignment(a.get("text", ""))) for a in matched_anchors)
    if (s_cnt > 4 or q_len > 700) and combined_block_len > 0 and (q_len / combined_block_len) > 0.85:
        flags.append("PARAGRAPH_DUMP")
    elif s_cnt > 6 or q_len > 1100:
        flags.append("PARAGRAPH_DUMP")

    primary = matched_anchors[0] if matched_anchors else None
    if primary:
        if primary.get("section_role") == "abstract":
            flags.append("ABSTRACT_ONLY_EVIDENCE")

        has_cite_in_q = bool(CITATION_IN_QUOTE_REGEX.search(q_clean))
        if has_cite_in_q or (
            (primary.get("has_citation_signal") or primary.get("has_citation_markers"))
            and primary.get("section_role") in ("introduction", "discussion")
        ):
            flags.append("CONTAINS_SECONDARY_CITATION")

        if (
            primary.get("block_type") in ("table_like", "caption")
            or "unextractable_asset" in (primary.get("quality_flags") or [])
            or "ocr_extracted" in (primary.get("quality_flags") or [])
            or (ASSET_IN_QUOTE_REGEX.search(q_clean) and primary.get("block_type") == "caption")
        ):
            flags.append("REQUIRES_VISUAL_TABLE_OR_FIGURE")

        matched_ids = set()
        for a in matched_anchors:
            if a.get("anchor_id"):
                matched_ids.add(a["anchor_id"])
            if a.get("paragraph_id"):
                matched_ids.add(a["paragraph_id"])
        if claimed_locator and claimed_locator not in matched_ids and alignment_status != "QUOTE_NOT_FOUND":
            flags.append("LOCATOR_RELOCATED")

    return flags


def _resolve_claimed_anchor(cand_anchors, locator_id, source_id=None):
    if not locator_id:
        return None
    loc_str = str(locator_id).strip()
    for a in cand_anchors:
        if a.get("anchor_id") == loc_str:
            return a
    if source_id:
        pref = f"{source_id}::{loc_str}"
        for a in cand_anchors:
            if a.get("anchor_id") == pref:
                return a
    for a in cand_anchors:
        if a.get("paragraph_id") == loc_str:
            return a
    return None


def _resolved_page(anc):
    if not anc or anc.get("locator_type") == "jats_paragraph":
        return None
    return anc.get("page")


def align_verbatim_quote(quote, source_id=None, locator_id=None, indexed_blocks=None, windows=None):
    """
    Dual-locator verbatim quote aligner for KG nodes and edges.
    - In Mode A (jats_paragraph): resolves against source_id + paragraph_id and keeps resolved_page_number=None.
    - In Mode B (pdf_page_block / text_block): resolves against doc_id/source_id + anchor_id and returns page number.
    """
    blocks = indexed_blocks or []
    q_str = (quote or "").strip()
    q_norm = normalize_for_alignment(q_str, join_hyphen_space=True)
    q_len = len(normalize_for_alignment(q_str))
    s_cnt = _count_sentences(q_str)

    if source_id:
        cand_anchors = [
            a for a in blocks
            if a.get("source_id") == source_id or a.get("doc_id") == source_id
        ]
    else:
        cand_anchors = list(blocks)

    if not cand_anchors:
        return {
            "aligned": False,
            "alignment_status": "SOURCE_NOT_FOUND",
            "match_mode": "source_not_found",
            "resolved_source_id": source_id,
            "resolved_anchor_id": None,
            "resolved_paragraph_id": None,
            "resolved_page_number": None,
            "matched_block_ids": [],
            "page_numbers": [],
            "similarity": 0.0,
            "match_ratio": 0.0,
            "sentence_count": s_cnt,
            "quote_char_len": q_len,
            "section_role": None,
            "sufficiency_flags": ["SOURCE_NOT_FOUND"],
        }

    claimed_anc = _resolve_claimed_anchor(cand_anchors, locator_id, source_id=source_id)

    if not q_str:
        return {
            "aligned": False,
            "alignment_status": "QUOTE_NOT_FOUND",
            "match_mode": "empty_quote",
            "resolved_source_id": source_id,
            "resolved_anchor_id": None,
            "resolved_paragraph_id": None,
            "resolved_page_number": None,
            "matched_block_ids": [],
            "page_numbers": [],
            "similarity": 0.0,
            "match_ratio": 0.0,
            "sentence_count": 0,
            "quote_char_len": 0,
            "section_role": claimed_anc.get("section_role") if claimed_anc else None,
            "sufficiency_flags": ["TOO_SHORT_FRAGMENT"],
        }

    def _build_hit(anc_list, status, mode, score=1.0, char_start=None, char_end=None):
        primary = anc_list[0]
        p_nums = sorted({p for p in (_resolved_page(a) for a in anc_list) if p is not None})
        flags = _compute_sufficiency_flags(q_str, anc_list, locator_id, status)
        return {
            "aligned": True,
            "alignment_status": status,
            "match_mode": mode,
            "resolved_source_id": primary.get("source_id") or primary.get("doc_id"),
            "resolved_anchor_id": primary.get("anchor_id"),
            "resolved_paragraph_id": primary.get("paragraph_id"),
            "resolved_page_number": _resolved_page(primary),
            "matched_block_ids": [a["anchor_id"] for a in anc_list],
            "page_numbers": p_nums,
            "char_start": char_start,
            "char_end": char_end,
            "similarity": round(score, 4),
            "match_ratio": round(score, 4),
            "sentence_count": s_cnt,
            "quote_char_len": q_len,
            "section_role": primary.get("section_role"),
            "sufficiency_flags": flags,
        }

    # 1. Exact / normalized match inside claimed locator
    if claimed_anc:
        st, cs, ce = _try_exact_or_norm_in_text(q_str, claimed_anc.get("text", ""))
        if st:
            return _build_hit([claimed_anc], st, st.lower(), 1.0, cs, ce)

    if windows is None:
        windows = _build_adjacent_windows(cand_anchors)

    # 2. Adjacent window involving claimed locator
    if claimed_anc:
        c_aid = claimed_anc["anchor_id"]
        for chunk, joined_txt in windows:
            if not any(c.get("anchor_id") == c_aid for c in chunk):
                continue
            st, cs, ce = _try_exact_or_norm_in_text(q_str, joined_txt)
            if st:
                return _build_hit(chunk, "CROSS_BLOCK_MATCH", "cross_block_claimed", 1.0, cs, ce)

    # 3. Same-source global exact / normalized search
    for anc in cand_anchors:
        if claimed_anc and anc.get("anchor_id") == claimed_anc.get("anchor_id"):
            continue
        st, cs, ce = _try_exact_or_norm_in_text(q_str, anc.get("text", ""))
        if st:
            return _build_hit([anc], st, f"relocated_{st.lower()}", 1.0, cs, ce)

    # 4. Same-source global adjacent window search
    for chunk, joined_txt in windows:
        st, cs, ce = _try_exact_or_norm_in_text(q_str, joined_txt)
        if st:
            return _build_hit(chunk, "CROSS_BLOCK_MATCH", "cross_block_relocated", 1.0, cs, ce)

    # 5. Fuzzy fallback (>= 0.78)
    best_score, best_anc = 0.0, None
    for anc in cand_anchors:
        b_norm = normalize_for_alignment(anc.get("text", ""), join_hyphen_space=True)
        if not b_norm:
            continue
        score = _fuzzy_score(q_norm, b_norm)
        if score > best_score:
            best_score, best_anc = score, anc

    if best_anc and best_score >= 0.78:
        return _build_hit([best_anc], "FUZZY_PARTIAL", "fuzzy_partial", best_score)

    flags = _compute_sufficiency_flags(q_str, [], locator_id, "QUOTE_NOT_FOUND")
    return {
        "aligned": False,
        "alignment_status": "QUOTE_NOT_FOUND",
        "match_mode": "not_found",
        "resolved_source_id": source_id,
        "resolved_anchor_id": best_anc["anchor_id"] if (best_anc and best_score >= 0.45) else None,
        "resolved_paragraph_id": best_anc.get("paragraph_id") if (best_anc and best_score >= 0.45) else None,
        "resolved_page_number": None,
        "matched_block_ids": [],
        "page_numbers": [],
        "similarity": round(best_score, 4),
        "match_ratio": round(best_score, 4),
        "sentence_count": s_cnt,
        "quote_char_len": q_len,
        "section_role": claimed_anc.get("section_role") if claimed_anc else None,
        "sufficiency_flags": flags,
    }


def align_evidence_quote(quote, anchors, claimed_block_id=None, doc_id=None, same_page_windows=None):
    """Backward-compatible wrapper around align_verbatim_quote."""
    res = align_verbatim_quote(
        quote=quote,
        source_id=doc_id,
        locator_id=claimed_block_id,
        indexed_blocks=anchors,
        windows=same_page_windows,
    )
    res["suggested_block_id"] = (
        res["resolved_anchor_id"]
        if res["resolved_anchor_id"] and res["resolved_anchor_id"] != claimed_block_id
        else None
    )
    return res


def align_evidence_batch(items, evidence_index):
    anchors = evidence_index.get("anchors", [])
    windows = _build_adjacent_windows(anchors)
    results = []
    for idx, item in enumerate(items):
        quote = item.get("evidence_quote") or item.get("quote") or ""
        sid = item.get("source_id") or item.get("doc_id")
        loc_id = item.get("paragraph_id") or item.get("anchor_id") or item.get("block_id")
        res = align_verbatim_quote(quote=quote, source_id=sid, locator_id=loc_id, indexed_blocks=anchors, windows=windows)
        res["item_id"] = item.get("edge_id") or item.get("node_id") or item.get("id") or f"ITEM-{idx + 1}"
        results.append(res)
    return results


def _build_registry_lookup(doc_registry):
    """Build filename/doc_id/source_id -> canonical independent source_id lookup (collapsing verification twins)."""
    alias_to_canonical = {}
    twin_files = set()

    if isinstance(doc_registry, dict):
        docs = doc_registry.get("documents", [])
        twins = doc_registry.get("verification_twins", [])
    elif isinstance(doc_registry, list):
        docs = [d for d in doc_registry if not d.get("is_verification_twin")]
        twins = [d for d in doc_registry if d.get("is_verification_twin")]
    else:
        docs, twins = [], []

    for d in docs:
        sid = d.get("source_id") or d.get("doc_id")
        if not sid:
            continue
        for key in (sid, d.get("doc_id"), d.get("source_file"), d.get("rel_path")):
            if key:
                alias_to_canonical[str(key).strip().lower()] = sid
                alias_to_canonical[os.path.basename(str(key).strip()).lower()] = sid
        if d.get("verification_twin_file"):
            tf = str(d["verification_twin_file"]).strip().lower()
            alias_to_canonical[tf] = sid
            alias_to_canonical[os.path.basename(tf)] = sid
            twin_files.add(os.path.basename(tf))

    for t in twins:
        canonical_sid = t.get("twin_of_source_id") or t.get("twin_of_doc_id")
        if not canonical_sid:
            continue
        for key in (t.get("twin_file"), t.get("source_file"), t.get("rel_path"), t.get("source_id")):
            if key:
                low = str(key).strip().lower()
                alias_to_canonical[low] = canonical_sid
                alias_to_canonical[os.path.basename(low)] = canonical_sid
                twin_files.add(os.path.basename(low))

    return alias_to_canonical, twin_files


def verify_cross_paper_independence(evidence_list, doc_registry):
    """
    Verify whether an evidence set truly spans >= 2 independent papers.
    Explicitly rejects treating same-paper multi-format files (e.g. secondary-material.json
    + secondary-publisher.xml) as cross-paper corroboration.
    """
    alias_to_canonical, twin_files = _build_registry_lookup(doc_registry)
    independent_sources = []
    seen_sources = set()
    twin_collapsed = []
    unresolved = []

    for ev in evidence_list or []:
        if isinstance(ev, str):
            raw_ref = ev
            explicit_twin = False
        elif isinstance(ev, dict):
            explicit_twin = bool(ev.get("is_verification_twin"))
            raw_ref = (
                ev.get("file")
                or ev.get("source_file")
                or ev.get("source_id")
                or ev.get("doc_id")
                or ""
            )
            if not raw_ref and ev.get("anchor_id") and "::" in str(ev.get("anchor_id")):
                raw_ref = str(ev["anchor_id"]).split("::", 1)[0]
        else:
            continue

        ref_low = str(raw_ref).strip().lower()
        base_low = os.path.basename(ref_low)
        canonical = alias_to_canonical.get(ref_low) or alias_to_canonical.get(base_low)

        # Fallback: also inspect source_id/doc_id separately if file wasn't matched
        if not canonical and isinstance(ev, dict):
            for alt_k in ("source_id", "doc_id"):
                alt_v = str(ev.get(alt_k) or "").strip().lower()
                if alt_v in alias_to_canonical:
                    canonical = alias_to_canonical[alt_v]
                    break
            if not canonical and (ev.get("source_id") or ev.get("doc_id")):
                canonical = ev.get("source_id") or ev.get("doc_id")

        if explicit_twin or base_low in twin_files:
            twin_collapsed.append({"input_ref": raw_ref, "resolved_to_source_id": canonical})

        if canonical:
            if canonical not in seen_sources:
                seen_sources.add(canonical)
                independent_sources.append(canonical)
        else:
            unresolved.append(raw_ref)

    is_cross = len(independent_sources) >= 2
    if is_cross:
        reason = f"Evidence spans {len(independent_sources)} independent paper(s): {independent_sources}."
    elif twin_collapsed:
        reason = (
            f"Not truly cross-paper: all evidence collapses to single independent paper {independent_sources} "
            f"(same-paper verification twin(s) {[t['input_ref'] for t in twin_collapsed]} excluded from cross-paper count)."
        )
    else:
        reason = f"Only {len(independent_sources)} independent paper(s) present ({independent_sources}); >= 2 required for cross-paper evidence."

    return {
        "is_truly_cross_paper": is_cross,
        "independent_source_ids": independent_sources,
        "independent_paper_count": len(independent_sources),
        "twin_collapsed_items": twin_collapsed,
        "unresolved_items": unresolved,
        "reason": reason,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Dual-Locator Verbatim Evidence Quote Aligner")
    parser.add_argument("--index", required=True, help="Path to evidence_index.json")
    parser.add_argument("--quote", required=True, help="Evidence quote string to align")
    parser.add_argument("--source-id", default=None, help="Source ID (e.g., primary, secondary, D1)")
    parser.add_argument("--locator-id", default=None, help="Optional paragraph_id or anchor_id")
    args = parser.parse_args()

    with open(args.index, "r", encoding="utf-8") as f:
        ev_idx = json.load(f)

    res = align_verbatim_quote(
        quote=args.quote,
        source_id=args.source_id,
        locator_id=args.locator_id,
        indexed_blocks=ev_idx.get("anchors", []),
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
