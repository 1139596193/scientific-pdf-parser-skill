#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_output.py - Final Deliverable Gatekeeper & Integrity Verifier
Verifies result.json, output_contract fidelity, honest status consistency, GraphML XML validity,
CSV/JSON/Markdown/SVG presence, and UTF-8 no-BOM compliance.
Part of the Literature Knowledge Graph Skill (task_10_kg).
Top-level imports are strictly restricted to standard libraries and sibling scripts.
"""

import os
import sys
import json
import argparse
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from kg_auditor import determine_honest_status
except ImportError:
    from .kg_auditor import determine_honest_status


REQUIRED_TEXT_ARTIFACTS = (
    "result.json",
    "knowledge_graph.json",
    "knowledge_graph.graphml",
    "nodes.csv",
    "edges.csv",
    "knowledge_graph_report.md",
    "kg_overview.svg",
)


def validate_deliverables(output_dir, work_dir=None):
    out_abs = os.path.abspath(output_dir)
    work_abs = os.path.abspath(work_dir) if work_dir else None
    errors = []
    warnings = []

    res_path = os.path.join(out_abs, "result.json")
    if not os.path.isfile(res_path):
        errors.append("Missing result.json in output_dir.")
        return False, {"passed": False, "errors": errors, "warnings": warnings}

    try:
        with open(res_path, "r", encoding="utf-8") as f:
            res_data = json.load(f)
    except Exception as exc:
        errors.append(f"Invalid JSON in result.json: {exc}")
        return False, {"passed": False, "errors": errors, "warnings": warnings}

    if not str(res_data.get("case_id") or "").strip():
        errors.append("result.json missing non-empty case_id.")

    if "output_contract" not in res_data:
        errors.append("result.json missing preserved output_contract.")
    elif isinstance(res_data["output_contract"], dict):
        req = res_data["output_contract"].get("required_fields")
        if isinstance(req, list):
            for fld in req:
                if fld not in res_data:
                    errors.append(f"result.json missing output_contract required field: {fld}")

    # Verify required deliverables exist, non-empty, UTF-8 without BOM
    for fn in REQUIRED_TEXT_ARTIFACTS:
        p = os.path.join(out_abs, fn)
        if not os.path.isfile(p):
            errors.append(f"Missing required deliverable: {fn}")
            continue
        with open(p, "rb") as rf:
            raw = rf.read()
        if len(raw) == 0:
            errors.append(f"Empty deliverable file: {fn}")
            continue
        if raw.startswith(b"\xef\xbb\xbf"):
            errors.append(f"Deliverable {fn} contains UTF-8 BOM.")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"Deliverable {fn} is not valid UTF-8: {exc}")

    # Validate GraphML XML well-formedness
    gml_path = os.path.join(out_abs, "knowledge_graph.graphml")
    if os.path.isfile(gml_path):
        try:
            with open(gml_path, "r", encoding="utf-8") as gf:
                ET.fromstring(gf.read())
        except Exception as exc:
            errors.append(f"Invalid XML in knowledge_graph.graphml: {exc}")

    # Validate knowledge_graph.json
    kg_path = os.path.join(out_abs, "knowledge_graph.json")
    kg_data = {}
    if os.path.isfile(kg_path):
        try:
            with open(kg_path, "r", encoding="utf-8") as kf:
                kg_data = json.load(kf)
        except Exception as exc:
            errors.append(f"Invalid JSON in knowledge_graph.json: {exc}")

    # Verify all declared artifacts in result.json exist & are non-empty
    declared_artifacts = res_data.get("artifacts") or {}
    for k, rel in declared_artifacts.items():
        if not rel:
            continue
        ap = rel if os.path.isabs(rel) else os.path.join(out_abs, rel)
        if not os.path.isfile(ap) or os.path.getsize(ap) == 0:
            errors.append(f"Declared artifact '{k}' missing or empty: {rel}")

    # Verify honest status consistency against audit_report & manifest
    if work_abs:
        aud_path = os.path.join(work_abs, "audit_report.json")
        cm_path = os.path.join(work_abs, "case_manifest.json")
        if os.path.isfile(aud_path):
            with open(aud_path, "r", encoding="utf-8") as af:
                aud_data = json.load(af)
            cm_data = {}
            if os.path.isfile(cm_path):
                with open(cm_path, "r", encoding="utf-8") as cf:
                    cm_data = json.load(cf)
            expected_status, _ = determine_honest_status(aud_data, cm_data, kg_data)
            if res_data.get("status") != expected_status:
                errors.append(
                    f"Honest status mismatch: result.json has status='{res_data.get('status')}', expected '{expected_status}'."
                )
            if res_data.get("status") == "success" and aud_data.get("high_blocker_count", 0) > 0:
                errors.append("Status cannot be 'success' when high_blocker_count > 0.")

    passed = len(errors) == 0
    return passed, {
        "passed": passed,
        "output_dir": out_abs,
        "status": res_data.get("status"),
        "errors": errors,
        "warnings": warnings,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Validate KG Deliverables")
    parser.add_argument("--output-dir", "--output", required=True, help="Output directory to validate")
    parser.add_argument("--work-dir", "--work", default=None, help="Optional work directory for status consistency check")
    args = parser.parse_args()

    ok, rep = validate_deliverables(args.output_dir, work_dir=args.work_dir)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
