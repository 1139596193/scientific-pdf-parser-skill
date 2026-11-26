#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_deliverables.py - Multi-Format Nanopublication Renderer & Dynamic Output Contract Adapter
Generates: result.json, nanopubs.jsonld, nanopubs.trig, nanopubs.csv, report.md, audit_report.json
Part of the Nanopublication Scientific Reading Skill (task_09_nanopub).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import csv
import argparse
from datetime import datetime, timezone


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_safe(path, default=None):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def extract_contract_fields(output_contract):
    """Extract required/declared field schemas from flexible output_contract definitions."""
    fields = {}
    if not output_contract:
        return fields
    if isinstance(output_contract, list):
        for item in output_contract:
            if isinstance(item, str) and item.strip():
                fields[item.strip()] = "any"
        return fields
    if isinstance(output_contract, dict):
        if "properties" in output_contract and isinstance(output_contract["properties"], dict):
            for k, v in output_contract["properties"].items():
                fields[k] = v if isinstance(v, dict) else {"type": str(v) if isinstance(v, str) else "any"}
            for req in output_contract.get("required", []):
                if req not in fields:
                    fields[req] = {"type": "any"}
            return fields
        for k, v in output_contract.items():
            if k in ("type", "description", "schema_version", "required"):
                continue
            fields[k] = v if isinstance(v, dict) else (str(v) if isinstance(v, str) else "any")
    return fields


def _coerce_value_to_expected_type(val, expected_type, field_name, payload):
    exp_low = str(expected_type).strip().lower()
    if exp_low in ("object", "dict") and not isinstance(val, dict):
        if isinstance(val, list):
            return {"items": val, "count": len(val)}
        return {"value": val}
    if exp_low in ("array", "list") and not isinstance(val, list):
        if isinstance(val, dict):
            if "items" in val and isinstance(val["items"], list):
                return val["items"]
            return [val]
        return [val] if val is not None else []
    if exp_low in ("string", "str") and not isinstance(val, str):
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return str(val) if val is not None else ""
    if exp_low in ("number", "float") and not isinstance(val, (int, float)):
        return float(len(payload.get("nanopublications", [])))
    if exp_low in ("integer", "int") and (not isinstance(val, int) or isinstance(val, bool)):
        return len(payload.get("nanopublications", []))
    if exp_low in ("boolean", "bool") and not isinstance(val, bool):
        return bool(payload.get("audit_summary", {}).get("audit_passed", False))
    return val


