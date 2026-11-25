#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entity_guard.py - Deterministic Fingerprint Extraction & Batch Guard Enrichment
Part of the Scientific Translation Skill (task_03_translation).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import glob
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "resources", "academic_invariants.json")


def load_invariants():
    """Load logic markers, SI units, and invariants from resources."""
    if os.path.exists(RESOURCES_PATH):
        try:
            with open(RESOURCES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"logic_markers": {}, "si_and_academic_units": []}


# ----------------------------------------------------------------------
# 1. Deterministic Entity Extractors (Masked Sequential Pipeline)
# ----------------------------------------------------------------------

# Citations: [1], [1, 2], [8]-[10], [7]---[9], [1]–[3], [8-10], \cite{key1,key2}
BRACKET_CITE_GROUP_REGEX = re.compile(
    r"\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\](?:\s*(?:[-–—]{1,3}|,)\s*\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\])*"
)
LATEX_CITE_REGEX = re.compile(
    r"\\(?:cite[tp]?|Cite[tp]?|textcite|parencite|autocite)\*?(?:\[[^\]]*\])?\{([^}]+)\}"
)
AUTHOR_YEAR_CITE_REGEX = re.compile(
    r"\([A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?(?:;\s*[A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?)*\)"
)

# Cross-references: Fig. 1, Figure 2(a), Table 1, Eq. (10), Theorem 2, Section 3.2, Appendix A, 图 1, 表 1, 式(2)
# Strict separation: requires explicit numeric ID (with optional sub-label) or strict uppercase Appendix letter after whitespace/paren.
# Prevents false matches on normal English words like 'secretly' (Sec+retly) or 'algorithm for'.
CROSS_REF_REGEX = re.compile(
    r"(?:"
    r"\b(?:[Ff]ig(?:ure)?s?\.?|[Tt]ables?|[Tt]abs?\.?|[Ee]q(?:uation)?s?\.?|[Tt]heorems?|[Ll]emmas?|[Cc]orollaries|[Pp]ropositions?|[Dd]efinitions?|[Aa]lgorithms?|[Ss]ections?|[Ss]ecs?\.?|[Aa]ppendix|[Aa]ppendices)"
    r"(?:"
    r"(?:\s+|(?<=\.))(?:\(\s*[A-Za-z]?\d+(?:\.\d+)*\s*\)|[A-Za-z]?\d+(?:\.\d+)*(?:\([a-z0-9]+\))?|[A-Z](?=\s|$|[.,:;]))"
    r"|"
    r"\(\s*\d+(?:\.\d+)*\s*\)"
    r")"
    r"(?:\s*[-–—]\s*(?:\(\s*[A-Za-z]?\d+(?:\.\d+)*\s*\)|[A-Za-z]?\d+(?:\.\d+)*))?"
    r"|"
    r"(?:图|表|续表|式|公式|定理|引理|推论|算法|附录)\s*[\(\（]?\s*[A-Za-z]?\d+(?:\.\d+)*\s*[\)\）]?"
    r"|"
    r"第\s*[0-9一二三四五六七八九十.]+\s*(?:节|章)"
    r")"
)
LATEX_REF_REGEX = re.compile(
    r"(?:(?:[Ff]ig(?:ure)?s?\.?|[Tt]ables?|[Tt]abs?\.?|[Ee]q(?:uation)?s?\.?|[Ss]ections?|[Ss]ecs?\.?|[Tt]heorems?|[Aa]ppendix|图|表|式|公式|节)\s*~?\s*\(?\s*)?\\(?:eq|fig|tab|sec|auto|v|c|C)?ref\*?\{([^}]+)\}\)?"
)

# Inline math patterns
DOLLAR_MATH_REGEX = re.compile(r"\$([^$\n]{1,120})\$")
SUB_SUP_EXPR_REGEX = re.compile(r"(?<![A-Za-z]/)\b[A-Za-z\\][A-Za-z0-9\\]*(?:_\{[^}]+\}|_[A-Za-z0-9]+|\^\{[^}]+\}|\^[A-Za-z0-9]+)+(?:\([^\)]+\))?")
GREEK_AND_OP_REGEX = re.compile(r"[αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ⟨⟩∥∈∉≤≥≈≠≡⊗⊕]")
BIG_O_REGEX = re.compile(r"\b[OΘΩ]\([^)\n]{1,40}\)")

