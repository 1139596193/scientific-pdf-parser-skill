#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_test.py - Offline Zero-Dependency 22-Case Regression Suite
Part of the Scientific Literature Notes Skill (task_06_notes).
Top-level imports are strictly restricted to standard libraries.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import contextlib
import io

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pdf_engine.layout_parser import (
    split_mixed_heading_and_body,
    classify_block_semantics,
    is_table_row_heuristic,
)
from structure_indexer import (
    classify_section_role,
    _clean_author_clues,
    _build_document_index,
    index_case,
)
from entity_guard import compare_claim_against_anchors
from note_assembler import assemble_notes
from note_auditor import audit_notes, _quote_matches_source
from render_deliverables import render_deliverables
from validate_output import validate_output


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def _make_sample_doc_and_index(tmp_root, extra_md=None, case_json=None):
    in_dir = os.path.join(tmp_root, "input")
    work_dir = os.path.join(tmp_root, "work")
    out_dir = os.path.join(tmp_root, "output")
    os.makedirs(in_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    md_text = extra_md or (
        "# Sample Quantum Paper\n\n"
        "## Abstract\n\n"
        "We propose a noise-resilient protocol achieving 84.3% fidelity.\n\n"
        "## 1. Introduction\n\n"
        "Quantum communication suffers from decoherence.\n\n"
        "## 2. Method\n\n"
        "Our algorithm runs in 95.3 s on benchmark circuits and does not improve worst-case latency when noise exceeds 10%.\n\n"
        "## 3. Experiments\n\n"
        "Table 1 shows accuracy reaches 84.3% across 5 trials.\n\n"
        "## 4. Limitations\n\n"
        "A key limitation is that the protocol assumes Markovian noise and fails to scale beyond 50 qubits.\n\n"
        "## 5. Conclusion\n\n"
        "We demonstrated verifiable improvements under bounded noise.\n\n"
        "## References\n\n"
        "[1] Alice et al., Quantum Notes, 2024.\n"
    )
    with open(os.path.join(in_dir, "paper.md"), "w", encoding="utf-8") as f:
        f.write(md_text)

    if case_json is not None:
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case_json, f, ensure_ascii=False, indent=2)
    else:
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "test_case_01", "output_contract": {"case_id": "string", "status": "string"}}, f)

    with _quiet():
        index_case(in_dir, work_dir, out_dir)
    ev = json.loads(open(os.path.join(work_dir, "evidence_index.json"), "r", encoding="utf-8").read())
    return in_dir, work_dir, out_dir, ev


def test_01_split_mixed_roman_heading():
    blk = {
        "text": "IV. APPLICATIONS TO PRIVATE QUANTUM HYPOTHESIS TESTING\nIn this section, we first review the problem setup of symmetric testing.",
        "bbox": [49.0, 520.0, 563.0, 591.0],
        "avg_size": 10.0,
        "fonts": ["Times-Bold"],
        "column": "full",
    }
    res = split_mixed_heading_and_body([blk])
    assert len(res) == 2, f"Expected 2 split blocks, got {len(res)}"
    assert res[0]["text"] == "IV. APPLICATIONS TO PRIVATE QUANTUM HYPOTHESIS TESTING"
    assert res[1]["text"].startswith("In this section, we first review")
    assert classify_block_semantics(res[0], 10.0, False) == "heading_h1"


def test_02_toc_no_false_reference_lock():
    blocks = [
        {"text": "CONTENTS", "bbox": [50, 100, 200, 120], "avg_size": 12.0, "fonts": ["Bold"]},
        {"text": "References 28", "bbox": [50, 140, 300, 160], "avg_size": 10.0, "fonts": ["Regular"]},
        {"text": "Appendix 30 A Properties . . . . . . . . . 30", "bbox": [50, 165, 350, 185], "avg_size": 10.0, "fonts": ["Regular"]},
        {"text": "I. INTRODUCTION", "bbox": [50, 210, 250, 230], "avg_size": 12.0, "fonts": ["Bold"]},
    ]
    t0 = classify_block_semantics(blocks[0], 10.0, False, in_toc=False)
    t1 = classify_block_semantics(blocks[1], 10.0, False, in_toc=True)
    t2 = classify_block_semantics(blocks[2], 10.0, False, in_toc=True)
    t3 = classify_block_semantics(blocks[3], 10.0, False, in_toc=False)
    assert t0 == "heading_h1"
    assert t1 == "paragraph", f"TOC References 28 leaked into {t1}"
    assert t2 == "paragraph", f"TOC Appendix 30 leaked into {t2}"
    assert t3 == "heading_h1"

    # Also test _build_document_index TOC guard when unit_type was paragraph/heading
    doc_meta = {"doc_id": "D1", "source_file": "toc_test.pdf", "page_count": 2, "diagnosis_summary": {"dominant_route": "native_clean"}}
    units = [
        {"unit_id": "U1", "unit_type": "heading", "raw_block_type": "heading_h1", "section_path": "CONTENTS", "page_span": [1, 1], "source_text": "CONTENTS", "word_count": 1, "char_count": 8, "reading_policy": "analyze", "avg_size": 12.0, "is_bold": True},
        {"unit_id": "U2", "unit_type": "paragraph", "raw_block_type": "paragraph", "section_path": "CONTENTS", "page_span": [1, 1], "source_text": "References 28", "word_count": 2, "char_count": 13, "reading_policy": "analyze", "avg_size": 10.0, "is_bold": False},
        {"unit_id": "U3", "unit_type": "heading", "raw_block_type": "heading_h1", "section_path": "I. INTRODUCTION", "page_span": [2, 2], "source_text": "I. INTRODUCTION", "word_count": 2, "char_count": 15, "reading_policy": "analyze", "avg_size": 12.0, "is_bold": True},
    ]
    anchors, secs, _, _ = _build_document_index(doc_meta, units)
    assert all(a["zone"] != "references_locked" for a in anchors)
    assert any(s["canonical_role"] == "toc" for s in secs)


