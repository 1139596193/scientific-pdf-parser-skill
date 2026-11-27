#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
epistemic_entity_guard.py - Deterministic Invariant Extraction & 5-Tier Epistemic Preservation Guard.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "resources")
LADDER_PATH = os.path.join(RESOURCES_DIR, "epistemic_hedging_ladder.json")


def load_epistemic_ladder():
    if os.path.exists(LADDER_PATH):
        try:
            with open(LADDER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"epistemic_ladder": {}, "comparison_direction": {}, "si_and_academic_units": []}


# ----------------------------------------------------------------------
# 1. Deterministic Invariant Extractors
# ----------------------------------------------------------------------

LATEX_CITE_RE = re.compile(
    r"\\(?:cite[tp]?|Cite[tp]?|textcite|parencite|autocite)\*?(?:\[[^\]]*\])?\{([^}]+)\}"
)
BRACKET_CITE_RE = re.compile(
    r"\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\](?:\s*(?:[-–—]{1,3}|,)\s*\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\])*"
)
AUTHOR_YEAR_CITE_RE = re.compile(
    r"\([A-Z][A-Za-z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?(?:;\s*[A-Z][A-Za-z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?)*\)"
)

LATEX_REF_RE = re.compile(
    r"(?:(?:[Ff]ig(?:ure)?s?\.?|[Tt]ables?|[Tt]abs?\.?|[Ee]q(?:uation)?s?\.?|[Ss]ections?|[Ss]ecs?\.?|[Tt]heorems?|[Aa]ppendix|图|表|式|公式|节)\s*~?\s*\(?\s*)?\\(?:eq|fig|tab|sec|auto|v|c|C)?ref\*?\{([^}]+)\}\)?"
)
CROSS_REF_RE = re.compile(
    r"(?:"
    r"\b(?:[Ff]ig(?:ure)?s?\.?|[Tt]ables?|[Tt]abs?\.?|[Ee]q(?:uation)?s?\.?|[Tt]heorems?|[Ll]emmas?|[Cc]orollaries|[Pp]ropositions?|[Dd]efinitions?|[Aa]lgorithms?|[Ss]ections?|[Ss]ecs?\.?|[Aa]ppendix|[Aa]ppendices)"
    r"(?:"
    r"(?:\s+|(?<=\.))(?:\(\s*[A-Za-z]?\d+(?:\.\d+)*[a-zA-Z]?\s*\)|[A-Za-z]?\d+(?:\.\d+)*[a-zA-Z]?(?:\([a-z0-9]+\))?|[A-Z](?=\s|$|[.,:;]))"
    r"|"
    r"\(\s*\d+(?:\.\d+)*[a-zA-Z]?\s*\)"
    r")"
    r"(?:\s*[-–—]\s*(?:\(\s*[A-Za-z]?\d+(?:\.\d+)*[a-zA-Z]?\s*\)|[A-Za-z]?\d+(?:\.\d+)*[a-zA-Z]?))?"
    r"|"
    r"(?:图|表|续表|式|公式|定理|引理|推论|算法|附录)\s*[\(\（]?\s*[A-Za-z]?\d+(?:\.\d+)*[a-zA-Z]?\s*[\)\）]?"
    r"|"
    r"第\s*[0-9一二三四五六七八九十.]+\s*(?:节|章)"
    r")"
)

DISPLAY_MATH_RE = re.compile(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)")
DOLLAR_MATH_RE = re.compile(r"(?<!\\)\$([^$\n]{1,160})(?<!\\)\$")
LATEX_CMD_SPAN_RE = re.compile(r"\\(?:label|textbf|emph|texttt|mathrm|mathbf|mathcal|frac|sum|int|prod)\*?(?:\[[^\]]*\])?\{[^}]*\}(?:\{[^}]*\})?")
SUB_SUP_RE = re.compile(r"(?<![A-Za-z]/)\b[A-Za-z\\][A-Za-z0-9\\]*(?:_\{[^}]+\}|_[A-Za-z0-9]+|\^\{[^}]+\}|\^[A-Za-z0-9]+)+")
URL_DOI_RE = re.compile(r"https?://[^\s\])>]+|\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+|`[^`\n]+`")

