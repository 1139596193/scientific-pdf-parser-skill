#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entity_guard.py - KG Entity Canonicalization, Acronym Binding, Non-Merge Guardrail & Epistemic Consistency Guard
Part of the Literature Knowledge Graph Skill (task_10_kg).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ALIGNMENT_RULES_PATH = os.path.join(BASE_DIR, "references", "entity_alignment_rules.json")
ONTOLOGY_PATH = os.path.join(BASE_DIR, "references", "kg_ontology.json")

DEFAULT_INVARIANTS = {
    "logic_markers": {
        "negation": {
            "en": [
                "not", "no", "never", "neither", "nor", "without", "cannot", "can't",
                "fail to", "fails to", "failed to", "failing to", "hardly", "rarely",
                "seldom", "insignificant", "absence of", "none of", "free of", "lack of",
                "unable to", "impossible",
            ],
            "zh": [
                "不", "无", "未", "没", "并非", "绝不", "既不", "也不", "不含", "缺乏",
                "缺少", "显著性不足", "不显著", "失败", "未能", "难以", "很少", "罕见",
                "无法", "不能", "不可能", "免于", "没有",
            ],
        },
        "qualifier_bound": {
            "en": [
                "only", "solely", "exclusively", "merely", "at least", "at most",
                "bounded by", "up to", "no more than", "no less than", "strictly less",
                "strictly greater", "unless", "except", "provided that", "subject to",
                "if and only if", "iff", "given that", "under the condition", "under the assumption",
            ],
            "zh": [
                "仅", "只", "只有", "唯一", "仅仅", "至少", "不少于", "不低于", "至多",
                "最多", "不超过", "不高于", "不小于", "不大于", "不多于", "高达",
                "严格小于", "严格大于", "除非", "除了", "前提是", "只要", "条件下",
                "受限于", "取决于", "当且仅当",
            ],
        },
        "concession_causal": {
            "en": [
                "although", "though", "even though", "whereas", "while", "however",
                "nevertheless", "nonetheless", "despite", "in spite of", "in contrast",
                "conversely", "therefore", "thus", "hence", "consequently", "due to", "because of",
            ],
            "zh": [
                "尽管", "虽然", "即便", "即使", "然而", "但是", "但", "不过", "相反",
                "相比之下", "反之", "而", "因此", "从而", "由此", "所以", "由于", "因为", "归因于",
            ],
        },
    },
    "si_and_academic_units": [
        "s", "ms", "us", "μs", "ns", "ps", "fs", "min", "h", "hr", "hrs",
        "Hz", "kHz", "MHz", "GHz", "THz", "J", "kJ", "MJ", "eV", "keV", "MeV", "GeV", "TeV",
        "W", "mW", "kW", "MW", "m", "km", "cm", "mm", "um", "μm", "nm", "pm", "Å",
        "kg", "g", "mg", "ug", "μg", "ng", "pg", "Da", "kDa", "mol", "mmol", "μmol", "nmol",
        "M", "mM", "μM", "nM", "ppm", "ppb", "wt%", "vol%", "L", "mL", "uL", "μL",
        "bit", "bits", "B", "KB", "MB", "GB", "TB", "PB", "bps", "kbps", "Mbps", "Gbps",
        "qubit", "qubits", "ebit", "K", "°C", "°F", "Pa", "kPa", "MPa", "GPa", "bar", "atm", "Torr",
        "V", "mV", "kV", "A", "mA", "μA", "Ω", "kΩ", "MΩ", "F", "μF", "nF", "pF",
        "H", "T", "mT", "G", "dB", "dBm", "rad", "deg", "°", "%", "‰",
        "p-value", "SD", "SE", "CI", "AUC", "ROC", "F1", "BLEU", "ROUGE",
    ],
}


def load_invariants():
    return DEFAULT_INVARIANTS


def load_alignment_rules(path=None):
    target = path or ALIGNMENT_RULES_PATH
    if os.path.isfile(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"non_merge_guardrails": [], "cross_paper_merge_sufficient_conditions": []}


def load_ontology(path=None):
    target = path or ONTOLOGY_PATH
    if os.path.isfile(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"entity_types": [], "relations": [], "epistemic_statuses": []}


# ----------------------------------------------------------------------
# 1. Deterministic Fingerprint & Ligature Normalization
# ----------------------------------------------------------------------

def _normalize_ligatures(text):
    if not text:
        return ""
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\ufb00", "ff").replace("\ufb03", "ffi").replace("\ufb04", "ffl")
    text = text.replace("\ufb05", "st").replace("\ufb06", "st")
    return re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)


