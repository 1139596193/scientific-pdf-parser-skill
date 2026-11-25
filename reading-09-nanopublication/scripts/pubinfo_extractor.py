#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pubinfo_extractor.py - Deterministic Provenance & Anti-Hallucination PubInfo Extractor
Part of the Nanopublication Scientific Reading Skill (task_09_nanopub).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from entity_guard import _normalize_ligatures
except ImportError:
    def _normalize_ligatures(text):
        return text or ""


DOI_REGEX = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
ARXIV_ID_REGEX = re.compile(
    r"(?:\barXiv\s*:\s*|https?://arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?)\b",
    re.I,
)
PMID_REGEX = re.compile(
    r"(?:\bPMID\s*[:：#]?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{6,9})\b",
    re.I,
)
YEAR_REGEX = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
DATE_YEAR_REGEX = re.compile(
    r"(?:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+|"
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+)"
    r"(19[89]\d|20[0-3]\d)\b",
    re.I,
)
ARXIV_YYMM_REGEX = re.compile(r"^(\d{2})(\d{2})\.\d{4,5}(?:v\d+)?$")

DATA_CODE_URL_REGEX = re.compile(
    r"https?://(?:github\.com|gitlab\.com|huggingface\.co|zenodo\.org|osf\.io|figshare\.com|datadryad\.org)/[^\s\)\],;>\"']+",
    re.I,
)
ANY_URL_REGEX = re.compile(r"https?://[^\s\)\],;>\"']+", re.I)

LICENSE_PATTERNS = [
    re.compile(r"\b(CC[\s\-]+BY(?:[\s\-]+(?:NC|ND|SA)){0,3}(?:\s*\d+\.\d+)?)\b", re.I),
    re.compile(r"\b(Creative\s+Commons\s+Attribution[^\n.;]{0,50})", re.I),
    re.compile(r"\b(MIT\s+License|Apache\s+License\s+2\.0|Apache-2\.0|GPLv[23]|BSD-\d-Clause)\b", re.I),
]

VENUE_PATTERNS = [
    re.compile(r"\b(?:Published\s+(?:in|at)|Appeared\s+in|Proceedings\s+of(?:\s+the)?|Accepted\s+(?:at|to|by))\s+([^\n.;]{4,100})", re.I),
    re.compile(
        r"\b(Nature(?:\s+[A-Z][a-z]+){0,3}|Science(?:\s+Advances)?|Cell|PNAS|The\s+Lancet|NEJM|JAMA|"
        r"NeurIPS|ICML|ICLR|ACL|EMNLP|NAACL|CVPR|ICCV|ECCV|AAAI|IJCAI|KDD|WWW|SIGIR|"
        r"Physical\s+Review\s+(?:Letters|A|B|C|D|E|X)|Phys\.\s*Rev\.\s*(?:Lett\.|A|B|C|D|E|X)|"
        r"IEEE\s+Transactions\s+on\s+[A-Za-z\s]{3,50}|Journal\s+of\s+[A-Za-z\s]{3,50}|"
        r"bioRxiv|medRxiv|ChemRxiv|arXiv)\b",
        re.I,
    ),
]

AUTHOR_LINE_NOISE = re.compile(
    r"(?:@|https?://|www\.|\barXiv\s*:|\[(?:quant-ph|cs\.[A-Z]+|math\.[A-Z]+|stat\.[A-Z]+|physics\.[a-z\-]+|cond-mat|hep-[a-z]+|q-bio)\]|"
    r"\b(?:University|Institute|Department|Dept\.?|Laboratory|Lab\b|College|Academy|School\s+of|"
    r"Center\s+for|Centre\s+for|Preprint|Submitted\s+to|Published\s+in|Keywords|Index\s+Terms|Abstract|Corresponding)\b|"
    r"(?:大学|学院|研究所|实验室|研究院|关键词|通信作者|摘要|收稿日期|基金项目))",
    re.I,
)

TRACKED_FIELDS = (
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "arxiv_id",
    "pmid",
    "url",
    "version_or_preprint",
    "data_or_code_urls",
    "license",
)


