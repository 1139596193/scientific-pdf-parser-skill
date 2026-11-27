#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kg_query_engine.py - Deterministic Subgraph Retrieval & Grounded Q&A Engine
Executes all 6 standardized scientific KG query templates directly on assembled_kg.json,
verifies Agent-submitted queries against real graph IDs, and guarantees >= 3 grounded queries.
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
    from entity_guard import canonicalize_entity_name
except ImportError:
    from .entity_guard import canonicalize_entity_name


UNREPORTED_PATTERNS = re.compile(
    r"(?:"
    r"\b(?:pdf\s+page\s+number|page\s+numbers?|raw\s+clinical|patient[- ]level\s+data|image\s+pixel|supplementary\s+(?:file|table|data)|underlying\s+experimental\s+data)\b"
    r"|(?:PDF\s*页码|原\s*PDF\s*页|图像像素|补充附件|底层临床|实验原始数据|未提供)"
    r")",
    re.I,
)


def _node_matches_term(node, term):
    if not term:
        return False
    t_str = str(term).strip()
    if t_str == node.get("node_id"):
        return True
    t_canon = canonicalize_entity_name(t_str, node.get("entity_type"))
    if not t_canon:
        return False
    cands = [node.get("canonical_name", ""), node.get("canonical_key", "")] + list(node.get("aliases") or [])
    for c in cands:
        c_canon = canonicalize_entity_name(c, node.get("entity_type"))
        if not c_canon:
            continue
        if t_canon == c_canon or (len(t_canon) >= 3 and t_canon in c_canon) or (len(c_canon) >= 3 and c_canon in t_canon):
            return True
    return False


def _find_matching_nodes(nodes, terms):
    if not terms:
        return []
    matched = []
    seen = set()
    for n in sorted(nodes, key=lambda x: x.get("node_id", "")):
        if any(_node_matches_term(n, t) for t in terms):
            if n["node_id"] not in seen:
                seen.add(n["node_id"])
                matched.append(n)
    return matched


def _collect_edge_citations(edge):
    cites = []
    for ev in edge.get("evidence") or []:
        al = ev.get("alignment") or {}
        cites.append({
            "edge_id": edge.get("edge_id"),
            "source_id": al.get("resolved_source_id") or ev.get("source_id") or edge.get("source_id"),
            "anchor_id": al.get("resolved_anchor_id") or ev.get("resolved_anchor_id") or ev.get("locator_id"),
            "paragraph_id": al.get("resolved_paragraph_id") or ev.get("paragraph_id"),
            "page_number": al.get("resolved_page_number"),
            "section_role": al.get("section_role") or ev.get("section_role"),
            "epistemic_status": edge.get("epistemic_status"),
            "verbatim_quote": ev.get("verbatim_quote", ""),
        })
    return cites


def _collect_node_citations(node):
    cites = []
    for ev in node.get("evidence") or []:
        al = ev.get("alignment") or {}
        cites.append({
            "node_id": node.get("node_id"),
            "source_id": al.get("resolved_source_id") or ev.get("source_id"),
            "anchor_id": al.get("resolved_anchor_id") or ev.get("resolved_anchor_id") or ev.get("locator_id"),
            "paragraph_id": al.get("resolved_paragraph_id") or ev.get("paragraph_id"),
            "page_number": al.get("resolved_page_number"),
            "section_role": al.get("section_role") or ev.get("section_role"),
            "verbatim_quote": ev.get("verbatim_quote", ""),
        })
    return cites


def _bfs_paths(nodes_by_id, edges, start_ids, end_ids, max_hops=3):
    """Deterministic BFS shortest paths (up to max_hops) across KG edges (treated traversably with direction tag)."""
    adj = collections.defaultdict(list)
    for e in sorted(edges, key=lambda x: x.get("edge_id", "")):
        s, o = e.get("subject_id"), e.get("object_id")
        if s in nodes_by_id and o in nodes_by_id:
            adj[s].append((o, e, "forward"))
            adj[o].append((s, e, "reverse"))
    for k in adj:
        adj[k].sort(key=lambda x: (x[1].get("edge_id", ""), x[0]))

    end_set = set(end_ids)
    paths_found = []
    for sid in sorted(start_ids):
        queue = collections.deque([(sid, [sid], [])])
        visited_depth = {sid: 0}
        while queue:
            curr, n_path, e_path = queue.popleft()
            if len(e_path) > 0 and (not end_set or curr in end_set):
                paths_found.append((n_path, e_path))
                if len(paths_found) >= 5:
                    break
            if len(e_path) >= max_hops:
                continue
            for nxt, edge, trav_dir in adj.get(curr, []):
                if nxt in n_path:
                    continue
                ndepth = len(e_path) + 1
                if nxt in visited_depth and visited_depth[nxt] < ndepth:
                    continue
                visited_depth[nxt] = ndepth
                queue.append((nxt, n_path + [nxt], e_path + [(edge, trav_dir)]))
        if len(paths_found) >= 5:
            break
    return paths_found