def test_03_prose_table_ref_vs_real_caption():
    prose_blk = {"text": "Table 4 lists the optimal QLDP parameter (=optimal QDP parameter with η = 1) across devices.", "bbox": [50, 200, 500, 230], "avg_size": 10.0}
    real_blk = {"text": "TABLE 4. PRIVACY PARAMETERS OF COMMON SINGLE-QUBIT CHANNELS", "bbox": [50, 100, 500, 120], "avg_size": 9.5}
    zh_prose = {"text": "表 1 展示了不同量子信道参数下的保真度对比结果。", "bbox": [50, 300, 500, 320], "avg_size": 10.0}
    zh_cap = {"text": "表 1 不同量子信道参数在各测试集上的保真度对比", "bbox": [50, 260, 500, 280], "avg_size": 10.0}
    assert classify_block_semantics(prose_blk, 10.0, False) == "paragraph"
    assert classify_block_semantics(real_blk, 10.0, False) == "table_caption"
    assert classify_block_semantics(zh_prose, 10.0, False) == "paragraph"
    assert classify_block_semantics(zh_cap, 10.0, False) == "table_caption"


def test_04_table_caption_header_binding_dedup():
    doc_meta = {"doc_id": "D1", "source_file": "tab.pdf", "page_count": 2, "diagnosis_summary": {"dominant_route": "native_clean"}}
    units = [
        {"unit_id": "U1", "unit_type": "table_caption", "raw_block_type": "table_caption", "section_path": "Experiments", "page_span": [2, 2], "source_text": "表 1 不同信道参数对比", "word_count": 8, "char_count": 15, "reading_policy": "analyze"},
        {"unit_id": "U2", "unit_type": "paragraph", "raw_block_type": "paragraph", "section_path": "Experiments", "page_span": [2, 2], "source_text": "信道类型 纠缠度 保真度 状态", "word_count": 8, "char_count": 16, "reading_policy": "analyze"},
        {"unit_id": "U3", "unit_type": "table_content", "raw_block_type": "table_content", "section_path": "Experiments", "page_span": [2, 2], "source_text": "Depolarizing 0.892 0.9842 通过", "word_count": 4, "char_count": 28, "reading_policy": "analyze"},
    ]
    _, _, _, sig = _build_document_index(doc_meta, units)
    assert len(sig["tables"]) == 1, f"Expected deduplicated 1 table, got {len(sig['tables'])}"
    assert sig["tables"][0]["content_anchor_ids"] == ["P2-B002", "P2-B003"]


def test_05_bilingual_section_tree_and_roles():
    assert classify_section_role("2 系统模型与理论推导") == "method"
    assert classify_section_role("一、实验设计与仿真平台") == "experiments"
    assert classify_section_role("3 结语与展望") == "conclusion"
    assert classify_section_role("CONTENTS") == "toc"
    assert classify_section_role("IV. APPLICATIONS TO PRIVATE QUANTUM HYPOTHESIS TESTING") == "body"


def test_06_formula_pua_low_severity_notice():
    doc_meta = {"doc_id": "D1", "source_file": "pua.pdf", "page_count": 1, "diagnosis_summary": {"dominant_route": "native_clean"}}
    units = [
        {"unit_id": "U1", "unit_type": "display_equation", "raw_block_type": "formula", "section_path": "Method", "page_span": [1, 1], "source_text": "\uf8f1\n\uf8f2 x = 1 \uf8f3", "word_count": 25, "char_count": 40, "reading_policy": "preserve_source", "source_warnings": ["formula_pua_glyphs"]},
    ]
    _, _, _, sig = _build_document_index(doc_meta, units)
    assert sig["initial_uncertainty"] == [], "Formula PUA must not become severe body garble uncertainty"
    pua_notices = [n for n in sig["quality_notices"] if n["category"] == "formula_pua_glyphs"]
    assert len(pua_notices) == 1
    assert pua_notices[0]["severity"] == "low"


def test_07_multi_doc_ingestion_and_anchors():
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = os.path.join(tmp, "in")
        w_dir = os.path.join(tmp, "work")
        os.makedirs(in_dir, exist_ok=True)
        with open(os.path.join(in_dir, "a_main.md"), "w", encoding="utf-8") as f:
            f.write("# Main Paper\n\n## Introduction\n\nFirst paper text.\n")
        with open(os.path.join(in_dir, "b_supp.tex"), "w", encoding="utf-8") as f:
            f.write("\\title{Supplement}\n\\begin{document}\n\\section{Proofs}\nDetailed derivation.\n\\end{document}\n")
        with open(os.path.join(in_dir, "c_notes.txt"), "w", encoding="utf-8") as f:
            f.write("# Extra Notes\n\nPlain text note.\n")
        with _quiet():
            res = index_case(in_dir, w_dir)
        assert res["docs"] == 3
        ev = json.loads(open(os.path.join(w_dir, "evidence_index.json"), "r", encoding="utf-8").read())
        ids = [a["anchor_id"] for a in ev["anchors"]]
        assert any(i.startswith("D1-P1-B") for i in ids)
        assert any(i.startswith("D2-P1-B") for i in ids)
        assert any(i.startswith("D3-P1-B") for i in ids)