def map_contract_field_value(field_name, expected_type, payload):
    """Recursively map canonical Nanopublication payload into arbitrary nested output_contract keys."""
    if isinstance(expected_type, dict):
        exp_t = expected_type.get("type", "any")
        props = expected_type.get("properties")
        reqs = expected_type.get("required") or []
        if (exp_t == "object" or props is not None) and (isinstance(props, dict) or reqs):
            obj = {}
            if isinstance(props, dict):
                for sub_k, sub_schema in props.items():
                    obj[sub_k] = map_contract_field_value(sub_k, sub_schema, payload)
            for req_k in reqs:
                if req_k not in obj:
                    obj[req_k] = map_contract_field_value(req_k, "any", payload)
            return obj
        if "type" not in expected_type and "properties" not in expected_type:
            obj = {}
            for sub_k, sub_schema in expected_type.items():
                obj[sub_k] = map_contract_field_value(sub_k, sub_schema, payload)
            return obj
        expected_type = exp_t

    low = field_name.lower()
    mapped = None

    if low == "case_id":
        mapped = payload["case_id"]
    elif low in ("status", "state", "run_status", "运行状态"):
        mapped = payload["status"]
    elif any(k in low for k in (
        "nanopub", "assertion", "atomic_claim", "extracted_claim", "claim",
        "triple", "finding", "knowledge_unit", "断言",
    )):
        mapped = payload["nanopublications"]
    elif any(k in low for k in (
        "qa_answer", "grounded_answer", "question_answer", "qa_result",
        "answers", "response", "qa", "questions", "问答", "回答",
    )):
        mapped = payload["qa_answers"]
    elif any(k in low for k in (
        "pubinfo", "bibliograph", "source_meta", "paper_meta",
        "metadata", "source_info", "doc_meta", "文献信息", "来源信息",
    )):
        mapped = payload["documents_pubinfo"]
    elif any(k in low for k in ("conflict", "contradict", "冲突")):
        mapped = payload["conflicts"]
    elif any(k in low for k in ("audit", "quality", "verif", "审计", "质检")):
        mapped = payload["audit_summary"]
    elif any(k in low for k in ("provenance", "evidence", "citation", "anchor", "sources", "证据", "来源")):
        evidence_records = [
            {
                "nanopub_id": n["head"]["nanopub_id"],
                "location": n["provenance"]["location"],
                "evidence_quote": n["provenance"]["evidence_quote"],
                "alignment_status": n["provenance"]["alignment_status"],
                "evidence_tier": n["provenance"]["evidence_tier"],
            }
            for n in payload["nanopublications"]
        ]
        if str(expected_type).lower() in ("object", "dict"):
            mapped = {
                "documents_pubinfo": payload["documents_pubinfo"],
                "evidence_anchors": evidence_records,
            }
        elif "source" in low:
            mapped = payload["documents_pubinfo"]
        else:
            mapped = evidence_records
    elif any(k in low for k in ("uncertain", "limitation", "warning", "risk", "gap", "不确定", "局限", "风险")):
        mapped = payload["uncertainty"]
    elif any(k in low for k in ("manual", "checklist", "review", "人工", "复核")):
        mapped = payload["manual_review_checklist"]
    elif any(k in low for k in ("change", "revision", "history", "变更", "修改")):
        mapped = payload["changes"]
    elif any(k in low for k in ("deliverable", "artifact", "file", "export", "output", "导出")):
        mapped = payload["deliverables"]
    elif any(k in low for k in ("summary", "report", "result", "reading", "摘要", "报告")):
        mapped = {
            "case_id": payload["case_id"],
            "status": payload["status"],
            "nanopublication_count": len(payload["nanopublications"]),
            "qa_count": len(payload["qa_answers"]),
            "conflict_count": len(payload["conflicts"]),
        }
    else:
        mapped = {} if str(expected_type).lower() in ("object", "dict") else (
            [] if str(expected_type).lower() in ("array", "list") else payload["status"]
        )

    return _coerce_value_to_expected_type(mapped, expected_type, field_name, payload)


BLOCKER_UNCERTAINTY_CATEGORIES = {
    "scanned_document_unreadable",
    "formula_pua_unrecoverable",
    "audit_high_issues",
    "unextractable_asset",
    "source_unreadable",
    "ocr_unavailable",
    "missing_evidence",
}

ANALYTICAL_UNCERTAINTY_CATEGORIES = {
    "scientific_conflict",
    "literature_conflict",
    "analytical_note",
}

ANALYTICAL_CONFLICT_IDS = {
    "cross_document_unresolved_conflict",
    "unresolved_scientific_conflict",
}


def _is_blocking_high_uncertainty(u, conflicts=None):
    """Distinguish source/coverage blockers (force partial) from analytical scientific findings."""
    sev = str(u.get("severity") or "").strip().lower()
    if sev not in ("high", "critical"):
        return False

    cat = str(u.get("category") or "").strip().lower()
    uid = str(u.get("uncertainty_id") or u.get("id") or "").strip().lower()
    code = str(u.get("code") or "").strip().lower()
    nature = str(u.get("uncertainty_nature") or u.get("nature") or "").strip().lower()

    if cat in BLOCKER_UNCERTAINTY_CATEGORIES or uid in BLOCKER_UNCERTAINTY_CATEGORIES or code in BLOCKER_UNCERTAINTY_CATEGORIES:
        return True
    if nature == "source_or_coverage_blocker":
        return True
    if cat in ANALYTICAL_UNCERTAINTY_CATEGORIES or nature == "analytical_finding":
        return False
    if conflicts and (
        cat in ANALYTICAL_CONFLICT_IDS
        or uid in ANALYTICAL_CONFLICT_IDS
        or code in ANALYTICAL_CONFLICT_IDS
        or ("conflict" in cat and any(tok in cat for tok in ("scientific", "literature", "cross_document", "unresolved")))
    ):
        return False
    return True


