#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_deliverables.py - Dynamic Output Contract Adapter & Obsidian/Zotero Literature Note Renderer
Part of the Scientific Literature Notes Skill (task_06_notes).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime, timezone


def load_json_safe(path, default=None):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        if "properties" in output_contract and isinstance(output_contract["properties"], dict):
            for k, v in output_contract["properties"].items():
                fields[k] = v if isinstance(v, dict) else {"type": str(v) if isinstance(v, str) else "any"}
            for req in output_contract.get("required", []):
                if req not in fields:
                    fields[req] = {"type": "any"}
            return fields

        for k, v in output_contract.items():
            if k in ("type", "description", "schema_version", "required"):
                continue
            fields[k] = v if isinstance(v, dict) else (str(v) if isinstance(v, str) else "any")

    return fields


def _coerce_value_to_expected_type(val, expected_type, field_name, payload):
    """Ensure dynamically mapped values never violate explicit schema types."""
    exp_low = str(expected_type).strip().lower()
    if exp_low in ("object", "dict") and not isinstance(val, dict):
        if isinstance(val, list):
            low = field_name.lower()
            if any(k in low for k in ("section", "note", "summary", "summaries")):
                return {"section_notes": val, "status": payload["status"]}
            return {"items": val}
        return {"value": val}
    if exp_low in ("array", "list") and not isinstance(val, list):
        if isinstance(val, dict):
            if "section_summaries" in val:
                return val["section_summaries"]
            return [val]
        return [val] if val is not None else []
    if exp_low in ("string", "str") and not isinstance(val, str):
        if isinstance(val, (dict, list)):
            pc = payload.get("paper_card", {})
            takeaway = (pc.get("one_line_takeaway") or {}).get("value", "")
            return f"{takeaway} (status={payload['status']}; sections={len(payload.get('section_summaries', []))})".strip()
        return str(val)
    if exp_low in ("number", "float") and not isinstance(val, (int, float)):
        return float(payload.get("evidence_audit_summary", {}).get("body_section_coverage", 0.0))
    if exp_low in ("integer", "int") and not isinstance(val, int):
        return len(payload.get("knowledge_points", []))
    if exp_low in ("boolean", "bool") and not isinstance(val, bool):
        return bool(payload.get("evidence_audit_summary", {}).get("audit_passed", False))
    return val


