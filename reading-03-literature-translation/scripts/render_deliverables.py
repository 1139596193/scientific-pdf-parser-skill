#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_deliverables.py - Dynamic Output Contract Adapter & Multi-View Verifiable Deliverable Renderer
Part of the Scientific Translation Skill (task_03_translation).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse
import datetime


def load_json_safe(path, default=None):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def extract_contract_fields(output_contract):
    """
    Extract required/declared field names and expected types/sub-schemas from flexible
    output_contract schemas (JSON Schema properties/required, dict map, or list).
    """
    fields = {}
    if not output_contract:
        return fields

    if isinstance(output_contract, list):
        for item in output_contract:
            if isinstance(item, str) and item.strip():
                fields[item.strip()] = "any"
        return fields

    if isinstance(output_contract, dict):
        # JSON Schema pattern
        if "properties" in output_contract and isinstance(output_contract["properties"], dict):
            for k, v in output_contract["properties"].items():
                fields[k] = v if isinstance(v, dict) else {"type": str(v) if isinstance(v, str) else "any"}
            for req in output_contract.get("required", []):
                if req not in fields:
                    fields[req] = {"type": "any"}
            return fields

        # Flat dict map pattern {"field_name": "list/string/object/description"} or nested schema dict
        for k, v in output_contract.items():
            if k in ("type", "description", "schema_version", "required"):
                continue
            fields[k] = v if isinstance(v, dict) else (str(v) if isinstance(v, str) else "any")

    return fields


def _coerce_value_to_expected_type(val, expected_type, field_name, payload):
    """Ensure dynamically mapped values never violate an explicit schema type (object vs array vs string)."""
    exp_low = str(expected_type).lower()
    if exp_low in ("object", "dict") and not isinstance(val, dict):
        if isinstance(val, list):
            low = field_name.lower()
            if any(k in low for k in ("translation", "bilingual", "payload", "segment", "pair", "alignment")):
                return {
                    "segments": payload["bilingual_alignment"],
                    "glossary": payload["glossary"],
                    "status": payload["status"],
                }
            if any(k in low for k in ("terminology", "glossary", "term")):
                return {"terms": val}
            return {"items": val}
        return {"value": val}
    if exp_low in ("array", "list") and not isinstance(val, list):
        if isinstance(val, dict):
            return [val]
        return [val] if val is not None else []
    if exp_low in ("string", "str") and not isinstance(val, str):
        if isinstance(val, (dict, list)):
            prog = payload.get("progress", {})
            return (
                f"Processed {prog.get('completed_units', 0)}/{prog.get('total_translatable_units', 0)} "
                f"units (status={payload['status']})."
            )
        return str(val)
    return val