def _determine_honest_status(work_state, audit_report, early_partial=False):
    """Enforce honest status downgrade whenever HIGH issues, unreadable inputs, or unverified QAs exist."""
    if early_partial:
        return "partial"

    nps = work_state.get("nanopublications", [])
    qa_list = work_state.get("qa_answers", [])
    unc_list = work_state.get("uncertainty", [])
    conflicts = work_state.get("conflicts", [])
    sev = (audit_report or {}).get("severity_counts") or {}
    high_cnt = int(sev.get("HIGH", 0))

    has_unreadable = any(
        u.get("category") == "scanned_document_unreadable" and str(u.get("severity", "")).lower() in ("high", "critical")
        for u in unc_list
    )
    has_high_uncertainty = any(
        _is_blocking_high_uncertainty(u, conflicts=conflicts)
        for u in unc_list
    )
    has_unanswered_or_unverified = any(
        q.get("answer_status") in ("not_reported", "unanswerable", "insufficient_evidence", "unverifiable")
        for q in qa_list
    )
    has_unaligned_quote = any(
        (n.get("provenance") or {}).get("alignment_status") == "QUOTE_NOT_FOUND"
        for n in nps
    )

    if not nps and not qa_list and has_unreadable:
        return "unanswerable"
    if high_cnt > 0 or has_unreadable or has_high_uncertainty or has_unanswered_or_unverified or has_unaligned_quote:
        return "partial"
    if not nps and not qa_list:
        return "partial"
    return "success"


def build_result_json_payload(work_state, audit_report=None, deliverables_map=None, early_partial=False):
    """Assemble full result.json payload preserving case_id and nested output_contract."""
    status = _determine_honest_status(work_state, audit_report, early_partial=early_partial)
    nps = list(work_state.get("nanopublications", []))
    qa_list = list(work_state.get("qa_answers", []))
    unc_list = list(work_state.get("uncertainty", []))
    mr_list = list(work_state.get("manual_review_checklist", []))
    changes = list(work_state.get("changes", []))
    conflicts = list(work_state.get("conflicts", []))
    docs_pub = list(work_state.get("documents_pubinfo", []))

    if audit_report and audit_report.get("severity_counts", {}).get("HIGH", 0) > 0:
        high_issues = [i for i in audit_report.get("issues", []) if i.get("severity") == "HIGH"]
        if not any(u.get("category") == "audit_high_issues" for u in unc_list):
            unc_list.append({
                "uncertainty_id": "UNC-AUDIT-HIGH",
                "category": "audit_high_issues",
                "severity": "high",
                "doc_id": None,
                "pages": [],
                "message": f"Downgraded status to '{status}' due to {len(high_issues)} HIGH-severity deterministic audit issue(s).",
            })
        existing_mr = {m.get("target_id") for m in mr_list if m.get("target_id")}
        for idx, hi in enumerate(high_issues, start=1):
            tid = hi.get("target_id")
            if tid not in existing_mr:
                mr_list.append({
                    "item_id": f"MR-AUD-{idx}",
                    "target_id": tid,
                    "severity": "high",
                    "category": hi.get("code"),
                    "reason": hi.get("message"),
                    "action_required": hi.get("fix_hint") or "Inspect source block and fix violation.",
                })

    audit_summary = {
        "audit_passed": (audit_report or {}).get("audit_passed", False) if not early_partial else False,
        "recommended_status": status,
        "total_nanopublications": len(nps),
        "grounded_nanopublications": (audit_report or {}).get("grounded_nanopublications", 0),
        "severity_counts": (audit_report or {}).get("severity_counts", {"HIGH": 0, "MEDIUM": 0, "LOW": 0}),
        "evidence_tier_distribution": (audit_report or {}).get("evidence_tier_distribution", {}),
    }

    delivs = deliverables_map or {
        "result_json": "result.json",
        "jsonld": "nanopubs.jsonld",
        "trig": "nanopubs.trig",
        "csv": "nanopubs.csv",
        "report_md": "report.md",
        "audit_report_json": "audit_report.json",
    }

    canonical = {
        "case_id": work_state.get("case_id", "nanopub_case"),
        "status": status,
        "schema_version": "nanopub-reading/1.0",
        "generated_at": _utc_now(),
        "documents_pubinfo": docs_pub,
        "nanopublications": nps,
        "qa_answers": qa_list,
        "conflicts": conflicts,
        "uncertainty": unc_list,
        "manual_review_checklist": mr_list,
        "audit_summary": audit_summary,
        "changes": changes,
        "deliverables": delivs,
    }

    contract = work_state.get("output_contract") or {}
    c_fields = extract_contract_fields(contract)
    for fname, fschema in c_fields.items():
        if fname in ("case_id", "status"):
            continue
        canonical[fname] = map_contract_field_value(fname, fschema, canonical)

    return canonical


