#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
note_assembler.py - Incremental Patch Assembly & Deterministic Provenance Backfill Engine
Part of the Scientific Literature Notes Skill (task_06_notes).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import json
import re
import glob
import argparse
from datetime import datetime, timezone

PLACEHOLDER_STRINGS = {
    "", "tbd", "todo", "n/a", "na", "unknown", "none", "null", "nil",
    "placeholder", "未报告", "暂无", "未知", "待定", "待补充", "无", "-", "--",
}

VALID_PROVENANCE_STATUSES = {"reported", "partially_reported", "not_reported_in_source"}

VALID_KP_CATEGORIES = {
    "concept_definition",
    "method_mechanism",
    "formula_algorithm",
    "empirical_finding",
    "dataset_metric",
    "limitation_risk",
    "future_direction",
}

KP_CATEGORY_ALIASES = {
    "concept": "concept_definition",
    "definition": "concept_definition",
    "method": "method_mechanism",
    "mechanism": "method_mechanism",
    "algorithm": "formula_algorithm",
    "formula": "formula_algorithm",
    "equation": "formula_algorithm",
    "finding": "empirical_finding",
    "result": "empirical_finding",
    "empirical": "empirical_finding",
    "dataset": "dataset_metric",
    "metric": "dataset_metric",
    "limitation": "limitation_risk",
    "risk": "limitation_risk",
    "assumption": "limitation_risk",
    "future": "future_direction",
}

PAPER_CARD_ANALYTICAL_FIELDS = (
    "one_line_takeaway",
    "research_problem",
    "research_gap",
    "methodology_summary",
    "limitations_and_assumptions",
    "reusability_and_takeaways",
)