def map_contract_field_value(field_name, expected_type, payload):
    """Semantically map canonical literature-notes data to custom output_contract nodes."""
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
        # Support simplified nested dict contracts like {"section_notes": "array", "paper_card": "object"}
        if "type" not in expected_type and "properties" not in expected_type:
            obj = {}
            for sub_k, sub_schema in expected_type.items():
                obj[sub_k] = map_contract_field_value(sub_k, sub_schema, payload)
            return obj
        expected_type = exp_t

    low = field_name.lower()
    mapped = None

    if low == "case_id":
        mapped = payload["case_id"]
    elif low in ("status", "state", "run_status", "运行状态"):
        mapped = payload["status"]
    elif any(k in low for k in ("paper_card", "card", "论文卡片")):
        mapped = payload["paper_card"]
    elif any(k in low for k in ("section_notes", "section_summaries", "sections", "章节")):
        mapped = payload["section_summaries"]
    elif any(k in low for k in ("knowledge", "concept", "points", "知识点")):
        mapped = payload["knowledge_points"]
    elif any(k in low for k in ("cross_doc", "synthesis", "comparison", "对比")):
        mapped = payload["cross_document_synthesis"]
    elif any(k in low for k in ("evidence_audit", "audit", "verif", "quality", "核验", "审计")):
        mapped = payload["evidence_audit_summary"]
    elif any(k in low for k in ("evidence", "provenance", "sources", "anchors", "来源", "证据")):
        mapped = payload["sources_index"]
    elif any(k in low for k in ("uncertain", "warning", "risk", "不确定", "风险")):
        mapped = payload["uncertainty"]
    elif any(k in low for k in ("manual", "checklist", "review", "人工")):
        mapped = payload["manual_review_checklist"]
    elif any(k in low for k in ("change", "revision", "history", "变更", "修订")):
        mapped = payload["change_log"]
    elif any(k in low for k in ("artifact", "file", "output")):
        mapped = payload["artifacts"]
    elif low in ("title", "paper_title", "标题"):
        mapped = (payload["paper_card"].get("title") or {}).get("value") or "Untitled"
    elif any(k in low for k in ("key_results", "findings", "results", "实验结果", "主要发现")):
        mapped = payload["paper_card"].get("key_results", [])
    elif any(k in low for k in ("limitation", "assumption", "局限", "假设")):
        mapped = (payload["paper_card"].get("limitations_and_assumptions") or {}).get("value") or "not_reported_in_source"
    elif any(k in low for k in ("methodology", "method", "方法")):
        mapped = (payload["paper_card"].get("methodology_summary") or {}).get("value") or "not_reported_in_source"
    elif any(k in low for k in ("takeaway", "one_line", "核心结论")):
        mapped = (payload["paper_card"].get("one_line_takeaway") or {}).get("value") or "not_reported_in_source"
    elif any(k in low for k in ("summary", "notes", "structured_notes", "reading_result", "overview", "摘要", "笔记", "总结")):
        exp_low = str(expected_type).lower()
        if exp_low in ("object", "dict"):
            mapped = {
                "paper_card": payload["paper_card"],
                "section_notes": payload["section_summaries"],
                "knowledge_points": payload["knowledge_points"],
            }
        elif exp_low in ("array", "list"):
            mapped = payload["section_summaries"]
        else:
            pc = payload["paper_card"]
            mapped = (pc.get("one_line_takeaway") or {}).get("value") or f"Literature notes generated (status={payload['status']})."

    if mapped is not None:
        return _coerce_value_to_expected_type(mapped, expected_type, field_name, payload)

    exp_low = str(expected_type).lower()
    if "object" in exp_low or "dict" in exp_low:
        return {"case_id": payload["case_id"], "status": payload["status"]}
    if "list" in exp_low or "array" in exp_low:
        return []
    if "num" in exp_low or "float" in exp_low or "int" in exp_low:
        return 0
    if "bool" in exp_low:
        return True
    return f"See canonical literature-notes fields for '{field_name}' (status={payload['status']})."


def _decide_honest_status(evidence_index, audit_report, notes):
    """Decide honest delivery status strictly gated by audit_report & source readability."""
    total_anchors = evidence_index.get("total_anchors", 0)
    if total_anchors == 0:
        return "failed", "No extractable anchors found in input material."

    if not audit_report:
        return "partial", "evidence_audit.json has not been run yet; cannot certify status='success'."

    has_unreadable = bool(audit_report.get("has_unreadable_source", False))
    blocking = int(audit_report.get("blocking_issues_count", 0))
    coverage = float(audit_report.get("body_section_coverage", 0.0))

    reasons = []
    if has_unreadable:
        reasons.append("source contains unreadable scanned pages without OCR")
    if blocking > 0:
        reasons.append(f"{blocking} blocking critical/high audit issue(s) remain")
    if coverage < 0.85:
        reasons.append(f"non-locked body section coverage is {coverage:.1%} (< 85.0%)")

    if reasons:
        # Check if completely empty (only unreadable asset placeholders)
        all_manual = all(
            a.get("reading_policy") == "manual_review_asset"
            for a in evidence_index.get("anchors", [])
        )
        if all_manual:
            return "partial", "All pages require manual visual review: " + "; ".join(reasons)
        return "partial", "Partial result delivered: " + "; ".join(reasons)

    return "success", (
        f"All quality gates passed: body_section_coverage={coverage:.1%}, "
        f"blocking_issues=0, source_readable=true."
    )


