#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_test.py - Zero-External-Dependency End-to-End Test Suite for task_09_nanopub
Runs 20 deterministic tests covering AST compliance, vocab, 20 examples, 6-trap benchmark,
assembler lifecycle, 8-dim auditor, multi-format rendering, validation gates, and degradation.
Top-level imports are strictly restricted to standard libraries and sibling modules.
"""

import ast
import csv
import json
import os
import re
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
REF_DIR = os.path.join(BASE_DIR, "references")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from case_ingestor import ingest_case, extract_research_questions, extract_target_formats
from structure_indexer import index_case, normalize_section_role, extract_block_signals
from entity_guard import extract_scientific_qualifiers, check_causal_strength_upgrade, compare_claim_against_anchors
from pubinfo_extractor import extract_pubinfo_from_index, verify_pubinfo_against_source
from evidence_aligner import align_evidence_quote
from nanopub_assembler import init_work_state, patch_work_state, finalize_work_state, load_relation_vocab
from nanopub_auditor import audit_work_state
from render_deliverables import render_all_deliverables
from validate_output import validate_output, validate_trig_syntax


def _make_clean_work_state(case_id, anchors, pub_by_doc, nps, qas=None, conflicts=None, rqs=None, contract=None):
    return {
        "schema_version": "nanopub-work-state/1.0",
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract or {},
        "research_questions": rqs or [],
        "documents_pubinfo": list(pub_by_doc.values()),
        "pubinfo_by_doc": pub_by_doc,
        "nanopublications": nps,
        "qa_answers": qas or [],
        "conflicts": conflicts or [],
        "uncertainty": [],
        "manual_review_checklist": [],
        "id_redirects": {},
        "changes": [],
    }


def test_01_ast_stdlib_only_top_level_imports():
    local_mods = {
        "pdf_engine", "case_ingestor", "structure_indexer", "entity_guard",
        "pubinfo_extractor", "evidence_aligner", "nanopub_assembler",
        "nanopub_auditor", "render_deliverables", "validate_output",
    }
    stdlib = getattr(sys, "stdlib_module_names", set())
    violations = []
    checked = 0
    for root, _dirs, files in os.walk(SCRIPT_DIR):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            checked += 1
            fpath = os.path.join(root, fn)
            with open(fpath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fpath)
            for node in tree.body:
                nodes = [node]
                if isinstance(node, ast.Try):
                    nodes.extend(node.body)
                    for h in node.handlers:
                        nodes.extend(h.body)
                for n in nodes:
                    if isinstance(n, ast.Import):
                        for al in n.names:
                            pkg = al.name.split(".")[0]
                            if pkg not in stdlib and pkg not in local_mods:
                                violations.append((fn, al.name))
                    elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                        pkg = n.module.split(".")[0]
                        if pkg not in stdlib and pkg not in local_mods:
                            violations.append((fn, n.module))
    assert checked >= 12, f"Expected >=12 python files, got {checked}"
    assert not violations, f"Non-stdlib top-level imports found: {violations}"


def test_02_cli_utf8_reconfigure_and_no_local_leaks():
    for root, _dirs, files in os.walk(BASE_DIR):
        for fn in files:
            fpath = os.path.join(root, fn)
            if fn.endswith(".py"):
                with open(fpath, "r", encoding="utf-8") as f:
                    src = f.read()
                if "def main(" in src:
                    assert "sys.stdout.reconfigure" in src and "hasattr(sys.stdout" in src, f"Missing utf-8 reconfigure in {fn}"
            if fn.endswith((".py", ".md", ".json")):
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                forbidden_win = "C:\\" + "Users\\"
                forbidden_user = "113" + "95"
                assert forbidden_win not in content and forbidden_user not in content, f"Hardcoded local path leak in {fn}"


def test_03_relation_vocab_integrity():
    data, by_id, alias_map = load_relation_vocab()
    preds = data.get("predicates", [])
    assert 26 <= len(preds) <= 32, f"Unexpected predicate count: {len(preds)}"
    cats = set(data.get("categories", []))
    assert len(cats) == 6
    req_keys = {"id", "uri", "category", "description_en", "description_zh", "symmetric", "inverse", "polarity", "causal_strength", "aliases"}
    for p in preds:
        assert req_keys <= set(p.keys()), f"Missing keys on predicate {p.get('id')}"
        assert p["category"] in cats
        assert p["aliases"], f"Empty aliases on {p['id']}"
        if p["inverse"]:
            assert p["inverse"].lower() in by_id, f"Inverse '{p['inverse']}' missing for {p['id']}"
    assert "outperforms" in alias_map.values()


def test_04_case_ingestor_questions_and_formats():
    with tempfile.TemporaryDirectory() as tmp:
        in_d = os.path.join(tmp, "in")
        wk_d = os.path.join(tmp, "wk")
        os.makedirs(in_d)
        with open(os.path.join(in_d, "case.json"), "w", encoding="utf-8") as f:
            json.dump({
                "case_id": "test_md_case_01",
                "questions": ["How much does Method X improve F1?"],
                "output_contract": {"case_id": "string", "status": "string", "nanopublications": "array"},
            }, f)
        with open(os.path.join(in_d, "task.md"), "w", encoding="utf-8") as f:
            f.write("# Task\nExport JSON-LD and TriG.\nQ2: What is the sample size?\n")
        with open(os.path.join(in_d, "paper.md"), "w", encoding="utf-8") as f:
            f.write("# Test Paper\n\n## Abstract\nMethod X improves F1 by 5.2%.\n\n## 2. Results\nOn Dataset A (n = 500), Method X increases F1 by 5.2% (p < 0.01).\n")
        manifest = ingest_case(in_d, wk_d)
        assert manifest["case_id"] == "test_md_case_01"
        assert len(manifest["research_questions"]) == 2
        assert "json-ld" in manifest["target_formats"] and "trig" in manifest["target_formats"]
        assert os.path.exists(os.path.join(wk_d, "case_manifest.json"))


def test_05_structure_indexer_10_roles_and_signals():
    assert normalize_section_role("front_matter") == "title_meta"
    assert normalize_section_role("abstract") == "abstract"
    assert normalize_section_role("related_work") == "introduction"
    assert normalize_section_role("experiments") == "methods"
    assert normalize_section_role("results") == "results"
    assert normalize_section_role("limitations") == "discussion"
    assert normalize_section_role("conclusion") == "conclusion"
    assert normalize_section_role("appendix") == "appendix"
    assert normalize_section_role("references") == "references"
    assert normalize_section_role("body") == "body_unclassified"
    cites, quant, assets = extract_block_signals("Prior work [1, 2] showed 12.5% gain in Table 1 and Fig. 2 (p < 0.01).")
    assert cites and quant and set(assets) == {"TAB-1", "FIG-2"}


def test_06_entity_guard_qualifiers_and_causal_upgrade():
    q = extract_scientific_qualifiers("At 37°C and pH 7.4 in vivo, 5 mg/kg dose for 14 days (n = 24, p < 0.05) improved outcome.")
    assert q["has_qualifiers"] and {"temperature", "ph", "concentration_dose", "sample_size", "significance", "time_window", "experimental_context"} <= set(q["by_kind"].keys())
    c1 = check_causal_strength_upgrade("Drug X causes recovery.", "Drug X is associated with recovery, which suggests benefit.")
    assert c1["upgraded"]
    c2 = check_causal_strength_upgrade("药物X导致恢复。", "药物X与恢复高度相关，提示可能有益。")
    assert c2["upgraded"]
    cmp_res = compare_claim_against_anchors("Three models achieved 95% accuracy.", ["3 models achieved 95% accuracy."])
    assert cmp_res["passed"]


def test_07_pubinfo_extractor_and_anti_hallucination():
    ev_idx = {
        "case_id": "pub_test",
        "documents": [{"doc_id": "D1", "source_file": "paper.pdf", "pdf_metadata": {}}],
        "anchors": [
            {"anchor_id": "P1-B001", "doc_id": "D1", "page": 1, "block_type": "title", "raw_block_type": "title", "section_role": "title_meta", "zone": "front_matter", "text": "Quantum Privacy Bounds"},
            {"anchor_id": "P1-B002", "doc_id": "D1", "page": 1, "block_type": "paragraph", "raw_block_type": "metadata", "section_role": "title_meta", "zone": "front_matter", "text": "Alice Smith, Bob Jones"},
            {"anchor_id": "P1-B003", "doc_id": "D1", "page": 1, "block_type": "paragraph", "raw_block_type": "metadata", "section_role": "title_meta", "zone": "front_matter", "text": "arXiv:2611.12345v2 [quant-ph] 10 Nov 2026"},
        ],
    }
    bundle = extract_pubinfo_from_index(ev_idx)
    src = bundle["sources"][0]
    assert src["title"] == "Quantum Privacy Bounds"
    assert src["authors"] == ["Alice Smith", "Bob Jones"]
    assert src["arxiv_id"] == "2611.12345v2"
    assert src["doi"] is None and "doi" in src["missing_fields"]
    fake = dict(src)
    fake["doi"] = "10.1234/invented.doi"
    ver = verify_pubinfo_against_source(fake, src, "Quantum Privacy Bounds Alice Smith Bob Jones arXiv:2611.12345v2")
    assert not ver["passed"] and any(h["field"] == "doi" for h in ver["hallucinations"])


def test_08_evidence_aligner_modes_and_flags():
    anchors = [
        {"anchor_id": "P1-B001", "doc_id": "D1", "page": 1, "section_role": "abstract", "block_type": "paragraph", "has_citation_markers": False, "text": "Abstract: First sentence here. Second sentence here. Third sentence here. Fourth sentence here. Fifth sentence here for dump check. " + "x " * 320},
        {"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_citation_markers": False, "text": "In our experiments, significant differential privacy gains"},
        {"anchor_id": "P2-B002", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_citation_markers": False, "text": "were observed across all 12 benchmark tasks (p < 0.01)."},
    ]
    r_exact = align_evidence_quote("were observed across all 12 benchmark tasks (p < 0.01).", anchors, claimed_block_id="P2-B002", doc_id="D1")
    assert r_exact["alignment_status"] == "EXACT_MATCH"
    r_norm = align_evidence_quote("In our experiments, signi\ufb01cant differen-\ntial privacy gains", anchors, claimed_block_id="P2-B001", doc_id="D1")
    assert r_norm["alignment_status"] == "NORMALIZED_MATCH"
    r_cross = align_evidence_quote("significant differential privacy gains were observed across all 12 benchmark tasks (p < 0.01).", anchors, claimed_block_id="P2-B001", doc_id="D1")
    assert r_cross["alignment_status"] == "CROSS_BLOCK_MATCH" and r_cross["matched_block_ids"] == ["P2-B001", "P2-B002"]
    r_drift = align_evidence_quote("were observed across all 12 benchmark tasks (p < 0.01).", anchors, claimed_block_id="P1-B001", doc_id="D1")
    assert r_drift["suggested_block_id"] == "P2-B002"
    r_miss = align_evidence_quote("Completely fabricated sentence about Mars rovers.", anchors, claimed_block_id="P2-B002", doc_id="D1")
    assert r_miss["alignment_status"] == "QUOTE_NOT_FOUND"
    r_dump = align_evidence_quote(anchors[0]["text"], anchors, claimed_block_id="P1-B001", doc_id="D1")
    assert "PARAGRAPH_DUMP" in r_dump["sufficiency_flags"] and "ABSTRACT_ONLY_EVIDENCE" in r_dump["sufficiency_flags"]
    r_short = align_evidence_quote("gains", anchors, claimed_block_id="P2-B001", doc_id="D1")
    assert "TOO_SHORT_FRAGMENT" in r_short["sufficiency_flags"]


def test_09_examples_20_nanopubs_all_pass_aligner_and_auditor():
    with open(os.path.join(REF_DIR, "examples_20_nanopubs.json"), "r", encoding="utf-8") as f:
        ex_data = json.load(f)
    examples = ex_data["examples"]
    assert len(examples) == 20, f"Expected 20 examples, got {len(examples)}"
    anchors = ex_data["mock_anchors"]
    pub_by_doc = ex_data["mock_documents_pubinfo"]
    ev_index = {"case_id": "examples_20", "anchors": anchors, "documents": list(pub_by_doc.values())}

    with tempfile.TemporaryDirectory() as tmp:
        ws_path = os.path.join(tmp, "ws.json")
        idx_path = os.path.join(tmp, "ev_idx.json")
        man_path = os.path.join(tmp, "man.json")
        pub_path = os.path.join(tmp, "pub.json")
        patch_path = os.path.join(tmp, "patch.json")
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(ev_index, f)
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump({"case_id": "examples_20", "output_contract": {}, "research_questions": []}, f)
        with open(pub_path, "w", encoding="utf-8") as f:
            json.dump({"sources": list(pub_by_doc.values())}, f)
        with open(patch_path, "w", encoding="utf-8") as f:
            json.dump({"nanopublications": examples}, f)

        init_work_state(man_path, idx_path, pub_path, ws_path)
        patch_work_state(ws_path, patch_path, idx_path)
        state = finalize_work_state(ws_path)
        aud = audit_work_state(state, ev_index)

        assert aud["severity_counts"]["HIGH"] == 0, f"Unexpected HIGH issues in 20 examples: {aud['issues']}"
        assert aud["total_nanopublications"] == 20
        assert len(state["conflicts"]) >= 1, "Expected conflict pair between NP-EX-17 and NP-EX-18"
        tiers = {n["provenance"]["evidence_tier"] for n in state["nanopublications"]}
        assert "T6_secondary_citation" in tiers


def test_10_mini_benchmark_6_traps():
    with open(os.path.join(REF_DIR, "mini_benchmark.json"), "r", encoding="utf-8") as f:
        bm = json.load(f)
    cases = bm["cases"]
    assert len(cases) == 6

    for c in cases:
        with tempfile.TemporaryDirectory() as tmp:
            ev_index = {"case_id": c["bench_id"], "anchors": c["anchors"], "documents": [c["pubinfo"]]}
            ws_path = os.path.join(tmp, "ws.json")
            idx_path = os.path.join(tmp, "idx.json")
            man_path = os.path.join(tmp, "man.json")
            pub_path = os.path.join(tmp, "pub.json")
            patch_path = os.path.join(tmp, "patch.json")
            with open(idx_path, "w", encoding="utf-8") as f:
                json.dump(ev_index, f)
            with open(man_path, "w", encoding="utf-8") as f:
                json.dump({"case_id": c["bench_id"], "output_contract": {}, "research_questions": []}, f)
            with open(pub_path, "w", encoding="utf-8") as f:
                json.dump({"sources": [c["pubinfo"]]}, f)
            with open(patch_path, "w", encoding="utf-8") as f:
                json.dump({"nanopublications": c["candidate_nanopubs"]}, f)

            init_work_state(man_path, idx_path, pub_path, ws_path)
            patch_work_state(ws_path, patch_path, idx_path)
            state = finalize_work_state(ws_path)
            aud = audit_work_state(state, ev_index)
            codes = {i["code"] for i in aud["issues"]}

            for exp_c in c.get("expected_audit_codes", []):
                assert exp_c in codes, f"{c['bench_id']} expected audit code {exp_c}, got {codes}"
            if "expected_canonical_count" in c:
                assert len(state["nanopublications"]) == c["expected_canonical_count"]
            if "expected_promoted_tier" in c:
                assert any(n["provenance"]["evidence_tier"] == c["expected_promoted_tier"] for n in state["nanopublications"])
            if "expected_conflict_types" in c:
                got_ct = [cf["conflict_type"] for cf in state["conflicts"]]
                for ect in c["expected_conflict_types"]:
                    assert ect in got_ct, f"{c['bench_id']} expected conflict {ect}, got {got_ct}"


def test_11_assembler_init_early_partial_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        out_d = os.path.join(tmp, "out")
        ws_path = os.path.join(tmp, "ws.json")
        idx_path = os.path.join(tmp, "idx.json")
        man_path = os.path.join(tmp, "man.json")
        pub_path = os.path.join(tmp, "pub.json")
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump({"case_id": "early_case", "anchors": [], "documents": []}, f)
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump({
                "case_id": "early_case",
                "output_contract": {
                    "case_id": "string",
                    "status": "string",
                    "nested_bundle": {"type": "object", "properties": {"claims": {"type": "array"}, "qa": {"type": "array"}}, "required": ["claims", "qa"]},
                },
                "research_questions": [{"question_id": "Q1", "question": "What happened?"}],
            }, f)
        with open(pub_path, "w", encoding="utf-8") as f:
            json.dump({"sources": []}, f)
        init_work_state(man_path, idx_path, pub_path, ws_path, output_dir=out_d)
        res_p = os.path.join(out_d, "result.json")
        assert os.path.exists(res_p)
        with open(res_p, "r", encoding="utf-8") as f:
            r = json.load(f)
        assert r["case_id"] == "early_case" and r["status"] == "partial"
        assert isinstance(r.get("nested_bundle"), dict) and "claims" in r["nested_bundle"]


def test_12_assembler_trusty_id_determinism():
    anchors = [{"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Model B improves accuracy by 4.5% over baseline."}]
    ids_runs = []
    for run_idx in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            idx_p = os.path.join(tmp, "idx.json")
            man_p = os.path.join(tmp, "man.json")
            pub_p = os.path.join(tmp, "pub.json")
            ws_p = os.path.join(tmp, "ws.json")
            pt_p = os.path.join(tmp, "patch.json")
            with open(idx_p, "w", encoding="utf-8") as f:
                json.dump({"case_id": "det_case", "anchors": anchors, "documents": []}, f)
            with open(man_p, "w", encoding="utf-8") as f:
                json.dump({"case_id": "det_case"}, f)
            with open(pub_p, "w", encoding="utf-8") as f:
                json.dump({"sources": []}, f)
            with open(pt_p, "w", encoding="utf-8") as f:
                json.dump({"nanopublications": [{
                    "np_id": f"RAND-{run_idx}",
                    "doc_id": "D1",
                    "block_id": "P2-B001",
                    "subject": "Model B",
                    "predicate": "increases",
                    "object": "accuracy (4.5%)",
                    "statement": "Model B improves accuracy by 4.5% over baseline.",
                    "evidence_quote": "Model B improves accuracy by 4.5% over baseline.",
                }]}, f)
            init_work_state(man_p, idx_p, pub_p, ws_p)
            st = patch_work_state(ws_p, pt_p, idx_p)
            ids_runs.append(st["nanopublications"][0]["head"]["nanopub_id"])
    assert ids_runs[0].startswith("np:sha256:") and ids_runs[0] == ids_runs[1]


def test_13_assembler_cross_section_dedup_promotion_and_qa_redirect():
    anchors = [
        {"anchor_id": "P1-B002", "doc_id": "D1", "page": 1, "section_role": "abstract", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Method X reduces latency by 20%."},
        {"anchor_id": "P4-B005", "doc_id": "D1", "page": 4, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "On GPU cluster (n = 8), Method X reduces latency by 20% (p < 0.01)."},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        idx_p, man_p, pub_p, ws_p, pt_p = [os.path.join(tmp, x) for x in ("idx.json", "man.json", "pub.json", "ws.json", "pt.json")]
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "dedup_case", "anchors": anchors, "documents": []}, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "dedup_case"}, f)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump({"sources": []}, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({
                "nanopublications": [
                    {"np_id": "TMP-ABS", "doc_id": "D1", "block_id": "P1-B002", "subject": "Method X", "predicate": "decreases", "object": "latency", "statement": "Method X reduces latency by 20%.", "evidence_quote": "Method X reduces latency by 20%."},
                    {"np_id": "TMP-RES", "doc_id": "D1", "block_id": "P4-B005", "subject": "Method X", "predicate": "decreases", "object": "latency", "statement": "Method X reduces latency by 20% on GPU cluster (n = 8, p < 0.01).", "qualifiers": {"sample_size": "n = 8", "statistics": "p < 0.01"}, "evidence_quote": "On GPU cluster (n = 8), Method X reduces latency by 20% (p < 0.01)."},
                ],
                "qa_answers": [{"question_id": "Q1", "question": "How much latency reduction?", "answer_status": "answered", "answer": "20%.", "supporting_nanopub_ids": ["TMP-ABS"]}],
            }, f)
        init_work_state(man_p, idx_p, pub_p, ws_p)
        patch_work_state(ws_p, pt_p, idx_p)
        st = finalize_work_state(ws_p)
        assert len(st["nanopublications"]) == 1
        win = st["nanopublications"][0]
        assert win["provenance"]["evidence_tier"] == "T1_direct_data"
        assert len(win["provenance"]["supporting_anchors"]) == 1
        assert st["qa_answers"][0]["supporting_nanopub_ids"] == [win["head"]["nanopub_id"]]


def test_14_assembler_polarity_and_quantitative_conflicts():
    anchors = [
        {"anchor_id": "P3-B001", "doc_id": "D1", "page": 3, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Agent A achieves 90.0% accuracy on Bench1."},
        {"anchor_id": "P3-B002", "doc_id": "D2", "page": 3, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Agent A achieves 60.0% accuracy on Bench1."},
        {"anchor_id": "P4-B001", "doc_id": "D1", "page": 4, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Noise increases error rate."},
        {"anchor_id": "P4-B002", "doc_id": "D2", "page": 4, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Noise decreases error rate."},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        idx_p, man_p, pub_p, ws_p, pt_p = [os.path.join(tmp, x) for x in ("idx.json", "man.json", "pub.json", "ws.json", "pt.json")]
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "conf_case", "anchors": anchors, "documents": []}, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "conf_case"}, f)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump({"sources": []}, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({"nanopublications": [
                {"doc_id": "D1", "block_id": "P3-B001", "subject": "Agent A", "predicate": "achieves_metric", "object": {"label": "Bench1 accuracy", "value": 90.0, "unit": "%"}, "statement": "Agent A achieves 90.0% accuracy on Bench1.", "evidence_quote": "Agent A achieves 90.0% accuracy on Bench1."},
                {"doc_id": "D2", "block_id": "P3-B002", "subject": "Agent A", "predicate": "achieves_metric", "object": {"label": "Bench1 accuracy", "value": 60.0, "unit": "%"}, "statement": "Agent A achieves 60.0% accuracy on Bench1.", "evidence_quote": "Agent A achieves 60.0% accuracy on Bench1."},
                {"doc_id": "D1", "block_id": "P4-B001", "subject": "Noise", "predicate": "increases", "object": "error rate", "statement": "Noise increases error rate.", "evidence_quote": "Noise increases error rate."},
                {"doc_id": "D2", "block_id": "P4-B002", "subject": "Noise", "predicate": "decreases", "object": "error rate", "statement": "Noise decreases error rate.", "evidence_quote": "Noise decreases error rate."},
            ]}, f)
        init_work_state(man_p, idx_p, pub_p, ws_p)
        patch_work_state(ws_p, pt_p, idx_p)
        st = finalize_work_state(ws_p)
        ctypes = {c["conflict_type"] for c in st["conflicts"]}
        assert "quantitative_conflict" in ctypes and "polarity_conflict" in ctypes
        assert len(st["manual_review_checklist"]) >= 2


def test_15_auditor_d1_to_d8_edge_traps():
    anchors = [
        {"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_id": "S2", "section_role": "results", "block_type": "paragraph", "text": "Our method did not improve survival (rate = 12%)."},
    ]
    pub = {"doc_id": "D1", "title": "T", "authors": ["Alice University"], "year": 2025, "doi": None, "arxiv_id": None, "field_sources": {"title": "pdf_text:P1-B001"}}
    bad_nps = [
        {
            "head": {"nanopub_id": "NP-BAD-1"},
            "assertion": {
                "statement": "This paper studies survival in detail and discusses many things without falsifiability. " * 5,
                "subject": {"label": ""},
                "predicate": {"id": "invented_pred", "vocab_mapped": False},
                "object": {"label": "survival"},
                "polarity": "affirmed",
                "qualifiers": {},
            },
            "provenance": {
                "evidence_quote": "Our method did not improve survival (rate = 12%).",
                "location": {"doc_id": "D1", "page": 2, "block_id": "P2-B001", "matched_block_ids": ["P2-B001"], "section_role": "results"},
                "alignment_status": "EXACT_MATCH",
                "evidence_tier": "T1_direct_data",
                "sufficiency_flags": [],
            },
            "pubinfo": pub,
        },
        {
            "head": {"nanopub_id": "NP-BAD-2"},
            "assertion": {
                "statement": "Our method improved survival to 99%.",
                "subject": {"label": "Method"},
                "predicate": {"id": "increases", "vocab_mapped": True},
                "object": {"label": "survival"},
                "polarity": "affirmed",
                "qualifiers": {},
            },
            "provenance": {
                "evidence_quote": "Our method did not improve survival (rate = 12%).",
                "location": {"doc_id": "D1", "page": 2, "block_id": "P2-B001", "matched_block_ids": ["P2-B001"], "section_role": "results"},
                "alignment_status": "EXACT_MATCH",
                "evidence_tier": "T1_direct_data",
                "sufficiency_flags": [],
            },
            "pubinfo": pub,
        },
    ]
    ws = _make_clean_work_state(
        "trap_case", anchors, {"D1": pub}, bad_nps,
        qas=[{"question_id": "Q1", "question": "Does it work?", "answer_status": "answered", "answer": "Yes", "supporting_nanopub_ids": ["NONEXISTENT"]}],
    )
    aud = audit_work_state(ws, {"case_id": "trap_case", "anchors": anchors, "documents": [pub]})
    codes = {i["code"] for i in aud["issues"]}
    for expected in ("CLAIM_TOO_LONG", "SPO_INCOMPLETE", "PREDICATE_NOT_IN_VOCAB", "NUMERIC_HALLUCINATION", "POLARITY_MISMATCH", "AUTHOR_CONTAMINATION", "QA_UNGROUNDED"):
        assert expected in codes, f"Expected {expected}, got {codes}"


def test_16_render_deliverables_nested_output_contract():
    with tempfile.TemporaryDirectory() as tmp:
        out_d = os.path.join(tmp, "out")
        pub = {"doc_id": "D1", "title": "Nested Contract Study", "authors": ["A. Lee"], "year": 2025, "doi": None, "arxiv_id": None, "field_sources": {"title": "pdf_text:P1-B001"}, "missing_fields": ["doi"]}
        anchors = [{"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Protocol P increases throughput by 15%."}]
        ws = _make_clean_work_state(
            "nested_contract_case", anchors, {"D1": pub}, [],
            contract={
                "case_id": "string",
                "status": "string",
                "reading_package": {
                    "type": "object",
                    "properties": {
                        "assertions_list": {"type": "array"},
                        "qa_bundle": {"type": "array"},
                        "audit_meta": {"type": "object"},
                    },
                    "required": ["assertions_list", "qa_bundle", "audit_meta"],
                },
            },
        )
        # Patch 1 clean np
        idx_p, man_p, pub_p, ws_p, pt_p = [os.path.join(tmp, x) for x in ("idx.json", "man.json", "pub.json", "ws.json", "pt.json")]
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "nested_contract_case", "anchors": anchors, "documents": [pub]}, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "nested_contract_case", "output_contract": ws["output_contract"]}, f)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump({"sources": [pub]}, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({"nanopublications": [{
                "doc_id": "D1", "block_id": "P2-B001", "subject": "Protocol P", "predicate": "increases",
                "object": "throughput (15%)", "statement": "Protocol P increases throughput by 15%.",
                "evidence_quote": "Protocol P increases throughput by 15%.",
            }]}, f)
        init_work_state(man_p, idx_p, pub_p, ws_p)
        patch_work_state(ws_p, pt_p, idx_p)
        st = finalize_work_state(ws_p)
        aud = audit_work_state(st, {"case_id": "nested_contract_case", "anchors": anchors, "documents": [pub]})
        res = render_all_deliverables(st, aud, out_d)
        assert isinstance(res["reading_package"], dict)
        assert len(res["reading_package"]["assertions_list"]) == 1
        ok, vrep = validate_output(out_d, ingest_manifest_path=man_p)
        assert ok, f"Validation failed: {vrep}"


def test_17_end_to_end_markdown_paper_all_formats_pass():
    with tempfile.TemporaryDirectory() as tmp:
        in_d = os.path.join(tmp, "in")
        wk_d = os.path.join(tmp, "wk")
        out_d = os.path.join(tmp, "out")
        os.makedirs(in_d)
        with open(os.path.join(in_d, "case.json"), "w", encoding="utf-8") as f:
            json.dump({
                "case_id": "e2e_md_nanopub",
                "questions": ["How does Catalyst Alpha affect yield at 60°C?"],
                "output_contract": {"case_id": "string", "status": "string", "nanopublications": "array", "qa_answers": "array"},
            }, f)
        with open(os.path.join(in_d, "paper.md"), "w", encoding="utf-8") as f:
            f.write(
                "# Catalyst Alpha Performance Study\n\n"
                "## Abstract\nCatalyst Alpha increases ester yield.\n\n"
                "## 2. Results\n"
                "At 60°C in vitro (n = 10, p < 0.01), Catalyst Alpha increases ester yield by 32.0% with \"special\" quotes \\ and\nnewlines.\n"
            )
        index_case(in_d, wk_d, out_d)
        with open(os.path.join(wk_d, "evidence_index.json"), "r", encoding="utf-8") as f:
            ev_idx = json.load(f)
        pub_b = extract_pubinfo_from_index(ev_idx)
        pub_p = os.path.join(wk_d, "pubinfo.json")
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump(pub_b, f)

        res_anc = next(a for a in ev_idx["anchors"] if a["section_role"] == "results" and a["block_type"] == "paragraph")
        ws_p = os.path.join(wk_d, "work_state.json")
        init_work_state(os.path.join(wk_d, "case_manifest.json"), os.path.join(wk_d, "evidence_index.json"), pub_p, ws_p, out_d)
        pt_p = os.path.join(wk_d, "patch_1.json")
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({
                "nanopublications": [{
                    "np_id": "NP1",
                    "doc_id": "D1",
                    "block_id": res_anc["anchor_id"],
                    "subject": "Catalyst Alpha",
                    "predicate": "increases",
                    "object": {"label": "ester yield", "value": 32.0, "unit": "%"},
                    "polarity": "affirmed",
                    "statement": "Catalyst Alpha increases ester yield by 32.0% at 60°C in vitro (n = 10, p < 0.01).",
                    "qualifiers": {"conditions": ["60°C", "in vitro"], "sample_size": "n = 10", "statistics": "p < 0.01"},
                    "evidence_quote": res_anc["text"],
                }],
                "qa_answers": [{
                    "question_id": "Q1",
                    "question": "How does Catalyst Alpha affect yield at 60°C?",
                    "answer_status": "answered",
                    "answer": "Catalyst Alpha increases ester yield by 32.0% at 60°C in vitro (n = 10, p < 0.01).",
                    "supporting_nanopub_ids": ["NP1"],
                }],
            }, f, ensure_ascii=False)
        patch_work_state(ws_p, pt_p)
        st = finalize_work_state(ws_p)
        aud = audit_work_state(st, ev_idx)
        res = render_all_deliverables(st, aud, out_d)
        assert res["status"] == "success"
        ok, vrep = validate_output(out_d, ingest_manifest_path=os.path.join(wk_d, "case_manifest.json"))
        assert ok, f"E2E validation failed: {vrep}"
        with open(os.path.join(out_d, "nanopubs.csv"), "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1


def test_18_validate_output_anti_dishonest_success_gate():
    with tempfile.TemporaryDirectory() as tmp:
        out_d = os.path.join(tmp, "out")
        os.makedirs(out_d)
        bad_res = {
            "case_id": "dishonest_case",
            "status": "success",
            "nanopublications": [{
                "head": {"nanopub_id": "np:sha256:1234", "uri": "urn:nanopub:1234"},
                "assertion": {"statement": "Fake", "subject": {"label": "A"}, "predicate": {"id": "causes"}, "object": {"label": "B"}, "polarity": "affirmed", "qualifiers": {}},
                "provenance": {"evidence_quote": "Not in paper", "alignment_status": "QUOTE_NOT_FOUND", "location": {"doc_id": "D1", "page": 1, "block_id": "P1-B001"}},
                "pubinfo": {"title": "X"},
            }],
            "qa_answers": [],
        }
        with open(os.path.join(out_d, "result.json"), "w", encoding="utf-8") as f:
            json.dump(bad_res, f)
        with open(os.path.join(out_d, "audit_report.json"), "w", encoding="utf-8") as f:
            json.dump({"severity_counts": {"HIGH": 1, "MEDIUM": 0, "LOW": 0}}, f)
        with open(os.path.join(out_d, "nanopubs.jsonld"), "w", encoding="utf-8") as f:
            json.dump({"@context": {}, "@graph": [{"head": {}, "assertion": {}, "provenance": {}, "pubinfo": {}}]}, f)
        with open(os.path.join(out_d, "nanopubs.trig"), "w", encoding="utf-8") as f:
            f.write("@prefix np: <http://www.nanopub.org/nschema#> .\n<urn:nanopub:1234#Head> { }\n<urn:nanopub:1234#assertion> { }\n<urn:nanopub:1234#provenance> { }\n<urn:nanopub:1234#pubinfo> { }\n")
        with open(os.path.join(out_d, "nanopubs.csv"), "w", encoding="utf-8") as f:
            f.write("nanopub_id\nnp:sha256:1234\n")
        with open(os.path.join(out_d, "report.md"), "w", encoding="utf-8") as f:
            f.write("# Report\n" + "x " * 60 + " P1-B001\n")

        ok, rep = validate_output(out_d)
        assert not ok
        err_codes = {e["code"] for e in rep["errors"]}
        assert "DISHONEST_SUCCESS_STATUS" in err_codes


def test_19_validate_output_trig_and_placeholder_gates():
    errs = validate_trig_syntax('@prefix np: <http://x#> .\n<a#Head> { <x> rdfs:label "unclosed . }\n', ["a"])
    assert any("Unclosed" in e or "Unbalanced" in e or "Missing named graph" in e for e in errs)

    with tempfile.TemporaryDirectory() as tmp:
        out_d = os.path.join(tmp, "out")
        os.makedirs(out_d)
        with open(os.path.join(out_d, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "ph_case", "status": "partial", "nanopublications": [], "note": "TBD by user"}, f)
        with open(os.path.join(out_d, "nanopubs.jsonld"), "w", encoding="utf-8") as f:
            json.dump({"@context": {}, "@graph": []}, f)
        with open(os.path.join(out_d, "nanopubs.trig"), "w", encoding="utf-8") as f:
            f.write("@prefix np: <http://www.nanopub.org/nschema#> .\n")
        with open(os.path.join(out_d, "nanopubs.csv"), "w", encoding="utf-8") as f:
            f.write("nanopub_id\n")
        with open(os.path.join(out_d, "report.md"), "w", encoding="utf-8") as f:
            f.write("# Empty Report\n" + "status partial " * 15)
        ok, rep = validate_output(out_d)
        assert not ok and any(e["code"] == "PLACEHOLDER_FOUND" for e in rep["errors"])


def test_20_scanned_unreadable_and_missing_metadata_degradation():
    with tempfile.TemporaryDirectory() as tmp:
        wk_d = os.path.join(tmp, "wk")
        out_d = os.path.join(tmp, "out")
        os.makedirs(wk_d)
        ev_idx = {
            "case_id": "scanned_case",
            "documents": [{"doc_id": "D1", "source_file": "scan.pdf", "pdf_metadata": {}}],
            "anchors": [{"anchor_id": "P1-B001", "doc_id": "D1", "page": 1, "section_role": "body_unclassified", "block_type": "paragraph", "reading_policy": "manual_review_asset", "quality_flags": ["unextractable_asset"], "text": "[Unextractable scanned page 1]"}],
        }
        sig = {
            "documents": [{"doc_id": "D1", "page_count": 1, "quality_risk": {"is_scanned": True, "unextractable_pages": [1], "garbled_pages": [], "low_density_pages": []}}],
            "initial_uncertainty": [{"doc_id": "D1", "category": "scanned_document_unreadable", "severity": "high", "pages": [1], "message": "Scanned PDF without OCR."}],
        }
        idx_p = os.path.join(wk_d, "evidence_index.json")
        man_p = os.path.join(wk_d, "case_manifest.json")
        pub_p = os.path.join(wk_d, "pubinfo.json")
        ws_p = os.path.join(wk_d, "work_state.json")
        pt_p = os.path.join(wk_d, "patch.json")
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump(ev_idx, f)
        with open(os.path.join(wk_d, "paper_signals.json"), "w", encoding="utf-8") as f:
            json.dump(sig, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "scanned_case", "research_questions": [{"question_id": "Q1", "question": "What is reported?"}]}, f)
        pub_b = extract_pubinfo_from_index(ev_idx)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump(pub_b, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({
                "qa_answers": [{"question_id": "Q1", "question": "What is reported?", "answer_status": "unanswerable", "answer": "Source PDF is scanned without extractable text or OCR.", "supporting_nanopub_ids": [], "uncertainty_note": "Page 1 unreadable."}]
            }, f)

        init_work_state(man_p, idx_p, pub_p, ws_p, out_d)
        patch_work_state(ws_p, pt_p)
        st = finalize_work_state(ws_p)
        aud = audit_work_state(st, ev_idx, paper_signals=sig)
        res = render_all_deliverables(st, aud, out_d)
        assert res["status"] in ("partial", "unanswerable")
        assert len(res["uncertainty"]) >= 1 and len(res["manual_review_checklist"]) >= 1
        ok, vrep = validate_output(out_d, ingest_manifest_path=man_p)
        assert ok, f"Scanned degradation output failed validation: {vrep}"


def test_21_skill_md_cli_contract_consistency():
    skill_path = os.path.join(BASE_DIR, "SKILL.md")
    with open(skill_path, "r", encoding="utf-8") as f:
        skill_txt = f.read()

    def _get_flags(script_path):
        with open(script_path, "r", encoding="utf-8") as sf:
            tree = ast.parse(sf.read(), filename=script_path)
        top_f, sub_p, var_cmd = {}, {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "add_parser" and node.value.args:
                    cname = ast.literal_eval(node.value.args[0])
                    var_cmd[node.targets[0].id] = cname
                    sub_p.setdefault(cname, {})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                fls = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--")]
                if not fls:
                    continue
                req = any(kw.arg == "required" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in node.keywords)
                t_name = node.func.value.id if isinstance(node.func.value, ast.Name) else "parser"
                tgt = sub_p[var_cmd[t_name]] if t_name in var_cmd else top_f
                for fl in fls:
                    tgt[fl] = req
        return top_f, sub_p

    cmds = 0
    for blk in re.findall(r"```bash\s*\n(.*?)```", skill_txt, re.S):
        joined = re.sub(r"\\\s*\n\s*", " ", blk)
        for line in joined.splitlines():
            m = re.match(r"^\s*python3?\s+-B\s+[^\s]*/scripts/([a-z0-9_]+\.py)(?:\s+(init|patch|finalize))?(.*)$", line.strip())
            if not m:
                continue
            s_name, subcmd, rest = m.group(1), m.group(2), m.group(3)
            s_path = os.path.join(SCRIPT_DIR, s_name)
            assert os.path.exists(s_path), f"Missing script referenced in SKILL.md: {s_name}"
            top_f, sub_p = _get_flags(s_path)
            valid = sub_p[subcmd] if subcmd else top_f
            used = re.findall(r"(--[a-zA-Z0-9\-]+)", rest)
            for uf in used:
                assert uf in valid, f"SKILL.md flag '{uf}' not in {s_name} {subcmd or ''}"
            for rf, is_req in valid.items():
                if is_req:
                    assert rf in used, f"SKILL.md missing required flag '{rf}' for {s_name} {subcmd or ''}"
            cmds += 1
    assert cmds >= 8, f"Expected >=8 CLI commands in SKILL.md, got {cmds}"

    json_blks = re.findall(r"```json\s*\n(.*?)```", skill_txt, re.S)
    assert json_blks, "Missing JSON patch skeleton block in SKILL.md"
    patch_obj = json.loads(json_blks[0])
    assert "nanopublications" in patch_obj and "qa_answers" in patch_obj


def test_22_outcome_delta_vs_ambient_qualifiers():
    q_delta = extract_scientific_qualifiers("Heating raises surface temperature from 20°C to 80°C, while cooling lowers it by 1.8°C.")
    assert not q_delta["by_kind"].get("temperature"), f"Outcome delta temps should be excluded: {q_delta}"
    q_zh = extract_scientific_qualifiers("加热使温度升高至 80°C，冷却使温度降低 1.8°C。")
    assert not q_zh["by_kind"].get("temperature"), f"ZH delta temps should be excluded: {q_zh}"
    q_amb = extract_scientific_qualifiers("Measured at 37°C under 300 K and pH 7.4 in vitro.")
    assert q_amb["by_kind"].get("temperature") == ["37°C", "300 K"] and q_amb["by_kind"].get("ph") == ["pH 7.4"]

    # Verify same-label different numeric values do not collide on Trusty ID and trigger quantitative_conflict
    anchors = [
        {"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Sensor A achieves 91.4% accuracy on TestSet."},
        {"anchor_id": "P3-B001", "doc_id": "D1", "page": 3, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Sensor A achieves 60.3% accuracy on TestSet."},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        idx_p, man_p, pub_p, ws_p, pt_p = [os.path.join(tmp, x) for x in ("idx.json", "man.json", "pub.json", "ws.json", "pt.json")]
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "num_hash_case", "anchors": anchors, "documents": []}, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "num_hash_case"}, f)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump({"sources": []}, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({"nanopublications": [
                {"doc_id": "D1", "block_id": "P2-B001", "subject": "Sensor A", "predicate": "achieves_metric", "object": {"label": "TestSet accuracy", "value": 91.4, "unit": "%"}, "statement": "Sensor A achieves 91.4% accuracy on TestSet.", "evidence_quote": "Sensor A achieves 91.4% accuracy on TestSet."},
                {"doc_id": "D1", "block_id": "P3-B001", "subject": "Sensor A", "predicate": "achieves_metric", "object": {"label": "TestSet accuracy", "value": 60.3, "unit": "%"}, "statement": "Sensor A achieves 60.3% accuracy on TestSet.", "evidence_quote": "Sensor A achieves 60.3% accuracy on TestSet."},
            ]}, f)
        init_work_state(man_p, idx_p, pub_p, ws_p)
        patch_work_state(ws_p, pt_p, idx_p)
        st = finalize_work_state(ws_p)
        assert len(st["nanopublications"]) == 2
        assert any(c["conflict_type"] == "quantitative_conflict" for c in st["conflicts"])


def test_23_heterogeneous_nested_output_contract_aliases():
    with tempfile.TemporaryDirectory() as tmp:
        out_d = os.path.join(tmp, "out")
        anchors = [{"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Method Q increases accuracy by 12%."}]
        pub = {"doc_id": "D1", "title": "Alias Study", "authors": ["Lin"], "year": 2025, "doi": None, "arxiv_id": None, "field_sources": {"title": "pdf_text:P1-B001"}}
        contract = {
            "case_id": "string",
            "status": "string",
            "layer_a": {
                "type": "object",
                "properties": {
                    "findings_with_evidence": {"type": "array"},
                    "triples": {"type": "array"},
                    "atomic_claims": {"type": "array"},
                    "grounded_answers": {"type": "array"},
                    "responses": {"type": "array"},
                },
                "required": ["findings_with_evidence", "triples", "atomic_claims", "grounded_answers", "responses"],
            },
            "layer_b": {
                "type": "object",
                "properties": {
                    "source_metadata": {"type": "array"},
                    "provenance_records": {"type": "array"},
                    "citations": {"type": "array"},
                    "contradictions": {"type": "array"},
                    "uncertainties": {"type": "array"},
                    "limitations": {"type": "array"},
                    "review_items": {"type": "array"},
                    "change_log": {"type": "array"},
                    "artifacts": {"type": "object"},
                },
                "required": ["source_metadata", "provenance_records", "citations", "contradictions", "uncertainties", "limitations", "review_items", "change_log", "artifacts"],
            },
        }
        idx_p, man_p, pub_p, ws_p, pt_p = [os.path.join(tmp, x) for x in ("idx.json", "man.json", "pub.json", "ws.json", "pt.json")]
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "alias_case", "anchors": anchors, "documents": [pub]}, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "alias_case", "output_contract": contract}, f)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump({"sources": [pub]}, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({
                "nanopublications": [{"np_id": "N1", "doc_id": "D1", "block_id": "P2-B001", "subject": "Method Q", "predicate": "increases", "object": "accuracy (12%)", "statement": "Method Q increases accuracy by 12%.", "evidence_quote": "Method Q increases accuracy by 12%."}],
                "qa_answers": [{"question_id": "Q1", "question": "Gain?", "answer_status": "answered", "answer": "12%.", "supporting_nanopub_ids": ["N1"]}],
            }, f)
        init_work_state(man_p, idx_p, pub_p, ws_p)
        patch_work_state(ws_p, pt_p, idx_p)
        st = finalize_work_state(ws_p)
        aud = audit_work_state(st, {"case_id": "alias_case", "anchors": anchors, "documents": [pub]})
        res = render_all_deliverables(st, aud, out_d)
        la, lb = res["layer_a"], res["layer_b"]
        assert len(la["findings_with_evidence"]) == 1 and len(la["triples"]) == 1 and len(la["atomic_claims"]) == 1
        assert len(la["grounded_answers"]) == 1 and len(la["responses"]) == 1
        assert len(lb["source_metadata"]) == 1 and len(lb["provenance_records"]) == 1 and len(lb["citations"]) == 1
        assert isinstance(lb["artifacts"], dict) and "result_json" in lb["artifacts"]
        ok, vrep = validate_output(out_d, ingest_manifest_path=man_p)
        assert ok, f"Alias contract validation failed: {vrep}"


def test_24_anchors_compact_txt_generation():
    with tempfile.TemporaryDirectory() as tmp:
        in_d = os.path.join(tmp, "in")
        wk_d = os.path.join(tmp, "wk")
        out_d = os.path.join(tmp, "out")
        os.makedirs(in_d)
        with open(os.path.join(in_d, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "compact_case"}, f)
        with open(os.path.join(in_d, "paper.md"), "w", encoding="utf-8") as f:
            f.write("# Compact Anchor Test\n\n## 1. Introduction\nPrior study [1] tested 15% gain.\n")
        index_case(in_d, wk_d, out_d)
        compact_p = os.path.join(wk_d, "anchors_compact.txt")
        assert os.path.exists(compact_p), "anchors_compact.txt was not generated"
        with open(os.path.join(wk_d, "evidence_index.json"), "r", encoding="utf-8") as f:
            ev_idx = json.load(f)
        assert ev_idx.get("artifacts", {}).get("anchors_compact_path") == compact_p
        with open(compact_p, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        assert len(lines) == len(ev_idx["anchors"])
        assert any("[D1 | " in ln and "role=introduction" in ln and "flags=" in ln for ln in lines)


def test_25_changes_and_checklist_temp_id_cleanup():
    anchors = [
        {"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Protocol Z improves accuracy by 18%."}
    ]
    with tempfile.TemporaryDirectory() as tmp:
        idx_p, man_p, pub_p, ws_p, pt_p = [os.path.join(tmp, x) for x in ("idx.json", "man.json", "pub.json", "ws.json", "pt.json")]
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "tmp_cleanup", "anchors": anchors, "documents": []}, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "tmp_cleanup"}, f)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump({"sources": []}, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({
                "nanopublications": [{
                    "np_id": "TMP-Z99",
                    "doc_id": "D1",
                    "block_id": "P2-B001",
                    "subject": "Protocol Z",
                    "predicate": "increases",
                    "object": "accuracy (18%)",
                    "statement": "Protocol Z improves accuracy by 18%.",
                    "evidence_quote": "Protocol Z improves accuracy by 18%.",
                }],
                "qa_answers": [{"question_id": "Q1", "question": "Gain?", "answer_status": "answered", "answer": "See TMP-Z99 (18%).", "supporting_nanopub_ids": ["TMP-Z99"]}],
                "changes": [{"step": "custom_fix", "from_id": "TMP-Z99", "to_id": "TMP-Z99", "affected_ids": ["TMP-Z99"], "description": "Refined TMP-Z99 qualifiers.", "note": "Updated TMP-Z99."}],
                "manual_review_checklist": [{"item_id": "MR-1", "severity": "low", "category": "check", "np_ids": ["TMP-Z99"], "reason": "Verify TMP-Z99."}],
            }, f)
        init_work_state(man_p, idx_p, pub_p, ws_p)
        patch_work_state(ws_p, pt_p, idx_p)
        st = finalize_work_state(ws_p)
        canon_id = st["nanopublications"][0]["head"]["nanopub_id"]
        assert canon_id.startswith("np:sha256:")
        chg_dump = json.dumps(st["changes"], ensure_ascii=False)
        mr_dump = json.dumps(st["manual_review_checklist"], ensure_ascii=False)
        qa_dump = json.dumps(st["qa_answers"], ensure_ascii=False)
        assert "TMP-Z99" not in chg_dump and canon_id in chg_dump
        assert "TMP-Z99" not in mr_dump and canon_id in mr_dump
        assert "TMP-Z99" not in qa_dump and canon_id in qa_dump


def test_26_high_uncertainty_honest_partial_and_medium_tolerance():
    anchors = [
        {"anchor_id": "P1-B001", "doc_id": "D1", "page": 1, "section_role": "title_meta", "block_type": "title", "text": "W Study. Zoe. 2025."},
        {"anchor_id": "P2-B001", "doc_id": "D1", "page": 2, "section_role": "results", "block_type": "paragraph", "has_quantitative_signals": True, "text": "Protocol W increases yield by 10%."},
    ]
    pub = {"doc_id": "D1", "title": "W Study", "authors": ["Zoe"], "year": 2025, "doi": None, "arxiv_id": None, "field_sources": {"title": "pdf_text:P1-B001", "authors": "pdf_text:P1-B001", "year": "pdf_text:P1-B001"}}
    with tempfile.TemporaryDirectory() as tmp:
        idx_p, man_p, pub_p, ws_p, pt_p = [os.path.join(tmp, x) for x in ("idx.json", "man.json", "pub.json", "ws.json", "pt.json")]
        out_high = os.path.join(tmp, "out_high")
        out_med = os.path.join(tmp, "out_med")
        with open(idx_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "unc_gate_case", "anchors": anchors, "documents": [pub]}, f)
        with open(man_p, "w", encoding="utf-8") as f:
            json.dump({"case_id": "unc_gate_case"}, f)
        with open(pub_p, "w", encoding="utf-8") as f:
            json.dump({"sources": [pub]}, f)
        with open(pt_p, "w", encoding="utf-8") as f:
            json.dump({
                "nanopublications": [{"np_id": "NW", "doc_id": "D1", "block_id": "P2-B001", "subject": "Protocol W", "predicate": "increases", "object": "yield (10%)", "statement": "Protocol W increases yield by 10%.", "evidence_quote": "Protocol W increases yield by 10%."}],
                "qa_answers": [{"question_id": "Q1", "question": "Yield?", "answer_status": "answered", "answer": "10%.", "supporting_nanopub_ids": ["NW"]}],
            }, f)

        # Case A: medium uncertainty -> status remains success
        init_work_state(man_p, idx_p, pub_p, ws_p)
        patch_work_state(ws_p, pt_p, idx_p)
        st_med = finalize_work_state(ws_p)
        st_med["uncertainty"].append({"uncertainty_id": "U-MED", "category": "missing_pubinfo", "severity": "medium", "message": "DOI missing."})
        aud_med = audit_work_state(st_med, {"case_id": "unc_gate_case", "anchors": anchors, "documents": [pub]})
        res_med = render_all_deliverables(st_med, aud_med, out_med)
        assert res_med["status"] == "success"
        ok_m, _ = validate_output(out_med, ingest_manifest_path=man_p)
        assert ok_m

        # Case B: high uncertainty -> honest partial downgrade & report Section 7 present
        st_high = json.loads(json.dumps(st_med))
        st_high["uncertainty"].append({"uncertainty_id": "U-HIGH", "category": "formula_pua_unrecoverable", "severity": "high", "message": "Formula PUA glyphs unrecoverable."})
        res_high = render_all_deliverables(st_high, aud_med, out_high)
        assert res_high["status"] == "partial"
        ok_h, _ = validate_output(out_high, ingest_manifest_path=man_p)
        assert ok_h
        with open(os.path.join(out_high, "report.md"), "r", encoding="utf-8") as f:
            assert "交付物清单与复核顺序" in f.read()

        # Case C: tamper high-uncertainty result.json back to success -> validate_output must reject
        with open(os.path.join(out_high, "result.json"), "r", encoding="utf-8") as f:
            tamp = json.load(f)
        tamp["status"] = "success"
        with open(os.path.join(out_high, "result.json"), "w", encoding="utf-8") as f:
            json.dump(tamp, f)
        ok_bad, rep_bad = validate_output(out_high, ingest_manifest_path=man_p)
        assert not ok_bad and any(e["code"] == "DISHONEST_SUCCESS_STATUS" for e in rep_bad["errors"])


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    tests = [
        test_01_ast_stdlib_only_top_level_imports,
        test_02_cli_utf8_reconfigure_and_no_local_leaks,
        test_03_relation_vocab_integrity,
        test_04_case_ingestor_questions_and_formats,
        test_05_structure_indexer_10_roles_and_signals,
        test_06_entity_guard_qualifiers_and_causal_upgrade,
        test_07_pubinfo_extractor_and_anti_hallucination,
        test_08_evidence_aligner_modes_and_flags,
        test_09_examples_20_nanopubs_all_pass_aligner_and_auditor,
        test_10_mini_benchmark_6_traps,
        test_11_assembler_init_early_partial_fallback,
        test_12_assembler_trusty_id_determinism,
        test_13_assembler_cross_section_dedup_promotion_and_qa_redirect,
        test_14_assembler_polarity_and_quantitative_conflicts,
        test_15_auditor_d1_to_d8_edge_traps,
        test_16_render_deliverables_nested_output_contract,
        test_17_end_to_end_markdown_paper_all_formats_pass,
        test_18_validate_output_anti_dishonest_success_gate,
        test_19_validate_output_trig_and_placeholder_gates,
        test_20_scanned_unreadable_and_missing_metadata_degradation,
        test_21_skill_md_cli_contract_consistency,
        test_22_outcome_delta_vs_ambient_qualifiers,
        test_23_heterogeneous_nested_output_contract_aliases,
        test_24_anchors_compact_txt_generation,
        test_25_changes_and_checklist_temp_id_cleanup,
        test_26_high_uncertainty_honest_partial_and_medium_tolerance,
    ]

    passed = 0
    failed = 0
    for t_fn in tests:
        try:
            t_fn()
            print(f"[PASS] {t_fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {t_fn.__name__}: {exc}")
            failed += 1

    print(f"\n[self_test] summary: passed={passed}/{len(tests)}, failed={failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