# Numeric / scientific notation / units (supports compound slash/exponent units like GB/s, mg/mL, m/s^2 and escaped \%)
SCI_NUM_REGEX = re.compile(r"(?<![A-Za-z0-9_])([+-]?\d+(?:\.\d+)?(?:\s*[×xX*]\s*10\^\{?[+-]?\d+\}?|[eE][+-]?\d+)|10\^\{?[+-]?\d+\}?)(?![A-Za-z0-9_])")
NUM_UNIT_REGEX = re.compile(
    r"(?<![A-Za-z0-9_\.])([+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:(\\?%)|([‰]|°[CF°]?|(?=[A-Za-zμΩÅ])[A-Za-zμΩÅ]{1,10}(?:\s*/\s*[A-Za-zμΩÅ]{1,8}(?:\^\{?[+-]?[0-9]+\}?|[²³])?)?))?(?![A-Za-z0-9_]|(?:\.\d))"
)


def _mask_span(text_chars, start, end):
    """Replace matched span with spaces to prevent double-extraction as plain numbers."""
    for i in range(start, end):
        if text_chars[i] != "\n":
            text_chars[i] = " "


def _expand_bracket_citations(raw_str):
    """Expand '[8]-[10]', '[7]---[9]', '[1]–[3]', or '[3, 4]' into sorted unique citation IDs."""
    ids = []
    # Check range between separate brackets: [8]-[10], [7]---[9], [1]–[3]
    range_between = re.findall(r"\[\s*(\d+)\s*\]\s*[-–—]{1,3}\s*\[\s*(\d+)\s*\]", raw_str)
    for a_s, b_s in range_between:
        a, b = int(a_s), int(b_s)
        if 0 < b - a <= 50:
            for k in range(a, b + 1):
                ids.append(str(k))

    # Check range inside single bracket: [8-10], [8---10]
    inner_blocks = re.findall(r"\[([^\]]+)\]", raw_str)
    for blk in inner_blocks:
        parts = [p.strip() for p in blk.split(",")]
        for p in parts:
            rm = re.match(r"^(\d+)\s*[-–—]{1,3}\s*(\d+)$", p)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                if 0 < b - a <= 50:
                    for k in range(a, b + 1):
                        ids.append(str(k))
            else:
                for d in re.findall(r"\d+", p):
                    ids.append(d)
    # Deduplicate preserving order
    seen = set()
    res = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            res.append(cid)
    return res


def _parse_cross_ref(raw_str):
    """Classify cross reference type and extract canonical numeric/alphanumeric ID."""
    low = raw_str.lower()
    if any(k in low for k in ("fig", "图")):
        r_type = "figure"
    elif any(k in low for k in ("tab", "表", "续表")):
        r_type = "table"
    elif any(k in low for k in ("eq", "式", "公式")):
        r_type = "equation"
    elif any(k in low for k in ("theorem", "lemma", "corol", "prop", "def", "定理", "引理", "推论")):
        r_type = "theorem"
    elif any(k in low for k in ("alg", "算法")):
        r_type = "algorithm"
    else:
        r_type = "section"

    # Extract core identifier(s)
    id_matches = re.findall(r"[A-Za-z]?\d+(?:\.\d+)*(?:\([a-z0-9]+\))?", raw_str)
    ref_id = id_matches[0] if id_matches else raw_str
    return {
        "raw": raw_str.strip(),
        "ref_type": r_type,
        "ref_id": ref_id,
    }