BRACKET_CITE_GROUP_REGEX = re.compile(
    r"\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\](?:\s*(?:[-–—]{1,3}|,)\s*\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\])*"
)
LATEX_CITE_REGEX = re.compile(r"\\(?:cite[tp]?|Cite[tp]?|textcite|parencite|autocite)\*?(?:\[[^\]]*\])?\{([^}]+)\}")
AUTHOR_YEAR_CITE_REGEX = re.compile(
    r"\([A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?(?:;\s*[A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?)*\)"
)
CROSS_REF_REGEX = re.compile(
    r"(?:\b(?:[Ff]ig(?:ure)?s?\.?|[Tt]ables?|[Tt]abs?\.?|[Ee]q(?:uation)?s?\.?|[Ss]ections?|[Ss]ecs?\.?|[Aa]ppendix)"
    r"(?:\s+|(?<=\.))(?:\(\s*[A-Za-z]?\d+(?:\.\d+)*\s*\)|[A-Za-z]?\d+(?:\.\d+)*(?:\([a-z0-9]+\))?|[A-Z](?=\s|$|[.,:;]))"
    r"|(?:图|表|续表|式|公式|定理|算法|附录)\s*[\(\（]?\s*[A-Za-z]?\d+(?:\.\d+)*\s*[\)\）]?)"
)
LATEX_REF_REGEX = re.compile(r"\\(?:eq|fig|tab|sec|auto|v|c|C)?ref\*?\{([^}]+)\}")
DOLLAR_MATH_REGEX = re.compile(r"\$([^$\n]{1,120})\$")
SUB_SUP_EXPR_REGEX = re.compile(r"(?<![A-Za-z]/)\b[A-Za-z\\][A-Za-z0-9\\]*(?:_\{[^}]+\}|_[A-Za-z0-9]+|\^\{[^}]+\}|\^[A-Za-z0-9]+)+(?:\([^\)]+\))?")
GREEK_AND_OP_REGEX = re.compile(r"[αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ⟨⟩∥∈∉≤≥≈≠≡⊗⊕]")
BIG_O_REGEX = re.compile(r"\b[OΘΩ]\([^)\n]{1,40}\)")
SCI_NUM_REGEX = re.compile(r"(?<![A-Za-z0-9_])([+-]?\d+(?:\.\d+)?(?:\s*[×xX*]\s*10\^\{?[+-]?\d+\}?|[eE][+-]?\d+)|10\^\{?[+-]?\d+\}?)(?![A-Za-z0-9_])")
NUM_UNIT_REGEX = re.compile(
    r"(?<![A-Za-z0-9_\.])([+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:(\\?%)|([‰]|°[CF°]?|(?=[A-Za-zμΩÅ])[A-Za-zμΩÅ]{1,10}(?:\s*/\s*[A-Za-zμΩÅ]{1,8}(?:\^\{?[+-]?[0-9]+\}?|[²³])?)?))?(?![A-Za-z0-9_]|(?:\.\d))"
)


def _mask_span(text_chars, start, end):
    for i in range(start, end):
        if text_chars[i] != "\n":
            text_chars[i] = " "


def _expand_bracket_citations(raw_str):
    ids = []
    for a_s, b_s in re.findall(r"\[\s*(\d+)\s*\]\s*[-–—]{1,3}\s*\[\s*(\d+)\s*\]", raw_str):
        a, b = int(a_s), int(b_s)
        if 0 < b - a <= 50:
            ids.extend(str(k) for k in range(a, b + 1))
    for blk in re.findall(r"\[([^\]]+)\]", raw_str):
        for p in [x.strip() for x in blk.split(",")]:
            rm = re.match(r"^(\d+)\s*[-–—]{1,3}\s*(\d+)$", p)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                if 0 < b - a <= 50:
                    ids.extend(str(k) for k in range(a, b + 1))
            else:
                ids.extend(re.findall(r"\d+", p))
    return list(dict.fromkeys(ids))


def _parse_cross_ref(raw_str):
    low = raw_str.lower()
    if any(k in low for k in ("fig", "图")):
        r_type = "figure"
    elif any(k in low for k in ("tab", "表", "续表")):
        r_type = "table"
    elif any(k in low for k in ("eq", "式", "公式")):
        r_type = "equation"
    else:
        r_type = "section"
    id_matches = re.findall(r"[A-Za-z]?\d+(?:\.\d+)*(?:\([a-z0-9]+\))?", raw_str)
    return {"raw": raw_str.strip(), "ref_type": r_type, "ref_id": id_matches[0] if id_matches else raw_str}