def _infer_query_type(query_obj):
    qt = (query_obj.get("query_type") or query_obj.get("template_id") or "").strip()
    if qt in (
        "single_hop_fact", "multi_hop_path", "cross_paper_comparison",
        "conflict_and_disambiguation", "provenance_reverse_trace", "unreported_boundary_check",
    ):
        return qt
    txt = (query_obj.get("question") or query_obj.get("query") or "").lower()
    if UNREPORTED_PATTERNS.search(txt) or query_obj.get("expected_unreported"):
        return "unreported_boundary_check"
    if any(k in txt for k in ("conflict", "contradict", "disambiguat", "non-merge", "separate", "冲突", "矛盾", "消歧", "拒并", "不合并", "独立")):
        return "conflict_and_disambiguation"
    if any(k in txt for k in ("compare", "comparison", "both papers", "primary", "secondary", "对比", "两篇", "跨篇")):
        return "cross_paper_comparison"
    if any(k in txt for k in ("path", "hop", "trace how", "lead to", "pathway", "路径", "多跳", "如何通过", "机制")):
        return "multi_hop_path"
    if any(k in txt for k in ("provenance", "where is", "which paragraph", "back to", "溯源", "哪个段落", "出处", "反查")):
        return "provenance_reverse_trace"
    return "single_hop_fact"


