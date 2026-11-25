#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
note_auditor.py - 4-Fold Deterministic Evidence, Numeric/Polarity, Coverage & Honesty Auditor
Part of the Scientific Literature Notes Skill (task_06_notes).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import re
import argparse
import collections

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from entity_guard import load_invariants, extract_text_fingerprints, compare_claim_against_anchors, _normalize_ligatures
except ImportError:
    from .entity_guard import load_invariants, extract_text_fingerprints, compare_claim_against_anchors, _normalize_ligatures

PLACEHOLDER_LEAK_REGEX = re.compile(r"(?:\{\{[^}]+\}\}|\bTODO\b|\bTBD\b|\bplaceholder\b|\[待补充\]|待填写)", re.I)


def _norm_for_quote(text):
    t = _normalize_ligatures(text or "")
    t = t.lower()
    t = re.sub(r"-\s+", "", t)
    t = re.sub(r"[^\w\u4e00-\u9fa5]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _quote_matches_source(quote, source_texts):
    nq = _norm_for_quote(quote)
    if not nq:
        return True
    ns = _norm_for_quote(" ".join(source_texts))
    if not ns:
        return False
    if nq in ns:
        return True
    q_tokens = set(nq.split())
    s_tokens = set(ns.split())
    if q_tokens:
        overlap = len(q_tokens & s_tokens) / len(q_tokens)
        if overlap >= 0.72:
            return True
    # CJK character-bigram fallback
    q_cjk = re.findall(r"[\u4e00-\u9fa5]", nq)
    if len(q_cjk) >= 4:
        q_bi = {q_cjk[i] + q_cjk[i + 1] for i in range(len(q_cjk) - 1)}
        s_cjk = re.findall(r"[\u4e00-\u9fa5]", ns)
        s_bi = {s_cjk[i] + s_cjk[i + 1] for i in range(len(s_cjk) - 1)}
        if q_bi and len(q_bi & s_bi) / len(q_bi) >= 0.70:
            return True
    return False


def _best_matching_sentence(claim_text, anchor_texts):
    """Select the most lexically relevant sentence(s) inside anchor_texts for accurate negation check."""
    combined = " ".join(t for t in anchor_texts if t)
    sents = [s.strip() for s in re.split(r"(?<=[.?!。！？])\s+|\n+", combined) if s.strip()]
    if len(sents) <= 1:
        return combined

    c_tokens = set(re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fa5]{2}", claim_text.lower()))
    if not c_tokens:
        return combined

    scored = []
    for s in sents:
        s_tokens = set(re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fa5]{2}", s.lower()))
        overlap = len(c_tokens & s_tokens)
        scored.append((overlap, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored[0][0] < 2:
        return combined
    top_score = scored[0][0]
    best = [s for sc, s in scored[:2] if sc >= max(2, top_score * 0.70)]
    return " ".join(best) if best else combined


def _is_unpinned_long_crosslingual(probe_query, anchor_texts, quote_ok=False):
    """True when anchor_texts is a long (>=5 sents) English block summarized in Chinese without verbatim_quote and <=2 shared tokens."""
    if quote_ok:
        return False
    combined = " ".join(t for t in anchor_texts if t)
    sents = [s.strip() for s in re.split(r"(?<=[.?!。！？])\s+|\n+", combined) if s.strip()]
    if len(sents) < 5:
        return False
    has_zh_q = bool(re.search(r"[\u4e00-\u9fa5]", probe_query or ""))
    has_en_a = bool(re.search(r"[A-Za-z]{4,}", combined))
    if not (has_zh_q and has_en_a):
        return False
    q_tokens = set(re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fa5]{2}", (probe_query or "").lower()))
    max_ov = 0
    for s in sents:
        s_tokens = set(re.findall(r"[a-z0-9_]{3,}|[\u4e00-\u9fa5]{2}", s.lower()))
        ov = len(q_tokens & s_tokens)
        if ov > max_ov:
            max_ov = ov
    return max_ov <= 2


def _collect_signal_ref_keys(paper_signals):
    """Collect valid figure/table/equation identifiers from paper_signals.json."""
    keys = set()
    for doc in paper_signals.get("documents", []):
        for fig in doc.get("figures", []):
            if fig.get("label"):
                keys.add(("figure", str(fig["label"]).lower()))
        for tab in doc.get("tables", []):
            if tab.get("label"):
                keys.add(("table", str(tab["label"]).lower()))
        for eq in doc.get("equations", []):
            if eq.get("equation_number"):
                keys.add(("equation", str(eq["equation_number"]).lower()))
    return keys


def _collect_table_content_map(paper_signals):
    """Map table caption anchor_id -> bound content_anchor_ids from paper_signals."""
    out = {}
    for doc in paper_signals.get("documents", []):
        for tab in doc.get("tables", []):
            aid = tab.get("anchor_id")
            c_ids = tab.get("content_anchor_ids") or ([tab["content_anchor_id"]] if tab.get("content_anchor_id") else [])
            if aid and c_ids:
                out[aid] = c_ids
    return out


def audit_notes(work_dir, max_repair_items=15):
    """Run 4-fold deterministic audit on assembled_notes.json."""
    work_abs = os.path.abspath(work_dir)
    notes_path = os.path.join(work_abs, "assembled_notes.json")
    ev_path = os.path.join(work_abs, "evidence_index.json")
    sig_path = os.path.join(work_abs, "paper_signals.json")

    for p in (notes_path, ev_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"Required artifact missing: {p}")

    with open(notes_path, "r", encoding="utf-8") as f:
        notes = json.load(f)
    with open(ev_path, "r", encoding="utf-8") as f:
        evidence_index = json.load(f)
    paper_signals = {}
    if os.path.exists(sig_path):
        with open(sig_path, "r", encoding="utf-8") as f:
            paper_signals = json.load(f)

    invariants = load_invariants()
    anchors_list = evidence_index.get("anchors", [])
    anchor_map = {a["anchor_id"]: a for a in anchors_list if a.get("anchor_id")}
    anchor_pos = {a["anchor_id"]: idx for idx, a in enumerate(anchors_list) if a.get("anchor_id")}
    signal_refs = _collect_signal_ref_keys(paper_signals)
    table_content_map = _collect_table_content_map(paper_signals)

    issues = []

    def add_issue(severity, issue_type, target_location, target_id, message, suggested_anchor_ids=None):
        issues.append({
            "issue_id": f"AUD-{len(issues) + 1:03d}",
            "severity": severity,
            "issue_type": issue_type,
            "target_location": target_location,
            "target_id": target_id,
            "message": message,
            "suggested_anchor_ids": suggested_anchor_ids or [],
        })

    def verify_claim_item(claim_text, anchor_ids, verbatim_quote, loc, tid, check_numbers=True):
        valid_ids = []
        for aid in anchor_ids or []:
            if aid not in anchor_map:
                add_issue(
                    "critical", "ghost_anchor", loc, tid,
                    f"Ghost anchor '{aid}' referenced in {loc} ({tid}) does not exist in evidence_index.json.",
                )
            else:
                valid_ids.append(aid)

        if not valid_ids:
            add_issue(
                "high", "missing_anchor", loc, tid,
                f"Substantive claim in {loc} ({tid}) has no valid evidence anchor.",
            )
            return

        # Expand bound table bodies when a table caption anchor is cited
        expanded_ids = list(valid_ids)
        for vid in valid_ids:
            for extra_id in table_content_map.get(vid, []):
                if extra_id in anchor_map and extra_id not in expanded_ids:
                    expanded_ids.append(extra_id)

        anchor_texts = [anchor_map[vid].get("text", "") for vid in expanded_ids]

        # Collect sibling anchors in same section (±3 positions for numbers, ±1 for negation window)
        sibling_texts = []
        neg_pm1_texts = []
        seen_sibs = set(expanded_ids)
        for vid in valid_ids:
            pos = anchor_pos.get(vid)
            sec_id = anchor_map[vid].get("section_id")
            if pos is None or not sec_id:
                continue
            for probe_idx in range(max(0, pos - 3), min(len(anchors_list), pos + 4)):
                cand = anchors_list[probe_idx]
                cid = cand.get("anchor_id")
                if cid and cid not in seen_sibs and cand.get("section_id") == sec_id and cand.get("zone") != "references_locked":
                    seen_sibs.add(cid)
                    ctext = cand.get("text", "")
                    sibling_texts.append(ctext)
                    if abs(probe_idx - pos) <= 1:
                        neg_pm1_texts.append(ctext)

        if verbatim_quote and not _quote_matches_source(verbatim_quote, anchor_texts):
            add_issue(
                "high", "fabricated_quote", loc, tid,
                f"Verbatim quote in {loc} ({tid}) does not match text of cited anchor(s) {valid_ids}.",
                valid_ids,
            )

        if not claim_text:
            return

        cmp_res = compare_claim_against_anchors(
            claim_text, anchor_texts, invariants=invariants, section_context=sibling_texts
        )
        if check_numbers:
            if cmp_res["unsupported_numbers"]:
                add_issue(
                    "high", "unsupported_number", loc, tid,
                    f"Claim in {loc} ({tid}) asserts unsupported number(s) {cmp_res['unsupported_numbers']} not present in anchor(s) {valid_ids}.",
                    valid_ids,
                )
            if cmp_res.get("same_section_matched"):
                add_issue(
                    "low", "same_section_number_context", loc, tid,
                    f"Number(s) {cmp_res['same_section_matched']} in {loc} ({tid}) matched sibling anchor(s) in the same section rather than primary anchor(s) {valid_ids}.",
                    valid_ids,
                )
            if cmp_res["unit_mismatches"]:
                add_issue(
                    "high", "unit_mismatch", loc, tid,
                    f"Unit mismatch in {loc} ({tid}): {cmp_res['unit_mismatches']} vs anchor(s) {valid_ids}.",
                    valid_ids,
                )

        # Window-aware negation polarity check (target anchor + same-section ±1 window fallback)
        neg_window_texts = anchor_texts + neg_pm1_texts
        quote_ok = bool(verbatim_quote) and _quote_matches_source(verbatim_quote, anchor_texts)
        probe_query = verbatim_quote if quote_ok else claim_text
        best_sent = _best_matching_sentence(probe_query, anchor_texts)
        sent_cmp = compare_claim_against_anchors(claim_text, best_sent, invariants=invariants)
        if sent_cmp["negation_alerts"]:
            alert = sent_cmp["negation_alerts"][0]
            a_neg = alert.get("anchor_negation", [])
            c_neg = alert.get("claim_negation", [])
            if a_neg and not c_neg:
                # If note expresses an upper/lower bound qualifier (e.g. 至多/以内) for source 'never'/'no', treat as low notice
                c_qual = sent_cmp["claim_fingerprints"]["logic_markers"].get("qualifier_bound", [])
                bound_equiv = bool(c_qual) and set(a_neg).issubset({"never", "no"})
                unpinned_long = _is_unpinned_long_crosslingual(probe_query, anchor_texts, quote_ok=quote_ok)
                best_sent_count = len([s for s in re.split(r"(?<=[.?!。！？])\s+|\n+", best_sent) if s.strip()])
                is_high_level_multi_sent = loc.startswith("paper_card.") and not loc.endswith("key_results") and best_sent_count >= 3
                if bound_equiv or unpinned_long:
                    add_issue(
                        "low", "negation_polarity_notice", loc, tid,
                        f"Cross-lingual negation/bound phrasing difference in {loc} ({tid}): {sent_cmp['negation_alerts']} vs anchor(s) {valid_ids}.",
                        valid_ids,
                    )
                elif not is_high_level_multi_sent:
                    add_issue(
                        "high", "negation_polarity_mismatch", loc, tid,
                        f"Negation polarity mismatch in {loc} ({tid}): {sent_cmp['negation_alerts']} vs anchor(s) {valid_ids}.",
                        valid_ids,
                    )
            elif c_neg and not a_neg:
                # Affirmative best sentence while note has negation/modifier: check full ±1 window first
                win_cmp = compare_claim_against_anchors(claim_text, neg_window_texts, invariants=invariants)
                if not win_cmp["anchor_fingerprints"]["logic_markers"]["negation"]:
                    add_issue(
                        "low", "negation_polarity_notice", loc, tid,
                        f"Note in {loc} ({tid}) adds negation/modifier {c_neg} not explicitly flagged in ±1 window around {valid_ids}.",
                        valid_ids,
                    )

        # Cross-reference existence check (must exist in anchor OR paper_signals)
        c_refs = cmp_res["claim_fingerprints"]["cross_refs"]
        a_ref_keys = {(r["ref_type"], r["ref_id"].lower()) for r in cmp_res["anchor_fingerprints"]["cross_refs"]}
        for cr in c_refs:
            r_type = cr["ref_type"]
            r_id = cr["ref_id"].lower()
            if r_type in ("figure", "table", "equation"):
                if (r_type, r_id) not in a_ref_keys and (r_type, r_id) not in signal_refs:
                    add_issue(
                        "high", "ghost_cross_ref", loc, tid,
                        f"Ghost cross-reference '{cr['raw']}' in {loc} ({tid}) not found in cited anchor(s) {valid_ids} or paper_signals.",
                        valid_ids,
                    )

    # ------------------------------------------------------------------
    # Audit 1 & 2: paper_card, section_summaries, knowledge_points
    # ------------------------------------------------------------------
    pc = notes.get("paper_card", {})
    for field_name in ("one_line_takeaway", "research_problem", "research_gap", "methodology_summary", "limitations_and_assumptions", "reusability_and_takeaways"):
        f_obj = pc.get(field_name, {})
        if not isinstance(f_obj, dict):
            continue
        if f_obj.get("provenance_status") != "not_reported_in_source" and f_obj.get("value") not in ("", "not_reported_in_source", None):
            verify_claim_item(
                str(f_obj.get("value", "")),
                f_obj.get("anchor_ids", []),
                "",
                f"paper_card.{field_name}",
                field_name,
                check_numbers=True,
            )

    for kr in pc.get("key_results", []):
        if not isinstance(kr, dict):
            continue
        rid = kr.get("result_id", "RES-?")
        claim_str = " ".join(
            str(kr.get(k, ""))
            for k in ("metric", "value", "baseline", "condition", "summary")
            if kr.get(k) and kr.get(k) != "not_reported_in_source"
        )
        verify_claim_item(
            claim_str,
            kr.get("anchor_ids", []),
            kr.get("verbatim_quote", ""),
            "paper_card.key_results",
            rid,
            check_numbers=True,
        )

    for sec in notes.get("section_summaries", []):
        sid = sec.get("section_id", "?")
        zone = sec.get("zone", "body")
        if zone in ("references_locked", "toc", "front_matter"):
            continue
        kps = sec.get("key_points", [])
        if sec.get("summary_status") == "summarized" and not kps:
            add_issue(
                "high", "missing_anchor", f"section_summaries[{sid}]", sid,
                f"Section {sid} ('{sec.get('section_title', '')}') has a summary but zero anchor-bound key_points.",
            )
        for kp_idx, kp in enumerate(kps, start=1):
            verify_claim_item(
                kp.get("text", ""),
                kp.get("anchor_ids", []),
                kp.get("verbatim_quote", ""),
                f"section_summaries[{sid}].key_points",
                f"{sid}-P{kp_idx}",
                check_numbers=True,
            )

    for kp in notes.get("knowledge_points", []):
        kid = kp.get("kp_id", "KP-?")
        detail_part = "" if kp.get("detail") == "not_reported_in_source" else str(kp.get("detail", ""))
        claim_str = f"{kp.get('title', '')}. {kp.get('claim', '')} {detail_part}".strip()
        verify_claim_item(
            claim_str,
            kp.get("anchor_ids", []),
            kp.get("verbatim_quote", ""),
            "knowledge_points",
            kid,
            check_numbers=True,
        )

    # ------------------------------------------------------------------
    # Audit 3: Non-Locked Body Section & Core Element Coverage
    # ------------------------------------------------------------------
    sec_index_map = {s["section_id"]: s for s in evidence_index.get("sections", [])}
    sec_notes_map = {s["section_id"]: s for s in notes.get("section_summaries", [])}

    body_sections = [
        s for s in evidence_index.get("sections", [])
        if s.get("zone") not in ("references_locked", "toc", "front_matter", "appendix")
        and s.get("word_count", 0) > 0
    ]
    summarized_body = 0
    core_roles = {"abstract", "introduction", "method", "experiments", "ablation", "results", "discussion", "limitations", "conclusion"}

    for sec in body_sections:
        sid = sec["section_id"]
        sn = sec_notes_map.get(sid, {})
        if sn.get("summary_status") == "summarized" and (sn.get("summary") or sn.get("key_points")):
            summarized_body += 1
        else:
            role = sec.get("canonical_role", "body")
            if role in core_roles or sec.get("word_count", 0) >= 40:
                sec_anchors = [
                    a["anchor_id"] for a in evidence_index.get("anchors", [])
                    if a.get("section_id") == sid and a.get("block_type") != "heading"
                ][:3]
                add_issue(
                    "high", "missing_section", f"section_summaries[{sid}]", sid,
                    f"Core non-locked section {sid} ('{sec.get('title', '')}', role={role}, pages={sec.get('page_span')}) is not summarized.",
                    sec_anchors,
                )

    body_coverage = round(summarized_body / max(1, len(body_sections)), 4) if body_sections else 0.0

    # Limitation clues coverage check
    all_lim_clues = [c for d in paper_signals.get("documents", []) for c in d.get("limitation_clues", [])]
    lim_field = pc.get("limitations_and_assumptions", {})
    if all_lim_clues and (not isinstance(lim_field, dict) or lim_field.get("provenance_status") == "not_reported_in_source"):
        add_issue(
            "medium", "missing_limitations", "paper_card.limitations_and_assumptions", "limitations_and_assumptions",
            f"paper_signals detected {len(all_lim_clues)} limitation/assumption clues, but paper_card.limitations_and_assumptions is empty/not_reported.",
            [c["anchor_id"] for c in all_lim_clues[:4]],
        )

    # ------------------------------------------------------------------
    # Audit 4: Honesty, Scanned/Garble Disclosure & Placeholder Leak Scan
    # ------------------------------------------------------------------
    has_unreadable_source = False
    existing_unc_msgs = " ".join(str(u.get("message", "")) for u in notes.get("uncertainties", []))
    existing_chk_msgs = " ".join(str(c.get("description", "")) for c in notes.get("manual_review_checklist", []))

    for doc in paper_signals.get("documents", []):
        qr = doc.get("quality_risk", {})
        unext = qr.get("unextractable_pages", [])
        garb = qr.get("garbled_pages", [])
        if qr.get("is_scanned") and unext:
            has_unreadable_source = True
            if str(unext[0]) not in existing_unc_msgs or not notes.get("uncertainties"):
                add_issue(
                    "high", "omitted_source_uncertainty", "uncertainties", doc.get("doc_id", "D1"),
                    f"Unreadable scanned pages {unext} in {doc.get('source_file')} must be disclosed in uncertainties.",
                )
            if not notes.get("manual_review_checklist"):
                add_issue(
                    "high", "omitted_manual_review_checklist", "manual_review_checklist", doc.get("doc_id", "D1"),
                    f"Unreadable scanned pages {unext} require an entry in manual_review_checklist.",
                )
        if garb and not notes.get("uncertainties"):
            add_issue(
                "high", "omitted_source_uncertainty", "uncertainties", doc.get("doc_id", "D1"),
                f"Garbled pages {garb} in {doc.get('source_file')} must be disclosed in uncertainties.",
            )

    if body_coverage == 0.0 and evidence_index.get("total_anchors", 0) <= len(paper_signals.get("documents", [])) * 2:
        # E.g. scanned PDF yielded only fallback placeholder anchors
        if any(a.get("reading_policy") == "manual_review_asset" for a in evidence_index.get("anchors", [])):
            has_unreadable_source = True

    # Scan notes payload (excluding change_log) for unreplaced template placeholders
    notes_no_log = {k: v for k, v in notes.items() if k != "change_log"}
    serialized = json.dumps(notes_no_log, ensure_ascii=False)
    for m in PLACEHOLDER_LEAK_REGEX.finditer(serialized):
        add_issue(
            "high", "placeholder_leak", "assembled_notes", "placeholder",
            f"Unreplaced template placeholder '{m.group(0)}' detected in assembled_notes.json.",
        )
        break

    # Summarize counts & build repair_queue.json (top 15 critical/high/medium)
    sev_counts = collections.Counter(i["severity"] for i in issues)
    type_counts = collections.Counter(i["issue_type"] for i in issues)
    blocking_count = sev_counts.get("critical", 0) + sev_counts.get("high", 0)
    audit_passed = (blocking_count == 0)

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_issues = sorted(issues, key=lambda x: (sev_rank.get(x["severity"], 9), x["issue_id"]))
    repair_queue = [
        {
            "repair_id": f"REP-{idx:03d}",
            "severity": it["severity"],
            "issue_type": it["issue_type"],
            "target_location": it["target_location"],
            "target_id": it["target_id"],
            "message": it["message"],
            "suggested_anchor_ids": it["suggested_anchor_ids"],
        }
        for idx, it in enumerate(sorted_issues[:max_repair_items], start=1)
        if it["severity"] in ("critical", "high", "medium")
    ]

    audit_report = {
        "case_id": notes.get("case_id", "unknown"),
        "audit_passed": audit_passed,
        "has_unreadable_source": has_unreadable_source,
        "body_section_coverage": body_coverage,
        "summarized_body_sections": summarized_body,
        "total_body_sections": len(body_sections),
        "blocking_issues_count": blocking_count,
        "counts_by_severity": dict(sev_counts),
        "counts_by_type": dict(type_counts),
        "issues": issues,
    }

    audit_path = os.path.join(work_abs, "evidence_audit.json")
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit_report, f, ensure_ascii=False, indent=2)

    rq_path = os.path.join(work_abs, "repair_queue.json")
    with open(rq_path, "w", encoding="utf-8") as f:
        json.dump({
            "case_id": notes.get("case_id", "unknown"),
            "total_issues": len(issues),
            "queued_items": len(repair_queue),
            "repairs": repair_queue,
        }, f, ensure_ascii=False, indent=2)

    compact_out = {
        "status": "passed" if audit_passed else "issues_found",
        "case_id": notes.get("case_id", "unknown"),
        "audit_passed": audit_passed,
        "has_unreadable_source": has_unreadable_source,
        "body_section_coverage": body_coverage,
        "blocking_issues": blocking_count,
        "counts_by_severity": dict(sev_counts),
        "repair_queued": len(repair_queue),
    }
    print("[note_auditor] " + json.dumps(compact_out, ensure_ascii=False))
    return audit_report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="4-Fold Evidence, Numeric/Polarity & Honesty Auditor")
    parser.add_argument("--work-dir", required=True, help="Work directory containing assembled_notes.json")
    parser.add_argument("--max-repair-items", type=int, default=15, help="Max items in repair_queue.json")
    args = parser.parse_args()
    audit_notes(args.work_dir, max_repair_items=args.max_repair_items)


if __name__ == "__main__":
    main()