def extract_text_fingerprints(text, block_type="paragraph", invariants=None):
    """Extract deterministic numbers, units, citations, cross-refs, inline math, and logic markers."""
    if invariants is None:
        invariants = load_invariants()
    text = _normalize_ligatures(text or "")
    chars = list(text)

    citations = []
    for pat, kind in (
        (BRACKET_CITE_GROUP_REGEX, "numeric_bracket"),
        (LATEX_CITE_REGEX, "latex_cite"),
        (AUTHOR_YEAR_CITE_REGEX, "author_year"),
    ):
        for m in pat.finditer(text):
            raw_c = m.group(0).strip()
            exp = _expand_bracket_citations(raw_c) if kind == "numeric_bracket" else (
                [k.strip() for k in m.group(1).split(",") if k.strip()] if kind == "latex_cite" else re.findall(r"(?:19|20)\d{2}[a-z]?", raw_c)
            )
            citations.append({"raw": raw_c, "kind": kind, "expanded_ids": exp})
            _mask_span(chars, m.start(), m.end())

    cross_refs = []
    for m in LATEX_REF_REGEX.finditer("".join(chars)):
        cross_refs.append({"raw": m.group(0).strip(), "ref_type": "latex_ref", "ref_id": m.group(1).strip(), "is_latex_ref": True})
        _mask_span(chars, m.start(), m.end())
    for m in CROSS_REF_REGEX.finditer("".join(chars)):
        cross_refs.append(_parse_cross_ref(m.group(0).strip()))
        _mask_span(chars, m.start(), m.end())

    inline_math = []
    for pat, kind in ((DOLLAR_MATH_REGEX, "latex_inline"), (SUB_SUP_EXPR_REGEX, "sub_sup_symbol"), (BIG_O_REGEX, "complexity")):
        for m in pat.finditer("".join(chars)):
            inline_math.append({"raw": m.group(0).strip(), "kind": kind})
            _mask_span(chars, m.start(), m.end())
    for g in sorted(set(GREEK_AND_OP_REGEX.findall("".join(chars)))):
        inline_math.append({"raw": g, "kind": "greek_or_operator"})

    numbers_units = []
    si_units_set = set(u.lower() for u in invariants.get("si_and_academic_units", []))
    if block_type != "metadata":
        for m in SCI_NUM_REGEX.finditer("".join(chars)):
            raw_n = m.group(0).strip()
            numbers_units.append({"raw": raw_n, "norm_value": raw_n.replace(" ", ""), "unit": "", "kind": "scientific_notation"})
            _mask_span(chars, m.start(), m.end())
        for m in NUM_UNIT_REGEX.finditer("".join(chars)):
            num_str = m.group(1)
            pct_str = m.group(2) or ""
            unit_raw = (m.group(3) or "").strip()
            norm_val = num_str.replace(",", "")
            valid_unit = "%" if pct_str else ""
            if not valid_unit and unit_raw:
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
            numbers_units.append({
                "raw": f"{num_str} {valid_unit}".strip() if valid_unit else num_str,
                "norm_value": norm_val,
                "unit": valid_unit,
                "kind": "numeric_with_unit" if valid_unit else "numeric",
            })

    logic_hits = {"negation": [], "qualifier_bound": [], "concession_causal": []}
    logic_rules = invariants.get("logic_markers", {})
    text_low = text.lower()
    zh_neg_text = re.sub(
        r"不同|不仅|不断|不止|不变|不确定|不超过|不小于|不大于|不少于|不多于|不低于|不高于|"
        r"无关|无论|无限|无监督|无线|无损|无偏|无向|无穷|无噪声|无约束|无条件|非常|非均匀|非线性|非负|非零|而非",
        "  ",
        text,
    )
    en_neg_text = re.sub(
        r"\b(?:different|at\s+most|at\s+least|bounded\s+by|no\s+more\s+than|no\s+less\s+than|"
        r"non[-\s]?(?:uniform|linear|markovian|local|negative|zero|empty|trivial|convex|parametric)|"
        r"unsupervised|unbiased|noiseless|lossless|unconditional(?:ly)?|unrelated)\b",
        " ",
        text_low,
    )

    for category in ("negation", "qualifier_bound", "concession_causal"):
        found = []
        for lang_key in ("en", "zh"):
            for trig in logic_rules.get(category, {}).get(lang_key, []):
                if lang_key == "en":
                    hay = en_neg_text if category == "negation" else text_low
                    if re.search(r"\b" + re.escape(trig) + r"\b", hay):
                        found.append(trig)
                else:
                    hay = zh_neg_text if category == "negation" else text
                    if trig in hay:
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
    text = record.get("text") or record.get("source_text") or ""
    b_type = record.get("block_type") or record.get("unit_type") or "paragraph"
    fp = extract_text_fingerprints(text, block_type=b_type, invariants=invariants)
    fp["anchor_id"] = record.get("anchor_id") or record.get("unit_id") or ""
    fp["block_type"] = b_type
    fp["page"] = record.get("page")
    if locked_terms is not None:
        fp["locked_terms"] = locked_terms
    return fp


def _to_float_safe(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def _decimal_places(num_str):
    s = str(num_str).strip()
    if "." in s:
        return len(re.sub(r"[^0-9]", "", s.split(".", 1)[1]))
    return 0


_WORD_NUMERALS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100,
}


def _extract_word_numeral_floats(text):
    res = []
    low = (text or "").lower()
    for w, v in _WORD_NUMERALS.items():
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            res.append(float(v))
    return res


