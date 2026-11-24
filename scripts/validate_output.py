#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_output.py - Output Contract Verification & Anti-Hallucination Evidence Auditor
Part of the Scientific Document Parser Skill for Harbor / DeepSeek Harness.
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse

FORBIDDEN_PLACEHOLDERS = [
    r"\bTODO\b",
    r"\bFIXME\b",
    r"\bMOCK\b",
    r"\bPLACEHOLDER\b",
    r"\bSAMPLE_ONLY\b",
    r"示意",
    r"占位符",
    r"请替换",
    r"<YOUR_",
]


def check_forbidden_text(data, path=""):
    """Recursively search for forbidden placeholder strings in result JSON."""
    found = []
    if isinstance(data, dict):
        for k, v in data.items():
            found.extend(check_forbidden_text(v, f"{path}.{k}" if path else k))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            found.extend(check_forbidden_text(item, f"{path}[{idx}]"))
    elif isinstance(data, str):
        for pat in FORBIDDEN_PLACEHOLDERS:
            if re.search(pat, data, re.IGNORECASE):
                found.append({
                    "location": path,
                    "matched_pattern": pat,
                    "sample": data[:100],
                })
    return found


def extract_cited_pages(data):
    """Recursively find all 'page' numbers referenced in evidence structures."""
    pages = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "page" and isinstance(v, (int, str)) and str(v).isdigit():
                pages.append(int(v))
            else:
                pages.extend(extract_cited_pages(v))
    elif isinstance(data, list):
        for item in data:
            pages.extend(extract_cited_pages(item))
    return pages


def extract_cited_texts(data):
    """Extract string values under keys named content/quote/text/evidence."""
    quotes = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k in ("content", "quote", "text", "evidence_text") and isinstance(v, str) and len(v.strip()) >= 15:
                quotes.append(v.strip())
            else:
                quotes.extend(extract_cited_texts(v))
    elif isinstance(data, list):
        for item in data:
            quotes.extend(extract_cited_texts(item))
    return quotes


def verify_text_overlap(quote, doc_corpus):
    """Check if quote has substantial substring or token overlap with doc corpus."""
    norm_quote = re.sub(r"\s+", " ", quote.lower())
    # Substring check
    if norm_quote in doc_corpus:
        return True, 1.0

    # Token overlap check (jaccard)
    quote_tokens = set(re.findall(r"[\w\u4e00-\u9fa5]{2,}", norm_quote))
    if not quote_tokens:
        return True, 1.0

    corpus_tokens = set(re.findall(r"[\w\u4e00-\u9fa5]{2,}", doc_corpus))
    intersection = quote_tokens.intersection(corpus_tokens)
    ratio = len(intersection) / len(quote_tokens)
    return (ratio >= 0.65), round(ratio, 2)


