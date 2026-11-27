#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kg_assembler.py - Incremental Batch Graph Assembler, Cross-Paper Disambiguation & v1->v2 Diff Engine
Supports bootstrap -> patch -> finish lifecycle with deterministic sorting and hotfix replay.
Part of the Literature Knowledge Graph Skill (task_10_kg).
Top-level imports are strictly restricted to standard libraries and sibling scripts.
"""

import os
import sys
import json
import glob
import re
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from entity_guard import (
        canonicalize_entity_name,
        evaluate_entity_merge_safety,
        load_alignment_rules,
        load_ontology,
    )
    from evidence_aligner import (
        align_verbatim_quote,
        _build_adjacent_windows,
        verify_cross_paper_independence,
    )
except ImportError:
    from .entity_guard import (
        canonicalize_entity_name,
        evaluate_entity_merge_safety,
        load_alignment_rules,
        load_ontology,
    )
    from .evidence_aligner import (
        align_verbatim_quote,
        _build_adjacent_windows,
        verify_cross_paper_independence,
    )


ANTONYM_RELATION_PAIRS = {
    frozenset(("increases", "decreases")),
    frozenset(("outperforms", "underperforms")),
    frozenset(("supports", "contradicts")),
    frozenset(("activates", "inhibits")),
    frozenset(("promotes", "suppresses")),
    frozenset(("replicates", "fails_to_replicate")),
    frozenset(("comparable_to", "differs_from")),
    frozenset(("positively_correlates_with", "negatively_correlates_with")),
}


def load_json_safe(path, default=None):
    if not path or not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _extract_batch_stage(filename, batch_data):
    st = (batch_data or {}).get("stage")
    if st:
        return str(st).strip()
    m = re.match(r"batch_\d+_(.+)\.json$", os.path.basename(filename))
    return m.group(1) if m else "patch"


def _coerce_str_list(val):
    """Safely coerce a single string or sequence into a deduplicated list of strings."""
    if not val:
        return []
    if isinstance(val, str):
        s = val.strip()
        return [s] if s else []
    if isinstance(val, (list, tuple)):
        return list(dict.fromkeys(str(x).strip() for x in val if str(x).strip()))
    s = str(val).strip()
    return [s] if s else []


def _normalize_evidence_list(ev_raw, default_source_id=None):
    """Normalize single dict or list of evidence dicts into canonical list."""
    if not ev_raw:
        return []
    items = ev_raw if isinstance(ev_raw, list) else [ev_raw]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = item.get("source_id") or item.get("doc_id") or default_source_id
        pid = item.get("paragraph_id") or item.get("anchor_id") or item.get("block_id") or item.get("locator_id")
        quote = item.get("verbatim_quote") or item.get("evidence_quote") or item.get("quote") or ""
        rec = dict(item)
        if sid:
            rec["source_id"] = str(sid)
        if pid:
            rec["locator_id"] = str(pid)
        rec["verbatim_quote"] = str(quote).strip()
        if "page_number" in item:
            rec["claimed_page_number"] = item["page_number"]
        elif "page" in item:
            rec["claimed_page_number"] = item["page"]
        out.append(rec)
    return out


def _align_record_evidence(ev_list, anchors, windows):
    """Align every evidence item against indexed anchors, preserving claimed page info for auditing."""
    aligned_list = []
    for ev in ev_list:
        ev_copy = dict(ev)
        sid = ev_copy.get("source_id")
        loc_id = ev_copy.get("locator_id") or ev_copy.get("paragraph_id") or ev_copy.get("anchor_id")
        quote = ev_copy.get("verbatim_quote", "")
        al = align_verbatim_quote(
            quote=quote,
            source_id=sid,
            locator_id=loc_id,
            indexed_blocks=anchors,
            windows=windows,
        )
        ev_copy["alignment"] = al
        if al.get("aligned"):
            ev_copy["resolved_source_id"] = al.get("resolved_source_id")
            ev_copy["resolved_anchor_id"] = al.get("resolved_anchor_id")
            ev_copy["resolved_paragraph_id"] = al.get("resolved_paragraph_id")
            ev_copy["resolved_page_number"] = al.get("resolved_page_number")
            ev_copy["section_role"] = al.get("section_role")
        aligned_list.append(ev_copy)
    return aligned_list


def _infer_stage_role(stage_name, source_ids):
    st_low = (stage_name or "").lower()
    if "v1" in st_low or "primary" in st_low:
        return "v1_primary"
    if "v2" in st_low or "secondary" in st_low:
        return "v2_secondary_update"
    sids = [str(s).lower() for s in (source_ids or []) if s]
    if sids and all(s in ("primary", "d1") for s in sids):
        return "v1_primary"
    return "v2_secondary_update"


def _replay_batches(batch_files):
    """Replay draft_batches in deterministic filename order with replaces_id & remove_*_ids support."""
    nodes_map = {}
    edges_map = {}
    merges_map = {}
    non_merges_map = {}
    conflicts_map = {}
    queries_map = {}
    hotfix_ops = []

    for bf in batch_files:
        bdata = load_json_safe(bf, {}) or {}
        b_id = bdata.get("batch_id") or os.path.splitext(os.path.basename(bf))[0]
        stage = _extract_batch_stage(bf, bdata)

        # Removals first
        for rem_key, target_map, kind in (
            ("remove_node_ids", nodes_map, "node"),
            ("remove_edge_ids", edges_map, "edge"),
            ("remove_merge_ids", merges_map, "merge_decision"),
            ("remove_non_merge_ids", non_merges_map, "non_merge_decision"),
            ("remove_conflict_ids", conflicts_map, "conflict"),
            ("remove_query_ids", queries_map, "query"),
        ):
            for rid in bdata.get(rem_key, []) or []:
                rid_s = str(rid).strip()
                if rid_s in target_map:
                    del target_map[rid_s]
                hotfix_ops.append({"batch_id": b_id, "stage": stage, "op": "remove", "kind": kind, "target_id": rid_s})

        def _upsert(items, target_map, id_key, kind):
            for idx_item, raw in enumerate(items or [], 1):
                if not isinstance(raw, dict):
                    continue
                rec = dict(raw)
                rep_id = rec.get("replaces_id")
                if rep_id and str(rep_id) in target_map:
                    del target_map[str(rep_id)]
                    hotfix_ops.append({
                        "batch_id": b_id,
                        "stage": stage,
                        "op": "replace",
                        "kind": kind,
                        "replaces_id": str(rep_id),
                        "new_id": str(rec.get(id_key) or rep_id),
                    })
                rec_id = str(rec.get(id_key) or rep_id or f"{kind.upper()}-{len(target_map) + idx_item:03d}").strip()
                rec[id_key] = rec_id
                rec["_batch_id"] = b_id
                rec["_batch_stage"] = stage
                target_map[rec_id] = rec

        _upsert(bdata.get("nodes"), nodes_map, "node_id", "node")
        _upsert(bdata.get("edges"), edges_map, "edge_id", "edge")
        _upsert(bdata.get("merge_decisions"), merges_map, "decision_id", "merge_decision")
        _upsert(bdata.get("non_merge_decisions"), non_merges_map, "decision_id", "non_merge_decision")
        _upsert(bdata.get("conflicts"), conflicts_map, "conflict_id", "conflict")
        _upsert(bdata.get("queries"), queries_map, "query_id", "query")

    return {
        "nodes_map": nodes_map,
        "edges_map": edges_map,
        "merges_map": merges_map,
        "non_merges_map": non_merges_map,
        "conflicts_map": conflicts_map,
        "queries_map": queries_map,
        "hotfix_ops": hotfix_ops,
    }


def _auto_detect_conflicts(nodes_by_id, edges_list, existing_conflicts):
    """Detect unlogged opposite-polarity, antonym-relation, or numerical discrepancy edge pairs."""
    covered_pairs = set()
    for c in existing_conflicts:
        eids = sorted(str(x) for x in (c.get("involved_edge_ids") or []))
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                covered_pairs.add((eids[i], eids[j]))

    auto_conflicts = []
    next_idx = 1
    existing_ids = {c.get("conflict_id") for c in existing_conflicts}

    def _next_cid():
        nonlocal next_idx
        while True:
            cid = f"CONFLICT-AUTO-{next_idx:03d}"
            next_idx += 1
            if cid not in existing_ids:
                existing_ids.add(cid)
                return cid

    def _norm_endpoint(nid):
        n = nodes_by_id.get(nid)
        if not n:
            return nid
        return canonicalize_entity_name(n.get("canonical_name") or nid, n.get("entity_type"))

    for i in range(len(edges_list)):
        e1 = edges_list[i]
        for j in range(i + 1, len(edges_list)):
            e2 = edges_list[j]
            pair_key = tuple(sorted((e1["edge_id"], e2["edge_id"])))
            if pair_key in covered_pairs:
                continue

            same_exact_endpoints = (e1.get("subject_id") == e2.get("subject_id") and e1.get("object_id") == e2.get("object_id"))
            same_canon_endpoints = (
                _norm_endpoint(e1.get("subject_id")) == _norm_endpoint(e2.get("subject_id"))
                and _norm_endpoint(e1.get("object_id")) == _norm_endpoint(e2.get("object_id"))
            )
            if not (same_exact_endpoints or same_canon_endpoints):
                continue

            r1 = e1.get("relation", "")
            r2 = e2.get("relation", "")
            p1 = (e1.get("polarity") or "neutral").lower()
            p2 = (e2.get("polarity") or "neutral").lower()
            q1 = e1.get("quantitative_value")
            q2 = e2.get("quantitative_value")

            ctype = None
            reason = ""
            if frozenset((r1, r2)) in ANTONYM_RELATION_PAIRS or (
                {p1, p2} == {"positive", "negative"}
            ) or (
                "no_significant_effect_on" in (r1, r2) and (r1 != r2)
            ):
                ctype = "polarity_opposite"
                reason = f"Opposite relation/polarity between {e1['edge_id']} ({r1}, {p1}) and {e2['edge_id']} ({r2}, {p2})."
            elif r1 == r2 and q1 and q2 and str(q1).strip().lower() != str(q2).strip().lower():
                ctype = "quantitative_discrepancy"
                reason = f"Distinct quantitative values for same relation '{r1}': '{q1}' ({e1['edge_id']}) vs '{q2}' ({e2['edge_id']})."

            if ctype:
                covered_pairs.add(pair_key)
                s1 = e1.get("source_id") or ""
                s2 = e2.get("source_id") or ""
                scope = "cross_paper" if (s1 and s2 and s1 != s2) else "intra_paper"
                auto_conflicts.append({
                    "conflict_id": _next_cid(),
                    "conflict_scope": scope,
                    "conflict_type": ctype,
                    "involved_node_ids": sorted({x for x in (e1.get("subject_id"), e1.get("object_id"), e2.get("subject_id"), e2.get("object_id")) if x}),
                    "involved_edge_ids": [e1["edge_id"], e2["edge_id"]],
                    "summary": reason,
                    "resolution_policy": "preserved_without_averaging",
                    "auto_detected": True,
                })

    return auto_conflicts


def _auto_detect_non_merges(nodes_list, existing_non_merges, rules):
    """Ensure cross-paper or condition-split identical surface terms are recorded in non_merge_decisions."""
    covered_pairs = set()
    for nm in existing_non_merges:
        a = nm.get("node_a_id")
        b = nm.get("node_b_id")
        if a and b:
            covered_pairs.add(tuple(sorted((str(a), str(b)))))

    existing_ids = {nm.get("decision_id") for nm in existing_non_merges}
    next_idx = 1

    def _next_nmid():
        nonlocal next_idx
        while True:
            did = f"NM-AUTO-{next_idx:03d}"
            next_idx += 1
            if did not in existing_ids:
                existing_ids.add(did)
                return did

    auto_nms = []
    for i in range(len(nodes_list)):
        na = nodes_list[i]
        ca = canonicalize_entity_name(na.get("canonical_name", ""), na.get("entity_type"))
        if not ca:
            continue
        for j in range(i + 1, len(nodes_list)):
            nb = nodes_list[j]
            cb = canonicalize_entity_name(nb.get("canonical_name", ""), nb.get("entity_type"))
            if ca != cb:
                continue
            pair_key = tuple(sorted((na["node_id"], nb["node_id"])))
            if pair_key in covered_pairs:
                continue

            sa = (na.get("source_ids") or ["D1"])[0]
            sb = (nb.get("source_ids") or ["D1"])[0]
            res = evaluate_entity_merge_safety(
                {
                    "name": na.get("canonical_name", ""),
                    "entity_type": na.get("entity_type", ""),
                    "source_id": sa,
                    "condition": na.get("context_scope", ""),
                },
                {
                    "name": nb.get("canonical_name", ""),
                    "entity_type": nb.get("entity_type", ""),
                    "source_id": sb,
                    "condition": nb.get("context_scope", ""),
                },
                rules=rules,
            )
            if res["verdict"] == "context_conflict_do_not_merge":
                covered_pairs.add(pair_key)
                auto_nms.append({
                    "decision_id": _next_nmid(),
                    "node_a_id": na["node_id"],
                    "node_b_id": nb["node_id"],
                    "surface_term": na.get("canonical_name", ""),
                    "reason_code": res["rule_id"],
                    "explanation": res["reason"],
                    "evidence_refs": [
                        {"node_id": na["node_id"], "source_ids": na.get("source_ids", [])},
                        {"node_id": nb["node_id"], "source_ids": nb.get("source_ids", [])},
                    ],
                    "auto_detected": True,
                })

    return auto_nms


def assemble_kg(work_dir):
    """
    Assemble all draft_batches in work_dir against evidence_index.json & case_manifest.json,
    returning deterministic assembled_kg dict and writing work_dir/assembled_kg.json.
    """
    work_abs = os.path.abspath(work_dir)
    ev_index = load_json_safe(os.path.join(work_abs, "evidence_index.json"), {}) or {}
    manifest = load_json_safe(os.path.join(work_abs, "case_manifest.json"), {}) or {}
    pubinfo = load_json_safe(os.path.join(work_abs, "pubinfo.json"), {}) or {}

    batches_dir = os.path.join(work_abs, "draft_batches")
    batch_files = sorted(glob.glob(os.path.join(batches_dir, "batch_*.json")))
    replayed = _replay_batches(batch_files)

    anchors = ev_index.get("anchors", [])
    windows = _build_adjacent_windows(anchors)
    rules = load_alignment_rules()
    ontology = load_ontology()

    # Process Nodes deterministically
    nodes_list = []
    for nid in sorted(replayed["nodes_map"]):
        raw_n = dict(replayed["nodes_map"][nid])
        sids = _coerce_str_list(raw_n.get("source_ids") or raw_n.get("source_id"))
        unique_node_sid = sids[0] if len(sids) == 1 else None
        raw_ev = _normalize_evidence_list(raw_n.get("evidence"), default_source_id=unique_node_sid)
        aligned_ev = _align_record_evidence(raw_ev, anchors, windows)

        # Derive true independent source_ids from evidence + declared source_ids via registry
        ev_refs_for_ind = list(aligned_ev) + [{"source_id": s} for s in sids]
        ind_check = verify_cross_paper_independence(ev_refs_for_ind, manifest if manifest else ev_index)

        raw_n["canonical_key"] = canonicalize_entity_name(raw_n.get("canonical_name", ""), raw_n.get("entity_type"))
        raw_n["aliases"] = _coerce_str_list(raw_n.get("aliases"))
        raw_n["source_ids"] = sids or ind_check["independent_source_ids"]
        raw_n["independent_source_ids"] = ind_check["independent_source_ids"]
        raw_n["is_cross_paper_shared"] = ind_check["is_truly_cross_paper"]
        raw_n["evidence"] = aligned_ev
        raw_n["origin_stage"] = _infer_stage_role(raw_n.get("_batch_stage"), raw_n["source_ids"])
        nodes_list.append(raw_n)

    nodes_by_id = {n["node_id"]: n for n in nodes_list}

    # Process Edges deterministically
    rel_lookup = {r["id"]: r for r in ontology.get("relations", [])}
    edges_list = []
    for eid in sorted(replayed["edges_map"]):
        raw_e = dict(replayed["edges_map"][eid])
        rel_name = (raw_e.get("relation") or raw_e.get("predicate") or "").strip()
        raw_e["relation"] = rel_name
        raw_e.setdefault("direction", "subject_to_object")
        sid = raw_e.get("source_id")
        raw_ev = _normalize_evidence_list(raw_e.get("evidence"), default_source_id=sid)
        aligned_ev = _align_record_evidence(raw_ev, anchors, windows)
        if not sid and aligned_ev:
            sid = aligned_ev[0].get("resolved_source_id") or aligned_ev[0].get("source_id")
        raw_e["source_id"] = sid
        if not raw_e.get("polarity"):
            raw_e["polarity"] = (rel_lookup.get(rel_name) or {}).get("polarity", "neutral")
        raw_e["qualifiers"] = raw_e.get("qualifiers") if raw_e.get("qualifiers") is not None else {}
        raw_e["evidence"] = aligned_ev
        raw_e["origin_stage"] = _infer_stage_role(raw_e.get("_batch_stage"), [sid] if sid else [])
        edges_list.append(raw_e)

    # Merge decisions
    merges_list = []
    for mid in sorted(replayed["merges_map"]):
        m = dict(replayed["merges_map"][mid])
        m_sids = _coerce_str_list(m.get("merged_source_ids"))
        m["merged_source_ids"] = m_sids
        m_refs = m.get("evidence_refs") or []
        ind_m = verify_cross_paper_independence(
            [{"source_id": s} for s in m_sids] + [r for r in m_refs if isinstance(r, (dict, str))],
            manifest if manifest else ev_index,
        )
        m["cross_paper_independence"] = ind_m
        merges_list.append(m)

    # Non-merge decisions (+ auto-detected generic term splits)
    non_merges_list = [dict(replayed["non_merges_map"][k]) for k in sorted(replayed["non_merges_map"])]
    non_merges_list.extend(_auto_detect_non_merges(nodes_list, non_merges_list, rules))
    non_merges_list.sort(key=lambda x: x.get("decision_id", ""))

    # Conflicts (+ auto-detected polarity/value conflicts)
    conflicts_list = [dict(replayed["conflicts_map"][k]) for k in sorted(replayed["conflicts_map"])]
    for c in conflicts_list:
        c.setdefault("resolution_policy", "preserved_without_averaging")
    conflicts_list.extend(_auto_detect_conflicts(nodes_by_id, edges_list, conflicts_list))
    conflicts_list.sort(key=lambda x: x.get("conflict_id", ""))

    # Queries (raw replayed; grounded in kg_query_engine)
    queries_list = [dict(replayed["queries_map"][k]) for k in sorted(replayed["queries_map"])]

    # Build v1 -> v2 Incremental Update Diff
    v1_nodes = [n["node_id"] for n in nodes_list if n.get("origin_stage") == "v1_primary"]
    v2_added_nodes = [n["node_id"] for n in nodes_list if n.get("origin_stage") != "v1_primary"]
    v1_edges = [e["edge_id"] for e in edges_list if e.get("origin_stage") == "v1_primary"]
    v2_added_edges = [e["edge_id"] for e in edges_list if e.get("origin_stage") != "v1_primary"]
    cross_merged_nodes = [n["node_id"] for n in nodes_list if n.get("is_cross_paper_shared")]
    rejected_pairs = [
        {
            "decision_id": nm.get("decision_id"),
            "surface_term": nm.get("surface_term"),
            "node_a_id": nm.get("node_a_id"),
            "node_b_id": nm.get("node_b_id"),
            "reason_code": nm.get("reason_code"),
        }
        for nm in non_merges_list
    ]
    new_conflicts = [
        c["conflict_id"] for c in conflicts_list
        if c.get("conflict_scope") == "cross_paper" or c.get("_batch_stage") != "v1_primary"
    ]

    incremental_diff = {
        "v1_primary_counts": {"nodes": len(v1_nodes), "edges": len(v1_edges)},
        "v2_total_counts": {"nodes": len(nodes_list), "edges": len(edges_list)},
        "added_node_ids": v2_added_nodes,
        "added_edge_ids": v2_added_edges,
        "cross_paper_merged_node_ids": cross_merged_nodes,
        "rejected_non_merge_pairs": rejected_pairs,
        "newly_introduced_conflict_ids": new_conflicts,
        "hotfix_operations": replayed["hotfix_ops"],
    }

    assembled = {
        "schema_version": "1.0.0",
        "case_id": manifest.get("case_id", ev_index.get("case_id", "unknown")),
        "ingest_mode": manifest.get("ingest_mode", ev_index.get("ingest_mode", "document_files")),
        "locator_scheme": manifest.get("locator_scheme", ev_index.get("locator_scheme", "pdf_page_block")),
        "independent_paper_count": manifest.get("independent_paper_count", ev_index.get("independent_paper_count", 0)),
        "documents": pubinfo.get("sources") or manifest.get("documents", []),
        "verification_twins": manifest.get("verification_twins", []),
        "twin_verification_notes": manifest.get("twin_verification_notes", []),
        "content_limitations": manifest.get("content_limitations", []),
        "batches_applied": [os.path.basename(b) for b in batch_files],
        "nodes": nodes_list,
        "edges": edges_list,
        "merge_decisions": merges_list,
        "non_merge_decisions": non_merges_list,
        "conflicts": conflicts_list,
        "incremental_update_diff": incremental_diff,
        "queries": queries_list,
    }

    out_path = os.path.join(work_abs, "assembled_kg.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(assembled, f, ensure_ascii=False, indent=2)

    print(
        f"[kg_assembler] batches={len(batch_files)} | nodes={len(nodes_list)} | edges={len(edges_list)} "
        f"| merges={len(merges_list)} | non_merges={len(non_merges_list)} | conflicts={len(conflicts_list)} -> {out_path}"
    )
    return assembled


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Incremental Literature Knowledge Graph Assembler")
    parser.add_argument("--work-dir", "--work", required=True, help="Work directory containing draft_batches/")
    args = parser.parse_args()
    assemble_kg(args.work_dir)


if __name__ == "__main__":
    main()
