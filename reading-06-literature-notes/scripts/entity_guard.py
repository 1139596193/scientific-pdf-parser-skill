#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entity_guard.py - Deterministic Fingerprint Extraction & Claim-to-Anchor Verification Guard
Part of the Scientific Literature Notes Skill (task_06_notes).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import json
import re
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
RESOURCES_CANDIDATES = [
    os.path.join(BASE_DIR, "resources", "academic_invariants.json"),
    os.path.join(BASE_DIR, "references", "academic_invariants.json"),
]


def load_invariants():
    """Load logic markers, SI units, and invariants from resources/references."""
    for path in RESOURCES_CANDIDATES:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {"logic_markers": {}, "si_and_academic_units": []}


# ----------------------------------------------------------------------
# 1. Deterministic Entity Extractors (Masked Sequential Pipeline)
# ----------------------------------------------------------------------

BRACKET_CITE_GROUP_REGEX = re.compile(
    r"\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\](?:\s*(?:[-–—]{1,3}|,)\s*\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\])*"
)
LATEX_CITE_REGEX = re.compile(
    r"\\(?:cite[tp]?|Cite[tp]?|textcite|parencite|autocite)\*?(?:\[[^\]]*\])?\{([^}]+)\}"
)
AUTHOR_YEAR_CITE_REGEX = re.compile(
    r"\([A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?(?:;\s*[A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?)*\)"
)

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

DOLLAR_MATH_REGEX = re.compile(r"\$([^$\n]{1,120})\$")
SUB_SUP_EXPR_REGEX = re.compile(
    r"(?<![A-Za-z]/)\b[A-Za-z\\][A-Za-z0-9\\]*(?:_\{[^}]+\}|_[A-Za-z0-9]+|\^\{[^}]+\}|\^[A-Za-z0-9]+)+(?:\([^\)]+\))?"
)
GREEK_AND_OP_REGEX = re.compile(r"[αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ⟨⟩∥∈∉≤≥≈≠≡⊗⊕]")
BIG_O_REGEX = re.compile(r"\b[OΘΩ]\([^)\n]{1,40}\)")

SCI_NUM_REGEX = re.compile(
    r"(?<![A-Za-z0-9_])([+-]?\d+(?:\.\d+)?(?:\s*[×xX*]\s*10\^\{?[+-]?\d+\}?|[eE][+-]?\d+)|10\^\{?[+-]?\d+\}?)(?![A-Za-z0-9_])"
)
NUM_UNIT_REGEX = re.compile(
    r"(?<![A-Za-z0-9_\.])([+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:(\\?%)|([‰]|°[CF°]?|(?=[A-Za-zμΩÅ])[A-Za-zμΩÅ]{1,10}(?:\s*/\s*[A-Za-zμΩÅ]{1,8}(?:\^\{?[+-]?[0-9]+\}?|[²³])?)?))?(?![A-Za-z0-9_]|(?:\.\d))"
)


def _mask_span(text_chars, start, end):
    """Replace matched span with spaces to prevent double-extraction as plain numbers."""
    for i in range(start, end):
        if text_chars[i] != "\n":
            text_chars[i] = " "


def _expand_bracket_citations(raw_str):
    """Expand '[8]-[10]', '[7]---[9]', '[1]–[3]', or '[3, 4]' into unique citation IDs."""
    ids = []
    range_between = re.findall(r"\[\s*(\d+)\s*\]\s*[-–—]{1,3}\s*\[\s*(\d+)\s*\]", raw_str)
    for a_s, b_s in range_between:
        a, b = int(a_s), int(b_s)
        if 0 < b - a <= 50:
            for k in range(a, b + 1):
                ids.append(str(k))

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

    id_matches = re.findall(r"[A-Za-z]?\d+(?:\.\d+)*(?:\([a-z0-9]+\))?", raw_str)
    ref_id = id_matches[0] if id_matches else raw_str
    return {
        "raw": raw_str.strip(),
        "ref_type": r_type,
        "ref_id": ref_id,
    }