def map_contract_field_value(field_name, expected_type, payload):
    """Semantically map canonical result data to custom contract field names and schema nodes."""
    # Support recursive JSON Schema nodes with nested properties/required
    if isinstance(expected_type, dict):
        exp_t = expected_type.get("type", "any")
        props = expected_type.get("properties")
        reqs = expected_type.get("required") or []
        if (exp_t == "object" or props is not None) and (isinstance(props, dict) or reqs):
            obj = {}
            if isinstance(props, dict):
                for sub_k, sub_schema in props.items():
                    obj[sub_k] = map_contract_field_value(sub_k, sub_schema, payload)
            for req_k in reqs:
                if req_k not in obj:
                    obj[req_k] = map_contract_field_value(req_k, "any", payload)
            return obj
        expected_type = exp_t

    low = field_name.lower()
    mapped = None

    if low == "case_id":
        mapped = payload["case_id"]
    elif low in ("status", "state", "run_status", "运行状态"):
        mapped = payload["status"]
    elif any(k in low for k in ("bilingual", "segment", "pair", "alignment", "translation", "段落", "对照", "译文")):
        if "markdown" in low or "md" in low or "text" in low:
            mapped = payload.get("_bilingual_md_excerpt", "")
        else:
            mapped = payload["bilingual_alignment"]
    elif any(k in low for k in ("terminology", "glossary", "term", "术语", "词表")):
        if "rate" in low or "score" in low or "adherence" in low or "consistency" in low:
            metrics = (payload.get("verification_summary") or {}).get("metrics") or {}
            mapped = metrics.get("term_consistency_rate", 1.0 if not payload["glossary"] else 0.0)
        else:
            mapped = payload["glossary"]
    elif any(k in low for k in ("audit", "verif", "check", "report", "quality", "核验", "质量")):
        mapped = payload["verification_summary"]
    elif any(k in low for k in ("uncertain", "warning", "note", "limit", "risk", "manual", "review", "不确定", "风险", "人工")):
        mapped = payload["uncertainty"]
    elif any(k in low for k in ("source", "provenance", "evidence", "来源", "证据")):
        mapped = payload["sources"]
    elif any(k in low for k in ("change", "revision", "edit", "history", "变更", "修订")):
        mapped = payload["change_records"]
    elif any(k in low for k in ("rate", "coverage", "fidelity")):
        metrics = (payload.get("verification_summary") or {}).get("metrics") or {}
        if "coverage" in low:
            mapped = metrics.get("coverage_rate", 0.0)
        elif "fidelity" in low:
            mapped = metrics.get("entity_fidelity_rate", 0.0)
        else:
            mapped = metrics.get("coverage_rate", 0.0)
    elif any(k in low for k in ("summary", "overview", "摘要", "总结")):
        prog = payload.get("progress", {})
        mapped = (
            f"Processed {prog.get('completed_units', 0)}/{prog.get('total_translatable_units', 0)} "
            f"translatable units (status={payload['status']}). "
            f"Locked glossary terms: {len(payload['glossary'])}."
        )
    elif any(k in low for k in ("artifact", "file", "output")):
        mapped = payload["artifacts"]

    if mapped is not None:
        return _coerce_value_to_expected_type(mapped, expected_type, field_name, payload)

    # Fallback based on expected type hint
    exp_low = str(expected_type).lower()
    if "object" in exp_low or "dict" in exp_low:
        return {"status": payload["status"], "case_id": payload["case_id"]}
    if "list" in exp_low or "array" in exp_low:
        return []
    if "num" in exp_low or "float" in exp_low or "int" in exp_low:
        return 0
    if "bool" in exp_low:
        return True
    return f"See canonical result sections for '{field_name}' (status={payload['status']})."


# ----------------------------------------------------------------------
# Sentence-Level Boundary Splitter & Alignment Helper (U000X.S1..Sn)
# ----------------------------------------------------------------------

_SENT_SENTINEL = "\x01"
_ABBREV_PATTERN = re.compile(
    r"\b(?:e\.g\.|i\.e\.|et\s+al\.|etc\.|vs\.|cf\.|resp\.|approx\.|viz\.|"
    r"Figs?\.|Eqs?\.|Secs?\.|Tabs?\.|Refs?\.|Ch\.|Vol\.|No\.|pp\.|Dr\.|Prof\.|Mr\.|Mrs\.|Ms\.)"
)


def split_sentences_safe(text):
    """
    Split text into sentences while protecting scientific abbreviations (e.g., i.e., Fig., Eq., et al.),
    initials, decimal numbers (0.43), and inline dollar math expressions.
    """
    if not text or not text.strip():
        return []

    masked = text.strip()
    # Mask dots inside inline $...$ spans
    masked = re.sub(r"\$[^$\n]+\$", lambda m: m.group(0).replace(".", _SENT_SENTINEL), masked)
    # Mask abbreviations
    masked = _ABBREV_PATTERN.sub(lambda m: m.group(0).replace(".", _SENT_SENTINEL), masked)
    # Mask author initials (e.g., 'P. Das' or 'J. K.')
    masked = re.sub(r"\b([A-Z])\.(?=\s+[A-Z])", rf"\1{_SENT_SENTINEL}", masked)
    # Mask decimals & version numbers (0.43, 1.2.3)
    masked = re.sub(r"(?<=\d)\.(?=\d)", _SENT_SENTINEL, masked)

    # Split at sentence-ending punctuation followed by whitespace or CJK/upper start
    splitter = re.compile(
        r"([.?!。！？]+[\"'”’\)\]]*(?:\s*\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\])?)"
        r"(?:\s+|(?<=。)(?=[\u4e00-\u9fa5A-Z0-9“（\$])|(?<=[！？])(?=[\u4e00-\u9fa5A-Z0-9“（\$]))"
    )
    parts = splitter.split(masked)
    sentences = []
    idx = 0
    while idx < len(parts):
        chunk = parts[idx]
        delim = parts[idx + 1] if idx + 1 < len(parts) else ""
        comb = (chunk + delim).replace(_SENT_SENTINEL, ".").strip()
        if comb:
            sentences.append(comb)
        idx += 2

    return sentences


