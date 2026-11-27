#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_test.py - Offline Zero-Dependency 41-Test Verification Suite for task_10_kg
Pure Python standard library + sibling skill modules only; uses temp directories and
in-memory/synthetic inputs (no external files or local paths required).
"""

import os
import sys
import ast
import re
import json
import struct
import shutil
import builtins
import tempfile
import subprocess
import py_compile
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(SCRIPT_DIR)
REFS_DIR = os.path.join(SKILL_ROOT, "references")
PIPELINE_PY = os.path.join(SCRIPT_DIR, "kg_pipeline.py")

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import case_ingestor
import structure_indexer
import pubinfo_extractor
import entity_guard
import evidence_aligner
import kg_assembler
import kg_query_engine
import kg_auditor
import render_deliverables
import validate_output


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_json(path, data):
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _build_minimal_valid_pdf(path, body_text="Hello Quantum Catalyst 2025"):
    """Build a minimal syntactically valid single-page PDF 1.4 purely in Python stdlib."""
    stream_str = f"BT /F1 12 Tf 72 720 Td ({body_text}) Tj ET\n"
    stream_bytes = stream_str.encode("latin-1")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1") + stream_bytes + b"endstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    buf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(buf)


def _create_sim_jats_case(case_dir, xml_doi="10.2000/sec.2026.202"):
    _write_json(os.path.join(case_dir, "case.json"), {
        "case_id": "selftest_jats_case",
        "title": "Primary Catalysis Paper",
        "doi": "10.1000/prim.2025.101",
        "authors": ["Alice Zhang", "Bob Li"],
        "research_questions": [
            {"id": "Q1", "question": "How does Cu-MOF affect yield?"},
            {"id": "Q2", "question": "Compare primary and secondary catalyst results."},
            {"id": "Q3", "question": "What conflicts and non-merges exist?"},
        ],
        "output_contract": {"required_fields": ["case_id", "status", "graph", "queries"]},
    })
    _write_text(os.path.join(case_dir, "task.md"), "# Task\nBuild KG across primary and secondary papers.\n")
    _write_json(os.path.join(case_dir, "corpus-manifest.json"), {
        "corpus": [
            {
                "source_id": "primary",
                "role": "primary",
                "title": "Primary Catalysis Paper",
                "doi": "10.1000/prim.2025.101",
                "authors": ["Alice Zhang", "Bob Li"],
                "year": 2025,
                "license": "CC-BY-4.0",
                "source_hash": "hash_p1",
                "files": ["primary-material.json"],
            },
            {
                "source_id": "secondary",
                "role": "secondary",
                "title": "Secondary Thermal Study",
                "doi": "10.2000/sec.2026.202",
                "authors": ["Clara Wang"],
                "year": 2026,
                "license": "CC-BY-NC-4.0",
                "source_hash": "hash_p2",
                "files": ["secondary-material.json", "secondary-publisher.xml"],
            },
        ]
    })
    _write_json(os.path.join(case_dir, "primary-material.json"), {
        "source_id": "primary",
        "sections": [
            {
                "section_title": "Introduction",
                "section_role": "introduction",
                "paragraphs": [
                    {"paragraph_id": "p-001", "text": "Earlier work reported that MOFs promote adsorption (Smith et al., 2021) [12]."}
                ],
            },
            {
                "section_title": "Methods",
                "section_role": "methods",
                "paragraphs": [
                    {"paragraph_id": "p-002", "text": "We prepared Copper Metal-Organic Framework (Cu-MOF) as a catalyst against an aqueous control group at 298 K."}
                ],
            },
            {
                "section_title": "Results",
                "section_role": "results",
                "paragraphs": [
                    {"paragraph_id": "p-003", "text": "Our measurements show Cu-MOF increases CO2 yield to 84.5% (p < 0.01) and outperforms the control group."}
                ],
            },
            {
                "section_title": "Discussion",
                "section_role": "discussion",
                "paragraphs": [
                    {"paragraph_id": "p-004", "text": "Open sites correlate with activity and may improve durability."}
                ],
            },
        ],
    })
    _write_json(os.path.join(case_dir, "secondary-material.json"), {
        "source_id": "secondary",
        "sections": [
            {
                "section_title": "Methods",
                "section_role": "methods",
                "paragraphs": [
                    {"paragraph_id": "sec2-p01", "text": "Cu-MOF (Copper Metal-Organic Framework) was evaluated as a catalyst alongside a deionized control group at 350 K."}
                ],
            },
            {
                "section_title": "Results",
                "section_role": "results",
                "paragraphs": [
                    {"paragraph_id": "sec2-p02", "text": "At 350 K, Cu-MOF decreases CO_2 yield to 41.0% after 12 h (p = 0.03)."}
                ],
            },
        ],
    })
    _write_json(os.path.join(case_dir, "material.json"), {
        "paragraphs": [
            {"source_id": "primary", "paragraph_id": "p-001", "text": "Earlier work reported that MOFs promote adsorption (Smith et al., 2021) [12]."},
            {"source_id": "secondary", "paragraph_id": "sec2-p02", "text": "At 350 K, Cu-MOF decreases CO_2 yield to 41.0% after 12 h (p = 0.03)."},
        ]
    })
    _write_text(os.path.join(case_dir, "secondary-publisher.xml"), f"""<?xml version="1.0" encoding="UTF-8"?>
<article xmlns="http://jats.nlm.nih.gov/ns/1.0">
  <front><article-meta>
    <article-id pub-id-type="doi">{xml_doi}</article-id>
    <title-group><article-title>Secondary Thermal Study</article-title></title-group>
    <contrib-group><contrib contrib-type="author"><name><surname>Wang</surname><given-names>Clara</given-names></name></contrib></contrib-group>
    <pub-date><year>2026</year></pub-date>
  </article-meta></front>
  <body>
    <sec sec-type="methods"><title>Methods</title>
      <p id="sec2-p01">Cu-MOF (Copper Metal-Organic Framework) was evaluated as a catalyst alongside a deionized control group at 350 K.</p>
    </sec>
    <sec sec-type="results"><title>Results</title>
      <p id="sec2-p02">At 350 K, Cu-MOF decreases CO2 yield to 41.0% after 12 h (p = 0.03).</p>
    </sec>
  </body>
