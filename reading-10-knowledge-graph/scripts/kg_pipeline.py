#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kg_pipeline.py - Unified 4-Command Knowledge Graph Pipeline CLI (bootstrap / patch / finish / query)
Designed for 64-request Harbor execution budgets with early fallback, instant patch precheck,
18-rule audit dashboard, and 1-round hotfix self-healing.
Part of the Literature Knowledge Graph Skill (task_10_kg).
Top-level imports are strictly restricted to standard libraries and sibling scripts.
"""

import os
import sys
import json
import glob
import re
import contextlib
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from structure_indexer import index_case
    from pubinfo_extractor import extract_pubinfo_from_index
    from entity_guard import load_ontology
    from evidence_aligner import align_verbatim_quote, _build_adjacent_windows
    from kg_assembler import assemble_kg, load_json_safe
    from kg_query_engine import ground_all_queries, execute_single_query
    from kg_auditor import audit_kg, determine_honest_status
    from render_deliverables import render_all_deliverables, _apply_output_contract_fields
    from validate_output import validate_deliverables
except ImportError:
    from .structure_indexer import index_case
    from .pubinfo_extractor import extract_pubinfo_from_index
    from .entity_guard import load_ontology
    from .evidence_aligner import align_verbatim_quote, _build_adjacent_windows
    from .kg_assembler import assemble_kg, load_json_safe
    from .kg_query_engine import ground_all_queries, execute_single_query
    from .kg_auditor import audit_kg, determine_honest_status
    from .render_deliverables import render_all_deliverables, _apply_output_contract_fields
    from .validate_output import validate_deliverables


def run_bootstrap(input_dir, work_dir, output_dir, max_packet_tokens=4000):
    """
    Execute case_ingestor -> structure_indexer -> pubinfo_extractor -> draft_batches init
    and immediately persist an early compliant fallback output_dir/result.json (status='partial').
    """
    work_abs = os.path.abspath(work_dir)
    out_abs = os.path.abspath(output_dir)
    batches_dir = os.path.join(work_abs, "draft_batches")
    os.makedirs(work_abs, exist_ok=True)
    os.makedirs(out_abs, exist_ok=True)
    os.makedirs(batches_dir, exist_ok=True)

    max_words = max(1200, int(max_packet_tokens * 0.72))
    max_chars = max(8000, int(max_packet_tokens * 4.5))

    log_path = os.path.join(work_abs, "bootstrap.log")
    with open(log_path, "w", encoding="utf-8", newline="\n") as log_f:
        with contextlib.redirect_stdout(log_f):
            index_case(
                input_dir=input_dir,
                work_dir=work_abs,
                output_dir=out_abs,
                max_packet_words=max_words,
                max_packet_chars=max_chars,
            )
            manifest = load_json_safe(os.path.join(work_abs, "case_manifest.json"), {}) or {}
            ev_index = load_json_safe(os.path.join(work_abs, "evidence_index.json"), {}) or {}
            paper_signals = load_json_safe(os.path.join(work_abs, "paper_signals.json"), {}) or {}
            pub_bundle = extract_pubinfo_from_index(
                ev_index,
                paper_signals=paper_signals,
                case_data=manifest.get("case_metadata"),
                root_case_metadata=manifest.get("root_case_metadata"),
            )
            pub_path = os.path.join(work_abs, "pubinfo.json")
            with open(pub_path, "w", encoding="utf-8", newline="\n") as pf:
                json.dump(pub_bundle, pf, ensure_ascii=False, indent=2)

    # Immediately write honest early fallback result.json preserving case_id & output_contract
    early_result = {
        "case_id": manifest.get("case_id", "unknown"),
        "status": "partial",
        "status_reasons": ["Bootstrap indexed only; KG batches not yet patched/finished."],
        "ingest_mode": manifest.get("ingest_mode"),
        "locator_scheme": manifest.get("locator_scheme"),
        "independent_paper_count": manifest.get("independent_paper_count", 0),
        "documents": pub_bundle.get("sources", []),
        "verification_twins": manifest.get("verification_twins", []),
        "kg_summary": {"node_count": 0, "edge_count": 0, "query_count": 0},
        "queries": [],
        "merge_decisions": [],
        "non_merge_decisions": [],
        "conflicts": [],
        "incremental_update_diff": {},
        "limitations_and_unreported": {"content_limitations": manifest.get("content_limitations", [])},
        "artifacts": {"result_json": "result.json"},
        "audit_summary": {"rules_evaluated": 18, "high_blocker_count": 0},
    }
    _apply_output_contract_fields(early_result, manifest.get("output_contract", {}))
    early_res_path = os.path.join(out_abs, "result.json")
    with open(early_res_path, "w", encoding="utf-8", newline="\n") as rf:
        json.dump(early_result, rf, ensure_ascii=False, indent=2)

    pkt_manifest = load_json_safe(os.path.join(work_abs, "reading_packets", "manifest.json"), {}) or {}
    packets_brief = [
        {
            "packet_id": p.get("packet_id"),
            "path": os.path.join(work_abs, "reading_packets", p.get("file", "")),
            "anchor_count": p.get("anchor_count", 0),
            "word_count": p.get("word_count", 0),
        }
        for p in pkt_manifest.get("packets", [])
    ]
    pub_summary = [
        {
            "doc_id": s.get("doc_id"),
            "source_id": s.get("source_id"),
            "role": s.get("role"),
            "title": s.get("title"),
            "doi": s.get("doi"),
            "authors_count": len(s.get("authors") or []),
            "license": s.get("license"),
            "missing_fields": s.get("missing_fields", []),
        }
        for s in pub_bundle.get("sources", [])
    ]

    return {
        "pipeline_stage": "bootstrap",
        "case_id": manifest.get("case_id"),
        "ingest_mode": manifest.get("ingest_mode"),
        "locator_scheme": manifest.get("locator_scheme"),
        "independent_paper_count": manifest.get("independent_paper_count", 0),
        "verification_twins": [
            {"twin_file": t.get("twin_file"), "twin_of_source_id": t.get("twin_of_source_id")}
            for t in manifest.get("verification_twins", [])
        ],
        "twin_verification_notes": manifest.get("twin_verification_notes", []),
        "anchor_count": ev_index.get("total_anchors", 0),
        "extracted_questions": manifest.get("research_questions", []),
        "anchors_compact_path": (ev_index.get("artifacts") or {}).get("anchors_compact_path"),
        "reading_packets": packets_brief,
        "pubinfo_summary": pub_summary,
        "leakage_check": pub_bundle.get("leakage_check", {}),
        "early_fallback_written": os.path.isfile(early_res_path),
        "next_recommended_action": "Read anchors_compact.txt or reading_packets, then submit v1_primary and v2_secondary_update batches via kg_pipeline.py patch.",
    }


def run_patch(work_dir, batch_data, stage="patch"):
    """
    Validate schema + quote alignment for a batch, persist as draft_batches/batch_NNN_<stage>.json,
    and return immediate counts and precheck warnings.
    """
    work_abs = os.path.abspath(work_dir)
    batches_dir = os.path.join(work_abs, "draft_batches")
    os.makedirs(batches_dir, exist_ok=True)

    existing = sorted(glob.glob(os.path.join(batches_dir, "batch_*.json")))
    next_seq = len(existing) + 1
    safe_stage = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(stage or "patch")).strip("_") or "patch"
    batch_fname = f"batch_{next_seq:03d}_{safe_stage}.json"
    batch_path = os.path.join(batches_dir, batch_fname)

    payload = dict(batch_data or {})
    payload.setdefault("batch_id", f"B{next_seq:03d}")
    payload["stage"] = safe_stage

    with open(batch_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    ev_index = load_json_safe(os.path.join(work_abs, "evidence_index.json"), {}) or {}
    anchors = ev_index.get("anchors", [])
    windows = _build_adjacent_windows(anchors) if anchors else []
    onto = load_ontology()
    valid_etypes = {e["id"] for e in onto.get("entity_types", [])}
    valid_rels = {r["id"] for r in onto.get("relations", [])}

    precheck_warnings = []
    for n in payload.get("nodes") or []:
        nid = n.get("node_id", "?")
        if n.get("entity_type") not in valid_etypes:
            precheck_warnings.append(f"Node {nid}: invalid entity_type '{n.get('entity_type')}'.")
        raw_sids = n.get("source_ids") or n.get("source_id")
        if isinstance(raw_sids, str):
            n_sids = [raw_sids.strip()] if raw_sids.strip() else []
        elif isinstance(raw_sids, (list, tuple)):
            n_sids = [str(s).strip() for s in raw_sids if str(s).strip()]
        else:
            n_sids = []
        unique_n_sid = n_sids[0] if len(n_sids) == 1 else None
        raw_evs = n.get("evidence")
        evs = [raw_evs] if isinstance(raw_evs, dict) else (raw_evs if isinstance(raw_evs, list) else [])
        if not evs:
            precheck_warnings.append(f"Node {nid}: missing evidence list.")
        elif anchors:
            for ev in evs:
                if not isinstance(ev, dict):
                    continue
                q = ev.get("verbatim_quote") or ev.get("quote") or ""
                loc = ev.get("paragraph_id") or ev.get("anchor_id") or ev.get("locator_id")
                esid = ev.get("source_id") or ev.get("doc_id") or unique_n_sid
                al = align_verbatim_quote(q, source_id=esid, locator_id=loc, indexed_blocks=anchors, windows=windows)
                if not al.get("aligned"):
                    precheck_warnings.append(f"Node {nid}: quote alignment failed ({al.get('alignment_status')}).")

    for e in payload.get("edges") or []:
        eid = e.get("edge_id", "?")
        rel = e.get("relation") or e.get("predicate")
        if rel not in valid_rels:
            precheck_warnings.append(f"Edge {eid}: invalid relation '{rel}'.")
        raw_evs = e.get("evidence")
        evs = [raw_evs] if isinstance(raw_evs, dict) else (raw_evs if isinstance(raw_evs, list) else [])
        if not evs:
            precheck_warnings.append(f"Edge {eid}: missing evidence list.")
        elif anchors:
            for ev in evs:
                if not isinstance(ev, dict):
                    continue
                q = ev.get("verbatim_quote") or ev.get("quote") or ""
                loc = ev.get("paragraph_id") or ev.get("anchor_id") or ev.get("locator_id")
                esid = ev.get("source_id") or ev.get("doc_id") or e.get("source_id")
                al = align_verbatim_quote(q, source_id=esid, locator_id=loc, indexed_blocks=anchors, windows=windows)
                if not al.get("aligned"):
                    precheck_warnings.append(f"Edge {eid}: quote alignment failed ({al.get('alignment_status')}).")

    removed_cnt = sum(
        len(payload.get(k) or [])
        for k in ("remove_node_ids", "remove_edge_ids", "remove_query_ids", "remove_merge_ids", "remove_non_merge_ids", "remove_conflict_ids")
    )
    replaced_cnt = sum(
        1 for col in ("nodes", "edges", "merge_decisions", "non_merge_decisions", "conflicts", "queries")
        for item in (payload.get(col) or [])
        if isinstance(item, dict) and item.get("replaces_id")
    )

    return {
        "pipeline_stage": "patch",
        "batch_file": batch_fname,
        "batch_path": batch_path,
        "stage": safe_stage,
        "counts": {
            "nodes": len(payload.get("nodes") or []),
            "edges": len(payload.get("edges") or []),
            "merge_decisions": len(payload.get("merge_decisions") or []),
            "non_merge_decisions": len(payload.get("non_merge_decisions") or []),
            "conflicts": len(payload.get("conflicts") or []),
            "queries": len(payload.get("queries") or []),
            "replaced": replaced_cnt,
            "removed": removed_cnt,
        },
        "precheck_warnings": precheck_warnings,
    }


def run_finish(work_dir, output_dir):
    """
    Execute kg_assembler -> kg_query_engine -> kg_auditor -> render_deliverables -> validate_output
    and return a top-surfaced diagnostic dashboard JSON.
    """
    work_abs = os.path.abspath(work_dir)
    out_abs = os.path.abspath(output_dir)
    os.makedirs(out_abs, exist_ok=True)

    log_path = os.path.join(work_abs, "finish.log")
    with open(log_path, "w", encoding="utf-8", newline="\n") as log_f:
        with contextlib.redirect_stdout(log_f):
            assembled = assemble_kg(work_abs)
            ev_index = load_json_safe(os.path.join(work_abs, "evidence_index.json"), {}) or {}
            manifest = load_json_safe(os.path.join(work_abs, "case_manifest.json"), {}) or {}
            pubinfo = load_json_safe(os.path.join(work_abs, "pubinfo.json"), {}) or {}
            paper_signals = load_json_safe(os.path.join(work_abs, "paper_signals.json"), {}) or {}

            ground_all_queries(assembled, case_manifest=manifest, evidence_index=ev_index)
            with open(os.path.join(work_abs, "assembled_kg.json"), "w", encoding="utf-8", newline="\n") as kf:
                json.dump(assembled, kf, ensure_ascii=False, indent=2)

            audit_rep = audit_kg(
                assembled,
                evidence_index=ev_index,
                case_manifest=manifest,
                pubinfo_bundle=pubinfo,
                paper_signals=paper_signals,
            )
            honest_status, status_reasons = determine_honest_status(audit_rep, manifest, assembled)
            audit_rep["honest_status"] = honest_status
            audit_rep["status_reasons"] = status_reasons
            with open(os.path.join(work_abs, "audit_report.json"), "w", encoding="utf-8", newline="\n") as af:
                json.dump(audit_rep, af, ensure_ascii=False, indent=2)

            result_payload = render_all_deliverables(assembled, audit_rep, manifest, out_abs)
            val_ok, val_rep = validate_deliverables(out_abs, work_dir=work_abs)

    top_high_issues = [
        {
            "target_id": i.get("target_id"),
            "code": i.get("code"),
            "message": i.get("message"),
            "fix_hint": i.get("fix_hint"),
        }
        for i in audit_rep.get("issues", [])
        if i.get("severity") == "HIGH"
    ][:10]

    diff = assembled.get("incremental_update_diff") or {}
    dashboard = {
        "pipeline_stage": "finish",
        "case_id": result_payload.get("case_id"),
        "status": result_payload.get("status"),
        "status_reasons": result_payload.get("status_reasons", []),
        "kg_counts": {
            "nodes": len(assembled.get("nodes", [])),
            "edges": len(assembled.get("edges", [])),
            "merge_decisions": len(assembled.get("merge_decisions", [])),
            "non_merge_decisions": len(assembled.get("non_merge_decisions", [])),
            "conflicts": len(assembled.get("conflicts", [])),
        },
        "query_count": len(assembled.get("queries", [])),
        "incremental_diff_summary": {
            "v1_primary": diff.get("v1_primary_counts"),
            "v2_total": diff.get("v2_total_counts"),
            "added_nodes": len(diff.get("added_node_ids", [])),
            "added_edges": len(diff.get("added_edge_ids", [])),
            "cross_paper_merged_nodes": len(diff.get("cross_paper_merged_node_ids", [])),
            "rejected_non_merges": len(diff.get("rejected_non_merge_pairs", [])),
            "new_conflicts": len(diff.get("newly_introduced_conflict_ids", [])),
            "hotfix_ops": len(diff.get("hotfix_operations", [])),
        },
        "high_blocker_count": audit_rep.get("high_blocker_count", 0),
        "top_high_issues": top_high_issues,
        "validation_passed": val_ok,
        "validation_errors": val_rep.get("errors", []),
        "deliverables": result_payload.get("artifacts", {}),
    }
    return dashboard, val_ok


def run_query_cmd(work_dir, question, query_type=None, entities=None, targets=None):
    work_abs = os.path.abspath(work_dir)
    kg_path = os.path.join(work_abs, "assembled_kg.json")
    if os.path.isfile(kg_path):
        kg = load_json_safe(kg_path, {}) or {}
    else:
        kg = assemble_kg(work_abs)
    all_targets = list(entities or []) + list(targets or [])
    return execute_single_query(
        {"query_id": "Q_ADHOC", "question": question, "query_type": query_type, "target_entities": all_targets},
        kg,
    )


def run_inspect_cmd(work_dir, source_id=None, section_role=None, grep=None, anchor_id=None, limit=20):
    """Fast read-only anchor/paragraph lookup so the Agent can copy verbatim quotes without writing scratch scripts."""
    work_abs = os.path.abspath(work_dir)
    ev_index = load_json_safe(os.path.join(work_abs, "evidence_index.json"), {}) or {}
    anchors = ev_index.get("anchors", [])
    grep_re = re.compile(grep, re.I) if grep else None
    matched = []
    for a in anchors:
        if source_id and a.get("source_id") != source_id and a.get("doc_id") != source_id:
            continue
        if section_role and a.get("section_role") != section_role:
            continue
        if anchor_id and a.get("anchor_id") != anchor_id and a.get("paragraph_id") != anchor_id:
            continue
        txt = a.get("text", "")
        if grep_re and not grep_re.search(txt):
            continue
        sigs = [
            k for k, flag in (
                ("cite", a.get("has_citation_signal")),
                ("spec", a.get("has_speculation_signal")),
                ("quant", a.get("has_quantitative_signal")),
                ("neg", a.get("has_negation_signal")),
            ) if flag
        ]
        matched.append({
            "anchor_id": a.get("anchor_id"),
            "source_id": a.get("source_id") or a.get("doc_id"),
            "paragraph_id": a.get("paragraph_id"),
            "page_number": a.get("page_number"),
            "section_role": a.get("section_role"),
            "section_title": a.get("section_title"),
            "signals": sigs,
            "text": txt,
        })
    lim = max(1, int(limit or 20))
    return {
        "pipeline_stage": "inspect",
        "locator_scheme": ev_index.get("locator_scheme"),
        "matched_count": len(matched),
        "returned_count": min(len(matched), lim),
        "anchors": matched[:lim],
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Literature Knowledge Graph 5-Command Pipeline CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_boot = sub.add_parser("bootstrap", help="Ingest, index, extract PubInfo, and write early result.json")
    p_boot.add_argument("--input", "--input-dir", dest="input_dir", required=True, help="Input directory")
    p_boot.add_argument("--work", "--work-dir", dest="work_dir", required=True, help="Work directory")
    p_boot.add_argument("--output", "--output-dir", dest="output_dir", required=True, help="Output directory")
    p_boot.add_argument("--max-packet-tokens", type=int, default=4000, help="Target token budget per reading packet")

    p_ins = sub.add_parser("inspect", help="Read-only filter/grep over indexed anchors & JATS paragraphs")
    p_ins.add_argument("--work", "--work-dir", dest="work_dir", required=True, help="Work directory")
    p_ins.add_argument("--source", dest="source_id", default=None, help="Filter by source_id or doc_id")
    p_ins.add_argument("--role", dest="section_role", default=None, help="Filter by section_role")
    p_ins.add_argument("--grep", default=None, help="Case-insensitive regex/keyword filter")
    p_ins.add_argument("--anchor", dest="anchor_id", default=None, help="Exact anchor_id or paragraph_id")
    p_ins.add_argument("--limit", type=int, default=20, help="Maximum anchors to return")

    p_patch = sub.add_parser("patch", help="Validate and record an incremental KG batch (v1_primary / v2_secondary_update / hotfix)")
    p_patch.add_argument("--work", "--work-dir", dest="work_dir", required=True, help="Work directory")
    src_grp = p_patch.add_mutually_exclusive_group(required=True)
    src_grp.add_argument("--batch-file", help="Path to JSON batch file")
    src_grp.add_argument("--stdin", action="store_true", help="Read JSON batch from stdin")
    p_patch.add_argument("--stage", default="patch", help="Stage label (e.g. v1_primary, v2_secondary_update, hotfix)")

    p_fin = sub.add_parser("finish", help="Assemble, query, audit (18 rules), render deliverables, and validate output")
    p_fin.add_argument("--work", "--work-dir", dest="work_dir", required=True, help="Work directory")
    p_fin.add_argument("--output", "--output-dir", dest="output_dir", required=True, help="Output directory")

    p_q = sub.add_parser("query", help="Execute a deterministic subgraph query against assembled_kg.json")
    p_q.add_argument("--work", "--work-dir", dest="work_dir", required=True, help="Work directory")
    p_q.add_argument("--question", required=True, help="Query question text")
    p_q.add_argument("--type", dest="query_type", default=None, help="Optional template_id")
    p_q.add_argument("--entity", action="append", default=[], help="Target entity name or node_id")
    p_q.add_argument("--target", action="append", default=[], help="Optional target end entity for multi_hop_path")

    args = parser.parse_args()
    if args.cmd == "bootstrap":
        res = run_bootstrap(args.input_dir, args.work_dir, args.output_dir, max_packet_tokens=args.max_packet_tokens)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "inspect":
        res = run_inspect_cmd(
            args.work_dir,
            source_id=args.source_id,
            section_role=args.section_role,
            grep=args.grep,
            anchor_id=args.anchor_id,
            limit=args.limit,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "patch":
        if args.stdin:
            batch_data = json.load(sys.stdin)
        else:
            with open(args.batch_file, "r", encoding="utf-8") as bf:
                batch_data = json.load(bf)
        res = run_patch(args.work_dir, batch_data, stage=args.stage)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "finish":
        dash, val_ok = run_finish(args.work_dir, args.output_dir)
        print(json.dumps(dash, ensure_ascii=False, indent=2))
        sys.exit(0 if val_ok else 1)
    elif args.cmd == "query":
        res = run_query_cmd(args.work_dir, args.question, query_type=args.query_type, entities=args.entity, targets=args.target)
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