def validate_contract(case_path, result_path, doc_json_path=None):
    """Audit result.json against case.json specifications and structured_doc."""
    errors = []
    warnings = []
    suggestions = []

    # 1. Read Case JSON
    case_data = {}
    if case_path and os.path.exists(case_path):
        try:
            with open(case_path, "r", encoding="utf-8") as f:
                case_data = json.load(f)
        except Exception as e:
            warnings.append(f"Could not parse case.json: {str(e)}")
    else:
        warnings.append(f"case.json not found at: {case_path}")

    expected_case_id = (
        case_data.get("case_id")
        or case_data.get("id")
        or case_data.get("challenge_id")
    )

    # 2. Read Result JSON
    if not os.path.exists(result_path):
        errors.append(f"Result file missing: {result_path}")
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "suggestions": ["Run the processing pipeline to generate /app/output/result.json"],
        }

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            result_data = json.load(f)
    except Exception as e:
        errors.append(f"Result file is not valid JSON: {str(e)}")
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "suggestions": ["Fix JSON formatting in result.json"],
        }

    # 3. Check Case ID Matching
    result_case_id = result_data.get("case_id")
    if expected_case_id:
        if not result_case_id:
            warnings.append(f"result.json is missing 'case_id' (expected: {expected_case_id})")
            suggestions.append(f"Add 'case_id': '{expected_case_id}' to result.json top-level.")
        elif str(result_case_id) != str(expected_case_id):
            errors.append(f"case_id mismatch: got '{result_case_id}', expected '{expected_case_id}'")

    # 4. Check Status Enum
    status = result_data.get("status")
    allowed_statuses = ("success", "partial", "unverifiable", "failed")
    if not status:
        errors.append("result.json is missing required 'status' field.")
        suggestions.append("Set 'status' to one of: success, partial, unverifiable, failed.")
    elif status not in allowed_statuses:
        errors.append(f"Invalid status '{status}'. Must be one of {allowed_statuses}")

    # 5. Check Output Contract Required Fields
    output_contract = case_data.get("output_contract", {})
    required_fields = []
    if isinstance(output_contract, dict):
        required_fields = output_contract.get("required_fields", [])
    elif isinstance(output_contract, list):
        required_fields = output_contract

    for field in required_fields:
        if field not in result_data:
            errors.append(f"Missing required contract field: '{field}'")
            suggestions.append(f"Ensure result.json includes top-level key '{field}'.")

    # 6. Check Uncertainty & Evidence
    if "uncertainty" not in result_data:
        warnings.append("Missing 'uncertainty' field; Harbor requires explicit tracking of unknown/unreported items.")
        suggestions.append("Add 'uncertainty': [] (or list of limitations) to result.json.")

    # 7. Check Forbidden Placeholders
    placeholders = check_forbidden_text(result_data)
    if placeholders:
        for p in placeholders:
            errors.append(f"Found forbidden mock/placeholder in '{p['location']}': pattern '{p['matched_pattern']}'")
        suggestions.append("Remove all mock/TODO/placeholder markers before final delivery.")

    # 8. Check Page Boundaries & Corpus Alignment
    doc_data = {}
    total_pages = 1
    doc_corpus = ""

    if doc_json_path and os.path.exists(doc_json_path):
        try:
            with open(doc_json_path, "r", encoding="utf-8") as f:
                doc_data = json.load(f)
            blocks = doc_data.get("blocks", [])
            total_pages = max([b.get("page", 1) for b in blocks] or [1])
            doc_corpus = " ".join([b.get("text", "") for b in blocks]).lower()
        except Exception:
            pass

    cited_pages = extract_cited_pages(result_data)
    for p in cited_pages:
        if p < 1 or p > total_pages:
            errors.append(f"Cited page {p} is out of bounds [1, {total_pages}].")

    # 9. Hallucination / Overlap Check
    if doc_corpus:
        quotes = extract_cited_texts(result_data)
        for q in quotes:
            ok, overlap_ratio = verify_text_overlap(q, doc_corpus)
            if not ok:
                warnings.append(f"Low overlap ({overlap_ratio}) with source document for text: '{q[:60]}...'")

    is_valid = (len(errors) == 0)
    summary = "Validation PASSED" if is_valid else f"Validation FAILED ({len(errors)} errors, {len(warnings)} warnings)"

    report = {
        "valid": is_valid,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "audit_stats": {
            "cited_pages_checked": len(cited_pages),
            "doc_total_pages": total_pages,
            "placeholders_found": len(placeholders),
        },
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Output Contract & Evidence Verifier")
    parser.add_argument("--case-json", default=None, help="Path to input /input/case.json")
    parser.add_argument("--result-json", required=True, help="Path to final /app/output/result.json")
    parser.add_argument("--doc-json", default=None, help="Path to structured_doc.json")
    parser.add_argument("--out-report", required=True, help="Output path for validation_report.json")
    args = parser.parse_args()

    report = validate_contract(args.case_json, args.result_json, args.doc_json)

    out_dir = os.path.dirname(os.path.abspath(args.out_report))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    with open(args.out_report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[validate_output] {report['summary']} -> {args.out_report}")

    # Return exit code 1 if errors exist
    if not report["valid"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