def compare_claim_against_anchors(claim_text, anchor_texts, invariants=None, section_context=None):
    """Compare claim numbers, units, cross-refs, and polarity against cited anchor text(s)."""
    if invariants is None:
        invariants = load_invariants()
    claim_text = _normalize_ligatures(claim_text or "")
    combined_anchor = _normalize_ligatures(anchor_texts if isinstance(anchor_texts, str) else "\n".join(t for t in anchor_texts if t))

    c_fp = extract_text_fingerprints(claim_text, invariants=invariants)
    a_fp = extract_text_fingerprints(combined_anchor, invariants=invariants)

    a_nums = a_fp["numbers_units"]
    a_norm_set = {n["norm_value"] for n in a_nums}
    a_floats = [(n, _to_float_safe(n["norm_value"])) for n in a_nums]
    a_word_floats = _extract_word_numeral_floats(combined_anchor)

    unsupported_numbers, unit_mismatches = [], []
    for cn in c_fp["numbers_units"]:
        c_val, c_unit, c_flt = cn["norm_value"], cn["unit"].lower(), _to_float_safe(cn["norm_value"])
        matched = [an for an in a_nums if an["norm_value"] == c_val] if c_val in a_norm_set else []
        pct_equiv = False
        if not matched and c_flt is not None:
            c_dec = _decimal_places(c_val)
            tol = 0.51 * (10 ** (-c_dec)) if c_dec > 0 else 1e-6
            matched = [an for an, af in a_floats if af is not None and abs(af - c_flt) <= tol]
            if not matched and c_unit == "%":
                t_dec = c_flt / 100.0
                matched = [an for an, af in a_floats if af is not None and not an["unit"] and abs(af - t_dec) <= 0.005]
                pct_equiv = bool(matched)
            if not matched and c_flt in a_word_floats:
                matched = [{"norm_value": c_val, "unit": ""}]
        if not matched:
            if not c_unit and c_val.isdigit() and int(c_val) <= 12:
                continue
            unsupported_numbers.append(cn["raw"])
        elif c_unit and not pct_equiv:
            a_units = {m["unit"].lower() for m in matched if m["unit"]}
            if a_units and c_unit not in a_units:
                unit_mismatches.append({"claim": cn["raw"], "anchor_units": sorted(a_units)})

    negation_alerts = []
    if bool(a_fp["logic_markers"]["negation"]) != bool(c_fp["logic_markers"]["negation"]):
        negation_alerts.append({
            "direction": "dropped_negation" if a_fp["logic_markers"]["negation"] else "added_negation",
            "anchor_negation": a_fp["logic_markers"]["negation"],
            "claim_negation": c_fp["logic_markers"]["negation"],
        })

    return {
        "passed": not (unsupported_numbers or unit_mismatches or negation_alerts),
        "unsupported_numbers": unsupported_numbers,
        "unit_mismatches": unit_mismatches,
        "negation_alerts": negation_alerts,
        "claim_fingerprints": c_fp,
        "anchor_fingerprints": a_fp,
    }


_QUALIFIER_PATTERNS = [
    ("temperature", re.compile(r"[+-]?\d+(?:\.\d+)?\s*(?:°\s*[CF]|K)\b")),
    ("ph", re.compile(r"\bpH\s*(?:[=<>≤≥≈~:]\s*)?\d+(?:\.\d+)?(?:\s*[-–—]\s*\d+(?:\.\d+)?)?\b", re.I)),
    ("concentration_dose", re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg/kg(?:/day)?|mg/mL|μg/mL|ug/mL|ng/mL|mM|μM|uM|nM|pM|wt%|vol%|ppm|ppb)\b", re.I)),
    ("sample_size", re.compile(r"(?:\b[nN]\s*[=≥≤<>]\s*\d+(?:,\d{3})*\b|样本量\s*(?:为|n\s*=)?\s*\d+)")),
    ("significance", re.compile(r"(?:\b(?:p|P|FDR|q)\s*[<≤=>]\s*0?\.\d+|\b\d+%\s*CI\b)", re.I)),
    ("time_window", re.compile(r"(?:\b(?:for|after|over|within|during|at)\s+\d+(?:\.\d+)?\s*(?:ms|s|sec|min|h|hr|hrs|hours?|days?|weeks?|months?|years?)\b|(?:持续|经过)\s*\d+(?:\.\d+)?\s*(?:秒|分钟|小时|天|周|月|年))", re.I)),
    ("experimental_context", re.compile(r"(?:\b(?:in\s+vitro|in\s+vivo|ex\s+vivo|in\s+silico)\b|体外|体内|离体)", re.I)),
]


def extract_scientific_qualifiers(text):
    norm_txt = _normalize_ligatures(text or "")
    hits, by_kind, seen = [], {}, set()
    for kind, pat in _QUALIFIER_PATTERNS:
        for m in pat.finditer(norm_txt):
            val = re.sub(r"\s+", " ", m.group(0)).strip()
            k = (kind, val.lower())
            if k not in seen:
                seen.add(k)
                hits.append({"kind": kind, "raw": val})
                by_kind.setdefault(kind, []).append(val)
    return {"has_qualifiers": bool(hits), "count": len(hits), "qualifiers": hits, "by_kind": by_kind}


_STRONG_CAUSAL_EN = [r"\bcauses?\b", r"\bproves?\b", r"\bproven\b", r"\bleads?\s+to\b", r"\binduces?\b", r"\btriggers?\b", r"\binhibits?\b", r"\bactivates?\b", r"\bresults?\s+in\b"]
_STRONG_CAUSAL_ZH = [r"导致", r"引起", r"引发", r"致使", r"证明", r"证实", r"诱发", r"抑制", r"激活"]
_HEDGED_ASSOC_EN = [r"\bcorrelates?\s+with\b", r"\bassociated\s+with\b", r"\bsuggests?\b", r"\bmay\b", r"\bmight\b", r"\bcould\b", r"\bappears?\s+to\b", r"\bhypothesize[sd]?\b"]
_HEDGED_ASSOC_ZH = [r"相关", r"关联", r"提示", r"可能", r"或许", r"推测", r"假说", r"倾向于"]