STAT_EXPR_RE = re.compile(
    r"(?:\b[pPrRFtZz]\s*(?:<=|>=|<|>|=|≈)\s*[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|"
    r"\bR\^?2\s*=\s*[+-]?\d+(?:\.\d+)?|"
    r"\b\d+(?:\.\d+)?\s*%\s*CI\b)"
)
SCI_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])([+-]?\d+(?:\.\d+)?(?:\s*[×xX*]\s*10\^\{?[+-]?\d+\}?|[eE][+-]?\d+)|10\^\{?[+-]?\d+\}?)(?![A-Za-z0-9_])"
)
NUM_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9_\.])([+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:(\\?%)|([‰]|°[CF°]?|(?=[A-Za-zμΩÅ])[A-Za-zμΩÅ]{1,10}(?:\s*/\s*[A-Za-zμΩÅ]{1,8}(?:\^\{?[+-]?[0-9]+\}?|[²³])?)?))?(?![A-Za-z0-9_]|(?:\.\d))"
)


def _mask(chars, start, end):
    for i in range(start, end):
        if chars[i] != "\n":
            chars[i] = " "


def _expand_bracket_ids(raw):
    ids = []
    for a_s, b_s in re.findall(r"\[\s*(\d+)\s*\]\s*[-–—]{1,3}\s*\[\s*(\d+)\s*\]", raw):
        a, b = int(a_s), int(b_s)
        if 0 < b - a <= 50:
            ids.extend(str(k) for k in range(a, b + 1))
    for blk in re.findall(r"\[([^\]]+)\]", raw):
        for p in blk.split(","):
            p = p.strip()
            rm = re.match(r"^(\d+)\s*[-–—]{1,3}\s*(\d+)$", p)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                if 0 < b - a <= 50:
                    ids.extend(str(k) for k in range(a, b + 1))
            else:
                ids.extend(re.findall(r"\d+", p))
    seen, out = set(), []
    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def check_math_delimiters_balanced(text):
    """Verify $, \\(...\\), \\[...\\] delimiter balance."""
    issues = []
    if not text:
        return True, issues
    unescaped_dollars = len(re.findall(r"(?<!\\)\$", text.replace("$$", "")))
    if unescaped_dollars % 2 != 0:
        issues.append("Unbalanced inline '$' math delimiter.")
    double_dollars = len(re.findall(r"(?<!\\)\$\$", text))
    if double_dollars % 2 != 0:
        issues.append("Unbalanced display '$$' math delimiter.")
    if len(re.findall(r"\\\(", text)) != len(re.findall(r"\\\)", text)):
        issues.append("Unbalanced '\\(' / '\\)' math delimiter.")
    if len(re.findall(r"\\\[", text)) != len(re.findall(r"\\\]", text)):
        issues.append("Unbalanced '\\[' / '\\]' math delimiter.")
    return len(issues) == 0, issues