def execute_single_query(query_obj, assembled_kg, evidence_index=None):
    """Execute one query deterministically against assembled_kg and return a fully grounded query record."""
    nodes = assembled_kg.get("nodes", [])
    edges = assembled_kg.get("edges", [])
    nodes_by_id = {n["node_id"]: n for n in nodes}
    edges_by_id = {e["edge_id"]: e for e in edges}
    merges = assembled_kg.get("merge_decisions", [])
    non_merges = assembled_kg.get("non_merge_decisions", [])
    conflicts = assembled_kg.get("conflicts", [])

    q_id = query_obj.get("query_id") or query_obj.get("question_id") or "Q1"
    q_text = (query_obj.get("question") or query_obj.get("query") or "").strip()
    q_type = _infer_query_type(query_obj)
    target_entities = list(query_obj.get("target_entities") or [])
    target_relations = set(query_obj.get("target_relations") or [])

    # Check if Agent claimed specific IDs that do not exist in the graph (drift check)
    claimed_nids = [str(x) for x in (query_obj.get("claimed_node_ids") or query_obj.get("matched_node_ids") or [])]
    claimed_eids = [str(x) for x in (query_obj.get("claimed_edge_ids") or query_obj.get("matched_edge_ids") or [])]
    dangling_n = [nid for nid in claimed_nids if nid not in nodes_by_id]
    dangling_e = [eid for eid in claimed_eids if eid not in edges_by_id]
    drift_detected = bool(dangling_n or dangling_e)

    # Extract entity candidates from question text if target_entities not explicitly given
    if not target_entities and q_text:
        for n in nodes:
            if _node_matches_term(n, q_text):
                target_entities.append(n["node_id"])
            else:
                for cand in [n.get("canonical_name", "")] + list(n.get("aliases") or []):
                    if cand and len(cand) >= 3 and cand.lower() in q_text.lower():
                        target_entities.append(n["node_id"])
                        break

    matched_node_ids = []
    matched_edge_ids = []
    evidence_paths = []
    provenance_citations = []
    undetermined_reasons = []
    answer_status = "answered"

    if q_type == "unreported_boundary_check" or query_obj.get("expected_unreported") or UNREPORTED_PATTERNS.search(q_text):
        answer_status = "unreported"
        lims = assembled_kg.get("content_limitations") or []
        undetermined_reasons.append(
            f"Requested information falls outside provided text/corpus limitations ({lims}) or is unreported in the source papers."
        )
        ans_summary = query_obj.get("answer_summary") or (
            "Unreported / outside material boundary: " + "; ".join(undetermined_reasons)
        )
    elif q_type == "provenance_reverse_trace":
        hit_nodes = _find_matching_nodes(nodes, target_entities + claimed_nids)
        hit_edges = [edges_by_id[eid] for eid in claimed_eids if eid in edges_by_id]
        # Also allow matching by locator/paragraph_id inside question text
        for e in edges:
            for ev in e.get("evidence") or []:
                loc = ev.get("resolved_paragraph_id") or ev.get("locator_id") or ""
                if loc and loc in q_text and e not in hit_edges:
                    hit_edges.append(e)
        for n in hit_nodes:
            matched_node_ids.append(n["node_id"])
            provenance_citations.extend(_collect_node_citations(n))
        for e in hit_edges:
            matched_edge_ids.append(e["edge_id"])
            for nid in (e.get("subject_id"), e.get("object_id")):
                if nid in nodes_by_id and nid not in matched_node_ids:
                    matched_node_ids.append(nid)
            provenance_citations.extend(_collect_edge_citations(e))
        if not matched_node_ids and not matched_edge_ids:
            answer_status = "no_match"
            undetermined_reasons.append("No matching node, edge, or locator found in assembled graph for reverse trace.")
        ans_summary = query_obj.get("answer_summary") or f"Reverse-traced {len(matched_node_ids)} node(s) and {len(matched_edge_ids)} edge(s)."

    elif q_type == "conflict_and_disambiguation":
        for c in conflicts:
            for nid in c.get("involved_node_ids") or []:
                if nid in nodes_by_id and nid not in matched_node_ids:
                    matched_node_ids.append(nid)
            for eid in c.get("involved_edge_ids") or []:
                if eid in edges_by_id and eid not in matched_edge_ids:
                    matched_edge_ids.append(eid)
                    provenance_citations.extend(_collect_edge_citations(edges_by_id[eid]))
        for nm in non_merges:
            for nid in (nm.get("node_a_id"), nm.get("node_b_id")):
                if nid in nodes_by_id and nid not in matched_node_ids:
                    matched_node_ids.append(nid)
                    provenance_citations.extend(_collect_node_citations(nodes_by_id[nid]))
        for m in merges:
            cid = m.get("canonical_node_id")
            if cid in nodes_by_id and cid not in matched_node_ids:
                matched_node_ids.append(cid)
                provenance_citations.extend(_collect_node_citations(nodes_by_id[cid]))
        if not conflicts and not non_merges and not merges:
            answer_status = "no_match"
            undetermined_reasons.append("No conflicts, merges, or non-merge disambiguations recorded in graph.")
        ans_summary = query_obj.get("answer_summary") or (
            f"Surfaced {len(conflicts)} conflict(s), {len(non_merges)} non-merge isolation decision(s), and {len(merges)} merge decision(s)."
        )

    elif q_type == "cross_paper_comparison":
        seed_nodes = _find_matching_nodes(nodes, target_entities) if target_entities else list(nodes)
        seed_ids = {n["node_id"] for n in seed_nodes}
        for e in sorted(edges, key=lambda x: x.get("edge_id", "")):
            if target_relations and e.get("relation") not in target_relations:
                continue
            if not target_entities or e.get("subject_id") in seed_ids or e.get("object_id") in seed_ids:
                matched_edge_ids.append(e["edge_id"])
                for nid in (e.get("subject_id"), e.get("object_id")):
                    if nid in nodes_by_id and nid not in matched_node_ids:
                        matched_node_ids.append(nid)
                provenance_citations.extend(_collect_edge_citations(e))
        for n in seed_nodes:
            if n["node_id"] not in matched_node_ids:
                matched_node_ids.append(n["node_id"])
                provenance_citations.extend(_collect_node_citations(n))
        if not matched_edge_ids and not matched_node_ids:
            answer_status = "no_match"
            undetermined_reasons.append("No nodes or edges matched the cross-paper comparison criteria.")
        ans_summary = query_obj.get("answer_summary") or (
            f"Cross-paper comparison across {len(matched_node_ids)} node(s) and {len(matched_edge_ids)} edge(s) "
            f"preserving {len(non_merges)} non-merge boundary(ies) and {len(conflicts)} conflict(s)."
        )

    elif q_type == "multi_hop_path":
        start_nodes = _find_matching_nodes(nodes, target_entities[:1]) if target_entities else []
        end_nodes = _find_matching_nodes(nodes, target_entities[1:]) if len(target_entities) > 1 else []
        if not start_nodes and nodes:
            start_nodes = [nodes[0]]
        raw_paths = _bfs_paths(
            nodes_by_id,
            edges,
            [n["node_id"] for n in start_nodes],
            [n["node_id"] for n in end_nodes],
            max_hops=int(query_obj.get("max_hops") or 3),
        )
        for n_seq, e_seq in raw_paths:
            hop_records = []
            for edge, trav_dir in e_seq:
                if edge["edge_id"] not in matched_edge_ids:
                    matched_edge_ids.append(edge["edge_id"])
                for nid in (edge.get("subject_id"), edge.get("object_id")):
                    if nid in nodes_by_id and nid not in matched_node_ids:
                        matched_node_ids.append(nid)
                cites = _collect_edge_citations(edge)
                provenance_citations.extend(cites)
                hop_records.append({
                    "edge_id": edge["edge_id"],
                    "subject_id": edge.get("subject_id"),
                    "relation": edge.get("relation"),
                    "object_id": edge.get("object_id"),
                    "traversal": trav_dir,
                    "epistemic_status": edge.get("epistemic_status"),
                    "source_id": edge.get("source_id"),
                    "verbatim_quote": cites[0]["verbatim_quote"] if cites else "",
                })
            evidence_paths.append({"node_sequence": n_seq, "hops": hop_records})
        if not evidence_paths:
            answer_status = "no_match"
            undetermined_reasons.append("No connected path found in the graph up to max_hops.")
        ans_summary = query_obj.get("answer_summary") or f"Found {len(evidence_paths)} grounded multi-hop path(s) across {len(matched_edge_ids)} edge(s)."

    else:  # single_hop_fact
        seed_nodes = _find_matching_nodes(nodes, target_entities)
        if not seed_nodes and claimed_nids:
            seed_nodes = [nodes_by_id[nid] for nid in claimed_nids if nid in nodes_by_id]
        seed_ids = {n["node_id"] for n in seed_nodes}
        for e in sorted(edges, key=lambda x: x.get("edge_id", "")):
            if target_relations and e.get("relation") not in target_relations:
                continue
            if e.get("subject_id") in seed_ids or e.get("object_id") in seed_ids:
                matched_edge_ids.append(e["edge_id"])
                for nid in (e.get("subject_id"), e.get("object_id")):
                    if nid in nodes_by_id and nid not in matched_node_ids:
                        matched_node_ids.append(nid)
                provenance_citations.extend(_collect_edge_citations(e))
        for n in seed_nodes:
            if n["node_id"] not in matched_node_ids:
                matched_node_ids.append(n["node_id"])
                provenance_citations.extend(_collect_node_citations(n))
        if not matched_node_ids and not matched_edge_ids:
            answer_status = "no_match"
            undetermined_reasons.append("Target entity or relation not present in assembled knowledge graph.")
        ans_summary = query_obj.get("answer_summary") or f"Retrieved {len(matched_edge_ids)} direct relation(s) over {len(matched_node_ids)} node(s)."

    # Deduplicate citations cleanly while preserving order
    seen_c = set()
    uniq_cites = []
    for c in provenance_citations:
        ck = (c.get("edge_id"), c.get("node_id"), c.get("source_id"), c.get("anchor_id"), c.get("verbatim_quote"))
        if ck not in seen_c:
            seen_c.add(ck)
            uniq_cites.append(c)

    if drift_detected:
        undetermined_reasons.append(f"Drift detected: dangling claimed node_ids={dangling_n}, edge_ids={dangling_e}")

    grounded = (
        not drift_detected
        and (
            answer_status in ("unreported", "no_match")
            or (bool(matched_node_ids or matched_edge_ids) and len(uniq_cites) > 0)
        )
    )

    return {
        "query_id": q_id,
        "template_id": q_type,
        "question": q_text,
        "answer_status": answer_status,
        "answer_summary": ans_summary,
        "matched_node_ids": matched_node_ids,
        "matched_edge_ids": matched_edge_ids,
        "evidence_paths": evidence_paths,
        "subgraph": {"node_ids": matched_node_ids, "edge_ids": matched_edge_ids},
        "provenance_citations": uniq_cites,
        "undetermined_or_missing": undetermined_reasons,
        "graph_grounded": grounded,
        "drift_detected": drift_detected,
        "dangling_claimed_ids": {"node_ids": dangling_n, "edge_ids": dangling_e},
        "synthesized_by_engine": bool(query_obj.get("synthesized_by_engine", False)),
    }


