#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
term_extractor.py - Scientific Terminology Extraction, External Glossary Ingestion & Compiler
Part of the Scientific Translation Skill (task_03_translation).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import csv
import argparse
import collections

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "resources", "academic_invariants.json")

# Common English function / glue words used as n-gram phrase boundaries
EN_GLUE_WORDS = {
    "the", "a", "an", "and", "or", "but", "nor", "for", "of", "in", "on", "at", "to",
    "from", "by", "with", "as", "into", "onto", "upon", "about", "above", "below",
    "through", "between", "among", "during", "before", "after", "under", "over",
    "without", "within", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "can", "could", "will", "would",
    "shall", "should", "may", "might", "must", "this", "that", "these", "those",
    "it", "its", "they", "their", "them", "we", "our", "us", "you", "your",
    "which", "who", "whom", "whose", "where", "when", "why", "how", "what",
    "if", "unless", "although", "though", "while", "whereas", "because", "since",
    "than", "such", "so", "very", "more", "most", "less", "least", "many", "much",
    "some", "any", "each", "every", "all", "both", "few", "several", "other", "another",
    "not", "no", "only", "also", "then", "thus", "hence", "therefore", "however",
    "e.g", "i.e", "et", "al", "etc", "via", "per", "using", "used", "based", "given",
    "shown", "obtained", "proposed", "consider", "considered", "following", "respectively",
    "follows", "follow", "followed", "implies", "imply", "yields", "yield", "holds", "hold",
    "satisfies", "satisfy", "denotes", "denote", "defines", "define", "shows", "show",
    "proves", "prove", "gives", "give", "takes", "take", "becomes", "become", "remains", "remain",
    "requires", "require", "provides", "provide", "contains", "contain", "reduces", "reduce",
    "last", "first", "second", "third", "next", "previous", "above", "below", "final", "following",
    "against", "across", "versus", "vs", "around", "toward", "towards", "throughout", "beyond"
}

EDGE_PREPOSITIONS = {
    "against", "across", "between", "within", "during", "versus", "vs",
    "through", "under", "over", "among", "around", "toward", "towards",
    "throughout", "beyond", "of", "in", "on", "at", "to", "from", "by",
    "with", "for", "as", "into", "onto", "upon", "about", "without", "after", "before", "via"
}

NARRATIVE_NOISE_PHRASES = {
    "last inequality", "first inequality", "second inequality", "third inequality",
    "last equality", "first equality", "second equality", "third equality",
    "inequality follows", "equality holds", "follows immediately", "follows directly",
    "above equation", "above inequality", "next section", "previous section",
    "main result", "main theorem", "motivating example", "final step", "first step", "second step",
    "supplementary note", "supplementary notes", "supplementary information",
    "supplementary figure", "supplementary table", "supplementary material",
    "supplementary materials", "extended data", "supporting information",
    "author contributions", "data availability", "code availability",
    "competing interests", "acknowledgements", "acknowledgments",
    "ethics declarations", "corresponding author",
    "physical review", "physical review letters", "nature physics", "nature communications"
}

GENERIC_SINGLE_WORDS = {
    "paper", "study", "work", "section", "chapter", "table", "figure", "fig", "eq",
    "equation", "theorem", "lemma", "proof", "corollary", "proposition", "definition",
    "method", "approach", "result", "results", "data", "system", "model", "value",
    "number", "case", "example", "problem", "solution", "analysis", "experiment",
    "performance", "time", "error", "rate", "level", "state", "process", "function",
    "property", "properties", "condition", "conditions", "parameter", "parameters",
    "part", "way", "form", "order", "set", "type", "class", "group", "point", "line"
}

SCIENTIFIC_SUFFIXES = (
    "tion", "sion", "ment", "ity", "ness", "sis", "ism", "logy", "graphy",
    "metry", "physics", "dynamics", "mechanics", "privacy", "entropy",
    "fidelity", "channel", "network", "protocol", "algorithm", "computing",
    "computation", "distribution", "cryptography", "decoherence", "entanglement"
)