def test_08_assembler_incremental_patch_and_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        _, w_dir, _, ev = _make_sample_doc_and_index(tmp)
        p_dir = os.path.join(w_dir, "patches")
        os.makedirs(p_dir, exist_ok=True)
        p1 = {
            "paper_card": {
                "one_line_takeaway": {"value": "Noise-resilient protocol achieving 84.3% fidelity.", "anchor_ids": ["P1-B003"]},
                "tags": ["quantum", "noise-resilient"],
            },
            "section_summaries": [
                {"section_id": "S02", "summary": "Proposes protocol with 84.3% fidelity.", "key_points": [{"text": "Achieves 84.3% fidelity.", "anchor_ids": ["P1-B003"]}]}
            ],
        }
        p2 = {
            "knowledge_points": [
                {"kp_id": "KP-001", "category": "empirical_finding", "title": "Fidelity", "claim": "Fidelity reaches 84.3%.", "anchor_ids": ["P1-B003", "P1-B009"]}
            ]
        }
        open(os.path.join(p_dir, "01.json"), "w", encoding="utf-8").write(json.dumps(p1))
        open(os.path.join(p_dir, "02.json"), "w", encoding="utf-8").write(json.dumps(p2))
        with _quiet():
            notes = assemble_notes(w_dir, patches_dir=p_dir)
        assert len(notes["change_log"]) >= 3  # seed + 2 patches
        prov = notes["paper_card"]["one_line_takeaway"]["resolved_provenance"]
        assert len(prov) == 1 and prov[0]["valid"] is True and prov[0]["page"] == 1


def test_09_not_reported_in_source_normalization():
    with tempfile.TemporaryDirectory() as tmp:
        _, w_dir, _, _ = _make_sample_doc_and_index(tmp)
        p_path = os.path.join(w_dir, "p_norm.json")
        open(p_path, "w", encoding="utf-8").write(json.dumps({
            "paper_card": {
                "venue_or_source": "TBD",
                "doi": "N/A",
                "research_gap": "",
                "key_results": [{"metric": "Accuracy", "value": "84.3%", "baseline": "Unknown", "condition": "None", "summary": "Reaches 84.3%.", "anchor_ids": ["P1-B003"]}]
            }
        }))
        with _quiet():
            notes = assemble_notes(w_dir, patch_files=[p_path])
        pc = notes["paper_card"]
        assert pc["venue_or_source"]["provenance_status"] == "not_reported_in_source"
        assert pc["doi"]["provenance_status"] == "not_reported_in_source"
        assert pc["research_gap"]["provenance_status"] == "not_reported_in_source"
        assert pc["key_results"][0]["baseline"] == "not_reported_in_source"


def test_10_auditor_ghost_anchor_and_fabricated_quote():
    with tempfile.TemporaryDirectory() as tmp:
        _, w_dir, _, _ = _make_sample_doc_and_index(tmp)
        p_path = os.path.join(w_dir, "p_bad.json")
        open(p_path, "w", encoding="utf-8").write(json.dumps({
            "knowledge_points": [
                {"kp_id": "KP-GHOST", "category": "finding", "title": "Ghost", "claim": "Something", "anchor_ids": ["P99-B999"]},
                {"kp_id": "KP-QUOTE", "category": "finding", "title": "Fake Quote", "claim": "Decoherence occurs.", "anchor_ids": ["P1-B005"], "verbatim_quote": "This completely fabricated quote never appeared in the source."},
            ]
        }))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_path])
            rep = audit_notes(w_dir)
        types = {i["issue_type"] for i in rep["issues"]}
        assert "ghost_anchor" in types
        assert "fabricated_quote" in types


def test_11_auditor_hallucinated_number_and_unit_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        _, w_dir, _, _ = _make_sample_doc_and_index(tmp)
        p_path = os.path.join(w_dir, "p_num.json")
        open(p_path, "w", encoding="utf-8").write(json.dumps({
            "paper_card": {
                "key_results": [
                    {"result_id": "R1", "metric": "Fidelity", "value": "83.4%", "summary": "Achieves 83.4% fidelity.", "anchor_ids": ["P1-B003"]},
                    {"result_id": "R2", "metric": "Latency", "value": "95.3 ms", "summary": "Runs in 95.3 ms.", "anchor_ids": ["P1-B007"]},
                ]
            }
        }))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_path])
            rep = audit_notes(w_dir)
        types = {i["issue_type"] for i in rep["issues"]}
        assert "unsupported_number" in types, f"Expected unsupported_number, got {types}"
        assert "unit_mismatch" in types, f"Expected unit_mismatch, got {types}"


def test_12_auditor_negation_polarity_inversion():
    with tempfile.TemporaryDirectory() as tmp:
        _, w_dir, _, _ = _make_sample_doc_and_index(tmp)
        p_path = os.path.join(w_dir, "p_neg.json")
        open(p_path, "w", encoding="utf-8").write(json.dumps({
            "knowledge_points": [
                {"kp_id": "KP-NEG", "category": "empirical_finding", "title": "Worst-case latency", "claim": "Our algorithm improves worst-case latency when noise exceeds 10%.", "anchor_ids": ["P1-B007"]}
            ]
        }))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_path])
            rep = audit_notes(w_dir)
        types = {i["issue_type"] for i in rep["issues"]}
        assert "negation_polarity_mismatch" in types, f"Expected negation_polarity_mismatch, got {types}"