def extract_invariants(text):
    """
    Deterministically extract citations, cross_refs, inline_math_latex, urls_dois,
    and numbers_units with masking to prevent double counting.
    """
    text = text or ""
    chars = list(text)

    # 1. URLs / DOIs / backtick spans
    urls_dois = []
    for m in URL_DOI_RE.finditer(text):
        urls_dois.append(m.group(0).strip())
        _mask(chars, m.start(), m.end())

    # 2. Inline math & LaTeX spans
    inline_math_latex = []
    for pat in (DISPLAY_MATH_RE, DOLLAR_MATH_RE, LATEX_CMD_SPAN_RE):
        cur = "".join(chars)
        for m in pat.finditer(cur):
            inline_math_latex.append(m.group(0).strip())
            _mask(chars, m.start(), m.end())

    # 3. Citations
    citations = []
    cur = "".join(chars)
    for m in LATEX_CITE_RE.finditer(cur):
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        citations.append({"raw": m.group(0).strip(), "kind": "latex_cite", "keys": keys})
        _mask(chars, m.start(), m.end())

    cur = "".join(chars)
    for m in BRACKET_CITE_RE.finditer(cur):
        raw_c = m.group(0).strip()
        citations.append({"raw": raw_c, "kind": "numeric_bracket", "keys": _expand_bracket_ids(raw_c)})
        _mask(chars, m.start(), m.end())

    cur = "".join(chars)
    for m in AUTHOR_YEAR_CITE_RE.finditer(cur):
        raw_c = m.group(0).strip()
        years = re.findall(r"(?:19|20)\d{2}[a-z]?", raw_c)
        citations.append({"raw": raw_c, "kind": "author_year", "keys": years})
        _mask(chars, m.start(), m.end())

    # 4. Cross-references
    cross_refs = []
    cur = "".join(chars)
    for m in LATEX_REF_RE.finditer(cur):
        cross_refs.append({"raw": m.group(0).strip(), "ref_id": m.group(1).strip(), "kind": "latex_ref"})
        _mask(chars, m.start(), m.end())

    cur = "".join(chars)
    for m in CROSS_REF_RE.finditer(cur):
        raw_r = m.group(0).strip()
        ids = re.findall(r"[A-Za-z]?\d+(?:\.\d+)*[a-zA-Z]?", raw_r)
        ref_id = ids[0] if ids else raw_r
        low = raw_r.lower()
        if any(k in low for k in ("fig", "图")):
            kind = "figure"
        elif any(k in low for k in ("tab", "表", "续表")):
            kind = "table"
        elif any(k in low for k in ("eq", "式", "公式")):
            kind = "equation"
        else:
            kind = "section_or_other"
        cross_refs.append({"raw": raw_r, "ref_id": ref_id, "kind": kind})
        _mask(chars, m.start(), m.end())

    # Remaining sub/superscript expressions
    cur = "".join(chars)
    for m in SUB_SUP_RE.finditer(cur):
        inline_math_latex.append(m.group(0).strip())
        _mask(chars, m.start(), m.end())

    # 5. Numbers, statistical expressions & units
    numbers_units = []
    cur = "".join(chars)
    for m in STAT_EXPR_RE.finditer(cur):
        raw_s = re.sub(r"\s+", " ", m.group(0).strip())
        numbers_units.append({"raw": raw_s, "norm": raw_s.lower().replace(" ", ""), "kind": "stat"})
        _mask(chars, m.start(), m.end())

    cur = "".join(chars)
    for m in SCI_NUM_RE.finditer(cur):
        raw_n = m.group(0).strip()
        numbers_units.append({"raw": raw_n, "norm": raw_n.lower().replace(" ", ""), "kind": "sci"})
        _mask(chars, m.start(), m.end())

    cur = "".join(chars)
    for m in NUM_UNIT_RE.finditer(cur):
        num_str = m.group(1).replace(",", "")
        unit = (m.group(2) or m.group(3) or "").strip()
        # Skip plain small integers only if attached to nothing? No, keep ALL numbers for scientific integrity
        raw_nu = m.group(0).strip()
        norm_nu = f"{num_str}{unit.lower()}" if unit else num_str
        numbers_units.append({"raw": raw_nu, "num": num_str, "unit": unit, "norm": norm_nu, "kind": "num_unit"})
        _mask(chars, m.start(), m.end())

    balanced, bal_issues = check_math_delimiters_balanced(text)
    return {
        "numbers_units": numbers_units,
        "citations": citations,
        "cross_refs": cross_refs,
        "inline_math_latex": inline_math_latex,
        "urls_dois": urls_dois,
        "math_delimiter_balanced": balanced,
        "math_delimiter_issues": bal_issues,
    }


# ----------------------------------------------------------------------
# 2. Epistemic Profile & Hedging Ladder Analysis
# ----------------------------------------------------------------------

def _match_terms(text, terms, is_en=True):
    hits = []
    if not text or not terms:
        return hits
    sorted_terms = sorted(set(terms), key=len, reverse=True)
    masked = text
    for term in sorted_terms:
        if is_en:
            pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.I)
        else:
            pat = re.compile(re.escape(term))
        for m in pat.finditer(masked):
            hits.append(term)
            masked = masked[:m.start()] + (" " * (m.end() - m.start())) + masked[m.end():]
    return hits


_SCOPE_SYNONYM_GROUPS = {
    "only": "only_group",
    "solely": "only_group",
    "exclusively": "only_group",
    "merely": "only_group",
    "仅": "only_group_zh",
    "只": "only_group_zh",
    "只有": "only_group_zh",
    "仅仅": "only_group_zh",
    "at least": "at_least_group",
    "no less than": "at_least_group",
    "至少": "at_least_group_zh",
    "不少于": "at_least_group_zh",
    "不低于": "at_least_group_zh",
    "at most": "at_most_group",
    "no more than": "at_most_group",
    "至多": "at_most_group_zh",
    "最多": "at_most_group_zh",
    "不超过": "at_most_group_zh",
    "不高于": "at_most_group_zh",
    "respectively": "respectively_exact",
    "分别": "respectively_zh_exact",
    "limited to": "limited_group",
    "restricted to": "limited_group",
    "confined to": "limited_group",
    "within": "within_exact",
    "under ambient": "ambient_exact",
    "at ambient": "ambient_exact",
    "in our cohort": "cohort_exact",
    "in this cohort": "cohort_exact",
    "局限于": "limited_zh_group",
    "限于": "limited_zh_group",
    "仅限于": "limited_zh_group",
}


