#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_output.py - Hard Output Contract, Artifact & Anti-Dishonest-Success Validator
Part of the Scientific Literature Notes Skill (task_06_notes).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from render_deliverables import extract_contract_fields

PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{[^}]+\}\}"),
    re.compile(r"\[insert\s+.*?\]", re.IGNORECASE),
    re.compile(r"\b(?:TODO|TBD|placeholder)\b", re.IGNORECASE),
    re.compile(r"\[待补充\]|待填写"),
]


def _check_json_type(val, exp_type_str):
    t = str(exp_type_str).strip().lower()
    if not t or t == "any":
        return True
    if t in ("object", "dict"):
        return isinstance(val, dict)
    if t in ("array", "list"):
        return isinstance(val, list)
    if t in ("string", "str"):
        return isinstance(val, str)
    if t in ("number", "float"):
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if t in ("integer", "int"):
        return isinstance(val, int) and not isinstance(val, bool)
    if t in ("boolean", "bool"):
        return isinstance(val, bool)
    return True


def _validate_contract_node(val, schema_node, path, add_err):
    """Recursively validate field existence and type against flat or nested contracts."""
    if isinstance(schema_node, dict):
        if "type" not in schema_node and "properties" not in schema_node:
            if not isinstance(val, dict):
                add_err("SCHEMA_TYPE_MISMATCH", f"Contract field '{path}' expects object, got '{type(val).__name__}'.")
                return
            for sub_k, sub_s in schema_node.items():
                if sub_k not in val or val[sub_k] is None:
                    add_err("MISSING_CONTRACT_FIELD", f"Required nested field '{path}.{sub_k}' is missing or null.")
                else:
                    _validate_contract_node(val[sub_k], sub_s, f"{path}.{sub_k}", add_err)
            return

        exp_t = schema_node.get("type", "any")
        if not _check_json_type(val, exp_t):
            add_err("SCHEMA_TYPE_MISMATCH", f"Contract field '{path}' expects type '{exp_t}', got '{type(val).__name__}'.")
            return

        if isinstance(val, dict):
            props = schema_node.get("properties") if isinstance(schema_node.get("properties"), dict) else {}
            reqs = schema_node.get("required") if isinstance(schema_node.get("required"), list) else []
            for req_k in reqs:
                if req_k not in val or val[req_k] is None:
                    add_err("MISSING_CONTRACT_FIELD", f"Required nested field '{path}.{req_k}' is missing or null.")
            for sub_k, sub_schema in props.items():
                if sub_k in val and val[sub_k] is not None:
                    _validate_contract_node(val[sub_k], sub_schema, f"{path}.{sub_k}", add_err)
    elif isinstance(schema_node, str):
        if not _check_json_type(val, schema_node):
            add_err("SCHEMA_TYPE_MISMATCH", f"Contract field '{path}' expects type '{schema_node}', got '{type(val).__name__}'.")


