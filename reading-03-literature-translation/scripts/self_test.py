#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_test.py - Zero-Dependency Offline Regression Suite (15 Independent Test Cases)
Part of the Scientific Translation Skill (task_03_translation).
Top-level imports are strictly restricted to Python standard libraries.
Uses tempfile.TemporaryDirectory() only; never depends on external files or test_run/.
"""

import os
import sys
import json
import time
import tempfile
import contextlib
import io

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pdf_engine.layout_parser import normalize_paragraph_text, is_table_row_heuristic, split_trailing_prose_from_formulas
from case_ingestor import ingest_case, count_words_and_sentences, SectionTracker, infer_heading_level
from term_extractor import extract_candidates, compile_glossary
from entity_guard import run_entity_guard, load_invariants, extract_unit_fingerprints
from translation_verifier import verify_translations
from merge_checkpoint import merge_checkpoint, diff_centered_excerpts
from render_deliverables import render_deliverables
from validate_output import validate_output


def _create_minimal_blank_pdf(path):
    """Synthesize a valid ISO PDF-1.4 1-page blank document using standard library bytes only."""
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    offsets = []
    body = bytearray(header)
    for obj in objs:
        offsets.append(len(body))
        body.extend(obj)
    xref_pos = len(body)
    body.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        body.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    body.extend(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii"))
    with open(path, "wb") as f:
        f.write(body)


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield


# ----------------------------------------------------------------------
# Test 1: Markdown Ingestion, Heading Stack Pop & Scope + Glossary Discovery
# ----------------------------------------------------------------------
def test_markdown_ingest_and_scope():
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = os.path.join(tmp, "in")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(in_dir)

        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "t1_md", "instructions": "Translate only Introduction."}, f)
        with open(os.path.join(in_dir, "glossary.csv"), "w", encoding="utf-8") as f:
            f.write("source_term,canonical_target\nquantum channel,量子信道\n")
        with open(os.path.join(in_dir, "paper.md"), "w", encoding="utf-8") as f:
            f.write(
                "# Paper Title\n\n## 1. Introduction\n\nIntro body text about quantum channel.\n\n"
                "### 1.1 Background\n\nSub background.\n\n## 2. Methods\n\nMethod body excluded.\n"
            )

        with _quiet():
            man = ingest_case(in_dir, work_dir)

        assert len(man["glossary_files_detected"]) == 1, "Failed to detect glossary.csv"
        with open(os.path.join(work_dir, "source_units.json"), encoding="utf-8") as f:
            units = json.load(f)["units"]

        # Verify section stack properly popped 1.1 before 2. Methods
        sec_paths = [u["section_path"] for u in units]
        assert any(s == "Paper Title > 2. Methods" for s in sec_paths), f"Stack pop failed: {sec_paths}"

        # Verify monotonic stack directly on numbered headings
        st = SectionTracker()
        st.push_heading("1. Introduction", infer_heading_level("1. Introduction"))
        st.push_heading("2. Preliminaries", infer_heading_level("2. Preliminaries"))
        st.push_heading("2.1 Sub", infer_heading_level("2.1 Sub"))
        assert st.get_path() == "2. Preliminaries > 2.1 Sub", f"Bad monotonic stack: {st.get_path()}"

        # Verify scope filter excluded Methods
        methods_units = [u for u in units if "Methods" in u["section_path"]]
        assert all(not u["in_scope"] for u in methods_units), "Scope filter did not exclude Methods"


# ----------------------------------------------------------------------
# Test 2: CJK/EN Mixed Word Count & Physical Soft Linebreak Cleaning
# ----------------------------------------------------------------------
def test_cjk_word_count_and_soft_wrap():
    en_soft = "We study quan-\ntum fault-\ntolerance across distributed\nnodes."
    cleaned_en = normalize_paragraph_text(en_soft, "paragraph")
    assert "quantum" in cleaned_en and "fault-tolerance" in cleaned_en and "\n" not in cleaned_en, f"Bad EN norm: {cleaned_en}"

    zh_soft = "表 1 不同量子\n信道参数在测试\n集上的对比"
    cleaned_zh = normalize_paragraph_text(zh_soft, "paragraph")
    assert cleaned_zh == "表 1 不同量子信道参数在测试集上的对比", f"Bad ZH norm: {cleaned_zh}"

    wc, cc, sc = count_words_and_sentences("在振幅阻尼条件下，纠缠度 E_N 下降约 14.3%。")
    assert wc >= 15 and cc > wc, f"Bad CJK word count: wc={wc}, cc={cc}"

    # Verify table_row_heuristic exemption on theorem prose
    assert not is_table_row_heuristic("定理 1. 设信道噪声参数为 η ∈ [0,\n1]，则满足："), "Theorem falsely flagged as table"
    assert not is_table_row_heuristic("2\nperfectly\ndistinguishable, and does not satisfy privacy."), "Prose falsely flagged as table"


# ----------------------------------------------------------------------
# Test 3: Scanned / Empty PDF Honest Fallback (No Fabrication)
# ----------------------------------------------------------------------
def test_scanned_untranslatable_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = os.path.join(tmp, "in")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(in_dir)
        pdf_path = os.path.join(in_dir, "blank_scan.pdf")
        _create_minimal_blank_pdf(pdf_path)

        with _quiet():
            man = ingest_case(in_dir, work_dir)

        assert man["untranslatable_asset_count"] == 1, f"Expected 1 untranslatable_asset, got {man}"
        assert man["batch_count"] == 0, "Blank scan should not generate translation batches"
        with open(os.path.join(work_dir, "source_units.json"), encoding="utf-8") as f:
            u = json.load(f)["units"][0]
        assert u["unit_type"] == "untranslatable_asset"
        assert u["translate_policy"] == "manual_review_asset"
        assert u["uncertainty_flag"] is True


# ----------------------------------------------------------------------
# Test 4: Term Extraction, Narrative Filtering & Boundary Compilation
# ----------------------------------------------------------------------
def test_term_extract_and_compile_boundaries():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = os.path.join(tmp, "work")
        os.makedirs(work_dir)

        units = [
            {"unit_id": "U0001", "unit_type": "abstract", "page_span": [1, 1], "translate_policy": "translate",
             "source_text": "We propose Entanglement-Assisted Quantum Differential Privacy (EA-QDP). Last inequality holds and inequality follows immediately."},
            {"unit_id": "U0002", "unit_type": "paragraph", "page_span": [1, 1], "translate_policy": "translate",
             "source_text": "The EA-QDP parameter bounds privacy leakage under entanglement attack. Note EA-QDP is stronger than DP."},
            {"unit_id": "U0003", "unit_type": "paragraph", "page_span": [1, 1], "translate_policy": "translate",
             "source_text": "Compute entanglement amplification (EA) via SDP. Last inequality follows directly."},
        ]
        with open(os.path.join(work_dir, "source_units.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "t4", "units": units}, f)

        with _quiet():
            cands = extract_candidates(work_dir, max_terms=30)

        cand_terms = [c["source_term"].lower() for c in cands["candidates"]]
        assert not any("last inequality" in t or "inequality follows" in t for t in cand_terms), f"Noise phrase leaked: {cand_terms}"

        # Test compilation boundary: 'EA' must NOT falsely match inside 'EA-QDP' in U0002
        g_path = os.path.join(work_dir, "glossary.json")
        with open(g_path, "w", encoding="utf-8") as f:
            json.dump({
                "terms": [
                    {"term_id": "T1", "source_term": "entanglement amplification", "aliases": ["EA"], "canonical_target": "纠缠放大"},
                    {"term_id": "T2", "source_term": "entanglement-assisted quantum differential privacy", "aliases": ["EA-QDP"], "canonical_target": "纠缠辅助量子差分隐私"},
                ]
            }, f)

        with _quiet():
            comp = compile_glossary(work_dir, g_path)

        u2_terms = [t["term_id"] for t in comp["unit_term_index"].get("U0002", [])]
        u3_terms = [t["term_id"] for t in comp["unit_term_index"].get("U0003", [])]
        assert "T1" not in u2_terms and "T2" in u2_terms, f"Boundary leak: EA matched EA-QDP in U0002! {u2_terms}"
        assert "T1" in u3_terms, f"EA failed to match standalone EA in U0003! {u3_terms}"


# ----------------------------------------------------------------------
# Test 5: Entity Guard Fingerprints & CJK/Metadata Exemptions
# ----------------------------------------------------------------------
def test_entity_guard_fingerprints():
    inv = load_invariants()
    # 5a. Numbers, citations range, cross-refs (no false match on 'secretly'/'algorithms')
    u_en = {
        "unit_id": "U1", "unit_type": "paragraph", "page_span": [1, 1],
        "source_text": "Algorithms work secretly in Fig. 1 and Table 2 (Eq. (10)) with 85 μs and 99.45% [8]-[10]."
    }
    fp_en = extract_unit_fingerprints(u_en, inv)
    ref_raws = [r["raw"] for r in fp_en["cross_refs"]]
    assert all("secretly" not in r.lower() and "algorithms" not in r.lower() for r in ref_raws), f"False cross-ref: {ref_raws}"
    assert len(fp_en["cross_refs"]) == 3, f"Expected 3 cross-refs, got {ref_raws}"
    assert fp_en["citations"][0]["expanded_ids"] == ["8", "9", "10"], f"Range expand failed: {fp_en['citations']}"
    assert len(fp_en["numbers_units"]) == 2, f"Expected 2 nums (85 μs, 99.45%), got {fp_en['numbers_units']}"

    # 5b. Chinese non-negation compounds ('不同', '不仅') must NOT trigger negation
    u_zh = {"unit_id": "U2", "unit_type": "table_caption", "page_span": [1, 1], "source_text": "表 1 不同量子信道不仅稳定且高效"}
    fp_zh = extract_unit_fingerprints(u_zh, inv)
    assert fp_zh["logic_markers"]["negation"] == [], f"False negation on 不同/不仅: {fp_zh['logic_markers']}"

    # 5c. Metadata unit numbers skipped
    u_meta = {"unit_id": "U3", "unit_type": "metadata", "page_span": [1, 1], "source_text": "Author A1,2, Author B2"}
    fp_meta = extract_unit_fingerprints(u_meta, inv)
    assert fp_meta["numbers_units"] == [], f"Metadata numbers not exempted: {fp_meta['numbers_units']}"


# ----------------------------------------------------------------------
# Test 6: Verifier 8 Classic Faults + 0 False Positives
# ----------------------------------------------------------------------
def test_verifier_8_faults_and_0_false_positives():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = os.path.join(tmp, "work")
        os.makedirs(work_dir)
        units = [
            {"unit_id": "U01", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 10, "translate_policy": "translate",
             "source_text": "Missing unit test sentence."},
            {"unit_id": "U02", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 30, "translate_policy": "translate",
             "source_text": "We evaluate a detailed protocol across multiple noisy quantum computing benchmarks using extensive simulations and theoretical error bounds to confirm stability across all trials."},
            {"unit_id": "U03", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 8, "translate_policy": "translate",
             "source_text": "We apply differential privacy here."},
            {"unit_id": "U04", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 8, "translate_policy": "translate",
             "source_text": "Accuracy reaches 93.5% on test data."},
            {"unit_id": "U05", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 8, "translate_policy": "translate",
             "source_text": "Prior works proved this theorem [11], [12]."},
            {"unit_id": "U06", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 8, "translate_policy": "translate",
             "source_text": "Results are summarized in Table 3."},
            {"unit_id": "U07", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 10, "translate_policy": "translate",
             "source_text": "The bound is not exceeded and holds for at most 5 steps."},
            {"unit_id": "U08", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 8, "translate_policy": "translate",
             "source_text": "We introduce a clean noise mechanism."},
            {"unit_id": "U09", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 16, "translate_policy": "translate",
             "source_text": "Differential privacy does not fail in Table 1 [4] with accuracy at most 80.5%."},
        ]
        with open(os.path.join(work_dir, "source_units.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "t6", "source_language": "en", "target_language": "zh", "units": units}, f)
        g_path = os.path.join(work_dir, "glossary.json")
        with open(g_path, "w", encoding="utf-8") as f:
            json.dump({"terms": [{"term_id": "T1", "source_term": "differential privacy", "canonical_target": "差分隐私"}]}, f)

        with _quiet():
            compile_glossary(work_dir, g_path)
            run_entity_guard(work_dir, enrich_batches=False)

        trans = {
            # U01 omitted -> MISSING_UNIT
            "U02": {"translated_text": "我们测了协议。"},  # SEVERE_UNDER_TRANSLATION
            "U03": {"translated_text": "我们在此应用微分隐私。"},  # TERM_MISMATCH
            "U04": {"translated_text": "在测试数据上准确率达到 95.0%。"},  # NUMBER_MISSING
            "U05": {"translated_text": "先前工作证明了该定理 [11]。"},  # CITATION_MISSING
            "U06": {"translated_text": "结果已汇总于前表中。"},  # CROSS_REF_MISSING
            "U07": {"translated_text": "该界限被满足且对 5 步成立。"},  # LOGIC_NEGATION_RISK & LOGIC_QUALIFIER_RISK
            "U08": {"translated_text": "以下是该段的中文翻译：我们引入了一种干净的噪声机制。"},  # BOILERPLATE_LEAK
            "U09": {"translated_text": "差分隐私在表 1 [4] 中并未失效，其准确率至多为 80.5%。"},  # CLEAN (0 issues)
        }
        t_path = os.path.join(work_dir, "trans.json")
        with open(t_path, "w", encoding="utf-8") as f:
            json.dump(trans, f)

        with _quiet():
            rep, _ = verify_translations(work_dir, t_path)

        codes = {iss["code"] for iss in rep["issues"]}
        expected_codes = {
            "MISSING_UNIT", "SEVERE_UNDER_TRANSLATION", "TERM_MISMATCH",
            "NUMBER_MISSING", "CITATION_MISSING", "CROSS_REF_MISSING",
            "LOGIC_NEGATION_RISK", "LOGIC_QUALIFIER_RISK", "BOILERPLATE_LEAK"
        }
        assert expected_codes.issubset(codes), f"Missed fault codes: {expected_codes - codes}"
        u09_issues = [iss for iss in rep["issues"] if iss["unit_id"] == "U09"]
        assert len(u09_issues) == 0, f"False positive on clean U09: {u09_issues}"


# ----------------------------------------------------------------------
# Test 7: Merge Checkpoint (Markdown Fence, Context Guard & Diff Excerpt)
# ----------------------------------------------------------------------
def test_merge_checkpoint_fenced_and_diff_excerpt():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = os.path.join(tmp, "work")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(work_dir); os.makedirs(out_dir)

        units = [
            {"unit_id": "U01", "unit_type": "paragraph", "page_span": [1, 1], "translate_policy": "translate", "in_scope": True, "source_text": "First [11], [12]."},
            {"unit_id": "U02", "unit_type": "paragraph", "page_span": [1, 1], "translate_policy": "translate", "in_scope": True, "source_text": "Second."},
        ]
        with open(os.path.join(work_dir, "source_units.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "t7", "units": units}, f)

        # Fenced batch with context_tail leakage U99
        b1_path = os.path.join(work_dir, "b1_out.json")
        with open(b1_path, "w", encoding="utf-8") as f:
            f.write('```json\n[{"unit_id":"U01","translated_text":"前置很长的中文文本用于验证差异中心切片定位效果，此处省略了引用标记 [11], [13] 后续内容。"},'
                    '{"unit_id":"U99","translated_text":"泄漏","role":"context_only_do_not_retranslate"}]\n```')

        with _quiet():
            res_mid = merge_checkpoint(work_dir, out_dir, add_batch=b1_path)
        assert res_mid["status"] == "partial" and res_mid["progress"]["completed_units"] == 1

        # Apply repair with diff-centered excerpt check
        rep_path = os.path.join(work_dir, "r1.json")
        with open(rep_path, "w", encoding="utf-8") as f:
            json.dump([{"unit_id": "U01", "translated_text": "前置很长的中文文本用于验证差异中心切片定位效果，此处省略了引用标记 [11], [12], [13] 后续内容。", "trigger_codes": ["CITATION_MISSING"]}], f)

        with _quiet():
            merge_checkpoint(work_dir, out_dir, apply_repair=rep_path)

        with open(os.path.join(work_dir, "change_records.json"), encoding="utf-8") as f:
            recs = json.load(f)["records"]
        rep_rec = [r for r in recs if r["action"] == "repair"][0]
        assert "[11], [12], [13]" in rep_rec["after_excerpt"], f"Diff excerpt did not center on edit: {rep_rec}"
        assert "[11], [13]" in rep_rec["before_excerpt"], f"Before excerpt did not center on edit: {rep_rec}"


# ----------------------------------------------------------------------
# Test 8: Mode C Existing Translation Direct Audit & Repair Loop
# ----------------------------------------------------------------------
def test_mode_c_existing_translation_audit_repair():
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = os.path.join(tmp, "work")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(work_dir); os.makedirs(out_dir)

        units = [{"unit_id": "U01", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 10, "translate_policy": "translate", "in_scope": True,
                  "source_text": "Accuracy is 92.4% in Fig. 2."}]
        with open(os.path.join(work_dir, "source_units.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "t8_mode_c", "source_language": "en", "target_language": "zh", "units": units}, f)

        draft = os.path.join(work_dir, "draft.json")
        with open(draft, "w", encoding="utf-8") as f:
            json.dump([{"unit_id": "U01", "translated_text": "图 2 中的准确率是 91.0%。"}], f)

        with _quiet():
            run_entity_guard(work_dir)
            merge_checkpoint(work_dir, out_dir, add_batch=draft)
            rep1, rq = verify_translations(work_dir, os.path.join(work_dir, "merged_translations.json"))

        assert rep1["metrics"]["error_count"] == 1 and rq["total_repair_units"] == 1

        fix = os.path.join(work_dir, "fix.json")
        with open(fix, "w", encoding="utf-8") as f:
            json.dump([{"unit_id": "U01", "translated_text": "图 2 中的准确率是 92.4%。"}], f)

        with _quiet():
            merge_checkpoint(work_dir, out_dir, apply_repair=fix)
            rep2, _ = verify_translations(work_dir, os.path.join(work_dir, "merged_translations.json"))
            render_deliverables(work_dir, out_dir, is_final=True)

        assert rep2["metrics"]["error_count"] == 0 and rep2["audit_status"] == "PASS"
        ok, _ = validate_output(out_dir, strict=True)
        assert ok is True


# ----------------------------------------------------------------------
# Test 9: Dynamic Output Contract Mapping (Dict & List Schemas)
# ----------------------------------------------------------------------
def test_dynamic_output_contract_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        for mode_idx, contract in enumerate([
            {"case_id": "string", "bilingual_segments": "list", "terminology_table": "list", "audit_report": "object", "uncertainty_notes": "list"},
            ["case_id", "bilingual_pairs", "glossary", "verification_summary", "custom_summary_overview"]
        ]):
            sub = os.path.join(tmp, f"m{mode_idx}")
            in_d, w_d, o_d = [os.path.join(sub, x) for x in ("in", "w", "o")]
            os.makedirs(in_d); os.makedirs(w_d); os.makedirs(o_d)

            with open(os.path.join(in_d, "case.json"), "w", encoding="utf-8") as f:
                json.dump({"case_id": f"c_{mode_idx}", "output_contract": contract}, f)
            with open(os.path.join(w_d, "source_units.json"), "w", encoding="utf-8") as f:
                json.dump({"case_id": f"c_{mode_idx}", "units": [
                    {"unit_id": "U01", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 4, "translate_policy": "translate", "in_scope": True, "source_text": "Hello world."}
                ]}, f)
            with open(os.path.join(w_d, "merged_translations.json"), "w", encoding="utf-8") as f:
                json.dump({"U01": {"unit_id": "U01", "translated_text": "你好世界。"}}, f)
            with _quiet():
                verify_translations(w_d, os.path.join(w_d, "merged_translations.json"))
                render_deliverables(w_d, o_d, input_dir=in_d, is_final=True)

            ok, rep = validate_output(o_d, input_dir=in_d, strict=True)
            assert ok is True, f"Contract mapping validation failed for schema {mode_idx}: {rep}"


# ----------------------------------------------------------------------
# Test 10: Validate Output Hard Rejection of Dishonest 'success' Status
# ----------------------------------------------------------------------
def test_validate_output_dishonest_status_rejection():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir)
        # Create fake result claiming status='success' while a translatable unit has empty text
        fake_res = {
            "case_id": "dishonest_case",
            "status": "success",
            "sources": {"dominant_format": "markdown"},
            "uncertainty": [],
            "change_records": [],
            "bilingual_alignment": [
                {"unit_id": "U01", "translate_policy": "translate", "source_text": "Test", "translated_text": ""}
            ],
            "verification_summary": {"audit_status": "PASS", "metrics": {"error_count": 0}},
            "artifacts": ["result.json"]
        }
        with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(fake_res, f)

        ok, rep = validate_output(out_dir, strict=True)
        assert ok is False, "Validator failed to reject dishonest status='success'"
        codes = {e["code"] for e in rep["errors"]}
        assert "STATUS_DISHONEST" in codes, f"Expected STATUS_DISHONEST, got {codes}"


# ----------------------------------------------------------------------
# Test 11: Recursive Subdir + Real LaTeX/HTML Ingestion & Semantic case_id
# ----------------------------------------------------------------------
def test_recursive_subdir_and_latex_html_ingest():
    with tempfile.TemporaryDirectory() as tmp:
        # 11a. Subdirectory recursive discovery & pure task.md without case.json
        sub_in = os.path.join(tmp, "input")
        sub_mat = os.path.join(sub_in, "materials", "sub")
        sub_w = os.path.join(tmp, "w_sub")
        os.makedirs(sub_mat)
        with open(os.path.join(sub_in, "task.md"), "w", encoding="utf-8") as f:
            f.write("# Quantum Entanglement Subdir Case\n\nTranslate all files inside materials.\n")
        with open(os.path.join(sub_mat, "glossary.csv"), "w", encoding="utf-8") as f:
            f.write("source_term,canonical_target\nbell state,贝尔态\n")
        with open(os.path.join(sub_mat, "doc.md"), "w", encoding="utf-8") as f:
            f.write("# Subdir Paper\n\nBell state fidelity reaches 99.1%.\n")

        with _quiet():
            man_sub = ingest_case(sub_in, sub_w)
        assert man_sub["total_units"] >= 2, f"Recursive subdir discovery failed: {man_sub}"
        assert len(man_sub["glossary_files_detected"]) == 1, "Failed to recursively discover nested glossary"
        assert man_sub["case_id"] not in ("input", "in", ""), f"Bad semantic case_id: {man_sub['case_id']}"

        # 11b. Real .tex ingestion + LaTeX entities (\cite, \ref, \%)
        tex_in = os.path.join(tmp, "tex_in")
        tex_w = os.path.join(tmp, "tex_w")
        os.makedirs(tex_in)
        with open(os.path.join(tex_in, "paper.tex"), "w", encoding="utf-8") as f:
            f.write(
                "\\documentclass{article}\n\\title{Error Correction with $[[n,k,d]]$ Codes}\n"
                "\\begin{document}\n\\maketitle\n"
                "\\begin{abstract}We improve threshold by 18.5\\%.\n\\end{abstract}\n"
                "\\section{Introduction}\n% This is a hidden comment\n"
                "Following Smith~\\cite{smith2024} and Fig.~\\ref{fig:arch}, error drops 18.5\\%.\n"
                "\\begin{equation}E = mc^2\\end{equation}\n"
                "\\begin{thebibliography}{9}\n\\bibitem{smith2024} J. Smith, 2024.\n\\end{thebibliography}\n"
                "\\end{document}\n"
            )
        with _quiet():
            man_tex = ingest_case(tex_in, tex_w)
            run_entity_guard(tex_w, enrich_batches=False)
        assert man_tex["dominant_format"] == "latex" and man_tex["total_units"] >= 5, f"LaTeX parse failed: {man_tex}"
        with open(os.path.join(tex_w, "source_units.json"), encoding="utf-8") as f:
            tex_units = json.load(f)["units"]
        assert not any("hidden comment" in u["source_text"] for u in tex_units), "LaTeX comment leaked!"
        para_u = [u for u in tex_units if u["unit_type"] == "paragraph" and "smith2024" in u["source_text"]][0]
        with open(os.path.join(tex_w, "unit_fingerprints.json"), encoding="utf-8") as f:
            fp_tex = json.load(f)["fingerprints"][para_u["unit_id"]]
        assert any(c["kind"] == "latex_cite" and "smith2024" in c["expanded_ids"] for c in fp_tex["citations"])
        assert any(r.get("is_latex_ref") and r["ref_id"] == "fig:arch" for r in fp_tex["cross_refs"])
        assert any(n["norm_value"] == "18.5" and n["unit"] == "%" for n in fp_tex["numbers_units"])

        # Verify valid ZH LaTeX translation passes verifier cleanly
        t_tex = os.path.join(tex_w, "trans_tex.json")
        trans_payload = {
            u["unit_id"]: {"translated_text": (
                "遵循 Smith~\\cite{smith2024} 与图~\\ref{fig:arch}，误差降低 18.5\\%。"
                if u["unit_id"] == para_u["unit_id"] else "已翻译内容 18.5%。"
            )}
            for u in tex_units if u["translate_policy"] == "translate"
        }
        with open(t_tex, "w", encoding="utf-8") as f:
            json.dump(trans_payload, f, ensure_ascii=False)
        with _quiet():
            rep_tex, _ = verify_translations(tex_w, t_tex)
        assert rep_tex["metrics"]["error_count"] == 0, f"LaTeX verification failed: {rep_tex['issues']}"

        # 11c. Real .html ingestion
        html_in = os.path.join(tmp, "html_in")
        html_w = os.path.join(tmp, "html_w")
        os.makedirs(html_in)
        with open(os.path.join(html_in, "page.html"), "w", encoding="utf-8") as f:
            f.write(
                "<html><head><title>HTML Sci Doc</title><script>var secret=123;</script></head>"
                "<body><h1>1. Overview</h1><p>Quantum readout fidelity is 99.2%.</p>"
                "<figcaption>Fig. 1 Setup</figcaption></body></html>"
            )
        with _quiet():
            man_html = ingest_case(html_in, html_w)
        assert man_html["dominant_format"] == "html" and man_html["total_units"] == 4, f"HTML parse failed: {man_html}"
        with open(os.path.join(html_w, "source_units.json"), encoding="utf-8") as f:
            h_units = json.load(f)["units"]
        assert not any("var secret" in u["source_text"] for u in h_units), "Script tag leaked into HTML units!"


# ----------------------------------------------------------------------
# Test 12: Nested JSON Schema Contract & Type Mismatch Validation
# ----------------------------------------------------------------------
def test_nested_json_schema_contract_and_type_check():
    with tempfile.TemporaryDirectory() as tmp:
        in_d, w_d, o_d = [os.path.join(tmp, x) for x in ("in", "w", "o")]
        os.makedirs(in_d); os.makedirs(w_d); os.makedirs(o_d)

        nested_schema = {
            "type": "object",
            "required": ["translation_payload", "status"],
            "properties": {
                "translation_payload": {
                    "type": "object",
                    "required": ["segments", "glossary"],
                    "properties": {
                        "segments": {"type": "array"},
                        "glossary": {"type": "array"},
                        "case_summary": {"type": "string"}
                    }
                },
                "status": {"type": "string"}
            }
        }
        with open(os.path.join(in_d, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "nested_schema_case", "output_contract": nested_schema}, f)
        with open(os.path.join(w_d, "source_units.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "nested_schema_case", "units": [
                {"unit_id": "U01", "unit_type": "paragraph", "page_span": [1, 1], "word_count": 5, "translate_policy": "translate", "in_scope": True, "source_text": "Quantum noise channel."}
            ]}, f)
        with open(os.path.join(w_d, "merged_translations.json"), "w", encoding="utf-8") as f:
            json.dump({"U01": {"unit_id": "U01", "translated_text": "量子噪声信道。"}}, f)

        with _quiet():
            verify_translations(w_d, os.path.join(w_d, "merged_translations.json"))
            res = render_deliverables(w_d, o_d, input_dir=in_d, is_final=True)

        assert isinstance(res["translation_payload"], dict), "Nested object schema collapsed to non-dict"
        assert isinstance(res["translation_payload"]["segments"], list)
        assert isinstance(res["translation_payload"]["glossary"], list)
        ok, rep = validate_output(o_d, input_dir=in_d, strict=True)
        assert ok is True, f"Valid nested contract failed validation: {rep}"

        # Now inject type mismatch (array instead of object) -> validator MUST fail
        res["translation_payload"] = []
        with open(os.path.join(o_d, "result.json"), "w", encoding="utf-8") as f:
            json.dump(res, f)
        ok_bad, rep_bad = validate_output(o_d, input_dir=in_d, strict=True)
        assert ok_bad is False and any(e["code"] == "SCHEMA_TYPE_MISMATCH" for e in rep_bad["errors"]), f"Expected SCHEMA_TYPE_MISMATCH, got {rep_bad}"


# ----------------------------------------------------------------------
# Test 13: Compound SI Units (GB/s) & Triple-Dash Citations ([7]---[9])
# ----------------------------------------------------------------------
def test_compound_units_and_triple_dash_citations():
    inv = load_invariants()
    u = {
        "unit_id": "U01", "unit_type": "paragraph", "page_span": [1, 1],
        "source_text": "Memory bandwidth hits 12.5 GB/s while latency is 3.2 m/s^2 as shown in [7]---[9] and [1]–[3]."
    }
    fp = extract_unit_fingerprints(u, inv)
    units_found = {n["unit"]: n["raw"] for n in fp["numbers_units"]}
    assert "GB/s" in units_found and "12.5 GB/s" == units_found["GB/s"], f"Compound unit failed: {fp['numbers_units']}"
    assert "m/s^2" in units_found, f"Exponent compound unit failed: {fp['numbers_units']}"

    cite_sets = [c["expanded_ids"] for c in fp["citations"]]
    assert ["7", "8", "9"] in cite_sets, f"Triple-dash expansion failed: {cite_sets}"
    assert ["1", "2", "3"] in cite_sets, f"En-dash expansion failed: {cite_sets}"


# ----------------------------------------------------------------------
# Test 14: Abbreviation-Safe Sentence Splitting & [S1]..[Sn] Alignment
# ----------------------------------------------------------------------
def test_sentence_boundary_alignment_output():
    with tempfile.TemporaryDirectory() as tmp:
        in_d, w_d, o_d = [os.path.join(tmp, x) for x in ("in", "w", "o")]
        os.makedirs(in_d); os.makedirs(w_d); os.makedirs(o_d)

        src = "We use QEC (see Fig. 1 and e.g. Eq. (2)). The error rate is 0.43%. This confirms Theorem 1."
        tgt = "我们采用 QEC（见图 1 和例如式 (2)）。错误率为 0.43%。这证实了定理 1。"
        with open(os.path.join(w_d, "source_units.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "t14_sent", "units": [
                {"unit_id": "U01", "unit_type": "abstract", "section_path": "Abstract", "page_span": [1, 1],
                 "word_count": 20, "translate_policy": "translate", "in_scope": True, "source_text": src}
            ]}, f)
        with open(os.path.join(w_d, "merged_translations.json"), "w", encoding="utf-8") as f:
            json.dump({"U01": {"unit_id": "U01", "translated_text": tgt}}, f)

        with _quiet():
            verify_translations(w_d, os.path.join(w_d, "merged_translations.json"))
            res = render_deliverables(w_d, o_d, is_final=True)

        sa = res["bilingual_alignment"][0]["sentence_alignment"]
        assert sa is not None and sa["alignment_status"] == "1to1_aligned", f"Bad alignment status: {sa}"
        assert sa["source_count"] == 3 and sa["target_count"] == 3, f"Decimal/abbrev false split: {sa}"
        with open(os.path.join(o_d, "bilingual_parallel.md"), "r", encoding="utf-8") as f:
            bp_md = f.read()
        for tok in ("[S1]", "[S2]", "[S3]"):
            assert tok in bp_md, f"Missing sentence anchor {tok} in bilingual_parallel.md"


# ----------------------------------------------------------------------
# Test 15: Tolerant Glossary Schema Compilation & Formula Trailing Prose Split
# ----------------------------------------------------------------------
def test_tolerant_glossary_schema_and_eq_prose_split():
    with tempfile.TemporaryDirectory() as tmp:
        w_dir = os.path.join(tmp, "work")
        os.makedirs(w_dir)
        units = [
            {
                "unit_id": "U01", "unit_type": "paragraph", "page_span": [1, 1], "translate_policy": "translate",
                "source_text": "We compare Pauli frame performance against idle window length under conditional spread."
            }
        ]
        with open(os.path.join(w_dir, "source_units.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "t15", "units": units}, f)

        # 15a. Preposition-bounded noise phrase check
        with _quiet():
            cands = extract_candidates(w_dir, max_terms=20)
        cand_strs = [c["source_term"].lower() for c in cands["candidates"]]
        assert not any(c.startswith("against ") or c.endswith(" against") for c in cand_strs), f"Preposition leak: {cand_strs}"

        # 15b. Bare list + short alias keys (source/target/forbidden) without term_id
        g_bare = os.path.join(w_dir, "bare_glossary.json")
        with open(g_bare, "w", encoding="utf-8") as f:
            json.dump([
                {"source": "Pauli frame", "target": "泡利框架", "forbidden": ["保利框"]},
                {"term": "conditional spread", "zh": "条件散布度"},
                {"en": "IQM Garnet", "do_not_translate": True},
                {"source": "bad empty entry", "target": ""}
            ], f, ensure_ascii=False)
        with _quiet():
            comp = compile_glossary(w_dir, g_bare)
        assert comp["total_locked_terms"] == 3, f"Tolerant compile failed: {comp}"
        assert comp["terms"][0]["term_id"] == "T001" and comp["terms"][0]["canonical_target"] == "泡利框架"
        assert comp["terms"][0]["forbidden_targets"] == ["保利框"]
        assert comp["terms"][1]["term_id"] == "T002" and comp["terms"][1]["canonical_target"] == "条件散布度"
        assert comp["terms"][2]["canonical_target"] == "IQM Garnet"
        assert len(comp["validation_warnings"]) == 1, "Expected 1 recoverable warning for empty-target entry"

        # 15c. Split formula trailing unnumbered English narrative sentence
        mixed_blks = [
            {
                "type": "formula",
                "page": 2,
                "bbox": [310, 550, 550, 588],
                "block_id": "b_2_49",
                "page_span": [2, 2],
                "printed_page_span": ["2", "2"],
                "text": "B = Ex B(ρin_{B}). In the ex-\nperimental hardware measurement described later, the adversary’s chosen input is ρ^{in}"
            }
        ]
        split_res = split_trailing_prose_from_formulas(mixed_blks)
        assert len(split_res) == 2, f"Expected split into 2 blocks, got {split_res}"
        assert split_res[0]["type"] == "formula" and split_res[0]["text"] == "B = Ex B(ρin_{B})."
        assert split_res[1]["type"] == "paragraph" and "experimental hardware measurement" in split_res[1]["text"]


# ----------------------------------------------------------------------
# Runner Entry Point
# ----------------------------------------------------------------------
TESTS = [
    test_markdown_ingest_and_scope,
    test_cjk_word_count_and_soft_wrap,
    test_scanned_untranslatable_fallback,
    test_term_extract_and_compile_boundaries,
    test_entity_guard_fingerprints,
    test_verifier_8_faults_and_0_false_positives,
    test_merge_checkpoint_fenced_and_diff_excerpt,
    test_mode_c_existing_translation_audit_repair,
    test_dynamic_output_contract_mapping,
    test_validate_output_dishonest_status_rejection,
    test_recursive_subdir_and_latex_html_ingest,
    test_nested_json_schema_contract_and_type_check,
    test_compound_units_and_triple_dash_citations,
    test_sentence_boundary_alignment_output,
    test_tolerant_glossary_schema_and_eq_prose_split,
]


def main():
    print("=" * 68)
    print(f"RUNNING OFFLINE REGRESSION SUITE ({len(TESTS)} tests)")
    print("=" * 68)
    passed = 0
    failed = 0
    t_all = time.time()

    for idx, fn in enumerate(TESTS, 1):
        t0 = time.time()
        try:
            fn()
            dt = time.time() - t0
            print(f"[{idx:02d}/{len(TESTS)}] PASS | {fn.__name__:<48s} ({dt:.3f}s)")
            passed += 1
        except Exception as e:
            dt = time.time() - t0
            print(f"[{idx:02d}/{len(TESTS)}] FAIL | {fn.__name__:<48s} ({dt:.3f}s) -> {str(e)}")
            failed += 1

    total_dt = time.time() - t_all
    print("=" * 68)
    print(f"SUMMARY: {passed} PASSED, {failed} FAILED (Total {total_dt:.3f}s)")
    print("=" * 68)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
