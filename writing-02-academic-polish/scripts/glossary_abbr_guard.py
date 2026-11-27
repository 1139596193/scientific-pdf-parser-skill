#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
glossary_abbr_guard.py - Glossary Ingestion, Internal Conflict Detection,
Document-Wide Terminology/Spelling Consistency & Abstract/Body Abbreviation Lifecycle Audit.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import csv
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "resources")
STYLE_PROFILES_PATH = os.path.join(RESOURCES_DIR, "style_profiles.json")


def _load_style_data():
    if os.path.exists(STYLE_PROFILES_PATH):
        try:
            with open(STYLE_PROFILES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"profiles": {}, "us_uk_spelling_pairs": []}


def _norm_key(term):
    return re.sub(r"[\s\-_]+", " ", (term or "").strip().lower())


def _split_forbidden(val):
    if not val:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    return [x.strip() for x in re.split(r"[|;；]", str(val)) if x.strip()]


def _parse_bool(val):
    if isinstance(val, bool):
        return val
    return str(val or "").strip().lower() in ("true", "1", "yes", "y", "是")


def _parse_glossary_file(path):
    """Parse external CSV, TSV, JSON, or Markdown table glossary into raw entries."""
    ext = os.path.splitext(path)[1].lower()
    entries = []
    if not os.path.exists(path):
        return entries

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_text = f.read()

    if ext == ".json":
        try:
            data = json.loads(raw_text)
            if isinstance(data, dict):
                items = data.get("terms") or data.get("glossary") or data.get("entries") or []
            elif isinstance(data, list):
                items = data
            else:
                items = []
            for it in items:
                if isinstance(it, dict):
                    entries.append(it)
        except Exception:
            pass
        return entries

    if ext in (".csv", ".tsv"):
        delim = "\t" if ext == ".tsv" else ","
        lines = [ln for ln in raw_text.splitlines() if ln.strip()]
        if not lines:
            return entries
        reader = csv.DictReader(lines, delimiter=delim)
        for row in reader:
            norm_row = {str(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            entries.append(norm_row)
        return entries

    # Markdown pipe table
    lines = [ln.strip() for ln in raw_text.splitlines() if "|" in ln]
    if len(lines) >= 2:
        headers = [h.strip().lower() for h in lines[0].strip("|").split("|")]
        for ln in lines[1:]:
            if re.match(r"^[\s|:-]+$", ln):
                continue
            cols = [c.strip() for c in ln.strip("|").split("|")]
            if len(cols) >= 2:
                row = {}
                for idx, h in enumerate(headers):
                    row[h] = cols[idx] if idx < len(cols) else ""
                entries.append(row)
    return entries


def _normalize_entry(raw, origin):
    src = (raw.get("source_term") or raw.get("source") or raw.get("term") or "").strip()
    pref = (raw.get("preferred_term") or raw.get("canonical_target") or raw.get("target") or raw.get("preferred") or "").strip()
    dne = _parse_bool(raw.get("do_not_edit", raw.get("do_not_translate", False)))
    if not pref and dne and src:
        pref = src
    if not pref and src:
        pref = src
    forb = _split_forbidden(raw.get("forbidden_terms") or raw.get("forbidden_targets") or raw.get("forbidden"))
    abbr = (raw.get("abbr") or raw.get("abbreviation") or "").strip()
    notes = (raw.get("notes") or raw.get("rationale") or "").strip()
    return {
        "source_term": src,
        "preferred_term": pref,
        "forbidden_terms": forb,
        "do_not_edit": dne,
        "abbr": abbr,
        "notes": notes,
        "origin": origin,
    }


def load_and_audit_glossary(glossary_paths=None, case_data=None):
    """
    Load external glossary files and inline case.json glossary, detect internal conflicts
    WITHOUT silent overwriting, and return clean locked_terms + glossary_conflicts.
    """
    raw_entries = []
    for gp in (glossary_paths or []):
        origin = os.path.basename(gp)
        for r in _parse_glossary_file(gp):
            norm = _normalize_entry(r, origin)
            if norm["source_term"]:
                raw_entries.append(norm)

    if case_data:
        inline = case_data.get("glossary") or case_data.get("terminology") or []
        if isinstance(inline, dict):
            inline = inline.get("terms") or inline.get("entries") or [
                {"source_term": k, "preferred_term": v} for k, v in inline.items() if isinstance(v, str)
            ]
        if isinstance(inline, list):
            for r in inline:
                if isinstance(r, dict):
                    norm = _normalize_entry(r, "case.json")
                    if norm["source_term"]:
                        raw_entries.append(norm)

    by_source = {}
    by_abbr = {}
    conflicts = []

    for ent in raw_entries:
        sk = _norm_key(ent["source_term"])
        by_source.setdefault(sk, []).append(ent)
        if ent["abbr"]:
            by_abbr.setdefault(ent["abbr"].upper(), []).append(ent)

        # Self-contradiction: preferred_term also in forbidden_terms
        pref_k = _norm_key(ent["preferred_term"])
        forb_ks = {_norm_key(ft) for ft in ent["forbidden_terms"]}
        if pref_k and pref_k in forb_ks:
            conflicts.append({
                "code": "GLOSSARY_CONFLICT_SELF_CONTRADICTION",
                "severity": "HIGH",
                "source_term": ent["source_term"],
                "detail": f"Term '{ent['source_term']}' has preferred_term='{ent['preferred_term']}' which also appears in forbidden_terms={ent['forbidden_terms']} ({ent['origin']}).",
                "requires_human_confirmation": True,
            })

    locked_terms = []
    conflicted_sources = set()
    for sk, group in by_source.items():
        prefs = []
        for g in group:
            pk = _norm_key(g["preferred_term"])
            if pk and pk not in [_norm_key(p) for p in prefs]:
                prefs.append(g["preferred_term"])
        if len(prefs) > 1:
            conflicted_sources.add(sk)
            conflicts.append({
                "code": "GLOSSARY_CONFLICT_MULTI_TARGET",
                "severity": "HIGH",
                "source_term": group[0]["source_term"],
                "targets": prefs,
                "detail": f"Conflicting preferred_term targets for '{group[0]['source_term']}': {prefs}. Will NOT silently overwrite.",
                "requires_human_confirmation": True,
            })
        else:
            # Merge non-conflicting metadata
            merged = dict(group[0])
            all_forb = []
            for g in group:
                for ft in g["forbidden_terms"]:
                    if ft not in all_forb:
                        all_forb.append(ft)
                merged["do_not_edit"] = merged["do_not_edit"] or g["do_not_edit"]
                if not merged["abbr"] and g["abbr"]:
                    merged["abbr"] = g["abbr"]
            merged["forbidden_terms"] = all_forb
            # Exclude from auto-enforcement if self-contradictory
            if _norm_key(merged["preferred_term"]) not in {_norm_key(ft) for ft in all_forb}:
                locked_terms.append(merged)

    for abbr_u, group in by_abbr.items():
        expansions = []
        for g in group:
            ek = _norm_key(g["preferred_term"] or g["source_term"])
            if ek and ek not in [_norm_key(x) for x in expansions]:
                expansions.append(g["preferred_term"] or g["source_term"])
        if len(expansions) > 1:
            conflicts.append({
                "code": "GLOSSARY_CONFLICT_ABBR",
                "severity": "HIGH",
                "abbr": abbr_u,
                "expansions": expansions,
                "detail": f"Abbreviation '{abbr_u}' maps to conflicting expansions in glossary: {expansions}.",
                "requires_human_confirmation": True,
            })

    return {
        "raw_count": len(raw_entries),
        "locked_terms": locked_terms,
        "glossary_conflicts": conflicts,
    }


def _term_pattern(term):
    if re.search(r"[\u4e00-\u9fff]", term):
        return re.compile(re.escape(term))
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.I)


# ----------------------------------------------------------------------
# 2. Document-Wide Terminology & Spelling Consistency Audit
# ----------------------------------------------------------------------

def audit_term_consistency(sentence_records, glossary_bundle, target_style="general_academic"):
    """
    sentence_records: list of {sentence_id, source_text, polished_text, in_scope}
    Checks locked terms, forbidden terms, hyphenation/figure/p-value variant drift,
    and US/UK spelling harmonization.
    """
    issues = []
    locked_terms = (glossary_bundle or {}).get("locked_terms", [])

    # 1. Locked & forbidden term enforcement per sentence
    for rec in sentence_records:
        sid = rec["sentence_id"]
        src = rec.get("source_text", "")
        pol = rec.get("polished_text", src)
        for term in locked_terms:
            src_t = term["source_term"]
            pref_t = term["preferred_term"]
            for ft in term.get("forbidden_terms", []):
                if _term_pattern(ft).search(pol):
                    issues.append({
                        "sentence_id": sid,
                        "code": "FORBIDDEN_TERM_USED",
                        "severity": "HIGH",
                        "detail": f"Forbidden term '{ft}' found in polished text (preferred: '{pref_t}').",
                        "repair_hint": f"Replace '{ft}' with '{pref_t}'.",
                    })
            # If source contained locked term (or do_not_edit), ensure preferred_term (or abbr) survives in polished
            if term.get("do_not_edit") and _term_pattern(src_t).search(src):
                if not _term_pattern(pref_t).search(pol):
                    issues.append({
                        "sentence_id": sid,
                        "code": "LOCKED_TERM_MISMATCH",
                        "severity": "HIGH",
                        "detail": f"Locked do-not-edit term '{src_t}' missing or altered in polished sentence.",
                        "repair_hint": f"Preserve '{pref_t}' verbatim.",
                    })
            elif src_t and pref_t and _norm_key(src_t) != _norm_key(pref_t) and _term_pattern(src_t).search(src):
                abbr = term.get("abbr", "")
                if not _term_pattern(pref_t).search(pol) and not (abbr and _term_pattern(abbr).search(pol)):
                    issues.append({
                        "sentence_id": sid,
                        "code": "LOCKED_TERM_MISMATCH",
                        "severity": "HIGH",
                        "detail": f"Source term '{src_t}' was not normalized to preferred_term '{pref_t}'.",
                        "repair_hint": f"Use preferred_term '{pref_t}'.",
                    })

    # 2. Document-wide variant drift & US/UK spelling check on polished corpus
    full_pol = "\n".join(r.get("polished_text", r.get("source_text", "")) for r in sentence_records)

    # Fig. vs Figure drift (excluding sentence-initial Figure)
    fig_abbr = re.findall(r"\bFig\.\s*\d+", full_pol)
    fig_full_mid = re.findall(r"(?<=[a-z,;]\s)Figure\s+\d+", full_pol)
    if fig_abbr and fig_full_mid:
        issues.append({
            "sentence_id": "DOCUMENT",
            "code": "TERM_VARIANT_DRIFT",
            "severity": "MEDIUM",
            "detail": f"Mixed mid-sentence figure references: 'Fig.' ({len(fig_abbr)}) vs 'Figure' ({len(fig_full_mid)}).",
            "repair_hint": "Harmonize mid-sentence figure references to match target journal style.",
        })

    # p-value vs P value drift
    p_lower = re.findall(r"\bp-value\b", full_pol)
    p_upper_sp = re.findall(r"\bP\s+value\b", full_pol)
    if p_lower and p_upper_sp:
        issues.append({
            "sentence_id": "DOCUMENT",
            "code": "TERM_VARIANT_DRIFT",
            "severity": "MEDIUM",
            "detail": f"Mixed p-value notation: 'p-value' ({len(p_lower)}) vs 'P value' ({len(p_upper_sp)}).",
            "repair_hint": "Use one consistent notation throughout.",
        })

    # Hyphenation drift on repeated compound phrases (word1-word2 vs word1 word2)
    hyphen_pairs = re.findall(r"\b([a-z]{3,15})-([a-z]{3,15})\b", full_pol, re.I)
    seen_hp = set()
    for w1, w2 in hyphen_pairs:
        key = (w1.lower(), w2.lower())
        if key in seen_hp:
            continue
        seen_hp.add(key)
        h_hits = len(re.findall(rf"\b{re.escape(w1)}-{re.escape(w2)}\b", full_pol, re.I))
        s_hits = len(re.findall(rf"\b{re.escape(w1)}\s+{re.escape(w2)}\b", full_pol, re.I))
        if h_hits >= 1 and s_hits >= 1:
            issues.append({
                "sentence_id": "DOCUMENT",
                "code": "TERM_VARIANT_DRIFT",
                "severity": "LOW",
                "detail": f"Hyphenation variant drift: '{w1}-{w2}' ({h_hits}x) vs '{w1} {w2}' ({s_hits}x).",
                "repair_hint": f"Verify modifier vs noun usage and harmonize '{w1}-{w2}' / '{w1} {w2}'.",
            })

    # US / UK spelling check
    style_data = _load_style_data()
    pairs = style_data.get("us_uk_spelling_pairs", [])
    us_hits = []
    uk_hits = []
    for pair in pairs:
        us_w = pair["us"]
        uk_w = pair["uk"]
        if re.search(rf"\b{re.escape(us_w)}\b", full_pol, re.I):
            us_hits.append(us_w)
        if re.search(rf"\b{re.escape(uk_w)}\b", full_pol, re.I):
            uk_hits.append(uk_w)

    if us_hits and uk_hits:
        issues.append({
            "sentence_id": "DOCUMENT",
            "code": "SPELLING_VARIANT_MIXED",
            "severity": "MEDIUM",
            "detail": f"Mixed US and UK spellings across manuscript: US={us_hits[:8]} vs UK={uk_hits[:8]}.",
            "repair_hint": f"Standardize all spellings to a single variant (target_style={target_style}).",
        })
    elif target_style == "ieee_acm" and uk_hits and not us_hits:
        issues.append({
            "sentence_id": "DOCUMENT",
            "code": "SPELLING_STYLE_MISMATCH",
            "severity": "LOW",
            "detail": f"IEEE/ACM style prefers US spelling, but UK spellings found: {uk_hits[:8]}.",
            "repair_hint": "Convert UK spellings to US equivalents for IEEE/ACM.",
        })

    return issues


# ----------------------------------------------------------------------
# 3. Abstract / Body Abbreviation Lifecycle Audit
# ----------------------------------------------------------------------

_ABBR_WHITELIST = {
    "DNA", "RNA", "MRNA", "TRNA", "ATP", "ADP", "NADH", "PCR", "QPCR", "RTPCR", "RT", "ELISA", "MRI", "CT", "PET",
    "PDF", "DOCX", "HTML", "XML", "JSON", "CSV", "TSV", "YAML", "TXT", "MD", "TEX",
    "URL", "URI", "DOI", "HTTP", "HTTPS", "API", "CLI", "SDK", "CPU", "GPU", "TPU", "RAM", "ROM", "SSD",
    "USA", "UK", "EU", "US", "CN", "UN", "WHO", "FDA", "NIH", "NSF", "NSFC", "IEEE", "ACM", "ISO", "ANSI",
    "SI", "SD", "SE", "CI", "IQ", "IQR", "ROC", "AUC", "ANOVA", "PH", "OR", "HR", "RR",
    "UV", "IR", "FTIR", "NMR", "XRD", "XPS", "SEM", "TEM", "AFM", "STM", "DFT", "EDS", "ICP", "HPLC", "GC", "MS",
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
    "XX", "XXX", "XXXX", "YY", "ZZ", "NN", "NA", "TBD", "TODO", "TBA", "FIXME", "VS", "OK",
    "FIG", "TAB", "EQ", "SEC", "REF", "APP", "VOL", "NO", "ET", "AL", "EG", "IE", "CC", "BY",
}

_EN_DEF_RE = re.compile(r"([A-Za-z][A-Za-z0-9\-\s/]{3,80}?)\s*\(\s*([A-Z][A-Z0-9\-]{1,10})\s*\)")
_ZH_DEF_RE = re.compile(r"((?=[^（\(\n]*[\u4e00-\u9fff])[\u4e00-\u9fffA-Za-z0-9\-\s,，]{2,60}?)\s*[（\(]\s*([A-Z][A-Z0-9\-]{1,10})\s*[）\)]")
_STANDALONE_ABBR_RE = re.compile(r"(?<![A-Za-z0-9\-\\])([A-Z][A-Z0-9]{1,8}(?:-[A-Z0-9]{1,4})?)(?![A-Za-z0-9])")


_STOP_LEAD_WORDS = {
    "the", "a", "an", "of", "in", "for", "and", "or", "with", "using", "via", "by", "this", "that", "our", "their",
    "we", "they", "propose", "proposed", "introduce", "introduced", "present", "presented", "define", "defined",
    "use", "used", "using", "call", "called", "known", "as", "denoted", "namely", "consider", "study", "studied",
    "is", "are", "was", "were", "be", "to", "from", "on", "at", "into", "such", "central", "idea",
}


def _clean_expansion(raw, abbr=None):
    # Stop at clause boundaries (, ; : or "known as" / "called" / "denoted as")
    txt = re.split(r"[,;:]|\b(?:known\s+as|called|denoted\s+(?:as|by)|namely)\b", raw, flags=re.I)[-1].strip()
    words = txt.split()
    while words and words[0].lower() in _STOP_LEAD_WORDS:
        words.pop(0)
    if abbr and not re.search(r"[\u4e00-\u9fff]", txt):
        abbr_letters = re.sub(r"[^A-Za-z]", "", abbr).upper()
        if len(abbr_letters) >= 2 and words:
            first_ch = abbr_letters[0]
            # Find the latest plausible start matching abbr[0] within a reasonable window
            win_start = max(0, len(words) - (len(abbr_letters) + 3))
            for idx in range(win_start, len(words)):
                w_clean = re.sub(r"^[^A-Za-z]+", "", words[idx])
                if w_clean and w_clean[0].upper() == first_ch:
                    words = words[idx:]
                    break
    if len(words) > 7:
        words = words[-6:]
    return " ".join(words).strip()


def _plausible_abbr_pair(expansion, abbr):
    if not expansion:
        return False
    if re.search(r"[\u4e00-\u9fff]", expansion):
        return True
    # Check if at least first letter of abbr matches one of expansion words or initials match
    words = [w for w in re.split(r"[\s\-/]+", expansion) if w]
    if len(words) < 2 and len(abbr) >= 3:
        return abbr[0].lower() in expansion.lower()
    initials = "".join(w[0].upper() for w in words if w)
    return abbr[0] in initials or any(ch in initials for ch in abbr)


def audit_abbreviations(sentence_records):
    """
    Audit abbreviation definitions and lifecycle separately in 'abstract' and 'body' scopes.
    Detects: ABBR_UNDEFINED, ABBR_REDEFINED, ABBR_CONFLICT_EXPANSION,
    ABBR_FULL_FORM_REPEATED, ABBR_DEFINED_NOT_REUSED.
    """
    issues = []
    # Group sentences by scope ("abstract" vs "body"), skipping references/acknowledgments
    scopes = {"abstract": [], "body": []}
    for rec in sentence_records:
        role = (rec.get("section_role") or "body").lower()
        if role in ("references", "acknowledgments"):
            continue
        if role == "abstract":
            scopes["abstract"].append(rec)
        else:
            scopes["body"].append(rec)

    global_expansions = {}  # abbr -> set of normalized expansions

    for scope_name, recs in scopes.items():
        defined_in_scope = {}  # abbr -> {"expansion": str, "sentence_id": str, "count_after": int}
        used_before_def = set()

        for rec in recs:
            sid = rec["sentence_id"]
            text = rec.get("polished_text", rec.get("source_text", ""))
            # Strip inline/display math and LaTeX commands before scanning uppercase tokens
            clean_txt = re.sub(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$[^$\n]+\$|\\[A-Za-z]+\*?(?:\[[^\]]*\])?\{[^}]*\}", " ", text)

            # 1. Scan definitions in this sentence (deduplicated by abbr + span overlap)
            defs_here = []
            for pat in (_EN_DEF_RE, _ZH_DEF_RE):
                for m in pat.finditer(clean_txt):
                    raw_exp = m.group(1)
                    abbr = m.group(2).strip()
                    if abbr.upper() in _ABBR_WHITELIST:
                        continue
                    exp_clean = _clean_expansion(raw_exp, abbr=abbr)
                    if not _plausible_abbr_pair(exp_clean, abbr):
                        continue
                    m_start, m_end = m.start(), m.end()
                    if any(da == abbr and not (m_end <= ds or m_start >= de) for da, _, ds, de in defs_here):
                        continue
                    defs_here.append((abbr, exp_clean, m_start, m_end))

            for abbr, exp_clean, d_start, d_end in defs_here:
                exp_norm = _norm_key(exp_clean)
                global_expansions.setdefault(abbr, set()).add(exp_norm)
                if abbr in defined_in_scope:
                    issues.append({
                        "sentence_id": sid,
                        "code": "ABBR_REDEFINED",
                        "severity": "MEDIUM",
                        "detail": f"Abbreviation '{abbr}' redefined in {scope_name} scope (previously defined at {defined_in_scope[abbr]['sentence_id']}).",
                        "repair_hint": f"Use '{abbr}' directly after first definition in {scope_name}.",
                    })
                else:
                    defined_in_scope[abbr] = {"expansion": exp_clean, "exp_norm": exp_norm, "sentence_id": sid, "count_after": 0}

            # 2. Check full-form repetition after definition
            for abbr, info in list(defined_in_scope.items()):
                if info["sentence_id"] == sid:
                    continue
                exp_str = info["expansion"]
                if len(exp_str) >= 8 and re.search(rf"(?<![A-Za-z]){re.escape(exp_str)}(?![A-Za-z])", clean_txt, re.I):
                    issues.append({
                        "sentence_id": sid,
                        "code": "ABBR_FULL_FORM_REPEATED",
                        "severity": "LOW",
                        "detail": f"Full form '{exp_str}' repeated at {sid} after '{abbr}' was already defined at {info['sentence_id']} in {scope_name}.",
                        "repair_hint": f"Replace repeated full form with '{abbr}'.",
                    })

            # 3. Scan standalone abbreviation uses
            def_spans = [(s, e) for _, _, s, e in defs_here]
            for m in _STANDALONE_ABBR_RE.finditer(clean_txt):
                tok = m.group(1)
                if tok.upper() in _ABBR_WHITELIST or len(tok) < 2:
                    continue
                # Ignore tokens immediately inside the definition parentheses
                if any(s <= m.start() <= e for s, e in def_spans):
                    continue
                # Ignore single-digit suffixes like S1, T1, Fig2
                if re.match(r"^[A-Z]\d+$", tok):
                    continue
                if tok in defined_in_scope:
                    defined_in_scope[tok]["count_after"] += 1
                else:
                    if tok not in used_before_def:
                        used_before_def.add(tok)
                        issues.append({
                            "sentence_id": sid,
                            "code": "ABBR_UNDEFINED",
                            "severity": "MEDIUM",
                            "detail": f"Abbreviation '{tok}' used in {scope_name} at {sid} without prior 'Full Term ({tok})' definition in {scope_name}.",
                            "repair_hint": f"Define '{tok}' at first occurrence in {scope_name} or expand to full term.",
                        })

        # 4. Check defined-but-never-reused in scope
        for abbr, info in defined_in_scope.items():
            if info["count_after"] == 0:
                issues.append({
                    "sentence_id": info["sentence_id"],
                    "code": "ABBR_DEFINED_NOT_REUSED",
                    "severity": "LOW",
                    "detail": f"Abbreviation '{abbr}' was defined in {scope_name} at {info['sentence_id']} but never reused later in {scope_name}.",
                    "repair_hint": f"Consider omitting the abbreviation '{abbr}' if used only once.",
                })

    # 5. Cross-scope expansion conflict
    for abbr, exps in global_expansions.items():
        if len(exps) > 1:
            issues.append({
                "sentence_id": "DOCUMENT",
                "code": "ABBR_CONFLICT_EXPANSION",
                "severity": "HIGH",
                "detail": f"Abbreviation '{abbr}' has conflicting expansions across manuscript: {sorted(exps)}.",
                "repair_hint": f"Harmonize expansion of '{abbr}' across Abstract and Body.",
            })

    return issues