def extract_unit_fingerprints(unit, invariants, locked_terms=None):
    """Extract all deterministic guard fingerprints from a single unit."""
    text = unit.get("source_text", "")
    u_type = unit.get("unit_type", "paragraph")
    chars = list(text)

    # 1. Extract Citations & mask
    citations = []
    for m in BRACKET_CITE_GROUP_REGEX.finditer(text):
        raw_c = m.group(0).strip()
        exp_ids = _expand_bracket_citations(raw_c)
        citations.append({
            "raw": raw_c,
            "kind": "numeric_bracket",
            "expanded_ids": exp_ids,
        })
        _mask_span(chars, m.start(), m.end())

    for m in LATEX_CITE_REGEX.finditer(text):
        raw_c = m.group(0).strip()
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        citations.append({
            "raw": raw_c,
            "kind": "latex_cite",
            "expanded_ids": keys,
        })
        _mask_span(chars, m.start(), m.end())

    for m in AUTHOR_YEAR_CITE_REGEX.finditer(text):
        raw_c = m.group(0).strip()
        years = re.findall(r"(?:19|20)\d{2}[a-z]?", raw_c)
        citations.append({
            "raw": raw_c,
            "kind": "author_year",
            "expanded_ids": years,
        })
        _mask_span(chars, m.start(), m.end())

    # 2. Extract Cross-References & mask
    cross_refs = []
    masked_text_latex_ref = "".join(chars)
    for m in LATEX_REF_REGEX.finditer(masked_text_latex_ref):
        raw_r = m.group(0).strip()
        lbl = m.group(1).strip()
        low_r = raw_r.lower()
        if any(k in low_r for k in ("fig", "图")):
            r_type = "figure"
        elif any(k in low_r for k in ("tab", "表")):
            r_type = "table"
        elif any(k in low_r for k in ("eq", "式", "公式")):
            r_type = "equation"
        elif any(k in low_r for k in ("thm", "theorem")):
            r_type = "theorem"
        else:
            r_type = "section"
        cross_refs.append({"raw": raw_r, "ref_type": r_type, "ref_id": lbl, "is_latex_ref": True})
        _mask_span(chars, m.start(), m.end())

    masked_text_1 = "".join(chars)
    for m in CROSS_REF_REGEX.finditer(masked_text_1):
        raw_r = m.group(0).strip()
        cross_refs.append(_parse_cross_ref(raw_r))
        _mask_span(chars, m.start(), m.end())

    # 3. Extract Inline Math & mask
    inline_math = []
    masked_text_2 = "".join(chars)
    for m in DOLLAR_MATH_REGEX.finditer(masked_text_2):
        expr = m.group(0).strip()
        inline_math.append({"raw": expr, "kind": "latex_inline"})
        _mask_span(chars, m.start(), m.end())

    masked_text_3 = "".join(chars)
    for m in SUB_SUP_EXPR_REGEX.finditer(masked_text_3):
        expr = m.group(0).strip()
        inline_math.append({"raw": expr, "kind": "sub_sup_symbol"})
        _mask_span(chars, m.start(), m.end())

    masked_text_4 = "".join(chars)
    for m in BIG_O_REGEX.finditer(masked_text_4):
        expr = m.group(0).strip()
        inline_math.append({"raw": expr, "kind": "complexity"})
        _mask_span(chars, m.start(), m.end())

    # Also detect standalone greek characters/math operators
    greek_hits = sorted(list(set(GREEK_AND_OP_REGEX.findall("".join(chars)))))
    for g in greek_hits:
        inline_math.append({"raw": g, "kind": "greek_or_operator"})

    # 4. Extract Numbers & Units on remaining masked text (skip author/affiliation metadata)
    numbers_units = []
    si_units_set = set(u.lower() for u in invariants.get("si_and_academic_units", []))

    if u_type != "metadata":
        # Mask leading section numbers on headings so "1. Introduction" isn't treated as data number
        if u_type == "heading":
            hm = re.match(r"^\s*(?:[0-9]+(?:\.[0-9]+)*\.?)\s+", "".join(chars))
            if hm:
                _mask_span(chars, hm.start(), hm.end())

        masked_for_nums = "".join(chars)

        # 4a. Scientific notation first
        for m in SCI_NUM_REGEX.finditer(masked_for_nums):
            raw_n = m.group(0).strip()
            numbers_units.append({
                "raw": raw_n,
                "norm_value": raw_n.replace(" ", ""),
                "unit": "",
                "kind": "scientific_notation",
            })
            _mask_span(chars, m.start(), m.end())

        # 4b. Standard numbers & compound/SI units
        masked_for_nums2 = "".join(chars)
        for m in NUM_UNIT_REGEX.finditer(masked_for_nums2):
            num_str = m.group(1)
            pct_str = m.group(2) or ""
            unit_raw = (m.group(3) or "").strip()
            norm_val = num_str.replace(",", "")
            valid_unit = ""

            if pct_str:
                valid_unit = "%"
            elif unit_raw:
                compact_u = re.sub(r"\s*/\s*", "/", unit_raw)
                if compact_u in ("‰", "°C", "°F", "°") or compact_u.lower() in si_units_set:
                    valid_unit = compact_u
                elif "/" in compact_u:
                    num_u, den_u = compact_u.split("/", 1)
                    den_base = re.sub(r"(?:\^\{?[+-]?\d+\}?|[²³])$", "", den_u)
                    if num_u.lower() in si_units_set and den_base.lower() in si_units_set:
                        valid_unit = compact_u
                    elif num_u.lower() in si_units_set:
                        valid_unit = num_u

            sep = " " if (valid_unit and valid_unit not in ("%", "‰", "°") and ("/" in valid_unit or re.match(r"^[^\d]*\d[\d,\.]*\s+", m.group(0)))) else ""
            raw_match = f"{num_str}{sep}{valid_unit}" if valid_unit else num_str
            numbers_units.append({
                "raw": raw_match,
                "norm_value": norm_val,
                "unit": valid_unit,
                "kind": "numeric_with_unit" if valid_unit else "numeric",
            })

    # 5. Extract High-Risk Logic Markers (Negation / Qualifier / Concession-Causal)
    logic_hits = {"negation": [], "qualifier_bound": [], "concession_causal": []}
    logic_rules = invariants.get("logic_markers", {})
    text_low = text.lower()

    # Detect source language of this unit
    is_zh_src = bool(re.search(r"[\u4e00-\u9fa5]", text)) and len(re.findall(r"[A-Za-z]+", text)) < len(re.findall(r"[\u4e00-\u9fa5]", text))
    src_key = "zh" if is_zh_src else "en"

    # Mask misleading non-negation / non-qualifier Chinese compounds before matching single-char triggers
    zh_logic_text = re.sub(r"不同|不仅|不断|不止|不亚于|不妨|不时|不免|不愧|无论|无非|无限|无意|非常|非但|只不过", "  ", text)

    for category in ("negation", "qualifier_bound", "concession_causal"):
        triggers = logic_rules.get(category, {}).get(src_key, [])
        found = []
        for trig in triggers:
            if src_key == "en":
                pat = r"\b" + re.escape(trig) + r"\b"
                if re.search(pat, text_low):
                    found.append(trig)
            else:
                if trig in zh_logic_text:
                    found.append(trig)
        if found:
            logic_hits[category] = sorted(list(set(found)))

    # 6. Locked Terms
    terms_list = locked_terms or []

    return {
        "unit_id": unit["unit_id"],
        "unit_type": u_type,
        "page_span": unit.get("page_span", [1, 1]),
        "numbers_units": numbers_units,
        "citations": citations,
        "cross_refs": cross_refs,
        "inline_math": inline_math,
        "logic_markers": logic_hits,
        "locked_terms": terms_list,
    }


