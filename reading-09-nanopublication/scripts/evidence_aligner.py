#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_aligner.py - Verbatim Quote Normalization, Cross-Block Sliding & Sufficiency Aligner
Part of the Nanopublication Scientific Reading Skill (task_09_nanopub).
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


def normalize_for_alignment(text, join_hyphen_space=False):
    """
    Normalize ligatures, soft hyphens, line-break hyphenations, smart quotes, dashes, and whitespace.
    """
    if not text:
        return ""
    s = _normalize_ligatures(text)
    s = s.replace("\u00ad", "")
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
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
    """Tokenize bilingual text into English words and CJK unigrams/bigrams for fuzzy scoring."""
    norm = normalize_for_alignment(text, join_hyphen_space=True).lower()
    en_tokens = re.findall(r"[a-z0-9]+(?:[._\-/][a-z0-9]+)*", norm)
    cjk_chars = re.findall(r"[\u4e00-\u9fa5]", norm)
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return en_tokens + (cjk_bigrams if cjk_bigrams else cjk_chars)


def _fuzzy_score(quote_norm, cand_norm):
    """Compute token containment + SequenceMatcher similarity between quote and candidate block/window."""
    q_toks = _tokenize_mixed(quote_norm)
    c_toks = _tokenize_mixed(cand_norm)
    if not q_toks or not c_toks:
        return 0.0
    c_set = set(c_toks)
    containment = sum(1 for t in q_toks if t in c_set) / max(1, len(q_toks))
    if containment < 0.45:
        return containment * 0.8

    # Find best local slice in candidate if candidate is much longer than quote
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
    """
    Attempt verbatim exact match, then normalized match (preserving/joining hyphen spaces).
    Returns (status_or_None, char_start, char_end).
    """
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


def _build_same_page_windows(anchors):
    """Build 2-block and 3-block same-doc, same-page sliding windows for cross-block matching."""
    windows = []
    by_doc_page = {}
    for a in anchors:
        key = (a.get("doc_id", "D1"), a.get("page", 1))
        by_doc_page.setdefault(key, []).append(a)

    for _key, blk_list in by_doc_page.items():
        for w_size in (2, 3):
            for i in range(len(blk_list) - w_size + 1):
                chunk = blk_list[i : i + w_size]
                joined = " ".join(c.get("text", "").strip() for c in chunk if c.get("text", "").strip())
                windows.append((chunk, joined))
    return windows


def _compute_sufficiency_flags(
    quote,
    matched_anchors,
    claimed_block_id=None,
    alignment_status="QUOTE_NOT_FOUND",
):
    """Compute deterministic sufficiency and quality flags for an aligned quote."""
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
            primary.get("has_citation_markers") and primary.get("section_role") in ("introduction", "discussion")
        ):
            flags.append("CONTAINS_SECONDARY_CITATION")

        if (
            primary.get("block_type") in ("table_like", "caption")
            or "unextractable_asset" in (primary.get("quality_flags") or [])
            or "ocr_extracted" in (primary.get("quality_flags") or [])
            or (ASSET_IN_QUOTE_REGEX.search(q_clean) and primary.get("block_type") == "caption")
        ):
            flags.append("REQUIRES_VISUAL_TABLE_OR_FIGURE")

        matched_ids = [a.get("anchor_id") for a in matched_anchors]
        if claimed_block_id and claimed_block_id not in matched_ids and alignment_status != "QUOTE_NOT_FOUND":
            flags.append("BLOCK_ID_RELOCATED")

    return flags


