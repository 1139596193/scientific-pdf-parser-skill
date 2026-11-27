#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pubinfo_extractor.py - Strictly Isolated Per-Paper Provenance & Anti-Leakage Metadata Extractor
Prevents root case.json DOI/title/authors from leaking onto secondary papers.
Part of the Literature Knowledge Graph Skill (task_10_kg).
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


def _normalize_ligatures(text):
    if not text:
        return ""
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\ufb00", "ff").replace("\ufb03", "ffi").replace("\ufb04", "ffl")
    text = text.replace("\ufb05", "st").replace("\ufb06", "st")
    return re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)


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
    val = str(raw).strip().rstrip(".,;)>]}")
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
    authors = []
    seen = set()
    source_aids = []
    for anc in front_anchors:
        p = anc.get("page")
        if p is not None and p > 2:
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
            names = _split_author_string(s)
            if names:
                for n in names:
                    if n.lower() not in seen:
                        seen.add(n.lower())
                        authors.append(n)
                if anc["anchor_id"] not in source_aids:
                    source_aids.append(anc["anchor_id"])
    return authors, source_aids


def _get_scoped_case_entry(case_data, doc_meta, doc_index=0):
    """
    Match ONLY explicitly scoped document entry in case.json (e.g. case.json['documents'][i]).
    Root case.json fields (title, doi, authors, year, venue, license) are STRICTLY
    restricted to role == 'primary' (and doc_index == 0); NEVER returned for secondary papers.
    """
    if not isinstance(case_data, dict):
        return {}, False
    src_file = (doc_meta.get("source_file") or "").lower()
    rel_path = (doc_meta.get("rel_path") or "").lower()
    did = doc_meta.get("doc_id")
    sid = doc_meta.get("source_id")
    role = (doc_meta.get("role") or ("primary" if doc_index == 0 else "secondary")).lower()

    for key in ("documents", "papers", "sources", "files"):
        val = case_data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    item_path = str(item.get("path") or item.get("file") or item.get("source_file") or "").lower()
                    item_id = item.get("doc_id") or item.get("source_id") or item.get("id")
                    item_role = str(item.get("role") or "").lower()
                    if (
                        (item_id and item_id in (did, sid))
                        or (item_role and item_role == role)
                        or (src_file and src_file in item_path)
                        or (rel_path and rel_path in item_path)
                    ):
                        return item, False

    allow_root_fallback = (role == "primary" and doc_index == 0)
    if allow_root_fallback:
        return case_data, True
    return {}, False


