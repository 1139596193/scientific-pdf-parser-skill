#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
self_test.py - Zero-Dependency Offline Regression Suite (44 Independent Tests)
including YAML Frontmatter Validation, AST CLI Zero-Drift Check, Pure-Stdlib OOXML
Track Changes Verification & 4-Tier PNG Preview Fallback Validation.
Part of the Academic Language Polishing Skill (task_02_polish).
Top-level imports are strictly restricted to Python standard library.
"""

import ast
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import warnings
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(SCRIPT_DIR)
RESOURCES_DIR = os.path.join(SKILL_ROOT, "resources")
PIPELINE_PY = os.path.join(SCRIPT_DIR, "polish_pipeline.py")
VALIDATE_PY = os.path.join(SCRIPT_DIR, "validate_output.py")
SKILL_MD = os.path.join(SKILL_ROOT, "SKILL.md")
README_MD = os.path.join(SKILL_ROOT, "README.md")

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import case_ingestor
import diff_engine
import docx_redline
import epistemic_entity_guard
import glossary_abbr_guard
import polish_verifier
import render_deliverables
import style_linter
import validate_output


def _w(path, txt):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)


def _make_docx(path, paragraphs, tables=None):
    p_xml = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        p_xml.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    t_xml = []
    for rows in (tables or []):
        trs = []
        for row in rows:
            tcs = "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in row)
            trs.append(f"<w:tr>{tcs}</w:tr>")
        t_xml.append(f"<w:tbl>{''.join(trs)}</w:tbl>")
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(p_xml)}{''.join(t_xml)}</w:body></w:document>"
    )
    ct = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("word/document.xml", doc_xml)


def _extract_pipeline_cli_contract():
    """Parse polish_pipeline.py AST to extract valid subcommands and flags."""
    with open(PIPELINE_PY, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=PIPELINE_PY)
    var_to_cmd = {}
    cmd_flags = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
                if node.value.func.attr == "add_parser" and node.value.args and isinstance(node.value.args[0], ast.Constant):
                    cmd_name = node.value.args[0].value
                    var_to_cmd[node.targets[0].id] = cmd_name
                    cmd_flags[cmd_name] = set()
    grp_parent = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "add_mutually_exclusive_group":
                if isinstance(node.value.func.value, ast.Name) and node.value.func.value.id in var_to_cmd:
                    grp_parent[node.targets[0].id] = var_to_cmd[node.value.func.value.id]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            if isinstance(node.func.value, ast.Name):
                vid = node.func.value.id
                cmd_name = var_to_cmd.get(vid) or grp_parent.get(vid)
                if cmd_name:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                            cmd_flags[cmd_name].add(arg.value)
    return cmd_flags


def _extract_validate_cli_flags():
    with open(VALIDATE_PY, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=VALIDATE_PY)
    flags = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                    flags.add(arg.value)
    return flags


def run_all_tests():
    results = []

    def check(tid, name, fn):
        try:
            detail = fn()
            results.append({"id": tid, "name": name, "status": "PASS", "detail": detail})
        except Exception as e:
            results.append({"id": tid, "name": name, "status": "FAIL", "error": f"{type(e).__name__}: {e}"})

    # --- Knowledge bases ---
    def t01():
        d = json.load(open(os.path.join(RESOURCES_DIR, "epistemic_hedging_ladder.json"), encoding="utf-8"))
        for k in ("tier_5_absolute_causal", "tier_4_strong_empirical", "tier_3_moderate_associative", "tier_2_cautious_tentative", "tier_1_negation_scope"):
            assert k in d["epistemic_ladder"]
        assert "comparison_direction" in d
        return "5 tiers ok"

    def t02():
        d = json.load(open(os.path.join(RESOURCES_DIR, "style_profiles.json"), encoding="utf-8"))
        assert len(d["profiles"]) == 6
        assert len(d["us_uk_spelling_pairs"]) >= 40
        assert len(d["colloquial_chinglish_lint"]["en"]) >= 30
        assert len(d["colloquial_chinglish_lint"]["zh"]) >= 15
        return f"profiles={len(d['profiles'])}, pairs={len(d['us_uk_spelling_pairs'])}"

    def t03():
        d = json.load(open(os.path.join(RESOURCES_DIR, "edit_taxonomy.json"), encoding="utf-8"))
        assert len(d["taxonomy"]) == 13
        assert os.path.isfile(os.path.join(RESOURCES_DIR, "glossary_template.md"))
        return "13 tags ok"

    # --- Multi-format ingestion & lossless reconstruction ---
    def t04():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            _w(os.path.join(ind, "note.txt"), "Introduction\n\nFig. 1 shows Smith et al. (2022) got 91.2% accuracy. Eq. (2) holds.")
            man, blks, sents = case_ingestor.ingest_case(ind, wkd)
            assert man["source_format"] == "txt"
            assert len(sents) == 3  # heading + 2 sentences
            assert case_ingestor.reconstruct_document(blks, None) == open(os.path.join(wkd, "normalized_source.txt"), encoding="utf-8").read()
            return f"sents={len(sents)}"

    def t05():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            md = "# Abstract\n\nWe study $E=mc^2$.\n\n```python\nx = 1\n```\n\n## References\n\n1. Doe (2020)."
            _w(os.path.join(ind, "paper.md"), md)
            man, blks, sents = case_ingestor.ingest_case(ind, wkd)
            assert man["source_format"] == "markdown"
            assert case_ingestor.reconstruct_document(blks, None) == open(os.path.join(wkd, "normalized_source.txt"), encoding="utf-8").read()
            assert any(not s["in_scope"] for s in sents)  # References excluded
            return f"sents={len(sents)}"

    def t06():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            tex = "\\documentclass{article}\n\\begin{document}\n\\section{Intro}\nWe test \\cite{a2024} at $T=300$ K.\n\\begin{equation}\ny = x^2\n\\end{equation}\n\\end{document}\n"
            _w(os.path.join(ind, "paper.tex"), tex)
            man, blks, sents = case_ingestor.ingest_case(ind, wkd)
            assert man["source_format"] == "latex"
            assert case_ingestor.reconstruct_document(blks, None) == open(os.path.join(wkd, "normalized_source.txt"), encoding="utf-8").read()
            return f"sents={len(sents)}"

    def t07():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            _w(os.path.join(ind, "paper.html"), "<html><body><h1>Results</h1><p>Accuracy improved to 95.0%.</p></body></html>")
            man, _, sents = case_ingestor.ingest_case(ind, wkd)
            assert man["source_format"] == "html" and len(sents) == 2
            return "html ok"

    def t08():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            os.makedirs(ind)
            _make_docx(os.path.join(ind, "ms.docx"), [("Heading1", "1. Methods"), ("Normal", "Samples were heated to 80 C.")], tables=[[["A", "B"], ["1", "2"]]])
            man, blks, sents = case_ingestor.ingest_case(ind, wkd)
            assert man["source_format"] == "docx" and any(b["block_type"] == "table" for b in blks)
            return "docx ok"

    def t09():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            case_obj = {
                "case_id": "nested_01",
                "source_text": "# Abstract\n\nWe do an experiment on 10 samples.",
                "output_contract": {"case_id": "", "meta": {"status": None, "polished_text": ""}, "edit_log": []},
            }
            _w(os.path.join(ind, "case.json"), json.dumps(case_obj))
            man, _, _ = case_ingestor.ingest_case(ind, wkd)
            assert man["case_id"] == "nested_01" and "output_contract" in man["case_data"]
            return "nested contract ok"

    # --- Adaptive budget batching ---
    def t10():
        sents = [{"sentence_id": f"S{i:04d}", "section_role": "body", "in_scope": True, "language": "en", "source_text": f"Sentence {i} reports 12.5 mg yield."} for i in range(1, 901)]
        batches, adv = case_ingestor.build_adaptive_batches(sents, target_max_batches=16, max_batch_tokens=2800, min_batch_size=6, max_batch_size=36)
        assert len(batches) <= 24, f"Too many batches: {len(batches)}"
        total_packed = sum(len(b) for b in batches)
        assert total_packed == 900
        # Verify compact packet keys
        first_item = batches[0][0]
        for k in ("sentence_id", "section_role", "source_text", "protected_invariants", "epistemic_tier", "locked_terms", "pre_lint_hints"):
            assert k in first_item
        return f"900 sents -> {len(batches)} batches"

    def t11():
        long_sent = "Word " * 400
        sents = [{"sentence_id": f"S{i:04d}", "section_role": "body", "in_scope": True, "language": "en", "source_text": long_sent} for i in range(1, 160)]
        batches, adv = case_ingestor.build_adaptive_batches(sents, target_max_batches=16, max_batch_tokens=2800)
        assert len(batches) > 24
        assert any(a["code"] == "LONG_DOCUMENT_BUDGET_ADVISORY" for a in adv)
        assert sum(len(b) for b in batches) == 159
        return f"advisory triggered at {len(batches)} batches"

    # --- Epistemic & double-negation whitelist ---
    def t12():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("Our data may partly explain this effect.", "Our data conclusively proves this effect.")
        assert any(x["code"] == "CLAIM_STRENGTH_ESCALATION" for x in iss)
        return "escalation caught"

    def t13():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("Our data conclusively demonstrates this effect.", "Our data may possibly suggest this effect.")
        assert any(x["code"] == "CLAIM_STRENGTH_WEAKENING" for x in iss)
        return "weakening caught"

    def t14():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("The difference was not significant.", "The difference was significant.")
        assert any(x["code"] == "POLARITY_FLIP" for x in iss)
        return "polarity flip caught"

    def t15():
        iss1 = epistemic_entity_guard.compare_epistemic_and_invariants("This phenomenon is not uncommon in clinical trials.", "This phenomenon is common in clinical trials.")
        iss2 = epistemic_entity_guard.compare_epistemic_and_invariants("该误差不容忽视。", "该误差必须重视。")
        assert not any(x["code"] == "POLARITY_FLIP" for x in iss1), iss1
        assert not any(x["code"] == "POLARITY_FLIP" for x in iss2), iss2
        return "double-neg whitelist ok"

    def t16():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("Values were 10 mg and 20 mg, respectively.", "Values were 10 mg and 20 mg.")
        assert any(x["code"] == "SCOPE_BOUND_DROPPED" for x in iss)
        return "scope bound caught"

    def t17():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("Group A showed higher yield than Group B.", "Group A showed lower yield than Group B.")
        assert any(x["code"] == "DIRECTION_FLIP" for x in iss)
        return "direction flip caught"

    # --- Invariants ---
    def t18():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("We observed p = 0.042 at 25 °C.", "We observed p < 0.05 at 25 °C.")
        assert any(x["code"] == "NUMBER_UNIT_MISSING" for x in iss)
        return "stat/number caught"

    def t19():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("Prior work \\cite{li2024} and [3, 4] showed gains.", "Prior work showed gains.")
        assert any(x["code"] == "CITATION_MISSING" for x in iss)
        return "citation caught"

    def t20():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("See Fig. 2b and Eq. (3).", "See the figure and equation.")
        assert any(x["code"] == "CROSS_REF_MISSING" for x in iss)
        return "cross-ref caught"

    def t21():
        iss = epistemic_entity_guard.compare_epistemic_and_invariants("Energy is $E = mc^2$.", "Energy is $E = mc^2.")
        codes = {x["code"] for x in iss}
        assert "MATH_DELIMITER_BROKEN" in codes and "MATH_LATEX_MISSING" in codes
        return "math tamper caught"

    # --- Glossary & Spelling ---
    def t22():
        with tempfile.TemporaryDirectory() as tmp:
            p_csv = os.path.join(tmp, "g.csv")
            p_tsv = os.path.join(tmp, "g.tsv")
            p_js = os.path.join(tmp, "g.json")
            p_md = os.path.join(tmp, "g.md")
            _w(p_csv, "source_term,preferred_term,forbidden_terms,do_not_edit,abbr,notes\na1,p1,f1,true,A1,n\n")
            _w(p_tsv, "source_term\tpreferred_term\tforbidden_terms\tdo_not_edit\tabbr\tnotes\na2\tp2\tf2\tfalse\tA2\tn\n")
            _w(p_js, json.dumps({"terms": [{"source_term": "a3", "preferred_term": "p3"}]}))
            _w(p_md, "| source_term | preferred_term |\n|---|---|\n| a4 | p4 |\n")
            gb = glossary_abbr_guard.load_and_audit_glossary([p_csv, p_tsv, p_js, p_md])
            assert len(gb["locked_terms"]) == 4 and len(gb["glossary_conflicts"]) == 0
            return "4 formats loaded"

    def t23():
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "g.csv")
            _w(p, "source_term,preferred_term,forbidden_terms,abbr\ndp,差分隐私,,DP\ndp,差异隐私,,DP\nfoo,bar,bar,FB\nx1,TermOne,,XX\nx2,TermTwo,,XX\n")
            gb = glossary_abbr_guard.load_and_audit_glossary([p])
            ccodes = {c["code"] for c in gb["glossary_conflicts"]}
            assert {"GLOSSARY_CONFLICT_MULTI_TARGET", "GLOSSARY_CONFLICT_SELF_CONTRADICTION", "GLOSSARY_CONFLICT_ABBR"} <= ccodes
            return f"conflicts={sorted(ccodes)}"

    def t24():
        gb = {"locked_terms": [{"source_term": "quantum channel", "preferred_term": "quantum channel", "forbidden_terms": ["quantum pipe"], "do_not_edit": True, "abbr": ""}]}
        iss = glossary_abbr_guard.audit_term_consistency([{"sentence_id": "S001", "source_text": "The quantum channel failed.", "polished_text": "The quantum pipe failed."}], gb)
        codes = {i["code"] for i in iss}
        assert "FORBIDDEN_TERM_USED" in codes and "LOCKED_TERM_MISMATCH" in codes
        return "forbidden+locked caught"

    def t25():
        recs = [{"sentence_id": "S001", "source_text": "We analyze and optimise.", "polished_text": "We analyze and optimise."}]
        iss = glossary_abbr_guard.audit_term_consistency(recs, {"locked_terms": []})
        assert any(i["code"] == "SPELLING_VARIANT_MIXED" for i in iss)
        return "US/UK mix caught"

    def t26():
        recs = [{"sentence_id": "S001", "source_text": "See Fig. 1 and Figure 2.", "polished_text": "In Fig. 1, see Figure 2."}]
        iss = glossary_abbr_guard.audit_term_consistency(recs, {"locked_terms": []})
        assert any(i["code"] == "TERM_VARIANT_DRIFT" for i in iss)
        return "variant drift caught"

    # --- Abbreviation lifecycle ---
    def t27():
        recs = [
            {"sentence_id": "S001", "section_role": "abstract", "source_text": "We use deep neural network (DNN). DNN works.", "polished_text": "We use deep neural network (DNN). DNN works."},
            {"sentence_id": "S002", "section_role": "body", "source_text": "We use DNN without body definition.", "polished_text": "We use DNN without body definition."},
        ]
        iss = glossary_abbr_guard.audit_abbreviations(recs)
        assert any(i["code"] == "ABBR_UNDEFINED" and "body" in i["detail"] for i in iss)
        return "separate abstract/body scope ok"

    def t28():
        recs = [
            {"sentence_id": "S001", "section_role": "body", "source_text": "Convolutional neural network (CNN) helps.", "polished_text": "Convolutional neural network (CNN) helps."},
            {"sentence_id": "S002", "section_role": "body", "source_text": "Convolutional neural network (CNN) scales.", "polished_text": "Convolutional neural network (CNN) scales."},
        ]
        iss = glossary_abbr_guard.audit_abbreviations(recs)
        assert any(i["code"] == "ABBR_REDEFINED" for i in iss)
        return "redefined caught"

    def t29():
        recs = [
            {"sentence_id": "S001", "section_role": "body", "source_text": "We measured DNA, RNA, NMR, XRD, SEM, TEM, ANOVA, CPU, JSON, and $XYZ$ at IEEE.", "polished_text": "We measured DNA, RNA, NMR, XRD, SEM, TEM, ANOVA, CPU, JSON, and $XYZ$ at IEEE."},
        ]
        iss = glossary_abbr_guard.audit_abbreviations(recs)
        assert not any(i["code"] == "ABBR_UNDEFINED" for i in iss), iss
        return "scientific whitelist + math skip ok"

    # --- Diff engine & ledger ---
    def t30():
        d = diff_engine.compute_sentence_diff("We did an experiment.", "We conducted an experiment.")
        assert d["is_changed"] and 0 < d["edit_ratio"] < 1 and "[-did-]{+conducted+}" in d["inline_diff"]
        assert any(op["op"] == "replace" for op in d["diff_ops"])
        return f"ratio={d['edit_ratio']}"

    def t31():
        _, iss1 = diff_engine.audit_edit_record("S001", "A.", "B.", [], "")
        _, iss2 = diff_engine.audit_edit_record("S002", "A.", "A.", ["grammar"], "Fixed")
        assert any(i["code"] == "UNLOGGED_CHANGE" for i in iss1)
        assert any(i["code"] == "PHANTOM_EDIT_LOG" for i in iss2)
        return "unlogged+phantom caught"

    def t32():
        _, iss = diff_engine.audit_edit_record("S001", "We trained the model.", "Optimization converged rapidly without oscillations.", ["academic_register"], "Rewrote", edit_intensity="minimal")
        assert any(i["code"] == "OVER_EDIT_RISK" for i in iss)
        return "over-edit caught"

    # --- Style linter ---
    def t33():
        iss = style_linter.lint_sentence("Because it rained, so we didn't get rid of a lot of noise.", lang="en")
        codes = {i["code"] for i in iss}
        assert "EN_CHINGLISH_BECAUSE_SO" in codes and "EN_COLLOQ_CONTRACTION" in codes
        return f"lint_codes={len(codes)}"

    def t34():
        iss = style_linter.lint_sentence("This sentence has Chinese comma，wrong.", lang="en")
        assert any(i["code"] == "PUNCT_WIDTH_MISMATCH" for i in iss)
        return "punct mismatch caught"

    def t35():
        iss = style_linter.lint_sentence("Here is the polished version: We tested the model.", lang="en")
        assert any(i["code"] == "META_LEAKAGE" and i["severity"] == "HIGH" for i in iss)
        return "meta leakage caught"

    # --- Mode routing & E2E pipeline ---
    def t36():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            case_obj = {
                "case_id": "e2e_polish_01",
                "output_contract": {"case_id": "", "status": "", "polished_text": "", "edit_log": []},
            }
            _w(os.path.join(ind, "case.json"), json.dumps(case_obj))
            _w(os.path.join(ind, "task.md"), "Polish this manuscript for IEEE submission.")
            _w(os.path.join(ind, "draft.md"), "# Introduction\n\nWe did an experiment to test accuracy.\n")
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "bootstrap", "--input", ind, "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            assert os.path.isfile(os.path.join(outd, "result.json"))
            insp = subprocess.run([sys.executable, "-B", PIPELINE_PY, "inspect", "--work-dir", wkd, "--view", "batch", "--batch-id", "batch_01"], check=True, stdout=subprocess.PIPE, encoding="utf-8")
            assert "S002" in insp.stdout
            # Patch bad first, then hotfix via --replaces-id
            p1 = os.path.join(tmp, "p1.json")
            _w(p1, json.dumps([
                {"sentence_id": "S001", "polished_text": "Introduction", "edit_types": ["minimal_preserve"], "reason": "Heading kept."},
                {"sentence_id": "S002", "polished_text": "We conducted an experiment to evaluate accuracy.", "edit_types": [], "reason": ""},
            ]))
            pr = subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--file", p1], check=True, stdout=subprocess.PIPE, encoding="utf-8")
            assert "UNLOGGED_CHANGE" in pr.stdout
            p2 = os.path.join(tmp, "p2.json")
            _w(p2, json.dumps([
                {"sentence_id": "S002", "polished_text": "We conducted an experiment to evaluate accuracy.", "edit_types": ["collocation", "academic_register"], "reason": "Replaced 'did an experiment' and 'test' with formal academic verbs."},
            ]))
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--file", p2, "--replaces-id", "S002"], check=True, stdout=subprocess.PIPE)
            fin = subprocess.run([sys.executable, "-B", PIPELINE_PY, "finish", "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE, encoding="utf-8")
            fin_js = json.loads(fin.stdout)
            assert fin_js["status"] == "ok" and fin_js["validation_passed"] is True
            res_js = json.load(open(os.path.join(outd, "result.json"), encoding="utf-8"))
            assert res_js["output_contract"]["status"] == "ok"
            return f"tier={fin_js['preview_render_tier']}"

    def t37():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            _w(os.path.join(ind, "task.md"), "请执行中英双语对照润色（bilingual polish）。")
            _w(os.path.join(ind, "draft.md"), "本研究提出了一种新方法。")
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "bootstrap", "--input", ind, "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            patch_json = json.dumps([{"sentence_id": "S001", "polished_text": "本研究提出了一种新颖方法。", "bilingual_counterpart": "This study proposes a novel method.", "edit_types": ["bilingual_alignment", "academic_register"], "reason": "润色表达并对齐英文对照。"}])
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--stdin"], input=patch_json, text=True, encoding="utf-8", check=True, stdout=subprocess.PIPE)
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "finish", "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            res = json.load(open(os.path.join(outd, "result.json"), encoding="utf-8"))
            assert res["execution_mode"] == "bilingual_polish"
            assert res["edit_log"][0]["bilingual_counterpart"] == "This study proposes a novel method."
            return "bilingual_polish ok"

    def t38():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            _w(os.path.join(ind, "task.md"), "仅审校诊断，不要改写正文（audit only）。")
            _w(os.path.join(ind, "draft.md"), "We did a lot of tests.")
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "bootstrap", "--input", ind, "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            p_json = json.dumps([{"sentence_id": "S001", "polished_text": "We conducted numerous tests.", "edit_types": ["collocation"], "reason": "Diagnostic suggestion."}])
            pr = subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--stdin"], input=p_json, text=True, encoding="utf-8", check=True, stdout=subprocess.PIPE)
            assert "MODE_EDIT_REJECTED" in pr.stdout
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "finish", "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            res = json.load(open(os.path.join(outd, "result.json"), encoding="utf-8"))
            assert res["edit_log"][0]["polished_text"] == "We did a lot of tests."
            assert res["edit_log"][0]["suggested_text"] == "We conducted numerous tests."
            return "audit_only protected source"

    def t39():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            _w(os.path.join(ind, "task.md"), "What is the sample size in this manuscript? Please explain.")
            _w(os.path.join(ind, "draft.md"), "The sample size is 50 patients.")
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "bootstrap", "--input", ind, "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            p_json = json.dumps([{"sentence_id": "S001", "polished_text": "Rewritten unauthorized.", "edit_types": ["conciseness"], "reason": "Edit."}])
            pr = subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--stdin"], input=p_json, text=True, encoding="utf-8", check=True, stdout=subprocess.PIPE)
            assert "MODE_EDIT_REJECTED" in pr.stdout
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "finish", "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            res = json.load(open(os.path.join(outd, "result.json"), encoding="utf-8"))
            assert res["execution_mode"] == "qa_guard" and res["edit_log"][0]["polished_text"] == "The sample size is 50 patients."
            return "qa_guard protected source"

    # --- Anomaly boundaries ---
    def t40():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            _w(os.path.join(ind, "task.md"), "Polish the text.")
            _w(os.path.join(ind, "draft.md"), "本方法提升 [TBD]，テスト結果 Ã© \ufffd 显示。")
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "bootstrap", "--input", ind, "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            p_json = json.dumps([{"sentence_id": "S001", "polished_text": "本方法提升 [TBD]，テスト結果 Ã© \ufffd 显示。", "edit_types": ["needs_human_review"], "reason": "Preserved anomaly placeholders.", "needs_human_review": True, "human_review_note": "Garbled + JP + TBD."}])
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--stdin"], input=p_json, text=True, encoding="utf-8", check=True, stdout=subprocess.PIPE)
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "finish", "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            res = json.load(open(os.path.join(outd, "result.json"), encoding="utf-8"))
            assert res["status"] == "needs_review"
            ucodes = {u["code"] for u in res["uncertainty"]}
            assert {"ENCODING_GARBLED", "MIXED_CJK_JP", "MISSING_MATERIAL_PLACEHOLDER"} <= ucodes
            return f"status={res['status']}"

    # --- Pure stdlib OOXML Track Changes & round-trip ---
    def t41():
        with tempfile.TemporaryDirectory() as tmp:
            clean_p = os.path.join(tmp, "clean.docx")
            red_p = os.path.join(tmp, "red.docx")
            blks = case_ingestor.parse_markdown_or_text("# Intro\n\nWe did an experiment & checked x < 5.\n")
            slots = case_ingestor.split_academic_sentences_lossless(blks[1]["body_text"])
            blks[1]["sentence_slots"] = [{"sentence_id": "S001", **slots[0]}]
            diff_d = diff_engine.compute_sentence_diff("We did an experiment & checked x < 5.", "We conducted an experiment & verified x < 5.")
            recs = [{"sentence_id": "S001", "source_text": "We did an experiment & checked x < 5.", "polished_text": "We conducted an experiment & verified x < 5.", "edit_types": ["collocation"], "reason": "Formal verbs.", "diff": diff_d}]
            docx_redline.generate_clean_docx(clean_p, "Test & Title", blks, {"S001": "We conducted an experiment & verified x < 5."})
            docx_redline.generate_redline_docx(red_p, "Test & Title", blks, recs, audit_summary={"total_sentences": 1, "edited_sentences": 1, "unchanged_sentences": 0, "overall_audit_score": 100, "high_count": 0})
            vc = docx_redline.verify_docx_package(clean_p, expect_track_changes=False)
            vr = docx_redline.verify_docx_package(red_p, expect_track_changes=True)
            assert vc["valid"] and vr["valid"] and vr["del_count"] > 0 and vr["ins_count"] > 0
            # Round-trip parse via case_ingestor.parse_docx
            parsed_blks = case_ingestor.parse_docx(clean_p)
            assert any("conducted an experiment" in b["body_text"] for b in parsed_blks)
            return f"del={vr['del_count']}, ins={vr['ins_count']}"

    # --- 4-tier PNG preview including forced Tier 4 stdlib ---
    def t42():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            _w(os.path.join(ind, "draft.md"), "We did a test.")
            man, blks, sents = case_ingestor.ingest_case(ind, wkd)
            rep = polish_verifier.verify_polish_run(man, sents, [{"sentence_id": "S001", "polished_text": "We conducted a test.", "edit_types": ["collocation"], "reason": "Formal verb."}])
            res_t4 = render_deliverables.render_all_deliverables(man, blks, rep, outd, force_preview_tier="tier4")
            assert res_t4["preview_render_tier"] == "tier4_stdlib_png"
            png_p = os.path.join(outd, "redline_preview_page1.png")
            pw, ph = validate_output._read_png_dimensions(png_p)
            assert pw >= 200 and ph >= 200
            val = validate_output.validate_deliverables(outd)
            assert val["passed"], val["errors"]
            return f"tier4={pw}x{ph}"

    # --- YAML & AST CLI zero-drift ---
    def t43():
        import yaml
        assert os.path.getsize(SKILL_MD) <= 12 * 1024, f"SKILL.md too large: {os.path.getsize(SKILL_MD)}"
        raw_skill = open(SKILL_MD, "r", encoding="utf-8").read()
        m = re.match(r"^---\n([\s\S]*?)\n---\n", raw_skill)
        assert m, "Missing YAML frontmatter in SKILL.md"
        fm = yaml.safe_load(m.group(1))
        assert fm.get("name") == "academic-language-polish" and isinstance(fm.get("description"), str) and len(fm["description"]) > 40

        cmd_flags = _extract_pipeline_cli_contract()
        assert set(cmd_flags.keys()) == {"bootstrap", "inspect", "patch", "finish"}
        val_flags = _extract_validate_cli_flags()

        for doc_p in (SKILL_MD, README_MD):
            doc_txt = open(doc_p, "r", encoding="utf-8").read()
            # Check every polish_pipeline.py invocation line
            joined = doc_txt.replace("\\\n", " ")
            for m_call in re.finditer(r"polish_pipeline\.py[^\n`]*", joined):
                seg = m_call.group(0)
                tokens = seg.split()
                if len(tokens) >= 2 and tokens[1] in cmd_flags:
                    sub = tokens[1]
                    used_flags = [t for t in tokens[2:] if t.startswith("--")]
                    for uf in used_flags:
                        assert uf in cmd_flags[sub], f"CLI drift in {os.path.basename(doc_p)}: '{uf}' not in {sub} {cmd_flags[sub]}"
            for m_val in re.finditer(r"validate_output\.py[^\n`]*", joined):
                used_flags = [t for t in m_val.group(0).split()[1:] if t.startswith("--")]
                for uf in used_flags:
                    assert uf in val_flags, f"validate_output CLI drift in {os.path.basename(doc_p)}: '{uf}'"
        return f"SKILL.md={os.path.getsize(SKILL_MD)}B, zero AST drift"

    # --- Harbor packaging & static compliance ---
    def t44():
        allowed_stdlib = set(sys.stdlib_module_names) | {
            "case_ingestor", "diff_engine", "docx_redline", "epistemic_entity_guard",
            "glossary_abbr_guard", "polish_verifier", "render_deliverables", "style_linter", "validate_output",
        }
        file_cnt = 0
        total_bytes = 0
        max_bytes = 0
        for root, dirs, files in os.walk(SKILL_ROOT):
            assert "__pycache__" not in dirs, f"__pycache__ found in {root}"
            for d in dirs:
                assert not os.path.islink(os.path.join(root, d))
            for fn in sorted(files):
                p = os.path.join(root, fn)
                assert not os.path.islink(p), f"Symlink found: {p}"
                raw = open(p, "rb").read()
                assert not raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {p}"
                assert b"\r\n" not in raw, f"CRLF found in {p}"
                txt = raw.decode("utf-8")
                bad_win = "C:" + "\\Users"
                bad_mac = "/" + "Users/"
                assert bad_win not in txt and bad_mac not in txt, f"Hardcoded local abs path in {p}"
                file_cnt += 1
                sz = len(raw)
                total_bytes += sz
                max_bytes = max(max_bytes, sz)
                assert sz <= 500 * 1024, f"File > 500KB: {p}"
                if fn.endswith(".py"):
                    tree = ast.parse(txt)
                    for node in tree.body:
                        if isinstance(node, ast.Import):
                            for a in node.names:
                                top = a.name.split(".")[0]
                                assert top in allowed_stdlib, f"Non-stdlib top import '{top}' in {p}"
                        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                            top = node.module.split(".")[0]
                            assert top in allowed_stdlib, f"Non-stdlib top import '{top}' in {p}"
        assert file_cnt <= 50, f"Too many files: {file_cnt}"
        assert total_bytes <= 2 * 1024 * 1024, f"Total size > 2MB: {total_bytes}"
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import pymupdf  # noqa
            assert len(w) == 0, f"pymupdf warning: {[str(x.message) for x in w]}"
        return f"files={file_cnt}, max={max_bytes}B, total={total_bytes}B"

    # --- Stage 4 targeted regressions (T45-T52) ---
    def t45():
        cases = [
            ("We don't ignore hardware drift.", "We do not ignore hardware drift."),
            ("This model can't converge here.", "This model cannot converge here."),
            ("The baseline isn't optimal.", "The baseline is not optimal."),
            ("They don\u2019t neglect noise.", "They do not neglect noise."),
        ]
        for s, p in cases:
            iss = epistemic_entity_guard.compare_epistemic_and_invariants(s, p, lang="en")
            assert not any(x["code"] == "POLARITY_FLIP" for x in iss), f"False POLARITY_FLIP on {s} -> {p}: {iss}"
        return "4 contraction expansions zero POLARITY_FLIP"

    def t46():
        recs = [
            {"sentence_id": "S001", "section_role": "body", "source_text": "We use a convolutional neural network (CNN) for feature extraction.", "polished_text": "We employ a convolutional neural network (CNN) for feature extraction."},
            {"sentence_id": "S002", "section_role": "body", "source_text": "The CNN achieves 94% accuracy.", "polished_text": "The CNN achieves 94% accuracy."},
        ]
        iss = glossary_abbr_guard.audit_abbreviations(recs)
        assert not any(x["code"] == "ABBR_REDEFINED" for x in iss), f"False ABBR_REDEFINED: {iss}"
        return "no dual-regex ABBR_REDEFINED"

    def t47():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            case_obj = {
                "case_id": "contract_fill_test",
                "output_contract": {
                    "case_id": "",
                    "status": "",
                    "deliverables": {},
                    "audit_summary": {},
                    "human_confirmation_items": [],
                    "bilingual_abstract": "",
                },
            }
            _w(os.path.join(ind, "case.json"), json.dumps(case_obj))
            _w(os.path.join(ind, "task.md"), "请执行中英双语对照润色（bilingual_polish）。")
            _w(os.path.join(ind, "draft.md"), "# 摘要\n\n本研究测量了 [TBD] 处的量子效率。\n")
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "bootstrap", "--input", ind, "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            p_json = json.dumps([
                {"sentence_id": "S001", "polished_text": "摘要", "bilingual_counterpart": "Abstract", "edit_types": ["minimal_preserve"], "reason": "标题保留。"},
                {"sentence_id": "S002", "polished_text": "本研究测得 [TBD] 处的量子效率。", "bilingual_counterpart": "This study measured quantum efficiency at [TBD].", "edit_types": ["academic_register", "needs_human_review"], "reason": "精简表达并保留占位符。", "needs_human_review": True, "human_review_note": "TBD pending"},
            ])
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--stdin"], input=p_json, text=True, encoding="utf-8", check=True, stdout=subprocess.PIPE)
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "finish", "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            res = json.load(open(os.path.join(outd, "result.json"), encoding="utf-8"))
            oc = res["output_contract"]
            assert oc["case_id"] == "contract_fill_test"
            assert isinstance(oc["deliverables"], dict) and len(oc["deliverables"]) >= 5
            assert isinstance(oc["audit_summary"], dict) and "overall_audit_score" in oc["audit_summary"]
            assert isinstance(oc["human_confirmation_items"], list) and len(oc["human_confirmation_items"]) >= 1
            assert isinstance(oc["bilingual_abstract"], str) and "ZH:" in oc["bilingual_abstract"] and "EN:" in oc["bilingual_abstract"]
            return "nested empty contract 100% populated"

    def t48():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            _w(os.path.join(ind, "task.md"), "请执行中英双语对照润色（bilingual_polish）。")
            _w(os.path.join(ind, "draft.md"), "# 摘要\n\n测试正文。\n\n# 1. 引言\n\n更多正文内容。\n")
            man, blks, sents = case_ingestor.ingest_case(ind, wkd)
            # Ensure "# 1. 引言" was NOT split into "1." and "引言"
            h_sents = [s for s in sents if "引言" in s["source_text"] or s["source_text"] == "1."]
            assert len(h_sents) == 1 and h_sents[0]["source_text"] == "1. 引言", h_sents
            # Ensure short headings do not trigger BILINGUAL_LENGTH_ANOMALY
            pol = [
                {"sentence_id": s["sentence_id"], "polished_text": "Abstract" if "摘要" in s["source_text"] else ("1. Introduction" if "引言" in s["source_text"] else s["source_text"]), "edit_types": ["bilingual_alignment"], "reason": "Aligned."}
                for s in sents
            ]
            rep = polish_verifier.verify_polish_run(man, sents, pol)
            assert not any(i["code"] == "BILINGUAL_LENGTH_ANOMALY" for i in rep["all_issues"]), rep["all_issues"]
            return "heading unsplit & zero short-heading BILINGUAL_LENGTH_ANOMALY"

    def t49():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd = os.path.join(tmp, "in"), os.path.join(tmp, "wk")
            _w(os.path.join(ind, "draft.md"), "We evaluate error mitigation.")
            _w(os.path.join(ind, "glossary.csv"), "source_term,preferred_term\nerror mitigation,quantum error mitigation\nerror mitigation,fault mitigation\n")
            man, _, sents = case_ingestor.ingest_case(ind, wkd)
            rep = polish_verifier.verify_polish_run(man, sents, [{"sentence_id": "S001", "polished_text": "We evaluate error mitigation.", "edit_types": ["minimal_preserve"], "reason": ""}])
            g_iss = [i for i in rep["all_issues"] if i["code"] == "GLOSSARY_CONFLICT_MULTI_TARGET"]
            assert g_iss and g_iss[0]["repairable"] is False
            assert rep["summary"]["advisory_high_count"] >= 1 and rep["summary"]["actionable_high_count"] == 0
            return "non-repairable glossary conflict isolated"

    def t50():
        txt = "样本中的 テスト結果 Ã© 显示良好，naïve café résumé."
        toks = diff_engine.tokenize_mixed(txt)
        assert "".join(toks) == txt, f"Tokenization loss: {toks}"
        d = diff_engine.compute_sentence_diff(txt, "样本中的 テスト結果 Ã© 表现良好，naïve café résumé.")
        assert "Ã©" in d["inline_diff"] and "résumé" in d["inline_diff"], d["inline_diff"]
        return "lossless Unicode diff ok"

    def t51():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            _w(os.path.join(ind, "task.md"), "仅审校诊断，禁止改写原文正文（audit only）。")
            _w(os.path.join(ind, "draft.md"), "We did a lot of tests.")
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "bootstrap", "--input", ind, "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            p_json = json.dumps([{"sentence_id": "S001", "polished_text": "We did a lot of tests.", "suggested_text": "We conducted numerous tests.", "audit_notes": "Fix colloquial 'did a lot of'.", "edit_types": ["needs_human_review"], "reason": "Diagnostic note."}])
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "patch", "--work-dir", wkd, "--stdin"], input=p_json, text=True, encoding="utf-8", check=True, stdout=subprocess.PIPE)
            subprocess.run([sys.executable, "-B", PIPELINE_PY, "finish", "--work-dir", wkd, "--out-dir", outd], check=True, stdout=subprocess.PIPE)
            md_txt = open(os.path.join(outd, "revision_ledger.md"), encoding="utf-8").read()
            csv_txt = open(os.path.join(outd, "revision_ledger.csv"), encoding="utf-8").read()
            html_txt = open(os.path.join(outd, "redline_diff.html"), encoding="utf-8").read()
            assert "Suggested Revision" in md_txt and "We conducted numerous tests." in md_txt
            assert "suggested_text" in csv_txt and "Fix colloquial" in csv_txt
            assert "Audit Notes" in html_txt and "We conducted numerous tests." in html_txt
            return "audit_only ledgers complete"

    def t52():
        with tempfile.TemporaryDirectory() as tmp:
            ind, wkd, outd = os.path.join(tmp, "in"), os.path.join(tmp, "wk"), os.path.join(tmp, "out")
            _w(os.path.join(ind, "draft.md"), "本研究 did a test.")
            man, blks, sents = case_ingestor.ingest_case(ind, wkd)
            rep = polish_verifier.verify_polish_run(man, sents, [{"sentence_id": "S001", "polished_text": "本研究 conducted a test.", "edit_types": ["collocation"], "reason": "Formal verb."}])
            res_t4 = render_deliverables.render_all_deliverables(man, blks, rep, outd, force_preview_tier="tier4")
            assert res_t4["preview_render_tier"] == "tier4_stdlib_png"
            assert "preview_render_note" in res_t4 and "redline_diff.html" in res_t4["preview_render_note"]
            # Check segments parser strips raw [- and {+ markers cleanly
            segs = render_deliverables._parse_inline_diff_segments("本研究 [-did-]{+conducted+} a test.")
            assert ("del", "did") in segs and ("ins", "conducted") in segs
            return "tier4 parsed red/green + CJK honest note ok"

    def t53():
        safe_pairs = [
            ("实验结果非常不错。", "实验结果较为理想。"),
            ("不仅如此，系统延迟也降低了。", "此外，系统延迟亦有所降低。"),
            ("未来研究将探讨多模态扩展。", "后续研究将探讨多模态扩展。"),
        ]
        for s, p in safe_pairs:
            iss = epistemic_entity_guard.compare_epistemic_and_invariants(s, p, lang="zh")
            assert not any(x["code"] == "POLARITY_FLIP" for x in iss), f"False ZH POLARITY_FLIP on {s} -> {p}: {iss}"
        bad_pairs = [
            ("组间差异不显著。", "组间差异显著。"),
            ("实验未观察到明显漂移。", "实验观察到明显漂移。"),
            ("该假设并非成立。", "该假设是成立的。"),
        ]
        for s, p in bad_pairs:
            iss = epistemic_entity_guard.compare_epistemic_and_invariants(s, p, lang="zh")
            assert any(x["code"] == "POLARITY_FLIP" and x["severity"] == "HIGH" for x in iss), f"Missed real ZH POLARITY_FLIP on {s} -> {p}: {iss}"
        return "3 safe ZH non-negation + 3 real ZH polarity flips ok"

    def t54():
        recs = [
            {"sentence_id": "S001", "section_role": "body", "source_text": "Catalyst achieved XX% yield under XXX conditions with TBD baseline and LSTM encoder.", "polished_text": "Catalyst achieved XX% yield under XXX conditions with TBD baseline and LSTM encoder."},
        ]
        iss = glossary_abbr_guard.audit_abbreviations(recs)
        undef_tokens = [i["detail"] for i in iss if i["code"] == "ABBR_UNDEFINED"]
        assert not any("XX" in d or "TBD" in d for d in undef_tokens), f"Placeholder triggered ABBR_UNDEFINED: {undef_tokens}"
        assert any("LSTM" in d for d in undef_tokens), f"Real undefined LSTM missed: {undef_tokens}"
        return "placeholder abbrs skipped, real LSTM flagged"

    suite = [
        ("T01", "kb_epistemic_ladder", t01),
        ("T02", "kb_style_profiles", t02),
        ("T03", "kb_edit_taxonomy_and_template", t03),
        ("T04", "ingest_txt_lossless_and_abbrev_split", t04),
        ("T05", "ingest_markdown_lossless_and_ref_scope", t05),
        ("T06", "ingest_latex_lossless", t06),
        ("T07", "ingest_html", t07),
        ("T08", "ingest_docx_zero_dep", t08),
        ("T09", "ingest_nested_output_contract", t09),
        ("T10", "adaptive_batches_900_sentences_under_24", t10),
        ("T11", "adaptive_batches_long_doc_advisory", t11),
        ("T12", "epistemic_escalation_high", t12),
        ("T13", "epistemic_weakening_high", t13),
        ("T14", "epistemic_polarity_flip_high", t14),
        ("T15", "epistemic_double_neg_whitelist_en_zh", t15),
        ("T16", "epistemic_scope_bound_dropped", t16),
        ("T17", "epistemic_direction_flip", t17),
        ("T18", "invariant_number_stat_missing", t18),
        ("T19", "invariant_citation_missing", t19),
        ("T20", "invariant_cross_ref_missing", t20),
        ("T21", "invariant_math_delimiter_and_span", t21),
        ("T22", "glossary_4_format_loading", t22),
        ("T23", "glossary_3_internal_conflicts", t23),
        ("T24", "glossary_forbidden_and_locked_mismatch", t24),
        ("T25", "glossary_us_uk_spelling_mix", t25),
        ("T26", "glossary_variant_drift", t26),
        ("T27", "abbr_separate_abstract_body_scope", t27),
        ("T28", "abbr_redefined", t28),
        ("T29", "abbr_scientific_whitelist_and_math_skip", t29),
        ("T30", "diff_engine_ops_and_inline_diff", t30),
        ("T31", "diff_unlogged_and_phantom_edit", t31),
        ("T32", "diff_over_edit_risk_minimal", t32),
        ("T33", "linter_colloquial_and_chinglish", t33),
        ("T34", "linter_punct_width_mismatch", t34),
        ("T35", "linter_meta_leakage_high", t35),
        ("T36", "e2e_polish_hotfix_and_validate", t36),
        ("T37", "e2e_bilingual_polish", t37),
        ("T38", "e2e_audit_only_guard", t38),
        ("T39", "e2e_qa_guard", t39),
        ("T40", "e2e_anomaly_boundaries", t40),
        ("T41", "ooxml_clean_redline_docx_and_roundtrip", t41),
        ("T42", "preview_4tier_forced_stdlib_png", t42),
        ("T43", "yaml_and_ast_cli_zero_drift", t43),
        ("T44", "harbor_packaging_and_static_compliance", t44),
        ("T45", "reg_negation_contraction_no_polarity_flip", t45),
        ("T46", "reg_en_abbr_no_dual_regex_redefined", t46),
        ("T47", "reg_nested_empty_output_contract_filled", t47),
        ("T48", "reg_heading_unsplit_and_bilingual_short_ok", t48),
        ("T49", "reg_non_repairable_glossary_conflict", t49),
        ("T50", "reg_diff_lossless_unicode_latin_ext", t50),
        ("T51", "reg_audit_only_suggested_and_notes_ledgers", t51),
        ("T52", "reg_tier4_parsed_diff_and_cjk_note", t52),
        ("T53", "reg_zh_non_negation_exclusions_and_real_polarity_flip", t53),
        ("T54", "reg_placeholder_abbr_whitelist_vs_real_undefined", t54),
    ]

    for tid, name, fn in suite:
        check(tid, name, fn)

    passed = sum(1 for r in results if r["status"] == "PASS")
    print(json.dumps({"passed": passed, "total": len(results), "results": results}, ensure_ascii=False, indent=2))
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    run_all_tests()
