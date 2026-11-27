#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
polish_verifier.py - 8-Dimension Deterministic Academic Language Polishing Auditor.
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
import style_linter


def _severity_rank(sev):
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}.get(sev, 4)


def verify_polish_run(manifest, sentences, polish_records, glossary_bundle=None):
    """
    Execute 8-dimension deterministic audit over a polishing run.

    manifest: dict from case_ingestor.ingest_case
    sentences: list of ingested sentence dicts (with sentence_id, source_text, in_scope, section_role, language)
    polish_records: dict or list keyed by sentence_id with:
      {sentence_id, polished_text, edit_types, reason, uncertainty, double_neg_allowed, source_bilingual_text}
    glossary_bundle: output from glossary_abbr_guard.load_and_audit_glossary
    """
    if isinstance(polish_records, list):
        rec_map = {str(r.get("sentence_id")): r for r in polish_records if isinstance(r, dict) and r.get("sentence_id")}
    elif isinstance(polish_records, dict):
        rec_map = polish_records
    else:
        rec_map = {}

    if glossary_bundle is None:
        glossary_bundle = glossary_abbr_guard.load_and_audit_glossary(
            manifest.get("glossary_paths_abs", []),
            manifest.get("case_data", {}),
        )

    exec_mode = manifest.get("execution_mode", "polish")
    edit_intensity = manifest.get("edit_intensity", "standard")
    target_style = manifest.get("target_style", "general_academic")

    dim_issues = {
        "dim1_coverage_scope": [],
        "dim2_invariants": [],
        "dim3_epistemic": [],
        "dim4_terminology_spelling": [],
        "dim5_abbreviations": [],
        "dim6_diff_ledger": [],
        "dim7_register_hygiene": [],
        "dim8_bilingual_uncertainty": [],
    }
    all_issues = []

    _NON_REPAIRABLE_CODES = {
        "GLOSSARY_CONFLICT_MULTI_TARGET",
        "GLOSSARY_CONFLICT_SELF_CONTRADICTION",
        "GLOSSARY_CONFLICT_ABBR",
        "ENCODING_GARBLED",
        "NO_EDITABLE_SENTENCES",
        "MISSING_MATERIAL_PLACEHOLDER",
    }

    def _is_repairable(issue_dict):
        if issue_dict.get("code") in _NON_REPAIRABLE_CODES:
            return False
        sid_v = str(issue_dict.get("sentence_id") or "").strip()
        if not sid_v or sid_v in ("GLOSSARY", "DOCUMENT"):
            return False
        return True

    def add_issue(dim_key, issue_dict):
        enriched = dict(issue_dict)
        if "repairable" not in enriched:
            enriched["repairable"] = _is_repairable(enriched)
        dim_issues[dim_key].append(enriched)
        all_issues.append(enriched)

    # Check coverage for extra/missing sentence_ids
    sent_ids = {s["sentence_id"] for s in sentences}
    for rsid in rec_map:
        if rsid not in sent_ids:
            add_issue("dim1_coverage_scope", {
                "sentence_id": rsid,
                "code": "UNKNOWN_SENTENCE_ID",
                "severity": "MEDIUM",
                "detail": f"Polish output contains unknown sentence_id '{rsid}'.",
                "repair_hint": "Emit only valid sentence_ids from sentences.json.",
            })

    merged_records = []
    edit_ratios = []
    edited_cnt = 0
    unchanged_cnt = 0
    uncertainty_items = []

    # Collect ingest anomalies into uncertainty items
    for anom in manifest.get("anomalies", []):
        if anom.get("requires_human_confirmation") or anom.get("severity") in ("HIGH", "MEDIUM"):
            uncertainty_items.append({
                "source": "ingest_anomaly",
                "code": anom["code"],
                "detail": anom["detail"],
                "requires_human_confirmation": bool(anom.get("requires_human_confirmation", True)),
            })

    # Collect glossary conflicts into Dim 4 & uncertainty items
    for conf in glossary_bundle.get("glossary_conflicts", []):
        add_issue("dim4_terminology_spelling", {
            "sentence_id": "GLOSSARY",
            "code": conf["code"],
            "severity": conf.get("severity", "HIGH"),
            "detail": conf["detail"],
            "repair_hint": "Flag conflict in uncertainty / needs_human_confirmation; do not silently pick one target.",
        })
        uncertainty_items.append({
            "source": "glossary_conflict",
            "code": conf["code"],
            "detail": conf["detail"],
            "requires_human_confirmation": True,
        })

    # Per-sentence checks (Dims 1, 2, 3, 6, 7, 8)
    for sent in sentences:
        sid = sent["sentence_id"]
        src = sent["source_text"]
        in_scope = bool(sent.get("in_scope", True))
        rec = rec_map.get(sid)

        if in_scope and rec is None and exec_mode not in ("audit_only", "qa_guard"):
            add_issue("dim1_coverage_scope", {
                "sentence_id": sid,
                "code": "COVERAGE_INCOMPLETE",
                "severity": "HIGH",
                "detail": f"In-scope sentence {sid} missing from polish output.",
                "repair_hint": f"Provide polished record for {sid}.",
            })
            pol = src
            etypes = []
            reason = ""
            unc = ""
            dbl_neg = False
        else:
            rec = rec or {}
            pol = rec.get("polished_text") if rec.get("polished_text") is not None else src
            etypes = rec.get("edit_types", [])
            reason = rec.get("reason", "")
            unc = (rec.get("uncertainty") or "").strip()
            dbl_neg = bool(rec.get("double_neg_allowed", False))

        # Scope / Mode compliance
        if not in_scope and pol != src:
            code = "QA_GUARD_VIOLATION" if exec_mode in ("audit_only", "qa_guard") else "OUT_OF_SCOPE_MODIFIED"
            add_issue("dim1_coverage_scope", {
                "sentence_id": sid,
                "code": code,
                "severity": "HIGH",
                "detail": f"Sentence {sid} was modified despite execution_mode='{exec_mode}' or in_scope=False.",
                "repair_hint": f"Restore verbatim source text for {sid}.",
            })

        # Dim 6: Diff & edit ledger alignment
        diff_info, ledger_issues = diff_engine.audit_edit_record(
            sid, src, pol, etypes, reason, edit_intensity=edit_intensity
        )
        if diff_info["is_changed"]:
            edited_cnt += 1
        else:
            unchanged_cnt += 1
        if in_scope:
            edit_ratios.append(diff_info["edit_ratio"])

        for li in ledger_issues:
            add_issue("dim6_diff_ledger", li)

        # Dim 2 & 3: Invariants & Epistemic preservation
        if exec_mode not in ("audit_only", "qa_guard") or pol != src:
            ee_issues = epistemic_entity_guard.compare_epistemic_and_invariants(
                src, pol, lang=sent.get("language"), double_neg_allowed=dbl_neg
            )
            for ei in ee_issues:
                ei_full = {"sentence_id": sid, **ei}
                if ei["code"] in ("NUMBER_UNIT_MISSING", "CITATION_MISSING", "CROSS_REF_MISSING", "MATH_LATEX_MISSING", "MATH_DELIMITER_BROKEN"):
                    add_issue("dim2_invariants", ei_full)
                else:
                    add_issue("dim3_epistemic", ei_full)

        # Dim 7: Register, Chinglish, Punctuation & Meta-leakage
        lint_hits = style_linter.lint_sentence(pol, lang=sent.get("language"), target_style=target_style, mode="post")
        for lh in lint_hits:
            if lh["code"] == "PLACEHOLDER_PRESENT":
                # Ensure placeholder was not fabricated away, and log confirmation requirement
                if not unc and "needs_human_review" not in etypes:
                    add_issue("dim8_bilingual_uncertainty", {
                        "sentence_id": sid,
                        "code": "PLACEHOLDER_UNACKNOWLEDGED",
                        "severity": "MEDIUM",
                        "detail": f"Sentence {sid} contains placeholder but lacks uncertainty note or 'needs_human_review' tag.",
                        "repair_hint": "Set uncertainty or add 'needs_human_review' to edit_types.",
                    })
                uncertainty_items.append({
                    "source": "sentence_placeholder",
                    "sentence_id": sid,
                    "code": "PLACEHOLDER_PRESENT",
                    "detail": lh["detail"],
                    "requires_human_confirmation": True,
                })
            else:
                add_issue("dim7_register_hygiene", {"sentence_id": sid, **lh})

        # Dim 8: Bilingual alignment check (when bilingual_polish mode is active)
        if exec_mode == "bilingual_polish" and in_scope:
            bilingual_src = (rec.get("source_bilingual_text") or src or "").strip()
            stripped_len = len(re.sub(r"[\s\W_]+", "", bilingual_src))
            is_short_or_heading = (sent.get("section_role") == "heading") or (sent.get("block_type") == "heading") or (stripped_len <= 5)
            if not bilingual_src or not pol.strip():
                add_issue("dim8_bilingual_uncertainty", {
                    "sentence_id": sid,
                    "code": "BILINGUAL_ALIGNMENT_MISSING",
                    "severity": "HIGH",
                    "detail": f"Missing paired text for bilingual alignment at {sid}.",
                    "repair_hint": "Provide both source and polished bilingual sentence text.",
                })
            elif not is_short_or_heading and len(pol.strip()) < max(4, int(len(bilingual_src) * 0.12)):
                add_issue("dim8_bilingual_uncertainty", {
                    "sentence_id": sid,
                    "code": "BILINGUAL_LENGTH_ANOMALY",
                    "severity": "MEDIUM",
                    "detail": f"Bilingual output suspiciously short compared to source at {sid}.",
                    "repair_hint": "Verify sentence is completely translated/polished without truncation.",
                })

        if unc:
            uncertainty_items.append({
                "source": "sentence_record",
                "sentence_id": sid,
                "code": "SENTENCE_UNCERTAINTY",
                "detail": unc,
                "requires_human_confirmation": True,
            })

        merged_records.append({
            "sentence_id": sid,
            "block_id": sent["block_id"],
            "section_role": sent["section_role"],
            "section_title": sent["section_title"],
            "in_scope": in_scope,
            "source_text": src,
            "polished_text": pol,
            "edit_types": etypes if etypes else (["minimal_preserve"] if not diff_info["is_changed"] else []),
            "reason": reason,
            "uncertainty": unc,
            "bilingual_counterpart": (rec.get("bilingual_counterpart") or "").strip(),
            "suggested_text": (rec.get("suggested_text") or "").strip(),
            "audit_notes": (rec.get("audit_notes") or "").strip(),
            "diff": diff_info,
        })

    # Dim 4: Document-wide terminology & US/UK spelling consistency
    term_issues = glossary_abbr_guard.audit_term_consistency(
        merged_records, glossary_bundle, target_style=target_style
    )
    for ti in term_issues:
        add_issue("dim4_terminology_spelling", ti)

    # Dim 5: Document-wide Abstract/Body abbreviation lifecycle
    abbr_issues = glossary_abbr_guard.audit_abbreviations(merged_records)
    for ai in abbr_issues:
        add_issue("dim5_abbreviations", ai)

    # Dim 8: Human confirmation completeness check (if declared uncertainty exists in case, verify surfaced)
    high_cnt = sum(1 for i in all_issues if i.get("severity") == "HIGH")
    med_cnt = sum(1 for i in all_issues if i.get("severity") == "MEDIUM")
    low_cnt = sum(1 for i in all_issues if i.get("severity") == "LOW")

    # Dimension scores (0..100 per dimension, purely deterministic audit metrics)
    dimension_scores = {}
    for dkey, dlist in dim_issues.items():
        penalty = sum(25 if x.get("severity") == "HIGH" else 10 if x.get("severity") == "MEDIUM" else 3 for x in dlist)
        dimension_scores[dkey] = max(0, 100 - penalty)

    overall_audit_score = round(sum(dimension_scores.values()) / max(len(dimension_scores), 1), 2)
    mean_edit_ratio = round(sum(edit_ratios) / max(len(edit_ratios), 1), 4) if edit_ratios else 0.0

    sorted_issues = sorted(all_issues, key=lambda x: (_severity_rank(x.get("severity")), str(x.get("sentence_id", ""))))
    all_high = [x for x in sorted_issues if x.get("severity") == "HIGH"]
    actionable_high = [x for x in all_high if x.get("repairable", True)]
    advisory_high = [x for x in all_high if not x.get("repairable", True)]
    top_high_issues = all_high[:15]

    report = {
        "summary": {
            "total_sentences": len(sentences),
            "edited_sentences": edited_cnt,
            "unchanged_sentences": unchanged_cnt,
            "mean_edit_ratio": mean_edit_ratio,
            "high_count": high_cnt,
            "actionable_high_count": len(actionable_high),
            "advisory_high_count": len(advisory_high),
            "medium_count": med_cnt,
            "low_count": low_cnt,
            "dimension_scores": dimension_scores,
            "overall_audit_score": overall_audit_score,
            "passed_high_gate": (high_cnt == 0),
        },
        "top_high_issues": top_high_issues,
        "actionable_top_high_issues": actionable_high[:15],
        "non_repairable_advisories": [x for x in sorted_issues if not x.get("repairable", True)][:15],
        "all_issues": sorted_issues,
        "uncertainty_items": uncertainty_items,
        "merged_records": merged_records,
        "glossary_summary": {
            "locked_term_count": len(glossary_bundle.get("locked_terms", [])),
            "conflict_count": len(glossary_bundle.get("glossary_conflicts", [])),
            "conflicts": glossary_bundle.get("glossary_conflicts", []),
        },
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="8-dimension deterministic academic polishing verifier.")
    parser.add_argument("--work-dir", required=True, help="Work directory containing ingest_manifest.json and sentences.json")
    parser.add_argument("--polished", required=True, help="JSON file of polished sentence records (list or dict)")
    parser.add_argument("--out", default=None, help="Output verification_report.json path")
    args = parser.parse_args()

    manifest = case_ingestor.load_json_safe(os.path.join(args.work_dir, "ingest_manifest.json"), {})
    sentences = case_ingestor.load_json_safe(os.path.join(args.work_dir, "sentences.json"), [])
    pol_records = case_ingestor.load_json_safe(args.polished, [])
    report = verify_polish_run(manifest, sentences, pol_records)

    out_path = args.out or os.path.join(args.work_dir, "verification_report.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(json.dumps({
        "summary": report["summary"],
        "top_high_issues": report["top_high_issues"][:5],
        "uncertainty_count": len(report["uncertainty_items"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