def test_13_auditor_ghost_crossref_and_missing_sections():
    with tempfile.TemporaryDirectory() as tmp:
        _, w_dir, _, _ = _make_sample_doc_and_index(tmp)
        p_path = os.path.join(w_dir, "p_ref.json")
        open(p_path, "w", encoding="utf-8").write(json.dumps({
            "knowledge_points": [
                {"kp_id": "KP-REF", "category": "empirical_finding", "title": "Phantom table", "claim": "As shown in Table 99, fidelity reaches 84.3%.", "anchor_ids": ["P1-B009"]}
            ]
        }))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_path])
            rep = audit_notes(w_dir)
        types = {i["issue_type"] for i in rep["issues"]}
        assert "ghost_cross_ref" in types
        assert "missing_section" in types
        assert "missing_limitations" in types


def test_14_scanned_degradation_uncertainty_and_partial():
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = os.path.join(tmp, "in")
        w_dir = os.path.join(tmp, "work")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(in_dir, exist_ok=True)
        # Create minimal valid 1-page blank PDF (zero external file dependency)
        blank_pdf = (
            b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >> endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
            b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF\n"
        )
        with open(os.path.join(in_dir, "scan.pdf"), "wb") as f:
            f.write(blank_pdf)
        open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8").write(json.dumps({"case_id": "scan_case"}))
        with _quiet():
            index_case(in_dir, w_dir, out_dir)
            assemble_notes(w_dir)
            rep = audit_notes(w_dir)
            res = render_deliverables(in_dir, w_dir, out_dir)
            ok, val_rep = validate_output(out_dir, in_dir, w_dir, strict=True)
        assert rep["has_unreadable_source"] is True
        assert res["status"] == "partial", f"Scanned PDF must force status='partial', got {res['status']}"
        assert len(res["uncertainty"]) >= 1
        assert len(res["manual_review_checklist"]) >= 1
        assert ok is True, f"Honest partial delivery should pass validator: {val_rep}"


def test_15_validate_output_rejects_dishonest_success():
    with tempfile.TemporaryDirectory() as tmp:
        in_dir, w_dir, out_dir, _ = _make_sample_doc_and_index(tmp)
        with _quiet():
            assemble_notes(w_dir)
            audit_notes(w_dir)
            res = render_deliverables(in_dir, w_dir, out_dir)
        assert res["status"] == "partial"
        # Tamper result.json to fake status="success"
        res_path = os.path.join(out_dir, "result.json")
        tampered = json.loads(open(res_path, "r", encoding="utf-8").read())
        tampered["status"] = "success"
        open(res_path, "w", encoding="utf-8").write(json.dumps(tampered, ensure_ascii=False, indent=2))
        ok, val_rep = validate_output(out_dir, in_dir, w_dir, strict=True)
        assert ok is False, "Validator must reject dishonest status='success'"
        assert any(e["code"] == "STATUS_DISHONEST" for e in val_rep["errors"])