PAPER_CARD_META_FIELDS = (
    "title",
    "authors",
    "year",
    "venue_or_source",
    "doi",
    "arxiv_id",
    "code_urls",
)


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json_tolerant(path):
    """Load JSON file, tolerating accidental markdown code fences."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw).strip()
    return json.loads(raw)


def _is_empty_or_placeholder(val):
    if val is None:
        return True
    if isinstance(val, str):
        s = val.strip()
        return s.lower() in PLACEHOLDER_STRINGS
    if isinstance(val, (list, dict)):
        return len(val) == 0
    return False


def _dedup_list(seq):
    seen = set()
    out = []
    for item in seq or []:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _resolve_anchors(anchor_ids, anchor_map):
    """Deterministically backfill source provenance records from evidence_index."""
    resolved = []
    for aid in _dedup_list(anchor_ids):
        anc = anchor_map.get(aid)
        if anc:
            txt = anc.get("text", "")
            excerpt = txt[:240] + ("..." if len(txt) > 240 else "")
            resolved.append({
                "anchor_id": aid,
                "source_file": anc.get("source_file"),
                "page": anc.get("page"),
                "section_id": anc.get("section_id"),
                "section_title": anc.get("section_title"),
                "block_type": anc.get("block_type"),
                "source_excerpt": excerpt,
                "valid": True,
            })
        else:
            resolved.append({
                "anchor_id": aid,
                "source_file": None,
                "page": None,
                "section_id": None,
                "section_title": None,
                "block_type": None,
                "source_excerpt": "",
                "valid": False,
            })
    return resolved


def _normalize_card_field(raw_val, anchor_map, allow_list=False):
    """Normalize a paper_card field into {value, provenance_status, anchor_ids, resolved_provenance}."""
    if isinstance(raw_val, dict) and ("value" in raw_val or "provenance_status" in raw_val):
        val = raw_val.get("value")
        p_status = raw_val.get("provenance_status", "")
        a_ids = _dedup_list(raw_val.get("anchor_ids", []))
    else:
        val = raw_val
        p_status = ""
        a_ids = []

    if _is_empty_or_placeholder(val):
        norm_val = [] if allow_list else "not_reported_in_source"
        p_status = "not_reported_in_source"
    else:
        if isinstance(val, str):
            norm_val = val.strip()
            if norm_val == "not_reported_in_source":
                p_status = "not_reported_in_source"
        elif isinstance(val, list):
            cleaned = [str(x).strip() for x in val if not _is_empty_or_placeholder(x)]
            if not cleaned:
                norm_val = [] if allow_list else "not_reported_in_source"
                p_status = "not_reported_in_source"
            else:
                norm_val = cleaned if allow_list else "; ".join(cleaned)
        else:
            norm_val = val

        if p_status not in VALID_PROVENANCE_STATUSES:
            p_status = "reported"

    return {
        "value": norm_val,
        "provenance_status": p_status,
        "anchor_ids": a_ids,
        "resolved_provenance": _resolve_anchors(a_ids, anchor_map),
    }


def _seed_base_notes(evidence_index, paper_signals, draft_skeleton):
    """Create initial assembled_notes structure seeded from deterministic indexing artifacts."""
    case_id = evidence_index.get("case_id", "literature_notes_case")
    multi_doc = bool(evidence_index.get("multi_document", False))
    docs = paper_signals.get("documents", [])
    primary_meta = docs[0].get("meta_clues", {}) if docs else {}

    title_val = primary_meta.get("candidate_title")
    if title_val == "Untitled":
        title_val = None
    authors_val = primary_meta.get("authors_cleaned") or [c.get("text", "") for c in primary_meta.get("author_clues", []) if c.get("text")]
    author_anchors = [c.get("anchor_id") for c in primary_meta.get("author_clues", []) if c.get("anchor_id")]
    year_val = primary_meta.get("years", [None])[0] if primary_meta.get("years") else None
    doi_val = primary_meta.get("dois", [None])[0] if primary_meta.get("dois") else None
    arxiv_val = primary_meta.get("arxiv_ids", [None])[0] if primary_meta.get("arxiv_ids") else None
    code_urls = primary_meta.get("code_urls", [])

    paper_card = {
        "title": {"value": title_val, "provenance_status": "reported" if title_val else "not_reported_in_source", "anchor_ids": []},
        "authors": {"value": authors_val, "provenance_status": "reported" if authors_val else "not_reported_in_source", "anchor_ids": author_anchors},
        "year": {"value": year_val, "provenance_status": "reported" if year_val else "not_reported_in_source", "anchor_ids": []},
        "venue_or_source": {"value": None, "provenance_status": "not_reported_in_source", "anchor_ids": []},
        "doi": {"value": doi_val, "provenance_status": "reported" if doi_val else "not_reported_in_source", "anchor_ids": []},
        "arxiv_id": {"value": arxiv_val, "provenance_status": "reported" if arxiv_val else "not_reported_in_source", "anchor_ids": []},
        "code_urls": {"value": code_urls, "provenance_status": "reported" if code_urls else "not_reported_in_source", "anchor_ids": []},
        "one_line_takeaway": {"value": None, "provenance_status": "not_reported_in_source", "anchor_ids": []},
        "research_problem": {"value": None, "provenance_status": "not_reported_in_source", "anchor_ids": []},
        "research_gap": {"value": None, "provenance_status": "not_reported_in_source", "anchor_ids": []},
        "methodology_summary": {"value": None, "provenance_status": "not_reported_in_source", "anchor_ids": []},
        "key_results": [],
        "limitations_and_assumptions": {"value": None, "provenance_status": "not_reported_in_source", "anchor_ids": []},
        "reusability_and_takeaways": {"value": None, "provenance_status": "not_reported_in_source", "anchor_ids": []},
        "tags": [],
    }

    section_summaries = []
    for sec in evidence_index.get("sections", []):
        p_span = sec.get("page_span", [1, 1])
        zone = sec.get("zone", "body")
        section_summaries.append({
            "section_id": sec["section_id"],
            "doc_id": sec.get("doc_id", "D1"),
            "section_title": sec.get("title", ""),
            "section_role": sec.get("canonical_role", "body"),
            "zone": zone,
            "page_range": p_span,
            "summary": "",
            "summary_status": "locked_references" if zone == "references_locked" else "pending",
            "key_points": [],
        })

    uncertainties = list(paper_signals.get("initial_uncertainty", []))
    checklist = []
    for u in uncertainties:
        sev = u.get("severity", "medium")
        if sev in ("high", "medium"):
            checklist.append({
                "item_id": f"CHK-{len(checklist) + 1:03d}",
                "category": u.get("category", "source_quality"),
                "pages": u.get("pages", []),
                "description": u.get("message", ""),
                "status": "requires_human_review",
            })

    return {
        "case_id": case_id,
        "schema_version": "literature-notes/v1",
        "multi_document": multi_doc,
        "doc_count": evidence_index.get("doc_count", 1),
        "paper_card": paper_card,
        "section_summaries": section_summaries,
        "knowledge_points": [],
        "cross_document_synthesis": {"comparison_table": [], "synthesis_notes": []} if multi_doc else [],
        "uncertainties": uncertainties,
        "manual_review_checklist": checklist,
        "change_log": [
            {
                "timestamp": _utc_now(),
                "action": "seed_from_index",
                "source": "evidence_index.json + paper_signals.json",
                "details": f"Initialized {len(section_summaries)} sections and {len(uncertainties)} initial uncertainties.",
            }
        ],
    }


def _apply_patch(notes, patch, patch_name):
    """Incrementally merge one JSON patch into assembled_notes."""
    changes = []

    # 1. paper_card updates
    pc_patch = patch.get("paper_card")
    if isinstance(pc_patch, dict):
        pc = notes["paper_card"]
        for k in PAPER_CARD_META_FIELDS + PAPER_CARD_ANALYTICAL_FIELDS:
            if k in pc_patch:
                v = pc_patch[k]
                if not _is_empty_or_placeholder(v):
                    pc[k] = v
                    changes.append(f"paper_card.{k}")
                elif isinstance(v, dict) and v.get("provenance_status") == "not_reported_in_source":
                    pc[k] = v
                    changes.append(f"paper_card.{k}=not_reported")

        if "key_results" in pc_patch and isinstance(pc_patch["key_results"], list):
            existing_by_id = {
                kr.get("result_id"): idx
                for idx, kr in enumerate(pc.get("key_results", []))
                if isinstance(kr, dict) and kr.get("result_id")
            }
            for item in pc_patch["key_results"]:
                if not isinstance(item, dict):
                    continue
                rid = item.get("result_id")
                if rid and rid in existing_by_id:
                    pc["key_results"][existing_by_id[rid]] = item
                else:
                    if not rid:
                        item = dict(item)
                        item["result_id"] = f"RES-{len(pc['key_results']) + 1:03d}"
                    pc["key_results"].append(item)
                    existing_by_id[item["result_id"]] = len(pc["key_results"]) - 1
            changes.append(f"paper_card.key_results({len(pc_patch['key_results'])})")

        if "tags" in pc_patch and isinstance(pc_patch["tags"], list):
            pc["tags"] = _dedup_list(list(pc.get("tags", [])) + pc_patch["tags"])
            changes.append("paper_card.tags")

    # 2. section_summaries updates (by section_id)
    sec_patch = patch.get("section_summaries")
    if isinstance(sec_patch, list):
        sec_idx_map = {s["section_id"]: i for i, s in enumerate(notes["section_summaries"])}
        updated_secs = []
        for sp in sec_patch:
            if not isinstance(sp, dict) or not sp.get("section_id"):
                continue
            sid = sp["section_id"]
            if sid in sec_idx_map:
                target = notes["section_summaries"][sec_idx_map[sid]]
                if "summary" in sp and not _is_empty_or_placeholder(sp["summary"]):
                    target["summary"] = str(sp["summary"]).strip()
                    target["summary_status"] = "summarized"
                if "key_points" in sp and isinstance(sp["key_points"], list):
                    target["key_points"] = sp["key_points"]
                    if target["summary_status"] == "pending" and sp["key_points"]:
                        target["summary_status"] = "summarized"
                if "section_title" in sp and not _is_empty_or_placeholder(sp["section_title"]):
                    target["section_title"] = str(sp["section_title"]).strip()
                updated_secs.append(sid)
            else:
                notes["section_summaries"].append(sp)
                sec_idx_map[sid] = len(notes["section_summaries"]) - 1
                updated_secs.append(sid)
        if updated_secs:
            changes.append(f"sections({','.join(updated_secs[:8])})")

    # 3. knowledge_points updates (upsert by kp_id)
    kp_patch = patch.get("knowledge_points")
    if isinstance(kp_patch, list):
        kp_map = {k.get("kp_id"): i for i, k in enumerate(notes["knowledge_points"]) if k.get("kp_id")}
        upserted = 0
        for kp in kp_patch:
            if not isinstance(kp, dict):
                continue
            kp_copy = dict(kp)
            kid = kp_copy.get("kp_id")
            if not kid:
                kid = f"KP-{len(notes['knowledge_points']) + 1:03d}"
                kp_copy["kp_id"] = kid
            if kid in kp_map:
                notes["knowledge_points"][kp_map[kid]] = kp_copy
            else:
                notes["knowledge_points"].append(kp_copy)
                kp_map[kid] = len(notes["knowledge_points"]) - 1
            upserted += 1
        if upserted:
            changes.append(f"knowledge_points({upserted})")

    # 4. cross_document_synthesis
    cds_patch = patch.get("cross_document_synthesis")
    if cds_patch is not None:
        if isinstance(cds_patch, dict):
            if not isinstance(notes.get("cross_document_synthesis"), dict):
                notes["cross_document_synthesis"] = {"comparison_table": [], "synthesis_notes": []}
            if isinstance(cds_patch.get("comparison_table"), list):
                notes["cross_document_synthesis"]["comparison_table"].extend(cds_patch["comparison_table"])
            if isinstance(cds_patch.get("synthesis_notes"), list):
                notes["cross_document_synthesis"]["synthesis_notes"].extend(cds_patch["synthesis_notes"])
            changes.append("cross_document_synthesis")
        elif isinstance(cds_patch, list) and cds_patch:
            notes["cross_document_synthesis"] = cds_patch
            changes.append("cross_document_synthesis")

    # 5. uncertainties & manual_review_checklist
    if isinstance(patch.get("uncertainties"), list):
        existing_msgs = {u.get("message") for u in notes["uncertainties"] if isinstance(u, dict)}
        for u in patch["uncertainties"]:
            if isinstance(u, dict) and u.get("message") not in existing_msgs:
                notes["uncertainties"].append(u)
                existing_msgs.add(u.get("message"))
        changes.append(f"uncertainties(+{len(patch['uncertainties'])})")

    if isinstance(patch.get("manual_review_checklist"), list):
        existing_desc = {c.get("description") for c in notes["manual_review_checklist"] if isinstance(c, dict)}
        for c in patch["manual_review_checklist"]:
            if isinstance(c, dict) and c.get("description") not in existing_desc:
                notes["manual_review_checklist"].append(c)
                existing_desc.add(c.get("description"))
        changes.append("manual_review_checklist")

    notes["change_log"].append({
        "timestamp": _utc_now(),
        "action": "apply_patch",
        "source": patch_name,
        "updated_fields": changes,
    })


def _normalize_and_backfill(notes, anchor_map):
    """Normalize placeholders to not_reported_in_source and backfill all resolved_provenance."""
    pc = notes["paper_card"]

    for f_name in PAPER_CARD_META_FIELDS:
        allow_l = f_name in ("authors", "code_urls")
        pc[f_name] = _normalize_card_field(pc.get(f_name), anchor_map, allow_list=allow_l)

    for f_name in PAPER_CARD_ANALYTICAL_FIELDS:
        pc[f_name] = _normalize_card_field(pc.get(f_name), anchor_map, allow_list=False)

    norm_results = []
    for idx, kr in enumerate(pc.get("key_results", []), start=1):
        if not isinstance(kr, dict):
            continue
        item = dict(kr)
        item["result_id"] = item.get("result_id") or f"RES-{idx:03d}"
        for sub_k in ("metric", "value", "baseline", "condition", "summary"):
            if _is_empty_or_placeholder(item.get(sub_k)):
                item[sub_k] = "not_reported_in_source"
        a_ids = _dedup_list(item.get("anchor_ids", []))
        item["anchor_ids"] = a_ids
        item["resolved_provenance"] = _resolve_anchors(a_ids, anchor_map)
        if item.get("provenance_status") not in VALID_PROVENANCE_STATUSES:
            item["provenance_status"] = "reported" if item.get("summary") != "not_reported_in_source" else "not_reported_in_source"
        norm_results.append(item)
    pc["key_results"] = norm_results
    pc["tags"] = _dedup_list(pc.get("tags", []))

    # Normalize section_summaries
    for sec in notes.get("section_summaries", []):
        if _is_empty_or_placeholder(sec.get("summary")):
            sec["summary"] = ""
            if sec.get("zone") == "references_locked":
                sec["summary_status"] = "locked_references"
            elif not sec.get("key_points"):
                sec["summary_status"] = "pending"
        else:
            sec["summary"] = str(sec["summary"]).strip()
            sec["summary_status"] = "summarized"

        norm_kps = []
        for kp in sec.get("key_points", []):
            if isinstance(kp, str):
                kp = {"text": kp, "anchor_ids": []}
            if not isinstance(kp, dict) or _is_empty_or_placeholder(kp.get("text")):
                continue
            a_ids = _dedup_list(kp.get("anchor_ids", []))
            norm_kps.append({
                "text": str(kp["text"]).strip(),
                "anchor_ids": a_ids,
                "verbatim_quote": str(kp.get("verbatim_quote") or "").strip(),
                "resolved_provenance": _resolve_anchors(a_ids, anchor_map),
            })
        sec["key_points"] = norm_kps

    # Normalize knowledge_points
    norm_kps_all = []
    for idx, kp in enumerate(notes.get("knowledge_points", []), start=1):
        if not isinstance(kp, dict):
            continue
        raw_cat = str(kp.get("category") or "concept_definition").strip().lower()
        cat = raw_cat if raw_cat in VALID_KP_CATEGORIES else KP_CATEGORY_ALIASES.get(raw_cat, "concept_definition")
        a_ids = _dedup_list(kp.get("anchor_ids", []))
        detail = kp.get("detail")
        if _is_empty_or_placeholder(detail):
            detail = "not_reported_in_source"
        norm_kps_all.append({
            "kp_id": kp.get("kp_id") or f"KP-{idx:03d}",
            "category": cat,
            "title": str(kp.get("title") or "").strip() or f"Point {idx}",
            "claim": str(kp.get("claim") or "").strip(),
            "detail": str(detail).strip() if isinstance(detail, str) else detail,
            "anchor_ids": a_ids,
            "verbatim_quote": str(kp.get("verbatim_quote") or "").strip(),
            "tags": _dedup_list(kp.get("tags", [])),
            "resolved_provenance": _resolve_anchors(a_ids, anchor_map),
        })
    notes["knowledge_points"] = norm_kps_all

    # Normalize cross_document_synthesis anchors if dict
    cds = notes.get("cross_document_synthesis")
    if isinstance(cds, dict):
        for row in cds.get("comparison_table", []):
            if isinstance(row, dict) and "anchor_ids" in row:
                row["anchor_ids"] = _dedup_list(row.get("anchor_ids", []))
                row["resolved_provenance"] = _resolve_anchors(row["anchor_ids"], anchor_map)
        for sn in cds.get("synthesis_notes", []):
            if isinstance(sn, dict) and "anchor_ids" in sn:
                sn["anchor_ids"] = _dedup_list(sn.get("anchor_ids", []))
                sn["resolved_provenance"] = _resolve_anchors(sn["anchor_ids"], anchor_map)


def assemble_notes(work_dir, patches_dir=None, patch_files=None, reset=False):
    """Assemble incremental note patches with deterministic anchor provenance backfill."""
    work_abs = os.path.abspath(work_dir)
    ev_path = os.path.join(work_abs, "evidence_index.json")
    sig_path = os.path.join(work_abs, "paper_signals.json")
    draft_path = os.path.join(work_abs, "draft_skeleton.json")
    out_path = os.path.join(work_abs, "assembled_notes.json")

    if not os.path.exists(ev_path):
        raise FileNotFoundError(f"evidence_index.json not found in {work_abs}")

    with open(ev_path, "r", encoding="utf-8") as f:
        evidence_index = json.load(f)
    paper_signals = {}
    if os.path.exists(sig_path):
        with open(sig_path, "r", encoding="utf-8") as f:
            paper_signals = json.load(f)
    draft_skeleton = {}
    if os.path.exists(draft_path):
        with open(draft_path, "r", encoding="utf-8") as f:
            draft_skeleton = json.load(f)

    anchor_map = {a["anchor_id"]: a for a in evidence_index.get("anchors", []) if a.get("anchor_id")}

    if not reset and os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            notes = json.load(f)
    else:
        notes = _seed_base_notes(evidence_index, paper_signals, draft_skeleton)

    patch_paths = []
    if patches_dir and os.path.isdir(patches_dir):
        patch_paths.extend(sorted(glob.glob(os.path.join(patches_dir, "*.json"))))
    if patch_files:
        for pf in patch_files:
            if pf and os.path.exists(pf):
                abs_pf = os.path.abspath(pf)
                if abs_pf not in [os.path.abspath(x) for x in patch_paths]:
                    patch_paths.append(abs_pf)

    for pp in patch_paths:
        patch_data = parse_json_tolerant(pp)
        _apply_patch(notes, patch_data, os.path.basename(pp))

    _normalize_and_backfill(notes, anchor_map)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)

    summarized_cnt = sum(1 for s in notes["section_summaries"] if s.get("summary_status") == "summarized")
    total_non_ref = sum(1 for s in notes["section_summaries"] if s.get("zone") not in ("references_locked", "toc", "front_matter"))
    summary = {
        "status": "ok",
        "case_id": notes["case_id"],
        "patches_applied": len(patch_paths),
        "sections_summarized": summarized_cnt,
        "sections_body_total": total_non_ref,
        "knowledge_points": len(notes["knowledge_points"]),
        "key_results": len(notes["paper_card"]["key_results"]),
        "out_path": out_path,
    }
    print("[note_assembler] " + json.dumps(summary, ensure_ascii=False))
    return notes


def main():
    parser = argparse.ArgumentParser(description="Incremental Literature Note Assembler & Provenance Engine")
    parser.add_argument("--work-dir", required=True, help="Work directory with evidence_index.json")
    parser.add_argument("--patches-dir", default=None, help="Directory of sorted *.json patch files")
    parser.add_argument("--patch", action="append", default=[], help="Single patch JSON file (repeatable)")
    parser.add_argument("--reset", action="store_true", help="Re-seed assembled_notes.json from index before applying patches")
    args = parser.parse_args()

    assemble_notes(
        work_dir=args.work_dir,
        patches_dir=args.patches_dir,
        patch_files=args.patch,
        reset=args.reset,
    )


if __name__ == "__main__":
    main()