def _match_patterns(text, en_pats, zh_pats):
    norm = _normalize_ligatures(text or "")
    hits = []
    for p in en_pats:
        for m in re.finditer(p, norm, re.I):
            hits.append(m.group(0).strip())
    for p in zh_pats:
        for m in re.finditer(p, norm):
            hits.append(m.group(0).strip())
    return list(dict.fromkeys(hits))


def check_causal_strength_upgrade(claim_text, evidence_text):
    c_causal = _match_patterns(claim_text, _STRONG_CAUSAL_EN, _STRONG_CAUSAL_ZH)
    e_causal = _match_patterns(evidence_text, _STRONG_CAUSAL_EN, _STRONG_CAUSAL_ZH)
    e_assoc = _match_patterns(evidence_text, _HEDGED_ASSOC_EN, _HEDGED_ASSOC_ZH)
    upgraded = bool(c_causal and not e_causal and e_assoc)
    return {
        "upgraded": upgraded,
        "severity": "high" if upgraded else "none",
        "claim_causal_markers": c_causal,
        "evidence_causal_markers": e_causal,
        "evidence_associative_markers": e_assoc,
        "reason": f"Claim upgrades hedged/associative evidence {e_assoc} to causal {c_causal}." if upgraded else "",
    }


# ----------------------------------------------------------------------
# 2. KG Entity Canonicalization, Acronym Binding & Non-Merge Guardrails
# ----------------------------------------------------------------------

_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")