def _clean_doi(raw):
    if not raw:
        return None
    val = raw.strip().rstrip(".,;)>]}")
    if DOI_REGEX.match(val):
        return val
    return None


def _parse_xmp_metadata(xmp_xml):
    """Extract title, authors, doi, date, rights from XMP XML if present."""
    res = {}
    if not xmp_xml or not isinstance(xmp_xml, str):
        return res
    try:
        root = ET.fromstring(xmp_xml)
    except Exception:
        # Regex fallback on malformed XMP snippet
        m_doi = re.search(r"prism:doi[^>]*>([^<]+)<", xmp_xml, re.I)
        if m_doi:
            d = _clean_doi(m_doi.group(1))
            if d:
                res["doi"] = d
        return res

    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        txt = (elem.text or "").strip()
        if tag == "doi" and txt:
            d = _clean_doi(txt)
            if d:
                res["doi"] = d
        elif tag == "publicationname" and txt and "venue" not in res:
            res["venue"] = txt
        elif tag == "rights" and txt and "license" not in res:
            res["license"] = txt
        elif tag in ("date", "createdate") and txt and "year" not in res:
            ym = YEAR_REGEX.search(txt)
            if ym:
                res["year"] = int(ym.group(1))
    return res


def _split_author_string(raw_str):
    """Split a clean author string into individual author names."""
    if not raw_str:
        return []
    s = _normalize_ligatures(raw_str).strip()
    s = re.sub(r"[\^_]\{[^}]*\}", "", s)
    s = re.sub(r"([\*∗†‡§¶\d])\s*(and\b)", r" \2", s)
    s = re.sub(r"(?<=[A-Za-z\u4e00-\u9fa5])\s*(?:\d+(?:\s*,\s*\d+)*|[\*∗†‡§¶]+)+(?=\s*(?:,|;|、|\band\b|&|$))", "", s)
    s = re.sub(r"[\*∗†‡§¶]+", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,;.")
    if not s:
        return []
    parts = re.split(r"\s*(?:;|,|\band\b|&|、)\s*", s)
    out = []
    seen = set()
    for p in parts:
        name = p.strip(" ,;.")
        if not name or len(name) < 2 or re.match(r"^\d+$", name):
            continue
        if AUTHOR_LINE_NOISE.search(name):
            continue
        if len(name.split()) > 6:
            continue
        low = name.lower()
        if low not in seen:
            seen.add(low)
            out.append(name)
    return out


def _extract_authors_from_anchors(front_anchors):
    """Extract clean authors with source anchor ID."""
    authors = []
    seen = set()
    source_aids = []
    for anc in front_anchors:
        if anc.get("page", 1) > 2:
            continue
        raw_bt = anc.get("raw_block_type", "")
        role = anc.get("section_role", "")
        if raw_bt != "metadata" and role != "title_meta":
            continue
        txt = anc.get("text", "")
        if anc.get("block_type") == "title":
            continue
        for ln in txt.splitlines():
            s = ln.strip()
            if not s or len(s) > 200:
                continue
            if AUTHOR_LINE_NOISE.search(s):
                continue
            if s.endswith((".", "。")) and len(s.split()) >= 12:
                continue
            # Reject lines that look like title/abstract prose without commas/ands
            names = _split_author_string(s)
            if names:
                for n in names:
                    if n.lower() not in seen:
                        seen.add(n.lower())
                        authors.append(n)
                if anc["anchor_id"] not in source_aids:
                    source_aids.append(anc["anchor_id"])
    return authors, source_aids


def _get_case_doc_entry(case_data, doc_meta):
    """Find matching document entry in case.json if provided."""
    if not isinstance(case_data, dict):
        return {}
    src_file = (doc_meta.get("source_file") or "").lower()
    rel_path = (doc_meta.get("rel_path") or "").lower()
    did = doc_meta.get("doc_id")
    for key in ("documents", "papers", "sources", "files"):
        val = case_data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    item_path = str(item.get("path") or item.get("file") or item.get("source_file") or "").lower()
                    item_id = item.get("doc_id") or item.get("id")
                    if (item_id and item_id == did) or (src_file and src_file in item_path) or (rel_path and rel_path in item_path):
                        return item
    return case_data if not isinstance(case_data.get("documents"), list) else {}


def extract_doc_pubinfo(doc_meta, doc_anchors, doc_signals=None, case_data=None):
    """
    Deterministically extract publication metadata for one document with strict field_sources provenance.
    Never guesses missing fields; returns null / [] and lists them in missing_fields.
    """
    doc_id = doc_meta.get("doc_id", "D1")
    src_file = doc_meta.get("source_file", "unknown")
    pdf_meta = doc_meta.get("pdf_metadata") or {}
    xmp_meta = _parse_xmp_metadata(pdf_meta.get("xmp_xml", ""))
    case_entry = _get_case_doc_entry(case_data, doc_meta)
    sig_meta = (doc_signals or {}).get("meta_clues") or {}

    field_sources = {}
    pub = {
        "doc_id": doc_id,
        "source_file": src_file,
        "title": None,
        "authors": [],
        "year": None,
        "venue": None,
        "doi": None,
        "arxiv_id": None,
        "pmid": None,
        "url": None,
        "version_or_preprint": None,
        "data_or_code_urls": [],
        "license": None,
        "field_sources": field_sources,
        "missing_fields": [],
        "extracted_by": "task_09_nanopub/pubinfo_extractor.py",
    }

    front_anchors = [
        a for a in doc_anchors
        if a.get("page", 1) <= 2 and a.get("zone") != "references_locked"
    ]
    non_ref_anchors = [a for a in doc_anchors if a.get("zone") != "references_locked"]

    # 1. Title
    if isinstance(case_entry.get("title"), str) and case_entry["title"].strip():
        pub["title"] = case_entry["title"].strip()
        field_sources["title"] = "case_json:title"
    else:
        for a in front_anchors:
            if a.get("block_type") == "title" and a.get("text", "").strip():
                pub["title"] = re.sub(r"\s+", " ", _normalize_ligatures(a["text"])).strip()
                field_sources["title"] = f"pdf_text:{a['anchor_id']}"
                break
        if not pub["title"] and pdf_meta.get("title"):
            t_meta = pdf_meta["title"].strip()
            if len(t_meta) >= 5 and not re.match(r"^(?:untitled|microsoft\s+word|arxiv:)", t_meta, re.I):
                pub["title"] = t_meta
                field_sources["title"] = "pdf_doc_metadata:title"
        if not pub["title"] and sig_meta.get("candidate_title") and sig_meta["candidate_title"] != "Untitled":
            pub["title"] = sig_meta["candidate_title"]
            field_sources["title"] = "paper_signals:candidate_title"

    # 2. Authors
    if isinstance(case_entry.get("authors"), list) and case_entry["authors"]:
        pub["authors"] = [str(x).strip() for x in case_entry["authors"] if str(x).strip()]
        field_sources["authors"] = "case_json:authors"
    else:
        a_list, a_srcs = _extract_authors_from_anchors(front_anchors)
        if a_list:
            pub["authors"] = a_list
            field_sources["authors"] = ",".join(f"pdf_text:{aid}" for aid in a_srcs)
        elif sig_meta.get("authors_cleaned"):
            pub["authors"] = list(sig_meta["authors_cleaned"])
            field_sources["authors"] = "paper_signals:authors_cleaned"
        elif pdf_meta.get("author"):
            meta_authors = _split_author_string(pdf_meta["author"])
            if meta_authors:
                pub["authors"] = meta_authors
                field_sources["authors"] = "pdf_doc_metadata:author"

    # 3. DOI (restrict text scan to front_anchors or bottom footnotes of p1-2 to avoid cited reference DOIs)
    if isinstance(case_entry.get("doi"), str) and _clean_doi(case_entry["doi"]):
        pub["doi"] = _clean_doi(case_entry["doi"])
        field_sources["doi"] = "case_json:doi"
    elif xmp_meta.get("doi"):
        pub["doi"] = xmp_meta["doi"]
        field_sources["doi"] = "pdf_xmp_metadata:doi"
    else:
        for a in front_anchors:
            m = DOI_REGEX.search(a.get("text", ""))
            if m:
                d = _clean_doi(m.group(1))
                if d:
                    pub["doi"] = d
                    field_sources["doi"] = f"pdf_text:{a['anchor_id']}"
                    break
        if not pub["doi"]:
            for k in ("subject", "keywords"):
                m = DOI_REGEX.search(pdf_meta.get(k, ""))
                if m:
                    d = _clean_doi(m.group(1))
                    if d:
                        pub["doi"] = d
                        field_sources["doi"] = f"pdf_doc_metadata:{k}"
                        break

    # 4. arXiv ID
    if isinstance(case_entry.get("arxiv_id"), str) and case_entry["arxiv_id"].strip():
        pub["arxiv_id"] = case_entry["arxiv_id"].strip()
        field_sources["arxiv_id"] = "case_json:arxiv_id"
    else:
        for a in front_anchors:
            m = ARXIV_ID_REGEX.search(a.get("text", ""))
            if m:
                pub["arxiv_id"] = m.group(1)
                field_sources["arxiv_id"] = f"pdf_text:{a['anchor_id']}"
                break
        if not pub["arxiv_id"]:
            for k in ("subject", "keywords", "title"):
                m = ARXIV_ID_REGEX.search(pdf_meta.get(k, ""))
                if m:
                    pub["arxiv_id"] = m.group(1)
                    field_sources["arxiv_id"] = f"pdf_doc_metadata:{k}"
                    break

    # 5. PMID
    if case_entry.get("pmid"):
        pub["pmid"] = str(case_entry["pmid"]).strip()
        field_sources["pmid"] = "case_json:pmid"
    else:
        for a in front_anchors:
            m = PMID_REGEX.search(a.get("text", ""))
            if m:
                pub["pmid"] = m.group(1)
                field_sources["pmid"] = f"pdf_text:{a['anchor_id']}"
                break

    # 6. Year (prefer explicit date/front-matter year -> arXiv YYMM -> XMP -> PDF creationDate)
    if case_entry.get("year") and str(case_entry["year"]).isdigit():
        pub["year"] = int(case_entry["year"])
        field_sources["year"] = "case_json:year"
    else:
        for a in front_anchors:
            if a.get("page") == 1 and a.get("section_role") == "title_meta":
                txt = a.get("text", "")
                dm = DATE_YEAR_REGEX.search(txt)
                if dm:
                    pub["year"] = int(dm.group(1))
                    field_sources["year"] = f"pdf_text:{a['anchor_id']}"
                    break
        if pub["year"] is None and pub["arxiv_id"]:
            am = ARXIV_YYMM_REGEX.match(pub["arxiv_id"])
            if am:
                yy = int(am.group(1))
                pub["year"] = 2000 + yy if yy < 80 else 1900 + yy
                field_sources["year"] = f"derived_from_arxiv_id:{pub['arxiv_id']}"
        if pub["year"] is None and xmp_meta.get("year"):
            pub["year"] = xmp_meta["year"]
            field_sources["year"] = "pdf_xmp_metadata:date"
        if pub["year"] is None:
            for a in front_anchors:
                if a.get("page") == 1 and a.get("section_role") == "title_meta":
                    ym = YEAR_REGEX.search(a.get("text", ""))
                    if ym:
                        pub["year"] = int(ym.group(1))
                        field_sources["year"] = f"pdf_text:{a['anchor_id']}"
                        break
        if pub["year"] is None and pdf_meta.get("creationDate"):
            ym = YEAR_REGEX.search(pdf_meta["creationDate"])
            if ym:
                pub["year"] = int(ym.group(1))
                field_sources["year"] = "pdf_doc_metadata:creationDate"

    # 7. Venue
    if isinstance(case_entry.get("venue"), str) and case_entry["venue"].strip():
        pub["venue"] = case_entry["venue"].strip()
        field_sources["venue"] = "case_json:venue"
    elif xmp_meta.get("venue"):
        pub["venue"] = xmp_meta["venue"]
        field_sources["venue"] = "pdf_xmp_metadata:publicationName"
    else:
        for a in front_anchors:
            if a.get("section_role") not in ("title_meta", "abstract") and a.get("page", 1) > 1:
                continue
            txt = a.get("text", "")
            for pat in VENUE_PATTERNS:
                m = pat.search(txt)
                if m:
                    cand = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;:")
                    if len(cand) >= 4:
                        pub["venue"] = cand
                        field_sources["venue"] = f"pdf_text:{a['anchor_id']}"
                        break
            if pub["venue"]:
                break
        if not pub["venue"] and pub["arxiv_id"]:
            pub["venue"] = "arXiv"
            field_sources["venue"] = f"derived_from_arxiv_id:{pub['arxiv_id']}"

    # 8. Version or Preprint
    if isinstance(case_entry.get("version"), str) and case_entry["version"].strip():
        pub["version_or_preprint"] = case_entry["version"].strip()
        field_sources["version_or_preprint"] = "case_json:version"
    elif pub["arxiv_id"]:
        vm = re.search(r"(v\d+)$", pub["arxiv_id"], re.I)
        ver_str = f"arXiv preprint ({vm.group(1)})" if vm else "arXiv preprint"
        pub["version_or_preprint"] = ver_str
        field_sources["version_or_preprint"] = field_sources.get("arxiv_id", "arxiv_id")
    else:
        for a in front_anchors:
            m = re.search(r"\b(preprint|accepted\s+manuscript|version\s+\d+(?:\.\d+)?|bioRxiv|medRxiv)\b", a.get("text", ""), re.I)
            if m:
                pub["version_or_preprint"] = m.group(1)
                field_sources["version_or_preprint"] = f"pdf_text:{a['anchor_id']}"
                break

    # 9. Canonical URL
    if isinstance(case_entry.get("url"), str) and case_entry["url"].strip():
        pub["url"] = case_entry["url"].strip()
        field_sources["url"] = "case_json:url"
    elif pub["doi"]:
        pub["url"] = f"https://doi.org/{pub['doi']}"
        field_sources["url"] = field_sources["doi"]
    elif pub["arxiv_id"]:
        pub["url"] = f"https://arxiv.org/abs/{pub['arxiv_id']}"
        field_sources["url"] = field_sources["arxiv_id"]
    else:
        for a in front_anchors:
            m = ANY_URL_REGEX.search(a.get("text", ""))
            if m:
                u = m.group(0).rstrip(".,;)>]}")
                if not DATA_CODE_URL_REGEX.match(u):
                    pub["url"] = u
                    field_sources["url"] = f"pdf_text:{a['anchor_id']}"
                    break

    # 10. Data or Code URLs
    code_items = []
    seen_urls = set()
    for a in non_ref_anchors:
        for m in DATA_CODE_URL_REGEX.finditer(a.get("text", "")):
            u = m.group(0).rstrip(".,;)>]}")
            if u not in seen_urls:
                seen_urls.add(u)
                code_items.append({"url": u, "source": f"pdf_text:{a['anchor_id']}"})
    if code_items:
        pub["data_or_code_urls"] = [c["url"] for c in code_items]
        field_sources["data_or_code_urls"] = ",".join(c["source"] for c in code_items)

    # 11. License
    if isinstance(case_entry.get("license"), str) and case_entry["license"].strip():
        pub["license"] = case_entry["license"].strip()
        field_sources["license"] = "case_json:license"
    elif xmp_meta.get("license"):
        pub["license"] = xmp_meta["license"]
        field_sources["license"] = "pdf_xmp_metadata:rights"
    else:
        for a in non_ref_anchors:
            if a.get("page", 1) > 2 and a.get("section_role") not in ("appendix", "discussion"):
                continue
            txt = a.get("text", "")
            for pat in LICENSE_PATTERNS:
                m = pat.search(txt)
                if m:
                    pub["license"] = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
                    field_sources["license"] = f"pdf_text:{a['anchor_id']}"
                    break
            if pub["license"]:
                break

    # Record missing fields honestly
    missing = []
    for fld in TRACKED_FIELDS:
        val = pub.get(fld)
        if val is None or val == [] or val == "":
            missing.append(fld)
    pub["missing_fields"] = missing

    return pub


def _norm_ws(text):
    return re.sub(r"\s+", " ", _normalize_ligatures(text or "")).strip().lower()


def verify_pubinfo_against_source(claimed_pubinfo, extracted_pubinfo, full_doc_text):
    """
    Verify claimed pubinfo fields against deterministic extraction & full document text.
    Flags fabricated DOIs, arXiv IDs, PMIDs, years, or ungrounded titles/venues.
    """
    claimed = claimed_pubinfo or {}
    extracted = extracted_pubinfo or {}
    norm_doc = _norm_ws(full_doc_text)

    hallucinations = []
    mismatches = []
    warnings = []
    verified_fields = []

    # Identifier fields: strict check
    for id_fld in ("doi", "arxiv_id", "pmid"):
        c_val = claimed.get(id_fld)
        e_val = extracted.get(id_fld)
        if c_val is None or str(c_val).strip() == "":
            continue
        c_str = str(c_val).strip()
        if e_val and c_str.lower() == str(e_val).strip().lower():
            verified_fields.append(id_fld)
        elif c_str.lower() in norm_doc:
            verified_fields.append(id_fld)
            if e_val and c_str.lower() != str(e_val).strip().lower():
                warnings.append(f"{id_fld} differs from primary extracted value ({e_val}) but appears in text.")
        else:
            hallucinations.append({
                "field": id_fld,
                "claimed": c_str,
                "extracted": e_val,
                "reason": f"Claimed {id_fld} '{c_str}' was neither deterministically extracted nor found in source text.",
            })

    # Year check
    c_year = claimed.get("year")
    e_year = extracted.get("year")
    if c_year is not None and str(c_year).strip() != "":
        try:
            cy_int = int(str(c_year).strip())
            if e_year is not None and cy_int == int(e_year):
                verified_fields.append("year")
            elif str(cy_int) in norm_doc:
                verified_fields.append("year")
                if e_year is not None and cy_int != int(e_year):
                    mismatches.append({"field": "year", "claimed": cy_int, "extracted": e_year})
            else:
                hallucinations.append({
                    "field": "year",
                    "claimed": cy_int,
                    "extracted": e_year,
                    "reason": f"Claimed year {cy_int} not found in front matter or document text.",
                })
        except ValueError:
            mismatches.append({"field": "year", "claimed": c_year, "extracted": e_year})

    # Title / Venue / License check
    for txt_fld in ("title", "venue", "license"):
        c_val = claimed.get(txt_fld)
        e_val = extracted.get(txt_fld)
        if not c_val or not str(c_val).strip():
            continue
        c_norm = _norm_ws(str(c_val))
        e_norm = _norm_ws(str(e_val)) if e_val else ""
        if e_norm and (c_norm == e_norm or c_norm in e_norm or e_norm in c_norm):
            verified_fields.append(txt_fld)
        elif c_norm in norm_doc:
            verified_fields.append(txt_fld)
        else:
            if txt_fld == "venue" and e_val is None:
                hallucinations.append({
                    "field": txt_fld,
                    "claimed": str(c_val),
                    "extracted": None,
                    "reason": f"Claimed venue '{c_val}' not found in source text.",
                })
            else:
                mismatches.append({"field": txt_fld, "claimed": str(c_val), "extracted": e_val})

    # Authors check
    c_auth = claimed.get("authors") or []
    e_auth = extracted.get("authors") or []
    if c_auth:
        missing_auth = []
        for a in c_auth:
            a_str = str(a).strip()
            last_name = a_str.split()[-1].lower() if a_str.split() else ""
            in_ext = any(a_str.lower() in ea.lower() or (last_name and last_name in ea.lower()) for ea in e_auth)
            in_doc = (a_str.lower() in norm_doc) or (len(last_name) >= 3 and last_name in norm_doc)
            if not (in_ext or in_doc):
                missing_auth.append(a_str)
        if missing_auth:
            hallucinations.append({
                "field": "authors",
                "claimed": missing_auth,
                "extracted": e_auth,
                "reason": f"Claimed authors {missing_auth} not verified in extracted authors or document text.",
            })
        else:
            verified_fields.append("authors")

    # Provenance field_sources check
    c_sources = claimed.get("field_sources") or {}
    for fld in ("title", "year", "venue", "doi", "arxiv_id", "pmid"):
        if claimed.get(fld) is not None and fld not in c_sources:
            warnings.append(f"Field '{fld}' lacks provenance entry in field_sources.")

    return {
        "passed": len(hallucinations) == 0,
        "hallucinations": hallucinations,
        "mismatches": mismatches,
        "warnings": warnings,
        "verified_fields": sorted(set(verified_fields)),
    }


def extract_pubinfo_from_index(evidence_index, paper_signals=None, case_data=None):
    """Run extract_doc_pubinfo across all documents in evidence_index."""
    anchors = evidence_index.get("anchors", [])
    docs = evidence_index.get("documents", [])
    if not docs and anchors:
        seen_d = []
        for a in anchors:
            did = a.get("doc_id", "D1")
            if did not in seen_d:
                seen_d.append(did)
                docs.append({"doc_id": did, "source_file": a.get("source_file", "unknown"), "pdf_metadata": {}})

    sig_by_doc = {}
    if paper_signals and isinstance(paper_signals.get("documents"), list):
        for ds in paper_signals["documents"]:
            sig_by_doc[ds.get("doc_id")] = ds

    anchors_by_doc = {}
    for a in anchors:
        anchors_by_doc.setdefault(a.get("doc_id", "D1"), []).append(a)

    results = []
    for dm in docs:
        did = dm.get("doc_id", "D1")
        results.append(
            extract_doc_pubinfo(
                dm,
                anchors_by_doc.get(did, []),
                doc_signals=sig_by_doc.get(did),
                case_data=case_data,
            )
        )
    return {
        "case_id": evidence_index.get("case_id", "unknown"),
        "doc_count": len(results),
        "sources": results,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Deterministic Provenance & PubInfo Extractor")
    parser.add_argument("--work-dir", default=None, help="Work directory containing evidence_index.json")
    parser.add_argument("--index", default=None, help="Explicit path to evidence_index.json")
    parser.add_argument("--case-json", default=None, help="Optional path to input case.json")
    parser.add_argument("--out", default=None, help="Optional output path (defaults to <work-dir>/pubinfo.json)")
    args = parser.parse_args()

    index_path = args.index or (os.path.join(args.work_dir, "evidence_index.json") if args.work_dir else None)
    if not index_path or not os.path.exists(index_path):
        raise FileNotFoundError(f"evidence_index.json not found: {index_path}")

    with open(index_path, "r", encoding="utf-8") as f:
        evidence_index = json.load(f)

    work_dir = args.work_dir or os.path.dirname(os.path.abspath(index_path))
    sig_path = os.path.join(work_dir, "paper_signals.json")
    paper_signals = None
    if os.path.exists(sig_path):
        with open(sig_path, "r", encoding="utf-8") as f:
            paper_signals = json.load(f)

    case_data = None
    if args.case_json and os.path.exists(args.case_json):
        with open(args.case_json, "r", encoding="utf-8") as f:
            case_data = json.load(f)
    else:
        cm_path = os.path.join(work_dir, "case_manifest.json")
        if os.path.exists(cm_path):
            with open(cm_path, "r", encoding="utf-8") as f:
                cm = json.load(f)
                case_data = cm.get("case_metadata")

    pub_bundle = extract_pubinfo_from_index(evidence_index, paper_signals=paper_signals, case_data=case_data)
    out_path = args.out or os.path.join(work_dir, "pubinfo.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pub_bundle, f, ensure_ascii=False, indent=2)

    print(f"[pubinfo_extractor] Extracted pubinfo for {pub_bundle['doc_count']} doc(s) -> {out_path}")


if __name__ == "__main__":
    main()