def build_jsonld_bundle(work_state):
    """Render valid W3C Nanopublication JSON-LD 1.1 document with explicit 4-graph separation."""
    context = {
        "np": "http://www.nanopub.org/nschema#",
        "npv": "https://w3id.org/nanopub-reading/vocab/",
        "prov": "http://www.w3.org/ns/prov#",
        "pav": "http://purl.org/pav/",
        "dcterms": "http://purl.org/dc/terms/",
        "cito": "http://purl.org/spar/cito/",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    }
    graph_nodes = []
    for np_item in work_state.get("nanopublications", []):
        head = np_item.get("head") or {}
        asrt = np_item.get("assertion") or {}
        prov = np_item.get("provenance") or {}
        loc = prov.get("location") or {}
        pub = np_item.get("pubinfo") or {}
        uri = head.get("uri") or f"urn:nanopub:{head.get('nanopub_id', 'unknown')}"

        graph_nodes.append({
            "@id": uri,
            "@type": "np:Nanopublication",
            "np:hasAssertion": {"@id": head.get("assertion_graph", f"{uri}#assertion")},
            "np:hasProvenance": {"@id": head.get("provenance_graph", f"{uri}#provenance")},
            "np:hasPublicationInfo": {"@id": head.get("pubinfo_graph", f"{uri}#pubinfo")},
            "head": {
                "@id": head.get("head_graph", f"{uri}#Head"),
                "@type": "np:Head",
                "nanopub_id": head.get("nanopub_id"),
                "schema_version": head.get("schema_version", "nanopub-reading/1.0"),
            },
            "assertion": {
                "@id": head.get("assertion_graph", f"{uri}#assertion"),
                "@type": "np:Assertion",
                "rdfs:label": asrt.get("statement", ""),
                "npv:subject": asrt.get("subject"),
                "npv:predicate": (asrt.get("predicate") or {}).get("uri"),
                "npv:predicateId": (asrt.get("predicate") or {}).get("id"),
                "npv:object": asrt.get("object"),
                "npv:polarity": asrt.get("polarity"),
                "npv:qualifiers": asrt.get("qualifiers"),
            },
            "provenance": {
                "@id": head.get("provenance_graph", f"{uri}#provenance"),
                "@type": "np:Provenance",
                "prov:quotedText": prov.get("evidence_quote", ""),
                "npv:evidenceTier": prov.get("evidence_tier"),
                "npv:alignmentStatus": prov.get("alignment_status"),
                "npv:sourceAttribution": prov.get("source_attribution"),
                "npv:confidence": prov.get("confidence"),
                "npv:location": loc,
            },
            "pubinfo": {
                "@id": head.get("pubinfo_graph", f"{uri}#pubinfo"),
                "@type": "np:PublicationInfo",
                "dcterms:title": pub.get("title"),
                "dcterms:creator": pub.get("authors", []),
                "dcterms:issued": pub.get("year"),
                "dcterms:isPartOf": pub.get("venue"),
                "npv:doi": pub.get("doi"),
                "npv:arxivId": pub.get("arxiv_id"),
                "npv:pmid": pub.get("pmid"),
                "rdfs:seeAlso": pub.get("url"),
                "pav:createdBy": pub.get("extracted_by", "task_09_nanopub/pubinfo_extractor.py"),
            },
        })

    return {
        "@context": context,
        "@id": f"urn:nanopub:case:{work_state.get('case_id', 'unknown')}",
        "@graph": graph_nodes,
    }


def _safe_iri(raw_uri):
    """Sanitize URI string so it is valid inside TriG <...> angle brackets."""
    s = str(raw_uri or "urn:nanopub:unknown").strip()
    s = re.sub(r"[\s<>\"{}|\\^`]", "_", s)
    return s