def align_evidence_quote(quote, anchors, claimed_block_id=None, doc_id=None, same_page_windows=None):
    """
    Align an evidence_quote string against indexed document anchors.
    Pipeline:
      1. Specified block_id exact / normalized match
      2. Same-page adjacent cross-block match involving claimed_block_id
      3. Global exact / normalized match across candidate anchors (detecting block_id mismatch)
      4. Global same-page adjacent cross-block match
      5. High-threshold fuzzy match (>= 0.78 -> FUZZY_PARTIAL, else QUOTE_NOT_FOUND)
    """
    q_str = (quote or "").strip()
    q_norm = normalize_for_alignment(q_str, join_hyphen_space=True)
    q_len = len(normalize_for_alignment(q_str))
    s_cnt = _count_sentences(q_str)

    cand_anchors = [a for a in anchors if (not doc_id or a.get("doc_id") == doc_id)]
    if not cand_anchors:
        cand_anchors = anchors

    anchor_by_id = {a.get("anchor_id"): a for a in cand_anchors}
    claimed_anc = anchor_by_id.get(claimed_block_id) if claimed_block_id else None

    if not q_str:
        return {
            "alignment_status": "QUOTE_NOT_FOUND",
            "matched_block_ids": [],
            "page_numbers": [],
            "char_start": None,
            "char_end": None,
            "match_ratio": 0.0,
            "sentence_count": 0,
            "quote_char_len": 0,
            "section_role": claimed_anc.get("section_role") if claimed_anc else None,
            "suggested_block_id": None,
            "sufficiency_flags": ["TOO_SHORT_FRAGMENT"],
        }

    # Step 1: Specified block_id
    if claimed_anc:
        st, c_start, c_end = _try_exact_or_norm_in_text(q_str, claimed_anc.get("text", ""))
        if st:
            matched = [claimed_anc]
            flags = _compute_sufficiency_flags(q_str, matched, claimed_block_id, st)
            return {
                "alignment_status": st,
                "matched_block_ids": [claimed_anc["anchor_id"]],
                "page_numbers": [claimed_anc["page"]],
                "char_start": c_start,
                "char_end": c_end,
                "match_ratio": 1.0,
                "sentence_count": s_cnt,
                "quote_char_len": q_len,
                "section_role": claimed_anc.get("section_role"),
                "suggested_block_id": None,
                "sufficiency_flags": flags,
            }

    if same_page_windows is None:
        same_page_windows = _build_same_page_windows(cand_anchors)

    # Step 2: Same-page cross-block windows containing claimed_block_id
    if claimed_anc:
        for chunk, joined_txt in same_page_windows:
            if not any(c.get("anchor_id") == claimed_block_id for c in chunk):
                continue
            st, c_start, c_end = _try_exact_or_norm_in_text(q_str, joined_txt)
            if st:
                # Only keep minimal sub-chunk that still contains the quote
                flags = _compute_sufficiency_flags(q_str, chunk, claimed_block_id, "CROSS_BLOCK_MATCH")
                return {
                    "alignment_status": "CROSS_BLOCK_MATCH",
                    "matched_block_ids": [c["anchor_id"] for c in chunk],
                    "page_numbers": sorted({c["page"] for c in chunk}),
                    "char_start": c_start,
                    "char_end": c_end,
                    "match_ratio": 1.0,
                    "sentence_count": s_cnt,
                    "quote_char_len": q_len,
                    "section_role": chunk[0].get("section_role"),
                    "suggested_block_id": None,
                    "sufficiency_flags": flags,
                }

    # Step 3: Global exact/normalized match across all candidate blocks
    for anc in cand_anchors:
        if anc.get("anchor_id") == claimed_block_id:
            continue
        st, c_start, c_end = _try_exact_or_norm_in_text(q_str, anc.get("text", ""))
        if st:
            matched = [anc]
            flags = _compute_sufficiency_flags(q_str, matched, claimed_block_id, st)
            return {
                "alignment_status": st,
                "matched_block_ids": [anc["anchor_id"]],
                "page_numbers": [anc["page"]],
                "char_start": c_start,
                "char_end": c_end,
                "match_ratio": 1.0,
                "sentence_count": s_cnt,
                "quote_char_len": q_len,
                "section_role": anc.get("section_role"),
                "suggested_block_id": anc["anchor_id"] if claimed_block_id != anc["anchor_id"] else None,
                "sufficiency_flags": flags,
            }

    # Step 4: Global same-page cross-block windows
    for chunk, joined_txt in same_page_windows:
        st, c_start, c_end = _try_exact_or_norm_in_text(q_str, joined_txt)
        if st:
            first_id = chunk[0]["anchor_id"]
            flags = _compute_sufficiency_flags(q_str, chunk, claimed_block_id, "CROSS_BLOCK_MATCH")
            return {
                "alignment_status": "CROSS_BLOCK_MATCH",
                "matched_block_ids": [c["anchor_id"] for c in chunk],
                "page_numbers": sorted({c["page"] for c in chunk}),
                "char_start": c_start,
                "char_end": c_end,
                "match_ratio": 1.0,
                "sentence_count": s_cnt,
                "quote_char_len": q_len,
                "section_role": chunk[0].get("section_role"),
                "suggested_block_id": first_id if claimed_block_id and claimed_block_id not in [c["anchor_id"] for c in chunk] else None,
                "sufficiency_flags": flags,
            }

    # Step 5: High-threshold token/n-gram fuzzy search
    best_score = 0.0
    best_anc = None
    for anc in cand_anchors:
        b_norm = normalize_for_alignment(anc.get("text", ""), join_hyphen_space=True)
        if not b_norm:
            continue
        score = _fuzzy_score(q_norm, b_norm)
        if score > best_score:
            best_score = score
            best_anc = anc

    if best_anc and best_score >= 0.78:
        matched = [best_anc]
        flags = _compute_sufficiency_flags(q_str, matched, claimed_block_id, "FUZZY_PARTIAL")
        sug = best_anc["anchor_id"] if claimed_block_id != best_anc["anchor_id"] else None
        return {
            "alignment_status": "FUZZY_PARTIAL",
            "matched_block_ids": [best_anc["anchor_id"]],
            "page_numbers": [best_anc["page"]],
            "char_start": None,
            "char_end": None,
            "match_ratio": round(best_score, 4),
            "sentence_count": s_cnt,
            "quote_char_len": q_len,
            "section_role": best_anc.get("section_role"),
            "suggested_block_id": sug,
            "sufficiency_flags": flags,
        }

    # QUOTE_NOT_FOUND
    flags = _compute_sufficiency_flags(q_str, [], claimed_block_id, "QUOTE_NOT_FOUND")
    return {
        "alignment_status": "QUOTE_NOT_FOUND",
        "matched_block_ids": [],
        "page_numbers": [],
        "char_start": None,
        "char_end": None,
        "match_ratio": round(best_score, 4),
        "sentence_count": s_cnt,
        "quote_char_len": q_len,
        "section_role": claimed_anc.get("section_role") if claimed_anc else None,
        "suggested_block_id": best_anc["anchor_id"] if (best_anc and best_score >= 0.45) else None,
        "sufficiency_flags": flags,
    }