def load_academic_stopwords():
    """Load standardized academic stopwords from academic_invariants.json."""
    stopwords = set()
    if os.path.exists(RESOURCES_PATH):
        try:
            with open(RESOURCES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for sw in data.get("academic_stopwords", []):
                    stopwords.add(sw.lower().strip())
        except Exception:
            pass
    return stopwords


# ----------------------------------------------------------------------
# 1. External Glossary Loader (CSV / TSV / JSON / Markdown)
# ----------------------------------------------------------------------

def parse_external_glossary_file(fpath):
    """Parse external glossary files into standardized term objects."""
    terms = []
    ext = os.path.splitext(fpath)[1].lower()
    fname = os.path.basename(fpath)

    try:
        if ext in (".csv", ".tsv"):
            delim = "\t" if ext == ".tsv" else ","
            with open(fpath, "r", encoding="utf-8-sig", errors="replace") as f:
                reader = csv.DictReader(f, delimiter=delim)
                for idx, row in enumerate(reader):
                    # Flexible column matching
                    norm_row = {str(k).strip().lower(): str(v or "").strip() for k, v in row.items() if k}
                    src = norm_row.get("source_term") or norm_row.get("source") or norm_row.get("term") or norm_row.get("en") or norm_row.get("英文")
                    tgt = norm_row.get("canonical_target") or norm_row.get("target_term") or norm_row.get("target") or norm_row.get("zh") or norm_row.get("中文") or norm_row.get("译名")
                    if not src:
                        continue
                    aliases_raw = norm_row.get("aliases") or norm_row.get("abbr") or ""
                    aliases = [a.strip() for a in re.split(r"[;|,]", aliases_raw) if a.strip()]
                    forbidden_raw = norm_row.get("forbidden_targets") or norm_row.get("forbidden") or ""
                    forbidden = [fb.strip() for fb in re.split(r"[;|,]", forbidden_raw) if fb.strip()]
                    rationale = norm_row.get("rationale") or norm_row.get("context") or f"Imported from external glossary {fname}"
                    dnt = norm_row.get("do_not_translate", "").lower() in ("true", "1", "yes")

                    terms.append({
                        "term_id": f"EXT_{len(terms) + 1:03d}",
                        "source_term": src,
                        "aliases": aliases,
                        "canonical_target": tgt or (src if dnt else ""),
                        "allowed_target_variants": [],
                        "forbidden_targets": forbidden,
                        "do_not_translate": dnt,
                        "case_sensitive": src.isupper() and len(src) <= 8,
                        "first_seen_unit": "",
                        "first_seen_page": 1,
                        "rationale": rationale,
                        "source_origin": "external_locked",
                    })
        elif ext == ".json":
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_list = data.get("terms", data) if isinstance(data, dict) else data
            if isinstance(raw_list, list):
                for item in raw_list:
                    if isinstance(item, dict) and item.get("source_term"):
                        item_copy = dict(item)
                        item_copy.setdefault("term_id", f"EXT_{len(terms) + 1:03d}")
                        item_copy.setdefault("aliases", [])
                        item_copy.setdefault("allowed_target_variants", [])
                        item_copy.setdefault("forbidden_targets", [])
                        item_copy.setdefault("do_not_translate", False)
                        item_copy.setdefault("case_sensitive", False)
                        item_copy["source_origin"] = "external_locked"
                        terms.append(item_copy)
        elif ext == ".md":
            # Parse markdown table: | source | target | ... |
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("|") and "---" not in s:
                        cols = [c.strip() for c in s.strip("|").split("|")]
                        if len(cols) >= 2 and cols[0].lower() not in ("source", "source_term", "term", "英文", "术语"):
                            terms.append({
                                "term_id": f"EXT_{len(terms) + 1:03d}",
                                "source_term": cols[0],
                                "aliases": [],
                                "canonical_target": cols[1],
                                "allowed_target_variants": [],
                                "forbidden_targets": [],
                                "do_not_translate": False,
                                "case_sensitive": False,
                                "first_seen_unit": "",
                                "first_seen_page": 1,
                                "rationale": f"Imported from Markdown glossary {fname}",
                                "source_origin": "external_locked",
                            })
    except Exception:
        pass

    return terms


# ----------------------------------------------------------------------
# 2. Multi-Path Candidate Terminology Extraction
# ----------------------------------------------------------------------

ABBR_EN_REGEX = re.compile(r"\b([A-Za-z][A-Za-z0-9\-]{1,}(?:\s+[A-Za-z0-9\-]+){1,5})\s*\(([A-Z][A-Za-z0-9\-]{1,10})\)")
ABBR_ZH_REGEX = re.compile(r"([\u4e00-\u9fa5]{2,14})\s*[（\(]([A-Za-z][A-Za-z0-9\-\s]{1,25})[）\)]")
PROPER_CAP_REGEX = re.compile(r"\b([A-Z][a-zA-Z0-9'\-]{2,}(?:\s+[A-Z][a-zA-Z0-9'\-]{1,})+)\b")
HYPHEN_COMP_REGEX = re.compile(r"\b([a-zA-Z]{3,}(?:-[a-zA-Z]{2,}){1,2})\b")


def validate_acronym_pair(full_phrase, abbr):
    """Verify if abbr plausibly initializes words in full_phrase."""
    words = [w for w in re.split(r"[\s\-]+", full_phrase) if w and w.lower() not in {"of", "and", "the", "for", "in", "on", "with", "to"}]
    if len(words) < 2:
        return False
    # Ignore citation years or pure numbers
    if abbr.isdigit():
        return False
    initials = "".join(w[0].upper() for w in words if w)
    # Check if most letters of abbr appear in initials or full_phrase uppercase/first letters
    abbr_alpha = [c.upper() for c in abbr if c.isalpha()]
    if not abbr_alpha:
        return False
    matches = sum(1 for c in abbr_alpha if c in initials or c in full_phrase.upper())
    return matches >= max(2, len(abbr_alpha) - 1)


def extract_en_noun_phrases(text, stopwords):
    """Extract high-value 2-4 gram English candidate noun phrases bounded by glue words."""
    # Split on punctuation or newlines
    clauses = re.split(r"[,.;:?!\(\)\[\]\{\}\n\"'“”]+", text)
    candidates = []

    for clause in clauses:
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", clause)
        # Segment token sequences by glue words
        segments = []
        curr_seg = []
        for tok in tokens:
            low = tok.lower()
            if low in EN_GLUE_WORDS or tok.isdigit() or len(tok) <= 1:
                if curr_seg:
                    segments.append(curr_seg)
                    curr_seg = []
            else:
                curr_seg.append(tok)
        if curr_seg:
            segments.append(curr_seg)

        for seg in segments:
            # Generate 2-4 grams
            n_toks = len(seg)
            for n in range(2, min(5, n_toks + 1)):
                for i in range(n_toks - n + 1):
                    sub = seg[i:i + n]
                    phrase = " ".join(sub)
                    low_p = phrase.lower()
                    if low_p in stopwords or low_p in NARRATIVE_NOISE_PHRASES:
                        continue
                    # Skip preposition-bounded fragments or generic words
                    if sub[0].lower() in EDGE_PREPOSITIONS or sub[-1].lower() in EDGE_PREPOSITIONS:
                        continue
                    # Avoid phrases starting/ending with weak verbs or generic adverbs
                    if sub[0].lower() in {"very", "highly", "well", "many", "more", "new", "large", "small", "high", "low", "different", "various"} and n == 2:
                        if sub[1].lower() in GENERIC_SINGLE_WORDS:
                            continue
                    if all(w.lower() in GENERIC_SINGLE_WORDS for w in sub):
                        continue
                    # Skip math expressions or citations remnants
                    if re.search(r"^(?:fig|table|eq|sec|section|theorem|lemma)\b", low_p):
                        continue
                    candidates.append(phrase)

    return candidates


def normalize_term_key(term):
    """Normalize term string for grouping singular/plural/casing variants."""
    t = re.sub(r"\s+", " ", term.strip().lower())
    if t.endswith("ies") and len(t) > 5:
        t = t[:-3] + "y"
    elif t.endswith("s") and not t.endswith(("ss", "us", "is")) and len(t) > 4:
        t = t[:-1]
    return t


def extract_candidates(work_dir, max_terms=60):
    """
    Scan source_units.json and manifest to extract and score candidate terminology.
    Also emits pre-locked glossary.json if external glossaries are found.
    """
    units_path = os.path.join(work_dir, "source_units.json")
    manifest_path = os.path.join(work_dir, "ingest_manifest.json")

    if not os.path.exists(units_path):
        raise FileNotFoundError(f"source_units.json not found in {work_dir}")

    with open(units_path, "r", encoding="utf-8") as f:
        units_data = json.load(f)
    units = units_data.get("units", [])
    case_id = units_data.get("case_id", "unknown")

    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

    stopwords = load_academic_stopwords()

    # 1. Import external glossaries if detected
    external_terms = []
    for g_path in manifest.get("glossary_files_detected", []):
        if os.path.exists(g_path):
            external_terms.extend(parse_external_glossary_file(g_path))

    # Track candidate statistics keyed by normalized form
    stats = collections.defaultdict(lambda: {
        "forms": collections.Counter(),
        "aliases": set(),
        "frequency": 0,
        "occurrences": [],
        "first_seen_unit": None,
        "first_seen_page": None,
        "sample_context": "",
        "in_priority_zone": False,
        "path_sources": set(),
        "score": 0.0,
    })

    for u in units:
        if u.get("translate_policy") not in ("translate", "preserve_source"):
            continue
        if u.get("unit_type") in ("reference", "metadata", "display_equation", "untranslatable_asset", "code_block"):
            continue

        u_id = u["unit_id"]
        u_type = u.get("unit_type", "paragraph")
        p_num = u.get("page_span", [1, 1])[0]
        text = u.get("source_text", "")
        if not text:
            continue

        is_priority = u_type in ("title", "abstract", "heading", "figure_caption", "table_caption")

        def record_hit(raw_term, path_tag, weight=1.0, alias=None):
            t_clean = re.sub(r"\s+", " ", raw_term.strip())
            low_t = t_clean.lower()
            t_words = low_t.split()
            if (
                len(t_clean) < 3
                or low_t in stopwords
                or low_t in NARRATIVE_NOISE_PHRASES
                or (t_words and (t_words[0] in EDGE_PREPOSITIONS or t_words[-1] in EDGE_PREPOSITIONS))
                or re.match(r"^(?:supplementary\s+(?:note|information|figure|table|material|discussion|section)|extended\s+data)", low_t)
            ):
                return
            key = normalize_term_key(t_clean)
            st = stats[key]
            st["forms"][t_clean] += 1
            if alias:
                st["aliases"].add(alias)
            st["frequency"] += 1
            if u_id not in st["occurrences"]:
                st["occurrences"].append(u_id)
            if st["first_seen_unit"] is None:
                st["first_seen_unit"] = u_id
                st["first_seen_page"] = p_num
                # Extract local context snippet
                idx = text.find(raw_term)
                if idx != -1:
                    s_idx = max(0, idx - 45)
                    e_idx = min(len(text), idx + len(raw_term) + 65)
                    st["sample_context"] = text[s_idx:e_idx].replace("\n", " ").strip()
                else:
                    st["sample_context"] = text[:120].replace("\n", " ").strip()
            if is_priority:
                st["in_priority_zone"] = True
            st["path_sources"].add(path_tag)
            st["score"] += weight * (2.5 if is_priority else 1.0)

        # Path A: Acronym pairs
        for m in ABBR_EN_REGEX.finditer(text):
            full_p, abbr = m.group(1).strip(), m.group(2).strip()
            if validate_acronym_pair(full_p, abbr):
                record_hit(full_p, "acronym_pair", weight=5.0, alias=abbr)

        for m in ABBR_ZH_REGEX.finditer(text):
            zh_term, en_term = m.group(1).strip(), m.group(2).strip()
            record_hit(zh_term, "zh_en_pair", weight=5.0, alias=en_term)

        # Path B: High-value noun phrases (2-4 grams)
        phrases = extract_en_noun_phrases(text, stopwords)
        for p in phrases:
            # Boost phrases with scientific suffixes or hyphens
            w_boost = 1.6 if (any(p.lower().endswith(sfx) for sfx in SCIENTIFIC_SUFFIXES) or "-" in p) else 1.0
            record_hit(p, "noun_phrase", weight=w_boost)

        # Path C: Proper capitalized terms & hyphenated technical compounds
        for m in PROPER_CAP_REGEX.finditer(text):
            prop = m.group(1).strip()
            if prop.lower() not in stopwords and len(prop.split()) <= 4:
                # Filter common sentence-initial non-terms
                if not re.match(r"^(?:Table|Figure|Section|Theorem|Lemma|Definition|Proposition|Appendix)\s+", prop):
                    record_hit(prop, "capitalized_proper", weight=1.8)

        for m in HYPHEN_COMP_REGEX.finditer(text):
            hc = m.group(1).strip()
            if hc.lower() not in stopwords and len(hc) >= 6:
                record_hit(hc, "hyphen_compound", weight=1.3)

    # Filter & rank candidates
    filtered_list = []
    for key, st in stats.items():
        best_form = st["forms"].most_common(1)[0][0]
        has_acronym = "acronym_pair" in st["path_sources"] or "zh_en_pair" in st["path_sources"]

        # Require frequency >= 2 unless it's an explicit acronym definition or in title/abstract/caption
        if st["frequency"] < 2 and not has_acronym and not st["in_priority_zone"]:
            continue
        # Require frequency >= 2 for pure noun_phrase even in priority zone if very generic
        if st["frequency"] == 1 and not has_acronym and "title" not in str(st["first_seen_unit"]):
            if not any(best_form.lower().endswith(sfx) for sfx in SCIENTIFIC_SUFFIXES):
                continue

        # Bonus for multi-unit spread and scientific morphology
        spread_bonus = len(st["occurrences"]) * 1.2
        word_len_bonus = 1.5 if len(best_form.split()) >= 2 else 0.5
        final_score = round(st["score"] + spread_bonus + word_len_bonus, 2)

        filtered_list.append({
            "key": key,
            "source_term": best_form,
            "aliases": sorted(list(st["aliases"])),
            "frequency": st["frequency"],
            "unit_spread": len(st["occurrences"]),
            "occurrences": st["occurrences"][:30],
            "first_seen_unit": st["first_seen_unit"],
            "first_seen_page": st["first_seen_page"],
            "in_priority_zone": st["in_priority_zone"],
            "extraction_paths": sorted(list(st["path_sources"])),
            "sample_context": st["sample_context"],
            "score": final_score,
        })

    # Deduplicate subsumed shorter phrases if a longer phrase has >= 75% of its frequency
    filtered_list.sort(key=lambda x: (-len(x["source_term"]), -x["score"]))
    kept = []
    for cand in filtered_list:
        c_low = cand["source_term"].lower()
        is_subsumed = False
        for k in kept:
            k_low = k["source_term"].lower()
            if c_low != k_low and c_low in k_low:
                if k["frequency"] >= cand["frequency"] * 0.70 and not cand["aliases"]:
                    is_subsumed = True
                    break
        if not is_subsumed:
            kept.append(cand)

    # Sort final candidates by score descending
    kept.sort(key=lambda x: (-x["score"], -x["frequency"]))
    top_candidates = kept[:max_terms]

    # Assign candidate IDs
    for idx, c in enumerate(top_candidates):
        c["candidate_id"] = f"C{idx + 1:03d}"

    candidates_out = {
        "case_id": case_id,
        "total_candidates": len(top_candidates),
        "external_locked_count": len(external_terms),
        "external_locked_terms": external_terms,
        "candidates": top_candidates,
    }

    cand_path = os.path.join(work_dir, "term_candidates.json")
    with open(cand_path, "w", encoding="utf-8") as f:
        json.dump(candidates_out, f, ensure_ascii=False, indent=2)

    # If external glossaries were present, initialize baseline glossary.json
    if external_terms:
        baseline_glossary = {
            "schema_version": "1.0.0",
            "glossary_metadata": {
                "case_id": case_id,
                "source_language": manifest.get("source_language", "en"),
                "target_language": manifest.get("target_language", "zh"),
            },
            "terms": external_terms,
        }
        g_out_path = os.path.join(work_dir, "glossary.json")
        with open(g_out_path, "w", encoding="utf-8") as f:
            json.dump(baseline_glossary, f, ensure_ascii=False, indent=2)

    print(f"[term_extractor] Extracted {len(top_candidates)} candidates ({len(external_terms)} external locked) -> {cand_path}")
    return candidates_out


# ----------------------------------------------------------------------
# 3. Glossary Validation & Regex Compilation (`compile` mode)
# ----------------------------------------------------------------------

def build_term_pattern(term_str, case_sensitive=False):
    """
    Build robust regex pattern matching word boundaries, English plurals (-s/-es/-ies),
    and hyphen/space equivalence (e.g. 'fault-tolerant' <-> 'fault tolerant').
    """
    t = term_str.strip()
    if not t:
        return None

    is_cjk = bool(re.search(r"[\u4e00-\u9fa5]", t))
    if is_cjk:
        return re.escape(t)

    # Split into tokens on spaces or hyphens
    parts = re.split(r"[\s\-]+", t)
    escaped_parts = []
    for i, part in enumerate(parts):
        if i == len(parts) - 1 and len(part) >= 3 and part.isalpha():
            # Allow plural inflection on the final noun
            low = part.lower()
            if low.endswith("y") and len(low) > 3 and low[-2] not in "aeiou":
                stem = re.escape(part[:-1])
                escaped_parts.append(f"(?:{stem}y|{stem}ies)")
            elif low.endswith(("s", "x", "z", "ch", "sh")):
                escaped_parts.append(f"{re.escape(part)}(?:es)?")
            else:
                escaped_parts.append(f"{re.escape(part)}(?:s|es)?")
        else:
            escaped_parts.append(re.escape(part))

    body_pat = r"[\s\-]+".join(escaped_parts)

    # Word boundary handling: protect short uppercase acronyms from hyphenated sub-matches like 'EA' inside 'EA-QDP'
    if t.isupper() and len(t) <= 6 and "-" not in t and " " not in t:
        return rf"(?<![A-Z0-9\-]){body_pat}(?![A-Z0-9\-])"

    prefix = r"\b" if t[0].isalnum() else ""
    suffix = r"\b" if t[-1].isalnum() else ""
    return f"{prefix}{body_pat}{suffix}"


def compile_glossary(work_dir, glossary_path):
    """
    Validate glossary schema, compile morphological regexes, and index
    all matching terms across every unit in source_units.json.
    """
    units_path = os.path.join(work_dir, "source_units.json")
    if not os.path.exists(units_path):
        raise FileNotFoundError(f"source_units.json not found in {work_dir}")
    if not os.path.exists(glossary_path):
        raise FileNotFoundError(f"Glossary file not found: {glossary_path}")

    with open(units_path, "r", encoding="utf-8") as f:
        units_data = json.load(f)
    units = units_data.get("units", [])
    case_id = units_data.get("case_id", "unknown")

    with open(glossary_path, "r", encoding="utf-8") as f:
        g_raw = json.load(f)

    def _first_nonempty_str(d, keys):
        for k in keys:
            val = d.get(k)
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    def _coerce_str_list(d, keys):
        for k in keys:
            val = d.get(k)
            if val is None:
                continue
            if isinstance(val, list):
                return [str(x).strip() for x in val if x is not None and str(x).strip()]
            if isinstance(val, str) and val.strip():
                return [val.strip()]
        return []

    if isinstance(g_raw, list):
        raw_terms = g_raw
    elif isinstance(g_raw, dict):
        raw_terms = None
        for root_key in ("terms", "glossary", "entries", "items"):
            if isinstance(g_raw.get(root_key), list):
                raw_terms = g_raw[root_key]
                break
        if raw_terms is None:
            raise ValueError(
                "Glossary JSON object must contain a list under 'terms', 'glossary', 'entries', or 'items'."
            )
    else:
        raise ValueError("Glossary JSON must be a list or an object containing a term list.")

    seen_sources = {}
    validated_terms = []
    validation_warnings = []

    for idx, t in enumerate(raw_terms):
        if not isinstance(t, dict):
            validation_warnings.append(f"Item #{idx} is not a JSON object ({type(t).__name__}); skipped.")
            continue

        src = _first_nonempty_str(t, ("source_term", "source", "term", "en"))
        if not src:
            validation_warnings.append(f"Item #{idx} missing 'source_term' (or alias 'source'/'term'/'en'); skipped.")
            continue

        dnt = bool(t.get("do_not_translate", False))
        tgt = _first_nonempty_str(t, ("canonical_target", "target", "translation", "zh", "canonical"))
        if not tgt and dnt:
            tgt = src
        if not tgt:
            validation_warnings.append(
                f"Item #{idx} ('{src}') missing 'canonical_target' (or alias 'target'/'translation'/'zh') with do_not_translate=False; skipped."
            )
            continue

        src_key = src.lower()
        if src_key in seen_sources:
            validation_warnings.append(f"Duplicate/conflicting source_term '{src}' (already defined as {seen_sources[src_key]}); skipped duplicate.")
            continue

        term_id = _first_nonempty_str(t, ("term_id", "id")) or f"T{len(validated_terms) + 1:03d}"
        seen_sources[src_key] = term_id

        aliases = _coerce_str_list(t, ("aliases", "synonyms", "abbreviations"))
        allowed_vars = _coerce_str_list(t, ("allowed_target_variants", "variants", "allowed_variants"))
        forbidden = _coerce_str_list(t, ("forbidden_targets", "forbidden", "banned", "banned_targets"))
        case_sens = bool(t.get("case_sensitive", False))
        rationale = _first_nonempty_str(t, ("rationale", "note", "notes", "reason"))
        source_origin = _first_nonempty_str(t, ("source_origin", "origin")) or "agent_confirmed"

        # Build regexes for primary source_term + each alias
        patterns = []
        p_main = build_term_pattern(src, case_sens)
        if p_main:
            patterns.append({"form": src, "regex": p_main, "case_sensitive": case_sens})

        for al in aliases:
            # Short all-caps acronyms (like DP, QKD) must be matched case-sensitively
            al_cs = True if (al.isupper() and len(al) <= 6) else case_sens
            p_al = build_term_pattern(al, al_cs)
            if p_al:
                patterns.append({"form": al, "regex": p_al, "case_sensitive": al_cs})

        validated_terms.append({
            "term_id": term_id,
            "source_term": src,
            "aliases": aliases,
            "canonical_target": tgt,
            "allowed_target_variants": allowed_vars,
            "forbidden_targets": forbidden,
            "do_not_translate": dnt,
            "case_sensitive": case_sens,
            "rationale": rationale,
            "source_origin": source_origin,
            "compiled_patterns": patterns,
        })

    # Scan all units in source_units.json
    unit_term_index = {}
    term_hit_counts = collections.Counter()

    # Pre-compile python regex objects
    compiled_objs = []
    for vt in validated_terms:
        re_list = []
        for pat in vt["compiled_patterns"]:
            flags = 0 if pat["case_sensitive"] else re.IGNORECASE
            re_list.append(re.compile(pat["regex"], flags))
        compiled_objs.append((vt, re_list))

    for u in units:
        if u.get("translate_policy") != "translate":
            continue
        u_id = u["unit_id"]
        text = u.get("source_text", "")
        hits = []
        for vt, re_list in compiled_objs:
            matched_forms = []
            for rx in re_list:
                for m in rx.finditer(text):
                    matched_forms.append(m.group(0))
            if matched_forms:
                term_hit_counts[vt["term_id"]] += 1
                hits.append({
                    "term_id": vt["term_id"],
                    "source_term": vt["source_term"],
                    "matched_forms": sorted(list(set(matched_forms))),
                    "canonical_target": vt["canonical_target"],
                    "allowed_target_variants": vt["allowed_target_variants"],
                    "forbidden_targets": vt["forbidden_targets"],
                    "do_not_translate": vt["do_not_translate"],
                })
        if hits:
            unit_term_index[u_id] = hits

    for vt in validated_terms:
        vt["matched_unit_count"] = term_hit_counts[vt["term_id"]]

    compiled_output = {
        "schema_version": "1.0.0",
        "case_id": case_id,
        "total_locked_terms": len(validated_terms),
        "units_with_terms": len(unit_term_index),
        "validation_warnings": validation_warnings,
        "terms": validated_terms,
        "unit_term_index": unit_term_index,
    }

    out_path = os.path.join(work_dir, "glossary_compiled.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(compiled_output, f, ensure_ascii=False, indent=2)

    print(f"[term_extractor] Compiled {len(validated_terms)} locked terms -> indexed {len(unit_term_index)} units -> {out_path}")
    return compiled_output


def main():
    parser = argparse.ArgumentParser(description="Scientific Terminology Extractor & Compiler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_ext = subparsers.add_parser("extract", help="Extract candidate terms from source_units.json")
    p_ext.add_argument("--work-dir", required=True, help="Work directory containing source_units.json")
    p_ext.add_argument("--max-terms", type=int, default=60, help="Maximum candidate terms to output")

    p_comp = subparsers.add_parser("compile", help="Validate and compile glossary.json against source_units.json")
    p_comp.add_argument("--work-dir", required=True, help="Work directory containing source_units.json")
    p_comp.add_argument("--glossary", required=True, help="Path to input glossary JSON file")

    args = parser.parse_args()

    if args.command == "extract":
        extract_candidates(args.work_dir, max_terms=args.max_terms)
    elif args.command == "compile":
        compile_glossary(args.work_dir, args.glossary)


if __name__ == "__main__":
    main()
