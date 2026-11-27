#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_deliverables.py - Multi-Format KG Deliverable Renderer
Outputs:
  1. knowledge_graph.json
  2. knowledge_graph.graphml (valid XML GraphML 1.0)
  3. nodes.csv & edges.csv (UTF-8 no BOM, LF)
  4. knowledge_graph_report.md (tables + Mermaid subgraph + queries + diff)
  5. kg_overview.svg + kg_overview.png (3-tier fallback: pymupdf -> Pillow -> pure-stdlib rasterizer)
  6. result.json (preserves case_id & all output_contract fields + honest status)
Part of the Literature Knowledge Graph Skill (task_10_kg).
Top-level imports are strictly restricted to standard libraries and sibling scripts.
"""

import os
import sys
import json
import csv
import re
import math
import struct
import zlib
import argparse
from xml.sax.saxutils import escape

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from kg_auditor import determine_honest_status
except ImportError:
    from .kg_auditor import determine_honest_status


def _first_ev_summary(ev_list):
    if not ev_list:
        return "", "", None, ""
    ev = ev_list[0]
    al = ev.get("alignment") or {}
    sid = al.get("resolved_source_id") or ev.get("source_id") or ""
    loc = al.get("resolved_paragraph_id") or al.get("resolved_anchor_id") or ev.get("locator_id") or ""
    pg = al.get("resolved_page_number")
    quote = ev.get("verbatim_quote", "")
    return str(sid), str(loc), pg, str(quote)


def _render_graphml(kg, out_path):
    """Write standard XML GraphML 1.0 file compatible with Gephi / Cytoscape / NetworkX."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"',
        '         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '         xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">',
        '  <key id="d_label" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="d_etype" for="node" attr.name="entity_type" attr.type="string"/>',
        '  <key id="d_sids" for="node" attr.name="source_ids" attr.type="string"/>',
        '  <key id="d_scope" for="node" attr.name="context_scope" attr.type="string"/>',
        '  <key id="d_rel" for="edge" attr.name="relation" attr.type="string"/>',
        '  <key id="d_ep" for="edge" attr.name="epistemic_status" attr.type="string"/>',
        '  <key id="d_pol" for="edge" attr.name="polarity" attr.type="string"/>',
        '  <key id="d_esid" for="edge" attr.name="source_id" attr.type="string"/>',
        '  <key id="d_loc" for="edge" attr.name="locator" attr.type="string"/>',
        '  <key id="d_quote" for="edge" attr.name="quote" attr.type="string"/>',
        f'  <graph id="{escape(str(kg.get("case_id", "kg")))}" edgedefault="directed">',
    ]
    node_ids = set()
    for n in kg.get("nodes", []):
        nid = str(n["node_id"])
        node_ids.add(nid)
        lines.extend([
            f'    <node id="{escape(nid)}">',
            f'      <data key="d_label">{escape(str(n.get("canonical_name", "")))}</data>',
            f'      <data key="d_etype">{escape(str(n.get("entity_type", "")))}</data>',
            f'      <data key="d_sids">{escape(",".join(n.get("independent_source_ids") or n.get("source_ids") or []))}</data>',
            f'      <data key="d_scope">{escape(str(n.get("context_scope", "")))}</data>',
            '    </node>',
        ])
    for e in kg.get("edges", []):
        eid = str(e["edge_id"])
        s = str(e.get("subject_id", ""))
        o = str(e.get("object_id", ""))
        if s not in node_ids or o not in node_ids:
            continue
        esid, loc, _pg, quote = _first_ev_summary(e.get("evidence") or [])
        lines.extend([
            f'    <edge id="{escape(eid)}" source="{escape(s)}" target="{escape(o)}">',
            f'      <data key="d_rel">{escape(str(e.get("relation", "")))}</data>',
            f'      <data key="d_ep">{escape(str(e.get("epistemic_status", "")))}</data>',
            f'      <data key="d_pol">{escape(str(e.get("polarity", "")))}</data>',
            f'      <data key="d_esid">{escape(esid or str(e.get("source_id", "")))}</data>',
            f'      <data key="d_loc">{escape(loc)}</data>',
            f'      <data key="d_quote">{escape(quote)}</data>',
            '    </edge>',
        ])
    lines.extend(["  </graph>", "</graphml>"])
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _render_csvs(kg, nodes_csv_path, edges_csv_path):
    with open(nodes_csv_path, "w", encoding="utf-8", newline="") as nf:
        nw = csv.writer(nf, lineterminator="\n")
        nw.writerow([
            "node_id", "canonical_name", "entity_type", "aliases",
            "independent_source_ids", "is_cross_paper_shared", "context_scope",
            "primary_source_id", "primary_locator", "primary_page_number", "verbatim_quote",
        ])
        for n in kg.get("nodes", []):
            sid, loc, pg, quote = _first_ev_summary(n.get("evidence") or [])
            nw.writerow([
                n.get("node_id", ""),
                n.get("canonical_name", ""),
                n.get("entity_type", ""),
                "; ".join(n.get("aliases") or []),
                ",".join(n.get("independent_source_ids") or n.get("source_ids") or []),
                str(bool(n.get("is_cross_paper_shared"))).lower(),
                n.get("context_scope", ""),
                sid,
                loc,
                "" if pg is None else str(pg),
                quote,
            ])

    with open(edges_csv_path, "w", encoding="utf-8", newline="") as ef:
        ew = csv.writer(ef, lineterminator="\n")
        ew.writerow([
            "edge_id", "subject_id", "relation", "object_id", "direction",
            "epistemic_status", "polarity", "quantitative_value", "qualifiers",
            "source_id", "locator", "page_number", "verbatim_quote",
        ])
        for e in kg.get("edges", []):
            sid, loc, pg, quote = _first_ev_summary(e.get("evidence") or [])
            quals = e.get("qualifiers")
            q_str = json.dumps(quals, ensure_ascii=False) if isinstance(quals, (dict, list)) else str(quals or "")
            ew.writerow([
                e.get("edge_id", ""),
                e.get("subject_id", ""),
                e.get("relation", ""),
                e.get("object_id", ""),
                e.get("direction", "subject_to_object"),
                e.get("epistemic_status", ""),
                e.get("polarity", ""),
                str(e.get("quantitative_value") or ""),
                q_str,
                sid or str(e.get("source_id") or ""),
                loc,
                "" if pg is None else str(pg),
                quote,
            ])