def extract_doc_pubinfo(doc_meta, doc_anchors, doc_signals=None, case_data=None, doc_index=0):
    """
    Deterministically extract publication metadata for ONE independent paper.
    Hard-enforces root case.json metadata isolation so secondary papers never inherit
    primary DOI/title/authors.
    """
    doc_id = doc_meta.get("doc_id", "D1")
    source_id = doc_meta.get("source_id") or doc_id
    role = (doc_meta.get("role") or ("primary" if doc_index == 0 else "secondary")).lower()
    src_file = doc_meta.get("source_file", "unknown")
    source_hash = doc_meta.get("source_hash")
    manifest_meta = doc_meta.get("manifest_meta") or {}
    embedded_meta = doc_meta.get("embedded_meta") or {}
    twin_meta = doc_meta.get("twin_meta") or {}
    pdf_meta = doc_meta.get("pdf_metadata") or {}
    xmp_meta = _parse_xmp_metadata(pdf_meta.get("xmp_xml", ""))
    case_entry, is_root_primary_fallback = _get_scoped_case_entry(case_data, doc_meta, doc_index=doc_index)
    sig_meta = (doc_signals or {}).get("meta_clues") or {}

    field_provenance = {}
    pub = {
        "doc_id": doc_id,
        "source_id": source_id,
        "role": role,
        "source_file": src_file,
        "source_hash": source_hash,
        "is_verification_twin": False,
        "verification_twin_file": doc_meta.get("verification_twin_file"),
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
        "license": manifest_meta.get("license") or embedded_meta.get("license") or twin_meta.get("license"),
        "field_provenance": field_provenance,
        "field_sources": field_provenance,
        "field_status": {},
        "missing_fields": [],
        "extracted_by": "task_10_kg/pubinfo_extractor.py",
    }

    if manifest_meta.get("license"):
        field_provenance["license"] = "corpus_manifest:license"
    elif embedded_meta.get("license"):
        field_provenance["license"] = f"embedded_json:{source_id}:license"
    elif twin_meta.get("license"):
        field_provenance["license"] = f"verification_twin_xml:{source_id}:license"

    front_anchors = [
        a for a in doc_anchors
        if (a.get("page") is None or a.get("page") <= 2) and a.get("zone") != "references_locked"
    ]
    non_ref_anchors = [a for a in doc_anchors if a.get("zone") != "references_locked"]

    def _case_src_tag(fld):
        return f"case_json_root_primary_only:{fld}" if is_root_primary_fallback else f"case_json_scoped:{fld}"

    # 1. Title
    if manifest_meta.get("title"):
        pub["title"] = manifest_meta["title"].strip()
        field_provenance["title"] = "corpus_manifest:title"
    elif embedded_meta.get("title"):
        pub["title"] = embedded_meta["title"].strip()
        field_provenance["title"] = f"embedded_json:{source_id}:title"
    elif twin_meta.get("title"):
        pub["title"] = twin_meta["title"].strip()
        field_provenance["title"] = f"verification_twin_xml:{source_id}:title"
    elif isinstance(case_entry.get("title"), str) and case_entry["title"].strip():
        pub["title"] = case_entry["title"].strip()
        field_provenance["title"] = _case_src_tag("title")
    else:
        for a in front_anchors:
            if a.get("block_type") == "title" and a.get("text", "").strip():
                pub["title"] = re.sub(r"\s+", " ", _normalize_ligatures(a["text"])).strip()
                field_provenance["title"] = f"doc_text:{a['anchor_id']}"
                break
        if not pub["title"] and pdf_meta.get("title"):
            t_meta = pdf_meta["title"].strip()
            if len(t_meta) >= 5 and not re.match(r"^(?:untitled|microsoft\s+word|arxiv:)", t_meta, re.I):
                pub["title"] = t_meta
                field_provenance["title"] = "pdf_doc_metadata:title"
        if not pub["title"] and sig_meta.get("candidate_title") and sig_meta["candidate_title"] != "Untitled":
            pub["title"] = sig_meta["candidate_title"]
            field_provenance["title"] = "paper_signals:candidate_title"

    # 2. Authors
    if manifest_meta.get("authors"):
        pub["authors"] = [str(x).strip() for x in manifest_meta["authors"] if str(x).strip()]
        field_provenance["authors"] = "corpus_manifest:authors"
    elif embedded_meta.get("authors"):
        pub["authors"] = [str(x).strip() for x in embedded_meta["authors"] if str(x).strip()]
        field_provenance["authors"] = f"embedded_json:{source_id}:authors"
    elif twin_meta.get("authors"):
        pub["authors"] = [str(x).strip() for x in twin_meta["authors"] if str(x).strip()]
        field_provenance["authors"] = f"verification_twin_xml:{source_id}:authors"
    elif isinstance(case_entry.get("authors"), list) and case_entry["authors"]:
        pub["authors"] = [str(x).strip() for x in case_entry["authors"] if str(x).strip()]
        field_provenance["authors"] = _case_src_tag("authors")
    else:
        a_list, a_srcs = _extract_authors_from_anchors(front_anchors)
        if a_list:
            pub["authors"] = a_list
            field_provenance["authors"] = ",".join(f"doc_text:{aid}" for aid in a_srcs)
        elif sig_meta.get("authors_cleaned"):
            pub["authors"] = list(sig_meta["authors_cleaned"])
            field_provenance["authors"] = "paper_signals:authors_cleaned"
        elif pdf_meta.get("author"):
            meta_authors = _split_author_string(pdf_meta["author"])
            if meta_authors:
                pub["authors"] = meta_authors
                field_provenance["authors"] = "pdf_doc_metadata:author"

    # 3. DOI
    if _clean_doi(manifest_meta.get("doi")):
        pub["doi"] = _clean_doi(manifest_meta["doi"])
        field_provenance["doi"] = "corpus_manifest:doi"
    elif _clean_doi(embedded_meta.get("doi")):
        pub["doi"] = _clean_doi(embedded_meta["doi"])
        field_provenance["doi"] = f"embedded_json:{source_id}:doi"
    elif _clean_doi(twin_meta.get("doi")):
        pub["doi"] = _clean_doi(twin_meta["doi"])
        field_provenance["doi"] = f"verification_twin_xml:{source_id}:doi"
    elif _clean_doi(case_entry.get("doi")):
        pub["doi"] = _clean_doi(case_entry["doi"])
        field_provenance["doi"] = _case_src_tag("doi")
    elif xmp_meta.get("doi"):
        pub["doi"] = xmp_meta["doi"]
        field_provenance["doi"] = "pdf_xmp_metadata:doi"
    else:
        for a in front_anchors:
            if a.get("section_role") not in ("title_meta", "abstract", "body_unclassified"):
                continue
            m = DOI_REGEX.search(a.get("text", ""))
            if m:
                d = _clean_doi(m.group(1))
                if d:
                    pub["doi"] = d
                    field_provenance["doi"] = f"doc_text:{a['anchor_id']}"
                    break
        if not pub["doi"]:
            for k in ("subject", "keywords"):
                m = DOI_REGEX.search(pdf_meta.get(k, ""))
                if m:
                    d = _clean_doi(m.group(1))
                    if d:
                        pub["doi"] = d
                        field_provenance["doi"] = f"pdf_doc_metadata:{k}"
                        break

    # 4. arXiv ID
    if isinstance(case_entry.get("arxiv_id"), str) and case_entry["arxiv_id"].strip():
        pub["arxiv_id"] = case_entry["arxiv_id"].strip()
        field_provenance["arxiv_id"] = _case_src_tag("arxiv_id")
    else:
        for a in front_anchors:
            m = ARXIV_ID_REGEX.search(a.get("text", ""))
            if m:
                pub["arxiv_id"] = m.group(1)
                field_provenance["arxiv_id"] = f"doc_text:{a['anchor_id']}"
                break
        if not pub["arxiv_id"]:
            for k in ("subject", "keywords", "title"):
                m = ARXIV_ID_REGEX.search(pdf_meta.get(k, ""))
                if m:
                    pub["arxiv_id"] = m.group(1)
                    field_provenance["arxiv_id"] = f"pdf_doc_metadata:{k}"
                    break

    # 5. PMID
    if case_entry.get("pmid"):
        pub["pmid"] = str(case_entry["pmid"]).strip()
        field_provenance["pmid"] = _case_src_tag("pmid")
    else:
        for a in front_anchors:
            m = PMID_REGEX.search(a.get("text", ""))
            if m:
                pub["pmid"] = m.group(1)
                field_provenance["pmid"] = f"doc_text:{a['anchor_id']}"
                break

    # 6. Year
    if manifest_meta.get("year") is not None:
        pub["year"] = int(manifest_meta["year"])
        field_provenance["year"] = "corpus_manifest:year"
    elif embedded_meta.get("year") is not None:
        pub["year"] = int(embedded_meta["year"])
        field_provenance["year"] = f"embedded_json:{source_id}:year"
    elif twin_meta.get("year") is not None:
        pub["year"] = int(twin_meta["year"])
        field_provenance["year"] = f"verification_twin_xml:{source_id}:year"
    elif case_entry.get("year") and str(case_entry["year"]).isdigit():
        pub["year"] = int(case_entry["year"])
        field_provenance["year"] = _case_src_tag("year")
    else:
        for a in front_anchors:
            if a.get("page") == 1 and a.get("section_role") == "title_meta":
                dm = DATE_YEAR_REGEX.search(a.get("text", ""))
                if dm:
                    pub["year"] = int(dm.group(1))
                    field_provenance["year"] = f"doc_text:{a['anchor_id']}"
                    break
        if pub["year"] is None and pub["arxiv_id"]:
            am = ARXIV_YYMM_REGEX.match(pub["arxiv_id"])
            if am:
                yy = int(am.group(1))
                pub["year"] = 2000 + yy if yy < 80 else 1900 + yy
                field_provenance["year"] = f"derived_from_arxiv_id:{pub['arxiv_id']}"
        if pub["year"] is None and xmp_meta.get("year"):
            pub["year"] = xmp_meta["year"]
            field_provenance["year"] = "pdf_xmp_metadata:date"
        if pub["year"] is None and pdf_meta.get("creationDate"):
            ym = YEAR_REGEX.search(pdf_meta["creationDate"])
            if ym:
                pub["year"] = int(ym.group(1))
                field_provenance["year"] = "pdf_doc_metadata:creationDate"

    # 7. Venue
    if manifest_meta.get("venue"):
        pub["venue"] = manifest_meta["venue"]
        field_provenance["venue"] = "corpus_manifest:venue"
    elif embedded_meta.get("venue"):
        pub["venue"] = embedded_meta["venue"]
        field_provenance["venue"] = f"embedded_json:{source_id}:venue"
    elif twin_meta.get("venue"):
        pub["venue"] = twin_meta["venue"]
        field_provenance["venue"] = f"verification_twin_xml:{source_id}:venue"
    elif isinstance(case_entry.get("venue"), str) and case_entry["venue"].strip():
        pub["venue"] = case_entry["venue"].strip()
        field_provenance["venue"] = _case_src_tag("venue")
    elif xmp_meta.get("venue"):
        pub["venue"] = xmp_meta["venue"]
        field_provenance["venue"] = "pdf_xmp_metadata:publicationName"
    else:
        for a in front_anchors:
            if a.get("section_role") not in ("title_meta", "abstract"):
                continue
            txt = a.get("text", "")
            for pat in VENUE_PATTERNS:
                m = pat.search(txt)
                if m:
                    cand = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;:")
                    if len(cand) >= 4:
                        pub["venue"] = cand
                        field_provenance["venue"] = f"doc_text:{a['anchor_id']}"
                        break
            if pub["venue"]:
                break
        if not pub["venue"] and pub["arxiv_id"]:
            pub["venue"] = "arXiv"
            field_provenance["venue"] = f"derived_from_arxiv_id:{pub['arxiv_id']}"

    # 8. Version or Preprint
    if isinstance(case_entry.get("version"), str) and case_entry["version"].strip():
        pub["version_or_preprint"] = case_entry["version"].strip()
        field_provenance["version_or_preprint"] = _case_src_tag("version")
    elif pub["arxiv_id"]:
        vm = re.search(r"(v\d+)$", pub["arxiv_id"], re.I)
        pub["version_or_preprint"] = f"arXiv preprint ({vm.group(1)})" if vm else "arXiv preprint"
        field_provenance["version_or_preprint"] = field_provenance.get("arxiv_id", "arxiv_id")

    # 9. Canonical URL
    if isinstance(case_entry.get("url"), str) and case_entry["url"].strip():
        pub["url"] = case_entry["url"].strip()
        field_provenance["url"] = _case_src_tag("url")
    elif pub["doi"]:
        pub["url"] = f"https://doi.org/{pub['doi']}"
        field_provenance["url"] = field_provenance["doi"]
    elif pub["arxiv_id"]:
        pub["url"] = f"https://arxiv.org/abs/{pub['arxiv_id']}"
        field_provenance["url"] = field_provenance["arxiv_id"]

    # 10. Data or Code URLs
    code_items = []
    seen_urls = set()
    for a in non_ref_anchors:
        for m in DATA_CODE_URL_REGEX.finditer(a.get("text", "")):
            u = m.group(0).rstrip(".,;)>]}")
            if u not in seen_urls:
                seen_urls.add(u)
                code_items.append({"url": u, "source": f"doc_text:{a['anchor_id']}"})
    if code_items:
        pub["data_or_code_urls"] = [c["url"] for c in code_items]
        field_provenance["data_or_code_urls"] = ",".join(c["source"] for c in code_items)

    # 11. License fallback from case/XMP/front text
    if not pub["license"]:
        if isinstance(case_entry.get("license"), str) and case_entry["license"].strip():
            pub["license"] = case_entry["license"].strip()
            field_provenance["license"] = _case_src_tag("license")
        elif xmp_meta.get("license"):
            pub["license"] = xmp_meta["license"]
            field_provenance["license"] = "pdf_xmp_metadata:rights"
        else:
            for a in non_ref_anchors:
                p = a.get("page")
                if p is not None and p > 2 and a.get("section_role") not in ("appendix", "discussion"):
                    continue
                for pat in LICENSE_PATTERNS:
                    m = pat.search(a.get("text", ""))
                    if m:
                        pub["license"] = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
                        field_provenance["license"] = f"doc_text:{a['anchor_id']}"
                        break
                if pub["license"]:
                    break

    missing = []
    for fld in TRACKED_FIELDS:
        val = pub.get(fld)
        if val is None or val == [] or val == "":
            missing.append(fld)
            pub["field_status"][fld] = "unreported"
            field_provenance.setdefault(fld, "unreported")
        else:
            pub["field_status"][fld] = "reported"
    pub["missing_fields"] = missing
    return pub