def canonicalize_entity_name(name, entity_type=None):
    """
    Deterministic canonical key generator for KG entities.
    Preserves stoichiometric numbers, versions, and prefixes while normalizing
    ligatures, dashes, whitespace, subscripts, and safe English plural suffixes.
    """
    if not name:
        return ""
    s = _normalize_ligatures(str(name)).replace("\u00ad", "")
    s = s.translate(_SUBSCRIPT_MAP)
    s = s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    s = re.sub(r"[_^]\{([^}]+)\}", r"\1", s)
    s = re.sub(r"\s*([/\-])\s*", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip(" \t\r\n.,;:\"'")
    low = s.lower()

    # Safe singularization for common English multi-letter nouns (skip formulas/codes/short words)
    if entity_type != "Researcher" and re.match(r"^[a-z][a-z\s\-]{4,}$", low):
        tokens = low.split()
        last = tokens[-1]
        if last.endswith("ies") and len(last) > 5:
            tokens[-1] = last[:-3] + "y"
        elif last.endswith("s") and not last.endswith(("ss", "us", "is", "sis", "ics")) and len(last) > 4:
            tokens[-1] = last[:-1]
        low = " ".join(tokens)
    return low


_ACRONYM_SKIP = {"FIG", "TAB", "EQ", "SEC", "PDF", "XML", "DOI", "URL", "USA", "AND", "FOR", "THE", "WITH", "PH", "SD", "SE", "CI", "II", "III", "IV", "VI"}
_ACRONYM_FULL_FIRST_REGEX = re.compile(r"\b([A-Za-z][A-Za-z0-9\-]{2,}(?:\s+[A-Za-z0-9\-]{2,}){1,7})\s*\(\s*([A-Za-z][A-Za-z0-9\-]{1,14})\s*\)")
_ACRONYM_SHORT_FIRST_REGEX = re.compile(r"\b([A-Za-z][A-Za-z0-9\-]{1,14})\s*\(\s*([A-Za-z][A-Za-z0-9\-]{2,}(?:\s+[A-Za-z0-9\-]{2,}){1,7})\s*\)")
_ACRONYM_ZH_REGEX = re.compile(r"([\u4e00-\u9fa5]{2,18})\s*[（\(]\s*([A-Za-z][A-Za-z0-9\-]{1,14})\s*[）\)]")


def _acronym_matches_expansion(acronym, expansion):
    acr_clean = re.sub(r"[^A-Za-z0-9]", "", acronym)
    upper_chars = [c for c in acr_clean if c.isupper()]
    if len(acr_clean) < 2 or len(upper_chars) < 2 or acr_clean.upper() in _ACRONYM_SKIP:
        return False
    words = [w for w in re.split(r"[\s\-]+", expansion.strip()) if w and w.lower() not in ("of", "the", "and", "in", "for", "on", "with", "to", "a", "an")]
    if len(words) < 2:
        return False
    initials = "".join(w[0].upper() for w in words if w[0].isalnum())
    exp_upper = re.sub(r"[^A-Z0-9]", "", expansion.upper())
    core_seq = "".join(c for c in acr_clean if c.isupper() or c.isdigit())
    it = iter(exp_upper)
    in_order = all(c in it for c in core_seq)
    core_in_initials = all(c in initials for c in core_seq if c.isalpha()) or (core_seq in exp_upper)
    return in_order and core_in_initials and (acr_clean[0].upper() == words[0][0].upper() or core_seq[0] in initials)


_EXPANSION_STOP_PREFIX = {
    "a", "an", "the", "this", "that", "these", "those", "we", "our", "they", "their",
    "using", "used", "use", "via", "with", "for", "from", "by", "in", "on", "of", "to", "as",
    "synthesized", "prepared", "developed", "proposed", "introduced", "called", "termed", "named", "known",
}


def _trim_expansion_to_acronym(acronym, expansion):
    """Trim leading verbs/articles/context words from an expansion phrase so it aligns tightly with acronym."""
    raw_tokens = expansion.strip().split()
    while len(raw_tokens) > 2 and raw_tokens[0].lower().strip(",;:") in _EXPANSION_STOP_PREFIX:
        raw_tokens.pop(0)
    core_first = acronym[0].upper() if acronym else ""
    # Prefer the tightest suffix starting with acronym's first letter if it still matches
    for i in range(len(raw_tokens)):
        cand = " ".join(raw_tokens[i:])
        if raw_tokens[i] and raw_tokens[i][0].upper() == core_first and _acronym_matches_expansion(acronym, cand):
            return cand
    return " ".join(raw_tokens)


def extract_acronym_bindings(blocks):
    """
    Extract intra-document Full Name (ACR) / ACR (Full Name) bindings per source_id,
    retaining exact anchor_id provenance so bindings never blindly leak across papers.
    """
    by_source = {}
    all_bindings = []
    seen = set()

    for blk in blocks or []:
        txt = blk.get("text") or blk.get("source_text") or ""
        if not txt:
            continue
        sid = blk.get("source_id") or blk.get("doc_id") or "D1"
        aid = blk.get("anchor_id") or blk.get("unit_id") or ""

        for m in _ACRONYM_FULL_FIRST_REGEX.finditer(txt):
            raw_full, acr = m.group(1).strip(), m.group(2).strip()
            full_name = _trim_expansion_to_acronym(acr, raw_full)
            if _acronym_matches_expansion(acr, full_name):
                key = (sid, acr.upper(), canonicalize_entity_name(full_name))
                if key not in seen:
                    seen.add(key)
                    rec = {"source_id": sid, "anchor_id": aid, "acronym": acr, "full_name": full_name, "canonical_full": key[2]}
                    all_bindings.append(rec)
                    by_source.setdefault(sid, {})[acr.upper()] = rec

        for m in _ACRONYM_SHORT_FIRST_REGEX.finditer(txt):
            acr, raw_full = m.group(1).strip(), m.group(2).strip()
            full_name = _trim_expansion_to_acronym(acr, raw_full)
            if _acronym_matches_expansion(acr, full_name):
                key = (sid, acr.upper(), canonicalize_entity_name(full_name))
                if key not in seen:
                    seen.add(key)
                    rec = {"source_id": sid, "anchor_id": aid, "acronym": acr, "full_name": full_name, "canonical_full": key[2]}
                    all_bindings.append(rec)
                    by_source.setdefault(sid, {})[acr.upper()] = rec

        for m in _ACRONYM_ZH_REGEX.finditer(txt):
            full_name, acr = m.group(1).strip(), m.group(2).strip()
            if acr.upper() not in _ACRONYM_SKIP:
                key = (sid, acr.upper(), canonicalize_entity_name(full_name))
                if key not in seen:
                    seen.add(key)
                    rec = {"source_id": sid, "anchor_id": aid, "acronym": acr, "full_name": full_name, "canonical_full": key[2]}
                    all_bindings.append(rec)
                    by_source.setdefault(sid, {})[acr.upper()] = rec

    return {"bindings": all_bindings, "by_source": by_source}


def _collect_guardrail_terms(rules):
    term_to_rule = {}
    for g in (rules or {}).get("non_merge_guardrails", []):
        rid = g.get("rule_id", "GUARD_GENERIC")
        for t in g.get("terms", []):
            term_to_rule[canonicalize_entity_name(t)] = rid
    # Built-in safety terms even if rules dict is partial
    fallback_terms = [
        "control group", "control", "baseline", "catalyst", "sample", "samples",
        "mice", "patients", "cnn", "resnet", "transformer", "dataset", "accuracy",
        "model", "algorithm", "对照组", "基线", "催化剂", "样品", "样本", "数据集", "模型",
    ]
    for t in fallback_terms:
        term_to_rule.setdefault(canonicalize_entity_name(t), "GUARD_GENERIC_BUILTIN")
    return term_to_rule


def _extract_version_or_formula_tokens(name):
    return set(re.findall(r"(?:v\d+(?:\.\d+)*|\b\d+(?:\.\d+)?(?:\s*[kK]|°C|nm|mm|mM|%)?|[A-Z][a-z]?\d+)", name or ""))


def evaluate_entity_merge_safety(entity_a, entity_b, rules=None):
    """
    Evaluate whether two entity records (same-paper or cross-paper) can be merged.
    Returns one of:
      - 'safe_merge'
      - 'context_conflict_do_not_merge'
      - 'needs_explicit_justification'
    """
    if rules is None:
        rules = load_alignment_rules()

    name_a = (entity_a.get("name") or entity_a.get("label") or "").strip()
    name_b = (entity_b.get("name") or entity_b.get("label") or "").strip()
    type_a = entity_a.get("entity_type") or ""
    type_b = entity_b.get("entity_type") or ""
    sid_a = entity_a.get("source_id") or "D1"
    sid_b = entity_b.get("source_id") or "D1"
    cond_a = canonicalize_entity_name(entity_a.get("condition") or entity_a.get("experimental_context") or "")
    cond_b = canonicalize_entity_name(entity_b.get("condition") or entity_b.get("experimental_context") or "")
    exp_a = canonicalize_entity_name(entity_a.get("expanded_name") or entity_a.get("acronym_expansion") or "")
    exp_b = canonicalize_entity_name(entity_b.get("expanded_name") or entity_b.get("acronym_expansion") or "")

    # 0. Twin format prohibition
    if entity_a.get("is_verification_twin") or entity_b.get("is_verification_twin"):
        return {
            "verdict": "context_conflict_do_not_merge",
            "rule_id": "GUARD_SAME_PAPER_MULTI_FORMAT_TWIN",
            "reason": "Verification twin file (e.g. XML mirror) cannot be treated as cross-paper entity alignment.",
        }

    # 1. Entity type incompatibility
    if type_a and type_b and type_a != type_b:
        return {
            "verdict": "context_conflict_do_not_merge",
            "rule_id": "GUARD_TYPE_MISMATCH",
            "reason": f"Incompatible entity types: {type_a} vs {type_b}.",
        }

    canon_a = canonicalize_entity_name(name_a, type_a)
    canon_b = canonicalize_entity_name(name_b, type_b)
    cross_paper = (sid_a != sid_b)

    # 2. Generic / context-sensitive term guardrail
    guard_map = _collect_guardrail_terms(rules)
    if canon_a in guard_map or canon_b in guard_map:
        matched_rule = guard_map.get(canon_a) or guard_map.get(canon_b)
        if cross_paper or (cond_a and cond_b and cond_a != cond_b):
            return {
                "verdict": "context_conflict_do_not_merge",
                "rule_id": matched_rule,
                "reason": (
                    f"Generic/context-sensitive term ('{name_a}' vs '{name_b}') across papers or differing experimental conditions "
                    f"({sid_a} vs {sid_b}) MUST remain separate instance nodes."
                ),
            }

    # 3. Finding_or_Claim never auto-merges across papers
    if type_a == "Finding_or_Claim" and cross_paper:
        return {
            "verdict": "context_conflict_do_not_merge",
            "rule_id": "GUARD_CLAIM_NEVER_MERGES",
            "reason": "Claims/Findings from different papers must remain separate nodes linked via supports/contradicts/replicates.",
        }

    # 4. Stoichiometric / version / condition conflict check
    toks_a = _extract_version_or_formula_tokens(name_a)
    toks_b = _extract_version_or_formula_tokens(name_b)
    if toks_a and toks_b and toks_a != toks_b and not (exp_a and exp_b and exp_a == exp_b):
        return {
            "verdict": "context_conflict_do_not_merge",
            "rule_id": "GUARD_NUMERIC_OR_QUALIFIER_CONFLICT",
            "reason": f"Version/stoichiometric token mismatch: {sorted(toks_a)} vs {sorted(toks_b)}.",
        }
    if cond_a and cond_b and cond_a != cond_b and type_a in ("Material_or_Chemical", "Method_or_Model", "Dataset_or_Sample", "Metric_or_Property", "Experimental_Condition"):
        return {
            "verdict": "context_conflict_do_not_merge",
            "rule_id": "GUARD_CONDITION_MISMATCH",
            "reason": f"Conflicting experimental conditions: '{cond_a}' vs '{cond_b}'.",
        }

    # 5. Verified acronym expansion match (e.g. "Graph Neural Network" <-> "GNN" with consistent expansion)
    if exp_a and exp_b and exp_a == exp_b and exp_a not in guard_map:
        return {
            "verdict": "safe_merge",
            "rule_id": "MERGE_VERIFIED_ACRONYM_EXPANSION",
            "reason": f"Consistent explicit acronym expansion '{exp_a}' across {sid_a} and {sid_b}.",
        }
    if exp_a and exp_a == canon_b and exp_a not in guard_map:
        return {
            "verdict": "safe_merge",
            "rule_id": "MERGE_VERIFIED_ACRONYM_EXPANSION",
            "reason": f"Entity '{name_a}' expands directly to '{name_b}'.",
        }
    if exp_b and exp_b == canon_a and exp_b not in guard_map:
        return {
            "verdict": "safe_merge",
            "rule_id": "MERGE_VERIFIED_ACRONYM_EXPANSION",
            "reason": f"Entity '{name_b}' expands directly to '{name_a}'.",
        }

    # 6. Exact non-generic canonical match
    if canon_a and canon_a == canon_b:
        if type_a == "Researcher" and cross_paper:
            id_a = entity_a.get("orcid") or entity_a.get("affiliation")
            id_b = entity_b.get("orcid") or entity_b.get("affiliation")
            if id_a and id_b and canonicalize_entity_name(id_a) == canonicalize_entity_name(id_b):
                return {
                    "verdict": "safe_merge",
                    "rule_id": "MERGE_RESEARCHER_IDENTITY",
                    "reason": "Researcher full name and corroborating affiliation/ORCID match.",
                }
            return {
                "verdict": "needs_explicit_justification",
                "rule_id": "CHECK_RESEARCHER_HOMONYM",
                "reason": "Cross-paper researcher name match requires affiliation/ORCID corroboration to rule out homonym.",
            }
        return {
            "verdict": "safe_merge",
            "rule_id": "MERGE_CANONICAL_SPECIFIC_ENTITY",
            "reason": f"Non-generic canonical key '{canon_a}' matches without condition conflict.",
        }

    return {
        "verdict": "needs_explicit_justification",
        "rule_id": "UNRESOLVED_CANDIDATE",
        "reason": f"Surface forms '{name_a}' and '{name_b}' differ without verified shared expansion or identifier.",
    }


def check_epistemic_consistency(edge, block_signals):
    """
    Verify that an edge's epistemic_status and predicate do not violate source block signals:
      - co_occurrence_only is strictly forbidden.
      - Introduction / References / citation-marked blocks cannot be tagged direct_experimental.
      - Speculative/hedged text cannot be upgraded to direct_experimental strong causal edges.
    """
    ep_status = (edge.get("epistemic_status") or "").strip()
    predicate = (edge.get("predicate") or edge.get("relation") or "").strip()
    quote = (edge.get("evidence_quote") or (block_signals or {}).get("text") or "").strip()
    sec_role = ((block_signals or {}).get("section_role") or "").strip().lower()
    has_cite = bool((block_signals or {}).get("has_citation_signal") or (block_signals or {}).get("has_citation_markers"))
    has_spec = bool((block_signals or {}).get("has_speculation_signal"))

    issues = []
    if ep_status == "co_occurrence_only":
        issues.append({
            "code": "EPISTEMIC_COOCCURRENCE_FORBIDDEN",
            "severity": "HIGH",
            "message": "co_occurrence_only edges are strictly forbidden in the knowledge graph.",
        })

    if ep_status == "direct_experimental":
        if sec_role == "references":
            issues.append({
                "code": "EPISTEMIC_REF_ZONE_AS_DIRECT",
                "severity": "HIGH",
                "message": "Block in 'references' section cannot support a direct_experimental edge.",
            })
        elif sec_role == "introduction" and has_cite:
            issues.append({
                "code": "EPISTEMIC_INTRO_CITATION_AS_DIRECT",
                "severity": "HIGH",
                "message": "Citation-bearing statement in 'introduction' must be 'reported_prior_work', not 'direct_experimental'.",
            })
        elif has_cite and not re.search(r"\b(?:our\s+(?:results?|experiments?|study)|we\s+(?:observe|measure|find|show|found|measured))\b|本研究|本文实验", quote, re.I):
            issues.append({
                "code": "EPISTEMIC_CITATION_AS_DIRECT",
                "severity": "HIGH",
                "message": "Evidence contains citation markers without first-person empirical attribution; cannot be tagged 'direct_experimental'.",
            })

    strong_causal = {"causes", "inhibits", "activates", "promotes", "suppresses"}
    if predicate in strong_causal and has_spec and ep_status == "direct_experimental":
        issues.append({
            "code": "EPISTEMIC_SPECULATION_UPGRADED_TO_CAUSAL",
            "severity": "HIGH",
            "message": f"Speculative/hedged text cannot be labeled direct_experimental with strong causal predicate '{predicate}'.",
        })

    if predicate in strong_causal and quote:
        up = check_causal_strength_upgrade(f"A {predicate} B", quote)
        if up["upgraded"]:
            issues.append({
                "code": "EPISTEMIC_CAUSAL_OVERCLAIM",
                "severity": "HIGH",
                "message": up["reason"],
            })

    return {
        "consistent": len(issues) == 0,
        "issues": issues,
    }


def run_entity_guard(work_dir):
    ev_path = os.path.join(work_dir, "evidence_index.json")
    if not os.path.exists(ev_path):
        raise FileNotFoundError(f"evidence_index.json not found in {work_dir}")
    with open(ev_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("anchors", [])
    case_id = data.get("case_id", "unknown")
    invariants = load_invariants()

    fingerprints = {}
    for rec in records:
        fp = extract_unit_fingerprints(rec, invariants=invariants)
        fingerprints[fp["anchor_id"]] = fp

    acronyms = extract_acronym_bindings(records)
    out_path = os.path.join(work_dir, "anchor_fingerprints.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "case_id": case_id,
            "fingerprints_count": len(fingerprints),
            "acronym_bindings": acronyms["bindings"],
            "fingerprints": fingerprints,
        }, f, ensure_ascii=False, indent=2)

    print(f"[entity_guard] Fingerprinted {len(fingerprints)} anchors | acronyms={len(acronyms['bindings'])} -> {out_path}")
    return {"fingerprints_count": len(fingerprints), "acronym_count": len(acronyms["bindings"]), "out_path": out_path}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="KG Entity Canonicalization, Non-Merge & Epistemic Guard")
    parser.add_argument("--work-dir", required=True, help="Work directory containing evidence_index.json")
    args = parser.parse_args()
    run_entity_guard(args.work_dir)


if __name__ == "__main__":
    main()