def analyze_epistemic_profile(text, lang=None):
    """Compute epistemic tier (0, 2..5), negation hits, scope bounds, and direction markers."""
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    ladder_data = load_epistemic_ladder()
    ladder = ladder_data.get("epistemic_ladder", {})
    comp_dir = ladder_data.get("comparison_direction", {})

    if not lang:
        zh_cnt = len(re.findall(r"[\u4e00-\u9fff]", text))
        en_cnt = len(re.findall(r"[A-Za-z]", text))
        lang = "zh" if zh_cnt > en_cnt else "en"

    # Negation & scope bounds first (tier 1)
    t1 = ladder.get("tier_1_negation_scope", {})
    neg_en = _match_terms(text, t1.get("negation", {}).get("en", []), is_en=True)

    # Mask Chinese non-negation lexicalized words before scanning ZH negation markers
    zh_neg_text = text
    zh_exclusions = sorted(set(t1.get("negation", {}).get("zh_negation_exclusions", [])), key=len, reverse=True)
    for exc in zh_exclusions:
        if exc and exc in zh_neg_text:
            zh_neg_text = zh_neg_text.replace(exc, " " * len(exc))
    neg_zh = _match_terms(zh_neg_text, t1.get("negation", {}).get("zh", []), is_en=False)
    neg_hits = neg_en + neg_zh

    scope_en = _match_terms(text, t1.get("scope_bounds", {}).get("en", []), is_en=True)
    scope_zh = _match_terms(text, t1.get("scope_bounds", {}).get("zh", []), is_en=False)
    scope_hits = scope_en + scope_zh

    # Tiers 5..2
    tier_defs = [
        (5, "tier_5_absolute_causal"),
        (4, "tier_4_strong_empirical"),
        (3, "tier_3_moderate_associative"),
        (2, "tier_2_cautious_tentative"),
    ]
    tier_hits = {}
    max_tier = 0
    for lvl, key in tier_defs:
        tdata = ladder.get(key, {})
        hits = _match_terms(text, tdata.get("en", []), is_en=True) + _match_terms(text, tdata.get("zh", []), is_en=False)
        if hits:
            tier_hits[key] = hits
            if lvl > max_tier:
                max_tier = lvl

    # Direction markers
    inc_def = comp_dir.get("increase_or_greater", {})
    dec_def = comp_dir.get("decrease_or_less", {})
    inc_hits = _match_terms(text, inc_def.get("en", []), is_en=True) + _match_terms(text, inc_def.get("zh", []), is_en=False)
    dec_hits = _match_terms(text, dec_def.get("en", []), is_en=True) + _match_terms(text, dec_def.get("zh", []), is_en=False)

    return {
        "max_tier": max_tier,
        "tier_hits": tier_hits,
        "negation_count": len(neg_hits),
        "negation_hits": neg_hits,
        "scope_bounds": scope_hits,
        "scope_groups": [_SCOPE_SYNONYM_GROUPS.get(s.lower(), s.lower()) for s in scope_hits],
        "direction_increase": inc_hits,
        "direction_decrease": dec_hits,
    }


# ----------------------------------------------------------------------
# 3. Source vs. Polished Comparison
# ----------------------------------------------------------------------

def _citation_present_in_polished(cite_item, pol_text, pol_inv):
    raw = cite_item["raw"]
    if raw in pol_text:
        return True
    kind = cite_item["kind"]
    keys = cite_item.get("keys", [])
    pol_keys = set()
    for pc in pol_inv["citations"]:
        pol_keys.update(pc.get("keys", []))
    if keys and all(k in pol_keys for k in keys):
        return True
    return False