def detect_root_metadata_leakage(pubinfo_list, root_case_metadata=None):
    """
    Cross-check extracted pubinfo across papers to catch root DOI/title/author leakage
    onto secondary papers or distinct-hash papers sharing identical DOI/title.
    """
    alerts = []
    seen_doi = {}
    seen_title = {}

    root_doi = _clean_doi((root_case_metadata or {}).get("doi"))
    root_title = re.sub(r"\s+", " ", str((root_case_metadata or {}).get("title") or "")).strip().lower()

    for idx, p in enumerate(pubinfo_list or []):
        sid = p.get("source_id") or p.get("doc_id") or f"doc_{idx + 1}"
        role = (p.get("role") or ("primary" if idx == 0 else "secondary")).lower()
        shash = p.get("source_hash")
        doi = _clean_doi(p.get("doi"))
        title_norm = re.sub(r"\s+", " ", str(p.get("title") or "")).strip().lower()
        prov = p.get("field_provenance") or {}

        # Check secondary paper against root_case_metadata
        if role != "primary" or idx > 0:
            for fld in ("doi", "title", "authors"):
                if "root_primary" in str(prov.get(fld, "")):
                    alerts.append({
                        "code": "ROOT_METADATA_PROVENANCE_LEAK",
                        "severity": "HIGH",
                        "source_id": sid,
                        "field": fld,
                        "message": f"Secondary document '{sid}' inherited '{fld}' from root case.json provenance.",
                    })
            if root_doi and doi and doi.lower() == root_doi.lower():
                alerts.append({
                    "code": "SECONDARY_SHARES_ROOT_DOI",
                    "severity": "HIGH",
                    "source_id": sid,
                    "field": "doi",
                    "value": doi,
                    "message": f"Secondary document '{sid}' has root case DOI '{doi}'. Verify against secondary manifest/XML.",
                })
            if root_title and title_norm and title_norm == root_title:
                alerts.append({
                    "code": "SECONDARY_SHARES_ROOT_TITLE",
                    "severity": "HIGH",
                    "source_id": sid,
                    "field": "title",
                    "value": p.get("title"),
                    "message": f"Secondary document '{sid}' shares exact root case title '{p.get('title')}'.",
                })

        if doi:
            dkey = doi.lower()
            if dkey in seen_doi:
                prev_sid, prev_hash = seen_doi[dkey]
                if prev_sid != sid and (not shash or not prev_hash or shash != prev_hash):
                    alerts.append({
                        "code": "DUPLICATE_DOI_ACROSS_PAPERS",
                        "severity": "HIGH",
                        "source_id": sid,
                        "other_source_id": prev_sid,
                        "field": "doi",
                        "value": doi,
                        "message": f"Distinct papers '{prev_sid}' and '{sid}' share identical DOI '{doi}'.",
                    })
            else:
                seen_doi[dkey] = (sid, shash)

        if title_norm:
            if title_norm in seen_title:
                prev_sid, prev_hash = seen_title[title_norm]
                if prev_sid != sid and (not shash or not prev_hash or shash != prev_hash):
                    alerts.append({
                        "code": "DUPLICATE_TITLE_ACROSS_PAPERS",
                        "severity": "HIGH",
                        "source_id": sid,
                        "other_source_id": prev_sid,
                        "field": "title",
                        "value": p.get("title"),
                        "message": f"Distinct papers '{prev_sid}' and '{sid}' share identical title.",
                    })
            else:
                seen_title[title_norm] = (sid, shash)

    return {
        "leakage_detected": len(alerts) > 0,
        "alert_count": len(alerts),
        "alerts": alerts,
    }