def _trig_str(val):
    """Escape a Python value into a valid double-quoted Turtle/TriG string literal."""
    s = str(val if val is not None else "")
    # Remove ASCII control characters except \t, \n, \r
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", s)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n").replace("\t", "\\t")
    return f'"{s}"'


def build_trig_text(work_state):
    """Render valid W3C TriG RDF document with 4 named graph blocks per Nanopublication."""
    lines = [
        "@prefix np: <http://www.nanopub.org/nschema#> .",
        "@prefix npv: <https://w3id.org/nanopub-reading/vocab/> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix pav: <http://purl.org/pav/> .",
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix cito: <http://purl.org/spar/cito/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "",
    ]

    for np_item in work_state.get("nanopublications", []):
        head = np_item.get("head") or {}
        asrt = np_item.get("assertion") or {}
        prov = np_item.get("provenance") or {}
        loc = prov.get("location") or {}
        pub = np_item.get("pubinfo") or {}

        np_uri = _safe_iri(head.get("uri") or f"urn:nanopub:{head.get('nanopub_id', 'unknown')}")
        h_iri = _safe_iri(head.get("head_graph") or f"{np_uri}#Head")
        a_iri = _safe_iri(head.get("assertion_graph") or f"{np_uri}#assertion")
        p_iri = _safe_iri(head.get("provenance_graph") or f"{np_uri}#provenance")
        i_iri = _safe_iri(head.get("pubinfo_graph") or f"{np_uri}#pubinfo")

        pred_id = (asrt.get("predicate") or {}).get("id") or "associated_with"
        pred_curie = f"npv:{re.sub(r'[^A-Za-z0-9_]', '_', pred_id)}"
        subj_iri = f"{a_iri}_subj"
        obj_iri = f"{a_iri}_obj"

        # 1. Head Graph
        lines.extend([
            f"<{h_iri}> {{",
            f"  <{np_uri}> a np:Nanopublication ;",
            f"    np:hasAssertion <{a_iri}> ;",
            f"    np:hasProvenance <{p_iri}> ;",
            f"    np:hasPublicationInfo <{i_iri}> ;",
            f"    dcterms:identifier {_trig_str(head.get('nanopub_id', ''))} .",
            "}",
            "",
        ])

        # 2. Assertion Graph
        subj_lbl = (asrt.get("subject") or {}).get("label", "")
        obj_lbl = (asrt.get("object") or {}).get("label", "")
        qual_str = json.dumps(asrt.get("qualifiers") or {}, ensure_ascii=False)
        lines.extend([
            f"<{a_iri}> {{",
            f"  <{subj_iri}> a npv:Entity ;",
            f"    rdfs:label {_trig_str(subj_lbl)} ;",
            f"    {pred_curie} <{obj_iri}> .",
            f"  <{obj_iri}> a npv:Entity ;",
            f"    rdfs:label {_trig_str(obj_lbl)} .",
            f"  <{a_iri}> rdfs:comment {_trig_str(asrt.get('statement', ''))} ;",
            f"    npv:polarity {_trig_str(asrt.get('polarity', 'affirmed'))} ;",
            f"    npv:qualifiersJson {_trig_str(qual_str)} .",
            "}",
            "",
        ])

        # 3. Provenance Graph
        page_num = loc.get("page")
        page_lit = f'"{int(page_num)}"^^xsd:integer' if isinstance(page_num, int) else _trig_str(str(page_num or ""))
        conf_val = prov.get("confidence", 0.0)
        lines.extend([
            f"<{p_iri}> {{",
            f"  <{a_iri}> prov:quotedText {_trig_str(prov.get('evidence_quote', ''))} ;",
            f"    npv:docId {_trig_str(loc.get('doc_id', ''))} ;",
            f"    npv:page {page_lit} ;",
            f"    npv:blockId {_trig_str(loc.get('block_id', ''))} ;",
            f"    npv:sectionRole {_trig_str(loc.get('section_role', ''))} ;",
            f"    npv:evidenceTier {_trig_str(prov.get('evidence_tier', ''))} ;",
            f"    npv:alignmentStatus {_trig_str(prov.get('alignment_status', ''))} ;",
            f"    npv:sourceAttribution {_trig_str(prov.get('source_attribution', ''))} ;",
            f'    npv:confidence "{float(conf_val):.3f}"^^xsd:decimal .',
            "}",
            "",
        ])

        # 4. PubInfo Graph
        pub_triples = [
            f"  <{np_uri}> pav:createdBy {_trig_str(pub.get('extracted_by', 'task_09_nanopub/pubinfo_extractor.py'))}",
        ]
        if pub.get("title"):
            pub_triples.append(f"    dcterms:title {_trig_str(pub['title'])}")
        if pub.get("year") is not None:
            pub_triples.append(f'    dcterms:issued "{int(pub["year"])}"^^xsd:gYear')
        if pub.get("venue"):
            pub_triples.append(f"    dcterms:isPartOf {_trig_str(pub['venue'])}")
        if pub.get("doi"):
            pub_triples.append(f"    npv:doi {_trig_str(pub['doi'])}")
        if pub.get("arxiv_id"):
            pub_triples.append(f"    npv:arxivId {_trig_str(pub['arxiv_id'])}")
        if pub.get("url"):
            pub_triples.append(f"    rdfs:seeAlso <{_safe_iri(pub['url'])}>")
        for auth in (pub.get("authors") or []):
            pub_triples.append(f"    dcterms:creator {_trig_str(auth)}")

        lines.append(f"<{i_iri}> {{")
        for idx_t, t_line in enumerate(pub_triples):
            sep = " ." if idx_t == len(pub_triples) - 1 else " ;"
            lines.append(f"{t_line}{sep}")
        lines.extend(["}", ""])

    return "\n".join(lines)