def _cross_ref_present_in_polished(ref_item, pol_text, pol_inv):
    if ref_item["raw"].lower() in pol_text.lower():
        return True
    rid = ref_item["ref_id"].lower()
    kind = ref_item["kind"]
    for pr in pol_inv["cross_refs"]:
        if pr["ref_id"].lower() == rid and pr["kind"] == kind:
            return True
    return False


def _number_present_in_polished(num_item, pol_text, pol_inv):
    raw = num_item["raw"]
    if raw in pol_text:
        return True
    norm = num_item["norm"]
    pol_norms = {p["norm"] for p in pol_inv["numbers_units"]}
    if norm in pol_norms:
        return True
    # Allow spacing/comma normalization for plain numbers without unit mismatch
    if num_item["kind"] == "num_unit":
        unit = (num_item.get("unit") or "").lower()
        num_val = num_item.get("num", "")
        for p in pol_inv["numbers_units"]:
            if p.get("num") == num_val and (p.get("unit") or "").lower() == unit:
                return True
    return False


# Safe academic double-negation simplification patterns
_DOUBLE_NEG_EN_RE = re.compile(
    r"\b(?:not|no|never)\s+(?:uncommon|insignificant|impossible|unlikely|unlike|infrequent|negligible|trivial|unrelated|without|inconsistent|unexpected|unavailable)\b|"
    r"\b(?:cannot|can't|could not|couldn't)\s+be\s+(?:ignored|overlooked|ruled out|dismissed|excluded|underestimated)\b|"
    r"\bfail(?:s|ed)?\s+to\s+show\s+no\b",
    re.I,
)
_DOUBLE_NEG_ZH_RE = re.compile(
    r"(?:并非|不|绝非)(?:罕见|不可能|不重要|无关|毫无意义|微不足道|偶然)|"
    r"(?:不容|不可|不能)(?:忽视|忽略|否认|排除)"
)


def has_safe_double_negation(text):
    if not text:
        return False
    return bool(_DOUBLE_NEG_EN_RE.search(text) or _DOUBLE_NEG_ZH_RE.search(text))