def extract_pubinfo_from_index(evidence_index, paper_signals=None, case_data=None, root_case_metadata=None):
    """Run extract_doc_pubinfo across all independent papers in evidence_index."""
    anchors = evidence_index.get("anchors", [])
    docs = [d for d in evidence_index.get("documents", []) if not d.get("is_verification_twin")]
    if not docs and anchors:
        seen_d = []
        for a in anchors:
            did = a.get("doc_id", "D1")
            if did not in seen_d:
                seen_d.append(did)
                docs.append({
                    "doc_id": did,
                    "source_id": a.get("source_id") or did,
                    "role": a.get("role") or ("primary" if len(seen_d) == 1 else "secondary"),
                    "source_file": a.get("source_file", "unknown"),
                    "pdf_metadata": {},
                })

    sig_by_doc = {}
    if paper_signals and isinstance(paper_signals.get("documents"), list):
        for ds in paper_signals["documents"]:
            sig_by_doc[ds.get("doc_id")] = ds

    anchors_by_doc = {}
    for a in anchors:
        anchors_by_doc.setdefault(a.get("doc_id", "D1"), []).append(a)

    results = []
    for idx, dm in enumerate(docs):
        did = dm.get("doc_id", f"D{idx + 1}")
        results.append(
            extract_doc_pubinfo(
                dm,
                anchors_by_doc.get(did, []),
                doc_signals=sig_by_doc.get(did),
                case_data=case_data,
                doc_index=idx,
            )
        )

    eff_root = root_case_metadata
    if eff_root is None and isinstance(case_data, dict):
        eff_root = {
            "title": case_data.get("title"),
            "doi": case_data.get("doi"),
            "authors": case_data.get("authors"),
        }
    leakage_report = detect_root_metadata_leakage(results, root_case_metadata=eff_root)

    return {
        "case_id": evidence_index.get("case_id", "unknown"),
        "doc_count": len(results),
        "sources": results,
        "leakage_check": leakage_report,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Isolated Per-Paper PubInfo & Anti-Leakage Extractor")
    parser.add_argument("--work-dir", default=None, help="Work directory containing evidence_index.json")
    parser.add_argument("--index", default=None, help="Explicit path to evidence_index.json")
    parser.add_argument("--case-json", default=None, help="Optional path to input case.json")
    parser.add_argument("--out", default=None, help="Optional output JSON path")
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
    root_meta = None
    if args.case_json and os.path.exists(args.case_json):
        with open(args.case_json, "r", encoding="utf-8") as f:
            case_data = json.load(f)
    else:
        cm_path = os.path.join(work_dir, "case_manifest.json")
        if os.path.exists(cm_path):
            with open(cm_path, "r", encoding="utf-8") as f:
                cm = json.load(f)
                case_data = cm.get("case_metadata")
                root_meta = cm.get("root_case_metadata")

    pub_bundle = extract_pubinfo_from_index(
        evidence_index,
        paper_signals=paper_signals,
        case_data=case_data,
        root_case_metadata=root_meta,
    )
    out_path = args.out or os.path.join(work_dir, "pubinfo.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(pub_bundle, f, ensure_ascii=False, indent=2)

    print(
        f"[pubinfo_extractor] Extracted pubinfo for {pub_bundle['doc_count']} independent doc(s) "
        f"| leakage_detected={pub_bundle['leakage_check']['leakage_detected']} -> {out_path}"
    )


if __name__ == "__main__":
    main()