def test_16_complex_contract_and_obsidian_markdown_e2e():
    complex_contract = {
        "type": "object",
        "required": ["case_id", "status", "structured_bundle", "evidence", "uncertainty"],
        "properties": {
            "case_id": {"type": "string"},
            "status": {"type": "string"},
            "structured_bundle": {
                "type": "object",
                "required": ["paper_card", "section_notes", "knowledge_points"],
                "properties": {
                    "paper_card": {"type": "object"},
                    "section_notes": {"type": "array"},
                    "knowledge_points": {"type": "array"},
                },
            },
            "evidence": {"type": "array"},
            "uncertainty": {"type": "array"},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        in_dir, w_dir, out_dir, ev = _make_sample_doc_and_index(
            tmp, case_json={"case_id": "e2e_complex_01", "output_contract": complex_contract}
        )
        # Summarize all non-locked body sections cleanly
        secs = [s for s in ev["sections"] if s["zone"] not in ("references_locked", "toc", "front_matter", "appendix")]
        sec_patches = []
        for s in secs:
            s_ancs = [a for a in ev["anchors"] if a["section_id"] == s["section_id"] and a["block_type"] != "heading"]
            aid = s_ancs[0]["anchor_id"] if s_ancs else "P1-B003"
            txt = s_ancs[0]["text"] if s_ancs else ""
            # Keep fidelity to source numbers/negation
            if "does not improve" in txt:
                pt_txt = "Algorithm runs in 95.3 s and does not improve worst-case latency when noise exceeds 10%."
            elif "84.3%" in txt:
                pt_txt = "Achieves 84.3% fidelity/accuracy."
            elif "Markovian" in txt:
                pt_txt = "Assumes Markovian noise and fails to scale beyond 50 qubits."
            else:
                pt_txt = txt[:120]
            sec_patches.append({
                "section_id": s["section_id"],
                "summary": pt_txt,
                "key_points": [{"text": pt_txt, "anchor_ids": [aid]}],
            })

        full_patch = {
            "paper_card": {
                "one_line_takeaway": {"value": "Noise-resilient quantum protocol achieving 84.3% fidelity.", "anchor_ids": ["P1-B003"]},
                "research_problem": {"value": "Mitigating quantum communication decoherence.", "anchor_ids": ["P1-B005"]},
                "research_gap": {"value": "Existing methods fail under non-trivial noise.", "anchor_ids": ["P1-B005"]},
                "methodology_summary": {"value": "Algorithm running in 95.3 s that does not improve worst-case latency above 10% noise.", "anchor_ids": ["P1-B007"]},
                "limitations_and_assumptions": {"value": "Assumes Markovian noise and fails to scale beyond 50 qubits.", "anchor_ids": ["P1-B011"]},
                "reusability_and_takeaways": {"value": "Reusable under bounded noise below 10%.", "anchor_ids": ["P1-B007", "P1-B013"]},
                "key_results": [
                    {"result_id": "R1", "metric": "Fidelity", "value": "84.3%", "summary": "Table 1 shows accuracy reaches 84.3% across 5 trials.", "anchor_ids": ["P1-B009"]}
                ],
                "tags": ["quantum", "notes"],
            },
            "section_summaries": sec_patches,
            "knowledge_points": [
                {"kp_id": "KP-001", "category": "empirical_finding", "title": "Fidelity Benchmark", "claim": "Table 1 shows accuracy reaches 84.3% across 5 trials.", "anchor_ids": ["P1-B009"], "verbatim_quote": "Table 1 shows accuracy reaches 84.3% across 5 trials."}
            ],
        }
        p_file = os.path.join(w_dir, "full_patch.json")
        open(p_file, "w", encoding="utf-8").write(json.dumps(full_patch, ensure_ascii=False))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_file])
            rep = audit_notes(w_dir)
            res = render_deliverables(in_dir, w_dir, out_dir)
            ok, val_rep = validate_output(out_dir, in_dir, w_dir, strict=True)
        assert rep["audit_passed"] is True, f"Audit failed unexpectedly: {rep['issues']}"
        assert res["status"] == "success"
        assert "structured_bundle" in res and isinstance(res["structured_bundle"]["section_notes"], list)
        assert ok is True, f"Validation failed: {val_rep}"
        md_txt = open(os.path.join(out_dir, "literature_notes.md"), "r", encoding="utf-8").read()
        assert md_txt.startswith("---\n")
        assert "## ⚡ 核心速览 (Executive Snapshot)" in md_txt
        assert "## 6. 可回查证据锚点原文附录" in md_txt
        assert "[P1-B009]" in md_txt


def test_17_author_cleaning_strips_arxiv_and_superscripts():
    raw_clues = [
        {"anchor_id": "P1-B002", "text": "Chenjian Li1,2,3, Ji Guan1,2,∗and Mingsheng Ying4"},
        {"anchor_id": "P1-B003", "text": "Key Laboratory of System Software, University of Science"},
        {"anchor_id": "P1-B004", "text": "arXiv:2611.25775v1 [quant-ph] 19 Nov 2026"},
        {"anchor_id": "P1-B005", "text": "alice@example.edu"},
    ]
    names, cleaned_clues = _clean_author_clues(raw_clues)
    assert names == ["Chenjian Li", "Ji Guan", "Mingsheng Ying"], f"Unexpected cleaned authors: {names}"
    assert len(cleaned_clues) == 1
    assert "arXiv" not in " ".join(names)


def test_18_cross_lingual_polarity_and_percent_equivalence():
    en_src = "Our algorithm achieves 0.85 fidelity and does not improve worst-case latency when noise exceeds 10%."
    # 1. Valid EN->ZH cross-lingual note with 85% (equiv to 0.85) and matching Chinese negation
    zh_good = "该算法达到 85% 保真度，但在噪声超过 10% 时未能改善最坏情况延迟。"
    res_good = compare_claim_against_anchors(zh_good, en_src)
    assert res_good["passed"] is True, f"Valid cross-lingual note failed: {res_good}"

    # 2. Cross-lingual dropped negation (EN 'does not improve' -> ZH '显著改善了')
    zh_drop_neg = "该算法达到 85% 保真度，且显著改善了最坏情况延迟。"
    res_drop = compare_claim_against_anchors(zh_drop_neg, en_src)
    assert res_drop["passed"] is False and len(res_drop["negation_alerts"]) > 0

    # 3. Domain non-negation ('非线性' / '无监督') on positive English anchor must NOT false-positive
    en_pos = "The model achieves 0.953 accuracy on unsupervised benchmarks."
    zh_pos = "该无监督非线性模型达到 95.3% 的准确率。"
    res_pos = compare_claim_against_anchors(zh_pos, en_pos)
    assert res_pos["passed"] is True, f"False negation alert on domain terms: {res_pos}"