def write_csv_export(work_state, csv_path):
    """Write flat UTF-8 CSV table of all Nanopublications."""
    fieldnames = [
        "nanopub_id", "doc_id", "page", "block_id", "section_role",
        "subject", "predicate_id", "object", "polarity", "qualifiers",
        "evidence_tier", "alignment_status", "confidence", "source_attribution",
        "statement", "evidence_quote", "title", "authors", "year", "doi", "arxiv_id",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for np_item in work_state.get("nanopublications", []):
            head = np_item.get("head") or {}
            asrt = np_item.get("assertion") or {}
            prov = np_item.get("provenance") or {}
            loc = prov.get("location") or {}
            pub = np_item.get("pubinfo") or {}
            writer.writerow({
                "nanopub_id": head.get("nanopub_id", ""),
                "doc_id": loc.get("doc_id", ""),
                "page": loc.get("page", ""),
                "block_id": loc.get("block_id", ""),
                "section_role": loc.get("section_role", ""),
                "subject": (asrt.get("subject") or {}).get("label", ""),
                "predicate_id": (asrt.get("predicate") or {}).get("id", ""),
                "object": (asrt.get("object") or {}).get("label", ""),
                "polarity": asrt.get("polarity", ""),
                "qualifiers": json.dumps(asrt.get("qualifiers") or {}, ensure_ascii=False),
                "evidence_tier": prov.get("evidence_tier", ""),
                "alignment_status": prov.get("alignment_status", ""),
                "confidence": prov.get("confidence", ""),
                "source_attribution": prov.get("source_attribution", ""),
                "statement": asrt.get("statement", ""),
                "evidence_quote": prov.get("evidence_quote", ""),
                "title": pub.get("title") or "",
                "authors": "; ".join(pub.get("authors") or []),
                "year": pub.get("year") if pub.get("year") is not None else "",
                "doi": pub.get("doi") or "",
                "arxiv_id": pub.get("arxiv_id") or "",
            })


def build_report_markdown(result_payload, audit_report):
    """Render structured scientific reading & verification report in Markdown."""
    case_id = result_payload.get("case_id", "unknown")
    status = result_payload.get("status", "partial")
    nps = result_payload.get("nanopublications", [])
    qas = result_payload.get("qa_answers", [])
    pubs = result_payload.get("documents_pubinfo", [])
    confs = result_payload.get("conflicts", [])
    uncs = result_payload.get("uncertainty", [])
    mrs = result_payload.get("manual_review_checklist", [])
    aud = audit_report or {}

    lines = [
        f"# Nanopublication 科研阅读与证据溯源报告 (`{case_id}`)",
        "",
        f"- **运行状态 (`status`)**: `{status}`",
        f"- **原子断言总数**: {len(nps)} | **问答条数**: {len(qas)} | **冲突数**: {len(confs)}",
        f"- **审计结果**: `audit_passed={aud.get('audit_passed', False)}` (`HIGH={aud.get('severity_counts', {}).get('HIGH', 0)}`, `MEDIUM={aud.get('severity_counts', {}).get('MEDIUM', 0)}`, `LOW={aud.get('severity_counts', {}).get('LOW', 0)}`)",
        "",
        "## 1. 科研问题回答总览（Grounded QA）",
        "",
    ]

    if not qas:
        lines.append("_输入未提供显式科研问题或尚未生成问答条目。_")
    else:
        for q in qas:
            np_refs = ", ".join(f"`{x}`" for x in (q.get("supporting_nanopub_ids") or [])) or "无"
            locs = ", ".join(
                f"[{el.get('doc_id', 'D1')}:{el.get('block_id', '?')} p.{el.get('page', '?')}]"
                for el in (q.get("evidence_locations") or [])
            ) or "无有效定位"
            lines.extend([
                f"### {q.get('question_id', 'Q?')}: {q.get('question', '')}",
                f"- **状态**: `{q.get('answer_status', 'unknown')}`",
                f"- **回答**: {q.get('answer', '')}",
                f"- **支撑断言**: {np_refs}",
                f"- **原文定位**: {locs}",
            ])
            if q.get("uncertainty_note"):
                lines.append(f"- **边界说明**: {q['uncertainty_note']}")
            lines.append("")

    lines.extend(["## 2. 原子断言与逐字证据清单（Nanopublications）", ""])
    if not nps:
        lines.append("_无有效提取的原子断言（请检查第 6 节不确定性与人工复核清单）。_")
    else:
        for n in nps:
            head = n.get("head") or {}
            asrt = n.get("assertion") or {}
            prov = n.get("provenance") or {}
            loc = prov.get("location") or {}
            pred = asrt.get("predicate") or {}
            lines.extend([
                f"### [{head.get('nanopub_id')}] {asrt.get('statement', '')}",
                f"- **SPO**: `({(asrt.get('subject') or {}).get('label', '')})` —[`{pred.get('id', '')}` / `{asrt.get('polarity', '')}`]→ `({(asrt.get('object') or {}).get('label', '')})`",
                f"- **定位与等级**: `[{loc.get('doc_id', 'D1')}:{loc.get('block_id', '?')}]` (Page {loc.get('page', '?')}, role=`{loc.get('section_role', '?')}`) | Tier=`{prov.get('evidence_tier')}` | Align=`{prov.get('alignment_status')}` | Conf=`{prov.get('confidence')}` | Attr=`{prov.get('source_attribution')}`",
                f"- **逐字引文**: \"{prov.get('evidence_quote', '')}\"",
                "",
            ])

    lines.extend(["## 3. 来源元数据溯源表（PubInfo）", ""])
    if pubs:
        lines.append("| doc_id | Title | Authors | Year | Venue | DOI / arXiv | Missing Fields |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in pubs:
            auths = ", ".join((p.get("authors") or [])[:4]) + (" et al." if len(p.get("authors") or []) > 4 else "")
            ident = p.get("doi") or p.get("arxiv_id") or p.get("pmid") or "null"
            miss = ", ".join(p.get("missing_fields") or []) or "none"
            lines.append(
                f"| {p.get('doc_id', 'D1')} | {p.get('title') or 'null'} | {auths or '[]'} | "
                f"{p.get('year') or 'null'} | {p.get('venue') or 'null'} | {ident} | {miss} |"
            )
    else:
        lines.append("_未提供来源元数据。_")

    lines.extend(["", "## 4. 冲突检测与去重合并记录", ""])
    if confs:
        for c in confs:
            lines.append(f"- **{c.get('conflict_id')}** (`{c.get('conflict_type')}`): {c.get('description')} — `np_ids={c.get('np_ids')}`")
    else:
        lines.append("- 未检测到极性或定量冲突。")

    lines.extend(["", "## 5. 8 维质量审计摘要", ""])
    aud_issues = aud.get("issues", [])
    if aud_issues:
        for iss in aud_issues[:30]:
            lines.append(f"- `[{iss.get('severity')}]` **{iss.get('code')}** ({iss.get('target_id')}): {iss.get('message')}")
    else:
        lines.append("- 全部 8 维确定性审计项通过，0 告警。")

    lines.extend(["", "## 6. 不确定性与人工接管复核清单", ""])
    if uncs:
        for u in uncs:
            lines.append(f"- **[{u.get('severity', 'medium').upper()}]** `{u.get('category')}`: {u.get('message')}")
    if mrs:
        for m in mrs:
            lines.append(f"- **复核项 {m.get('item_id', '')}** (`{m.get('category', '')}`): {m.get('reason', '')} → _{m.get('action_required', '')}_")
    if not uncs and not mrs:
        lines.append("- 无未决不确定性或人工接管项。")

    lines.extend([
        "",
        "## 7. 交付物清单与复核顺序（Deliverables & 30-Second Review Guide）",
        "",
        "| 文件 | 用途 | 推荐复核顺序 |",
        "|---|---|---|",
        "| `audit_report.json` | 8 维确定性审计明细与 `HIGH/MEDIUM/LOW` 统计 | **第 1 步**：先确认 `audit_passed` 与问题码分布 |",
        "| `result.json` | 保留完整 `output_contract`、`qa_answers`、`conflicts` 与 `uncertainty` 的主结构化交付 | **第 2 步**：核对 `status`、`qa_answers` 依据链与冲突条目 |",
        "| `report.md` | 人类可读科研问答、逐字引文定位与人工复核清单 | **第 3 步**：抽查 `[P{page}-B{seq}]` 与高风险人工复核项 |",
        "| `nanopubs.csv` | 扁平化表格导出（含 SPO、分级、引文与 PubInfo） | **第 4 步**：快速筛选低分级或缺失字段条目 |",
        "| `nanopubs.jsonld` | W3C Nanopublication JSON-LD 1.1 四图导出 | 下游程序直接解析入库 |",
        "| `nanopubs.trig` | W3C TriG RDF 命名图（`#Head`/`#assertion`/`#provenance`/`#pubinfo`） | RDF 三元组库直接加载验证 |",
        "",
    ])
    return "\n".join(lines)


def render_all_deliverables(work_state, audit_report, output_dir):
    """Render result.json, nanopubs.jsonld, nanopubs.trig, nanopubs.csv, report.md, and audit_report.json."""
    out_abs = os.path.abspath(output_dir)
    os.makedirs(out_abs, exist_ok=True)

    result_payload = build_result_json_payload(work_state, audit_report=audit_report, early_partial=False)
    jsonld_doc = build_jsonld_bundle(work_state)
    trig_text = build_trig_text(work_state)
    report_md = build_report_markdown(result_payload, audit_report)

    with open(os.path.join(out_abs, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result_payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_abs, "nanopubs.jsonld"), "w", encoding="utf-8") as f:
        json.dump(jsonld_doc, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_abs, "nanopubs.trig"), "w", encoding="utf-8") as f:
        f.write(trig_text)
    write_csv_export(work_state, os.path.join(out_abs, "nanopubs.csv"))
    with open(os.path.join(out_abs, "report.md"), "w", encoding="utf-8") as f:
        f.write(report_md)
    if audit_report is not None:
        with open(os.path.join(out_abs, "audit_report.json"), "w", encoding="utf-8") as f:
            json.dump(audit_report, f, ensure_ascii=False, indent=2)

    print(
        f"[render_deliverables] case_id={result_payload['case_id']} | status={result_payload['status']} "
        f"| nps={len(result_payload['nanopublications'])} -> {out_abs}"
    )
    return result_payload


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Multi-Format Nanopublication Deliverables Renderer")
    parser.add_argument("--work-state", required=True, help="Path to finalized work_state.json")
    parser.add_argument("--audit-report", default=None, help="Path to audit_report.json")
    parser.add_argument("--output-dir", required=True, help="Output directory for deliverables")
    args = parser.parse_args()

    ws = load_json_safe(args.work_state)
    if not ws:
        raise FileNotFoundError(f"work_state.json not found: {args.work_state}")
    aud = load_json_safe(args.audit_report) if args.audit_report else None
    if aud is None:
        cand_aud = os.path.join(os.path.dirname(os.path.abspath(args.work_state)), "audit_report.json")
        aud = load_json_safe(cand_aud)

    render_all_deliverables(ws, aud, args.output_dir)


if __name__ == "__main__":
    main()