def _safe_label(txt, max_len=24):
    s = re.sub(r"\s+", " ", str(txt or "")).strip()
    return (s[: max_len - 1] + "…") if len(s) > max_len else s


def _render_svg_and_png(kg, svg_path, png_path):
    """Render clean layered SVG overview and attempt PNG preview via dynamic pymupdf import."""
    nodes = kg.get("nodes", [])[:18]
    shown_ids = {n["node_id"] for n in nodes}
    edges = [e for e in kg.get("edges", []) if e.get("subject_id") in shown_ids and e.get("object_id") in shown_ids][:28]
    conflict_eids = {eid for c in kg.get("conflicts", []) for eid in (c.get("involved_edge_ids") or [])}

    left_nodes, mid_nodes, right_nodes = [], [], []
    for n in nodes:
        sids = [str(s).lower() for s in (n.get("independent_source_ids") or n.get("source_ids") or [])]
        if n.get("is_cross_paper_shared") or len(sids) >= 2:
            mid_nodes.append(n)
        elif any(s in ("secondary", "d2") for s in sids):
            right_nodes.append(n)
        else:
            left_nodes.append(n)

    cols = [(left_nodes, 160, "#E8F1FA", "#2B6CB0", "Primary Paper"),
            (mid_nodes, 520, "#E6FFFA", "#2C7A7B", "Cross-Paper Shared"),
            (right_nodes, 880, "#FFF5F5", "#C53030", "Secondary Paper")]

    max_rows = max(len(left_nodes), len(mid_nodes), len(right_nodes), 1)
    width = 1080
    height = max(440, 120 + max_rows * 72)

    pos = {}
    for grp, x_c, fill_c, stroke_c, _title in cols:
        for idx, n in enumerate(grp):
            y_c = 115 + idx * 68
            pos[n["node_id"]] = (x_c, y_c, fill_c, stroke_c, n)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<defs>',
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#4A5568"/>',
        '</marker>',
        '<marker id="arrow-conflict" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '<path d="M 0 1 L 10 5 L 0 9 z" fill="#E53E3E"/>',
        '</marker>',
        '</defs>',
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        f'<text x="28" y="34" font-family="DejaVu Sans, Arial, sans-serif" font-size="18" font-weight="bold" fill="#1A202C">Knowledge Graph Overview: {escape(str(kg.get("case_id", "")))}</text>',
        f'<text x="28" y="56" font-family="DejaVu Sans, Arial, sans-serif" font-size="12" fill="#4A5568">Nodes: {len(kg.get("nodes", []))} | Edges: {len(kg.get("edges", []))} | Conflicts: {len(kg.get("conflicts", []))} | Non-Merges: {len(kg.get("non_merge_decisions", []))}</text>',
    ]

    # Column headers + Legend
    for grp, x_c, fill_c, stroke_c, title in cols:
        svg.append(f'<rect x="{x_c - 85}" y="68" width="170" height="22" rx="4" fill="{fill_c}" stroke="{stroke_c}" stroke-width="1"/>')
        svg.append(f'<text x="{x_c}" y="83" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="11" font-weight="bold" fill="{stroke_c}">{escape(title)} ({len(grp)})</text>')

    # Edges
    for e in edges:
        x1, y1, *_ = pos[e["subject_id"]]
        x2, y2, *_ = pos[e["object_id"]]
        is_conf = e["edge_id"] in conflict_eids
        stroke = "#E53E3E" if is_conf else "#718096"
        dash = ' stroke-dasharray="5,3"' if is_conf else ""
        marker = "url(#arrow-conflict)" if is_conf else "url(#arrow)"
        dx = 95 if x2 > x1 else (-95 if x2 < x1 else 0)
        sx, ex = x1 + dx, x2 - dx
        if sx == ex and y1 == y2:
            continue
        mx, my = (sx + ex) / 2, (y1 + y2) / 2 - 6
        svg.append(f'<line x1="{sx}" y1="{y1}" x2="{ex}" y2="{y2}" stroke="{stroke}" stroke-width="1.5"{dash} marker-end="{marker}"/>')
        svg.append(f'<text x="{mx}" y="{my}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="9" fill="{stroke}">{escape(_safe_label(e.get("relation", ""), 18))}</text>')

    # Nodes
    for nid, (xc, yc, fill_c, stroke_c, n) in pos.items():
        lbl = _safe_label(n.get("canonical_name", nid), 22)
        sub_lbl = f"{nid} | {_safe_label(n.get('entity_type', ''), 18)}"
        svg.append(f'<rect x="{xc - 95}" y="{yc - 22}" width="190" height="44" rx="8" fill="{fill_c}" stroke="{stroke_c}" stroke-width="1.5"/>')
        svg.append(f'<text x="{xc}" y="{yc - 3}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="11" font-weight="bold" fill="#1A202C">{escape(lbl)}</text>')
        svg.append(f'<text x="{xc}" y="{yc + 13}" text-anchor="middle" font-family="DejaVu Sans, Arial, sans-serif" font-size="9" fill="#4A5568">{escape(sub_lbl)}</text>')

    svg.append("</svg>")
    with open(svg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(svg) + "\n")

    ctx = {
        "kg": kg,
        "width": width,
        "height": height,
        "cols": cols,
        "pos": pos,
        "edges": edges,
        "conflict_eids": conflict_eids,
    }

    # Strict 3-tier PNG fallback: pymupdf -> Pillow -> pure stdlib rasterizer
    png_status = _try_render_png_pymupdf(svg_path, png_path)
    if not png_status:
        png_status = _try_render_png_pillow(ctx, png_path)
    if not png_status:
        png_status = _render_png_stdlib(ctx, png_path)

    return png_status