</article>
""")


def _extract_pipeline_cli_contract():
    """Parse kg_pipeline.py AST to extract subcommands and valid --flags without execution."""
    with open(PIPELINE_PY, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=PIPELINE_PY)
    var_to_cmd = {}
    cmd_flags = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "add_parser":
                if node.value.args and isinstance(node.value.args[0], ast.Constant):
                    cmd_name = node.value.args[0].value
                    var_to_cmd[node.targets[0].id] = cmd_name
                    cmd_flags[cmd_name] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in var_to_cmd:
                cmd_name = var_to_cmd[node.func.value.id]
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                        cmd_flags[cmd_name].add(arg.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_mutually_exclusive_group":
            pass
    # Also resolve src_grp attached to p_patch
    grp_parent = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "add_mutually_exclusive_group":
                if isinstance(node.value.func.value, ast.Name) and node.value.func.value.id in var_to_cmd:
                    grp_parent[node.targets[0].id] = var_to_cmd[node.value.func.value.id]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in grp_parent:
                cmd_name = grp_parent[node.func.value.id]
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                        cmd_flags[cmd_name].add(arg.value)
    return cmd_flags


def _make_clean_ctx(tmp):
    case_d = os.path.join(tmp, "case")
    work_d = os.path.join(tmp, "work")
    out_d = os.path.join(tmp, "out")
    _create_sim_jats_case(case_d)
    structure_indexer.index_case(case_d, work_d, out_d)
    ev = _read_json(os.path.join(work_d, "evidence_index.json"))
    cm = _read_json(os.path.join(work_d, "case_manifest.json"))
    ps = _read_json(os.path.join(work_d, "paper_signals.json"))
    pub = pubinfo_extractor.extract_pubinfo_from_index(ev, ps, cm.get("case_metadata"), cm.get("root_case_metadata"))
    return case_d, work_d, out_d, ev, cm, ps, pub


def _base_clean_kg(cm, ev, pub):
    return {
        "schema_version": "1.0.0",
        "case_id": cm["case_id"],
        "ingest_mode": cm["ingest_mode"],
        "locator_scheme": cm["locator_scheme"],
        "independent_paper_count": 2,
        "documents": pub["sources"],
        "verification_twins": cm["verification_twins"],
        "twin_verification_notes": cm.get("twin_verification_notes", []),
        "content_limitations": cm.get("content_limitations", []),
        "nodes": [
            {
                "node_id": "N-001",
                "canonical_name": "Cu-MOF",
                "entity_type": "Material_or_Chemical",
                "aliases": ["Copper Metal-Organic Framework"],
                "source_ids": ["primary"],
                "independent_source_ids": ["primary"],
                "evidence": [{"source_id": "primary", "paragraph_id": "p-002", "verbatim_quote": "Copper Metal-Organic Framework (Cu-MOF)", "alignment": {"aligned": True, "resolved_source_id": "primary", "resolved_anchor_id": "primary::p-002", "resolved_paragraph_id": "p-002", "resolved_page_number": None, "section_role": "methods"}}],
            },
            {
                "node_id": "N-002",
                "canonical_name": "CO2 yield",
                "entity_type": "Metric_or_Property",
                "aliases": [],
                "source_ids": ["primary"],
                "independent_source_ids": ["primary"],
                "evidence": [{"source_id": "primary", "paragraph_id": "p-003", "verbatim_quote": "Cu-MOF increases CO2 yield to 84.5% (p < 0.01)", "alignment": {"aligned": True, "resolved_source_id": "primary", "resolved_anchor_id": "primary::p-003", "resolved_paragraph_id": "p-003", "resolved_page_number": None, "section_role": "results"}}],
            },
        ],
        "edges": [
            {
                "edge_id": "E-001",
                "subject_id": "N-001",
                "relation": "increases",
                "object_id": "N-002",
                "direction": "subject_to_object",
                "epistemic_status": "direct_experimental",
                "polarity": "positive",
                "source_id": "primary",
                "evidence": [{"source_id": "primary", "paragraph_id": "p-003", "verbatim_quote": "Our measurements show Cu-MOF increases CO2 yield to 84.5% (p < 0.01)", "alignment": {"aligned": True, "resolved_source_id": "primary", "resolved_anchor_id": "primary::p-003", "resolved_paragraph_id": "p-003", "resolved_page_number": None, "section_role": "results"}}],
            }
        ],
        "merge_decisions": [],
        "non_merge_decisions": [],
        "conflicts": [],
        "queries": [
            {"query_id": "Q1", "template_id": "single_hop_fact", "answer_status": "answered", "graph_grounded": True, "matched_node_ids": ["N-001", "N-002"], "matched_edge_ids": ["E-001"], "provenance_citations": [{"edge_id": "E-001", "source_id": "primary", "anchor_id": "primary::p-003", "page_number": None, "verbatim_quote": "Cu-MOF increases CO2 yield"}]},
            {"query_id": "Q2", "template_id": "cross_paper_comparison", "answer_status": "answered", "graph_grounded": True, "matched_node_ids": ["N-001"], "matched_edge_ids": ["E-001"], "provenance_citations": [{"edge_id": "E-001", "source_id": "primary", "anchor_id": "primary::p-003", "page_number": None, "verbatim_quote": "Cu-MOF increases CO2 yield"}]},
            {"query_id": "Q3", "template_id": "unreported_boundary_check", "answer_status": "unreported", "graph_grounded": True, "matched_node_ids": [], "matched_edge_ids": [], "provenance_citations": []},
        ],
    }


# ----------------------------------------------------------------------
# 41 Independent Tests
# ----------------------------------------------------------------------

def run_all_tests():
    results = []
    tmp_root = tempfile.mkdtemp(prefix="kg_selftest_")

    def _run(name, fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as exc:
            results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))

    # --- Group 1: Assets & Static Compliance (5) ---
    def t01():
        onto = _read_json(os.path.join(REFS_DIR, "kg_ontology.json"))
        align = _read_json(os.path.join(REFS_DIR, "entity_alignment_rules.json"))
        qtmp = _read_json(os.path.join(REFS_DIR, "query_templates.json"))
        ref_ex = _read_json(os.path.join(REFS_DIR, "reference_kg_example.json"))
        mb = _read_json(os.path.join(REFS_DIR, "mini_benchmark.json"))
        assert len(onto["entity_types"]) == 8
        assert len(onto["relations"]) == 35
        assert len(onto["epistemic_statuses"]) == 4
        assert len(align["non_merge_guardrails"]) >= 7
        assert len(qtmp["templates"]) == 6
        assert len(mb["items"]) == 12
        assert ref_ex["locator_scheme"] == "jats_paragraph" and len(ref_ex["queries"]) == 6

    def t02():
        sm_path = os.path.join(SKILL_ROOT, "SKILL.md")
        raw = _read_bytes(sm_path)
        assert len(raw) <= 12288, f"SKILL.md too large: {len(raw)}"
        assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
        txt = raw.decode("utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
        assert m, "Missing YAML frontmatter"
        fm = m.group(1)
        assert "name: reading-10-knowledge-graph" in fm and "description:" in fm
        try:
            import yaml
            parsed = yaml.safe_load(fm)
            assert parsed.get("name") == "reading-10-knowledge-graph" and len(parsed.get("description", "")) > 20
        except ImportError:
            pass

    def t03():
        contract = _extract_pipeline_cli_contract()
        assert {"bootstrap", "inspect", "patch", "finish", "query"}.issubset(contract.keys())
        for doc_name in ("SKILL.md", "README.md"):
            txt = _read_text(os.path.join(SKILL_ROOT, doc_name))
            collapsed = re.sub(r"\\\n\s*", " ", txt)
            for m in re.finditer(r"kg_pipeline\.py\s+([a-z_]+)([^\n]*)", collapsed):
                subcmd = m.group(1)
                tail = m.group(2)
                assert subcmd in contract, f"{doc_name}: unknown subcommand '{subcmd}'"
                for flg in re.findall(r"(--[a-zA-Z0-9\-]+)", tail):
                    assert flg in contract[subcmd], f"{doc_name}: phantom flag '{flg}' for '{subcmd}' (valid={contract[subcmd]})"

    def t04():
        forbidden = [b"C:\\" + b"Users", b"/Use" + b"rs/", b"local_" + b"bench", b"bo" + b"er"]
        total_bytes = 0
        f_count = 0
        for root, dirs, files in os.walk(SKILL_ROOT):
            assert "__pycache__" not in dirs, f"__pycache__ in {root}"
            for fn in files:
                fp = os.path.join(root, fn)
                assert not os.path.islink(fp), f"Symlink found: {fp}"
                assert not fn.endswith(".pyc"), f".pyc found: {fp}"
                raw = _read_bytes(fp)
                f_count += 1
                total_bytes += len(raw)
                assert not raw.startswith(b"\xef\xbb\xbf"), f"BOM in {fp}"
                assert b"\r" not in raw, f"CR/CRLF in {fp}"
                raw.decode("utf-8")
                for bad in forbidden:
                    assert bad not in raw, f"Forbidden local token {bad!r} in {fp}"
        assert f_count <= 30 and total_bytes <= 1024 * 1024

    def t05():
        allowed_stdlib = getattr(sys, "stdlib_module_names", set())
        local_mods = {
            "pdf_engine", "case_ingestor", "structure_indexer", "pubinfo_extractor",
            "entity_guard", "evidence_aligner", "kg_assembler", "kg_query_engine",
            "kg_auditor", "render_deliverables", "validate_output", "kg_pipeline",
        }
        tmp_c = os.path.join(tmp_root, "pycomp")
        os.makedirs(tmp_c, exist_ok=True)
        for root, _dirs, files in os.walk(SCRIPT_DIR):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(root, fn)
                py_compile.compile(fp, cfile=os.path.join(tmp_c, fn + "c"), doraise=True)
                tree = ast.parse(_read_text(fp), filename=fp)
                for node in tree.body:
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            assert a.name.split(".")[0] in allowed_stdlib | local_mods, f"{fn}: {a.name}"
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        assert node.module.split(".")[0] in allowed_stdlib | local_mods, f"{fn}: {node.module}"

    _run("T01_references_json_validity", t01)
    _run("T02_skill_md_budget_and_frontmatter", t02)
    _run("T03_ast_cli_zero_drift", t03)
    _run("T04_hygiene_utf8_lf_no_leaks", t04)
    _run("T05_ast_imports_and_py_compile", t05)

    # --- Group 2: Dual-Mode Ingestion, Dual Locators & Metadata Isolation (6) ---
    def t06():
        _, _, _, ev, cm, _, _ = _make_clean_ctx(os.path.join(tmp_root, "t06"))
        assert cm["independent_paper_count"] == 2
        assert ev["locator_scheme"] == "jats_paragraph"
        assert all(a["page_number"] is None and "::" in a["anchor_id"] for a in ev["anchors"])

    def t07():
        _, _, _, _, cm, _, _ = _make_clean_ctx(os.path.join(tmp_root, "t07"))
        assert len(cm["verification_twins"]) == 1 and cm["verification_twins"][0]["is_verification_twin"] is True
        assert cm["jats_summary"]["dedup_skipped_from_material_index"] == 2

    def t08():
        d = os.path.join(tmp_root, "t08")
        c, w = os.path.join(d, "case"), os.path.join(d, "work")
        _create_sim_jats_case(c, xml_doi="10.2000/xml.mismatch.999")
        m = case_ingestor.ingest_case(c, w)
        assert any("DOI mismatch" in n for n in m["twin_verification_notes"])

    def t09():
        d = os.path.join(tmp_root, "t09")
        c, w = os.path.join(d, "case"), os.path.join(d, "work")
        _create_sim_jats_case(c)
        # Strip secondary DOI/title/authors from manifest & XML to ensure root case.json NEVER leaks
        _write_json(os.path.join(c, "corpus-manifest.json"), {
            "corpus": [
                {"source_id": "primary", "role": "primary", "files": ["primary-material.json"]},
                {"source_id": "secondary", "role": "secondary", "files": ["secondary-material.json"]},
            ]
        })
        os.remove(os.path.join(c, "secondary-publisher.xml"))
        structure_indexer.index_case(c, w)
        ev = _read_json(os.path.join(w, "evidence_index.json"))
        cm = _read_json(os.path.join(w, "case_manifest.json"))
        pub = pubinfo_extractor.extract_pubinfo_from_index(ev, case_data=cm["case_metadata"], root_case_metadata=cm["root_case_metadata"])
        by_sid = {s["source_id"]: s for s in pub["sources"]}
        assert by_sid["primary"]["doi"] == "10.1000/prim.2025.101"
        assert by_sid["secondary"]["doi"] is None and by_sid["secondary"]["title"] is None
        bad = [dict(by_sid["primary"]), dict(by_sid["secondary"], doi="10.1000/prim.2025.101")]
        assert pubinfo_extractor.detect_root_metadata_leakage(bad, cm["root_case_metadata"])["leakage_detected"] is True

    def t10():
        d = os.path.join(tmp_root, "t10")
        md_c, md_w = os.path.join(d, "md_c"), os.path.join(d, "md_w")
        _write_text(os.path.join(md_c, "paper.md"), "# Graphene Catalyst\n\n## Results\nGraphene increases conductivity by 40%.\n")
        _write_text(os.path.join(md_c, "notes.tex"), "\\title{LaTeX Test}\n\\begin{document}\n\\section{Results}\nYield reached 75\\%.\n\\end{document}\n")
        m_md = case_ingestor.ingest_case(md_c, md_w)
        assert m_md["locator_scheme"] == "text_block" and m_md["total_units"] >= 2
        pdf_c, pdf_w = os.path.join(d, "pdf_c"), os.path.join(d, "pdf_w")
        _build_minimal_valid_pdf(os.path.join(pdf_c, "mini.pdf"))
        s_pdf = structure_indexer.index_case(pdf_c, pdf_w)
        assert s_pdf["locator_scheme"] == "pdf_page_block" and s_pdf["anchors"] >= 1

    def t11():
        d = os.path.join(tmp_root, "t11")
        c, w = os.path.join(d, "empty_c"), os.path.join(d, "empty_w")
        os.makedirs(c, exist_ok=True)
        _write_json(os.path.join(c, "case.json"), {"case_id": "empty_case"})
        m = case_ingestor.ingest_case(c, w)
        assert m["status"] == "partial" and m["unextractable_asset_count"] >= 1

    _run("T06_jats_dual_paper_ingestion", t06)
    _run("T07_verification_twin_and_material_dedup", t07)
    _run("T08_xml_json_metadata_discrepancy_note", t08)
    _run("T09_pubinfo_root_isolation_and_leakage_catch", t09)
    _run("T10_mode_b_markdown_latex_and_synthetic_pdf", t10)
    _run("T11_unreadable_or_missing_source_degradation", t11)

    # --- Group 3: Entity Normalization, Alignment Guard & Verbatim Alignment (5) ---
    def t12():
        blocks = [{"anchor_id": "secondary::sec2-p02", "source_id": "secondary", "doc_id": "D2", "locator_type": "jats_paragraph", "paragraph_id": "sec2-p02", "page": None, "section_role": "results", "text": "At 350 K, Cu-MOF decreases CO_2 yield to 41.0% after 12 h."}]
        r = evidence_aligner.align_verbatim_quote("Cu-MOF decreases CO2 yield to 41.0%", "secondary", "sec2-p02", blocks)
        assert r["aligned"] is True and r["resolved_page_number"] is None

    def t13():
        blocks = [
            {"source_id": "primary", "anchor_id": "primary::p-002", "text": "We prepared Copper Metal-Organic Framework (Cu-MOF) as catalyst."},
            {"source_id": "secondary", "anchor_id": "secondary::sec2-p01", "text": "Cu-MOF (Copper Metal-Organic Framework) was evaluated."},
        ]
        acr = entity_guard.extract_acronym_bindings(blocks)
        assert "CU-MOF" in acr["by_source"]["primary"] and "CU-MOF" in acr["by_source"]["secondary"]
        res = entity_guard.evaluate_entity_merge_safety(
            {"name": "Cu-MOF", "entity_type": "Material_or_Chemical", "source_id": "primary", "acronym_expansion": acr["by_source"]["primary"]["CU-MOF"]["full_name"]},
            {"name": "Copper Metal-Organic Framework", "entity_type": "Material_or_Chemical", "source_id": "secondary", "acronym_expansion": acr["by_source"]["secondary"]["CU-MOF"]["full_name"]},
        )
        assert res["verdict"] == "safe_merge"

    def t14():
        for term, et in (("control group", "Dataset_or_Sample"), ("catalyst", "Material_or_Chemical"), ("CNN", "Method_or_Model"), ("baseline", "Method_or_Model")):
            r = entity_guard.evaluate_entity_merge_safety(
                {"name": term, "entity_type": et, "source_id": "primary"},
                {"name": term, "entity_type": et, "source_id": "secondary"},
            )
            assert r["verdict"] == "context_conflict_do_not_merge", f"Failed guardrail for {term}: {r}"

    def t15():
        reg = {"documents": [{"doc_id": "D1", "source_id": "primary", "source_file": "primary-material.json"}, {"doc_id": "D2", "source_id": "secondary", "source_file": "secondary-material.json", "verification_twin_file": "secondary-publisher.xml"}], "verification_twins": [{"twin_file": "secondary-publisher.xml", "twin_of_source_id": "secondary"}]}
        assert evidence_aligner.verify_cross_paper_independence([{"file": "secondary-material.json"}, {"file": "secondary-publisher.xml"}], reg)["is_truly_cross_paper"] is False
        assert evidence_aligner.verify_cross_paper_independence([{"source_id": "primary"}, {"source_id": "secondary"}], reg)["is_truly_cross_paper"] is True

    def t16():
        _, work_d, _, _, _, _, _ = _make_clean_ctx(os.path.join(tmp_root, "t16"))
        os.makedirs(os.path.join(work_d, "draft_batches"), exist_ok=True)
        _write_json(os.path.join(work_d, "draft_batches", "batch_001_v1_primary.json"), {
            "nodes": [{
                "node_id": "N-001",
                "canonical_name": "Cu-MOF",
                "entity_type": "Material_or_Chemical",
                "aliases": "Copper Metal-Organic Framework",
                "source_ids": "primary",
                "evidence": {"paragraph_id": "p-002", "verbatim_quote": "Copper Metal-Organic Framework (Cu-MOF)"},
            }],
            "edges": [],
        })
        akg = kg_assembler.assemble_kg(work_d)
        n = akg["nodes"][0]
        assert n["aliases"] == ["Copper Metal-Organic Framework"]
        assert n["source_ids"] == ["primary"]
        assert isinstance(n["evidence"], list) and n["evidence"][0]["alignment"]["aligned"] is True

    _run("T12_verbatim_alignment_ligatures_and_linear_math", t12)
    _run("T13_acronym_binding_and_safe_merge", t13)
    _run("T14_generic_term_non_merge_guardrail", t14)
    _run("T15_cross_paper_independence_twin_rejection", t15)
    _run("T16_assembler_lightweight_normalization", t16)

    # --- Group 4: 18 Audit Rules Individual Coverage (T17 - T34) ---
    base_c, base_w, base_o, base_ev, base_cm, base_ps, base_pub = _make_clean_ctx(os.path.join(tmp_root, "audit_base"))

    def _check_rule(mutate_fn, expected_code, expect_partial=True):
        kg = _base_clean_kg(base_cm, base_ev, base_pub)
        pub_c = json.loads(json.dumps(base_pub))
        cm_c = json.loads(json.dumps(base_cm))
        ps_c = json.loads(json.dumps(base_ps))
        mutate_fn(kg, pub_c, cm_c, ps_c)
        rep = kg_auditor.audit_kg(kg, base_ev, cm_c, pub_c, ps_c)
        codes = {i["code"] for i in rep["issues"]}
        assert expected_code in codes, f"Expected {expected_code}, got {codes}"
        st, _ = kg_auditor.determine_honest_status(rep, cm_c, kg)
        if expect_partial:
            assert st in ("partial", "failed"), f"Expected partial/failed for {expected_code}, got {st}"
        return rep, st

    _run("T17_audit_01_ROOT_METADATA_LEAKAGE", lambda: _check_rule(
        lambda kg, pub, cm, ps: pub["leakage_check"].update({"leakage_detected": True, "alerts": [{"source_id": "secondary", "message": "Leaked root DOI"}]}),
        "ROOT_METADATA_LEAKAGE",
    ))
    _run("T18_audit_02_FAKE_PDF_PAGE_IN_JATS", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["nodes"][0]["evidence"][0].update({"claimed_page_number": 1}),
        "FAKE_PDF_PAGE_IN_JATS",
    ))
    _run("T19_audit_03_SAME_PAPER_MULTI_FORMAT_AS_CROSS_PAPER", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["merge_decisions"].append({"decision_id": "M-TWIN", "canonical_node_id": "N-001", "merged_source_ids": ["secondary"], "is_cross_paper": True, "evidence_refs": [{"file": "secondary-material.json"}, {"file": "secondary-publisher.xml"}]}),
        "SAME_PAPER_MULTI_FORMAT_AS_CROSS_PAPER",
    ))
    _run("T20_audit_04_UNGROUNDED_NODE", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["nodes"][0].update({"evidence": []}),
        "UNGROUNDED_NODE",
    ))
    _run("T21_audit_05_UNGROUNDED_EDGE", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["edges"][0]["evidence"][0]["alignment"].update({"aligned": False}),
        "UNGROUNDED_EDGE",
    ))
    _run("T22_audit_06_DANGLING_EDGE_ENDPOINT", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["edges"][0].update({"object_id": "N-999"}),
        "DANGLING_EDGE_ENDPOINT",
    ))
    _run("T23_audit_07_INVALID_ENTITY_TYPE", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["nodes"][0].update({"entity_type": "UnknownType"}),
        "INVALID_ENTITY_TYPE",
    ))
    _run("T24_audit_08_INVALID_RELATION_TYPE", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["edges"][0].update({"relation": "co_occurrence_only", "epistemic_status": "co_occurrence_only"}),
        "INVALID_RELATION_TYPE",
    ))
    _run("T25_audit_09_RELATION_TYPE_CONSTRAINT_VIOLATION", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["edges"][0].update({"relation": "authored_by"}),
        "RELATION_TYPE_CONSTRAINT_VIOLATION",
        expect_partial=False,
    ))
    _run("T26_audit_10_EPISTEMIC_UPGRADE_VIOLATION", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["edges"][0]["evidence"][0].update({"resolved_anchor_id": "primary::p-001", "section_role": "introduction", "verbatim_quote": "Earlier work reported that MOFs promote adsorption (Smith et al., 2021) [12]."}),
        "EPISTEMIC_UPGRADE_VIOLATION",
    ))
    _run("T27_audit_11_CAUSAL_STRENGTH_OVERCLAIM", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["edges"][0].update({"relation": "causes", "evidence": [{"source_id": "primary", "paragraph_id": "p-004", "verbatim_quote": "Open sites correlate with activity and may improve durability.", "alignment": {"aligned": True, "resolved_source_id": "primary", "resolved_anchor_id": "primary::p-004", "resolved_paragraph_id": "p-004", "resolved_page_number": None, "section_role": "discussion"}}]}),
        "CAUSAL_STRENGTH_OVERCLAIM",
    ))
    _run("T28_audit_12_GENERIC_TERM_OVERMERGE", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["nodes"].append({"node_id": "N-GEN", "canonical_name": "control group", "entity_type": "Dataset_or_Sample", "source_ids": ["primary", "secondary"], "independent_source_ids": ["primary", "secondary"], "evidence": [{"source_id": "primary", "paragraph_id": "p-002", "verbatim_quote": "aqueous control group", "alignment": {"aligned": True, "resolved_anchor_id": "primary::p-002", "resolved_page_number": None}}]}),
        "GENERIC_TERM_OVERMERGE",
    ))
    _run("T29_audit_13_UNLOGGED_DUPLICATE_SURFACE", lambda: _check_rule(
        lambda kg, pub, cm, ps: (
            kg["nodes"].append({"node_id": "N-DUP", "canonical_name": "Cu-MOF", "entity_type": "Material_or_Chemical", "source_ids": ["secondary"], "independent_source_ids": ["secondary"], "evidence": [{"source_id": "secondary", "paragraph_id": "sec2-p01", "verbatim_quote": "Cu-MOF (Copper Metal-Organic Framework)", "alignment": {"aligned": True, "resolved_anchor_id": "secondary::sec2-p01", "resolved_page_number": None}}]}),
            kg["edges"].append({"edge_id": "E-DUP", "subject_id": "N-DUP", "relation": "increases", "object_id": "N-002", "epistemic_status": "direct_experimental", "source_id": "secondary", "evidence": [{"source_id": "secondary", "paragraph_id": "sec2-p02", "verbatim_quote": "At 350 K, Cu-MOF decreases CO_2 yield to 41.0% after 12 h (p = 0.03).", "alignment": {"aligned": True, "resolved_anchor_id": "secondary::sec2-p02", "resolved_page_number": None}}]}),
        ),
        "UNLOGGED_DUPLICATE_SURFACE",
        expect_partial=False,
    ))
    _run("T30_audit_14_QUERY_GRAPH_DRIFT", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["queries"][0].update({"drift_detected": True, "dangling_claimed_ids": {"node_ids": [], "edge_ids": ["E-999"]}}),
        "QUERY_GRAPH_DRIFT",
    ))
    _run("T31_audit_15_INSUFFICIENT_QUERY_COVERAGE", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg.update({"queries": []}),
        "INSUFFICIENT_QUERY_COVERAGE",
    ))
    _run("T32_audit_16_ORPHAN_NODE_WARNING", lambda: _check_rule(
        lambda kg, pub, cm, ps: kg["nodes"].append({"node_id": "N-ORPH", "canonical_name": "Aqueous phase", "entity_type": "Experimental_Condition", "source_ids": ["primary"], "independent_source_ids": ["primary"], "evidence": [{"source_id": "primary", "paragraph_id": "p-002", "verbatim_quote": "at 298 K", "alignment": {"aligned": True, "resolved_anchor_id": "primary::p-002", "resolved_page_number": None}}]}),
        "ORPHAN_NODE_WARNING",
        expect_partial=False,
    ))
    _run("T33_audit_17_SOURCE_COVERAGE_BLOCKER", lambda: _check_rule(
        lambda kg, pub, cm, ps: ps["documents"][0]["quality_risk"].update({"unextractable_pages": [1]}),
        "SOURCE_COVERAGE_BLOCKER",
    ))
    def t34():
        rep, st = _check_rule(
            lambda kg, pub, cm, ps: kg["conflicts"].append({"conflict_id": "C-001", "conflict_scope": "cross_paper", "conflict_type": "polarity_opposite", "involved_node_ids": ["N-001", "N-002"], "involved_edge_ids": ["E-001"], "summary": "Conflict preserved", "resolution_policy": "preserved_without_averaging"}),
            "SCIENTIFIC_CONFLICT_PRESERVED",
            expect_partial=False,
        )
        assert st == "success" and rep["high_blocker_count"] == 0

    _run("T34_audit_18_SCIENTIFIC_CONFLICT_PRESERVED", t34)

    # --- Group 5: Full CLI Pipeline, Diff, 6 Queries, Deliverables & Determinism (6) ---
    cli_env = dict(os.environ)
    cli_env["PYTHONDONTWRITEBYTECODE"] = "1"

    def _cli(args, expect_code=0):
        p = subprocess.run([sys.executable, "-B", PIPELINE_PY] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=cli_env)
        assert p.returncode == expect_code, f"CLI {args} rc={p.returncode}: {p.stdout}\n{p.stderr}"
        return json.loads(p.stdout)

    pipe_dir = os.path.join(tmp_root, "pipe_e2e")
    p_case = os.path.join(pipe_dir, "case")
    p_work = os.path.join(pipe_dir, "work")
    p_out = os.path.join(pipe_dir, "out")
    _create_sim_jats_case(p_case)

    def t35():
        b = _cli(["bootstrap", "--input", p_case, "--work", p_work, "--output", p_out])
        assert b["early_fallback_written"] is True and b["independent_paper_count"] == 2
        ins = _cli(["inspect", "--work", p_work, "--source", "primary", "--role", "results", "--limit", "5"])
        assert ins["matched_count"] >= 1 and ins["anchors"][0]["paragraph_id"] == "p-003"
        b1p = os.path.join(p_work, "in_b1.json")
        _write_json(b1p, {
            "nodes": [
                {"node_id": "N-001", "canonical_name": "Cu-MOF", "entity_type": "Material_or_Chemical", "source_ids": ["primary"], "evidence": [{"paragraph_id": "p-002", "verbatim_quote": "Copper Metal-Organic Framework (Cu-MOF)"}]},
                {"node_id": "N-002", "canonical_name": "CO2 yield", "entity_type": "Metric_or_Property", "source_ids": ["primary"], "evidence": [{"paragraph_id": "p-003", "verbatim_quote": "Cu-MOF increases CO2 yield to 84.5% (p < 0.01)"}]},
                {"node_id": "N-003", "canonical_name": "control group", "entity_type": "Dataset_or_Sample", "source_ids": ["primary"], "context_scope": "primary 298K", "evidence": [{"paragraph_id": "p-002", "verbatim_quote": "aqueous control group at 298 K"}]},
            ],
            "edges": [
                {"edge_id": "E-001", "subject_id": "N-001", "relation": "increases", "object_id": "N-002", "epistemic_status": "direct_experimental", "polarity": "positive", "quantitative_value": "84.5%", "source_id": "primary", "evidence": [{"paragraph_id": "p-003", "verbatim_quote": "Our measurements show Cu-MOF increases CO2 yield to 84.5% (p < 0.01)"}]},
                {"edge_id": "E-002", "subject_id": "N-001", "relation": "outperforms", "object_id": "N-003", "epistemic_status": "direct_experimental", "polarity": "positive", "source_id": "primary", "evidence": [{"paragraph_id": "p-003", "verbatim_quote": "outperforms the control group"}]},
            ],
        })
        p1 = _cli(["patch", "--work", p_work, "--batch-file", b1p, "--stage", "v1_primary"])
        assert not p1["precheck_warnings"]
        b2p = os.path.join(p_work, "in_b2.json")
        _write_json(b2p, {
            "nodes": [
                {"node_id": "N-001", "replaces_id": "N-001", "canonical_name": "Cu-MOF", "entity_type": "Material_or_Chemical", "acronym_expansion": "Copper Metal-Organic Framework", "source_ids": ["primary", "secondary"], "evidence": [{"source_id": "primary", "paragraph_id": "p-002", "verbatim_quote": "Copper Metal-Organic Framework (Cu-MOF)"}, {"source_id": "secondary", "paragraph_id": "sec2-p01", "verbatim_quote": "Cu-MOF (Copper Metal-Organic Framework)"}]},
                {"node_id": "N-004", "canonical_name": "control group", "entity_type": "Dataset_or_Sample", "source_ids": ["secondary"], "context_scope": "secondary 350K", "evidence": [{"paragraph_id": "sec2-p01", "verbatim_quote": "deionized control group at 350 K"}]},
            ],
            "edges": [
                {"edge_id": "E-003", "subject_id": "N-001", "relation": "decreases", "object_id": "N-002", "epistemic_status": "direct_experimental", "polarity": "negative", "quantitative_value": "41.0%", "source_id": "secondary", "evidence": [{"paragraph_id": "sec2-p02", "verbatim_quote": "At 350 K, Cu-MOF decreases CO_2 yield to 41.0% after 12 h (p = 0.03)."}]},
                {"edge_id": "E-004", "subject_id": "N-001", "relation": "evaluates_on", "object_id": "N-004", "epistemic_status": "definition_or_setup", "polarity": "neutral", "source_id": "secondary", "evidence": [{"paragraph_id": "sec2-p01", "verbatim_quote": "evaluated as a catalyst alongside a deionized control group at 350 K"}]},
            ],
            "merge_decisions": [{"decision_id": "M-001", "canonical_node_id": "N-001", "merged_mentions": ["Cu-MOF", "Copper Metal-Organic Framework"], "merged_source_ids": ["primary", "secondary"], "merge_basis": "Matching acronyms", "evidence_refs": [{"source_id": "primary", "paragraph_id": "p-002"}, {"source_id": "secondary", "paragraph_id": "sec2-p01"}]}],
            "non_merge_decisions": [{"decision_id": "NM-001", "node_a_id": "N-003", "node_b_id": "N-004", "surface_term": "control group", "reason_code": "GUARD_GENERIC_CONTROL_AND_COHORT", "explanation": "Distinct conditions"}],
        })
        p2 = _cli(["patch", "--work", p_work, "--batch-file", b2p, "--stage", "v2_secondary_update"])
        assert not p2["precheck_warnings"]
        fin = _cli(["finish", "--work", p_work, "--output", p_out])
        assert fin["status"] == "success" and fin["high_blocker_count"] == 0 and fin["validation_passed"] is True
        qres = _cli(["query", "--work", p_work, "--question", "Trace Cu-MOF", "--type", "provenance_reverse_trace", "--entity", "N-001"])
        assert qres["answer_status"] == "answered"

    def t36():
        kg = _read_json(os.path.join(p_out, "knowledge_graph.json"))
        diff = kg["incremental_update_diff"]
        assert diff["v1_primary_counts"] == {"nodes": 2, "edges": 2}
        assert diff["v2_total_counts"] == {"nodes": 4, "edges": 4}
        assert "N-001" in diff["cross_paper_merged_node_ids"]
        assert len(diff["newly_introduced_conflict_ids"]) >= 1
        # Test 1-round hotfix self-heal on a separate work copy
        h_work, h_out = os.path.join(tmp_root, "h_work"), os.path.join(tmp_root, "h_out")
        _cli(["bootstrap", "--input", p_case, "--work", h_work, "--output", h_out])
        bad_p = os.path.join(h_work, "bad.json")
        _write_json(bad_p, {
            "nodes": [
                {"node_id": "N-1", "canonical_name": "Cu-MOF", "entity_type": "Material_or_Chemical", "source_ids": ["primary"], "evidence": [{"paragraph_id": "p-002", "page_number": 1, "verbatim_quote": "Copper Metal-Organic Framework (Cu-MOF)"}]},
                {"node_id": "N-2", "canonical_name": "CO2 yield", "entity_type": "Metric_or_Property", "source_ids": ["primary"], "evidence": [{"paragraph_id": "p-003", "verbatim_quote": "Cu-MOF increases CO2 yield to 84.5% (p < 0.01)"}]},
            ],
            "edges": [{"edge_id": "E-1", "subject_id": "N-1", "relation": "increases", "object_id": "N-2", "epistemic_status": "direct_experimental", "source_id": "primary", "evidence": [{"paragraph_id": "p-003", "verbatim_quote": "Our measurements show Cu-MOF increases CO2 yield to 84.5% (p < 0.01)"}]}],
        })
        _cli(["patch", "--work", h_work, "--batch-file", bad_p, "--stage", "v1_primary"])
        f_bad = _cli(["finish", "--work", h_work, "--output", h_out])
        assert f_bad["status"] == "partial" and f_bad["high_blocker_count"] >= 1
        fix_p = os.path.join(h_work, "fix.json")
        _write_json(fix_p, {"nodes": [{"node_id": "N-1", "replaces_id": "N-1", "canonical_name": "Cu-MOF", "entity_type": "Material_or_Chemical", "source_ids": ["primary"], "evidence": [{"paragraph_id": "p-002", "verbatim_quote": "Copper Metal-Organic Framework (Cu-MOF)"}]}]})
        _cli(["patch", "--work", h_work, "--batch-file", fix_p, "--stage", "hotfix"])
        f_ok = _cli(["finish", "--work", h_work, "--output", h_out])
        assert f_ok["status"] == "success" and f_ok["high_blocker_count"] == 0

    def t37():
        kg = _read_json(os.path.join(p_out, "knowledge_graph.json"))
        q1 = kg_query_engine.execute_single_query({"query_id": "T1", "query_type": "single_hop_fact", "question": "Cu-MOF", "target_entities": ["N-001"]}, kg)
        q2 = kg_query_engine.execute_single_query({"query_id": "T2", "query_type": "multi_hop_path", "question": "Path N-003 to N-004", "target_entities": ["N-003", "N-004"]}, kg)
        q3 = kg_query_engine.execute_single_query({"query_id": "T3", "query_type": "cross_paper_comparison", "question": "Compare", "target_entities": ["N-001"]}, kg)
        q4 = kg_query_engine.execute_single_query({"query_id": "T4", "query_type": "conflict_and_disambiguation", "question": "Conflicts"}, kg)
        q5 = kg_query_engine.execute_single_query({"query_id": "T5", "query_type": "provenance_reverse_trace", "question": "Trace E-001", "claimed_edge_ids": ["E-001"]}, kg)
        q6 = kg_query_engine.execute_single_query({"query_id": "T6", "query_type": "unreported_boundary_check", "question": "What are raw image pixels?"}, kg)
        assert q1["answer_status"] == "answered" and len(q1["matched_edge_ids"]) >= 1
        assert q2["answer_status"] == "answered" and len(q2["evidence_paths"]) >= 1 and len(q2["evidence_paths"][0]["hops"]) == 2
        assert q3["answer_status"] == "answered" and q4["answer_status"] == "answered" and q5["answer_status"] == "answered"
        assert q6["answer_status"] == "unreported" and q6["matched_node_ids"] == []

    def t38():
        res = _read_json(os.path.join(p_out, "result.json"))
        assert res["case_id"] == "selftest_jats_case"
        assert all(k in res for k in ("case_id", "status", "graph", "queries"))
        gml_root = ET.fromstring(_read_text(os.path.join(p_out, "knowledge_graph.graphml")))
        ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
        assert len(gml_root.findall(".//g:node", ns)) == 4
        assert len(gml_root.findall(".//g:edge", ns)) == 4
        ET.fromstring(_read_text(os.path.join(p_out, "kg_overview.svg")))
        md_txt = _read_text(os.path.join(p_out, "knowledge_graph_report.md"))
        assert "```mermaid" in md_txt and "18 项硬核审计摘要" in md_txt
        ok, rep = validate_output.validate_deliverables(p_out, work_dir=p_work)
        assert ok, rep

    def t39():
        kg1 = _read_json(os.path.join(p_out, "knowledge_graph.json"))
        _cli(["finish", "--work", p_work, "--output", p_out])
        kg2 = _read_json(os.path.join(p_out, "knowledge_graph.json"))
        assert kg1 == kg2

    def t40():
        spoof_out = os.path.join(tmp_root, "spoof_out")
        shutil.copytree(p_out, spoof_out)
        spoof_work = os.path.join(tmp_root, "spoof_work")
        shutil.copytree(p_work, spoof_work)
        aud = _read_json(os.path.join(spoof_work, "audit_report.json"))
        aud["high_blocker_count"] = 1
        aud["blocker_issues"] = [{"code": "UNGROUNDED_EDGE", "severity": "HIGH"}]
        _write_json(os.path.join(spoof_work, "audit_report.json"), aud)
        ok, rep = validate_output.validate_deliverables(spoof_out, work_dir=spoof_work)
        assert ok is False and any("Honest status mismatch" in e or "high_blocker_count" in e for e in rep["errors"])

    def test_stdlib_png_fallback_without_pymupdf():
        kg = _read_json(os.path.join(p_work, "assembled_kg.json"))
        aud = _read_json(os.path.join(p_work, "audit_report.json"))
        cm = _read_json(os.path.join(p_work, "case_manifest.json"))
        fb_out = os.path.join(tmp_root, "t41_stdlib_png_out")
        orig_import = builtins.__import__
        saved_mods = {}
        blocked_keys = ("pymupdf", "fitz", "PIL", "PIL.Image", "PIL.ImageDraw")
        for k in blocked_keys:
            if k in sys.modules:
                saved_mods[k] = sys.modules[k]
            sys.modules[k] = None

        def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            top = str(name or "").split(".")[0]
            if top in ("pymupdf", "fitz", "PIL"):
                raise ImportError(f"Mocked missing optional dependency: {name}")
            return orig_import(name, globals, locals, fromlist, level)

        builtins.__import__ = _guarded_import
        try:
            res = render_deliverables.render_all_deliverables(kg, aud, cm, fb_out)
        finally:
            builtins.__import__ = orig_import
            for k in blocked_keys:
                if k in saved_mods:
                    sys.modules[k] = saved_mods[k]
                else:
                    sys.modules.pop(k, None)

        assert res["kg_summary"]["png_preview_status"] == "rendered_stdlib_rasterizer", res["kg_summary"]
        assert res["artifacts"].get("kg_overview_png") == "kg_overview.png", res["artifacts"]
        png_path = os.path.join(fb_out, "kg_overview.png")
        assert os.path.isfile(png_path) and os.path.getsize(png_path) > 1024
        raw = _read_bytes(png_path)
        assert raw.startswith(b"\x89PNG\r\n\x1a\n") and raw[12:16] == b"IHDR"
        pw, ph = struct.unpack("!II", raw[16:24])
        assert pw >= 600 and ph >= 400, (pw, ph)

    _run("T35_cli_full_pipeline_and_inspect", t35)
    _run("T36_incremental_diff_and_hotfix_self_heal", t36)
    _run("T37_all_6_query_templates", t37)
    _run("T38_deliverables_graphml_csv_svg_contract", t38)
    _run("T39_determinism_2x_finish", t39)
    _run("T40_honest_status_anti_spoofing", t40)
    _run("T41_test_stdlib_png_fallback_without_pymupdf", test_stdlib_png_fallback_without_pymupdf)

    shutil.rmtree(tmp_root, ignore_errors=True)

    passed = sum(1 for _, st, _ in results if st == "PASS")
    failed = [(n, msg) for n, st, msg in results if st != "PASS"]
    print(json.dumps({
        "self_test_summary": "ALL_PASSED" if not failed else "FAILED",
        "total_tests": len(results),
        "passed": passed,
        "failed_count": len(failed),
        "failed_details": failed,
    }, ensure_ascii=False, indent=2))
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    run_all_tests()
