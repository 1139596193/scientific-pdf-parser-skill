#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kg_auditor.py - 18-Rule Deterministic Knowledge Graph Auditor & Honest Status Gatekeeper
Separates HIGH/MEDIUM blockers from honestly preserved scientific conflicts & unreported boundaries.
Part of the Literature Knowledge Graph Skill (task_10_kg).
Top-level imports are strictly restricted to standard libraries and sibling scripts.
"""

import os
import sys
import json
import re
import collections
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from entity_guard import (
        canonicalize_entity_name,
        check_causal_strength_upgrade,
        check_epistemic_consistency,
        evaluate_entity_merge_safety,
        load_alignment_rules,
        load_ontology,
    )
    from evidence_aligner import verify_cross_paper_independence
    from pubinfo_extractor import detect_root_metadata_leakage
except ImportError:
    from .entity_guard import (
        canonicalize_entity_name,
        check_causal_strength_upgrade,
        check_epistemic_consistency,
        evaluate_entity_merge_safety,
        load_alignment_rules,
        load_ontology,
    )
    from .evidence_aligner import verify_cross_paper_independence
    from .pubinfo_extractor import detect_root_metadata_leakage


FAKE_PAGE_REGEX = re.compile(r"(?:\bpage\s*\d+|\bp\.\s*\d+|第\s*\d+\s*页)", re.I)


def _add_issue(issues, code, severity, category, target_id, message, fix_hint=""):
    issues.append({
        "issue_id": f"AUD-{len(issues) + 1:03d}",
        "code": code,
        "severity": severity,
        "category": category,
        "target_id": target_id,
        "message": message,
        "fix_hint": fix_hint,
    })


def audit_kg(assembled_kg, evidence_index=None, case_manifest=None, pubinfo_bundle=None, paper_signals=None):
    """
    Execute all 18 deterministic audit checks on assembled_kg + metadata + query results.
    Returns audit_report dict with strict separation between blocker_issues and scientific_findings.
    """
    ev_idx = evidence_index or {}
    manifest = case_manifest or {}
    pub = pubinfo_bundle or {}
    sigs = paper_signals or {}

    ontology = load_ontology()
    rules = load_alignment_rules()
    valid_entity_types = {e["id"] for e in ontology.get("entity_types", [])}
    relations_by_id = {r["id"]: r for r in ontology.get("relations", [])}
    valid_epistemic = {s["id"] for s in ontology.get("epistemic_statuses", [])}

    anchors = ev_idx.get("anchors", [])
    anchors_by_id = {a["anchor_id"]: a for a in anchors if a.get("anchor_id")}
    locator_scheme = assembled_kg.get("locator_scheme") or manifest.get("locator_scheme") or "pdf_page_block"
    is_jats = (locator_scheme == "jats_paragraph")
    registry = manifest if manifest else ev_idx

    nodes = assembled_kg.get("nodes", [])
    edges = assembled_kg.get("edges", [])
    nodes_by_id = {n["node_id"]: n for n in nodes}
    merges = assembled_kg.get("merge_decisions", [])
    non_merges = assembled_kg.get("non_merge_decisions", [])
    conflicts = assembled_kg.get("conflicts", [])
    queries = assembled_kg.get("queries", [])

    issues = []

    # 1. ROOT_METADATA_LEAKAGE
    leak_rep = pub.get("leakage_check")
    if leak_rep is None and (pub.get("sources") or assembled_kg.get("documents")):
        leak_rep = detect_root_metadata_leakage(
            pub.get("sources") or assembled_kg.get("documents"),
            root_case_metadata=manifest.get("root_case_metadata"),
        )
    if leak_rep and leak_rep.get("leakage_detected"):
        for al in leak_rep.get("alerts", []):
            _add_issue(
                issues,
                "ROOT_METADATA_LEAKAGE",
                "HIGH",
                "blocker",
                al.get("source_id", "secondary"),
                al.get("message", "Root metadata leaked onto secondary paper."),
                "Keep root case.json DOI/title/authors restricted to primary paper only; use secondary manifest/XML metadata.",
            )

    # 2. FAKE_PDF_PAGE_IN_JATS
    if is_jats:
        for n in nodes:
            for ev in n.get("evidence") or []:
                c_pg = ev.get("claimed_page_number")
                if c_pg is None:
                    c_pg = ev.get("page_number") if "page_number" in ev else ev.get("page")
                loc_str = str(ev.get("locator_id") or ev.get("paragraph_id") or "")
                if c_pg is not None or FAKE_PAGE_REGEX.search(loc_str):
                    _add_issue(
                        issues,
                        "FAKE_PDF_PAGE_IN_JATS",
                        "HIGH",
                        "blocker",
                        n["node_id"],
                        f"Node {n['node_id']} claims PDF page number '{c_pg or loc_str}' under JATS mode (no PDF pages exist).",
                        "Replace page_number with null and cite exact source_id + JATS paragraph_id via hotfix.",
                    )
        for e in edges:
            for ev in e.get("evidence") or []:
                c_pg = ev.get("claimed_page_number")
                if c_pg is None:
                    c_pg = ev.get("page_number") if "page_number" in ev else ev.get("page")
                loc_str = str(ev.get("locator_id") or ev.get("paragraph_id") or "")
                if c_pg is not None or FAKE_PAGE_REGEX.search(loc_str):
                    _add_issue(
                        issues,
                        "FAKE_PDF_PAGE_IN_JATS",
                        "HIGH",
                        "blocker",
                        e["edge_id"],
                        f"Edge {e['edge_id']} claims PDF page number '{c_pg or loc_str}' under JATS mode (no PDF pages exist).",
                        "Remove page_number (set null) and bind JATS paragraph_id via hotfix replaces_id.",
                    )
        for q in queries:
            for c in q.get("provenance_citations") or []:
                if c.get("page_number") is not None:
                    _add_issue(
                        issues,
                        "FAKE_PDF_PAGE_IN_JATS",
                        "HIGH",
                        "blocker",
                        q.get("query_id", "Q"),
                        f"Query {q.get('query_id')} citation contains non-null page_number under JATS mode.",
                        "Ensure query provenance resolves purely through JATS paragraph_id.",
                    )

    # 3. SAME_PAPER_MULTI_FORMAT_AS_CROSS_PAPER
    for m in merges:
        m_sids = list(m.get("merged_source_ids") or [])
        m_refs = list(m.get("evidence_refs") or [])
        ind = m.get("cross_paper_independence") or verify_cross_paper_independence(
            [{"source_id": s} for s in m_sids] + [r for r in m_refs if isinstance(r, (dict, str))],
            registry,
        )
        claimed_cross = bool(m.get("is_cross_paper", len(m_sids) >= 2 or len(m_refs) >= 2))
        if claimed_cross and not ind["is_truly_cross_paper"]:
            _add_issue(
                issues,
                "SAME_PAPER_MULTI_FORMAT_AS_CROSS_PAPER",
                "HIGH",
                "blocker",
                m.get("decision_id", "MERGE"),
                f"Merge decision {m.get('decision_id')} claims cross-paper evidence, but {ind['reason']}",
                "Remove invalid cross-paper merge decision via remove_merge_ids; XML twin only verifies same paper.",
            )

    for n in nodes:
        decl_sids = n.get("source_ids") or []
        raw_ev_sids = [ev.get("source_id") for ev in (n.get("evidence") or []) if ev.get("source_id")]
        ev_ind = verify_cross_paper_independence(
            (n.get("evidence") or []) + [{"source_id": s} for s in decl_sids],
            registry,
        )
        if (len(set(decl_sids)) >= 2 or len(set(raw_ev_sids)) >= 2 or n.get("is_cross_paper")) and not ev_ind["is_truly_cross_paper"]:
            if ev_ind.get("twin_collapsed_items"):
                _add_issue(
                    issues,
                    "SAME_PAPER_MULTI_FORMAT_AS_CROSS_PAPER",
                    "HIGH",
                    "blocker",
                    n["node_id"],
                    f"Node {n['node_id']} treats verification twin + JSON of same paper as cross-paper sources: {ev_ind['reason']}",
                    "Restrict source_ids to independent source_id via hotfix replaces_id.",
                )

    # 4 & 7 & 12: Nodes check (UNGROUNDED_NODE, INVALID_ENTITY_TYPE, GENERIC_TERM_OVERMERGE)
    for n in nodes:
        nid = n["node_id"]
        etype = n.get("entity_type")
        if etype not in valid_entity_types:
            _add_issue(
                issues,
                "INVALID_ENTITY_TYPE",
                "HIGH",
                "blocker",
                nid,
                f"Node {nid} has invalid entity_type '{etype}'. Must be one of {sorted(valid_entity_types)}.",
                "Fix entity_type via hotfix replaces_id.",
            )

        ev_list = n.get("evidence") or []
        if not ev_list or not any((ev.get("alignment") or {}).get("aligned") and ev.get("verbatim_quote") for ev in ev_list):
            _add_issue(
                issues,
                "UNGROUNDED_NODE",
                "HIGH",
                "blocker",
                nid,
                f"Node {nid} ('{n.get('canonical_name')}') lacks aligned verbatim source evidence.",
                "Attach exact source_id, paragraph_id/anchor_id, and verbatim_quote from source text.",
            )

        ind_sids = n.get("independent_source_ids") or []
        if len(ind_sids) >= 2:
            # Verify cross-paper merge safety for shared node
            eval_res = evaluate_entity_merge_safety(
                {
                    "name": n.get("canonical_name", ""),
                    "entity_type": etype or "",
                    "source_id": ind_sids[0],
                    "condition": n.get("context_scope", ""),
                    "acronym_expansion": n.get("acronym_expansion", ""),
                },
                {
                    "name": n.get("canonical_name", ""),
                    "entity_type": etype or "",
                    "source_id": ind_sids[1],
                    "condition": n.get("context_scope", ""),
                    "acronym_expansion": n.get("acronym_expansion", ""),
                },
                rules=rules,
            )
            if eval_res["verdict"] == "context_conflict_do_not_merge":
                _add_issue(
                    issues,
                    "GENERIC_TERM_OVERMERGE",
                    "HIGH",
                    "blocker",
                    nid,
                    f"Node {nid} ('{n.get('canonical_name')}') improperly merges context-sensitive term across {ind_sids}: {eval_res['reason']}",
                    "Split into paper-scoped nodes (e.g. N-P1 / N-P2) and log in non_merge_decisions.",
                )

    # Also check merge_decisions against GENERIC_TERM_OVERMERGE
    for m in merges:
        cnid = m.get("canonical_node_id")
        cnode = nodes_by_id.get(cnid) if cnid else None
        mentions = m.get("merged_mentions") or []
        m_sids = m.get("merged_source_ids") or []
        if len(m_sids) >= 2:
            term_to_check = (cnode or {}).get("canonical_name") or (mentions[0] if mentions else "")
            if term_to_check:
                ev_res = evaluate_entity_merge_safety(
                    {"name": term_to_check, "entity_type": (cnode or {}).get("entity_type", ""), "source_id": m_sids[0]},
                    {"name": mentions[1] if len(mentions) > 1 else term_to_check, "entity_type": (cnode or {}).get("entity_type", ""), "source_id": m_sids[1]},
                    rules=rules,
                )
                if ev_res["verdict"] == "context_conflict_do_not_merge":
                    _add_issue(
                        issues,
                        "GENERIC_TERM_OVERMERGE",
                        "HIGH",
                        "blocker",
                        m.get("decision_id", cnid or "MERGE"),
                        f"Merge decision {m.get('decision_id')} merges generic/context-sensitive term '{term_to_check}': {ev_res['reason']}",
                        "Remove merge decision and split node per paper via hotfix.",
                    )

    # 5, 6, 8, 9, 10, 11: Edges checks
    degree = collections.Counter()
    for e in edges:
        eid = e["edge_id"]
        sid = e.get("subject_id")
        oid = e.get("object_id")
        rel_name = e.get("relation", "")
        ep_status = e.get("epistemic_status", "")

        if sid not in nodes_by_id or oid not in nodes_by_id:
            _add_issue(
                issues,
                "DANGLING_EDGE_ENDPOINT",
                "HIGH",
                "blocker",
                eid,
                f"Edge {eid} references missing endpoint(s): subject={sid}, object={oid}.",
                "Ensure both subject_id and object_id exist in nodes or remove edge.",
            )
        else:
            degree[sid] += 1
            degree[oid] += 1

        if rel_name == "co_occurrence_only" or ep_status == "co_occurrence_only" or rel_name not in relations_by_id:
            _add_issue(
                issues,
                "INVALID_RELATION_TYPE",
                "HIGH",
                "blocker",
                eid,
                f"Edge {eid} uses invalid or forbidden relation '{rel_name}' / epistemic_status '{ep_status}'.",
                "Use one of the 35 controlled predicates and 4 valid epistemic_statuses (co_occurrence_only is forbidden).",
            )
        elif ep_status not in valid_epistemic:
            _add_issue(
                issues,
                "INVALID_RELATION_TYPE",
                "HIGH",
                "blocker",
                eid,
                f"Edge {eid} uses unrecognized epistemic_status '{ep_status}'.",
                f"Use one of {sorted(valid_epistemic)}.",
            )
        else:
            rel_def = relations_by_id[rel_name]
            subj_node = nodes_by_id.get(sid)
            obj_node = nodes_by_id.get(oid)
            if subj_node and obj_node:
                st = subj_node.get("entity_type")
                ot = obj_node.get("entity_type")
                if st in valid_entity_types and ot in valid_entity_types:
                    if st not in rel_def.get("allowed_subject_types", []) or ot not in rel_def.get("allowed_object_types", []):
                        _add_issue(
                            issues,
                            "RELATION_TYPE_CONSTRAINT_VIOLATION",
                            "MEDIUM",
                            "quality",
                            eid,
                            f"Edge {eid} ({st} -[{rel_name}]-> {ot}) violates ontology domain/range {rel_def.get('allowed_subject_types')} -> {rel_def.get('allowed_object_types')}.",
                            "Verify relation direction or entity_type assignment.",
                        )

        ev_list = e.get("evidence") or []
        aligned_evs = [ev for ev in ev_list if (ev.get("alignment") or {}).get("aligned") and ev.get("verbatim_quote")]
        if not ev_list or not aligned_evs:
            _add_issue(
                issues,
                "UNGROUNDED_EDGE",
                "HIGH",
                "blocker",
                eid,
                f"Edge {eid} ({sid} -[{rel_name}]-> {oid}) lacks aligned verbatim quote in source blocks.",
                "Fix locator_id or verbatim_quote via hotfix replaces_id.",
            )
        else:
            for ev in aligned_evs:
                aid = ev.get("resolved_anchor_id")
                blk = anchors_by_id.get(aid, {})
                blk_sigs = {
                    "section_role": ev.get("section_role") or blk.get("section_role", ""),
                    "has_citation_signal": blk.get("has_citation_signal", False),
                    "has_citation_markers": blk.get("has_citation_markers", False),
                    "has_speculation_signal": blk.get("has_speculation_signal", False),
                    "text": blk.get("text", ""),
                }
                ep_chk = check_epistemic_consistency(
                    {"predicate": rel_name, "epistemic_status": ep_status, "evidence_quote": ev.get("verbatim_quote", "")},
                    blk_sigs,
                )
                if not ep_chk["consistent"]:
                    for ep_i in ep_chk["issues"]:
                        code_mapped = "CAUSAL_STRENGTH_OVERCLAIM" if ep_i["code"] == "EPISTEMIC_CAUSAL_OVERCLAIM" else "EPISTEMIC_UPGRADE_VIOLATION"
                        _add_issue(
                            issues,
                            code_mapped,
                            "HIGH",
                            "blocker",
                            eid,
                            f"Edge {eid}: {ep_i['message']}",
                            "Downgrade epistemic_status to reported_prior_work/author_speculation or use associative predicate.",
                        )
                else:
                    # Explicit causal strength upgrade check against relation verb
                    up = check_causal_strength_upgrade(f"Subject {rel_name} Object", ev.get("verbatim_quote", ""))
                    if up["upgraded"]:
                        _add_issue(
                            issues,
                            "CAUSAL_STRENGTH_OVERCLAIM",
                            "HIGH",
                            "blocker",
                            eid,
                            f"Edge {eid}: {up['reason']}",
                            "Change relation from strong causal predicate to associative/hedged predicate.",
                        )

    # 13. UNLOGGED_DUPLICATE_SURFACE
    logged_nm_pairs = {
        tuple(sorted((str(nm.get("node_a_id")), str(nm.get("node_b_id")))))
        for nm in non_merges if nm.get("node_a_id") and nm.get("node_b_id")
    }
    by_canon = collections.defaultdict(list)
    for n in nodes:
        ck = canonicalize_entity_name(n.get("canonical_name", ""), n.get("entity_type"))
        if ck:
            by_canon[(ck, n.get("entity_type", ""))].append(n)
    for (ck, _et), grp in by_canon.items():
        if len(grp) > 1:
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    pk = tuple(sorted((grp[i]["node_id"], grp[j]["node_id"])))
                    if pk not in logged_nm_pairs:
                        _add_issue(
                            issues,
                            "UNLOGGED_DUPLICATE_SURFACE",
                            "MEDIUM",
                            "quality",
                            f"{pk[0]},{pk[1]}",
                            f"Nodes {pk[0]} and {pk[1]} share canonical surface '{ck}' without merge or non_merge_decisions entry.",
                            "Either merge with evidence or record distinction in non_merge_decisions.",
                        )

    # 14. QUERY_GRAPH_DRIFT
    for q in queries:
        qid = q.get("query_id", "Q")
        if q.get("drift_detected"):
            _add_issue(
                issues,
                "QUERY_GRAPH_DRIFT",
                "HIGH",
                "blocker",
                qid,
                f"Query {qid} references non-existent graph IDs: {q.get('dangling_claimed_ids')}",
                "Update query target_entities / claimed IDs to match actual graph nodes and edges.",
            )
        elif q.get("answer_status") not in ("unreported", "no_match") and not q.get("graph_grounded"):
            _add_issue(
                issues,
                "QUERY_GRAPH_DRIFT",
                "HIGH",
                "blocker",
                qid,
                f"Query {qid} answered without matched graph nodes/edges and provenance citations.",
                "Bind query to real graph subgraph or mark unreported.",
            )

    # 15. INSUFFICIENT_QUERY_COVERAGE
    if len(nodes) > 0 and len(queries) < 3:
        _add_issue(
            issues,
            "INSUFFICIENT_QUERY_COVERAGE",
            "HIGH" if len(queries) == 0 else "MEDIUM",
            "blocker" if len(queries) == 0 else "quality",
            "queries",
            f"Only {len(queries)} query/queries executed; at least 3 grounded queries required.",
            "Add or synthesize at least 3 graph-grounded queries.",
        )

    # 16. ORPHAN_NODE_WARNING
    orphans = [n["node_id"] for n in nodes if degree[n["node_id"]] == 0]
    if orphans:
        _add_issue(
            issues,
            "ORPHAN_NODE_WARNING",
            "MEDIUM",
            "quality",
            ",".join(orphans[:5]),
            f"{len(orphans)} orphan node(s) have zero connected edges: {orphans[:8]}",
            "Connect isolated nodes with evidence-grounded relations or remove unused nodes.",
        )

    # 17. SOURCE_COVERAGE_BLOCKER
    unreadable_docs = []
    for ds in sigs.get("documents", []):
        qr = ds.get("quality_risk") or {}
        if qr.get("unextractable_pages"):
            unreadable_docs.append(ds.get("doc_id"))
    if manifest.get("status") == "partial" and not anchors:
        unreadable_docs.append("all_inputs")
    if unreadable_docs:
        _add_issue(
            issues,
            "SOURCE_COVERAGE_BLOCKER",
            "HIGH",
            "blocker",
            ",".join(unreadable_docs),
            f"Critical input document(s) {unreadable_docs} yielded unextractable/unreadable content.",
            "Inspect rendered page images or provide readable text layer.",
        )

    # 18. SCIENTIFIC_CONFLICT_PRESERVED (INFO scientific_finding, NEVER downgrades status)
    if conflicts:
        _add_issue(
            issues,
            "SCIENTIFIC_CONFLICT_PRESERVED",
            "INFO",
            "scientific_finding",
            ",".join(c.get("conflict_id", "C") for c in conflicts[:6]),
            f"Preserved {len(conflicts)} cross-paper/intra-paper scientific conflict(s) without averaging.",
            "",
        )

    sev_counts = collections.Counter(i["severity"] for i in issues)
    blocker_issues = [i for i in issues if i["category"] == "blocker"]
    high_blockers = [i for i in blocker_issues if i["severity"] == "HIGH"]
    scientific_findings = [i for i in issues if i["category"] == "scientific_finding"]

    audit_report = {
        "case_id": assembled_kg.get("case_id", "unknown"),
        "rules_evaluated": 18,
        "severity_counts": {
            "HIGH": sev_counts.get("HIGH", 0),
            "MEDIUM": sev_counts.get("MEDIUM", 0),
            "LOW": sev_counts.get("LOW", 0),
            "INFO": sev_counts.get("INFO", 0),
        },
        "blocker_count": len(blocker_issues),
        "high_blocker_count": len(high_blockers),
        "scientific_findings_count": len(scientific_findings),
        "orphan_node_ids": orphans,
        "issues": issues,
        "blocker_issues": blocker_issues,
        "scientific_findings": scientific_findings,
    }
    return audit_report


def determine_honest_status(audit_report, ingest_manifest, kg):
    """
    Deterministic status gatekeeper:
      - 'failed' if zero extractable anchors across all inputs
      - 'partial' if any HIGH blocker exists, graph lacks nodes/edges, or < 1 query
      - 'success' only when 0 HIGH blockers and grounded graph + queries pass
    Scientific conflicts and unreported boundaries do NOT cause downgrade.
    """
    reasons = []
    nodes = (kg or {}).get("nodes", [])
    edges = (kg or {}).get("edges", [])
    queries = (kg or {}).get("queries", [])
    high_blockers = audit_report.get("high_blocker_count", 0)

    if (ingest_manifest or {}).get("total_units", 0) == 0:
        return "failed", ["Zero extractable text units across all input files."]

    if high_blockers > 0:
        codes = list(dict.fromkeys(i["code"] for i in audit_report.get("blocker_issues", []) if i["severity"] == "HIGH"))
        reasons.append(f"{high_blockers} HIGH blocker issue(s) detected: {codes}")

    if not nodes or not edges:
        reasons.append(f"Incomplete graph: nodes={len(nodes)}, edges={len(edges)}.")

    if len(queries) < 3 and nodes:
        reasons.append(f"Fewer than 3 grounded queries ({len(queries)}).")

    if reasons:
        return "partial", reasons

    return "success", [
        f"All 18 audit checks passed with 0 HIGH blockers; {len(nodes)} nodes, {len(edges)} edges, {len(queries)} grounded queries."
    ]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="18-Rule Knowledge Graph Auditor")
    parser.add_argument("--work-dir", "--work", required=True, help="Work directory containing assembled_kg.json")
    args = parser.parse_args()

    work_abs = os.path.abspath(args.work_dir)
    def _rj(fn):
        p = os.path.join(work_abs, fn)
        return json.load(open(p, "r", encoding="utf-8")) if os.path.isfile(p) else {}

    kg = _rj("assembled_kg.json")
    ev = _rj("evidence_index.json")
    cm = _rj("case_manifest.json")
    pub = _rj("pubinfo.json")
    ps = _rj("paper_signals.json")

    rep = audit_kg(kg, ev, cm, pub, ps)
    st, st_reasons = determine_honest_status(rep, cm, kg)
    rep["honest_status"] = st
    rep["status_reasons"] = st_reasons

    out_path = os.path.join(work_abs, "audit_report.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(f"[kg_auditor] status={st} | high_blockers={rep['high_blocker_count']} -> {out_path}")


if __name__ == "__main__":
    main()