def _collect_cited_sources_index(notes, evidence_index, paper_signals):
    """Collect deduplicated verifiable source records for all cited anchors + limitation clues."""
    anchor_map = {a["anchor_id"]: a for a in evidence_index.get("anchors", []) if a.get("anchor_id")}
    cited_ids = []
    seen = set()

    def _add_ids(ids):
        for aid in ids or []:
            if aid in anchor_map and aid not in seen:
                seen.add(aid)
                cited_ids.append(aid)

    pc = notes.get("paper_card", {})
    for k, v in pc.items():
        if isinstance(v, dict) and "anchor_ids" in v:
            _add_ids(v.get("anchor_ids"))
    for kr in pc.get("key_results", []):
        if isinstance(kr, dict):
            _add_ids(kr.get("anchor_ids"))

    for sec in notes.get("section_summaries", []):
        for kp in sec.get("key_points", []):
            if isinstance(kp, dict):
                _add_ids(kp.get("anchor_ids"))

    for kp in notes.get("knowledge_points", []):
        if isinstance(kp, dict):
            _add_ids(kp.get("anchor_ids"))

    for doc in paper_signals.get("documents", []):
        for lc in doc.get("limitation_clues", []):
            _add_ids([lc.get("anchor_id")])

    # Sort deterministically by document, page, and anchor_id
    def _sort_key(aid):
        anc = anchor_map[aid]
        return (anc.get("doc_id", "D1"), int(anc.get("page", 1)), aid)

    sources_index = []
    for aid in sorted(cited_ids, key=_sort_key):
        anc = anchor_map[aid]
        txt = anc.get("text", "")
        sources_index.append({
            "anchor_id": aid,
            "doc_id": anc.get("doc_id", "D1"),
            "source_file": anc.get("source_file", ""),
            "page": anc.get("page", 1),
            "printed_page": (anc.get("printed_page_span") or ["1"])[0],
            "section_id": anc.get("section_id", ""),
            "section_title": anc.get("section_title", ""),
            "block_type": anc.get("block_type", "paragraph"),
            "source_excerpt": txt[:320] + ("..." if len(txt) > 320 else ""),
        })
    return sources_index


def _yaml_escape(val):
    s = str(val or "").replace("\n", " ").replace('"', '\\"').strip()
    return f'"{s}"'


def _fmt_card_val(field_obj):
    if not isinstance(field_obj, dict):
        return str(field_obj or "not_reported_in_source")
    val = field_obj.get("value")
    if isinstance(val, list):
        val_str = ", ".join(str(x) for x in val) if val else "not_reported_in_source"
    else:
        val_str = str(val or "not_reported_in_source")
    a_ids = field_obj.get("anchor_ids") or []
    if a_ids:
        refs = " ".join(f"`[{aid}]`" for aid in a_ids)
        return f"{val_str} {refs}"
    return val_str