def compare_epistemic_and_invariants(source_text, polished_text, lang=None, double_neg_allowed=False):
    """
    Compare source_text vs polished_text across invariants & epistemic fidelity.
    Returns list of issue dicts with code, severity, detail, repair_hint.
    """
    issues = []
    src_inv = extract_invariants(source_text)
    pol_inv = extract_invariants(polished_text)

    # 1. Math delimiter integrity
    if not pol_inv["math_delimiter_balanced"]:
        issues.append({
            "code": "MATH_DELIMITER_BROKEN",
            "severity": "HIGH",
            "detail": "; ".join(pol_inv["math_delimiter_issues"]),
            "repair_hint": "Restore balanced $, $$, \\(...\\), or \\[...\\] delimiters matching source.",
        })

    # 2. Inline math / LaTeX spans preserved
    pol_norm_math = {re.sub(r"\s+", "", m) for m in pol_inv["inline_math_latex"]}
    pol_nospace = re.sub(r"\s+", "", polished_text or "")
    for m_expr in src_inv["inline_math_latex"]:
        m_compact = re.sub(r"\s+", "", m_expr)
        if m_compact not in pol_norm_math and m_compact not in pol_nospace:
            issues.append({
                "code": "MATH_LATEX_MISSING",
                "severity": "HIGH",
                "detail": f"Protected math/LaTeX span '{m_expr}' missing or altered in polished text.",
                "repair_hint": f"Keep '{m_expr}' verbatim.",
            })

    # 3. Citations preserved
    for c in src_inv["citations"]:
        if not _citation_present_in_polished(c, polished_text or "", pol_inv):
            issues.append({
                "code": "CITATION_MISSING",
                "severity": "HIGH",
                "detail": f"Citation '{c['raw']}' missing or altered in polished text.",
                "repair_hint": f"Restore exact citation '{c['raw']}'.",
            })

    # 4. Cross-references preserved
    for r in src_inv["cross_refs"]:
        if not _cross_ref_present_in_polished(r, polished_text or "", pol_inv):
            issues.append({
                "code": "CROSS_REF_MISSING",
                "severity": "HIGH",
                "detail": f"Cross-reference '{r['raw']}' missing or altered in polished text.",
                "repair_hint": f"Restore cross-reference '{r['raw']}' (id={r['ref_id']}).",
            })

    # 5. Numbers, stats & units preserved
    for nu in src_inv["numbers_units"]:
        if not _number_present_in_polished(nu, polished_text or "", pol_inv):
            issues.append({
                "code": "NUMBER_UNIT_MISSING",
                "severity": "HIGH",
                "detail": f"Numeric/statistical invariant '{nu['raw']}' missing or modified in polished text.",
                "repair_hint": f"Preserve exact numeric/stat expression '{nu['raw']}'.",
            })

    # 6. Epistemic profile check
    src_ep = analyze_epistemic_profile(source_text, lang=lang)
    pol_ep = analyze_epistemic_profile(polished_text, lang=lang)

    if src_ep["max_tier"] in (2, 3) and pol_ep["max_tier"] in (4, 5):
        issues.append({
            "code": "CLAIM_STRENGTH_ESCALATION",
            "severity": "HIGH",
            "detail": f"Epistemic strength escalated from Tier {src_ep['max_tier']} ({src_ep['tier_hits']}) to Tier {pol_ep['max_tier']} ({pol_ep['tier_hits']}).",
            "repair_hint": "Restore cautious/associative hedging matching original claim strength.",
        })
    elif (
        (src_ep["max_tier"] == 5 and pol_ep["max_tier"] in (2, 3))
        or (
            src_ep["max_tier"] == 4
            and (
                pol_ep["max_tier"] == 2
                or (
                    pol_ep["max_tier"] == 3
                    and "tier_2_cautious_tentative" in pol_ep["tier_hits"]
                    and "tier_2_cautious_tentative" not in src_ep["tier_hits"]
                )
            )
        )
    ):
        issues.append({
            "code": "CLAIM_STRENGTH_WEAKENING",
            "severity": "HIGH",
            "detail": f"Epistemic strength weakened from Tier {src_ep['max_tier']} ({src_ep['tier_hits']}) to Tier {pol_ep['max_tier']} ({pol_ep['tier_hits']}).",
            "repair_hint": "Preserve original empirical assertion strength without unwarranted hedging.",
        })

    # Polarity parity check (with safe academic double-negation simplification whitelist)
    parity_changed = (src_ep["negation_count"] % 2) != (pol_ep["negation_count"] % 2)
    safe_dbl_neg = double_neg_allowed or (
        (has_safe_double_negation(source_text) and not has_safe_double_negation(polished_text))
        or (has_safe_double_negation(polished_text) and not has_safe_double_negation(source_text))
    )
    if parity_changed and not safe_dbl_neg:
        issues.append({
            "code": "POLARITY_FLIP",
            "severity": "HIGH",
            "detail": f"Negation polarity changed: source={src_ep['negation_hits']} vs polished={pol_ep['negation_hits']}.",
            "repair_hint": "Preserve exact positive/negative polarity of the original sentence.",
        })

    # Scope bounds check
    pol_groups = set(pol_ep["scope_groups"])
    for raw_sb, grp in zip(src_ep["scope_bounds"], src_ep["scope_groups"]):
        if grp not in pol_groups:
            issues.append({
                "code": "SCOPE_BOUND_DROPPED",
                "severity": "HIGH",
                "detail": f"Scope/boundary qualifier '{raw_sb}' present in source was dropped in polished text.",
                "repair_hint": f"Restore scope qualifier '{raw_sb}' (or exact equivalent).",
            })

    # Comparison direction check (only if polarity did not intentionally flip)
    if (src_ep["negation_count"] % 2) == (pol_ep["negation_count"] % 2):
        src_inc = bool(src_ep["direction_increase"]) and not bool(src_ep["direction_decrease"])
        src_dec = bool(src_ep["direction_decrease"]) and not bool(src_ep["direction_increase"])
        pol_inc = bool(pol_ep["direction_increase"]) and not bool(pol_ep["direction_decrease"])
        pol_dec = bool(pol_ep["direction_decrease"]) and not bool(pol_ep["direction_increase"])
        if (src_inc and pol_dec) or (src_dec and pol_inc):
            issues.append({
                "code": "DIRECTION_FLIP",
                "severity": "HIGH",
                "detail": f"Comparison/trend direction reversed: source(+:{src_ep['direction_increase']}, -:{src_ep['direction_decrease']}) -> polished(+:{pol_ep['direction_increase']}, -:{pol_ep['direction_decrease']}).",
                "repair_hint": "Restore original increase/decrease or greater/less comparison direction.",
            })

    return issues