def validate_output(output_dir, input_dir=None, work_dir=None, strict=False):
    """Run hard validation checks on output deliverables. Returns (ok, report)."""
    errors = []
    warnings = []

    def add_err(code, msg):
        errors.append({"code": code, "severity": "error", "message": msg})

    def add_warn(code, msg):
        warnings.append({"code": code, "severity": "warning", "message": msg})

    out_abs = os.path.abspath(output_dir)
    result_path = os.path.join(out_abs, "result.json")

    if not os.path.exists(result_path):
        add_err("MISSING_RESULT_JSON", f"result.json not found in {out_abs}")
        return False, {"valid": False, "errors": errors, "warnings": warnings}

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            result_data = json.load(f)
    except Exception as e:
        add_err("INVALID_RESULT_JSON", f"Failed to parse result.json as UTF-8 JSON: {str(e)}")
        return False, {"valid": False, "errors": errors, "warnings": warnings}

    if not isinstance(result_data, dict) or not result_data:
        add_err("EMPTY_RESULT_JSON", "result.json must be a non-empty JSON object.")
        return False, {"valid": False, "errors": errors, "warnings": warnings}

    # 1. Case contract validation
    if input_dir:
        case_json_path = os.path.join(os.path.abspath(input_dir), "case.json")
        if os.path.exists(case_json_path):
            try:
                with open(case_json_path, "r", encoding="utf-8") as f:
                    case_data = json.load(f)
                expected_case_id = case_data.get("case_id") or case_data.get("id")
                actual_case_id = result_data.get("case_id")
                if expected_case_id and actual_case_id != expected_case_id:
                    add_err("CASE_ID_MISMATCH", f"Expected case_id '{expected_case_id}', got '{actual_case_id}'.")

                req_fields = extract_contract_fields(case_data.get("output_contract", {}))
                for rf, rf_schema in req_fields.items():
                    if rf not in result_data or result_data[rf] is None:
                        add_err("MISSING_CONTRACT_FIELD", f"Contract field '{rf}' is missing or null in result.json.")
                    elif isinstance(result_data[rf], str) and not result_data[rf].strip():
                        add_err("EMPTY_CONTRACT_FIELD", f"Contract string field '{rf}' is empty in result.json.")
                    else:
                        _validate_contract_node(result_data[rf], rf_schema, rf, add_err)
            except Exception as e:
                add_warn("CASE_JSON_READ_WARN", f"Could not inspect input case.json: {str(e)}")

    # 2. Mandatory canonical fields
    valid_statuses = {"success", "partial", "failed", "unable_to_process"}
    status = result_data.get("status")
    if status not in valid_statuses:
        add_err("INVALID_STATUS", f"status must be one of {sorted(valid_statuses)}, got '{status}'.")

    for req_dict_k in ("paper_card", "evidence_audit_summary"):
        if not isinstance(result_data.get(req_dict_k), dict):
            add_err("MISSING_CANONICAL_OBJECT", f"Mandatory dict field '{req_dict_k}' is missing or invalid.")

    for req_list_k in ("section_summaries", "knowledge_points", "sources_index", "uncertainty", "manual_review_checklist", "change_log", "artifacts"):
        if not isinstance(result_data.get(req_list_k), list):
            add_err("MISSING_CANONICAL_LIST", f"Mandatory list field '{req_list_k}' is missing or invalid.")

    # 3. Cross-check work_dir/evidence_audit.json & anti-dishonest-success gate
    disk_audit = None
    if work_dir:
        audit_p = os.path.join(os.path.abspath(work_dir), "evidence_audit.json")
        if os.path.exists(audit_p):
            try:
                with open(audit_p, "r", encoding="utf-8") as f:
                    disk_audit = json.load(f)
            except Exception as e:
                add_err("INVALID_AUDIT_JSON", f"Failed to load evidence_audit.json: {str(e)}")
        elif strict:
            add_err("MISSING_AUDIT_JSON", f"Strict mode requires evidence_audit.json in {work_dir}")

    aud_sum = result_data.get("evidence_audit_summary") or {}
    if status == "success":
        if not aud_sum.get("audit_run", False) or not aud_sum.get("audit_passed", False):
            add_err("STATUS_DISHONEST", "status is 'success', but evidence_audit_summary indicates audit did not run or did not pass.")
        if aud_sum.get("blocking_issues_count", 0) > 0:
            add_err("STATUS_DISHONEST", f"status is 'success', but blocking_issues_count={aud_sum.get('blocking_issues_count')}.")
        if aud_sum.get("has_unreadable_source", False):
            add_err("STATUS_DISHONEST", "status is 'success', but source has unreadable scanned pages (has_unreadable_source=True).")
        if float(aud_sum.get("body_section_coverage", 0.0)) < 0.85:
            add_err("STATUS_DISHONEST", f"status is 'success', but body_section_coverage={aud_sum.get('body_section_coverage')} < 0.85.")

        if disk_audit is not None:
            if not disk_audit.get("audit_passed", False) or int(disk_audit.get("blocking_issues_count", 0)) > 0:
                add_err("STATUS_DISHONEST", "status is 'success', but disk evidence_audit.json contains unresolved critical/high blocking issues.")
            if disk_audit.get("has_unreadable_source", False):
                add_err("STATUS_DISHONEST", "status is 'success', but disk evidence_audit.json reports unreadable scanned pages.")
            if float(disk_audit.get("body_section_coverage", 0.0)) < 0.85:
                add_err("STATUS_DISHONEST", f"status is 'success', but disk evidence_audit.json body_section_coverage={disk_audit.get('body_section_coverage')} < 0.85.")

    # 4. Artifact existence & Markdown structure check
    artifacts = result_data.get("artifacts", [])
    if "result.json" not in artifacts:
        add_warn("ARTIFACT_LIST_MISSING_RESULT", "artifacts list should include 'result.json'.")
    for art in artifacts:
        art_path = os.path.join(out_abs, art)
        if not os.path.exists(art_path):
            add_err("MISSING_ARTIFACT_FILE", f"Declared artifact '{art}' does not exist in {out_abs}.")
        elif os.path.getsize(art_path) == 0:
            add_err("EMPTY_ARTIFACT_FILE", f"Declared artifact '{art}' is 0 bytes.")

    md_path = os.path.join(out_abs, "literature_notes.md")
    if not os.path.exists(md_path):
        add_err("MISSING_MARKDOWN", "literature_notes.md not found in output directory.")
    else:
        with open(md_path, "r", encoding="utf-8") as f:
            md_txt = f.read()
        if not md_txt.lstrip().startswith("---"):
            add_warn("MARKDOWN_NO_FRONTMATTER", "literature_notes.md is missing YAML frontmatter.")
        if "## 6." not in md_txt and "Evidence Anchor Appendix" not in md_txt:
            add_warn("MARKDOWN_NO_APPENDIX", "literature_notes.md is missing the Evidence Anchor Appendix.")
        for pat in PLACEHOLDER_PATTERNS:
            m = pat.search(md_txt)
            if m:
                add_err("PLACEHOLDER_LEAK_MD", f"Unreplaced placeholder '{m.group(0)}' found in literature_notes.md.")
                break

    # 5. Placeholder leak scan in result.json (excluding change_log)
    res_no_log = {k: v for k, v in result_data.items() if k != "change_log"}
    res_str = json.dumps(res_no_log, ensure_ascii=False)
    for pat in PLACEHOLDER_PATTERNS:
        m = pat.search(res_str)
        if m:
            add_err("PLACEHOLDER_LEAK_JSON", f"Unreplaced placeholder '{m.group(0)}' found in result.json.")
            break

    ok = (len(errors) == 0)
    report = {
        "valid": ok,
        "case_id": result_data.get("case_id"),
        "status": status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    return ok, report


def main():
    parser = argparse.ArgumentParser(description="Hard Output Contract & Anti-Dishonest-Success Validator")
    parser.add_argument("--output-dir", required=True, help="Directory containing result.json and literature_notes.md")
    parser.add_argument("--input-dir", default=None, help="Optional input directory containing case.json")
    parser.add_argument("--work-dir", default=None, help="Optional work directory containing evidence_audit.json")
    parser.add_argument("--strict", action="store_true", help="Require evidence_audit.json presence")
    args = parser.parse_args()

    ok, report = validate_output(
        output_dir=args.output_dir,
        input_dir=args.input_dir,
        work_dir=args.work_dir,
        strict=args.strict,
    )
    print("[validate_output] " + json.dumps(report, ensure_ascii=False))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