def _render_obsidian_markdown(payload, paper_signals):
    """Generate Obsidian/Zotero-ready structured Markdown with verifiable anchor appendix."""
    pc = payload["paper_card"]
    title = (pc.get("title") or {}).get("value") or "Untitled Literature Note"
    authors = (pc.get("authors") or {}).get("value") or []
    year = (pc.get("year") or {}).get("value") or "not_reported_in_source"
    doi = (pc.get("doi") or {}).get("value") or "not_reported_in_source"
    arxiv = (pc.get("arxiv_id") or {}).get("value") or "not_reported_in_source"
    tags = pc.get("tags") or []

    key_results = pc.get("key_results", [])
    takeaway_snap = _fmt_card_val(pc.get("one_line_takeaway"))
    method_raw = str((pc.get("methodology_summary") or {}).get("value") or "not_reported_in_source").strip()
    method_snap = method_raw if len(method_raw) <= 180 else method_raw[:177].rstrip() + "..."
    method_anchors = " ".join(f"`[{aid}]`" for aid in ((pc.get("methodology_summary") or {}).get("anchor_ids") or []))
    if method_anchors:
        method_snap = f"{method_snap} {method_anchors}"

    lim_raw = str((pc.get("limitations_and_assumptions") or {}).get("value") or "not_reported_in_source").strip()
    lim_snap = lim_raw if len(lim_raw) <= 120 else lim_raw[:117].rstrip() + "..."
    lim_anchors = " ".join(f"`[{aid}]`" for aid in ((pc.get("limitations_and_assumptions") or {}).get("anchor_ids") or []))
    if lim_anchors:
        lim_snap = f"{lim_snap} {lim_anchors}"

    lines = [
        "---",
        f"title: {_yaml_escape(title)}",
        f"case_id: {_yaml_escape(payload['case_id'])}",
        f"status: {_yaml_escape(payload['status'])}",
        f"authors: {json.dumps(authors, ensure_ascii=False)}",
        f"year: {_yaml_escape(year)}",
        f"doi: {_yaml_escape(doi)}",
        f"arxiv_id: {_yaml_escape(arxiv)}",
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        f"generated_at: {_yaml_escape(_utc_now())}",
        "---",
        "",
        f"# {title}",
        "",
        f"> **Case ID**: `{payload['case_id']}` | **Delivery Status**: `{payload['status']}` — {payload['status_reason']}",
        "",
        "## ⚡ 核心速览 (Executive Snapshot)",
        "",
        f"> **一句话价值**：{takeaway_snap}",
        f"> **方法主线**：{method_snap}",
    ]
    if key_results:
        lines.append("> **Top 实证发现**：")
        for kr in key_results[:2]:
            kr_a = " ".join(f"`[{a}]`" for a in kr.get("anchor_ids", []))
            kr_line = f"{kr.get('metric', '')}: {kr.get('value', '')} (vs {kr.get('baseline', 'not_reported_in_source')}; {kr.get('summary', '')}) {kr_a}".strip()
            lines.append(f"> - {kr_line}")
    else:
        lines.append("> **Top 实证发现**：`not_reported_in_source`")
    lines.extend([
        f"> **关键局限**：{lim_snap}",
        "",
        "## 1. 统一论文卡片 (Paper Card)",
        "",
        "| 字段 | 内容与来源定位 |",
        "|-|-|",
        f"| **Title** | {_fmt_card_val(pc.get('title'))} |",
        f"| **Authors** | {_fmt_card_val(pc.get('authors'))} |",
        f"| **Year / Venue** | {_fmt_card_val(pc.get('year'))} / {_fmt_card_val(pc.get('venue_or_source'))} |",
        f"| **DOI / arXiv** | `{doi}` / `{arxiv}` |",
        f"| **Code / Links** | {_fmt_card_val(pc.get('code_urls'))} |",
        f"| **One-line Takeaway** | {_fmt_card_val(pc.get('one_line_takeaway'))} |",
        f"| **Research Problem** | {_fmt_card_val(pc.get('research_problem'))} |",
        f"| **Research Gap** | {_fmt_card_val(pc.get('research_gap'))} |",
        f"| **Methodology** | {_fmt_card_val(pc.get('methodology_summary'))} |",
        f"| **Limitations & Assumptions** | {_fmt_card_val(pc.get('limitations_and_assumptions'))} |",
        f"| **Reusability & Takeaways** | {_fmt_card_val(pc.get('reusability_and_takeaways'))} |",
        "",
    ])

    lines.append("### 关键实验结果 (Key Results)")
    lines.append("")
    if key_results:
        lines.append("| ID | Metric | Value | Baseline | Condition | Summary & Anchors |")
        lines.append("|-|-|-|-|-|-|")
        for kr in key_results:
            a_str = " ".join(f"`[{a}]`" for a in kr.get("anchor_ids", []))
            lines.append(
                f"| {kr.get('result_id', '')} | {kr.get('metric', '')} | {kr.get('value', '')} | "
                f"{kr.get('baseline', '')} | {kr.get('condition', '')} | {kr.get('summary', '')} {a_str} |"
            )
    else:
        lines.append("- `not_reported_in_source`")
    lines.append("")

    # Section 2: Section-Aligned Summaries
    lines.append("## 2. 章节对齐摘要 (Section-Aligned Summaries)")
    lines.append("")
    for sec in payload.get("section_summaries", []):
        zone = sec.get("zone", "body")
        if zone in ("front_matter", "toc"):
            continue
        p_rng = sec.get("page_range", [1, 1])
        p_label = f"p.{p_rng[0]}" if p_rng[0] == p_rng[-1] else f"p.{p_rng[0]}–{p_rng[-1]}"
        lines.append(
            f"### [{sec.get('section_id')}] {sec.get('section_title')} "
            f"(`role={sec.get('section_role')}`, `{p_label}`, `status={sec.get('summary_status')}`)"
        )
        lines.append("")
        if zone == "references_locked":
            lines.append("- *[References section locked; excluded from claim synthesis unless explicitly requested]*")
            lines.append("")
            continue
        if sec.get("summary"):
            lines.append(sec["summary"])
            lines.append("")
        for kp in sec.get("key_points", []):
            a_str = " ".join(f"`[{a}]`" for a in kp.get("anchor_ids", []))
            lines.append(f"- {kp.get('text', '')} {a_str}")
        if not sec.get("summary") and not sec.get("key_points"):
            lines.append("- *[Pending / Not summarized]*")
        lines.append("")

    # Section 3: Knowledge Points
    lines.append("## 3. 原子化知识点清单 (Atomic Knowledge Points)")
    lines.append("")
    kps = payload.get("knowledge_points", [])
    if kps:
        for kp in kps:
            a_str = " ".join(f"`[{a}]`" for a in kp.get("anchor_ids", []))
            tag_str = " ".join(f"`#{t}`" for t in kp.get("tags", []))
            lines.append(f"- **{kp.get('kp_id')}** (`{kp.get('category')}`) **{kp.get('title')}**: {kp.get('claim')} {a_str}")
            if kp.get("detail") and kp.get("detail") != "not_reported_in_source":
                lines.append(f"  - *Detail*: {kp.get('detail')} {tag_str}")
    else:
        lines.append("- *No atomic knowledge points recorded.*")
    lines.append("")

    # Cross-document synthesis if present
    cds = payload.get("cross_document_synthesis")
    if isinstance(cds, dict) and (cds.get("comparison_table") or cds.get("synthesis_notes")):
        lines.append("### 跨文献对比与综合 (Cross-Document Synthesis)")
        lines.append("")
        for sn in cds.get("synthesis_notes", []):
            if isinstance(sn, dict):
                a_str = " ".join(f"`[{a}]`" for a in sn.get("anchor_ids", []))
                lines.append(f"- {sn.get('note', sn.get('text', ''))} {a_str}")
            else:
                lines.append(f"- {sn}")
        lines.append("")

    # Section 4: Figures, Tables, Equations Inventory
    lines.append("## 4. 图表与公式速查表 (Figures, Tables & Equations Inventory)")
    lines.append("")
    figs = [f for d in paper_signals.get("documents", []) for f in d.get("figures", [])]
    tabs = [t for d in paper_signals.get("documents", []) for t in d.get("tables", [])]
    eqs = [e for d in paper_signals.get("documents", []) for e in d.get("equations", [])]

    if figs or tabs:
        lines.append("| ID | Type | Page | Section | Anchor | Caption / Content Binding |")
        lines.append("|-|-|:-:|-|-|-|")
        for f_item in figs:
            cap = f_item.get("caption", "").replace("|", "\\|").replace("\n", " ")[:140]
            lines.append(f"| {f_item['figure_id']} | Figure | {f_item['page']} | {f_item['section_id']} | `[{f_item['anchor_id']}]` | {cap} |")
        for t_item in tabs:
            cap = t_item.get("caption", "").replace("|", "\\|").replace("\n", " ")[:140]
            bound = t_item.get("content_anchor_ids") or []
            bound_str = f" (body: {', '.join(bound)})" if bound else ""
            lines.append(f"| {t_item['table_id']} | Table | {t_item['page']} | {t_item['section_id']} | `[{t_item['anchor_id']}]` | {cap}{bound_str} |")
    else:
        lines.append("- *No figure or table captions detected in source.*")
    lines.append("")
    lines.append(f"- **Detected Display Equations**: {len(eqs)}")
    if eqs:
        for eq in eqs[:12]:
            eq_num = f"Eq. ({eq['equation_number']})" if eq.get("equation_number") else eq["eq_id"]
            eq_txt = eq.get("text", "").replace("\n", " ")[:120]
            lines.append(f"  - `{eq['eq_id']}` ({eq_num}, p.{eq['page']}, `[{eq['anchor_id']}]`): `{eq_txt}`")
        if len(eqs) > 12:
            lines.append(f"  - *... and {len(eqs) - 12} more equations in `paper_signals.json`.*")
    lines.append("")

    # Section 5: Audit & Manual Review Checklist
    aud = payload["evidence_audit_summary"]
    lines.append("## 5. 真实审计统计与人工复核清单 (Audit & Manual Review)")
    lines.append("")
    lines.append(f"- **Audit Passed**: `{aud.get('audit_passed')}` | **Body Coverage**: `{aud.get('body_section_coverage', 0.0):.1%}` | **Blocking Issues**: `{aud.get('blocking_issues_count', 0)}` | **Unreadable Source**: `{aud.get('has_unreadable_source', False)}`")
    lines.append("")
    if payload.get("uncertainty"):
        lines.append("### 不确定性与风险声明 (Uncertainties)")
        for u in payload["uncertainty"]:
            lines.append(f"- **[{u.get('severity', 'medium').upper()}]** ({u.get('category', 'risk')}): {u.get('message', '')}")
        lines.append("")
    if payload.get("manual_review_checklist"):
        lines.append("### 人工复核清单 (Manual Review Checklist)")
        for chk in payload["manual_review_checklist"]:
            lines.append(f"- [ ] **{chk.get('item_id', 'CHK')}** (pages={chk.get('pages', [])}): {chk.get('description', '')}")
        lines.append("")

    # Section 6: Verifiable Evidence Anchor Appendix
    lines.append("## 6. 可回查证据锚点原文附录 (Verifiable Evidence Anchor Appendix)")
    lines.append("")
    src_idx = payload.get("sources_index", [])
    if src_idx:
        for s in src_idx:
            excerpt = s.get("source_excerpt", "").replace("\n", " ")
            lines.append(
                f"- **`[{s['anchor_id']}]`** ({s.get('source_file')}, p.{s.get('page')}, "
                f"`{s.get('section_id')}` {s.get('section_title')}, `{s.get('block_type')}`): {excerpt}"
            )
    else:
        lines.append("- *No verifiable anchors referenced.*")
    lines.append("")

    return "\n".join(lines)