def _synthesize_fallback_queries(assembled_kg, existing_queries):
    """Synthesize deterministic queries up to minimum count 3 when graph is non-empty."""
    nodes = assembled_kg.get("nodes", [])
    edges = assembled_kg.get("edges", [])
    if not nodes:
        return []

    used_ids = {q.get("query_id") for q in existing_queries}
    used_types = {q.get("template_id") for q in existing_queries}

    def _next_qid():
        idx = len(existing_queries) + 1
        while f"Q{idx}" in used_ids:
            idx += 1
        qid = f"Q{idx}"
        used_ids.add(qid)
        return qid

    deg = collections.Counter()
    for e in edges:
        deg[e.get("subject_id")] += 1
        deg[e.get("object_id")] += 1
    top_node = max(nodes, key=lambda n: (deg[n["node_id"]], -len(n["node_id"])))

    candidates = []
    if "single_hop_fact" not in used_types:
        candidates.append({
            "query_id": _next_qid(),
            "query_type": "single_hop_fact",
            "question": f"What direct relations and evidence are reported for {top_node['canonical_name']}?",
            "target_entities": [top_node["node_id"]],
            "synthesized_by_engine": True,
        })
    if "cross_paper_comparison" not in used_types and assembled_kg.get("independent_paper_count", 1) > 1:
        candidates.append({
            "query_id": _next_qid(),
            "query_type": "cross_paper_comparison",
            "question": "Compare the entities, relations, and non-merged divergences across primary and secondary papers.",
            "target_entities": [top_node["node_id"]],
            "synthesized_by_engine": True,
        })
    elif "multi_hop_path" not in used_types and len(edges) >= 2:
        candidates.append({
            "query_id": _next_qid(),
            "query_type": "multi_hop_path",
            "question": f"Trace multi-hop evidence paths starting from {top_node['canonical_name']}.",
            "target_entities": [top_node["node_id"]],
            "synthesized_by_engine": True,
        })
    if "conflict_and_disambiguation" not in used_types:
        candidates.append({
            "query_id": _next_qid(),
            "query_type": "conflict_and_disambiguation",
            "question": "What conflicts, cross-paper merges, and non-merge disambiguations exist in the graph?",
            "synthesized_by_engine": True,
        })
    if "unreported_boundary_check" not in used_types:
        candidates.append({
            "query_id": _next_qid(),
            "query_type": "unreported_boundary_check",
            "question": "Are raw PDF page numbers or image pixels provided in the material package?",
            "expected_unreported": True,
            "synthesized_by_engine": True,
        })

    needed = max(0, 3 - len(existing_queries))
    return [execute_single_query(c, assembled_kg) for c in candidates[:needed]]


