#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nanopub_auditor.py - 8-Dimensional Deterministic Nanopublication & Grounded QA Auditor
Part of the Nanopublication Scientific Reading Skill (task_09_nanopub).
Top-level imports are strictly restricted to standard libraries and sibling modules.
"""

import os
import sys
import json
import re
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from entity_guard import (
        load_invariants,
        compare_claim_against_anchors,
        extract_scientific_qualifiers,
        check_causal_strength_upgrade,
    )
    from pubinfo_extractor import extract_pubinfo_from_index, verify_pubinfo_against_source
    from nanopub_assembler import load_relation_vocab, load_json_safe
except ImportError:
    from .entity_guard import (
        load_invariants,
        compare_claim_against_anchors,
        extract_scientific_qualifiers,
        check_causal_strength_upgrade,
    )
    from .pubinfo_extractor import extract_pubinfo_from_index, verify_pubinfo_against_source
    from .nanopub_assembler import load_relation_vocab, load_json_safe


SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.?!。！？])\s+(?=[A-Z0-9\u4e00-\u9fa5\(\[\"“])")
VAGUE_CLAIM_REGEX = re.compile(
    r"^(?:this\s+(?:paper|study|work|article)|the\s+authors?|we|本文|本研究|该研究|作者)\s+"
    r"(?:studies|discusses|investigates|reviews|surveys|explores|presents\s+an?\s+overview\s+of|"
    r"focuses\s+on|addresses|研究了|探讨了|综述了|介绍了|讨论了|分析了)\b",
    re.I,
)
COMPOUND_CLAUSE_SPLIT_REGEX = re.compile(r"\s*[;；]\s*")
CLAUSE_PREDICATE_HINT = re.compile(
    r"(?:\b(?:increases?|decreases?|reduces?|improves?|causes?|inhibits?|activates?|outperforms?|achieves?|correlates?|shows?|proves?|is|are|was|were|yields?)\b"
    r"|\d+(?:\.\d+)?\s*%|提高|降低|增加|减少|导致|抑制|激活|优于|达到|相关|表明|证明)",
    re.I,
)
MULTI_CONJUNCTION_VERB_REGEX = re.compile(
    r"\b(?:increases?|decreases?|reduces?|improves?|causes?|inhibits?|activates?|outperforms?|achieves?)\b"
    r".{8,140}\b(?:and\s+(?:also\s+)?|while\s+|whereas\s+)(?:increases?|decreases?|reduces?|improves?|causes?|inhibits?|activates?|outperforms?|achieves?|reduces?)\b",
    re.I,
)
MULTI_CONJUNCTION_ZH_REGEX = re.compile(
    r"(?:提高|增加|降低|减少|促进|抑制|优于).{4,60}(?:并|且|同时|而|并且)(?:提高|增加|降低|减少|促进|抑制|优于)"
)

PASSIVE_BY_EN_REGEX = re.compile(r"\b(?:is|are|was|were|be|been)\s+[a-z]+ed\s+by\b", re.I)
PASSIVE_ZH_REGEX = re.compile(r"(?:被|由)[^，。；\n]{1,30}(?:抑制|激活|导致|引发|促进|提高|降低)")

CITATION_MARKER_REGEX = re.compile(
    r"(?:\[\s*\d+(?:\s*(?:[-–—]{1,3}|,)\s*\d+)*\s*\]|\([A-Z][a-zA-Z'\-]+(?:\s+et\s+al\.?|\s+(?:and|&)\s+[A-Z][a-zA-Z'\-]+)?,\s*(?:19|20)\d{2}[a-z]?\))"
)
THIRD_PERSON_ATTR_REGEX = re.compile(
    r"(?:\bet\s+al\.?|\b(?:showed|reported|found|demonstrated|observed|proposed|argued)\s+that\b|"
    r"\b(?:previous|prior|earlier)\s+(?:work|studies|research|literature)\b|"
    r"等人|前人研究|已有研究|早期研究|文献\s*\[)",
    re.I,
)
FIRST_PERSON_CONTRIB_REGEX = re.compile(
    r"(?:\b(?:we\s+(?:propose|prove|show|demonstrate|introduce|present|establish|find|observe)|"
    r"our\s+(?:method|approach|framework|model|results|experiments|theorem|analysis)|"
    r"in\s+this\s+paper|this\s+(?:paper|study|work)\s+(?:proposes|proves|shows|demonstrates|introduces))\b|"
    r"(?:本文提出|我们提出|我们证明|本文证明|本研究发现|本文实验|我们构建))",
    re.I,
)

AUTHOR_CONTAM_REGEX = re.compile(
    r"(?:@|\barXiv\s*:|\b(?:19|20)\d{2}\b|\b(?:University|Institute|Department|Laboratory|College|Academy|School\s+of|Center\s+for)\b|"
    r"(?:大学|学院|研究所|实验室|研究院|关键词|通信作者))",
    re.I,
)


def _count_sentences(txt):
    if not txt or not txt.strip():
        return 0
    parts = [p.strip() for p in SENTENCE_SPLIT_REGEX.split(txt.strip()) if p.strip()]
    return max(1, len(parts))


def audit_work_state(work_state, evidence_index, pubinfo_bundle=None, paper_signals=None):
    """
    Execute 8-dimensional deterministic quality audit across all Nanopublications,
    PubInfo records, QA answers, conflicts, and checklist items.
    """
    issues = []

    def add_issue(code, dimension, severity, target_id, message, fix_hint=""):
        issues.append({
            "code": code,
            "dimension": dimension,
            "severity": severity,
            "target_id": target_id,
            "message": message,
            "fix_hint": fix_hint,
        })

    anchors = evidence_index.get("anchors", [])
    anchor_by_id = {a.get("anchor_id"): a for a in anchors}
    has_body_or_results_blocks = any(
        a.get("section_role") in ("results", "methods", "body_unclassified", "appendix")
        and a.get("block_type") in ("paragraph", "table_like", "caption", "formula")
        for a in anchors
    )

    # Risk pages lookup from paper_signals
    risk_pages_by_doc = {}
    if paper_signals and isinstance(paper_signals.get("documents"), list):
        for ds in paper_signals["documents"]:
            did = ds.get("doc_id", "D1")
            qr = ds.get("quality_risk") or {}
            risk_pages = set(qr.get("garbled_pages", [])) | set(qr.get("unextractable_pages", []))
            if qr.get("is_scanned"):
                risk_pages |= set(qr.get("low_density_pages", []))
            risk_pages_by_doc[did] = risk_pages

    _vocab, vocab_by_id, _alias_map = load_relation_vocab()
    invariants = load_invariants()

    # Deterministic re-extraction of PubInfo from source for D7 verification
    re_extracted_pub = extract_pubinfo_from_index(evidence_index, paper_signals=paper_signals)
    ext_pub_by_doc = {s.get("doc_id", "D1"): s for s in re_extracted_pub.get("sources", [])}
    full_text_by_doc = {}
    for a in anchors:
        did = a.get("doc_id", "D1")
        full_text_by_doc.setdefault(did, []).append(a.get("text", ""))
    full_text_by_doc = {k: "\n".join(v) for k, v in full_text_by_doc.items()}

    nps = work_state.get("nanopublications", [])
    valid_grounded_np_ids = set()

    for np in nps:
        head = np.get("head") or {}
        np_id = head.get("nanopub_id") or "NP-UNKNOWN"
        asrt = np.get("assertion") or {}
        prov = np.get("provenance") or {}
        loc = prov.get("location") or {}
        pub = np.get("pubinfo") or {}

        stmt = (asrt.get("statement") or "").strip()
        subj = asrt.get("subject") or {}
        pred = asrt.get("predicate") or {}
        obj = asrt.get("object") or {}
        qual = asrt.get("qualifiers") or {}
        polarity = asrt.get("polarity", "affirmed")
        quote = (prov.get("evidence_quote") or "").strip()
        doc_id = loc.get("doc_id", "D1")
        claimed_bid = loc.get("block_id")
        matched_ids = loc.get("matched_block_ids") or []
        align_status = prov.get("alignment_status", "QUOTE_NOT_FOUND")
        s_flags = set(prov.get("sufficiency_flags") or [])
        sug_bid = prov.get("suggested_block_id")

        # --- D1: Atomicity & Granularity ---
        s_cnt = _count_sentences(stmt)
        if len(stmt) > 320 or s_cnt > 2:
            add_issue(
                "CLAIM_TOO_LONG",
                "D1_atomicity",
                "HIGH",
                np_id,
                f"Assertion statement exceeds atomicity limit (chars={len(stmt)}, sentences={s_cnt}).",
                "Split into single-sentence falsifiable atomic claims <= 320 chars.",
            )

        semi_parts = [p.strip() for p in COMPOUND_CLAUSE_SPLIT_REGEX.split(stmt) if len(p.strip()) >= 18]
        has_multi_semi = len(semi_parts) >= 2 and sum(1 for p in semi_parts if CLAUSE_PREDICATE_HINT.search(p)) >= 2
        has_respectively = bool(re.search(r"\brespectively\b|分别", stmt, re.I)) and len(re.findall(r"\d+(?:\.\d+)?", stmt)) >= 3
        has_multi_verb_conj = bool(MULTI_CONJUNCTION_VERB_REGEX.search(stmt) or MULTI_CONJUNCTION_ZH_REGEX.search(stmt))
        if has_multi_semi or has_respectively or has_multi_verb_conj:
            add_issue(
                "COMPOUND_UNSPLIT_CLAIM",
                "D1_atomicity",
                "HIGH",
                np_id,
                f"Statement contains unsplit compound/respectively/multi-metric clauses: '{stmt[:120]}'.",
                "Decompose each independent finding or metric into its own atomic Nanopublication.",
            )

        if VAGUE_CLAIM_REGEX.search(stmt) and not re.search(r"\d+(?:\.\d+)?\s*%|\bp\s*[<≤]|\boutperform|\bincreas|\bdecreas|\binhibit|\bactivat|\bcaus", stmt, re.I):
            add_issue(
                "VAGUE_NON_FALSIFIABLE_CLAIM",
                "D1_atomicity",
                "HIGH",
                np_id,
                f"Statement appears to be a non-falsifiable topic summary rather than an atomic scientific claim: '{stmt[:120]}'.",
                "Rewrite into a concrete falsifiable subject-predicate-object proposition.",
            )

        # --- D2: SPO Structure & Controlled Vocabulary ---
        s_lbl = (subj.get("label") or "").strip()
        o_lbl = (obj.get("label") or "").strip()
        p_id = (pred.get("id") or "").strip()
        if not s_lbl or not o_lbl or not p_id:
            add_issue(
                "SPO_INCOMPLETE",
                "D2_spo_vocab",
                "HIGH",
                np_id,
                f"Incomplete SPO triple: subject='{s_lbl}', predicate='{p_id}', object='{o_lbl}'.",
                "Ensure subject.label, predicate.id, and object.label are all non-empty.",
            )

        is_in_vocab = bool(pred.get("vocab_mapped", True)) and (p_id.lower() in vocab_by_id)
        if p_id and not is_in_vocab:
            add_issue(
                "PREDICATE_NOT_IN_VOCAB",
                "D2_spo_vocab",
                "MEDIUM",
                np_id,
                f"Predicate '{p_id}' is not mapped to references/relation_vocab.json.",
                "Select one of the 30 canonical predicates in relation_vocab.json.",
            )

        pred_meta = vocab_by_id.get(p_id.lower(), {})
        if pred_meta and not pred_meta.get("symmetric", False) and s_lbl and o_lbl:
            if PASSIVE_BY_EN_REGEX.search(stmt) or PASSIVE_ZH_REGEX.search(stmt):
                s_pos = stmt.lower().find(s_lbl.lower())
                o_pos = stmt.lower().find(o_lbl.lower())
                if s_pos != -1 and o_pos != -1 and o_pos < s_pos:
                    add_issue(
                        "DIRECTIONALITY_REVIEW",
                        "D2_spo_vocab",
                        "MEDIUM",
                        np_id,
                        f"Passive-voice statement '{stmt[:100]}' places object before subject; verify SPO direction.",
                        "Verify subject is the agent/cause and object is the target/effect.",
                    )

        # --- D3: Verbatim Quote Alignment & Anchor Existence ---
        if align_status == "QUOTE_NOT_FOUND":
            add_issue(
                "QUOTE_NOT_FOUND",
                "D3_quote_alignment",
                "HIGH",
                np_id,
                f"Evidence quote could not be verified in source anchors (claimed block={claimed_bid}).",
                f"Replace with exact verbatim span from source. Best candidate block={sug_bid}." if sug_bid else "Replace with exact verbatim span from source.",
            )
        else:
            valid_grounded_np_ids.add(np_id)
            if sug_bid and claimed_bid and sug_bid != claimed_bid:
                add_issue(
                    "BLOCK_ID_DRIFT",
                    "D3_quote_alignment",
                    "MEDIUM",
                    np_id,
                    f"Claimed block_id '{claimed_bid}' drifted; quote actually matched block '{sug_bid}'.",
                    f"Update location.block_id to '{sug_bid}'.",
                )
            if align_status == "FUZZY_PARTIAL":
                add_issue(
                    "FUZZY_QUOTE_ALIGNMENT",
                    "D3_quote_alignment",
                    "MEDIUM",
                    np_id,
                    f"Quote only partially matched (ratio={prov.get('match_ratio')}); possible minor paraphrase.",
                    "Copy exact verbatim text from the PDF anchor block.",
                )

        if claimed_bid and claimed_bid not in anchor_by_id and not matched_ids:
            add_issue(
                "ANCHOR_ID_NONEXISTENT",
                "D3_quote_alignment",
                "HIGH",
                np_id,
                f"Claimed block_id '{claimed_bid}' does not exist in evidence_index.json.",
                "Use valid P{page}-B{seq:03d} anchor IDs from reading_packets or evidence_index.",
            )

        # --- D4: Minimal Sufficiency & Evidence Tier ---
        if "PARAGRAPH_DUMP" in s_flags:
            add_issue(
                "PARAGRAPH_DUMP",
                "D4_sufficiency_tier",
                "HIGH",
                np_id,
                f"Evidence quote is an oversized paragraph dump (chars={prov.get('quote_char_len')}, sentences={prov.get('sentence_count')}).",
                "Trim evidence_quote to the minimal 1-3 sentence span directly supporting this single assertion.",
            )
        if "TOO_SHORT_FRAGMENT" in s_flags:
            add_issue(
                "TOO_SHORT_FRAGMENT",
                "D4_sufficiency_tier",
                "MEDIUM",
                np_id,
                f"Evidence quote is too short (<20 chars, len={prov.get('quote_char_len')}) to stand as verifiable evidence.",
                "Include the complete minimal sentence containing subject, relation, and value.",
            )
        if "ABSTRACT_ONLY_EVIDENCE" in s_flags and has_body_or_results_blocks:
            add_issue(
                "ABSTRACT_ONLY_EVIDENCE",
                "D4_sufficiency_tier",
                "MEDIUM",
                np_id,
                "Assertion is grounded only in the Abstract even though body/results sections are available.",
                "Bind primary evidence from results, tables, figures, or methods blocks.",
            )

        matched_ancs = [anchor_by_id[mid] for mid in matched_ids if mid in anchor_by_id]
        doc_risk_pages = risk_pages_by_doc.get(doc_id, set())
        if "REQUIRES_VISUAL_TABLE_OR_FIGURE" in s_flags and any(a.get("page") in doc_risk_pages or "unextractable_asset" in (a.get("quality_flags") or []) for a in matched_ancs):
            add_issue(
                "VISUAL_EVIDENCE_UNVERIFIABLE",
                "D4_sufficiency_tier",
                "MEDIUM",
                np_id,
                "Referenced table/figure falls on a scanned/garbled/unextractable page and requires visual confirmation.",
                "Downgrade confidence and add entry to manual_review_checklist.",
            )

        # --- D5: Numeric, Unit, Qualifier, Polarity & Causal Fidelity ---
        ev_texts_for_guard = [quote] if quote else [a.get("text", "") for a in matched_ancs]
        sec_ctx = []
        if matched_ancs:
            target_sec_id = matched_ancs[0].get("section_id")
            sec_ctx = [a.get("text", "") for a in anchors if a.get("doc_id") == doc_id and a.get("section_id") == target_sec_id]

        cmp_res = compare_claim_against_anchors(
            stmt,
            ev_texts_for_guard,
            invariants=invariants,
            section_context=sec_ctx,
        )
        if cmp_res.get("unsupported_numbers"):
            add_issue(
                "NUMERIC_HALLUCINATION",
                "D5_fidelity",
                "HIGH",
                np_id,
                f"Claim contains unsupported number(s) absent from evidence/section: {cmp_res['unsupported_numbers']}.",
                "Verify exact digits and units against the matched source block.",
            )
        if cmp_res.get("unit_mismatches"):
            add_issue(
                "UNIT_MISMATCH",
                "D5_fidelity",
                "HIGH",
                np_id,
                f"Unit mismatch between claim and evidence: {cmp_res['unit_mismatches']}.",
                "Preserve exact original measurement units.",
            )

        if cmp_res.get("negation_alerts"):
            add_issue(
                "POLARITY_MISMATCH",
                "D5_fidelity",
                "HIGH",
                np_id,
                f"Negation polarity mismatch between statement and evidence: {cmp_res['negation_alerts']}.",
                "Align assertion polarity and statement negation with the source quote.",
            )
        else:
            # Also check explicit polarity field against evidence negation
            ev_neg = (cmp_res.get("anchor_fingerprints") or {}).get("logic_markers", {}).get("negation", [])
            cl_neg = (cmp_res.get("claim_fingerprints") or {}).get("logic_markers", {}).get("negation", [])
            if polarity == "negated" and not ev_neg and not cl_neg and p_id != "no_significant_effect_on":
                add_issue(
                    "POLARITY_MISMATCH",
                    "D5_fidelity",
                    "HIGH",
                    np_id,
                    "Assertion polarity is 'negated' without negation markers in statement or evidence.",
                    "Set polarity='affirmed' or verify negated phrasing.",
                )

        # Scientific qualifiers retention check
        ev_qual = extract_scientific_qualifiers(quote)
        stmt_qual = extract_scientific_qualifiers(stmt)
        qual_flat_str = " ".join([
            " ".join(qual.get("conditions") or []),
            str(qual.get("population_or_dataset") or ""),
            str(qual.get("sample_size") or ""),
            str(qual.get("statistics") or ""),
            str(qual.get("temporal_or_dose") or ""),
        ]).strip()
        qual_field_parsed = extract_scientific_qualifiers(qual_flat_str)
        important_kinds = ("temperature", "ph", "concentration_dose", "sample_size", "significance", "experimental_context")
        dropped_kinds = []
        for knd in important_kinds:
            if ev_qual["by_kind"].get(knd) and not stmt_qual["by_kind"].get(knd) and not qual_field_parsed["by_kind"].get(knd):
                dropped_kinds.append({knd: ev_qual["by_kind"][knd]})
        if dropped_kinds:
            add_issue(
                "QUALIFIER_DROPPED",
                "D5_fidelity",
                "MEDIUM",
                np_id,
                f"Evidence contains scientific qualifiers dropped from both statement and qualifiers: {dropped_kinds}.",
                "Preserve dose/temperature/pH/sample-size/p-value/context qualifiers in assertion.qualifiers and statement.",
            )

        # Causal strength upgrade check (check both statement text AND predicate causal_strength)
        causal_chk = check_causal_strength_upgrade(stmt, quote)
        pred_is_causal = pred_meta.get("causal_strength") == "causal" and p_id != "no_significant_effect_on"
        ev_has_assoc = bool(causal_chk.get("evidence_associative_markers"))
        ev_has_causal = bool(causal_chk.get("evidence_causal_markers"))
        if causal_chk.get("upgraded") or (pred_is_causal and ev_has_assoc and not ev_has_causal):
            add_issue(
                "CAUSAL_OVERCLAIM",
                "D5_fidelity",
                "HIGH",
                np_id,
                causal_chk.get("reason") or f"Predicate '{p_id}' asserts causal strength whereas evidence uses associative/hedged language {causal_chk.get('evidence_associative_markers')}.",
                "Downgrade predicate to 'correlates_with' / 'associated_with' and hedge statement faithfully.",
            )

        # --- D6: Secondary Citation Attribution Guard (Stage 1 lesson) ---
        sec_role = loc.get("section_role")
        attr_type = prov.get("source_attribution", "primary_study_finding")
        has_cite = bool(CITATION_MARKER_REGEX.search(quote)) or any(a.get("has_citation_markers") for a in matched_ancs)
        has_third = bool(THIRD_PERSON_ATTR_REGEX.search(quote))
        has_first = bool(FIRST_PERSON_CONTRIB_REGEX.search(quote))
        if (
            sec_role in ("introduction", "discussion", "body_unclassified")
            and has_cite
            and has_third
            and not has_first
            and attr_type in ("primary_study_finding", "primary_study")
        ):
            add_issue(
                "SECONDARY_CITATION_MISATTRIBUTED",
                "D6_secondary_citation",
                "HIGH",
                np_id,
                "Evidence in intro/discussion cites prior work with third-person attribution and no primary-study anchor, yet source_attribution is marked primary_study_finding.",
                "Set source_attribution='cited_prior_work', evidence_type='secondary_citation', evidence_tier='T6_secondary_citation'.",
            )

    # --- D7: PubInfo Provenance & Anti-Hallucination ---
    checked_pub_docs = set()
    docs_pub_list = list(work_state.get("documents_pubinfo") or [])
    for np in nps:
        pub = np.get("pubinfo") or {}
        did = pub.get("doc_id") or (np.get("provenance") or {}).get("location", {}).get("doc_id") or "D1"
        if did not in checked_pub_docs and pub:
            docs_pub_list.append(pub)
            checked_pub_docs.add(did)

    seen_pub_keys = set()
    for idx_p, pub in enumerate(docs_pub_list):
        did = pub.get("doc_id", f"D{idx_p + 1}")
        key = (did, pub.get("doi"), pub.get("arxiv_id"), pub.get("year"), tuple(pub.get("authors") or []))
        if key in seen_pub_keys:
            continue
        seen_pub_keys.add(key)
        ext_pub = ext_pub_by_doc.get(did, {})
        full_txt = full_text_by_doc.get(did, "")
        pv = verify_pubinfo_against_source(pub, ext_pub, full_txt)
        for h in pv.get("hallucinations", []):
            add_issue(
                "PUBINFO_HALLUCINATION",
                "D7_pubinfo",
                "HIGH",
                did,
                h.get("reason", f"Hallucinated pubinfo field '{h.get('field')}'."),
                "Replace with null if absent from source document and case.json.",
            )
        # Secondary check for author contamination
        bad_auth = [a for a in (pub.get("authors") or []) if AUTHOR_CONTAM_REGEX.search(str(a))]
        if bad_auth:
            add_issue(
                "AUTHOR_CONTAMINATION",
                "D7_pubinfo",
                "MEDIUM",
                did,
                f"Extracted authors list contains affiliation/email/year/arXiv noise: {bad_auth}.",
                "Strip affiliation lines, emails, years, and arXiv stamps from authors.",
            )

    # --- D8: QA Binding Completeness & Honesty ---
    qa_list = work_state.get("qa_answers", [])
    answered_qids = {q.get("question_id") for q in qa_list if q.get("question_id")}
    for rq in work_state.get("research_questions", []):
        qid = rq.get("question_id")
        if qid and qid not in answered_qids:
            add_issue(
                "QA_MISSING_QUESTION",
                "D8_qa_honesty",
                "MEDIUM",
                qid,
                f"Research question '{qid}' from input manifest has no entry in qa_answers.",
                "Add grounded answer or explicit unanswerable/not_reported entry for this question.",
            )

    for qa in qa_list:
        qid = qa.get("question_id", "Q-UNKNOWN")
        astatus = qa.get("answer_status", "answered")
        sup_ids = qa.get("supporting_nanopub_ids") or []
        if astatus in ("answered", "partially_answered", "conflicting_evidence"):
            valid_refs = [sid for sid in sup_ids if sid in valid_grounded_np_ids]
            if not valid_refs:
                add_issue(
                    "QA_UNGROUNDED",
                    "D8_qa_honesty",
                    "HIGH",
                    qid,
                    f"QA '{qid}' has answer_status='{astatus}' but 0 verified grounded nanopub IDs (refs={sup_ids}).",
                    "Bind at least one verified nanopub_id or downgrade answer_status to 'insufficient_evidence'/'unanswerable'.",
                )
        elif astatus in ("not_reported", "unverifiable", "insufficient_evidence", "unanswerable"):
            if not (qa.get("answer") or qa.get("uncertainty_note")):
                add_issue(
                    "QA_UNEXPLAINED_GAP",
                    "D8_qa_honesty",
                    "MEDIUM",
                    qid,
                    f"Unanswered QA '{qid}' ({astatus}) lacks explanation in answer or uncertainty_note.",
                    "Document why the source materials cannot verify this question.",
                )

    checklist = work_state.get("manual_review_checklist", [])
    checklist_str = json.dumps(checklist, ensure_ascii=False)
    for conf in work_state.get("conflicts", []):
        cid = conf.get("conflict_id", "")
        if cid and cid not in checklist_str:
            add_issue(
                "CHECKLIST_INCOMPLETE",
                "D8_qa_honesty",
                "MEDIUM",
                cid,
                f"Detected conflict '{cid}' is not recorded in manual_review_checklist.",
                "Include unresolved conflicts in manual_review_checklist.",
            )

    # Summary & recommended status
    sev_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for iss in issues:
        sev_counts[iss["severity"]] = sev_counts.get(iss["severity"], 0) + 1

    tier_dist = {}
    for np in nps:
        t = (np.get("provenance") or {}).get("evidence_tier", "T7_unverified")
        tier_dist[t] = tier_dist.get(t, 0) + 1

    all_unreadable = bool(
        paper_signals
        and paper_signals.get("documents")
        and all(
            (d.get("quality_risk") or {}).get("is_scanned")
            and len((d.get("quality_risk") or {}).get("unextractable_pages", [])) >= d.get("page_count", 1)
            for d in paper_signals["documents"]
        )
    )
    has_unanswerable_qa = any(
        q.get("answer_status") in ("unanswerable", "insufficient_evidence", "unverifiable")
        for q in qa_list
    )

    if all_unreadable or (not nps and not qa_list):
        rec_status = "partial" if (nps or qa_list or work_state.get("documents_pubinfo")) else "unanswerable"
    elif sev_counts["HIGH"] > 0 or has_unanswerable_qa:
        rec_status = "partial"
    else:
        rec_status = "success"

    report = {
        "case_id": work_state.get("case_id", "unknown"),
        "audit_passed": sev_counts["HIGH"] == 0,
        "recommended_status": rec_status,
        "total_nanopublications": len(nps),
        "grounded_nanopublications": len(valid_grounded_np_ids),
        "total_qa_answers": len(qa_list),
        "total_conflicts": len(work_state.get("conflicts", [])),
        "severity_counts": sev_counts,
        "evidence_tier_distribution": tier_dist,
        "issues": issues,
    }
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="8-Dimensional Deterministic Nanopublication Auditor")
    parser.add_argument("--work-state", required=True, help="Path to work_state.json")
    parser.add_argument("--structure-index", required=True, help="Path to evidence_index.json")
    parser.add_argument("--pubinfo", default=None, help="Optional path to pubinfo.json")
    parser.add_argument("--out", default=None, help="Output path for audit_report.json")
    args = parser.parse_args()

    work_state = load_json_safe(args.work_state)
    if not work_state:
        raise FileNotFoundError(f"work_state.json not found: {args.work_state}")
    ev_index = load_json_safe(args.structure_index)
    if not ev_index:
        raise FileNotFoundError(f"evidence_index.json not found: {args.structure_index}")
    pub_bundle = load_json_safe(args.pubinfo) if args.pubinfo else None
    sig_path = os.path.join(os.path.dirname(os.path.abspath(args.structure_index)), "paper_signals.json")
    paper_signals = load_json_safe(sig_path)

    report = audit_work_state(work_state, ev_index, pubinfo_bundle=pub_bundle, paper_signals=paper_signals)
    out_path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.work_state)), "audit_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(
        f"[nanopub_auditor] passed={report['audit_passed']} | recommended={report['recommended_status']} "
        f"| HIGH={report['severity_counts']['HIGH']} MEDIUM={report['severity_counts']['MEDIUM']} "
        f"LOW={report['severity_counts']['LOW']} -> {out_path}"
    )


if __name__ == "__main__":
    main()