def _find_custom_markdown_targets(output_contract):
    """Detect if output_contract declares a specific .md filename to mirror."""
    targets = []
    if not output_contract:
        return targets
    serialized = json.dumps(output_contract, ensure_ascii=False)
    for m in re.finditer(r'"([A-Za-z0-9_\-]+\.md)"', serialized):
        fn = m.group(1)
        if fn != "literature_notes.md" and fn not in targets:
            targets.append(fn)
    return targets


def render_deliverables(input_dir, work_dir, output_dir):
    """Render /app/output/result.json and Obsidian-ready literature_notes.md."""
    in_abs = os.path.abspath(input_dir)
    work_abs = os.path.abspath(work_dir)
    out_abs = os.path.abspath(output_dir)
    os.makedirs(out_abs, exist_ok=True)

    case_data = load_json_safe(os.path.join(in_abs, "case.json"), {}) or {}
    evidence_index = load_json_safe(os.path.join(work_abs, "evidence_index.json"), {}) or {}
    paper_signals = load_json_safe(os.path.join(work_abs, "paper_signals.json"), {}) or {}
    audit_report = load_json_safe(os.path.join(work_abs, "evidence_audit.json"), None)

    notes = load_json_safe(os.path.join(work_abs, "assembled_notes.json"), None)
    if notes is None:
        # Graceful fallback from draft_skeleton if assembler hasn't run
        draft = load_json_safe(os.path.join(work_abs, "draft_skeleton.json"), {}) or {}
        notes = {
            "case_id": draft.get("case_id") or evidence_index.get("case_id") or "literature_notes_case",
            "paper_card": {
                "title": {"value": "Untitled", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "authors": {"value": [], "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "year": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "venue_or_source": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "doi": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "arxiv_id": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "code_urls": {"value": [], "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "one_line_takeaway": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "research_problem": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "research_gap": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "methodology_summary": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "key_results": [],
                "limitations_and_assumptions": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "reusability_and_takeaways": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source", "anchor_ids": []},
                "tags": [],
            },
            "section_summaries": [
                {
                    "section_id": s["section_id"],
                    "doc_id": s.get("doc_id", "D1"),
                    "section_title": s.get("title", ""),
                    "section_role": s.get("canonical_role", "body"),
                    "zone": s.get("zone", "body"),
                    "page_range": s.get("page_span", [1, 1]),
                    "summary": "",
                    "summary_status": s.get("summary_status", "pending"),
                    "key_points": [],
                }
                for s in draft.get("section_skeleton", [])
            ],
            "knowledge_points": [],
            "cross_document_synthesis": [],
            "uncertainties": draft.get("uncertainty", []),
            "manual_review_checklist": [],
            "change_log": [],
        }

    case_id = case_data.get("case_id") or case_data.get("id") or notes.get("case_id") or evidence_index.get("case_id") or "literature_notes_case"
    output_contract = case_data.get("output_contract", {})

    status, status_reason = _decide_honest_status(evidence_index, audit_report, notes)
    sources_index = _collect_cited_sources_index(notes, evidence_index, paper_signals)

    audit_summary = {
        "audit_run": audit_report is not None,
        "audit_passed": bool(audit_report.get("audit_passed", False)) if audit_report else False,
        "has_unreadable_source": bool(audit_report.get("has_unreadable_source", False)) if audit_report else False,
        "body_section_coverage": float(audit_report.get("body_section_coverage", 0.0)) if audit_report else 0.0,
        "blocking_issues_count": int(audit_report.get("blocking_issues_count", 0)) if audit_report else 0,
        "counts_by_severity": audit_report.get("counts_by_severity", {}) if audit_report else {},
        "counts_by_type": audit_report.get("counts_by_type", {}) if audit_report else {},
    }

    artifacts = ["result.json", "literature_notes.md"]
    for extra_md in _find_custom_markdown_targets(output_contract):
        if extra_md not in artifacts:
            artifacts.append(extra_md)

    change_log = list(notes.get("change_log", []))
    change_log.append({
        "timestamp": _utc_now(),
        "action": "render_deliverables",
        "status": status,
        "reason": status_reason,
    })

    canonical_payload = {
        "case_id": case_id,
        "status": status,
        "status_reason": status_reason,
        "paper_card": notes.get("paper_card", {}),
        "section_summaries": notes.get("section_summaries", []),
        "knowledge_points": notes.get("knowledge_points", []),
        "cross_document_synthesis": notes.get("cross_document_synthesis", []),
        "sources_index": sources_index,
        "uncertainty": notes.get("uncertainties", []),
        "quality_notices": paper_signals.get("quality_notices", []),
        "manual_review_checklist": notes.get("manual_review_checklist", []),
        "evidence_audit_summary": audit_summary,
        "change_log": change_log,
        "artifacts": artifacts,
    }

    # Preserve all original output_contract fields via recursive mapping
    final_result = {}
    contract_fields = extract_contract_fields(output_contract)
    for f_name, f_schema in contract_fields.items():
        final_result[f_name] = map_contract_field_value(f_name, f_schema, canonical_payload)

    # Ensure all canonical fields are also present
    for k, v in canonical_payload.items():
        if k not in final_result:
            final_result[k] = v

    # Lock exact case_id
    final_result["case_id"] = case_id

    # Render Markdown deliverables
    md_content = _render_obsidian_markdown(canonical_payload, paper_signals)
    md_path = os.path.join(out_abs, "literature_notes.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    for extra_md in _find_custom_markdown_targets(output_contract):
        extra_path = os.path.join(out_abs, extra_md)
        with open(extra_path, "w", encoding="utf-8") as f:
            f.write(md_content)

    res_path = os.path.join(out_abs, "result.json")
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)

    print(
        "[render_deliverables] "
        + json.dumps(
            {
                "case_id": case_id,
                "status": status,
                "body_coverage": audit_summary["body_section_coverage"],
                "blocking_issues": audit_summary["blocking_issues_count"],
                "artifacts": artifacts,
                "output_dir": out_abs,
            },
            ensure_ascii=False,
        )
    )
    return final_result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Literature Notes Contract Mapper & Deliverable Renderer")
    parser.add_argument("--input-dir", required=True, help="Input directory containing case.json")
    parser.add_argument("--work-dir", required=True, help="Work directory containing assembled_notes.json and evidence_audit.json")
    parser.add_argument("--output-dir", required=True, help="Output directory for result.json and literature_notes.md")
    args = parser.parse_args()
    render_deliverables(args.input_dir, args.work_dir, args.output_dir)


if __name__ == "__main__":
    main()
