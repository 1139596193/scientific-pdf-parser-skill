#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translation_verifier.py - 6-Dimension Deterministic Bilingual Translation Auditor
Part of the Scientific Translation Skill (task_03_translation).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse
import collections

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

RESOURCES_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "resources", "academic_invariants.json")

try:
    from entity_guard import load_invariants, extract_unit_fingerprints, _expand_bracket_citations
except ImportError:
    load_invariants = None
    extract_unit_fingerprints = None
    _expand_bracket_citations = None


CJK_REGEX = re.compile(r"[\u4e00-\u9fa5]")
EN_WORD_REGEX = re.compile(r"\b[a-zA-Z]{2,}\b")
BRACKET_CITE_EXTRACT_REGEX = re.compile(r"\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\](?:\s*(?:[-–—]{1,3}|,)\s*\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\])*")
LATEX_CITE_EXTRACT_REGEX = re.compile(r"\\(?:cite[tp]?|Cite[tp]?|textcite|parencite|autocite)\*?(?:\[[^\]]*\])?\{([^}]+)\}")


def _fallback_expand_cites(raw_str):
    ids = []
    range_between = re.findall(r"\[\s*(\d+)\s*\]\s*[-–—]{1,3}\s*\[\s*(\d+)\s*\]", raw_str)
    for a_s, b_s in range_between:
        a, b = int(a_s), int(b_s)
        if 0 < b - a <= 50:
            ids.extend(str(k) for k in range(a, b + 1))
    inner_blocks = re.findall(r"\[([^\]]+)\]", raw_str)
    for blk in inner_blocks:
        for p in [x.strip() for x in blk.split(",")]:
            rm = re.match(r"^(\d+)\s*[-–—]{1,3}\s*(\d+)$", p)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                if 0 < b - a <= 50:
                    ids.extend(str(k) for k in range(a, b + 1))
            else:
                ids.extend(re.findall(r"\d+", p))
    return ids