def run_entity_guard(work_dir, enrich_batches=False):
    """Run fingerprint extraction across all units and optionally enrich batch payloads."""
    units_path = os.path.join(work_dir, "source_units.json")
    if not os.path.exists(units_path):
        raise FileNotFoundError(f"source_units.json not found in {work_dir}")

    with open(units_path, "r", encoding="utf-8") as f:
        units_data = json.load(f)
    units = units_data.get("units", [])
    case_id = units_data.get("case_id", "unknown")

    invariants = load_invariants()

    # Load compiled glossary if available
    compiled_path = os.path.join(work_dir, "glossary_compiled.json")
    unit_term_index = {}
    terms_catalog = {}
    if os.path.exists(compiled_path):
        with open(compiled_path, "r", encoding="utf-8") as f:
            c_data = json.load(f)
            unit_term_index = c_data.get("unit_term_index", {})
            for t in c_data.get("terms", []):
                terms_catalog[t["term_id"]] = t

    fingerprints = {}
    stats = {
        "total_units_fingerprinted": 0,
        "total_numbers_units": 0,
        "total_citations": 0,
        "total_cross_refs": 0,
        "total_inline_math": 0,
        "units_with_negation": 0,
        "units_with_qualifier": 0,
        "units_with_locked_terms": 0,
    }

    for u in units:
        if u.get("translate_policy") != "translate":
            continue
        u_id = u["unit_id"]
        u_terms = unit_term_index.get(u_id, [])
        fp = extract_unit_fingerprints(u, invariants, locked_terms=u_terms)
        fingerprints[u_id] = fp

        stats["total_units_fingerprinted"] += 1
        stats["total_numbers_units"] += len(fp["numbers_units"])
        stats["total_citations"] += len(fp["citations"])
        stats["total_cross_refs"] += len(fp["cross_refs"])
        stats["total_inline_math"] += len(fp["inline_math"])
        if fp["logic_markers"]["negation"]:
            stats["units_with_negation"] += 1
        if fp["logic_markers"]["qualifier_bound"]:
            stats["units_with_qualifier"] += 1
        if fp["locked_terms"]:
            stats["units_with_locked_terms"] += 1

    fp_out_path = os.path.join(work_dir, "unit_fingerprints.json")
    with open(fp_out_path, "w", encoding="utf-8") as f:
        json.dump({
            "case_id": case_id,
            "stats": stats,
            "fingerprints": fingerprints,
        }, f, ensure_ascii=False, indent=2)

    # Optionally enrich batches/batch_*.json
    batches_enriched = 0
    if enrich_batches:
        batches_dir = os.path.join(work_dir, "batches")
        batch_files = sorted(glob.glob(os.path.join(batches_dir, "batch_*.json")))
        for bf in batch_files:
            with open(bf, "r", encoding="utf-8") as f:
                b_data = json.load(f)

            batch_glossary_map = {}
            for u in b_data.get("units", []):
                u_id = u["unit_id"]
                fp = fingerprints.get(u_id)
                if not fp:
                    continue

                # Build compact guard hints per unit
                must_keep_citations = [c["raw"] for c in fp["citations"]]
                must_keep_refs = [r["raw"] for r in fp["cross_refs"]]
                must_keep_nums = [n["raw"] for n in fp["numbers_units"]]
                must_keep_math = [m["raw"] for m in fp["inline_math"] if m["kind"] != "greek_or_operator"]
                logic_warnings = {}
                for k, v in fp["logic_markers"].items():
                    if v:
                        logic_warnings[k] = v

                u["guard_hints"] = {
                    "locked_terms": [
                        {
                            "source": lt["source_term"],
                            "target": lt["canonical_target"],
                            "do_not_translate": lt["do_not_translate"],
                        }
                        for lt in fp["locked_terms"]
                    ],
                    "must_keep_numbers_units": must_keep_nums,
                    "must_keep_citations": must_keep_citations,
                    "must_keep_cross_refs": must_keep_refs,
                    "must_keep_inline_math": must_keep_math,
                    "high_risk_logic": logic_warnings,
                }

                for lt in fp["locked_terms"]:
                    tid = lt["term_id"]
                    if tid not in batch_glossary_map:
                        batch_glossary_map[tid] = {
                            "term_id": tid,
                            "source_term": lt["source_term"],
                            "canonical_target": lt["canonical_target"],
                            "forbidden_targets": lt["forbidden_targets"],
                            "do_not_translate": lt["do_not_translate"],
                        }

            b_data["batch_glossary"] = list(batch_glossary_map.values())
            with open(bf, "w", encoding="utf-8") as f:
                json.dump(b_data, f, ensure_ascii=False, indent=2)
            batches_enriched += 1

    print(f"[entity_guard] Fingerprinted {stats['total_units_fingerprinted']} units; enriched {batches_enriched} batches -> {fp_out_path}")
    return {"stats": stats, "batches_enriched": batches_enriched}


def main():
    parser = argparse.ArgumentParser(description="Deterministic Entity & Logic Fingerprint Guard")
    parser.add_argument("--work-dir", required=True, help="Work directory containing source_units.json")
    parser.add_argument("--enrich-batches", action="store_true", help="Inject guard_hints and batch_glossary into batch JSON files")
    args = parser.parse_args()

    run_entity_guard(args.work_dir, enrich_batches=args.enrich_batches)


if __name__ == "__main__":
    main()