def test_19_unnumbered_prose_fallback_body_section():
    # Part A: Unit test _build_document_index when prose follows TOC without body headings
    doc_meta = {"doc_id": "D1", "source_file": "whitepaper.pdf", "page_count": 2, "diagnosis_summary": {"dominant_route": "native_clean"}}
    prose_p1 = " ".join(["quantum"] * 75) + " achieves 88.2% reliability across distributed edge nodes."
    prose_p2 = " ".join(["protocol"] * 75) + " reduces communication overhead under bounded channel noise."
    units = [
        {"unit_type": "title", "raw_block_type": "title", "source_text": "Whitepaper on Distributed Quantum Sensing", "page_span": [1, 1], "word_count": 5, "char_count": 41, "reading_policy": "analyze"},
        {"unit_type": "heading", "raw_block_type": "heading", "source_text": "Contents", "page_span": [1, 1], "word_count": 1, "char_count": 8, "reading_policy": "analyze"},
        {"unit_type": "paragraph", "raw_block_type": "paragraph", "source_text": prose_p1, "page_span": [1, 1], "word_count": 83, "char_count": len(prose_p1), "reading_policy": "analyze"},
        {"unit_type": "paragraph", "raw_block_type": "paragraph", "source_text": prose_p2, "page_span": [2, 2], "word_count": 83, "char_count": len(prose_p2), "reading_policy": "analyze"},
    ]
    anchors, sec_flat, _tree, _sig = _build_document_index(doc_meta, units, multi_doc=False)
    body_secs = [s for s in sec_flat if s["zone"] == "body"]
    assert len(body_secs) >= 1, f"Expected synthetic body section, got: {sec_flat}"
    assert any(a["zone"] == "body" and "88.2%" in a["text"] for a in anchors)

    # Part B: End-to-end unnumbered prose markdown -> assemble -> audit -> render -> status=success
    prose_md = (
        "Distributed quantum sensing requires careful synchronization across heterogeneous optical links and low-noise repeaters. "
        "In this technical report we examine phase stabilization protocols deployed across metropolitan fiber testbeds over thirty consecutive days. "
        "Experimental telemetry demonstrates 91.4% state transfer fidelity across 42 km urban fibers without cryogenic repeater stations. "
        "Throughput reaches 1200 entangled pairs per second under standard thermal fluctuation regimes.\n\n"
        "Furthermore, we analyze calibration drift caused by diurnal temperature swings along aerial fiber spans and bridge conduits. "
        "Adaptive feedback loops compensate polarization rotation within 15 ms, preventing burst error propagation across downstream receivers. "
        "These empirical findings suggest metropolitan deployment is feasible using existing telecommunication infrastructure and off-the-shelf detectors."
    )
    with tempfile.TemporaryDirectory() as tmp:
        in_dir, w_dir, out_dir, ev = _make_sample_doc_and_index(tmp, extra_md=prose_md)
        body_secs_e2e = [s for s in ev["sections"] if s["zone"] == "body"]
        assert len(body_secs_e2e) >= 1, f"Expected body section for unnumbered prose: {ev['sections']}"
        sid = body_secs_e2e[0]["section_id"]
        first_aid = [a["anchor_id"] for a in ev["anchors"] if a["section_id"] == sid][0]
        patch = {
            "paper_card": {
                "one_line_takeaway": {"value": "Metropolitan quantum link achieves 91.4% fidelity over 42 km without cryogenic repeaters.", "anchor_ids": [first_aid]},
                "key_results": [
                    {"result_id": "RES-001", "metric": "Fidelity", "value": "91.4%", "summary": "Achieves 91.4% fidelity across 42 km fiber without cryogenic repeater stations.", "anchor_ids": [first_aid]}
                ],
            },
            "section_summaries": [
                {
                    "section_id": sid,
                    "summary": "Reports 91.4% fidelity over 42 km urban fiber without cryogenic repeaters and 15 ms polarization feedback.",
                    "key_points": [
                        {"text": "Telemetry demonstrates 91.4% fidelity across 42 km urban fibers without cryogenic repeater stations.", "anchor_ids": [first_aid], "verbatim_quote": "demonstrates 91.4% state transfer fidelity across 42 km"}
                    ],
                }
            ],
        }
        p_file = os.path.join(w_dir, "patch.json")
        open(p_file, "w", encoding="utf-8").write(json.dumps(patch, ensure_ascii=False))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_file])
            rep = audit_notes(w_dir)
            res = render_deliverables(in_dir, w_dir, out_dir)
            ok, val_rep = validate_output(out_dir, in_dir, w_dir, strict=True)
        assert rep["body_section_coverage"] > 0.0 and rep["body_section_coverage"] >= 0.85
        assert rep["audit_passed"] is True, f"Audit issues: {rep['issues']}"
        assert res["status"] == "success" and ok is True, f"Validate failed: {val_rep}"


