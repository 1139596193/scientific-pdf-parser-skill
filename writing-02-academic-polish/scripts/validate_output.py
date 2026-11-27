#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_output.py - Final Deliverable Gatekeeper & Integrity Verifier.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import argparse
import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import docx_redline


def _read_png_dimensions(png_path):
    with open(png_path, "rb") as f:
        header = f.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None, None
    w, h = struct.unpack("!II", header[16:24])
    return w, h


def validate_deliverables(out_dir, case_data=None, strict=False):
    """
    Validate all final output deliverables under out_dir:
    - result.json UTF-8 no BOM + LF + valid JSON
    - case_id and output_contract preservation
    - honest status (disallow 'ok' when HIGH issues, conflicts, or incomplete coverage exist)
    - zero UNLOGGED_CHANGE
    - deliverable files exist & non-empty
    - DOCX packages pass OOXML verification (and track changes check if edited > 0)
    - PNG preview exists with valid PNG magic & width/height >= 200px
    """
    errors = []
    warnings = []
    checks = {}

    res_path = os.path.join(out_dir, "result.json")
    if not os.path.isfile(res_path):
        return {"passed": False, "errors": ["Missing result.json"], "warnings": [], "checks": {}}

    raw = open(res_path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append("result.json contains UTF-8 BOM.")
    if b"\r\n" in raw:
        errors.append("result.json contains CRLF line endings instead of LF.")
    try:
        res = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return {"passed": False, "errors": [f"Invalid result.json UTF-8/JSON: {e}"], "warnings": [], "checks": {}}

    checks["result_json_encoding"] = True

    if case_data:
        expected_id = case_data.get("case_id")
        if expected_id and res.get("case_id") != expected_id:
            errors.append(f"case_id mismatch: expected '{expected_id}', got '{res.get('case_id')}'.")
        if "output_contract" in case_data and "output_contract" not in res:
            errors.append("Original case.json 'output_contract' was not preserved in result.json.")

    status = res.get("status")
    if status not in ("ok", "needs_review", "partial", "blocked"):
        errors.append(f"Invalid status '{status}'.")

    audit_rep = res.get("audit_report") or {}
    summ = audit_rep.get("summary") or {}
    top_high = audit_rep.get("top_high_issues") or []
    high_cnt = int(summ.get("high_count", 0))
    edited_cnt = int(summ.get("edited_sentences", 0))
    conflicts = res.get("glossary_conflicts") or []

    # Check honest status
    if status == "ok":
        if high_cnt > 0 or conflicts:
            errors.append(f"Dishonest status='ok' despite high_count={high_cnt}, glossary_conflicts={len(conflicts)}.")
        if any(i.get("code") == "COVERAGE_INCOMPLETE" for i in top_high):
            errors.append("Dishonest status='ok' despite COVERAGE_INCOMPLETE.")
        if any(u.get("requires_human_confirmation") for u in (res.get("uncertainty") or [])):
            errors.append("Dishonest status='ok' despite unresolved requires_human_confirmation uncertainty.")

    # Zero UNLOGGED_CHANGE
    if any(i.get("code") == "UNLOGGED_CHANGE" for i in top_high):
        errors.append("Found UNLOGGED_CHANGE in audit_report.")

    # Check declared deliverables exist & non-empty
    delivs = res.get("deliverables") or {}
    if not delivs:
        errors.append("No deliverables listed in result.json.")
    for dname, dmeta in delivs.items():
        rel_p = dmeta.get("path", dname) if isinstance(dmeta, dict) else str(dmeta)
        full_p = os.path.join(out_dir, rel_p)
        if not os.path.isfile(full_p) or os.path.getsize(full_p) <= 0:
            errors.append(f"Declared deliverable missing or empty: {rel_p}")

    # Verify DOCX packages
    clean_docx = os.path.join(out_dir, "polished_clean.docx")
    redline_docx = os.path.join(out_dir, "polished_redline.docx")
    if os.path.isfile(clean_docx):
        v_clean = docx_redline.verify_docx_package(clean_docx, expect_track_changes=False)
        checks["clean_docx"] = v_clean
        if not v_clean["valid"]:
            errors.append(f"polished_clean.docx invalid: {v_clean['missing_parts'] or v_clean['xml_parse_errors']}")
    else:
        errors.append("Missing polished_clean.docx.")

    if os.path.isfile(redline_docx):
        v_red = docx_redline.verify_docx_package(redline_docx, expect_track_changes=(edited_cnt > 0))
        checks["redline_docx"] = v_red
        if not v_red["valid"]:
            errors.append(f"polished_redline.docx invalid: {v_red['missing_parts'] or v_red['xml_parse_errors']}")
    else:
        errors.append("Missing polished_redline.docx.")

    # Verify PNG preview(s)
    png_files = [d for d in delivs if d.lower().endswith(".png")]
    if not png_files:
        errors.append("No PNG preview file declared in deliverables.")
    else:
        valid_pngs = 0
        for pn in png_files:
            pfull = os.path.join(out_dir, pn)
            if os.path.isfile(pfull):
                pw, ph = _read_png_dimensions(pfull)
                if pw is None or ph is None:
                    errors.append(f"Invalid PNG header/IHDR: {pn}")
                elif pw < 200 or ph < 200:
                    errors.append(f"PNG preview too small ({pw}x{ph} < 200x200): {pn}")
                else:
                    valid_pngs += 1
                    checks[f"png_{pn}"] = f"{pw}x{ph}"
        if valid_pngs == 0:
            errors.append("No valid PNG preview >= 200x200px found.")

    if strict and high_cnt > 0:
        warnings.append(f"strict_note: high_count={high_cnt} remains (honest status='{status}').")

    return {
        "passed": len(errors) == 0,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Validate final academic polishing deliverables.")
    parser.add_argument("--out-dir", required=True, help="Output directory containing result.json and artifacts")
    parser.add_argument("--case", default=None, help="Optional original case.json path for contract verification")
    parser.add_argument("--strict", action="store_true", help="Report non-zero HIGH count warnings")
    args = parser.parse_args()

    case_data = None
    if args.case and os.path.isfile(args.case):
        with open(args.case, "r", encoding="utf-8", errors="replace") as f:
            case_data = json.load(f)

    res = validate_deliverables(args.out_dir, case_data=case_data, strict=args.strict)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