def extract_text_fingerprints(text, block_type="paragraph", invariants=None):
    """Extract deterministic entity and logic fingerprints from arbitrary text."""
    if invariants is None:
        invariants = load_invariants()
    text = text or ""
    chars = list(text)

    # 1. Citations
    citations = []
    for m in BRACKET_CITE_GROUP_REGEX.finditer(text):
        raw_c = m.group(0).strip()
        citations.append({
            "raw": raw_c,
            "kind": "numeric_bracket",
            "expanded_ids": _expand_bracket_citations(raw_c),
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

    # 2. Cross-References
    cross_refs = []
    masked_latex = "".join(chars)
    for m in LATEX_REF_REGEX.finditer(masked_latex):
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

    masked_1 = "".join(chars)
    for m in CROSS_REF_REGEX.finditer(masked_1):
        cross_refs.append(_parse_cross_ref(m.group(0).strip()))
        _mask_span(chars, m.start(), m.end())

    # 3. Inline Math
    inline_math = []
    for m in DOLLAR_MATH_REGEX.finditer("".join(chars)):
        inline_math.append({"raw": m.group(0).strip(), "kind": "latex_inline"})
        _mask_span(chars, m.start(), m.end())

    for m in SUB_SUP_EXPR_REGEX.finditer("".join(chars)):
        inline_math.append({"raw": m.group(0).strip(), "kind": "sub_sup_symbol"})
        _mask_span(chars, m.start(), m.end())

    for m in BIG_O_REGEX.finditer("".join(chars)):
        inline_math.append({"raw": m.group(0).strip(), "kind": "complexity"})
        _mask_span(chars, m.start(), m.end())

    for g in sorted(set(GREEK_AND_OP_REGEX.findall("".join(chars)))):
        inline_math.append({"raw": g, "kind": "greek_or_operator"})

    # 4. Numbers & Compound Units
    numbers_units = []
    si_units_set = set(u.lower() for u in invariants.get("si_and_academic_units", []))

    if block_type != "metadata":
        if block_type == "heading":
            hm = re.match(r"^\s*(?:[0-9]+(?:\.[0-9]+)*\.?)\s+", "".join(chars))
            if hm:
                _mask_span(chars, hm.start(), hm.end())

        for m in SCI_NUM_REGEX.finditer("".join(chars)):
            raw_n = m.group(0).strip()
            numbers_units.append({
                "raw": raw_n,
                "norm_value": raw_n.replace(" ", ""),
                "unit": "",
                "kind": "scientific_notation",
            })
            _mask_span(chars, m.start(), m.end())

        for m in NUM_UNIT_REGEX.finditer("".join(chars)):
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

    # 5. Negation / Qualifier / Concession-Causal Logic Markers (supports cross-lingual polarity alignment)
    logic_hits = {"negation": [], "qualifier_bound": [], "concession_causal": []}
    logic_rules = invariants.get("logic_markers", {})
    text_low = text.lower()
    zh_logic_text = re.sub(
        r"不同|不仅|不断|不止|不亚于|不妨|不时|不免|不愧|不变|不确定|不足为|不当|不均|不等|"
        r"无论|无非|无限|无意|无监督|无线|无损|无偏|无向|无穷|无噪声|无纠缠|无约束|"
        r"非常|非但|非线性|非对称|非马尔可夫|非负|非参数|非局部|只不过",
        "  ",
        text,
    )
    zh_strong_neg_triggers = [
        "不", "未", "无", "没有", "无法", "难以", "未能", "并非", "不可", "非显著", "不能",
        "绝不", "并不", "并未", "不会", "缺乏", "缺少", "失败", "不可能", "不含", "不显著",
    ]

    has_zh = bool(re.search(r"[\u4e00-\u9fa5]", text))
    has_en = bool(re.search(r"[A-Za-z]{2,}", text))
    langs_to_check = []
    if has_en:
        langs_to_check.append("en")
    if has_zh:
        langs_to_check.append("zh")
    if not langs_to_check:
        langs_to_check = ["en"]

    for category in ("negation", "qualifier_bound", "concession_causal"):
        found = []
        for lang_key in langs_to_check:
            triggers = list(logic_rules.get(category, {}).get(lang_key, []))
            if category == "negation" and lang_key == "zh":
                triggers = sorted(set(triggers) | set(zh_strong_neg_triggers))
            for trig in triggers:
                if lang_key == "en":
                    if re.search(r"\b" + re.escape(trig) + r"\b", text_low):
                        found.append(trig)
                else:
                    if trig in zh_logic_text:
                        found.append(trig)
        if found:
            logic_hits[category] = sorted(set(found))

    return {
        "numbers_units": numbers_units,
        "citations": citations,
        "cross_refs": cross_refs,
        "inline_math": inline_math,
        "logic_markers": logic_hits,
    }


def extract_unit_fingerprints(record, invariants=None, locked_terms=None):
    """Extract fingerprints from an anchor block or unit dictionary."""
    text = record.get("text") or record.get("source_text") or ""
    b_type = record.get("block_type") or record.get("unit_type") or "paragraph"
    fp = extract_text_fingerprints(text, block_type=b_type, invariants=invariants)
    fp["anchor_id"] = record.get("anchor_id") or record.get("unit_id") or ""
    fp["block_type"] = b_type
    fp["page"] = record.get("page") or (record.get("page_span", [1, 1])[0])
    if locked_terms is not None:
        fp["locked_terms"] = locked_terms
    return fp


# ----------------------------------------------------------------------
# 2. Claim-vs-Anchor Deterministic Comparison Helper (for note_auditor)
# ----------------------------------------------------------------------

def _to_float_safe(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def _decimal_places(num_str):
    s = str(num_str).strip()
    if "." in s:
        frac = s.split(".", 1)[1]
        return len(re.sub(r"[^0-9]", "", frac))
    return 0


def compare_claim_against_anchors(claim_text, anchor_texts, invariants=None):
    """
    Compare a structured note claim against the text(s) of its cited anchor(s).
    Checks numeric/unit consistency, cross-ref fidelity, and negation/qualifier retention.
    """
    if invariants is None:
        invariants = load_invariants()
    if isinstance(anchor_texts, str):
        combined_anchor = anchor_texts
    else:
        combined_anchor = "\n".join(t for t in anchor_texts if t)

    c_fp = extract_text_fingerprints(claim_text, block_type="paragraph", invariants=invariants)
    a_fp = extract_text_fingerprints(combined_anchor, block_type="paragraph", invariants=invariants)

    # Index anchor numbers by normalized string and float value
    a_nums = a_fp["numbers_units"]
    a_norm_set = {n["norm_value"] for n in a_nums}
    a_floats = [(n, _to_float_safe(n["norm_value"])) for n in a_nums]

    unsupported_numbers = []
    unit_mismatches = []

    for cn in c_fp["numbers_units"]:
        c_val = cn["norm_value"]
        c_unit = cn["unit"].lower()
        c_flt = _to_float_safe(c_val)

        matched_items = []
        pct_equiv_matched = False
        if c_val in a_norm_set:
            matched_items = [an for an in a_nums if an["norm_value"] == c_val]
        elif c_flt is not None:
            c_dec = _decimal_places(c_val)
            tol = 0.51 * (10 ** (-c_dec)) if c_dec > 0 else 1e-6
            matched_items = [an for an, af in a_floats if af is not None and abs(af - c_flt) <= tol]

            # Decimal <-> Percentage equivalence (e.g. 0.85 <-> 85%, 0.953 <-> 95.3%)
            if not matched_items and c_unit == "%":
                target_dec = c_flt / 100.0
                tol_dec = max(1e-6, (0.51 * (10 ** (-c_dec)) if c_dec > 0 else 0.005) / 100.0)
                equiv = [an for an, af in a_floats if af is not None and not an["unit"] and 0.0 <= af <= 1.000001 and abs(af - target_dec) <= tol_dec]
                if equiv:
                    matched_items = equiv
                    pct_equiv_matched = True
            elif not matched_items and not c_unit and 0.0 <= c_flt <= 1.0:
                target_pct = c_flt * 100.0
                tol_pct = max(1e-6, (0.51 * (10 ** (-c_dec)) if c_dec > 0 else 1e-4) * 100.0)
                equiv = [an for an, af in a_floats if af is not None and an["unit"] == "%" and abs(af - target_pct) <= tol_pct]
                if equiv:
                    matched_items = equiv
                    pct_equiv_matched = True

        if not matched_items:
            # Ignore small ordinal/list numbers (1, 2, 3...) without units unless large or decimal/unit-bearing
            is_trivial_int = (not c_unit) and c_val.isdigit() and int(c_val) <= 12
            if not is_trivial_int:
                unsupported_numbers.append(cn["raw"])
            continue

        if c_unit and not pct_equiv_matched:
            a_units = {m["unit"].lower() for m in matched_items if m["unit"]}
            if a_units and c_unit not in a_units:
                unit_mismatches.append({"claim": cn["raw"], "anchor_units": sorted(a_units)})

    # Cross-reference check
    a_ref_keys = {(r["ref_type"], r["ref_id"].lower()) for r in a_fp["cross_refs"]}
    cross_ref_alerts = []
    for cr in c_fp["cross_refs"]:
        key = (cr["ref_type"], cr["ref_id"].lower())
        if key not in a_ref_keys:
            cross_ref_alerts.append(cr["raw"])

    # Negation & Qualifier polarity check (flag when source has negation/qualifier absent from claim)
    a_neg = a_fp["logic_markers"]["negation"]
    c_neg = c_fp["logic_markers"]["negation"]
    negation_alerts = []
    if bool(a_neg) != bool(c_neg):
        negation_alerts.append({
            "anchor_negation": a_neg,
            "claim_negation": c_neg,
        })

    a_qual = a_fp["logic_markers"]["qualifier_bound"]
    c_qual = c_fp["logic_markers"]["qualifier_bound"]
    qualifier_alerts = []
    if a_qual and not c_qual:
        qualifier_alerts.append({
            "anchor_qualifiers": a_qual,
            "claim_qualifiers": c_qual,
        })

    passed = not (unsupported_numbers or unit_mismatches or cross_ref_alerts or negation_alerts)
    return {
        "passed": passed,
        "unsupported_numbers": unsupported_numbers,
        "unit_mismatches": unit_mismatches,
        "cross_ref_alerts": cross_ref_alerts,
        "negation_alerts": negation_alerts,
        "qualifier_alerts": qualifier_alerts,
        "claim_fingerprints": c_fp,
        "anchor_fingerprints": a_fp,
    }


def run_entity_guard(work_dir):
    """Run fingerprint extraction across evidence_index.json (or source_units.json)."""
    ev_path = os.path.join(work_dir, "evidence_index.json")
    su_path = os.path.join(work_dir, "source_units.json")
    invariants = load_invariants()

    if os.path.exists(ev_path):
        with open(ev_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("anchors", [])
        case_id = data.get("case_id", "unknown")
    elif os.path.exists(su_path):
        with open(su_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("units", [])
        case_id = data.get("case_id", "unknown")
    else:
        raise FileNotFoundError(f"Neither evidence_index.json nor source_units.json found in {work_dir}")

    fingerprints = {}
    stats = {
        "total_anchors_fingerprinted": 0,
        "total_numbers_units": 0,
        "total_citations": 0,
        "total_cross_refs": 0,
        "total_inline_math": 0,
        "anchors_with_negation": 0,
        "anchors_with_qualifier": 0,
    }

    for rec in records:
        fp = extract_unit_fingerprints(rec, invariants=invariants)
        aid = fp["anchor_id"]
        fingerprints[aid] = fp
        stats["total_anchors_fingerprinted"] += 1
        stats["total_numbers_units"] += len(fp["numbers_units"])
        stats["total_citations"] += len(fp["citations"])
        stats["total_cross_refs"] += len(fp["cross_refs"])
        stats["total_inline_math"] += len(fp["inline_math"])
        if fp["logic_markers"]["negation"]:
            stats["anchors_with_negation"] += 1
        if fp["logic_markers"]["qualifier_bound"]:
            stats["anchors_with_qualifier"] += 1

    out_path = os.path.join(work_dir, "anchor_fingerprints.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "case_id": case_id,
            "stats": stats,
            "fingerprints": fingerprints,
        }, f, ensure_ascii=False, indent=2)

    print(f"[entity_guard] Fingerprinted {stats['total_anchors_fingerprinted']} anchors -> {out_path}")
    return {"stats": stats, "out_path": out_path}


def main():
    parser = argparse.ArgumentParser(description="Deterministic Entity & Logic Fingerprint Guard")
    parser.add_argument("--work-dir", required=True, help="Work directory containing evidence_index.json or source_units.json")
    args = parser.parse_args()
    run_entity_guard(args.work_dir)


if __name__ == "__main__":
    main()