_FONT_5X7 = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    "!": (0x04, 0x04, 0x04, 0x04, 0x00, 0x00, 0x04),
    '"': (0x0A, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00),
    "#": (0x0A, 0x1F, 0x0A, 0x0A, 0x1F, 0x0A, 0x00),
    "%": (0x19, 0x19, 0x02, 0x04, 0x08, 0x13, 0x13),
    "&": (0x0C, 0x12, 0x14, 0x08, 0x15, 0x12, 0x0D),
    "'": (0x04, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00),
    "(": (0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02),
    ")": (0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08),
    "*": (0x00, 0x0A, 0x04, 0x1F, 0x04, 0x0A, 0x00),
    "+": (0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00),
    ",": (0x00, 0x00, 0x00, 0x00, 0x04, 0x04, 0x08),
    "-": (0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00),
    ".": (0x00, 0x00, 0x00, 0x00, 0x00, 0x06, 0x06),
    "/": (0x01, 0x02, 0x04, 0x04, 0x08, 0x10, 0x00),
    ":": (0x00, 0x06, 0x06, 0x00, 0x06, 0x06, 0x00),
    ";": (0x00, 0x06, 0x06, 0x00, 0x06, 0x04, 0x08),
    "<": (0x02, 0x04, 0x08, 0x10, 0x08, 0x04, 0x02),
    "=": (0x00, 0x00, 0x1F, 0x00, 0x1F, 0x00, 0x00),
    ">": (0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08),
    "?": (0x0E, 0x11, 0x02, 0x04, 0x04, 0x00, 0x04),
    "@": (0x0E, 0x11, 0x17, 0x15, 0x17, 0x10, 0x0E),
    "[": (0x0E, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0E),
    "\\": (0x10, 0x08, 0x04, 0x04, 0x02, 0x01, 0x00),
    "]": (0x0E, 0x02, 0x02, 0x02, 0x02, 0x02, 0x0E),
    "^": (0x04, 0x0A, 0x11, 0x00, 0x00, 0x00, 0x00),
    "_": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F),
    "|": (0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "~": (0x00, 0x08, 0x15, 0x02, 0x00, 0x00, 0x00),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F),
    "3": (0x1F, 0x02, 0x04, 0x06, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x07, 0x02, 0x02, 0x02, 0x12, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "a": (0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F),
    "b": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x1E),
    "c": (0x00, 0x00, 0x0E, 0x11, 0x10, 0x11, 0x0E),
    "d": (0x01, 0x01, 0x0F, 0x11, 0x11, 0x11, 0x0F),
    "e": (0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E),
    "f": (0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08),
    "g": (0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E),
    "h": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "i": (0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E),
    "j": (0x02, 0x00, 0x06, 0x02, 0x02, 0x12, 0x0C),
    "k": (0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12),
    "l": (0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "m": (0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11),
    "n": (0x00, 0x00, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "o": (0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E),
    "p": (0x00, 0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10),
    "q": (0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x01),
    "r": (0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10),
    "s": (0x00, 0x00, 0x0F, 0x10, 0x0E, 0x01, 0x1E),
    "t": (0x08, 0x08, 0x1C, 0x08, 0x08, 0x09, 0x06),
    "u": (0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D),
    "v": (0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "w": (0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A),
    "x": (0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11),
    "y": (0x00, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "z": (0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F),
}


def _hex_to_rgb(hx):
    s = hx.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _ascii_label(txt, max_len=24):
    s = re.sub(r"\s+", " ", str(txt or "")).strip()
    out = []
    for ch in s:
        if ch in _FONT_5X7:
            out.append(ch)
        elif ch == "\u2026":
            out.append(".")
        elif ord(ch) < 128:
            out.append(" ")
        else:
            out.append("?")
    res = "".join(out)
    if len(res) > max_len:
        res = res[: max_len - 2] + ".."
    return res


def _try_render_png_pymupdf(svg_path, png_path):
    try:
        import pymupdf
        doc = pymupdf.open(svg_path)
        pix = doc[0].get_pixmap(dpi=150, alpha=False)
        pix.save(png_path)
        doc.close()
        if os.path.isfile(png_path) and os.path.getsize(png_path) > 0:
            return "rendered_pymupdf"
    except Exception:
        return None
    return None


def _try_render_png_pillow(ctx, png_path):
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    try:
        w, h = ctx["width"], ctx["height"]
        kg = ctx["kg"]
        img = Image.new("RGB", (w, h), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((28, 18), _ascii_label(f"Knowledge Graph Overview: {kg.get('case_id', '')}", 72), fill=(26, 32, 44))
        draw.text(
            (28, 42),
            f"Nodes: {len(kg.get('nodes', []))} | Edges: {len(kg.get('edges', []))} | Conflicts: {len(kg.get('conflicts', []))} | Non-Merges: {len(kg.get('non_merge_decisions', []))}",
            fill=(74, 85, 104),
        )
        for grp, x_c, fill_c, stroke_c, title in ctx["cols"]:
            draw.rectangle([x_c - 85, 68, x_c + 85, 90], fill=_hex_to_rgb(fill_c), outline=_hex_to_rgb(stroke_c), width=1)
            draw.text((x_c - 68, 73), _ascii_label(f"{title} ({len(grp)})", 24), fill=_hex_to_rgb(stroke_c))
        for e in ctx["edges"]:
            x1, y1, *_ = ctx["pos"][e["subject_id"]]
            x2, y2, *_ = ctx["pos"][e["object_id"]]
            is_conf = e["edge_id"] in ctx["conflict_eids"]
            col = (229, 62, 62) if is_conf else (113, 128, 150)
            dx = 95 if x2 > x1 else (-95 if x2 < x1 else 0)
            sx, ex = x1 + dx, x2 - dx
            if sx != ex or y1 != y2:
                draw.line([(sx, y1), (ex, y2)], fill=col, width=2)
                draw.text(((sx + ex) // 2 - 24, (y1 + y2) // 2 - 12), _ascii_label(e.get("relation", ""), 14), fill=col)
        for nid, (xc, yc, fill_c, stroke_c, n) in ctx["pos"].items():
            draw.rectangle([xc - 95, yc - 22, xc + 95, yc + 22], fill=_hex_to_rgb(fill_c), outline=_hex_to_rgb(stroke_c), width=2)
            draw.text((xc - 84, yc - 14), _ascii_label(n.get("canonical_name", nid), 24), fill=(26, 32, 44))
            draw.text((xc - 84, yc + 4), _ascii_label(f"{nid} | {n.get('entity_type', '')}", 26), fill=(74, 85, 104))
        img.save(png_path, format="PNG")
        if os.path.isfile(png_path) and os.path.getsize(png_path) > 0:
            return "rendered_pillow"
    except Exception:
        return None
    return None


def _render_png_stdlib(ctx, png_path):
    """Pure Python stdlib RGB PNG rasterizer using 5x7 bitmap font and zlib+struct."""
    w = max(800, int(ctx["width"]))
    h = max(440, int(ctx["height"]))
    kg = ctx["kg"]
    buf = bytearray([255]) * (w * h * 3)

    def fill_rect(x0, y0, x1, y1, rgb):
        rx0 = max(0, min(w, int(x0)))
        rx1 = max(0, min(w, int(x1)))
        ry0 = max(0, min(h, int(y0)))
        ry1 = max(0, min(h, int(y1)))
        if rx1 <= rx0 or ry1 <= ry0:
            return
        row_bytes = bytes(rgb) * (rx1 - rx0)
        stride = w * 3
        for y in range(ry0, ry1):
            off = y * stride + rx0 * 3
            buf[off:off + len(row_bytes)] = row_bytes

    def stroke_rect(x0, y0, x1, y1, rgb, thick=2):
        fill_rect(x0, y0, x1, y0 + thick, rgb)
        fill_rect(x0, y1 - thick, x1, y1, rgb)
        fill_rect(x0, y0, x0 + thick, y1, rgb)
        fill_rect(x1 - thick, y0, x1, y1, rgb)

    def set_px(x, y, rgb):
        if 0 <= x < w and 0 <= y < h:
            off = (y * w + x) * 3
            buf[off], buf[off + 1], buf[off + 2] = rgb

    def draw_line(x0, y0, x1, y1, rgb, dashed=False, thick=2):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            if dashed and (i // 6) % 2 == 1:
                continue
            px = int(round(x0 + (x1 - x0) * i / steps))
            py = int(round(y0 + (y1 - y0) * i / steps))
            for dy in range(thick):
                for dx in range(thick):
                    set_px(px + dx, py + dy, rgb)

    def draw_arrow(x0, y0, x1, y1, rgb):
        ang = math.atan2(y1 - y0, x1 - x0)
        sz = 9
        for spread in (2.65, -2.65):
            ax = x1 + sz * math.cos(ang + spread)
            ay = y1 + sz * math.sin(ang + spread)
            draw_line(x1, y1, ax, ay, rgb, dashed=False, thick=2)

    def draw_text(x, y, text, rgb, scale=1, center=False):
        clean = _ascii_label(text, 80)
        adv = 6 * scale
        sx = int(x - (len(clean) * adv) // 2) if center else int(x)
        sy = int(y)
        for ch in clean:
            glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
            for r_idx, row_bits in enumerate(glyph):
                for c_idx in range(5):
                    if (row_bits >> (4 - c_idx)) & 1:
                        px = sx + c_idx * scale
                        py = sy + r_idx * scale
                        if scale == 1:
                            set_px(px, py, rgb)
                        else:
                            fill_rect(px, py, px + scale, py + scale, rgb)
            sx += adv

    # Top bar & title
    fill_rect(0, 0, w, 62, (248, 250, 252))
    fill_rect(0, 62, w, 64, (226, 232, 240))
    draw_text(24, 14, f"Knowledge Graph Overview: {kg.get('case_id', '')}", (26, 32, 44), scale=2)
    stats = f"Nodes:{len(kg.get('nodes', []))}  Edges:{len(kg.get('edges', []))}  Conflicts:{len(kg.get('conflicts', []))}  Non-Merges:{len(kg.get('non_merge_decisions', []))}"
    draw_text(24, 40, stats, (74, 85, 104), scale=1)

    # Compact legend in top right
    legend_items = [
        (680, (43, 108, 176), "Primary"),
        (770, (44, 122, 123), "Shared"),
        (855, (197, 48, 48), "Secondary"),
        (955, (229, 62, 62), "Conflict"),
    ]
    for lx, lcol, ltxt in legend_items:
        fill_rect(lx, 39, lx + 10, 49, lcol)
        draw_text(lx + 14, 40, ltxt, (45, 55, 72), scale=1)

    # Column banners
    for grp, x_c, fill_c, stroke_c, title in ctx["cols"]:
        f_rgb = _hex_to_rgb(fill_c)
        s_rgb = _hex_to_rgb(stroke_c)
        fill_rect(x_c - 88, 70, x_c + 88, 92, f_rgb)
        stroke_rect(x_c - 88, 70, x_c + 88, 92, s_rgb, thick=1)
        draw_text(x_c, 77, f"{title} ({len(grp)})", s_rgb, scale=1, center=True)

    # Edges + Conflict lines
    for e in ctx["edges"]:
        x1, y1, *_ = ctx["pos"][e["subject_id"]]
        x2, y2, *_ = ctx["pos"][e["object_id"]]
        is_conf = e["edge_id"] in ctx["conflict_eids"]
        e_rgb = (229, 62, 62) if is_conf else (113, 128, 150)
        dx = 95 if x2 > x1 else (-95 if x2 < x1 else 0)
        sx, ex = x1 + dx, x2 - dx
        if sx == ex and y1 == y2:
            continue
        draw_line(sx, y1, ex, y2, e_rgb, dashed=is_conf, thick=2)
        draw_arrow(sx, y1, ex, y2, e_rgb)
        draw_text((sx + ex) // 2, (y1 + y2) // 2 - 11, _ascii_label(e.get("relation", ""), 16), e_rgb, scale=1, center=True)

    # Color-coded nodes
    for nid, (xc, yc, fill_c, stroke_c, n) in ctx["pos"].items():
        f_rgb = _hex_to_rgb(fill_c)
        s_rgb = _hex_to_rgb(stroke_c)
        fill_rect(xc - 95, yc - 22, xc + 95, yc + 22, f_rgb)
        stroke_rect(xc - 95, yc - 22, xc + 95, yc + 22, s_rgb, thick=2)
        draw_text(xc, yc - 13, _ascii_label(n.get("canonical_name", nid), 24), (26, 32, 44), scale=1, center=True)
        draw_text(xc, yc + 4, _ascii_label(f"{nid} | {n.get('entity_type', '')}", 26), (74, 85, 104), scale=1, center=True)

    def _chunk(ctype, cdata):
        body = ctype + cdata
        return struct.pack("!I", len(cdata)) + body + struct.pack("!I", zlib.crc32(body) & 0xFFFFFFFF)

    stride = w * 3
    raw_scanlines = bytearray()
    for y in range(h):
        raw_scanlines.append(0)
        raw_scanlines.extend(buf[y * stride:(y + 1) * stride])
    ihdr = struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw_scanlines), level=6))
        + _chunk(b"IEND", b"")
    )
    os.makedirs(os.path.dirname(os.path.abspath(png_path)), exist_ok=True)
    with open(png_path, "wb") as f:
        f.write(png_bytes)
    return "rendered_stdlib_rasterizer"


def _render_report_md(kg, audit_rep, honest_status, status_reasons, out_path):
    lines = [
        f"# 文献知识图谱分析报告 ({kg.get('case_id', 'unknown')})",
        "",
        f"- **运行状态**: `{honest_status}` ({'; '.join(status_reasons)})",
        f"- **摄取模式 / 定位体系**: `{kg.get('ingest_mode')}` / `{kg.get('locator_scheme')}`",
        f"- **独立论文数**: `{kg.get('independent_paper_count')}` | **核对副本 (Verification Twins)**: `{len(kg.get('verification_twins', []))}`",
        "",
        "## 1. 语料来源与独立元数据隔离总览",
        "",
        "| source_id | role | title | doi | authors | license | source_hash |",
        "|-|-|-|-|-|-|-|",
    ]
    for d in kg.get("documents", []):
        auths = ", ".join((d.get("authors") or [])[:4]) or "unreported"
        lines.append(
            f"| {d.get('source_id') or d.get('doc_id')} | {d.get('role', 'primary')} | "
            f"{str(d.get('title') or 'unreported').replace('|', '/')} | {d.get('doi') or 'unreported'} | "
            f"{auths.replace('|', '/')} | {d.get('license') or 'unreported'} | {d.get('source_hash') or 'unreported'} |"
        )
    if kg.get("verification_twins"):
        lines.extend(["", "### 核对副本（不计入独立论文或跨篇证据）"])
        for t in kg["verification_twins"]:
            lines.append(f"- `{t.get('twin_file')}` -> `twin_of={t.get('twin_of_source_id')}` (`is_verification_twin=true`)")
        for note in kg.get("twin_verification_notes") or []:
            lines.append(f"  - {note}")
    if kg.get("content_limitations"):
        lines.extend(["", "### 内容与材料边界声明", f"- `content_limitations`: `{', '.join(kg['content_limitations'])}`"])

    diff = kg.get("incremental_update_diff") or {}
    lines.extend([
        "",
        "## 2. 图谱核心统计与 v1 -> v2 增量更新 Diff",
        "",
        f"- **v1_primary**: 节点 `{diff.get('v1_primary_counts', {}).get('nodes', 0)}` | 边 `{diff.get('v1_primary_counts', {}).get('edges', 0)}`",
        f"- **v2_total**: 节点 `{diff.get('v2_total_counts', {}).get('nodes', 0)}` | 边 `{diff.get('v2_total_counts', {}).get('edges', 0)}`",
        f"- **新增节点**: `{diff.get('added_node_ids', [])}`",
        f"- **新增边**: `{diff.get('added_edge_ids', [])}`",
        f"- **跨篇真实合并节点**: `{diff.get('cross_paper_merged_node_ids', [])}`",
        f"- **新增/跨篇冲突**: `{diff.get('newly_introduced_conflict_ids', [])}`",
        "",
        "## 3. 核心知识图谱子图 (Mermaid)",
        "",
        "```mermaid",
        "flowchart LR",
    ])
    nodes_by_id = {n["node_id"]: n for n in kg.get("nodes", [])}
    for n in kg.get("nodes", [])[:16]:
        lbl = str(n.get("canonical_name", n["node_id"])).replace('"', "'")
        lines.append(f'  {n["node_id"]}["{n["node_id"]}: {lbl}"]')
    for e in kg.get("edges", [])[:24]:
        if e.get("subject_id") in nodes_by_id and e.get("object_id") in nodes_by_id:
            lines.append(f'  {e["subject_id"]} -->|"{e.get("relation")}"| {e["object_id"]}')
    lines.extend(["```", ""])

    lines.extend(["## 4. 实体合并与同名异物拒并台账", ""])
    if kg.get("merge_decisions"):
        lines.extend(["### 跨篇/篇内合并决策 (`merge_decisions`)", "| decision_id | canonical_node_id | merged_source_ids | merge_basis |", "|-|-|-|-|"])
        for m in kg["merge_decisions"]:
            lines.append(f"| {m.get('decision_id')} | {m.get('canonical_node_id')} | {m.get('merged_source_ids')} | {str(m.get('merge_basis', '')).replace('|', '/')} |")
        lines.append("")
    if kg.get("non_merge_decisions"):
        lines.extend(["### 拒绝合并台账 (`non_merge_decisions`)", "| decision_id | surface_term | node_a | node_b | reason_code | explanation |", "|-|-|-|-|-|-|"])
        for nm in kg["non_merge_decisions"]:
            lines.append(
                f"| {nm.get('decision_id')} | {nm.get('surface_term')} | {nm.get('node_a_id')} | {nm.get('node_b_id')} | "
                f"{nm.get('reason_code')} | {str(nm.get('explanation', '')).replace('|', '/')} |"
            )
        lines.append("")

    lines.extend(["## 5. 跨篇与源内冲突保留 (`conflicts`)", ""])
    if kg.get("conflicts"):
        lines.extend(["| conflict_id | scope | type | involved_edges | resolution_policy | summary |", "|-|-|-|-|-|-|"])
        for c in kg["conflicts"]:
            lines.append(
                f"| {c.get('conflict_id')} | {c.get('conflict_scope')} | {c.get('conflict_type')} | "
                f"{c.get('involved_edge_ids')} | {c.get('resolution_policy')} | {str(c.get('summary', '')).replace('|', '/')} |"
            )
    else:
        lines.append("- 未检测到对立或数值冲突。")

    lines.extend(["", "## 6. 科研图谱查询与溯源证据链 (`queries`)", ""])
    for q in kg.get("queries", []):
        lines.extend([
            f"### {q.get('query_id')} [{q.get('template_id')}] — `{q.get('answer_status')}`",
            f"- **问题**: {q.get('question')}",
            f"- **结论**: {q.get('answer_summary')}",
            f"- **命中子图**: nodes=`{q.get('matched_node_ids', [])}`, edges=`{q.get('matched_edge_ids', [])}`",
        ])
        for c in (q.get("provenance_citations") or [])[:5]:
            loc_show = c.get("paragraph_id") or c.get("anchor_id") or ""
            lines.append(f"  - 依据 [`{c.get('source_id')}:{loc_show}`]: \"{c.get('verbatim_quote', '')}\"")
        if q.get("undetermined_or_missing"):
            lines.append(f"- **边界/未报告说明**: {'; '.join(q['undetermined_or_missing'])}")
        lines.append("")

    sev = audit_rep.get("severity_counts", {})
    lines.extend([
        "## 7. 18 项硬核审计摘要",
        "",
        f"- **HIGH Blockers**: `{audit_rep.get('high_blocker_count', 0)}` | **Severity**: HIGH=`{sev.get('HIGH', 0)}`, MEDIUM=`{sev.get('MEDIUM', 0)}`, INFO=`{sev.get('INFO', 0)}`",
    ])
    for iss in audit_rep.get("issues", []):
        lines.append(f"- [{iss['severity']}] `{iss['code']}` ({iss['target_id']}): {iss['message']}")

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _apply_output_contract_fields(result_payload, output_contract):
    """Preserve output_contract and satisfy any top-level fields declared in output_contract."""
    result_payload["output_contract"] = output_contract
    if not isinstance(output_contract, dict):
        return
    req_fields = output_contract.get("required_fields")
    alias_map = {
        "graph": result_payload.get("kg_summary"),
        "knowledge_graph": result_payload.get("kg_summary"),
        "sources": result_payload.get("documents"),
        "uncertainty": result_payload.get("limitations_and_unreported"),
        "qa_answers": result_payload.get("queries"),
        "answer": [q.get("answer_summary") for q in result_payload.get("queries", [])],
        "evidence": [c for q in result_payload.get("queries", []) for c in q.get("provenance_citations", [])][:20],
        "changes": result_payload.get("incremental_update_diff"),
    }
    if isinstance(req_fields, list):
        for fld in req_fields:
            if isinstance(fld, str) and fld not in result_payload:
                result_payload[fld] = alias_map.get(fld, result_payload.get("kg_summary"))
    for k, v in output_contract.items():
        if k not in ("required_fields", "format", "schema_version", "description") and k not in result_payload:
            if k in alias_map:
                result_payload[k] = alias_map[k]
            elif isinstance(v, list):
                result_payload[k] = []
            elif isinstance(v, dict):
                sub_obj = {}
                for req_k in (v.get("required") or []):
                    if isinstance(req_k, str):
                        sub_obj[req_k] = result_payload.get(req_k, alias_map.get(req_k, {}))
                if isinstance(v.get("properties"), dict):
                    for pk in v["properties"]:
                        if pk not in sub_obj:
                            sub_obj[pk] = result_payload.get(pk, alias_map.get(pk, {}))
                result_payload[k] = sub_obj


def render_all_deliverables(assembled_kg, audit_rep, case_manifest, output_dir):
    out_abs = os.path.abspath(output_dir)
    os.makedirs(out_abs, exist_ok=True)

    honest_status, status_reasons = determine_honest_status(audit_rep, case_manifest, assembled_kg)
    audit_rep["honest_status"] = honest_status
    audit_rep["status_reasons"] = status_reasons

    kg_json_path = os.path.join(out_abs, "knowledge_graph.json")
    graphml_path = os.path.join(out_abs, "knowledge_graph.graphml")
    nodes_csv_path = os.path.join(out_abs, "nodes.csv")
    edges_csv_path = os.path.join(out_abs, "edges.csv")
    report_md_path = os.path.join(out_abs, "knowledge_graph_report.md")
    svg_path = os.path.join(out_abs, "kg_overview.svg")
    png_path = os.path.join(out_abs, "kg_overview.png")
    result_path = os.path.join(out_abs, "result.json")

    kg_export = {
        "schema_version": "1.0.0",
        "case_id": assembled_kg.get("case_id"),
        "ingest_mode": assembled_kg.get("ingest_mode"),
        "locator_scheme": assembled_kg.get("locator_scheme"),
        "independent_paper_count": assembled_kg.get("independent_paper_count"),
        "documents": assembled_kg.get("documents", []),
        "verification_twins": assembled_kg.get("verification_twins", []),
        "twin_verification_notes": assembled_kg.get("twin_verification_notes", []),
        "nodes": assembled_kg.get("nodes", []),
        "edges": assembled_kg.get("edges", []),
        "merge_decisions": assembled_kg.get("merge_decisions", []),
        "non_merge_decisions": assembled_kg.get("non_merge_decisions", []),
        "conflicts": assembled_kg.get("conflicts", []),
        "incremental_update_diff": assembled_kg.get("incremental_update_diff", {}),
        "queries": assembled_kg.get("queries", []),
        "content_limitations": assembled_kg.get("content_limitations", []),
        "audit_summary": {
            "honest_status": honest_status,
            "high_blocker_count": audit_rep.get("high_blocker_count", 0),
            "severity_counts": audit_rep.get("severity_counts", {}),
        },
    }
    with open(kg_json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(kg_export, f, ensure_ascii=False, indent=2)

    _render_graphml(kg_export, graphml_path)
    _render_csvs(kg_export, nodes_csv_path, edges_csv_path)
    png_status = _render_svg_and_png(kg_export, svg_path, png_path)
    _render_report_md(kg_export, audit_rep, honest_status, status_reasons, report_md_path)

    artifacts = {
        "result_json": "result.json",
        "knowledge_graph_json": "knowledge_graph.json",
        "knowledge_graph_graphml": "knowledge_graph.graphml",
        "nodes_csv": "nodes.csv",
        "edges_csv": "edges.csv",
        "knowledge_graph_report_md": "knowledge_graph_report.md",
        "kg_overview_svg": "kg_overview.svg",
    }
    if png_status.startswith("rendered_") and os.path.isfile(png_path) and os.path.getsize(png_path) > 0:
        artifacts["kg_overview_png"] = "kg_overview.png"

    unreported_items = [
        q for q in kg_export["queries"]
        if q.get("answer_status") in ("unreported", "no_match")
    ]

    result_payload = {
        "case_id": kg_export["case_id"],
        "status": honest_status,
        "status_reasons": status_reasons,
        "ingest_mode": kg_export["ingest_mode"],
        "locator_scheme": kg_export["locator_scheme"],
        "independent_paper_count": kg_export["independent_paper_count"],
        "documents": kg_export["documents"],
        "verification_twins": kg_export["verification_twins"],
        "kg_summary": {
            "node_count": len(kg_export["nodes"]),
            "edge_count": len(kg_export["edges"]),
            "merge_decision_count": len(kg_export["merge_decisions"]),
            "non_merge_decision_count": len(kg_export["non_merge_decisions"]),
            "conflict_count": len(kg_export["conflicts"]),
            "query_count": len(kg_export["queries"]),
            "png_preview_status": png_status,
        },
        "queries": kg_export["queries"],
        "merge_decisions": kg_export["merge_decisions"],
        "non_merge_decisions": kg_export["non_merge_decisions"],
        "conflicts": kg_export["conflicts"],
        "incremental_update_diff": kg_export["incremental_update_diff"],
        "limitations_and_unreported": {
            "content_limitations": kg_export["content_limitations"],
            "twin_verification_notes": kg_export["twin_verification_notes"],
            "unreported_queries": [
                {"query_id": u["query_id"], "question": u["question"], "reasons": u.get("undetermined_or_missing", [])}
                for u in unreported_items
            ],
        },
        "artifacts": artifacts,
        "audit_summary": {
            "rules_evaluated": audit_rep.get("rules_evaluated", 18),
            "high_blocker_count": audit_rep.get("high_blocker_count", 0),
            "severity_counts": audit_rep.get("severity_counts", {}),
            "scientific_findings_count": audit_rep.get("scientific_findings_count", 0),
        },
    }
    _apply_output_contract_fields(result_payload, (case_manifest or {}).get("output_contract", {}))

    with open(result_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result_payload, f, ensure_ascii=False, indent=2)

    return result_payload


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Multi-Format KG Deliverable Renderer")
    parser.add_argument("--work-dir", "--work", required=True, help="Work directory")
    parser.add_argument("--output-dir", "--output", required=True, help="Output directory")
    args = parser.parse_args()

    work_abs = os.path.abspath(args.work_dir)
    with open(os.path.join(work_abs, "assembled_kg.json"), "r", encoding="utf-8") as kf:
        kg = json.load(kf)
    with open(os.path.join(work_abs, "audit_report.json"), "r", encoding="utf-8") as af:
        aud = json.load(af)
    cm_p = os.path.join(work_abs, "case_manifest.json")
    if os.path.isfile(cm_p):
        with open(cm_p, "r", encoding="utf-8") as cf:
            cm = json.load(cf)
    else:
        cm = {}
    res = render_all_deliverables(kg, aud, cm, args.output_dir)
    print(f"[render_deliverables] status={res['status']} | artifacts={list(res['artifacts'].keys())}")


if __name__ == "__main__":
    main()