def test_20_same_section_number_fallback_and_word_numerals_and_ligatures():
    # Test 1 & 2: Same-section sibling number -> low severity; truly absent number -> high severity
    with tempfile.TemporaryDirectory() as tmp:
        md = (
            "# Quantum Report\n\n"
            "## 1. Experiments\n\n"
            "We evaluate the baseline encoder on standard benchmarks.\n\n"
            "Our full system achieves 84.3% fidelity across trials.\n"
        )
        in_dir, w_dir, out_dir, ev = _make_sample_doc_and_index(tmp, extra_md=md)
        exp_sec = [s for s in ev["sections"] if "Experiments" in s["title"]][0]
        exp_anchors = [a for a in ev["anchors"] if a["section_id"] == exp_sec["section_id"] and a["block_type"] != "heading"]
        assert len(exp_anchors) >= 2
        a_first = exp_anchors[0]["anchor_id"]  # does NOT contain 84.3%, sibling exp_anchors[1] DOES

        patch_sib = {
            "section_summaries": [
                {
                    "section_id": exp_sec["section_id"],
                    "summary": "Evaluates encoder achieving 84.3% fidelity.",
                    "key_points": [
                        {"text": "Baseline encoder achieves 84.3% fidelity.", "anchor_ids": [a_first]}
                    ],
                }
            ]
        }
        p_file = os.path.join(w_dir, "sib_patch.json")
        open(p_file, "w", encoding="utf-8").write(json.dumps(patch_sib, ensure_ascii=False))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_file])
            rep_sib = audit_notes(w_dir)
        low_sib_issues = [i for i in rep_sib["issues"] if i["issue_type"] == "same_section_number_context"]
        assert low_sib_issues and all(i["severity"] == "low" for i in low_sib_issues), f"Expected low same-section issue: {rep_sib['issues']}"
        assert rep_sib["blocking_issues_count"] == 0, f"Sibling number should not block: {rep_sib['issues']}"

        # Truly hallucinated number (99.7%) not anywhere in section -> MUST stay high
        patch_bad = {
            "section_summaries": [
                {
                    "section_id": exp_sec["section_id"],
                    "summary": "Hallucinated 99.7% result.",
                    "key_points": [
                        {"text": "Achieves 99.7% fidelity.", "anchor_ids": [a_first]}
                    ],
                }
            ]
        }
        open(p_file, "w", encoding="utf-8").write(json.dumps(patch_bad, ensure_ascii=False))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_file])
            rep_bad = audit_notes(w_dir)
        assert any(i["issue_type"] == "unsupported_number" and i["severity"] == "high" for i in rep_bad["issues"])

    # Test 3: Word numerals ("three baselines" vs "3 baselines", and "twenty epochs" vs "20 epochs")
    res_wn1 = compare_claim_against_anchors("Compared against three baselines.", "Evaluated over 3 baselines.")
    assert res_wn1["passed"] is True, f"Word numeral failed: {res_wn1}"
    res_wn2 = compare_claim_against_anchors("Trained for 20 epochs.", "We train the network for twenty epochs.")
    assert res_wn2["passed"] is True, f"Digit vs word numeral >12 failed: {res_wn2}"

    # Test 4: Ligature normalization (fi ligature \ufb01 vs ASCII fi)
    assert _quote_matches_source("e\ufb03cient quantum \ufb01ltering", ["efficient quantum filtering protocol"]) is True
    assert _quote_matches_source("efficient filtering", ["e\ufb03cient \ufb01ltering protocol"]) is True

    # Test 5: Hyphenated line break normalization ("differ-\nential" vs "differential")
    assert _quote_matches_source("differ-\nential privacy", ["quantum differential privacy bounds"]) is True
    assert _quote_matches_source("differential privacy", ["quantum differ-\nential privacy bounds"]) is True


def test_21_chinese_neutral_negation_compounds_no_false_positive():
    # 1. Neutral Chinese compounds ("不同", "不超过", "非均匀", "无关", "不等式", "无纠缠") must NOT trigger false blocking
    en_src = "Different non-uniform Pauli mechanisms satisfy an inequality bounded by 2 and are independent of entanglement-free data."
    zh_note = "不同的非均匀 Pauli 机制满足不超过 2 的不等式，且与无纠缠数据无关。"
    res = compare_claim_against_anchors(zh_note, en_src)
    assert res["passed"] is True, f"False positive on neutral compounds: {res}"

    # 2. Auditor check: affirmative source + Chinese note with neutral compounds -> 0 blocking;
    #    affirmative source + note adding a qualifier negation -> severity='low' (non-blocking);
    #    strong negative source + claim dropping negation -> severity='high' (blocking)
    with tempfile.TemporaryDirectory() as tmp:
        md = (
            "# Neutral Compound Audit Test\n\n"
            "## 1. Method\n\n"
            "The bound scales with factor 2 under non-uniform noise and independent states.\n\n"
            "Our protocol does not converge when noise exceeds 35%.\n"
        )
        in_dir, w_dir, out_dir, ev = _make_sample_doc_and_index(tmp, extra_md=md)
        sec = [s for s in ev["sections"] if "Method" in s["title"]][0]
        ancs = [a for a in ev["anchors"] if a["section_id"] == sec["section_id"] and a["block_type"] != "heading"]
        a_pos, a_neg = ancs[0]["anchor_id"], ancs[1]["anchor_id"]

        # 2a. Neutral compounds + added modifier on affirmative anchor -> audit_passed=True
        p_ok = {
            "section_summaries": [{
                "section_id": sec["section_id"],
                "summary": "在非均匀噪声和无关态下，界不超过 2。",
                "key_points": [
                    {"text": "在非均匀噪声与无关态下，该不等式因子不超过 2（目前尚未做硬件平滑）。", "anchor_ids": [a_pos]},
                    {"text": "当噪声超过 35% 时，协议无法收敛。", "anchor_ids": [a_neg]},
                ],
            }]
        }
        p_file = os.path.join(w_dir, "p_ok.json")
        open(p_file, "w", encoding="utf-8").write(json.dumps(p_ok, ensure_ascii=False))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_file])
            rep_ok = audit_notes(w_dir)
        assert rep_ok["audit_passed"] is True and rep_ok["blocking_issues_count"] == 0, f"Unexpected blocking: {rep_ok['issues']}"

        # 2b. Dropped strong negation on negative anchor -> MUST block with severity='high'
        p_bad = {
            "section_summaries": [{
                "section_id": sec["section_id"],
                "summary": "Protocol converges under 35% noise.",
                "key_points": [
                    {"text": "当噪声超过 35% 时协议顺利收敛。", "anchor_ids": [a_neg]},
                ],
            }]
        }
        open(p_file, "w", encoding="utf-8").write(json.dumps(p_bad, ensure_ascii=False))
        with _quiet():
            assemble_notes(w_dir, patch_files=[p_file])
            rep_bad = audit_notes(w_dir)
        assert rep_bad["audit_passed"] is False
        assert any(i["issue_type"] == "negation_polarity_mismatch" and i["severity"] == "high" for i in rep_bad["issues"])