def align_evidence_batch(items, evidence_index):
    """
    Align a list of nanopublication dicts or simple {"quote", "block_id", "doc_id"} dicts
    against evidence_index.
    """
    anchors = evidence_index.get("anchors", [])
    windows = _build_same_page_windows(anchors)
    results = []

    for idx, item in enumerate(items):
        if "provenance" in item:
            prov = item.get("provenance") or {}
            loc = prov.get("location") or {}
            quote = prov.get("evidence_quote", "")
            blk_id = loc.get("block_id")
            did = loc.get("doc_id")
            item_id = (item.get("head") or {}).get("nanopub_id") or f"ITEM-{idx+1}"
        else:
            quote = item.get("evidence_quote") or item.get("quote") or ""
            blk_id = item.get("block_id")
            did = item.get("doc_id")
            item_id = item.get("id") or f"ITEM-{idx+1}"

        res = align_evidence_quote(
            quote=quote,
            anchors=anchors,
            claimed_block_id=blk_id,
            doc_id=did,
            same_page_windows=windows,
        )
        res["item_id"] = item_id
        results.append(res)

    return results


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Deterministic Verbatim Evidence Quote Aligner")
    parser.add_argument("--index", required=True, help="Path to evidence_index.json")
    parser.add_argument("--claims", default=None, help="Optional JSON file containing nanopubs or quote items")
    parser.add_argument("--quote", default=None, help="Single evidence_quote string to align")
    parser.add_argument("--block-id", default=None, help="Optional claimed block_id (e.g., P2-B004)")
    parser.add_argument("--doc-id", default=None, help="Optional doc_id filter (e.g., D1)")
    parser.add_argument("--out", default=None, help="Optional output JSON path")
    args = parser.parse_args()

    with open(args.index, "r", encoding="utf-8") as f:
        evidence_index = json.load(f)

    if args.quote is not None:
        output = align_evidence_quote(
            quote=args.quote,
            anchors=evidence_index.get("anchors", []),
            claimed_block_id=args.block_id,
            doc_id=args.doc_id,
        )
    elif args.claims:
        with open(args.claims, "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = raw.get("nanopublications", raw) if isinstance(raw, dict) else raw
        output = {
            "case_id": evidence_index.get("case_id", "unknown"),
            "aligned_count": len(items),
            "results": align_evidence_batch(items, evidence_index),
        }
    else:
        raise ValueError("Provide either --quote or --claims.")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
