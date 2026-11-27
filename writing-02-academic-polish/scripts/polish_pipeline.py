#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
polish_pipeline.py - Unified 4-Command Academic Language Polishing CLI
(bootstrap / inspect / patch / finish).
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import case_ingestor
import diff_engine
import epistemic_entity_guard
import glossary_abbr_guard
import polish_verifier
import render_deliverables
import style_linter
import validate_output


def _write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def _parse_json_tolerant(raw_text):
    """Strip markdown code fences and trailing commas before JSON parsing."""
    txt = (raw_text or "").strip()
    if txt.startswith("\ufeff"):
        txt = txt[1:]
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", txt, re.I)
    if m:
        txt = m.group(1).strip()
    try:
        return json.loads(txt)
    except Exception:
        # Remove trailing commas before ] or }
        cleaned = re.sub(r",\s*([\]}])", r"\1", txt)
        return json.loads(cleaned)


def run_bootstrap(args):
    manifest, blocks, sentences = case_ingestor.ingest_case(
        input_path=args.input,
        work_dir=args.work_dir,
        task_override=args.task,
        glossary_override=args.glossary,
        style_override=args.style,
        intensity_override=args.intensity,
        mode_override=args.mode,
    )
    glossary_bundle = glossary_abbr_guard.load_and_audit_glossary(
        manifest.get("glossary_paths_abs", []),
        manifest.get("case_data", {}),
    )
    _write_json_atomic(os.path.join(args.work_dir, "glossary_audit.json"), glossary_bundle)

    # Pre-check summary per sentence
    pre_lint_cnt = 0
    for s in sentences:
        if s.get("in_scope"):
            pre_lint_cnt += len(style_linter.lint_sentence(s["source_text"], lang=s.get("language"), target_style=manifest["target_style"], mode="pre"))

    # Compact manifest text for fast inspection
    compact_lines = [
        f"case_id: {manifest['case_id']}",
        f"source_format: {manifest['source_format']} | mode: {manifest['execution_mode']} | style: {manifest['target_style']} | intensity: {manifest['edit_intensity']}",
        f"sentences: {manifest['sentence_count']} total / {manifest['in_scope_sentence_count']} in-scope | batches: {manifest['batch_count']}",
        f"glossary_locked: {len(glossary_bundle['locked_terms'])} | glossary_conflicts: {len(glossary_bundle['glossary_conflicts'])}",
        f"anomalies: {[a['code'] for a in manifest['anomalies']]}",
        "---",
    ]
    for s in sentences[:80]:
        tag = "EDIT" if s["in_scope"] else "KEEP"
        compact_lines.append(f"{s['sentence_id']} [{tag}|{s['section_role']}]: {s['source_text'][:140]}")
    if len(sentences) > 80:
        compact_lines.append(f"... (+{len(sentences) - 80} more sentences in sentences.json)")
    with open(os.path.join(args.work_dir, "manifest_compact.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(compact_lines) + "\n")

    # Initialize state & empty checkpoint
    state = {
        "case_id": manifest["case_id"],
        "execution_mode": manifest["execution_mode"],
        "target_style": manifest["target_style"],
        "edit_intensity": manifest["edit_intensity"],
        "total_sentences": manifest["sentence_count"],
        "in_scope_sentences": manifest["in_scope_sentence_count"],
        "batch_files": manifest["batch_files"],
        "pre_lint_issue_count": pre_lint_cnt,
    }
    _write_json_atomic(os.path.join(args.work_dir, "state.json"), state)
    if not os.path.exists(os.path.join(args.work_dir, "checkpoint.json")):
        _write_json_atomic(os.path.join(args.work_dir, "checkpoint.json"), {"records": {}, "patch_history": []})

    # Immediate early-drop fallback result.json in out_dir
    os.makedirs(args.out_dir, exist_ok=True)
    case_data = manifest.get("case_data") or {}
    early_status = "blocked" if manifest["sentence_count"] == 0 else "partial"
    early_result = {
        "case_id": manifest["case_id"],
        "status": early_status,
        "execution_mode": manifest["execution_mode"],
        "target_style": manifest["target_style"],
        "edit_intensity": manifest["edit_intensity"],
        "polished_text": "",
        "edit_log": [],
        "audit_report": {"summary": {"total_sentences": manifest["sentence_count"], "edited_sentences": 0, "high_count": 0}, "top_high_issues": []},
        "glossary_conflicts": glossary_bundle["glossary_conflicts"],
        "uncertainty": [
            {"source": "bootstrap", "code": a["code"], "detail": a["detail"], "requires_human_confirmation": bool(a.get("requires_human_confirmation"))}
            for a in manifest["anomalies"]
        ],
        "sources": [{"path": manifest.get("source_path"), "format": manifest.get("source_format")}] if manifest.get("source_path") else [],
        "deliverables": {},
    }
    if "output_contract" in case_data:
        early_result["output_contract"] = render_deliverables._adapt_output_contract(case_data["output_contract"], early_result)
    _write_json_atomic(os.path.join(args.out_dir, "result.json"), early_result)

    next_act = (
        "Run finish directly (no editable prose sentences)."
        if manifest["in_scope_sentence_count"] == 0
        else ("Inspect reading_packets or run finish (audit_only/qa_guard mode)." if manifest["execution_mode"] in ("audit_only", "qa_guard") else "Inspect reading_packets/batch_01.json and submit patch batches.")
    )
    print(json.dumps({
        "case_id": manifest["case_id"],
        "execution_mode": manifest["execution_mode"],
        "target_style": manifest["target_style"],
        "edit_intensity": manifest["edit_intensity"],
        "total_sentences": manifest["sentence_count"],
        "in_scope_sentences": manifest["in_scope_sentence_count"],
        "batch_count": manifest["batch_count"],
        "glossary_conflict_count": len(glossary_bundle["glossary_conflicts"]),
        "anomaly_codes": [a["code"] for a in manifest["anomalies"]],
        "next_action": next_act,
    }, ensure_ascii=False, indent=2))


def run_inspect(args):
    work_dir = args.work_dir
    manifest = case_ingestor.load_json_safe(os.path.join(work_dir, "ingest_manifest.json"), {})
    sentences = case_ingestor.load_json_safe(os.path.join(work_dir, "sentences.json"), [])
    glossary_bundle = case_ingestor.load_json_safe(os.path.join(work_dir, "glossary_audit.json"), {})
    ckpt = case_ingestor.load_json_safe(os.path.join(work_dir, "checkpoint.json"), {"records": {}})

    view = args.view
    if view == "overview":
        payload = {
            "case_id": manifest.get("case_id"),
            "execution_mode": manifest.get("execution_mode"),
            "target_style": manifest.get("target_style"),
            "edit_intensity": manifest.get("edit_intensity"),
            "total_sentences": len(sentences),
            "in_scope_sentences": manifest.get("in_scope_sentence_count", 0),
            "patched_sentences": len(ckpt.get("records", {})),
            "batch_files": manifest.get("batch_files", []),
            "anomalies": manifest.get("anomalies", []),
            "glossary_conflicts": glossary_bundle.get("glossary_conflicts", []),
        }
    elif view == "batch":
        bid = args.batch_id or "batch_01"
        if not bid.endswith(".json"):
            bid_fn = f"{bid}.json"
        else:
            bid_fn = os.path.basename(bid)
        p = os.path.join(work_dir, "reading_packets", bid_fn)
        payload = case_ingestor.load_json_safe(p, {"error": f"Batch not found: {bid_fn}"})
    elif view == "glossary":
        payload = glossary_bundle
    elif view == "issues":
        ver = case_ingestor.load_json_safe(os.path.join(work_dir, "verification_report.json"), {})
        if ver:
            payload = {
                "summary": ver.get("summary", {}),
                "actionable_top_high_issues": ver.get("actionable_top_high_issues", []),
                "non_repairable_advisories": ver.get("non_repairable_advisories", []),
                "top_high_issues": ver.get("top_high_issues", []),
                "uncertainty_items": ver.get("uncertainty_items", []),
            }
        else:
            payload = {"anomalies": manifest.get("anomalies", []), "glossary_conflicts": glossary_bundle.get("glossary_conflicts", [])}
    elif view == "sentences":
        target_ids = set(x.strip() for x in (args.ids or "").split(",") if x.strip())
        subset = [s for s in sentences if (not target_ids or s["sentence_id"] in target_ids)]
        payload = {"count": len(subset), "sentences": subset[:40]}
    else:
        payload = {"error": f"Unknown view: {view}"}

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_patch(args):
    work_dir = args.work_dir
    manifest = case_ingestor.load_json_safe(os.path.join(work_dir, "ingest_manifest.json"), {})
    sentences = case_ingestor.load_json_safe(os.path.join(work_dir, "sentences.json"), [])
    ckpt_path = os.path.join(work_dir, "checkpoint.json")
    ckpt = case_ingestor.load_json_safe(ckpt_path, {"records": {}, "patch_history": []})

    if args.stdin:
        raw_input = sys.stdin.read()
    elif args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            raw_input = f.read()
    else:
        raise ValueError("patch requires either --file or --stdin")

    parsed = _parse_json_tolerant(raw_input)
    if isinstance(parsed, dict):
        items = parsed.get("edits") or parsed.get("sentences") or parsed.get("records") or ([parsed] if "sentence_id" in parsed else [])
    elif isinstance(parsed, list):
        items = parsed
    else:
        items = []

    sent_map = {s["sentence_id"]: s for s in sentences}
    exec_mode = manifest.get("execution_mode", "polish")
    edit_intensity = manifest.get("edit_intensity", "standard")
    target_style = manifest.get("target_style", "general_academic")

    immediate_high_issues = []
    mode_warnings = []
    accepted = 0
    replaces_set = {x.strip() for x in (args.replaces_id or "").split(",") if x.strip()}

    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("sentence_id", "")).strip()
        if not sid or sid not in sent_map:
            immediate_high_issues.append({"sentence_id": sid or "UNKNOWN", "code": "UNKNOWN_SENTENCE_ID", "severity": "HIGH", "detail": f"Unknown sentence_id '{sid}'."})
            continue
        if replaces_set and sid not in replaces_set and sid in ckpt["records"]:
            pass

        sent = sent_map[sid]
        src = sent["source_text"]
        incoming_pol = item.get("polished_text")
        if incoming_pol is None:
            incoming_pol = src

        edit_types = item.get("edit_types") or []
        if isinstance(edit_types, str):
            edit_types = [t.strip() for t in edit_types.split(",") if t.strip()]
        reason = str(item.get("reason") or "").strip()
        needs_hr = bool(item.get("needs_human_review", False))
        hr_note = str(item.get("human_review_note") or "").strip()
        uncertainty = str(item.get("uncertainty") or hr_note).strip()
        if needs_hr and "needs_human_review" not in edit_types:
            edit_types.append("needs_human_review")
        if needs_hr and not uncertainty:
            uncertainty = "Flagged needs_human_review."

        bilingual_cp = str(item.get("bilingual_counterpart") or "").strip()
        dbl_neg = bool(item.get("double_neg_allowed", False))
        audit_notes = str(item.get("audit_notes") or "").strip()
        suggested_txt = str(item.get("suggested_text") or "").strip()

        # Protect qa_guard and audit_only modes from unauthorized text overwriting
        if exec_mode in ("qa_guard", "audit_only") and incoming_pol != src:
            mode_warnings.append({
                "sentence_id": sid,
                "code": "MODE_EDIT_REJECTED",
                "detail": f"Mode '{exec_mode}' forbids modifying source_text; kept verbatim source_text.",
            })
            if exec_mode == "audit_only" and not suggested_txt:
                suggested_txt = incoming_pol
            final_pol = src
            if not edit_types or all(t != "minimal_preserve" for t in edit_types):
                edit_types = ["minimal_preserve"] if exec_mode == "qa_guard" else ["needs_human_review"]
            if not reason:
                reason = f"Preserved verbatim under {exec_mode} mode."
        elif not sent.get("in_scope", True) and incoming_pol != src:
            mode_warnings.append({
                "sentence_id": sid,
                "code": "OUT_OF_SCOPE_REJECTED",
                "detail": f"Sentence {sid} is out of scope; kept verbatim source_text.",
            })
            final_pol = src
            edit_types = ["minimal_preserve"]
            reason = "Out-of-scope sentence preserved verbatim."
        else:
            final_pol = incoming_pol

        # Real-time single-sentence checks
        _, ledger_iss = diff_engine.audit_edit_record(sid, src, final_pol, edit_types, reason, edit_intensity=edit_intensity)
        ee_iss = epistemic_entity_guard.compare_epistemic_and_invariants(src, final_pol, lang=sent.get("language"), double_neg_allowed=dbl_neg)
        lint_iss = style_linter.lint_sentence(final_pol, lang=sent.get("language"), target_style=target_style, mode="post")

        for iss in (ledger_iss + [{"sentence_id": sid, **x} for x in ee_iss] + [{"sentence_id": sid, **x} for x in lint_iss]):
            if iss.get("severity") == "HIGH":
                immediate_high_issues.append(iss)

        ckpt["records"][sid] = {
            "sentence_id": sid,
            "polished_text": final_pol,
            "edit_types": edit_types,
            "reason": reason,
            "uncertainty": uncertainty,
            "needs_human_review": needs_hr,
            "bilingual_counterpart": bilingual_cp,
            "double_neg_allowed": dbl_neg,
            "suggested_text": suggested_txt,
            "audit_notes": audit_notes,
        }
        accepted += 1

    ckpt.setdefault("patch_history", []).append({"accepted": accepted, "replaces_id": args.replaces_id})
    _write_json_atomic(ckpt_path, ckpt)

    in_scope_total = max(1, sum(1 for s in sentences if s.get("in_scope")))
    done_in_scope = sum(1 for s in sentences if s.get("in_scope") and s["sentence_id"] in ckpt["records"])
    pct = round(100.0 * done_in_scope / in_scope_total, 1) if manifest.get("in_scope_sentence_count", 0) > 0 else 100.0

    print(json.dumps({
        "accepted_count": accepted,
        "completed_in_scope": done_in_scope,
        "total_in_scope": manifest.get("in_scope_sentence_count", 0),
        "completion_pct": pct,
        "immediate_high_issues": immediate_high_issues[:15],
        "mode_warnings": mode_warnings[:10],
    }, ensure_ascii=False, indent=2))


def run_finish(args):
    work_dir = args.work_dir
    out_dir = args.out_dir
    manifest = case_ingestor.load_json_safe(os.path.join(work_dir, "ingest_manifest.json"), {})
    blocks = case_ingestor.load_json_safe(os.path.join(work_dir, "blocks.json"), [])
    sentences = case_ingestor.load_json_safe(os.path.join(work_dir, "sentences.json"), [])
    glossary_bundle = case_ingestor.load_json_safe(os.path.join(work_dir, "glossary_audit.json"), None)
    ckpt = case_ingestor.load_json_safe(os.path.join(work_dir, "checkpoint.json"), {"records": {}})

    # Auto-passthrough out-of-scope sentences (and in audit_only / qa_guard modes)
    merged_input = dict(ckpt.get("records", {}))
    exec_mode = manifest.get("execution_mode", "polish")
    for s in sentences:
        sid = s["sentence_id"]
        if sid not in merged_input and (not s.get("in_scope", True) or exec_mode in ("audit_only", "qa_guard")):
            merged_input[sid] = {
                "sentence_id": sid,
                "polished_text": s["source_text"],
                "edit_types": ["minimal_preserve"],
                "reason": f"Preserved verbatim ({'mode=' + exec_mode if exec_mode in ('audit_only', 'qa_guard') else 'out_of_scope'}).",
                "uncertainty": "",
            }

    report = polish_verifier.verify_polish_run(manifest, sentences, merged_input, glossary_bundle=glossary_bundle)
    _write_json_atomic(os.path.join(work_dir, "verification_report.json"), report)

    result_obj = render_deliverables.render_all_deliverables(
        manifest, blocks, report, out_dir, allow_partial=bool(args.allow_partial)
    )
    val_report = validate_output.validate_deliverables(out_dir, case_data=manifest.get("case_data"), strict=True)

    print(json.dumps({
        "case_id": result_obj["case_id"],
        "status": result_obj["status"],
        "overall_audit_score": report["summary"]["overall_audit_score"],
        "high_count": report["summary"]["high_count"],
        "actionable_high_count": report["summary"].get("actionable_high_count", report["summary"]["high_count"]),
        "advisory_high_count": report["summary"].get("advisory_high_count", 0),
        "medium_count": report["summary"]["medium_count"],
        "actionable_top_high_issues": report.get("actionable_top_high_issues", [])[:10],
        "non_repairable_advisories": report.get("non_repairable_advisories", [])[:10],
        "top_high_issues": report["top_high_issues"][:10],
        "preview_render_tier": result_obj["preview_render_tier"],
        "preview_render_note": result_obj.get("preview_render_note", ""),
        "validation_passed": val_report["passed"],
        "validation_errors": val_report["errors"],
        "deliverables": list(result_obj["deliverables"].keys()),
    }, ensure_ascii=False, indent=2))

    if not val_report["passed"]:
        sys.exit(1)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Unified Academic Language Polishing Pipeline CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_boot = sub.add_parser("bootstrap", help="Ingest material, audit glossary, slice packets, stage fallback result.json")
    p_boot.add_argument("--input", required=True, help="Input directory or file path")
    p_boot.add_argument("--work-dir", required=True, help="Working directory")
    p_boot.add_argument("--out-dir", required=True, help="Output deliverable directory")
    p_boot.add_argument("--task", default=None, help="Optional task.md path override")
    p_boot.add_argument("--glossary", default=None, help="Optional external glossary file path")
    p_boot.add_argument("--style", default=None, help="Target style profile override")
    p_boot.add_argument("--intensity", default=None, choices=["minimal", "standard", "deep"], help="Edit intensity")
    p_boot.add_argument("--mode", default="auto", choices=["auto", "polish", "bilingual_polish", "audit_only", "qa_guard"], help="Execution mode")

    p_insp = sub.add_parser("inspect", help="Read-only lookup of work manifests, reading packets, glossary, or issues")
    p_insp.add_argument("--work-dir", required=True, help="Working directory")
    p_insp.add_argument("--view", required=True, choices=["overview", "batch", "issues", "glossary", "sentences"], help="Inspection view")
    p_insp.add_argument("--batch-id", default=None, help="Batch ID (e.g., batch_01) when --view batch")
    p_insp.add_argument("--ids", default=None, help="Comma-separated sentence IDs when --view sentences")

    p_patch = sub.add_parser("patch", help="Submit polished sentence records and run real-time verification")
    p_patch.add_argument("--work-dir", required=True, help="Working directory")
    src_grp = p_patch.add_mutually_exclusive_group(required=True)
    src_grp.add_argument("--file", default=None, help="JSON file containing polished sentence records")
    src_grp.add_argument("--stdin", action="store_true", help="Read JSON polished sentence records from stdin")
    p_patch.add_argument("--replaces-id", default=None, help="Optional comma-separated sentence IDs for targeted hotfix")

    p_fin = sub.add_parser("finish", help="Merge checkpoint, run 8-dim audit, render deliverables & previews, validate")
    p_fin.add_argument("--work-dir", required=True, help="Working directory")
    p_fin.add_argument("--out-dir", required=True, help="Output deliverable directory")
    p_fin.add_argument("--allow-partial", action="store_true", help="Allow partial status when budgeted run stops early")

    args = parser.parse_args()
    if args.command == "bootstrap":
        run_bootstrap(args)
    elif args.command == "inspect":
        run_inspect(args)
    elif args.command == "patch":
        run_patch(args)
    elif args.command == "finish":
        run_finish(args)


if __name__ == "__main__":
    main()