def ground_all_queries(assembled_kg, case_manifest=None, evidence_index=None):
    """Execute and bind all batch queries + case research questions + fallback queries >= 3."""
    raw_queries = list(assembled_kg.get("queries") or [])
    seen_norm = {re.sub(r"\s+", " ", (q.get("question") or "").strip()).lower().rstrip("?？") for q in raw_queries}
    used_qids = {q.get("query_id") for q in raw_queries if q.get("query_id")}

    # Incorporate explicit research questions from case.json / task.md if not already submitted
    for rq in (case_manifest or {}).get("research_questions") or []:
        qtxt = (rq.get("question") or "").strip()
        if not qtxt:
            continue
        qnorm = re.sub(r"\s+", " ", qtxt).lower().rstrip("?？")
        if qnorm in seen_norm:
            continue
        seen_norm.add(qnorm)
        qid = rq.get("question_id") or f"Q{len(raw_queries) + 1}"
        while qid in used_qids:
            qid = f"Q{len(raw_queries) + 1}"
        used_qids.add(qid)
        raw_queries.append({"query_id": qid, "question": qtxt})

    grounded = [execute_single_query(q, assembled_kg, evidence_index=evidence_index) for q in raw_queries]
    if len(grounded) < 3 and assembled_kg.get("nodes"):
        grounded.extend(_synthesize_fallback_queries(assembled_kg, grounded))

    grounded.sort(key=lambda x: x.get("query_id", ""))
    assembled_kg["queries"] = grounded
    return grounded


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Deterministic Knowledge Graph Subgraph Query Engine")
    parser.add_argument("--work-dir", "--work", required=True, help="Work directory containing assembled_kg.json")
    parser.add_argument("--question", required=True, help="Question text to query")
    parser.add_argument("--type", dest="query_type", default=None, help="Optional template_id")
    parser.add_argument("--entity", action="append", default=[], help="Target entity name or node_id")
    parser.add_argument("--target", action="append", default=[], help="Optional second entity for multi_hop_path")
    args = parser.parse_args()

    kg_path = os.path.join(os.path.abspath(args.work_dir), "assembled_kg.json")
    if not os.path.isfile(kg_path):
        raise FileNotFoundError(f"assembled_kg.json not found in {args.work_dir}; run finish or kg_assembler first.")
    with open(kg_path, "r", encoding="utf-8") as f:
        kg = json.load(f)

    targets = list(args.entity or []) + list(args.target or [])
    q_res = execute_single_query(
        {"query_id": "Q_CLI", "question": args.question, "query_type": args.query_type, "target_entities": targets},
        kg,
    )
    print(json.dumps(q_res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
