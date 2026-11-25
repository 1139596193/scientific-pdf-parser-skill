#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_output.py - Hard Output Contract, Serialization & Anti-Dishonest-Success Validator
Part of the Nanopublication Scientific Reading Skill (task_09_nanopub).
Top-level imports are strictly restricted to standard libraries and sibling modules.
"""

import os
import sys
import json
import re
import csv
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from render_deliverables import extract_contract_fields
except ImportError:
    from .render_deliverables import extract_contract_fields


PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{[^}]+\}\}"),
    re.compile(r"\[insert\s+.*?\]", re.IGNORECASE),
    re.compile(r"\b(?:TODO|TBD|placeholder)\b", re.IGNORECASE),
    re.compile(r"<fill[^>]*>", re.IGNORECASE),
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
            add_err("SCHEMA_TYPE_MISMATCH", f"Contract field '{path}' expects '{exp_t}', got '{type(val).__name__}'.")
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
            add_err("SCHEMA_TYPE_MISMATCH", f"Contract field '{path}' expects '{schema_node}', got '{type(val).__name__}'.")


def _scan_placeholders(obj, path="result"):
    hits = []
    if isinstance(obj, str):
        for pat in PLACEHOLDER_PATTERNS:
            if pat.search(obj):
                hits.append(f"{path}: '{obj[:80]}'")
                break
    elif isinstance(obj, dict):
        for k, v in obj.items():
            hits.extend(_scan_placeholders(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            hits.extend(_scan_placeholders(item, f"{path}[{idx}]"))
    return hits


def validate_trig_syntax(trig_text, expected_np_ids):
    """Validate TriG @prefix, balanced braces, string literal closure, control chars, and 4-graph presence."""
    errs = []
    if "@prefix" not in trig_text:
        errs.append("Missing @prefix declarations in nanopubs.trig.")

    # Scan characters outside string literals to check brace balance and unclosed quotes
    in_str = False
    escaped = False
    brace_depth = 0
    for idx, ch in enumerate(trig_text):
        if ord(ch) < 32 and ch not in ("\n", "\r", "\t"):
            errs.append(f"Raw control character U+{ord(ch):04X} at offset {idx} in nanopubs.trig.")
            break
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth < 0:
                    errs.append(f"Unmatched closing '}}' at offset {idx} in nanopubs.trig.")
                    break

    if in_str:
        errs.append("Unclosed string literal in nanopubs.trig.")
    if brace_depth != 0:
        errs.append(f"Unbalanced curly braces in nanopubs.trig (final depth={brace_depth}).")

    for np_id in expected_np_ids:
        safe_frag = re.sub(r"[\s<>\"{}|\\^`]", "_", np_id.replace(":", "_"))
        for suffix in ("#Head", "#assertion", "#provenance", "#pubinfo"):
            if f"{safe_frag}{suffix}" not in trig_text and f"{np_id}{suffix}" not in trig_text:
                errs.append(f"Missing named graph '{suffix}' for nanopub '{np_id}' in nanopubs.trig.")
                break

    return errs


def validate_output(output_dir, ingest_manifest_path=None, input_dir=None):
    """Run all 6 hard gates on output_dir deliverables. Returns (ok, report_dict)."""
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
        add_err("INVALID_RESULT_JSON", f"Invalid UTF-8 JSON in result.json: {e}")
        return False, {"valid": False, "errors": errors, "warnings": warnings}

    if not isinstance(result_data, dict) or not result_data:
        add_err("EMPTY_RESULT_JSON", "result.json must be a non-empty JSON object.")
        return False, {"valid": False, "errors": errors, "warnings": warnings}

    # Gate 1: Case ID, Output Contract & Placeholder check
    manifest_data = {}
    if ingest_manifest_path and os.path.exists(ingest_manifest_path):
        with open(ingest_manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
    elif input_dir and os.path.exists(os.path.join(input_dir, "case.json")):
        with open(os.path.join(input_dir, "case.json"), "r", encoding="utf-8") as f:
            c_raw = json.load(f)
            manifest_data = {"case_id": c_raw.get("case_id") or c_raw.get("id"), "output_contract": c_raw.get("output_contract", {})}

    exp_cid = manifest_data.get("case_id")
    if exp_cid and result_data.get("case_id") != exp_cid:
        add_err("CASE_ID_MISMATCH", f"Expected case_id '{exp_cid}', got '{result_data.get('case_id')}'.")
    if not result_data.get("case_id"):
        add_err("MISSING_CASE_ID", "result.json is missing case_id.")

    contract = manifest_data.get("output_contract") or {}
    c_fields = extract_contract_fields(contract)
    for fname, fschema in c_fields.items():
        if fname not in result_data or result_data[fname] is None:
            add_err("MISSING_CONTRACT_FIELD", f"Required output_contract field '{fname}' missing or null in result.json.")
        else:
            _validate_contract_node(result_data[fname], fschema, fname, add_err)

    ph_hits = _scan_placeholders(result_data)
    if ph_hits:
        add_err("PLACEHOLDER_FOUND", f"Placeholder text detected in result.json: {ph_hits[:3]}")

    # Gate 2: Anti-Dishonest-Success Check
    status_val = result_data.get("status")
    nps = result_data.get("nanopublications") or []
    qas = result_data.get("qa_answers") or []
    aud_path = os.path.join(out_abs, "audit_report.json")
    aud_data = None
    if os.path.exists(aud_path):
        try:
            with open(aud_path, "r", encoding="utf-8") as f:
                aud_data = json.load(f)
        except Exception as e:
            add_err("INVALID_AUDIT_REPORT", f"Failed to parse audit_report.json: {e}")

    if status_val == "success":
        if aud_data and aud_data.get("severity_counts", {}).get("HIGH", 0) > 0:
            add_err("DISHONEST_SUCCESS_STATUS", "result.json claims status='success' despite HIGH-severity issues in audit_report.json.")
        if any((n.get("provenance") or {}).get("alignment_status") == "QUOTE_NOT_FOUND" for n in nps):
            add_err("DISHONEST_SUCCESS_STATUS", "result.json claims status='success' despite QUOTE_NOT_FOUND nanopublication.")
        if any(q.get("answer_status") in ("not_reported", "unanswerable", "insufficient_evidence", "unverifiable") for q in qas):
            add_err("DISHONEST_SUCCESS_STATUS", "result.json claims status='success' despite not_reported/unanswerable/unverifiable QA.")
        if any(u.get("category") == "scanned_document_unreadable" and u.get("severity") == "high" for u in (result_data.get("uncertainty") or [])):
            add_err("DISHONEST_SUCCESS_STATUS", "result.json claims status='success' despite unreadable scanned PDF uncertainty.")
        if any(str(u.get("severity") or "").strip().lower() in ("high", "critical") for u in (result_data.get("uncertainty") or [])):
            add_err("DISHONEST_SUCCESS_STATUS", "result.json claims status='success' despite HIGH/CRITICAL severity uncertainty record(s).")

    expected_np_ids = [(n.get("head") or {}).get("nanopub_id", "") for n in nps]

    # Gate 3: nanopubs.jsonld
    jsonld_path = os.path.join(out_abs, "nanopubs.jsonld")
    if not os.path.exists(jsonld_path):
        add_err("MISSING_JSONLD", "nanopubs.jsonld missing in output directory.")
    else:
        try:
            with open(jsonld_path, "r", encoding="utf-8") as f:
                jld = json.load(f)
            if "@context" not in jld or "@graph" not in jld:
                add_err("INVALID_JSONLD_STRUCTURE", "nanopubs.jsonld must contain '@context' and '@graph'.")
            else:
                g = jld["@graph"]
                if len(g) != len(nps):
                    add_err("JSONLD_COUNT_MISMATCH", f"nanopubs.jsonld @graph has {len(g)} items, result.json has {len(nps)}.")
                for idx, node in enumerate(g):
                    for part in ("head", "assertion", "provenance", "pubinfo"):
                        if part not in node or not isinstance(node[part], dict):
                            add_err("JSONLD_MISSING_GRAPH", f"nanopubs.jsonld @graph[{idx}] missing '{part}' sub-graph.")
                            break
        except Exception as e:
            add_err("INVALID_JSONLD", f"Failed to parse nanopubs.jsonld: {e}")

    # Gate 4: nanopubs.trig
    trig_path = os.path.join(out_abs, "nanopubs.trig")
    if not os.path.exists(trig_path):
        add_err("MISSING_TRIG", "nanopubs.trig missing in output directory.")
    else:
        with open(trig_path, "r", encoding="utf-8") as f:
            trig_txt = f.read()
        for t_err in validate_trig_syntax(trig_txt, expected_np_ids):
            add_err("INVALID_TRIG_SYNTAX", t_err)

    # Gate 5: nanopubs.csv
    csv_path = os.path.join(out_abs, "nanopubs.csv")
    if not os.path.exists(csv_path):
        add_err("MISSING_CSV", "nanopubs.csv missing in output directory.")
    else:
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            if len(rows) != len(nps):
                add_err("CSV_COUNT_MISMATCH", f"nanopubs.csv has {len(rows)} rows, result.json has {len(nps)}.")
        except Exception as e:
            add_err("INVALID_CSV", f"Failed to parse nanopubs.csv: {e}")

    # Gate 6: report.md
    rep_path = os.path.join(out_abs, "report.md")
    if not os.path.exists(rep_path):
        add_err("MISSING_REPORT_MD", "report.md missing in output directory.")
    else:
        with open(rep_path, "r", encoding="utf-8") as f:
            rep_txt = f.read().strip()
        if len(rep_txt) < 80:
            add_err("EMPTY_REPORT_MD", "report.md is too short or empty.")
        elif nps and not re.search(r"P\d+-B\d{3}", rep_txt):
            add_warn("REPORT_MISSING_BLOCK_REFS", "report.md has nanopublications but no P{page}-B{seq} anchor citations.")

    ok = len(errors) == 0
    return ok, {
        "valid": ok,
        "output_dir": out_abs,
        "status": status_val,
        "nanopub_count": len(nps),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Hard Output Contract & Serialization Gate")
    parser.add_argument("--output-dir", required=True, help="Output directory containing deliverables")
    parser.add_argument("--ingest-manifest", default=None, help="Path to case_manifest.json / ingest_manifest.json")
    parser.add_argument("--input-dir", default=None, help="Optional input directory containing case.json")
    args = parser.parse_args()

    ok, rep = validate_output(args.output_dir, ingest_manifest_path=args.ingest_manifest, input_dir=args.input_dir)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
