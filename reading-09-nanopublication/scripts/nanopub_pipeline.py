#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nanopub_pipeline.py - Composite 2-Command Pipeline Entrypoint (bootstrap / finish)
Part of the Nanopublication Scientific Reading Skill (task_09_nanopub).
Reduces Harness round consumption while preserving all standalone CLI scripts.
Top-level imports are strictly restricted to standard libraries and sibling modules.
"""

import os
import sys
import json
import contextlib
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from structure_indexer import index_case
    from pubinfo_extractor import extract_pubinfo_from_index
    from nanopub_assembler import init_work_state, finalize_work_state, load_json_safe
    from nanopub_auditor import audit_work_state
    from render_deliverables import render_all_deliverables
    from validate_output import validate_output
except ImportError:
    from .structure_indexer import index_case
    from .pubinfo_extractor import extract_pubinfo_from_index
    from .nanopub_assembler import init_work_state, finalize_work_state, load_json_safe
    from .nanopub_auditor import audit_work_state
    from .render_deliverables import render_all_deliverables
    from .validate_output import validate_output


def run_bootstrap(input_dir, work_dir, output_dir, max_packet_tokens=4000):
    """
    Execute case_ingestor -> structure_indexer -> pubinfo_extractor -> nanopub_assembler init
    and print a concise single-screen JSON summary to stdout.
    """
    work_abs = os.path.abspath(work_dir)
    out_abs = os.path.abspath(output_dir)
    os.makedirs(work_abs, exist_ok=True)
    os.makedirs(out_abs, exist_ok=True)

    max_words = max(1200, int(max_packet_tokens * 0.72))
    max_chars = max(8000, int(max_packet_tokens * 4.5))

    log_path = os.path.join(work_abs, "bootstrap.log")
    with open(log_path, "w", encoding="utf-8") as log_f:
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
            )
            pub_path = os.path.join(work_abs, "pubinfo.json")
            with open(pub_path, "w", encoding="utf-8") as pf:
                json.dump(pub_bundle, pf, ensure_ascii=False, indent=2)

            ws_path = os.path.join(work_abs, "work_state.json")
            init_work_state(
                ingest_manifest_path=os.path.join(work_abs, "case_manifest.json"),
                structure_index_path=os.path.join(work_abs, "evidence_index.json"),
                pubinfo_path=pub_path,
                work_state_path=ws_path,
                output_dir=out_abs,
            )

    pkt_manifest = load_json_safe(os.path.join(work_abs, "reading_packets", "manifest.json"), {}) or {}
    sig_by_doc = {d.get("doc_id"): d for d in paper_signals.get("documents", [])}
    rendered_pages = (ev_index.get("artifacts") or {}).get("rendered_pages", [])

    docs_summary = []
    for dm in manifest.get("documents", []):
        did = dm.get("doc_id")
        ds = sig_by_doc.get(did) or {}
        qr = ds.get("quality_risk") or {}
        docs_summary.append({
            "doc_id": did,
            "source_file": dm.get("source_file"),
            "pages": dm.get("page_count", 1),
            "dominant_route": dm.get("dominant_route", "unknown"),
            "ocr_pages": qr.get("ocr_pages", []),
            "unextractable_pages": qr.get("unextractable_pages", []),
            "garbled_pages": qr.get("garbled_pages", []),
            "rendered_pages": [
                (rp.get("png_path") or rp.get("image_path"))
                for rp in rendered_pages
                if rp.get("doc_id") == did and (rp.get("png_path") or rp.get("image_path"))
            ],
        })

    pub_summary = [
        {
            "doc_id": s.get("doc_id"),
            "title": s.get("title"),
            "authors_count": len(s.get("authors") or []),
            "year": s.get("year"),
            "doi": s.get("doi"),
            "arxiv_id": s.get("arxiv_id"),
            "missing_fields": s.get("missing_fields", []),
        }
        for s in pub_bundle.get("sources", [])
    ]

    packets_brief = [
        {
            "packet_id": p.get("packet_id"),
            "path": os.path.join(work_abs, "reading_packets", p.get("file", "")),
            "pages": p.get("pages", []),
            "word_count": p.get("word_count", 0),
        }
        for p in pkt_manifest.get("packets", [])
    ]

    summary = {
        "pipeline_stage": "bootstrap",
        "case_id": manifest.get("case_id"),
        "documents": docs_summary,
        "anchors_count": ev_index.get("total_anchors", 0),
        "anchors_compact_path": (ev_index.get("artifacts") or {}).get("anchors_compact_path"),
        "reading_packets": packets_brief,
        "research_questions": manifest.get("research_questions", []),
        "pubinfo_summary": pub_summary,
        "early_fallback_written": os.path.isfile(os.path.join(out_abs, "result.json")),
        "work_state_path": os.path.join(work_abs, "work_state.json"),
    }
    return summary


def run_finish(work_dir, output_dir):
    """
    Execute nanopub_assembler finalize -> nanopub_auditor -> render_deliverables -> validate_output
    and print a concise diagnostic dashboard (surfacing top 10 HIGH issues first if any).
    """
    work_abs = os.path.abspath(work_dir)
    out_abs = os.path.abspath(output_dir)
    ws_path = os.path.join(work_abs, "work_state.json")
    idx_path = os.path.join(work_abs, "evidence_index.json")
    pub_path = os.path.join(work_abs, "pubinfo.json")
    sig_path = os.path.join(work_abs, "paper_signals.json")
    man_path = os.path.join(work_abs, "case_manifest.json")
    aud_path = os.path.join(work_abs, "audit_report.json")

    log_path = os.path.join(work_abs, "finish.log")
    with open(log_path, "w", encoding="utf-8") as log_f:
        with contextlib.redirect_stdout(log_f):
            state = finalize_work_state(ws_path)
            ev_index = load_json_safe(idx_path, {}) or {}
            pub_bundle = load_json_safe(pub_path)
            paper_signals = load_json_safe(sig_path)

            audit_rep = audit_work_state(
                state,
                ev_index,
                pubinfo_bundle=pub_bundle,
                paper_signals=paper_signals,
            )
            with open(aud_path, "w", encoding="utf-8") as af:
                json.dump(audit_rep, af, ensure_ascii=False, indent=2)

            result_payload = render_all_deliverables(state, audit_rep, out_abs)
            val_ok, val_rep = validate_output(
                out_abs,
                ingest_manifest_path=man_path if os.path.exists(man_path) else None,
            )

    sev = audit_rep.get("severity_counts", {"HIGH": 0, "MEDIUM": 0, "LOW": 0})
    high_issues = [
        {
            "target_id": i.get("target_id"),
            "code": i.get("code"),
            "message": i.get("message"),
            "fix_hint": i.get("fix_hint"),
        }
        for i in audit_rep.get("issues", [])
        if i.get("severity") == "HIGH"
    ][:10]

    dashboard = {
        "pipeline_stage": "finish",
        "case_id": result_payload.get("case_id"),
        "status": result_payload.get("status"),
        "audit_counts": sev,
        "top_high_issues": high_issues,
        "canonical_np_count": len(result_payload.get("nanopublications", [])),
        "qa_count": len(result_payload.get("qa_answers", [])),
        "conflicts_count": len(result_payload.get("conflicts", [])),
        "validate_passed": val_ok,
        "validate_errors": val_rep.get("errors", []),
        "deliverables": {
            "result_json": os.path.join(out_abs, "result.json"),
            "jsonld": os.path.join(out_abs, "nanopubs.jsonld"),
            "trig": os.path.join(out_abs, "nanopubs.trig"),
            "csv": os.path.join(out_abs, "nanopubs.csv"),
            "report_md": os.path.join(out_abs, "report.md"),
            "audit_report_json": os.path.join(out_abs, "audit_report.json"),
        },
    }
    return dashboard, val_ok


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Composite 2-Command Nanopublication Pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_boot = sub.add_parser("bootstrap", help="Ingest, index, OCR/render, extract PubInfo, and write early result.json")
    p_boot.add_argument("--input-dir", required=True, help="Directory containing input case.json and documents")
    p_boot.add_argument("--work-dir", required=True, help="Work directory for intermediate state")
    p_boot.add_argument("--output-dir", required=True, help="Output directory for early fallback and final deliverables")
    p_boot.add_argument("--max-packet-tokens", type=int, default=4000, help="Target token budget per reading packet")

    p_fin = sub.add_parser("finish", help="Finalize, audit, render all formats, and validate deliverables")
    p_fin.add_argument("--work-dir", required=True, help="Work directory containing work_state.json")
    p_fin.add_argument("--output-dir", required=True, help="Output directory for deliverables")

    args = parser.parse_args()
    if args.cmd == "bootstrap":
        summary = run_bootstrap(
            input_dir=args.input_dir,
            work_dir=args.work_dir,
            output_dir=args.output_dir,
            max_packet_tokens=args.max_packet_tokens,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.cmd == "finish":
        dashboard, val_ok = run_finish(work_dir=args.work_dir, output_dir=args.output_dir)
        print(json.dumps(dashboard, ensure_ascii=False, indent=2))
        sys.exit(0 if val_ok else 1)


if __name__ == "__main__":
    main()