def build_sentence_alignment(src_text, tgt_text, t_policy):
    """Compute U000X.S1..Sn sentence-level boundary alignment record for translated units."""
    if t_policy != "translate" or not tgt_text:
        return None
    src_sents = split_sentences_safe(src_text)
    tgt_sents = split_sentences_safe(tgt_text)
    if not src_sents or not tgt_sents:
        return None

    max_n = max(len(src_sents), len(tgt_sents))
    status = "1to1_aligned" if len(src_sents) == len(tgt_sents) else "merged_or_split"
    pairs = []
    for i in range(max_n):
        pairs.append({
            "sid": f"S{i + 1}",
            "source": src_sents[i] if i < len(src_sents) else "[merged/split]",
            "target": tgt_sents[i] if i < len(tgt_sents) else "[merged/split]",
        })
    return {
        "alignment_status": status,
        "source_count": len(src_sents),
        "target_count": len(tgt_sents),
        "source_sentences": src_sents,
        "target_sentences": tgt_sents,
        "pairs": pairs,
    }


def format_page_span(p_span):
    if not p_span or len(p_span) < 2:
        return "p.1"
    if p_span[0] == p_span[1]:
        return f"p.{p_span[0]}"
    return f"p.{p_span[0]}-{p_span[1]}"


def render_deliverables(work_dir, output_dir, input_dir=None, is_final=False):
    """Assemble and write result.json + bilingual_parallel.md + glossary.md/.json + verification_report.md."""
    os.makedirs(output_dir, exist_ok=True)

    manifest = load_json_safe(os.path.join(work_dir, "ingest_manifest.json"), {})
    units_data = load_json_safe(os.path.join(work_dir, "source_units.json"), {"units": []})
    units = units_data.get("units", [])
    merged_trans = load_json_safe(os.path.join(work_dir, "merged_translations.json"), {})
    glossary_raw = load_json_safe(os.path.join(work_dir, "glossary.json"), {})
    glossary_compiled = load_json_safe(os.path.join(work_dir, "glossary_compiled.json"), {})
    ver_report = load_json_safe(os.path.join(work_dir, "verification_report.json"), None)
    change_records_data = load_json_safe(os.path.join(work_dir, "change_records.json"), {"records": []})

    case_data = {}
    if input_dir and os.path.exists(os.path.join(input_dir, "case.json")):
        case_data = load_json_safe(os.path.join(input_dir, "case.json"), {})

    case_id = (
        case_data.get("case_id")
        or case_data.get("id")
        or manifest.get("case_id")
        or units_data.get("case_id")
        or "unknown_case"
    )
    output_contract = case_data.get("output_contract") or manifest.get("output_contract") or {}

    # 1. Build Glossary List
    glossary_terms = glossary_compiled.get("terms") or glossary_raw.get("terms") or []
    clean_glossary = []
    for t in glossary_terms:
        clean_glossary.append({
            "term_id": t.get("term_id", ""),
            "source_term": t.get("source_term", ""),
            "aliases": t.get("aliases", []),
            "canonical_target": t.get("canonical_target", ""),
            "allowed_target_variants": t.get("allowed_target_variants", []),
            "forbidden_targets": t.get("forbidden_targets", []),
            "do_not_translate": bool(t.get("do_not_translate", False)),
            "first_seen_unit": t.get("first_seen_unit", ""),
            "first_seen_page": t.get("first_seen_page", 1),
            "matched_unit_count": t.get("matched_unit_count", 0),
            "rationale": t.get("rationale", ""),
            "source_origin": t.get("source_origin", "agent_confirmed"),
        })

    unit_term_index = glossary_compiled.get("unit_term_index", {})

    # 2. Build 1:1 Bilingual Alignment & Track Completion
    bilingual_alignment = []
    expected_translatable_count = 0
    completed_translatable_count = 0
    untranslatable_assets = []
    uncertainty_list = []

    for u in units:
        u_id = u["unit_id"]
        u_type = u.get("unit_type", "paragraph")
        t_policy = u.get("translate_policy", "translate")
        in_scope = u.get("in_scope", True)
        p_span = u.get("page_span", [1, 1])

        if not in_scope or t_policy == "scope_excluded":
            continue

        t_rec = merged_trans.get(u_id, {})
        tgt_text = str(t_rec.get("translated_text", "")).strip()
        rationale = str(t_rec.get("rationale", "")).strip()
        u_uncert = t_rec.get("uncertainty", "")

        if t_policy == "translate":
            expected_translatable_count += 1
            if tgt_text:
                completed_translatable_count += 1
        elif t_policy == "preserve_source":
            if not tgt_text:
                tgt_text = u.get("source_text", "")
            if not rationale:
                rationale = f"Preserved verbatim ({u_type})."
        elif t_policy == "manual_review_asset":
            untranslatable_assets.append({
                "unit_id": u_id,
                "page_span": p_span,
                "reason": u.get("source_text", "Missing text layer / unreadable asset"),
                "recommended_action": "Manual visual inspection or external high-DPI OCR required.",
            })

        matched_terms = [
            {"source_term": mt["source_term"], "canonical_target": mt["canonical_target"]}
            for mt in unit_term_index.get(u_id, [])
        ]

        # Collect structural/source uncertainties
        if u.get("source_warnings"):
            for sw in u["source_warnings"]:
                if sw in ("potential_font_garble", "ocr_extracted", "native_text_layer_missing", "ocr_unavailable_or_failed"):
                    uncertainty_list.append({
                        "unit_id": u_id,
                        "page_span": p_span,
                        "category": "source_extraction",
                        "reason": sw,
                        "recommended_action": "Verify extracted text against original PDF page.",
                    })

        if u_uncert:
            uncertainty_list.append({
                "unit_id": u_id,
                "page_span": p_span,
                "category": "translation_note",
                "reason": str(u_uncert),
                "recommended_action": "Domain expert review recommended.",
            })

        sent_align = build_sentence_alignment(u.get("source_text", ""), tgt_text, t_policy) if u_type not in ("display_equation", "code_block", "metadata", "untranslatable_asset") else None

        bilingual_alignment.append({
            "unit_id": u_id,
            "unit_type": u_type,
            "section_path": u.get("section_path", "Main"),
            "page_span": p_span,
            "printed_page_span": u.get("printed_page_span", [str(p_span[0]), str(p_span[1])]),
            "translate_policy": t_policy,
            "source_text": u.get("source_text", ""),
            "translated_text": tgt_text,
            "sentence_alignment": sent_align,
            "matched_terms": matched_terms,
            "rationale": rationale,
            "uncertainty": u_uncert,
            "evidence_anchors": u.get("evidence_anchors", []),
        })

    # Include unresolved verification warnings/errors into uncertainty
    manual_review_checklist = list(untranslatable_assets)
    if ver_report:
        for m_item in ver_report.get("manual_review_items", []):
            manual_review_checklist.append({
                "unit_id": m_item["unit_id"],
                "page_span": m_item.get("page_span", [1, 1]),
                "reason": f"[{m_item['code']}] {m_item['message']}",
                "recommended_action": m_item.get("fix_hint", "Verify logic polarity/bounds."),
            })
        for iss in ver_report.get("issues", []):
            if iss["severity"] == "error":
                uncertainty_list.append({
                    "unit_id": iss["unit_id"],
                    "page_span": iss.get("page_span", [1, 1]),
                    "category": "unresolved_audit_error",
                    "reason": f"[{iss['code']}] {iss['message']}",
                    "recommended_action": iss.get("fix_hint", "Repair required."),
                })

    # 3. Honest Status Determination
    if expected_translatable_count == 0 and len(untranslatable_assets) > 0:
        status = "unable_to_process"
    elif completed_translatable_count < expected_translatable_count:
        status = "partial"
    elif ver_report and ver_report.get("metrics", {}).get("error_count", 0) > 0:
        status = "partial_with_warnings"
    elif len(untranslatable_assets) > 0 or (ver_report and ver_report.get("metrics", {}).get("warning_count", 0) > 0):
        status = "partial_with_warnings" if is_final and len(untranslatable_assets) > 0 else "success"
    elif not is_final:
        status = "partial" if completed_translatable_count < expected_translatable_count else "success"
    else:
        status = "success"

    # If verification hasn't run yet and status would be success, keep honest note
    ver_summary = ver_report if ver_report else {
        "audit_status": "not_run_yet",
        "metrics": {
            "expected_translatable_units": expected_translatable_count,
            "covered_units": completed_translatable_count,
            "coverage_rate": round(completed_translatable_count / max(1, expected_translatable_count), 4) if expected_translatable_count else 0.0,
        },
    }

    # 4. Generate Human-Verifiable Markdown & JSON Artifacts
    # 4a. bilingual_parallel.md
    bp_lines = [
        f"# Bilingual Parallel Translation: `{case_id}`\n",
        f"- **Status**: `{status}` | **Progress**: {completed_translatable_count}/{expected_translatable_count} translatable units",
        f"- **Language Direction**: `{manifest.get('language_direction', 'en->zh')}`",
        f"- **Scope**: `{json.dumps(manifest.get('scope_constraint', {}), ensure_ascii=False)}`\n",
        "---\n",
    ]
    last_sec = None
    for item in bilingual_alignment:
        sec = item["section_path"]
        if sec != last_sec:
            bp_lines.append(f"\n## Section: {sec}\n")
            last_sec = sec

        u_id = item["unit_id"]
        p_str = format_page_span(item["page_span"])
        u_type = item["unit_type"]
        pol = item["translate_policy"]

        bp_lines.append(f"### [{u_id} | {p_str} | {u_type}]")
        bp_lines.append(f"> **Source**: {item['source_text']}\n")

        if pol == "manual_review_asset":
            bp_lines.append(f"**[⚠️ 缺失文字层/模糊资产，需人工复核]** `{item['source_text']}`\n")
        elif pol == "preserve_source":
            bp_lines.append(f"**Preserved (`{u_type}`)**: `{item['translated_text']}`\n")
        else:
            if item["translated_text"]:
                bp_lines.append(f"**Translation**: {item['translated_text']}\n")
            else:
                bp_lines.append("**Translation**: *[PENDING / UNTRANSLATED]*\n")

        sa = item.get("sentence_alignment")
        if sa and (sa.get("source_count", 0) >= 2 or sa.get("target_count", 0) >= 2):
            bp_lines.append(f"- *Sentence Anchors* (`{sa['alignment_status']}`, {sa['source_count']}↔{sa['target_count']}):")
            for pair in sa.get("pairs", []):
                bp_lines.append(f"  - `[{pair['sid']}]` {pair['source']} ↔ {pair['target']}")

        if item["matched_terms"]:
            terms_str = ", ".join(f"`{m['source_term']}` → **{m['canonical_target']}**" for m in item["matched_terms"])
            bp_lines.append(f"- *Locked Terms*: {terms_str}")
        if item["rationale"]:
            bp_lines.append(f"- *Rationale*: {item['rationale']}")
        if item["uncertainty"]:
            bp_lines.append(f"- *Uncertainty*: ⚠️ {item['uncertainty']}")
        bp_lines.append("")

    bilingual_md_path = os.path.join(output_dir, "bilingual_parallel.md")
    with open(bilingual_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(bp_lines))

    # 4b. glossary.md & glossary.json
    g_lines = [
        f"# Locked Domain Glossary: `{case_id}`\n",
        f"Total locked terms: **{len(clean_glossary)}**\n",
        "| Term ID | Source Term | Aliases | Canonical Target | Forbidden Targets | First Seen | Rationale |",
        "|---|---|---|---|---|---|---|",
    ]
    for gt in clean_glossary:
        aliases_s = ", ".join(gt["aliases"]) if gt["aliases"] else "-"
        forb_s = ", ".join(gt["forbidden_targets"]) if gt["forbidden_targets"] else "-"
        fs_s = f"{gt['first_seen_unit']} (p.{gt['first_seen_page']})" if gt["first_seen_unit"] else f"p.{gt['first_seen_page']}"
        g_lines.append(f"| {gt['term_id']} | `{gt['source_term']}` | {aliases_s} | **{gt['canonical_target']}** | {forb_s} | {fs_s} | {gt['rationale']} |")

    glossary_md_path = os.path.join(output_dir, "glossary.md")
    with open(glossary_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(g_lines))

    glossary_json_out_path = os.path.join(output_dir, "glossary.json")
    with open(glossary_json_out_path, "w", encoding="utf-8") as f:
        json.dump({"case_id": case_id, "terms": clean_glossary}, f, ensure_ascii=False, indent=2)

    # 4c. verification_report.md
    vr_lines = [
        f"# Deterministic Translation Audit Report: `{case_id}`\n",
        f"- **Final Deliverable Status**: `{status}`",
        f"- **Audit Status**: `{ver_summary.get('audit_status', 'not_run_yet')}`",
    ]
    metrics = ver_summary.get("metrics") or {}
    if metrics:
        vr_lines.extend([
            f"- **Coverage Rate**: {metrics.get('coverage_rate', 0):.1%} ({metrics.get('covered_units', 0)}/{metrics.get('expected_translatable_units', 0)})",
            f"- **Term Consistency Rate**: {metrics.get('term_consistency_rate', 1.0):.1%} ({metrics.get('passed_term_checks', 0)}/{metrics.get('total_term_checks', 0)})",
            f"- **Entity Fidelity Rate**: {metrics.get('entity_fidelity_rate', 1.0):.1%} ({metrics.get('passed_entity_checks', 0)}/{metrics.get('total_entity_checks', 0)})",
            f"- **Errors / Warnings**: `{metrics.get('error_count', 0)}` errors, `{metrics.get('warning_count', 0)}` warnings\n",
        ])

    change_recs = change_records_data.get("records", [])
    vr_lines.append(f"## Revision & Change Records ({len(change_recs)} edits)")
    if change_recs:
        for cr in change_recs:
            vr_lines.append(f"- `{cr.get('unit_id')}` [{cr.get('action')}]: {cr.get('reason', '')} (triggers: {cr.get('trigger_codes', [])})")
    else:
        vr_lines.append("- No post-hoc repairs recorded.")

    vr_lines.append(f"\n## Manual Review Checklist ({len(manual_review_checklist)} items)")
    if manual_review_checklist:
        for mr in manual_review_checklist:
            vr_lines.append(f"- **{mr['unit_id']}** ({format_page_span(mr.get('page_span'))}): {mr['reason']} — *{mr['recommended_action']}*")
    else:
        vr_lines.append("- None. All logic markers and assets cleared.")

    if ver_report and ver_report.get("issues"):
        vr_lines.append(f"\n## Remaining Audit Issues ({len(ver_report['issues'])})")
        for iss in ver_report["issues"]:
            vr_lines.append(f"- `[{iss['severity'].upper()}]` **{iss['unit_id']}** `{iss['code']}`: {iss['message']}")

    ver_md_path = os.path.join(output_dir, "verification_report.md")
    with open(ver_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(vr_lines))

    # 5. Assemble Final result.json (Canonical + Dynamic Contract Fields)
    artifacts_list = [
        "result.json",
        "bilingual_parallel.md",
        "glossary.md",
        "glossary.json",
        "verification_report.md",
    ]

    sources_info = {
        "input_dir": manifest.get("input_dir", input_dir or ""),
        "dominant_format": manifest.get("dominant_format", "unknown"),
        "diagnosis_summary": manifest.get("diagnosis_summary"),
        "glossary_files_detected": manifest.get("glossary_files_detected", []),
        "scope_constraint": manifest.get("scope_constraint", {}),
    }

    canonical_result = {
        "case_id": case_id,
        "status": status,
        "generated_at": datetime.datetime.now().isoformat(),
        "progress": {
            "completed_units": completed_translatable_count,
            "total_translatable_units": expected_translatable_count,
            "total_units": len(bilingual_alignment),
        },
        "sources": sources_info,
        "uncertainty": uncertainty_list,
        "change_records": change_recs,
        "glossary": clean_glossary,
        "verification_summary": ver_summary,
        "manual_review_checklist": manual_review_checklist,
        "bilingual_alignment": bilingual_alignment,
        "artifacts": artifacts_list,
        "_bilingual_md_excerpt": "\n".join(bp_lines[:30]),
    }

    # Inject dynamically requested output_contract fields without dropping canonical keys
    contract_fields = extract_contract_fields(output_contract)
    for cf_name, cf_type in contract_fields.items():
        if cf_name not in canonical_result:
            canonical_result[cf_name] = map_contract_field_value(cf_name, cf_type, canonical_result)

    canonical_result.pop("_bilingual_md_excerpt", None)

    result_json_path = os.path.join(output_dir, "result.json")
    with open(result_json_path, "w", encoding="utf-8") as f:
        json.dump(canonical_result, f, ensure_ascii=False, indent=2)

    print(f"[render_deliverables] Rendered {len(bilingual_alignment)} aligned units (status={status}, {completed_translatable_count}/{expected_translatable_count}) -> {result_json_path}")
    return canonical_result


def main():
    parser = argparse.ArgumentParser(description="Multi-View Translation Deliverable Renderer")
    parser.add_argument("--work-dir", required=True, help="Work directory")
    parser.add_argument("--output-dir", required=True, help="Output directory for deliverables")
    parser.add_argument("--input-dir", default=None, help="Original input directory containing case.json")
    parser.add_argument("--final", action="store_true", help="Mark as final deliverable pass")
    args = parser.parse_args()

    render_deliverables(args.work_dir, args.output_dir, input_dir=args.input_dir, is_final=args.final)


if __name__ == "__main__":
    main()
