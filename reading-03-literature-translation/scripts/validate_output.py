#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_output.py - Hard Output Contract & Honesty Validator
Part of the Scientific Translation Skill (task_03_translation).
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
    re.compile(r"<insert\s+translation>", re.IGNORECASE),
    re.compile(r"\[insert\s+.*?\]", re.IGNORECASE),
    re.compile(r"\b(?:TODO|TBD)\b"),
]


def _check_json_type(val, exp_type_str):
    """Verify Python runtime value against JSON Schema / contract type strings."""
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
    """Recursively validate field existence and type against flat or nested JSON Schema contracts."""
    if isinstance(schema_node, dict):
        exp_t = schema_node.get("type", "any")
        if not _check_json_type(val, exp_t):
            add_err(
                "SCHEMA_TYPE_MISMATCH",
                f"Contract field '{path}' expects type '{exp_t}', got '{type(val).__name__}'."
            )
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
            add_err(
                "SCHEMA_TYPE_MISMATCH",
                f"Contract field '{path}' expects type '{schema_node}', got '{type(val).__name__}'."
            )


def validate_output(output_dir, input_dir=None, strict=False):
    """Run hard validation checks on output deliverables. Returns (ok, report)."""
    errors = []
    warnings = []

    def add_err(code, msg):
        errors.append({"code": code, "severity": "error", "message": msg})

    def add_warn(code, msg):
        warnings.append({"code": code, "severity": "warning", "message": msg})

    result_path = os.path.join(output_dir, "result.json")

    # 1. Check result.json existence and UTF-8 JSON validity
    if not os.path.exists(result_path):
        add_err("MISSING_RESULT_JSON", f"result.json not found in {output_dir}")
        report = {"valid": False, "errors": errors, "warnings": warnings}
        return False, report

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            result_data = json.load(f)
    except Exception as e:
        add_err("INVALID_RESULT_JSON", f"Failed to parse result.json as valid UTF-8 JSON: {str(e)}")
        report = {"valid": False, "errors": errors, "warnings": warnings}
        return False, report

    if not isinstance(result_data, dict) or not result_data:
        add_err("EMPTY_RESULT_JSON", "result.json must be a non-empty JSON object.")
        report = {"valid": False, "errors": errors, "warnings": warnings}
        return False, report

    # 2. Check case.json contract consistency
    if input_dir:
        case_json_path = os.path.join(input_dir, "case.json")
        if os.path.exists(case_json_path):
            try:
                with open(case_json_path, "r", encoding="utf-8") as f:
                    case_data = json.load(f)
                expected_case_id = case_data.get("case_id") or case_data.get("id")
                actual_case_id = result_data.get("case_id")
                if expected_case_id and actual_case_id != expected_case_id:
                    add_err("CASE_ID_MISMATCH", f"Expected case_id '{expected_case_id}', got '{actual_case_id}'.")

                output_contract = case_data.get("output_contract", {})
                req_fields = extract_contract_fields(output_contract)
                for rf, rf_schema in req_fields.items():
                    if rf not in result_data or result_data[rf] is None:
                        add_err("MISSING_CONTRACT_FIELD", f"Contract field '{rf}' is missing or null in result.json.")
                    elif isinstance(result_data[rf], str) and not result_data[rf].strip():
                        add_err("EMPTY_CONTRACT_FIELD", f"Contract string field '{rf}' is empty in result.json.")
                    else:
                        _validate_contract_node(result_data[rf], rf_schema, rf, add_err)
            except Exception as e:
                add_warn("CASE_JSON_READ_WARN", f"Could not inspect input case.json: {str(e)}")

    # 3. Check mandatory audit fields
    valid_statuses = {"success", "partial", "partial_with_warnings", "unable_to_process"}
    status = result_data.get("status")
    if status not in valid_statuses:
        add_err("INVALID_STATUS", f"status must be one of {sorted(valid_statuses)}, got '{status}'.")

    if not isinstance(result_data.get("sources"), (dict, list)) or not result_data.get("sources"):
        add_err("MISSING_SOURCES", "Mandatory field 'sources' is missing or empty.")

    if not isinstance(result_data.get("uncertainty"), list):
        add_err("INVALID_UNCERTAINTY", "Mandatory field 'uncertainty' must be a list.")

    if not isinstance(result_data.get("change_records"), list):
        add_err("INVALID_CHANGE_RECORDS", "Mandatory field 'change_records' must be a list.")

    # 4. Honesty check on status == "success"
    alignment = result_data.get("bilingual_alignment", [])
    if status == "success":
        untranslated = [
            item.get("unit_id")
            for item in alignment
            if item.get("translate_policy") == "translate" and not str(item.get("translated_text", "")).strip()
        ]
        if untranslated:
            add_err(
                "STATUS_DISHONEST",
                f"status is 'success', but {len(untranslated)} translatable units lack translations (e.g. {untranslated[:5]})."
            )

        ver_sum = result_data.get("verification_summary") or {}
        err_cnt = (ver_sum.get("metrics") or {}).get("error_count", 0)
        if err_cnt and err_cnt > 0:
            add_err(
                "STATUS_DISHONEST",
                f"status is 'success', but verification_summary reports {err_cnt} unresolved errors."
            )
        if strict and ver_sum.get("audit_status") == "not_run_yet":
            add_err("AUDIT_NOT_RUN", "Strict validation failed: final audit has not been run yet.")

    # 5. Check declared artifacts exist on disk and are non-empty
    artifacts = result_data.get("artifacts", [])
    if not artifacts:
        add_warn("NO_ARTIFACTS_DECLARED", "No artifacts declared in result.json.")
    for art in artifacts:
        art_path = os.path.join(output_dir, art)
        if not os.path.exists(art_path):
            add_err("MISSING_ARTIFACT_FILE", f"Declared artifact '{art}' does not exist on disk.")
        elif os.path.getsize(art_path) == 0:
            add_err("EMPTY_ARTIFACT_FILE", f"Declared artifact '{art}' is 0 bytes.")

    # 6. Hygiene check: scan all deliverable text files for template placeholders
    files_to_scan = [result_path] + [
        os.path.join(output_dir, a) for a in artifacts
        if a.endswith((".md", ".json")) and os.path.exists(os.path.join(output_dir, a))
    ]
    for fpath in set(files_to_scan):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            bname = os.path.basename(fpath)
            for pat in PLACEHOLDER_PATTERNS:
                m = pat.search(content)
                if m:
                    add_err("PLACEHOLDER_LEAK", f"Prohibited placeholder pattern '{m.group(0)}' found in {bname}.")
            if status == "success" and "[PENDING / UNTRANSLATED]" in content:
                add_err("DISHONEST_PENDING_MARKER", f"Found '[PENDING / UNTRANSLATED]' inside {bname} while status='success'.")
        except Exception as e:
            add_warn("SCAN_WARN", f"Could not scan {fpath}: {str(e)}")

    ok = len(errors) == 0
    report = {
        "valid": ok,
        "status_checked": status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    return ok, report


def main():
    parser = argparse.ArgumentParser(description="Hard Output Contract & Quality Validator")
    parser.add_argument("--output-dir", required=True, help="Output directory containing result.json and deliverables")
    parser.add_argument("--input-dir", default=None, help="Optional input directory containing case.json")
    parser.add_argument("--strict", action="store_true", help="Require completed verification audit for status='success'")
    args = parser.parse_args()

    ok, report = validate_output(args.output_dir, input_dir=args.input_dir, strict=args.strict)

    print(f"[validate_output] Valid={ok} | Status={report.get('status_checked')} | Errors={report.get('error_count', 0)}, Warnings={report.get('warning_count', 0)}")
    for err in report.get("errors", []):
        print(f"  [ERROR] {err['code']}: {err['message']}")
    for warn in report.get("warnings", []):
        print(f"  [WARN]  {warn['code']}: {warn['message']}")

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