def test_22_appendix_included_while_references_locked():
    import zlib
    from pdf_engine.pdf_diagnose import extract_pdf_pages_stdlib_zlib
    from pdf_engine.layout_parser import extract_with_stdlib_zlib

    # Part A: References locked out of reading packets, while Appendix A/B ARE included
    md = (
        "# Paper With Appendix Tables\n\n"
        "## 1. Introduction\n\n"
        "We study differential privacy under entanglement.\n\n"
        "## References\n\n"
        "[1] Doe et al., Quantum Privacy, 2024.\n\n"
        "[2] Smith et al., Channel Bounds, 2025.\n\n"
        "## Appendix A. Deferred Proofs\n\n"
        "Theorem 2 proof details follow from Schmidt decomposition.\n\n"
        "## Appendix B. Summary Parameters\n\n"
        "Table 4 lists privacy bounds for depolarizing channels with epsilon 0.42.\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        in_dir, w_dir, out_dir, ev = _make_sample_doc_and_index(tmp, extra_md=md)
        ref_secs = [s for s in ev["sections"] if s["zone"] == "references_locked"]
        app_secs = [s for s in ev["sections"] if s["zone"] == "appendix"]
        assert len(ref_secs) >= 1 and len(app_secs) >= 2, f"Unexpected sections: {ev['sections']}"

        manifest = json.load(open(os.path.join(w_dir, "reading_packets", "manifest.json"), "r", encoding="utf-8"))
        pkt_texts = "\n".join(
            open(os.path.join(w_dir, "reading_packets", p["file"]), "r", encoding="utf-8").read()
            for p in manifest["packets"]
        )
        assert "[1] Doe et al." not in pkt_texts, "References should stay locked out of reading packets"
        assert "Appendix B. Summary Parameters" in pkt_texts, "Appendix B must be included in reading packets"
        assert "Table 4 lists privacy bounds" in pkt_texts, "Appendix B table/prose must be included in reading packets"

        # Part B: Verify pure-stdlib zlib FlateDecode PDF content-stream fallback
        stream_body = b"BT /F1 12 Tf 72 720 Td (Quantum differential privacy achieves 92.5 percent accuracy across trials.) Tj ET"
        comp = zlib.compress(stream_body)
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >> endobj\n"
            b"4 0 obj << /Filter /FlateDecode /Length " + str(len(comp)).encode("ascii") + b" >>\nstream\n"
            + comp + b"\nendstream\nendobj\n%%EOF\n"
        )
        z_pdf = os.path.join(tmp, "zlib_test.pdf")
        open(z_pdf, "wb").write(pdf_bytes)
        diag_fb = extract_pdf_pages_stdlib_zlib(z_pdf)
        assert "92.5 percent accuracy" in diag_fb["pages"][0]["text"]
        lp_blocks = extract_with_stdlib_zlib(z_pdf)
        assert lp_blocks and "92.5 percent accuracy" in lp_blocks[0]["blocks"][0]["text"]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    tests = [
        test_01_split_mixed_roman_heading,
        test_02_toc_no_false_reference_lock,
        test_03_prose_table_ref_vs_real_caption,
        test_04_table_caption_header_binding_dedup,
        test_05_bilingual_section_tree_and_roles,
        test_06_formula_pua_low_severity_notice,
        test_07_multi_doc_ingestion_and_anchors,
        test_08_assembler_incremental_patch_and_provenance,
        test_09_not_reported_in_source_normalization,
        test_10_auditor_ghost_anchor_and_fabricated_quote,
        test_11_auditor_hallucinated_number_and_unit_mismatch,
        test_12_auditor_negation_polarity_inversion,
        test_13_auditor_ghost_crossref_and_missing_sections,
        test_14_scanned_degradation_uncertainty_and_partial,
        test_15_validate_output_rejects_dishonest_success,
        test_16_complex_contract_and_obsidian_markdown_e2e,
        test_17_author_cleaning_strips_arxiv_and_superscripts,
        test_18_cross_lingual_polarity_and_percent_equivalence,
        test_19_unnumbered_prose_fallback_body_section,
        test_20_same_section_number_fallback_and_word_numerals_and_ligatures,
        test_21_chinese_neutral_negation_compounds_no_false_positive,
        test_22_appendix_included_while_references_locked,
    ]
    t_start = time.time()
    passed = 0
    failed = 0
    for fn in tests:
        t0 = time.time()
        try:
            fn()
            dt = round(time.time() - t0, 3)
            print(f"[PASS] {fn.__name__} ({dt}s)")
            passed += 1
        except Exception as exc:
            dt = round(time.time() - t0, 3)
            print(f"[FAIL] {fn.__name__} ({dt}s): {exc}")
            failed += 1
    total_dt = round(time.time() - t_start, 3)
    print(f"\n[self_test] {passed}/{len(tests)} passed, {failed} failed in {total_dt}s")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