def load_translations_map(trans_path):
    """
    Normalize input translations.json into:
      trans_map: {unit_id: dict_record}
      duplicate_ids: list of duplicated unit_ids if list input
    """
    with open(trans_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    trans_map = {}
    duplicate_ids = []

    if isinstance(raw, dict):
        if "translations" in raw and isinstance(raw["translations"], list):
            items = raw["translations"]
        elif "translations" in raw and isinstance(raw["translations"], dict):
            raw = raw["translations"]
            items = None
        else:
            items = None

        if items is None:
            for uid, val in raw.items():
                if uid in ("case_id", "metadata", "status"):
                    continue
                if isinstance(val, str):
                    trans_map[uid] = {"unit_id": uid, "translated_text": val}
                elif isinstance(val, dict):
                    rec = dict(val)
                    rec["unit_id"] = uid
                    trans_map[uid] = rec
        else:
            for item in items:
                if isinstance(item, dict) and item.get("unit_id"):
                    uid = item["unit_id"]
                    if uid in trans_map:
                        duplicate_ids.append(uid)
                    trans_map[uid] = item
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("unit_id"):
                uid = item["unit_id"]
                if uid in trans_map:
                    duplicate_ids.append(uid)
                trans_map[uid] = item

    return trans_map, duplicate_ids


def check_number_preserved(tgt_text, num_item):
    """Check if normalized numeric value exists in target text (handles comma/scientific/escaped-% variants)."""
    norm_val = num_item["norm_value"]
    raw_val = num_item["raw"]
    tgt_clean = tgt_text.replace(",", "").replace("％", "%").replace(r"\%", "%")

    # Direct match on raw or normalized value
    if norm_val in tgt_clean:
        return True
    if raw_val in tgt_clean:
        return True

    # Scientific notation equivalence (e.g. 10^-3 <-> 10^{-3} <-> 0.001)
    if num_item.get("kind") == "scientific_notation":
        # Strip braces and spaces
        compact_tgt = re.sub(r"[\s\{\}]", "", tgt_clean)
        compact_val = re.sub(r"[\s\{\}]", "", norm_val)
        if compact_val in compact_tgt:
            return True
        # Try evaluating simple 10^k or a*10^k or ae-k
        try:
            m_e = re.match(r"^([+-]?\d+(?:\.\d+)?)[eE]([+-]?\d+)$", compact_val)
            if m_e:
                val = float(m_e.group(1)) * (10 ** int(m_e.group(2)))
                if str(val) in tgt_clean:
                    return True
            m_pow = re.match(r"^10\^([+-]?\d+)$", compact_val)
            if m_pow:
                val = 10 ** int(m_pow.group(1))
                if str(val) in tgt_clean:
                    return True
        except Exception:
            pass

    # Strict boundary check for integer/float to avoid matching '1' inside '10'
    esc = re.escape(norm_val)
    if re.search(rf"(?<![\d\.]){esc}(?![\d\.])", tgt_clean):
        return True

    return False


def check_cross_ref_preserved(tgt_text, ref_item):
    """Verify figure/table/equation/section cross-reference number survives translation."""
    ref_type = ref_item["ref_type"]
    ref_id = ref_item["ref_id"]
    raw_ref = ref_item["raw"]

    # Normalize full-width parens
    tgt_norm = tgt_text.replace("（", "(").replace("）", ")")

    if ref_item.get("is_latex_ref"):
        esc_id = re.escape(ref_id)
        if re.search(rf"\\(?:[a-zA-Z]*ref)\*?\s*\{{[^}}]*{esc_id}", tgt_norm) or ref_id in tgt_norm:
            return True
        return False

    if raw_ref in tgt_norm:
        return True

    esc_id = re.escape(ref_id)

    # Keyword equivalence map
    kw_map = {
        "figure": r"(?:图|附图|Fig(?:ure)?s?\.?)",
        "table": r"(?:表|续表|Tables?|Tabs?\.?)",
        "equation": r"(?:式|公式|方程|Eq(?:uation)?s?\.?)",
        "theorem": r"(?:定理|引理|推论|命题|定义|Theorems?|Lemmas?|Corollaries|Propositions?|Definitions?)",
        "algorithm": r"(?:算法|Algorithms?)",
        "section": r"(?:第\s*节|节|部分|章|附录|Sections?|Secs?\.?|Appendix|Appendices)",
    }

    kw_pat = kw_map.get(ref_type, r"")
    if ref_type == "equation":
        # Often translated as "式(10)", "公式 (10)", "方程 (10)", or just "(10)"
        if re.search(rf"(?:{kw_pat}\s*[\(\[]?\s*{esc_id}\s*[\)\]]?|\(\s*{esc_id}\s*\))", tgt_norm, re.IGNORECASE):
            return True
    elif ref_type == "section":
        # Often translated as "第 3.2 节" or "3.2 节" or "Section 3.2"
        if re.search(rf"(?:第\s*{esc_id}\s*节|{kw_pat}\s*{esc_id}|{esc_id}\s*节)", tgt_norm, re.IGNORECASE):
            return True
    else:
        if re.search(rf"{kw_pat}\s*[\(\[]?\s*{esc_id}\s*[\)\]]?", tgt_norm, re.IGNORECASE):
            return True

    return False


def check_inline_math_preserved(tgt_text, math_item):
    """Check if core mathematical symbol/expression survived."""
    raw = math_item["raw"]
    if raw in tgt_text:
        return True
    # Strip outer $ and whitespace
    core = raw.strip("$").strip()
    if core in tgt_text:
        return True
    # Strip curly braces in subscripts/superscripts (e.g. M_{RR} -> M_RR)
    core_no_braces = core.replace("{", "").replace("}", "")
    if core_no_braces in tgt_text.replace("{", "").replace("}", ""):
        return True
    # Check primary identifier before _ or ^ or (
    m_base = re.match(r"^([A-Za-z\\][A-Za-z0-9\\]*)", core)
    if m_base and len(m_base.group(1)) >= 1:
        base = m_base.group(1)
        if base in tgt_text:
            return True
    return False


def verify_translations(work_dir, translations_path):
    """Run full 6-dimension audit and emit verification_report.json & repair_queue.json."""
    units_path = os.path.join(work_dir, "source_units.json")
    fp_path = os.path.join(work_dir, "unit_fingerprints.json")
    compiled_path = os.path.join(work_dir, "glossary_compiled.json")

    if not os.path.exists(units_path):
        raise FileNotFoundError(f"source_units.json not found in {work_dir}")
    if not os.path.exists(translations_path):
        raise FileNotFoundError(f"translations.json not found: {translations_path}")

    with open(units_path, "r", encoding="utf-8") as f:
        units_data = json.load(f)
    units = units_data.get("units", [])
    case_id = units_data.get("case_id", "unknown")
    src_lang = units_data.get("source_language", "en")
    tgt_lang = units_data.get("target_language", "zh")

    invariants = load_invariants() if load_invariants else {}
    if not invariants and os.path.exists(RESOURCES_PATH):
        with open(RESOURCES_PATH, "r", encoding="utf-8") as f:
            invariants = json.load(f)

    # Load or compute fingerprints
    fingerprints = {}
    if os.path.exists(fp_path):
        with open(fp_path, "r", encoding="utf-8") as f:
            fingerprints = json.load(f).get("fingerprints", {})

    # Load compiled glossary if fingerprints didn't already include it
    unit_term_index = {}
    if os.path.exists(compiled_path):
        with open(compiled_path, "r", encoding="utf-8") as f:
            unit_term_index = json.load(f).get("unit_term_index", {})

    trans_map, duplicate_ids = load_translations_map(translations_path)
    expand_fn = _expand_bracket_citations or _fallback_expand_cites

    # Metrics counters
    expected_units = [u for u in units if u.get("translate_policy") == "translate"]
    expected_ids = {u["unit_id"] for u in expected_units}
    units_by_id = {u["unit_id"]: u for u in units}

    issues = []
    manual_review_items = []
    repair_queue_map = {}

    covered_count = 0
    total_term_checks = 0
    passed_term_checks = 0
    total_entity_checks = 0
    passed_entity_checks = 0

    def record_issue(u_id, dimension, code, severity, message, page_span=None, fix_hint=""):
        item = {
            "unit_id": u_id,
            "page_span": page_span or [1, 1],
            "dimension": dimension,
            "code": code,
            "severity": severity,
            "message": message,
            "fix_hint": fix_hint,
        }
        issues.append(item)
        if code in ("LOGIC_NEGATION_RISK", "LOGIC_QUALIFIER_RISK"):
            manual_review_items.append(item)

        if severity == "error" or code in ("LOGIC_NEGATION_RISK", "LOGIC_QUALIFIER_RISK"):
            if u_id not in repair_queue_map:
                u_obj = units_by_id.get(u_id, {})
                t_obj = trans_map.get(u_id, {})
                fp_obj = fingerprints.get(u_id, {})
                repair_queue_map[u_id] = {
                    "unit_id": u_id,
                    "page_span": u_obj.get("page_span", [1, 1]),
                    "source_text": u_obj.get("source_text", ""),
                    "current_translation": t_obj.get("translated_text", ""),
                    "issues": [],
                    "required_terms": [
                        {
                            "source_term": lt["source_term"],
                            "canonical_target": lt["canonical_target"],
                            "forbidden_targets": lt.get("forbidden_targets", []),
                        }
                        for lt in (fp_obj.get("locked_terms") or unit_term_index.get(u_id, []))
                    ],
                    "required_entities": {
                        "numbers_units": [n["raw"] for n in fp_obj.get("numbers_units", [])],
                        "citations": [c["raw"] for c in fp_obj.get("citations", [])],
                        "cross_refs": [r["raw"] for r in fp_obj.get("cross_refs", [])],
                    },
                }
            repair_queue_map[u_id]["issues"].append({
                "dimension": dimension,
                "code": code,
                "severity": severity,
                "message": message,
                "fix_hint": fix_hint,
            })

    # 1a. Check duplicate / extra / non-translatable units
    for dup_id in duplicate_ids:
        record_issue(dup_id, "COVERAGE", "EXTRA_OR_DUPLICATE_UNIT", "error", f"Duplicate translation entry detected for {dup_id}.")

    for tid in trans_map:
        if tid not in expected_ids:
            u_info = units_by_id.get(tid)
            reason = f"Unit {tid} has policy '{u_info.get('translate_policy')}'" if u_info else f"Unknown unit_id '{tid}' (possible context_tail hallucination)"
            record_issue(tid, "COVERAGE", "EXTRA_OR_DUPLICATE_UNIT", "error", f"Unexpected unit translated: {reason}.")

    # Compile hygiene boilerplate patterns
    leak_patterns = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in invariants.get("boilerplate_leak_patterns", [])]
    logic_rules = invariants.get("logic_markers", {})
    tgt_logic_key = "zh" if tgt_lang == "zh" else "en"

    # Iterate through all expected units
    for u in expected_units:
        u_id = u["unit_id"]
        p_span = u.get("page_span", [1, 1])
        src_text = u.get("source_text", "")
        src_words = u.get("word_count", 0)

        t_rec = trans_map.get(u_id)
        if t_rec is None:
            record_issue(u_id, "COVERAGE", "MISSING_UNIT", "error", f"Missing translation for required unit {u_id}.", p_span, "Translate this unit completely.")
            continue

        tgt_text = str(t_rec.get("translated_text", "")).strip()
        if not tgt_text:
            record_issue(u_id, "COVERAGE", "EMPTY_TRANSLATION", "error", f"Empty translation string for unit {u_id}.", p_span, "Provide full translation.")
            continue

        covered_count += 1

        # Get fingerprints (generate on the fly if missing)
        fp = fingerprints.get(u_id)
        if not fp and extract_unit_fingerprints:
            u_terms = unit_term_index.get(u_id, [])
            fp = extract_unit_fingerprints(u, invariants, locked_terms=u_terms)
            fingerprints[u_id] = fp
        elif not fp:
            fp = {"numbers_units": [], "citations": [], "cross_refs": [], "inline_math": [], "logic_markers": {}, "locked_terms": unit_term_index.get(u_id, [])}

        # 2. LENGTH_ANOMALY (Protected against short phrases/headings/titles)
        tgt_cjk = len(CJK_REGEX.findall(tgt_text))
        tgt_en = len(EN_WORD_REGEX.findall(tgt_text))
        if src_lang == "en" and tgt_lang == "zh":
            tgt_eff = tgt_cjk + tgt_en * 1.2
            if src_words >= 12 and u.get("unit_type") not in ("heading", "title"):
                ratio = tgt_eff / max(1, src_words)
                if ratio < 0.45:
                    record_issue(
                        u_id, "LENGTH_ANOMALY", "SEVERE_UNDER_TRANSLATION", "error",
                        f"Possible summary/omission: {src_words} source words yielded only {tgt_cjk} CJK chars (ratio={ratio:.2f} < 0.45).",
                        p_span, "Expand translation to faithfully cover every clause without summarizing."
                    )
                elif ratio > 6.5:
                    record_issue(
                        u_id, "LENGTH_ANOMALY", "SEVERE_OVER_TRANSLATION", "warning",
                        f"Excessively long translation: {src_words} source words yielded {tgt_cjk} CJK chars (ratio={ratio:.2f} > 6.5).",
                        p_span, "Check for repetition or leaked prompt instructions."
                    )
        elif src_lang == "zh" and tgt_lang == "en":
            if src_words >= 25 and u.get("unit_type") not in ("heading", "title"):
                ratio = tgt_en / max(1, src_words)
                if ratio < 0.25:
                    record_issue(
                        u_id, "LENGTH_ANOMALY", "SEVERE_UNDER_TRANSLATION", "error",
                        f"Possible omission: {src_words} source chars yielded only {tgt_en} English words (ratio={ratio:.2f}).",
                        p_span
                    )

        # 3. TERM_CONSISTENCY
        # 3. TERM_CONSISTENCY (Ordered longest-first to avoid substring masking e.g. '量子差分隐私' masking missing '差分隐私')
        locked_terms = fp.get("locked_terms") or unit_term_index.get(u_id, [])
        locked_sorted = sorted(locked_terms, key=lambda x: len(x.get("canonical_target", "")), reverse=True)
        consumed_src = src_text
        consumed_tgt = tgt_text

        for lt in locked_sorted:
            total_term_checks += 1
            src_t = lt["source_term"]
            canon_t = lt["canonical_target"]
            allowed = [canon_t] + lt.get("allowed_target_variants", [])
            forbidden = lt.get("forbidden_targets", [])
            dnt = lt.get("do_not_translate", False)

            # Check forbidden mistranslations (case-insensitive for English target terms)
            is_ascii_tgt = bool(re.match(r"^[A-Za-z0-9\s\-_]+$", canon_t))
            re_flags = 0 if (lt.get("case_sensitive") or not is_ascii_tgt) else re.IGNORECASE

            forbidden_hit = None
            for fb in forbidden:
                if not fb:
                    continue
                if re_flags and re.search(re.escape(fb), tgt_text, re_flags):
                    forbidden_hit = fb
                    break
                elif fb in tgt_text:
                    forbidden_hit = fb
                    break

            if forbidden_hit:
                record_issue(
                    u_id, "TERM_CONSISTENCY", "FORBIDDEN_TERM_USED", "error",
                    f"Forbidden mistranslation '{forbidden_hit}' used for locked term '{src_t}' (expected '{canon_t}').",
                    p_span, f"Replace '{forbidden_hit}' with '{canon_t}'."
                )
                continue

            # Check if this term is independently present (or just subsumed by a longer term already checked)
            matched_canon = False
            if dnt:
                if src_t in tgt_text or canon_t in tgt_text:
                    matched_canon = True
            else:
                for cand in allowed:
                    if not cand:
                        continue
                    cand_pat = re.escape(cand)
                    if re_flags:
                        if re.search(cand_pat, consumed_tgt, re_flags):
                            matched_canon = True
                            consumed_tgt = re.sub(cand_pat, " ", consumed_tgt, flags=re_flags)
                            break
                        elif re.search(cand_pat, tgt_text, re_flags) and not any(
                            re.search(re.escape(f), consumed_src, re.IGNORECASE) for f in lt.get("matched_forms", [src_t])
                        ):
                            matched_canon = True
                            break
                    else:
                        if cand in consumed_tgt:
                            matched_canon = True
                            consumed_tgt = consumed_tgt.replace(cand, " ")
                            break
                        elif cand in tgt_text and not any(
                            re.search(re.escape(f), consumed_src, re.IGNORECASE) for f in lt.get("matched_forms", [src_t])
                        ):
                            matched_canon = True
                            break

            # Mask matched forms in consumed_src
            for mf in lt.get("matched_forms", [src_t]):
                consumed_src = re.sub(re.escape(mf), " ", consumed_src, flags=re.IGNORECASE)

            if matched_canon:
                passed_term_checks += 1
            else:
                record_issue(
                    u_id, "TERM_CONSISTENCY", "TERM_MISMATCH", "error",
                    f"Locked term '{src_t}' missing canonical translation '{canon_t}' in target text.",
                    p_span, f"Ensure term '{src_t}' is translated strictly as '{canon_t}'."
                )

        # 4. ENTITY_FIDELITY
        # 4a. Numbers & Units
        for num_item in fp.get("numbers_units", []):
            total_entity_checks += 1
            if check_number_preserved(tgt_text, num_item):
                passed_entity_checks += 1
            else:
                record_issue(
                    u_id, "ENTITY_FIDELITY", "NUMBER_MISSING", "error",
                    f"Numeric/unit entity '{num_item['raw']}' missing or altered in translation.",
                    p_span, f"Verify and restore exact numeric value '{num_item['raw']}'."
                )

        # 4b. Citations
        tgt_bracket_ids = set()
        for m in BRACKET_CITE_EXTRACT_REGEX.finditer(tgt_text):
            tgt_bracket_ids.update(expand_fn(m.group(0)))

        tgt_latex_keys = set()
        for m in LATEX_CITE_EXTRACT_REGEX.finditer(tgt_text):
            for ck in m.group(1).split(","):
                if ck.strip():
                    tgt_latex_keys.add(ck.strip())

        for cite_item in fp.get("citations", []):
            total_entity_checks += 1
            ckind = cite_item["kind"]
            if ckind == "numeric_bracket":
                exp_ids = cite_item.get("expanded_ids", [])
                missing_ids = [cid for cid in exp_ids if cid not in tgt_bracket_ids]
                if not missing_ids:
                    passed_entity_checks += 1
                else:
                    record_issue(
                        u_id, "ENTITY_FIDELITY", "CITATION_MISSING", "error",
                        f"Citation '{cite_item['raw']}' missing reference ID(s) {missing_ids} in translation.",
                        p_span, f"Restore citation mark '{cite_item['raw']}'."
                    )
            elif ckind == "latex_cite":
                exp_keys = cite_item.get("expanded_ids", [])
                missing_keys = [k for k in exp_keys if k not in tgt_latex_keys and k not in tgt_text]
                if not missing_keys:
                    passed_entity_checks += 1
                else:
                    record_issue(
                        u_id, "ENTITY_FIDELITY", "CITATION_MISSING", "error",
                        f"LaTeX citation '{cite_item['raw']}' missing key(s) {missing_keys} in translation.",
                        p_span, f"Restore LaTeX citation '{cite_item['raw']}'."
                    )
            else:
                # Author-year check: verify year survives
                exp_years = cite_item.get("expanded_ids", [])
                if all(yr in tgt_text for yr in exp_years):
                    passed_entity_checks += 1
                else:
                    record_issue(
                        u_id, "ENTITY_FIDELITY", "CITATION_MISSING", "error",
                        f"Author-year citation '{cite_item['raw']}' missing in translation.",
                        p_span, f"Preserve citation '{cite_item['raw']}'."
                    )

        # 4c. Cross References
        for ref_item in fp.get("cross_refs", []):
            total_entity_checks += 1
            if check_cross_ref_preserved(tgt_text, ref_item):
                passed_entity_checks += 1
            else:
                record_issue(
                    u_id, "ENTITY_FIDELITY", "CROSS_REF_MISSING", "error",
                    f"Cross-reference '{ref_item['raw']}' ({ref_item['ref_type']} {ref_item['ref_id']}) missing or corrupted in translation.",
                    p_span, f"Restore reference '{ref_item['raw']}'."
                )

        # 4d. Inline Math (Warning level)
        for math_item in fp.get("inline_math", []):
            if math_item.get("kind") == "greek_or_operator":
                continue
            total_entity_checks += 1
            if check_inline_math_preserved(tgt_text, math_item):
                passed_entity_checks += 1
            else:
                record_issue(
                    u_id, "ENTITY_FIDELITY", "INLINE_MATH_MISSING", "warning",
                    f"Inline mathematical expression '{math_item['raw']}' may be missing or altered.",
                    p_span, f"Preserve math token '{math_item['raw']}' verbatim."
                )

        # 5. LOGIC_RELATION (Negation & Qualifier Bound)
        unit_logic = fp.get("logic_markers", {})
        if unit_logic.get("negation"):
            tgt_neg_signals = logic_rules.get("negation", {}).get(tgt_logic_key, [])
            if tgt_neg_signals and not any(sig in tgt_text.lower() if tgt_logic_key == "en" else sig in tgt_text for sig in tgt_neg_signals):
                record_issue(
                    u_id, "LOGIC_RELATION", "LOGIC_NEGATION_RISK", "warning",
                    f"Source contains negation trigger(s) {unit_logic['negation']}, but no target negation marker found.",
                    p_span, "Verify negation polarity was not accidentally flipped."
                )

        if unit_logic.get("qualifier_bound"):
            quals = unit_logic["qualifier_bound"]
            extremal_en = {"at most", "at least", "no more than", "no less than", "up to", "strictly less", "strictly greater"}
            extremal_zh = ["至多", "最多", "不超过", "至少", "不少于", "不低于", "高达", "严格小于", "严格大于", "不高于"]
            hit_extremal_en = [q for q in quals if q in extremal_en]

            strict_zh_bounds = {"至多", "最多", "不超过", "至少", "不少于", "不低于", "高达", "严格小于", "严格大于", "不高于", "仅", "只", "只有", "唯一", "仅仅", "除非", "当且仅当"}
            cond_zh_triggers = {"条件下", "前提是", "只要", "受限于", "取决于"}
            hit_strict_zh = [q for q in quals if q in strict_zh_bounds]
            hit_cond_zh = [q for q in quals if q in cond_zh_triggers]

            if tgt_logic_key == "zh" and hit_extremal_en:
                if not any(sig in tgt_text for sig in extremal_zh):
                    record_issue(
                        u_id, "LOGIC_RELATION", "LOGIC_QUALIFIER_RISK", "warning",
                        f"Source contains strict quantitative bound {hit_extremal_en}, but no corresponding target bound marker (e.g. 至多/至少/不超过) found.",
                        p_span, "Verify strict quantitative bounds are accurately translated."
                    )
            elif tgt_logic_key == "en" and hit_cond_zh and not hit_strict_zh:
                # Allow natural academic English conditional phrasing for Chinese '在…条件下'
                tgt_qual_signals = logic_rules.get("qualifier_bound", {}).get("en", [])
                has_std = any(sig in tgt_text.lower() for sig in tgt_qual_signals)
                has_acad_cond = bool(re.search(
                    r"\bunder\b.{1,55}\b(?:conditions?|assumptions?|regime|setting|constraints?|damping|noise|scenarios?)\b|\bgiven that\b|\bprovided\b",
                    tgt_text, re.IGNORECASE
                ))
                if not (has_std or has_acad_cond):
                    record_issue(
                        u_id, "LOGIC_RELATION", "LOGIC_QUALIFIER_RISK", "warning",
                        f"Source contains conditional trigger(s) {quals}, but no corresponding English conditional marker found.",
                        p_span, "Verify conditional boundaries are accurately translated."
                    )
            else:
                tgt_qual_signals = logic_rules.get("qualifier_bound", {}).get(tgt_logic_key, [])
                if tgt_qual_signals and not any(sig in tgt_text.lower() if tgt_logic_key == "en" else sig in tgt_text for sig in tgt_qual_signals):
                    record_issue(
                        u_id, "LOGIC_RELATION", "LOGIC_QUALIFIER_RISK", "warning",
                        f"Source contains boundary/qualifier trigger(s) {quals}, but no corresponding target qualifier marker found.",
                        p_span, "Verify strict quantitative bounds/conditions (e.g., only, at most, unless) are accurately translated."
                    )

        # 6. FORMAT_HYGIENE
        for lp in leak_patterns:
            if lp.search(tgt_text):
                record_issue(
                    u_id, "FORMAT_HYGIENE", "BOILERPLATE_LEAK", "error",
                    f"LLM conversational/meta boilerplate prefix or marker leaked into translation.",
                    p_span, "Strip boilerplate chatter; keep pure translated content only."
                )
                break

        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]", tgt_text):
            record_issue(
                u_id, "FORMAT_HYGIENE", "GARBLED_OUTPUT", "error",
                "Control characters or replacement glyph U+FFFD detected in translated text.",
                p_span, "Clean garbled characters."
            )

        # Residual untranslated English text check (when translating EN->ZH)
        if src_lang == "en" and tgt_lang == "zh" and src_words >= 8 and u.get("unit_type") not in ("heading", "title", "table_content"):
            if tgt_cjk / max(1, len(tgt_text)) < 0.15 and tgt_en >= 8:
                # Check if unit is mostly non-translatable proper names
                record_issue(
                    u_id, "FORMAT_HYGIENE", "UNTRANSLATED_SOURCE_RESIDUAL", "warning",
                    f"Translation appears predominantly untranslated English ({tgt_en} EN words, only {tgt_cjk} CJK chars).",
                    p_span, "Ensure full translation into target Chinese."
                )

    # Compute final aggregate metrics
    coverage_rate = round(covered_count / max(1, len(expected_units)), 4)
    term_consistency_rate = round(passed_term_checks / max(1, total_term_checks), 4) if total_term_checks > 0 else 1.0
    entity_fidelity_rate = round(passed_entity_checks / max(1, total_entity_checks), 4) if total_entity_checks > 0 else 1.0

    error_count = sum(1 for iss in issues if iss["severity"] == "error")
    warning_count = sum(1 for iss in issues if iss["severity"] == "warning")

    by_dim = collections.Counter(iss["dimension"] for iss in issues)
    by_code = collections.Counter(iss["code"] for iss in issues)

    verification_report = {
        "case_id": case_id,
        "audit_status": "PASS" if error_count == 0 else "NEEDS_REPAIR",
        "metrics": {
            "expected_translatable_units": len(expected_units),
            "covered_units": covered_count,
            "coverage_rate": coverage_rate,
            "total_term_checks": total_term_checks,
            "passed_term_checks": passed_term_checks,
            "term_consistency_rate": term_consistency_rate,
            "total_entity_checks": total_entity_checks,
            "passed_entity_checks": passed_entity_checks,
            "entity_fidelity_rate": entity_fidelity_rate,
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "issue_breakdown_by_dimension": dict(by_dim),
        "issue_breakdown_by_code": dict(by_code),
        "issues": issues,
        "manual_review_items": manual_review_items,
    }

    repair_queue = {
        "case_id": case_id,
        "total_repair_units": len(repair_queue_map),
        "items": list(repair_queue_map.values()),
    }

    rep_path = os.path.join(work_dir, "verification_report.json")
    rq_path = os.path.join(work_dir, "repair_queue.json")

    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(verification_report, f, ensure_ascii=False, indent=2)

    with open(rq_path, "w", encoding="utf-8") as f:
        json.dump(repair_queue, f, ensure_ascii=False, indent=2)

    print(f"[translation_verifier] Status={verification_report['audit_status']} | Coverage={coverage_rate:.1%} | Term={term_consistency_rate:.1%} | Entity={entity_fidelity_rate:.1%} | Errors={error_count}, Warnings={warning_count}")
    return verification_report, repair_queue


def main():
    parser = argparse.ArgumentParser(description="6-Dimension Deterministic Bilingual Translation Auditor")
    parser.add_argument("--work-dir", required=True, help="Work directory containing source_units.json and fingerprints")
    parser.add_argument("--translations", required=True, help="Path to translations.json")
    args = parser.parse_args()

    verify_translations(args.work_dir, args.translations)


if __name__ == "__main__":
    main()
