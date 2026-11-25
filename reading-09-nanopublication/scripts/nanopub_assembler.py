#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nanopub_assembler.py - Incremental Nanopublication Assembler, Deduplication Promoter & Conflict Graph
Part of the Nanopublication Scientific Reading Skill (task_09_nanopub).
Top-level imports are strictly restricted to standard libraries and sibling modules.
"""

import os
import sys
import json
import re
import hashlib
import argparse
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from evidence_aligner import align_evidence_quote, _build_same_page_windows, normalize_for_alignment
    from entity_guard import _normalize_ligatures, extract_scientific_qualifiers
except ImportError:
    from .evidence_aligner import align_evidence_quote, _build_same_page_windows, normalize_for_alignment
    from .entity_guard import _normalize_ligatures, extract_scientific_qualifiers


VOCAB_PATH_CANDIDATES = [
    os.path.join(BASE_DIR, "references", "relation_vocab.json"),
    os.path.join(BASE_DIR, "resources", "relation_vocab.json"),
]

TIER_RANK = {
    "T1_direct_data": 1,
    "T2_table_figure": 2,
    "T3_methods_or_text": 3,
    "T4_abstract_summary": 4,
    "T5_indirect_inference": 5,
    "T6_secondary_citation": 6,
    "T7_unverified": 7,
}

TIER_MAX_CONFIDENCE = {
    "T1_direct_data": 0.96,
    "T2_table_figure": 0.92,
    "T3_methods_or_text": 0.86,
    "T4_abstract_summary": 0.72,
    "T5_indirect_inference": 0.65,
    "T6_secondary_citation": 0.55,
    "T7_unverified": 0.25,
}

ALIGN_RANK = {
    "EXACT_MATCH": 1,
    "NORMALIZED_MATCH": 2,
    "CROSS_BLOCK_MATCH": 3,
    "FUZZY_PARTIAL": 4,
    "QUOTE_NOT_FOUND": 5,
}

THEOREM_PROOF_REGEX = re.compile(
    r"(?:\b(?:Theorem|Lemma|Proposition|Corollary|Proof|Definition|Remark)\b|定理|引理|命题|推论|证明)",
    re.I,
)
QUANT_IN_TEXT_REGEX = re.compile(
    r"(?:\d+(?:\.\d+)?\s*%|\bp\s*[<≤=>]\s*0?\.\d+|\b\d+(?:\.\d+)?\s*(?:mg/kg|mM|μM|nM|ms|Hz|GHz|dB|K|°C|qubits?|ebits?|fold|times)\b|[±]\s*\d)",
    re.I,
)

OPPOSING_PREDICATE_PAIRS = {
    frozenset(("increases", "decreases")),
    frozenset(("promotes", "suppresses")),
    frozenset(("activates", "inhibits")),
    frozenset(("outperforms", "underperforms")),
    frozenset(("positively_correlates_with", "negatively_correlates_with")),
    frozenset(("supports", "contradicts")),
    frozenset(("replicates", "fails_to_replicate")),
}

DIRECTIONAL_EFFECT_PREDICATES = {
    "causes", "inhibits", "activates", "promotes", "suppresses", "increases", "decreases", "outperforms", "underperforms"
}


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_safe(path, default=None):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def load_relation_vocab():
    """Load canonical relation vocabulary and build id/alias lookup tables."""
    for p in VOCAB_PATH_CANDIDATES:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            by_id = {}
            alias_map = {}
            for pred in data.get("predicates", []):
                pid = pred["id"]
                by_id[pid.lower()] = pred
                alias_map[pid.lower()] = pid
                alias_map[pid.replace("_", " ").lower()] = pid
                for al in pred.get("aliases", []):
                    alias_map[al.strip().lower()] = pid
            return data, by_id, alias_map
    return {"predicates": []}, {}, {}


def normalize_predicate(raw_pred, vocab_by_id, alias_map):
    """Map raw predicate string or dict to canonical vocabulary entry."""
    raw_verb = None
    cand = ""
    if isinstance(raw_pred, dict):
        cand = str(raw_pred.get("id") or raw_pred.get("label") or raw_pred.get("raw_verb") or "").strip()
        raw_verb = str(raw_pred.get("raw_verb") or raw_pred.get("label") or cand).strip() or None
    elif isinstance(raw_pred, str):
        cand = raw_pred.strip()
        raw_verb = cand or None

    key = re.sub(r"\s+", " ", cand.replace("_", " ")).strip().lower()
    key_under = cand.strip().lower().replace(" ", "_")

    matched_id = None
    if key_under in vocab_by_id:
        matched_id = vocab_by_id[key_under]["id"]
    elif cand.lower() in alias_map:
        matched_id = alias_map[cand.lower()]
    elif key in alias_map:
        matched_id = alias_map[key]
    else:
        # Substring fallback against longer aliases first
        for al_k in sorted(alias_map.keys(), key=len, reverse=True):
            if len(al_k) >= 4 and (al_k == key or re.search(r"\b" + re.escape(al_k) + r"\b", key)):
                matched_id = alias_map[al_k]
                break

    if matched_id and matched_id.lower() in vocab_by_id:
        p = vocab_by_id[matched_id.lower()]
        return {
            "id": p["id"],
            "uri": p["uri"],
            "label": p["id"].replace("_", " "),
            "category": p["category"],
            "polarity": p["polarity"],
            "causal_strength": p["causal_strength"],
            "symmetric": bool(p.get("symmetric", False)),
            "raw_verb": raw_verb or p["id"],
            "vocab_mapped": True,
        }

    fallback_id = re.sub(r"[^a-z0-9_]+", "_", key_under).strip("_") or "unmapped_relation"
    return {
        "id": fallback_id,
        "uri": f"npv:unmapped/{fallback_id}",
        "label": raw_verb or fallback_id,
        "category": "unmapped",
        "polarity": "neutral",
        "causal_strength": "unspecified",
        "symmetric": False,
        "raw_verb": raw_verb or fallback_id,
        "vocab_mapped": False,
    }


def _norm_entity_text(val):
    if isinstance(val, dict):
        lbl = val.get("label") or val.get("name") or val.get("value") or ""
    else:
        lbl = str(val or "")
    s = normalize_for_alignment(lbl, join_hyphen_space=True).lower()
    return re.sub(r"\s+", " ", s).strip()


def _normalize_entity_obj(val, default_type="entity"):
    if isinstance(val, dict):
        lbl = str(val.get("label") or val.get("name") or "").strip()
        out = {
            "label": lbl,
            "entity_type": str(val.get("entity_type") or default_type),
            "normalized_id": val.get("normalized_id"),
        }
        if "value" in val:
            out["value"] = val.get("value")
        if "unit" in val:
            out["unit"] = val.get("unit")
        return out
    lbl = str(val or "").strip()
    return {"label": lbl, "entity_type": default_type, "normalized_id": None}


def _normalize_qualifiers(raw_qual, statement_text="", evidence_text=""):
    """Normalize qualifiers dict and enrich deterministically if conditions are passed as list/str."""
    if isinstance(raw_qual, dict):
        conds = raw_qual.get("conditions") or []
        if isinstance(conds, str):
            conds = [conds]
        q_out = {
            "conditions": [str(c).strip() for c in conds if str(c).strip()],
            "population_or_dataset": raw_qual.get("population_or_dataset"),
            "sample_size": raw_qual.get("sample_size"),
            "statistics": raw_qual.get("statistics"),
            "temporal_or_dose": raw_qual.get("temporal_or_dose"),
        }
    elif isinstance(raw_qual, list):
        q_out = {
            "conditions": [str(c).strip() for c in raw_qual if str(c).strip()],
            "population_or_dataset": None,
            "sample_size": None,
            "statistics": None,
            "temporal_or_dose": None,
        }
    elif isinstance(raw_qual, str) and raw_qual.strip():
        q_out = {
            "conditions": [raw_qual.strip()],
            "population_or_dataset": None,
            "sample_size": None,
            "statistics": None,
            "temporal_or_dose": None,
        }
    else:
        q_out = {
            "conditions": [],
            "population_or_dataset": None,
            "sample_size": None,
            "statistics": None,
            "temporal_or_dose": None,
        }

    # Auto-populate qualifier buckets from statement_text if empty
    det_q = extract_scientific_qualifiers(statement_text, exclude_outcome_deltas=True)
    if det_q.get("has_qualifiers"):
        bk = det_q["by_kind"]
        if not q_out["sample_size"] and bk.get("sample_size"):
            q_out["sample_size"] = ", ".join(bk["sample_size"])
        if not q_out["statistics"] and bk.get("significance"):
            q_out["statistics"] = ", ".join(bk["significance"])
        # Exclude delta/range temperature outcomes from ambient condition tags
        cond_temps = [
            t for t in bk.get("temperature", [])
            if not re.search(r"(?:\bby|\bto|\bfrom|至|到|升高|降低)\s+" + re.escape(t), statement_text or "", re.I)
        ]
        dose_time = bk.get("concentration_dose", []) + bk.get("time_window", []) + cond_temps + bk.get("ph", [])
        if not q_out["temporal_or_dose"] and dose_time:
            q_out["temporal_or_dose"] = ", ".join(dose_time)
        if not q_out["conditions"] and bk.get("experimental_context"):
            q_out["conditions"] = list(bk["experimental_context"])

    return q_out


def compute_trusty_np_id(doc_id, subj_norm, pred_id, obj_norm, qualifiers_dict, polarity, symmetric=False, obj_value=None, obj_unit=None):
    """Compute deterministic sha256 Trusty URI/ID independent of run order."""
    if symmetric and subj_norm > obj_norm:
        s_key, o_key = obj_norm, subj_norm
    else:
        s_key, o_key = subj_norm, obj_norm
    val_key = ""
    if obj_value is not None and obj_value != "":
        val_key = f"{obj_value}{str(obj_unit or '').strip().lower()}"
    q_parts = []
    for k in sorted(qualifiers_dict.keys()):
        v = qualifiers_dict[k]
        if isinstance(v, list):
            v_norm = "|".join(sorted(str(x).strip().lower() for x in v if str(x).strip()))
        else:
            v_norm = str(v or "").strip().lower()
        if v_norm:
            q_parts.append(f"{k}={v_norm}")
    payload = "||".join([
        str(doc_id or "D1").strip(),
        s_key,
        str(pred_id or "").strip().lower(),
        o_key,
        val_key,
        ";".join(q_parts),
        str(polarity or "affirmed").strip().lower(),
    ])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"np:sha256:{digest}"


def compute_evidence_tier(
    alignment_res,
    matched_anchors,
    evidence_quote="",
    evidence_type="direct_empirical",
    attribution_type="primary_study",
):
    """
    Deterministic 7-tier evidence hierarchy fused with Stage 1 boundary lessons:
      T1_direct_data > T2_table_figure > T3_methods_or_text > T4_abstract_summary
      > T5_indirect_inference > T6_secondary_citation > T7_unverified
    """
    status = alignment_res.get("alignment_status", "QUOTE_NOT_FOUND")
    flags = set(alignment_res.get("sufficiency_flags") or [])
    primary = matched_anchors[0] if matched_anchors else None
    q_flags = set((primary or {}).get("quality_flags") or [])

    if status == "QUOTE_NOT_FOUND" or not matched_anchors or "unextractable_asset" in q_flags:
        return "T7_unverified", 0.25, "Evidence quote could not be verbatim-aligned or target page is unextractable."

    if attribution_type == "cited_prior_work" or evidence_type == "secondary_citation":
        return "T6_secondary_citation", 0.55, "Attributed to cited prior work rather than primary empirical output."

    if evidence_type == "indirect_inference":
        return "T5_indirect_inference", 0.65, "Derived via indirect inference across statements/assumptions."

    roles = {a.get("section_role") for a in matched_anchors}
    if roles <= {"abstract", "conclusion"}:
        return "T4_abstract_summary", 0.72, "Supported only in abstract/conclusion summary; primary body/results anchor preferred."

    has_quant = (
        any(a.get("has_quantitative_signals") for a in matched_anchors)
        or any(a.get("block_type") == "formula" for a in matched_anchors)
        or any(any(ra.startswith("EQ-") for ra in (a.get("referenced_assets") or [])) for a in matched_anchors)
        or bool(QUANT_IN_TEXT_REGEX.search(evidence_quote or ""))
        or bool(THEOREM_PROOF_REGEX.search(evidence_quote or ""))
    )
    is_visual_block = any(a.get("block_type") in ("table_like", "caption") for a in matched_anchors)
    if is_visual_block or evidence_type == "table_figure_value":
        conf = 0.90 if status != "FUZZY_PARTIAL" else 0.78
        if "REQUIRES_VISUAL_TABLE_OR_FIGURE" in flags:
            conf = min(conf, 0.85)
        return "T2_table_figure", conf, "Grounded in table/figure caption or quantitative visual asset reference."

    if roles & {"results", "methods", "appendix", "body_unclassified"} and has_quant:
        conf = 0.95 if status == "EXACT_MATCH" else (0.93 if status in ("NORMALIZED_MATCH", "CROSS_BLOCK_MATCH") else 0.82)
        return "T1_direct_data", conf, f"Direct empirical/quantitative or formal theorem evidence ({status}) in {sorted(roles)}."

    has_table_fig_ref = (
        any(any(ra.startswith(("TAB-", "FIG-")) for ra in (a.get("referenced_assets") or [])) for a in matched_anchors)
        or bool(re.search(r"\b(?:Table|Tab\.|Fig\.|Figure)\s*[A-Za-z]?\d+|(?:表|图)\s*[A-Za-z]?\d+", evidence_quote, re.I))
    )
    if has_table_fig_ref:
        conf = 0.88 if status != "FUZZY_PARTIAL" else 0.76
        return "T2_table_figure", conf, "Grounded in text referencing a table/figure asset."

    conf = 0.85 if status in ("EXACT_MATCH", "NORMALIZED_MATCH") else (0.82 if status == "CROSS_BLOCK_MATCH" else 0.72)
    return "T3_methods_or_text", conf, f"Direct qualitative or methodological text evidence ({status}) in {sorted(roles)}."


def _map_contract_early_stub(output_contract, state):
    """Build honest early partial result.json preserving case_id and nested output_contract."""
    try:
        from render_deliverables import build_result_json_payload
        return build_result_json_payload(state, audit_report=None, early_partial=True)
    except Exception:
        # Minimal deterministic fallback if render_deliverables is not yet imported
        out = {
            "case_id": state["case_id"],
            "status": "partial",
            "nanopublications": state.get("nanopublications", []),
            "qa_answers": state.get("qa_answers", []),
            "documents_pubinfo": state.get("documents_pubinfo", []),
            "conflicts": state.get("conflicts", []),
            "uncertainty": state.get("uncertainty", []),
            "manual_review_checklist": state.get("manual_review_checklist", []),
            "changes": state.get("changes", []),
        }
        if isinstance(output_contract, dict):
            props = output_contract.get("properties") if isinstance(output_contract.get("properties"), dict) else output_contract
            for k in props:
                if k not in out and k not in ("type", "description", "schema_version", "required"):
                    out[k] = [] if "list" in str(props[k]).lower() or "array" in str(props[k]).lower() else {}
        return out


def init_work_state(
    ingest_manifest_path,
    structure_index_path,
    pubinfo_path,
    work_state_path,
    output_dir=None,
):
    """Initialize work_state.json and write early partial result.json to output_dir."""
    manifest = load_json_safe(ingest_manifest_path, {}) or {}
    ev_index = load_json_safe(structure_index_path, {}) or {}
    pub_bundle = load_json_safe(pubinfo_path, {}) or {}

    work_dir = os.path.dirname(os.path.abspath(structure_index_path))
    sig_data = load_json_safe(os.path.join(work_dir, "paper_signals.json"), {}) or {}

    case_id = manifest.get("case_id") or ev_index.get("case_id") or "nanopub_case"
    sources_list = pub_bundle.get("sources", [])
    pub_by_doc = {s.get("doc_id", f"D{i+1}"): s for i, s in enumerate(sources_list)}

    uncertainties = list(sig_data.get("initial_uncertainty") or [])
    manual_checklist = []
    for u in uncertainties:
        if u.get("severity") == "high":
            manual_checklist.append({
                "item_id": f"MR-INIT-{len(manual_checklist) + 1}",
                "severity": "high",
                "category": u.get("category", "extraction_risk"),
                "doc_id": u.get("doc_id"),
                "pages": u.get("pages", []),
                "reason": u.get("message", ""),
                "action_required": "Inspect original PDF pages visually before trusting extracted claims.",
            })

    # Seed honest uncertainty note for early partial stage
    init_notice = {
        "uncertainty_id": "UNC-INIT-STAGE",
        "category": "partial_pipeline_stage",
        "severity": "low",
        "doc_id": None,
        "pages": [],
        "message": "索引与来源元数据已完成，正在按阅读包增量提取原子科研断言与问答证据链。",
    }

    state = {
        "schema_version": "nanopub-work-state/1.0",
        "case_id": case_id,
        "status": "partial",
        "stage": "initialized",
        "ingest_manifest_path": os.path.abspath(ingest_manifest_path) if ingest_manifest_path else None,
        "structure_index_path": os.path.abspath(structure_index_path) if structure_index_path else None,
        "pubinfo_path": os.path.abspath(pubinfo_path) if pubinfo_path else None,
        "output_contract": manifest.get("output_contract", {}),
        "research_questions": manifest.get("research_questions", []),
        "target_formats": manifest.get("target_formats", ["json"]),
        "documents": ev_index.get("documents", manifest.get("documents", [])),
        "documents_pubinfo": sources_list,
        "pubinfo_by_doc": pub_by_doc,
        "nanopublications": [],
        "qa_answers": [],
        "conflicts": [],
        "uncertainty": uncertainties + [init_notice],
        "manual_review_checklist": manual_checklist,
        "id_redirects": {},
        "changes": [
            {
                "step": "init",
                "timestamp": _utc_now(),
                "note": f"Initialized work_state for case_id={case_id} ({len(sources_list)} doc(s), {len(manifest.get('research_questions', []))} question(s)).",
            }
        ],
    }

    os.makedirs(os.path.dirname(os.path.abspath(work_state_path)), exist_ok=True)
    with open(work_state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    if output_dir:
        out_abs = os.path.abspath(output_dir)
        os.makedirs(out_abs, exist_ok=True)
        early_result = _map_contract_early_stub(state["output_contract"], state)
        with open(os.path.join(out_abs, "result.json"), "w", encoding="utf-8") as f:
            json.dump(early_result, f, ensure_ascii=False, indent=2)

    print(f"[nanopub_assembler:init] case_id={case_id} | docs={len(sources_list)} -> {work_state_path}")
    return state


def _canonicalize_incoming_nanopub(raw_item, anchors, anchor_by_id, windows, pub_by_doc, vocab_by_id, alias_map, case_id):
    """Convert raw patch nanopub item (flat or 4-graph) into canonical Nanopublication dict."""
    # Support both nested 4-graph schema and compact LLM patch format
    if "assertion" in raw_item and isinstance(raw_item["assertion"], dict):
        asrt_in = raw_item["assertion"]
        prov_in = raw_item.get("provenance") or {}
        loc_in = prov_in.get("location") or {}
        head_in = raw_item.get("head") or {}
        raw_np_id = head_in.get("nanopub_id") or raw_item.get("np_id") or raw_item.get("id")
        doc_id = loc_in.get("doc_id") or raw_item.get("doc_id") or "D1"
        block_id = loc_in.get("block_id") or raw_item.get("block_id")
        quote = prov_in.get("evidence_quote") or raw_item.get("evidence_quote") or ""
        ev_type = prov_in.get("evidence_type") or raw_item.get("evidence_type") or "direct_empirical"
        attr_type = prov_in.get("source_attribution") or prov_in.get("attribution_type") or raw_item.get("attribution_type") or "primary_study_finding"
    else:
        asrt_in = raw_item
        prov_in = {}
        loc_in = {}
        head_in = {}
        raw_np_id = raw_item.get("np_id") or raw_item.get("nanopub_id") or raw_item.get("id")
        doc_id = raw_item.get("doc_id") or "D1"
        block_id = raw_item.get("block_id")
        quote = raw_item.get("evidence_quote") or raw_item.get("quote") or ""
        ev_type = raw_item.get("evidence_type") or "direct_empirical"
        attr_type = raw_item.get("attribution_type") or raw_item.get("source_attribution") or "primary_study_finding"

    if attr_type == "primary_study":
        attr_type = "primary_study_finding"

    statement = str(asrt_in.get("statement") or raw_item.get("statement") or "").strip()
    subj_obj = _normalize_entity_obj(asrt_in.get("subject"), default_type="entity")
    obj_obj = _normalize_entity_obj(asrt_in.get("object"), default_type="entity")
    pred_obj = normalize_predicate(asrt_in.get("predicate"), vocab_by_id, alias_map)

    polarity = str(asrt_in.get("polarity") or raw_item.get("polarity") or "affirmed").strip().lower()
    if polarity not in ("affirmed", "negated", "inconclusive"):
        polarity = "affirmed"

    qual_obj = _normalize_qualifiers(asrt_in.get("qualifiers") or raw_item.get("qualifiers"), statement_text=statement, evidence_text=quote)
    has_any_qual = bool(
        qual_obj["conditions"]
        or qual_obj["population_or_dataset"]
        or qual_obj["sample_size"]
        or qual_obj["statistics"]
        or qual_obj["temporal_or_dose"]
    )
    scope_comp = asrt_in.get("scope_completeness")
    if not scope_comp:
        scope_comp = "complete" if (qual_obj["conditions"] or qual_obj["population_or_dataset"]) and (qual_obj["statistics"] or qual_obj["sample_size"] or qual_obj["temporal_or_dose"]) else ("partial_qualifiers" if has_any_qual else "unqualified")

    # Alignment against evidence_index
    align_res = align_evidence_quote(
        quote=quote,
        anchors=anchors,
        claimed_block_id=block_id,
        doc_id=doc_id,
        same_page_windows=windows,
    )
    matched_ids = align_res.get("matched_block_ids") or []
    matched_anchors = [anchor_by_id[mid] for mid in matched_ids if mid in anchor_by_id]
    primary_anc = matched_anchors[0] if matched_anchors else anchor_by_id.get(block_id)

    effective_block_id = block_id or (matched_ids[0] if matched_ids else None)
    page_num = (
        (align_res.get("page_numbers") or [None])[0]
        or (primary_anc.get("page") if primary_anc else None)
        or loc_in.get("page")
    )
    printed_page = (
        (primary_anc.get("printed_page_span", [str(page_num or "")])[0] if primary_anc else None)
        or loc_in.get("printed_page")
        or (str(page_num) if page_num is not None else None)
    )

    ev_tier, conf, conf_basis = compute_evidence_tier(
        align_res,
        matched_anchors,
        evidence_quote=quote,
        evidence_type=ev_type,
        attribution_type=attr_type,
    )

    # Override doc_id if primary_anc resolved in a known doc
    if primary_anc and primary_anc.get("doc_id"):
        doc_id = primary_anc["doc_id"]

    # Trusty ID
    trusty_id = compute_trusty_np_id(
        doc_id=doc_id,
        subj_norm=_norm_entity_text(subj_obj),
        pred_id=pred_obj["id"],
        obj_norm=_norm_entity_text(obj_obj),
        qualifiers_dict=qual_obj,
        polarity=polarity,
        symmetric=pred_obj.get("symmetric", False),
        obj_value=obj_obj.get("value"),
        obj_unit=obj_obj.get("unit"),
    )
    uri = f"urn:nanopub:case:{case_id}:{trusty_id.replace(':', '_')}"

    doc_pub = pub_by_doc.get(doc_id) or (list(pub_by_doc.values())[0] if pub_by_doc else {
        "doc_id": doc_id,
        "title": None,
        "authors": [],
        "year": None,
        "venue": None,
        "doi": None,
        "arxiv_id": None,
        "pmid": None,
        "url": None,
        "version_or_preprint": None,
        "data_or_code_urls": [],
        "license": None,
        "field_sources": {},
        "missing_fields": [],
        "extracted_by": "task_09_nanopub/pubinfo_extractor.py",
    })

    canonical_np = {
        "head": {
            "nanopub_id": trusty_id,
            "uri": uri,
            "head_graph": f"{uri}#Head",
            "assertion_graph": f"{uri}#assertion",
            "provenance_graph": f"{uri}#provenance",
            "pubinfo_graph": f"{uri}#pubinfo",
            "schema_version": "nanopub-reading/1.0",
        },
        "assertion": {
            "statement": statement,
            "subject": subj_obj,
            "predicate": pred_obj,
            "object": obj_obj,
            "polarity": polarity,
            "qualifiers": qual_obj,
            "scope_completeness": scope_comp,
        },
        "provenance": {
            "evidence_quote": quote.strip(),
            "evidence_type": ev_type,
            "evidence_tier": ev_tier,
            "source_attribution": attr_type,
            "location": {
                "doc_id": doc_id,
                "page": page_num,
                "printed_page": printed_page,
                "block_id": effective_block_id,
                "matched_block_ids": matched_ids,
                "section_id": primary_anc.get("section_id") if primary_anc else loc_in.get("section_id"),
                "section_title": primary_anc.get("section_title") if primary_anc else loc_in.get("section_title"),
                "section_role": align_res.get("section_role") or (primary_anc.get("section_role") if primary_anc else None),
                "char_start": align_res.get("char_start"),
                "char_end": align_res.get("char_end"),
                "referenced_assets": primary_anc.get("referenced_assets", []) if primary_anc else [],
            },
            "alignment_status": align_res.get("alignment_status", "QUOTE_NOT_FOUND"),
            "match_ratio": align_res.get("match_ratio", 0.0),
            "sentence_count": align_res.get("sentence_count", 0),
            "quote_char_len": align_res.get("quote_char_len", 0),
            "sufficiency_flags": align_res.get("sufficiency_flags", []),
            "suggested_block_id": align_res.get("suggested_block_id"),
            "confidence": round(conf, 3),
            "confidence_basis": conf_basis,
            "supporting_anchors": list(prov_in.get("supporting_anchors") or []),
        },
        "pubinfo": doc_pub,
    }
    return canonical_np, raw_np_id


def patch_work_state(work_state_path, patch_json_path, structure_index_path=None):
    """Incrementally ingest a batch of nanopubs, QA answers, uncertainties, and checklists."""
    state = load_json_safe(work_state_path)
    if not state:
        raise FileNotFoundError(f"work_state.json not found: {work_state_path}")
    patch_data = load_json_safe(patch_json_path)
    if patch_data is None:
        raise FileNotFoundError(f"patch_json not found: {patch_json_path}")

    idx_path = structure_index_path or state.get("structure_index_path")
    ev_index = load_json_safe(idx_path, {"anchors": []}) or {"anchors": []}
    anchors = ev_index.get("anchors", [])
    anchor_by_id = {a.get("anchor_id"): a for a in anchors}
    windows = _build_same_page_windows(anchors)

    _vocab, vocab_by_id, alias_map = load_relation_vocab()
    pub_by_doc = state.get("pubinfo_by_doc") or {}
    case_id = state.get("case_id", "nanopub_case")

    existing_by_id = {np["head"]["nanopub_id"]: np for np in state.get("nanopublications", [])}
    id_redirects = state.setdefault("id_redirects", {})
    added_cnt, updated_cnt = 0, 0

    for raw_np in patch_data.get("nanopublications", []):
        c_np, raw_id = _canonicalize_incoming_nanopub(
            raw_np, anchors, anchor_by_id, windows, pub_by_doc, vocab_by_id, alias_map, case_id
        )
        tid = c_np["head"]["nanopub_id"]
        if raw_id and raw_id != tid:
            id_redirects[str(raw_id)] = tid

        if tid in existing_by_id:
            old_np = existing_by_id[tid]
            old_rank = (TIER_RANK.get(old_np["provenance"]["evidence_tier"], 9), ALIGN_RANK.get(old_np["provenance"]["alignment_status"], 9))
            new_rank = (TIER_RANK.get(c_np["provenance"]["evidence_tier"], 9), ALIGN_RANK.get(c_np["provenance"]["alignment_status"], 9))
            if new_rank < old_rank:
                # Promote new, keep old anchor as supporting
                c_np["provenance"]["supporting_anchors"].append({
                    "block_id": old_np["provenance"]["location"].get("block_id"),
                    "page": old_np["provenance"]["location"].get("page"),
                    "evidence_tier": old_np["provenance"]["evidence_tier"],
                    "evidence_quote": old_np["provenance"]["evidence_quote"],
                })
                existing_by_id[tid] = c_np
            else:
                old_np["provenance"]["supporting_anchors"].append({
                    "block_id": c_np["provenance"]["location"].get("block_id"),
                    "page": c_np["provenance"]["location"].get("page"),
                    "evidence_tier": c_np["provenance"]["evidence_tier"],
                    "evidence_quote": c_np["provenance"]["evidence_quote"],
                })
            updated_cnt += 1
        else:
            existing_by_id[tid] = c_np
            added_cnt += 1

    state["nanopublications"] = list(existing_by_id.values())

    # Merge QA answers (preserving order by question_id)
    qa_by_id = {q.get("question_id"): q for q in state.get("qa_answers", []) if q.get("question_id")}
    for qa in patch_data.get("qa_answers", []):
        qid = qa.get("question_id") or f"Q{len(qa_by_id) + 1}"
        sup_ids = qa.get("supporting_nanopub_ids") or qa.get("supporting_np_ids") or []
        norm_qa = {
            "question_id": qid,
            "question": qa.get("question", ""),
            "answer_status": qa.get("answer_status", "answered"),
            "answer": qa.get("answer", ""),
            "supporting_nanopub_ids": [id_redirects.get(str(x), str(x)) for x in sup_ids],
            "evidence_locations": qa.get("evidence_locations", []),
            "uncertainty_note": qa.get("uncertainty_note"),
        }
        qa_by_id[qid] = norm_qa
    state["qa_answers"] = list(qa_by_id.values())

    # Append uncertainties, checklist, changes (removing init placeholder once real content arrives)
    if state["nanopublications"] or state["qa_answers"]:
        state["uncertainty"] = [u for u in state.get("uncertainty", []) if u.get("uncertainty_id") != "UNC-INIT-STAGE"]
    for u in patch_data.get("uncertainty", []):
        state["uncertainty"].append(u)
    for m in patch_data.get("manual_review_checklist", []):
        state["manual_review_checklist"].append(m)
    for c in patch_data.get("changes", []):
        state["changes"].append(c)

    state["stage"] = "patched"
    state["changes"].append({
        "step": "patch",
        "timestamp": _utc_now(),
        "note": f"Patched +{added_cnt} new, {updated_cnt} merged nanopubs; total={len(state['nanopublications'])}, qa={len(state['qa_answers'])}.",
    })

    with open(work_state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"[nanopub_assembler:patch] +{added_cnt} new, {updated_cnt} merged -> total={len(state['nanopublications'])}")
    return state


def _qualifiers_core_key(qual):
    """Coarse qualifier key for cross-section dedup (allows merging abstract unqualified claim with results qualified claim)."""
    parts = []
    for k in ("population_or_dataset", "temporal_or_dose"):
        v = str(qual.get(k) or "").strip().lower()
        if v:
            parts.append(f"{k}={v}")
    return "|".join(parts)


def _qualifiers_compatible_for_conflict(q1, q2):
    """Check if two assertions share compatible experimental context (not separated by different dataset/dose/temp)."""
    for k in ("population_or_dataset", "temporal_or_dose"):
        v1 = str(q1.get(k) or "").strip().lower()
        v2 = str(q2.get(k) or "").strip().lower()
        if v1 and v2 and v1 != v2:
            return False
    c1 = {x.strip().lower() for x in (q1.get("conditions") or []) if x.strip()}
    c2 = {x.strip().lower() for x in (q2.get("conditions") or []) if x.strip()}
    if c1 and c2 and not (c1 & c2):
        # Distinct explicit condition tags like "in vitro" vs "in vivo" -> scoped, not conflict
        return False
    return True


def _extract_primary_number_unit(obj_dict):
    val = obj_dict.get("value")
    unit = str(obj_dict.get("unit") or "").strip().lower()
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val), unit
    lbl = str(obj_dict.get("label") or "")
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(%|ms|s|Hz|kHz|MHz|GHz|mM|μM|nM|mg/kg|°C|K)?", lbl)
    if m:
        try:
            return float(m.group(1)), (m.group(2) or unit).lower()
        except ValueError:
            pass
    return None, unit


def finalize_work_state(work_state_path):
    """
    Run cross-section deduplication & tier promotion, conflict detection graph,
    and QA evidence chain resolution.
    """
    state = load_json_safe(work_state_path)
    if not state:
        raise FileNotFoundError(f"work_state.json not found: {work_state_path}")

    nps = list(state.get("nanopublications", []))
    id_redirects = state.setdefault("id_redirects", {})
    changes = state.setdefault("changes", [])

    # 1. Cross-section Deduplication & Evidence Promotion
    groups = {}
    for np in nps:
        asrt = np["assertion"]
        prov = np["provenance"]
        loc = prov["location"]
        pred = asrt["predicate"]
        s_norm = _norm_entity_text(asrt["subject"])
        o_norm = _norm_entity_text(asrt["object"])
        if pred.get("symmetric") and s_norm > o_norm:
            s_norm, o_norm = o_norm, s_norm
        # Group within same doc_id; allow unqualified abstract/conclusion claim to merge with qualified results claim
        # by keying on (doc_id, s_norm, pred_id, o_norm, polarity) and checking compatibility
        g_key = (loc.get("doc_id", "D1"), s_norm, pred["id"], o_norm, asrt["polarity"])
        groups.setdefault(g_key, []).append(np)

    deduped = []
    promoted_count = 0
    for _gk, cand_list in groups.items():
        if len(cand_list) == 1:
            deduped.append(cand_list[0])
            continue

        # Sub-cluster by compatible qualifiers (only merge when one has empty/compatible qualifiers)
        clusters = []
        for item in cand_list:
            q_i = item["assertion"]["qualifiers"]
            placed = False
            for cl in clusters:
                q_ref = cl[0]["assertion"]["qualifiers"]
                c_i = _qualifiers_core_key(q_i)
                c_ref = _qualifiers_core_key(q_ref)
                # Do not merge quantitatively conflicting values (>15% divergence) into a duplicate
                vi, ui = _extract_primary_number_unit(item["assertion"]["object"])
                vr, ur = _extract_primary_number_unit(cl[0]["assertion"]["object"])
                nums_conflict = (
                    vi is not None
                    and vr is not None
                    and (not ui or not ur or ui == ur)
                    and abs(vi - vr) / max(abs(vi), abs(vr), 1e-9) > 0.15
                )
                if not nums_conflict and (not c_i or not c_ref or c_i == c_ref) and _qualifiers_compatible_for_conflict(q_i, q_ref):
                    cl.append(item)
                    placed = True
                    break
            if not placed:
                clusters.append([item])

        for cl in clusters:
            if len(cl) == 1:
                deduped.append(cl[0])
                continue
            cl.sort(key=lambda x: (
                TIER_RANK.get(x["provenance"]["evidence_tier"], 9),
                ALIGN_RANK.get(x["provenance"]["alignment_status"], 9),
                -len(str(x["assertion"]["qualifiers"])),
            ))
            winner = cl[0]
            win_id = winner["head"]["nanopub_id"]
            for loser in cl[1:]:
                lose_id = loser["head"]["nanopub_id"]
                if lose_id != win_id:
                    id_redirects[lose_id] = win_id
                winner["provenance"]["supporting_anchors"].append({
                    "demoted_np_id": lose_id,
                    "block_id": loser["provenance"]["location"].get("block_id"),
                    "page": loser["provenance"]["location"].get("page"),
                    "section_role": loser["provenance"]["location"].get("section_role"),
                    "evidence_tier": loser["provenance"]["evidence_tier"],
                    "evidence_quote": loser["provenance"]["evidence_quote"],
                })
                promoted_count += 1
                changes.append({
                    "step": "finalize_dedup_promote",
                    "timestamp": _utc_now(),
                    "winner_np_id": win_id,
                    "demoted_np_id": lose_id,
                    "note": f"Merged duplicate claim from {loser['provenance']['location'].get('section_role')} ({loser['provenance']['evidence_tier']}) into higher-tier {winner['provenance']['location'].get('section_role')} ({winner['provenance']['evidence_tier']}).",
                })
            deduped.append(winner)

    # 2. Conflict Detection Graph (intra-doc & cross-doc)
    conflicts = []
    seen_conf_pairs = set()
    for i in range(len(deduped)):
        for j in range(i + 1, len(deduped)):
            n1, n2 = deduped[i], deduped[j]
            a1, a2 = n1["assertion"], n2["assertion"]
            p1, p2 = a1["predicate"], a2["predicate"]
            s1, o1 = _norm_entity_text(a1["subject"]), _norm_entity_text(a1["object"])
            s2, o2 = _norm_entity_text(a2["subject"]), _norm_entity_text(a2["object"])

            same_pair = (s1 == s2 and o1 == o2) or ((p1.get("symmetric") or p2.get("symmetric")) and s1 == o2 and o1 == s2)
            if not same_pair:
                continue
            if not _qualifiers_compatible_for_conflict(a1["qualifiers"], a2["qualifiers"]):
                continue

            id1, id2 = n1["head"]["nanopub_id"], n2["head"]["nanopub_id"]
            pair_key = tuple(sorted((id1, id2)))
            if pair_key in seen_conf_pairs:
                continue

            is_pol_conflict = False
            if p1["id"] == p2["id"] and a1["polarity"] != a2["polarity"] and "inconclusive" not in (a1["polarity"], a2["polarity"]):
                is_pol_conflict = True
            elif frozenset((p1["id"], p2["id"])) in OPPOSING_PREDICATE_PAIRS and a1["polarity"] == a2["polarity"]:
                is_pol_conflict = True
            elif (
                (p1["id"] == "no_significant_effect_on" and p2["id"] in DIRECTIONAL_EFFECT_PREDICATES and a2["polarity"] == "affirmed")
                or (p2["id"] == "no_significant_effect_on" and p1["id"] in DIRECTIONAL_EFFECT_PREDICATES and a1["polarity"] == "affirmed")
            ):
                is_pol_conflict = True

            if is_pol_conflict:
                seen_conf_pairs.add(pair_key)
                conf_id = f"CONF-{len(conflicts) + 1:03d}"
                conflicts.append({
                    "conflict_id": conf_id,
                    "conflict_type": "polarity_conflict",
                    "np_ids": [id1, id2],
                    "description": f"Opposing claims on ({a1['subject']['label']}, {a1['object']['label']}): '{p1['id']}' ({a1['polarity']}) vs '{p2['id']}' ({a2['polarity']}).",
                    "evidence_anchors": [
                        {"np_id": id1, "doc_id": n1["provenance"]["location"]["doc_id"], "page": n1["provenance"]["location"]["page"], "block_id": n1["provenance"]["location"]["block_id"]},
                        {"np_id": id2, "doc_id": n2["provenance"]["location"]["doc_id"], "page": n2["provenance"]["location"]["page"], "block_id": n2["provenance"]["location"]["block_id"]},
                    ],
                    "resolution_status": "unresolved_reported",
                })
                continue

            # Quantitative conflict check
            if p1["id"] == p2["id"] and a1["polarity"] == a2["polarity"]:
                v1, u1 = _extract_primary_number_unit(a1["object"])
                v2, u2 = _extract_primary_number_unit(a2["object"])
                if v1 is not None and v2 is not None and (not u1 or not u2 or u1 == u2):
                    denom = max(abs(v1), abs(v2), 1e-9)
                    if abs(v1 - v2) / denom > 0.15:
                        seen_conf_pairs.add(pair_key)
                        conf_id = f"CONF-{len(conflicts) + 1:03d}"
                        conflicts.append({
                            "conflict_id": conf_id,
                            "conflict_type": "quantitative_conflict",
                            "np_ids": [id1, id2],
                            "description": f"Quantitative discrepancy on ({a1['subject']['label']}, {p1['id']}, {a1['object']['label']}): {v1}{u1} vs {v2}{u2}.",
                            "evidence_anchors": [
                                {"np_id": id1, "doc_id": n1["provenance"]["location"]["doc_id"], "page": n1["provenance"]["location"]["page"], "block_id": n1["provenance"]["location"]["block_id"]},
                                {"np_id": id2, "doc_id": n2["provenance"]["location"]["doc_id"], "page": n2["provenance"]["location"]["page"], "block_id": n2["provenance"]["location"]["block_id"]},
                            ],
                            "resolution_status": "unresolved_reported",
                        })

    # Add conflicts to manual_review_checklist (dedup by conflict_id)
    existing_mr_ids = {m.get("conflict_id") for m in state.get("manual_review_checklist", []) if m.get("conflict_id")}
    for c in conflicts:
        if c["conflict_id"] not in existing_mr_ids:
            state["manual_review_checklist"].append({
                "item_id": f"MR-{c['conflict_id']}",
                "conflict_id": c["conflict_id"],
                "severity": "high",
                "category": c["conflict_type"],
                "np_ids": c["np_ids"],
                "reason": c["description"],
                "action_required": "Verify conflicting source spans and determine whether conditions/populations differ.",
            })

    # 3. Resolve QA answers evidence chains via id_redirects
    np_by_id = {n["head"]["nanopub_id"]: n for n in deduped}
    for qa in state.get("qa_answers", []):
        resolved_ids = []
        seen_q_np = set()
        for raw_sid in qa.get("supporting_nanopub_ids", []):
            curr = str(raw_sid)
            # Follow redirect chain safely
            for _ in range(5):
                if curr in id_redirects and id_redirects[curr] != curr:
                    curr = id_redirects[curr]
                else:
                    break
            if curr in np_by_id and curr not in seen_q_np:
                seen_q_np.add(curr)
                resolved_ids.append(curr)
        qa["supporting_nanopub_ids"] = resolved_ids

        # Rebuild grounded evidence_locations from canonical nanopubs
        ev_locs = []
        for sid in resolved_ids:
            p = np_by_id[sid]["provenance"]
            loc = p["location"]
            ev_locs.append({
                "nanopub_id": sid,
                "doc_id": loc.get("doc_id"),
                "page": loc.get("page"),
                "block_id": loc.get("block_id"),
                "section_role": loc.get("section_role"),
                "evidence_tier": p.get("evidence_tier"),
                "evidence_quote": p.get("evidence_quote"),
            })
        qa["evidence_locations"] = ev_locs

        # Check if any supporting np is involved in a detected conflict
        conf_hit = [c["conflict_id"] for c in conflicts if set(c["np_ids"]) & set(resolved_ids)]
        if conf_hit and qa.get("answer_status") == "answered":
            qa["answer_status"] = "conflicting_evidence"
            qa["uncertainty_note"] = (qa.get("uncertainty_note") or "") + f" [Detected conflicts: {', '.join(conf_hit)}]"

    # 4. Resolve any temporary patch IDs in changes, manual_review_checklist, and qa_answers prose
    def _follow_redirect(raw_id):
        curr = str(raw_id)
        for _ in range(6):
            if curr in id_redirects and id_redirects[curr] != curr:
                curr = id_redirects[curr]
            else:
                break
        return curr

    temp_redirects = {
        str(k): _follow_redirect(v)
        for k, v in id_redirects.items()
        if k and not str(k).startswith("np:sha256:")
    }
    if temp_redirects:
        sorted_temp_keys = sorted(temp_redirects.keys(), key=len, reverse=True)
        temp_pat = re.compile("|".join(re.escape(k) for k in sorted_temp_keys))

        def _sub_temp_text(txt):
            if not txt or not isinstance(txt, str):
                return txt
            return temp_pat.sub(lambda m: temp_redirects.get(m.group(0), m.group(0)), txt)

        for chg in changes:
            for fk in ("from_id", "to_id"):
                if fk in chg and chg[fk] in temp_redirects:
                    chg[fk] = temp_redirects[chg[fk]]
            if isinstance(chg.get("affected_ids"), list):
                chg["affected_ids"] = [temp_redirects.get(str(x), str(x)) for x in chg["affected_ids"]]
            for tk in ("description", "note"):
                if tk in chg and isinstance(chg[tk], str):
                    chg[tk] = _sub_temp_text(chg[tk])

        for mr in state.get("manual_review_checklist", []):
            if isinstance(mr.get("np_ids"), list):
                mr["np_ids"] = [temp_redirects.get(str(x), str(x)) for x in mr["np_ids"]]
            for tk in ("reason", "action_required"):
                if tk in mr and isinstance(mr[tk], str):
                    mr[tk] = _sub_temp_text(mr[tk])

        for qa in state.get("qa_answers", []):
            for tk in ("answer", "uncertainty_note"):
                if tk in qa and isinstance(qa[tk], str):
                    qa[tk] = _sub_temp_text(qa[tk])

    state["nanopublications"] = deduped
    state["conflicts"] = conflicts
    state["stage"] = "finalized"
    state["changes"].append({
        "step": "finalize",
        "timestamp": _utc_now(),
        "note": f"Finalized {len(deduped)} canonical nanopubs (merged {promoted_count} duplicates), {len(conflicts)} conflict(s), {len(state.get('qa_answers', []))} QA answer(s).",
    })

    with open(work_state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(
        f"[nanopub_assembler:finalize] canonical_nps={len(deduped)} | merged={promoted_count} "
        f"| conflicts={len(conflicts)} | qa={len(state.get('qa_answers', []))}"
    )
    return state


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Incremental Nanopublication Assembler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize work_state.json and write early partial result.json")
    p_init.add_argument("--ingest-manifest", required=True, help="Path to case_manifest.json / ingest_manifest.json")
    p_init.add_argument("--structure-index", required=True, help="Path to evidence_index.json")
    p_init.add_argument("--pubinfo", required=True, help="Path to pubinfo.json")
    p_init.add_argument("--work-state", required=True, help="Path to work_state.json")
    p_init.add_argument("--output-dir", default=None, help="Output directory for early fallback result.json")

    p_patch = sub.add_parser("patch", help="Incrementally patch nanopubs/qa into work_state.json")
    p_patch.add_argument("--work-state", required=True, help="Path to work_state.json")
    p_patch.add_argument("--patch-json", required=True, help="Path to patch JSON file")
    p_patch.add_argument("--structure-index", default=None, help="Optional override path to evidence_index.json")

    p_fin = sub.add_parser("finalize", help="Deduplicate, promote evidence tiers, build conflict graph & resolve QA")
    p_fin.add_argument("--work-state", required=True, help="Path to work_state.json")

    args = parser.parse_args()
    if args.cmd == "init":
        init_work_state(
            ingest_manifest_path=args.ingest_manifest,
            structure_index_path=args.structure_index,
            pubinfo_path=args.pubinfo,
            work_state_path=args.work_state,
            output_dir=args.output_dir,
        )
    elif args.cmd == "patch":
        patch_work_state(
            work_state_path=args.work_state,
            patch_json_path=args.patch_json,
            structure_index_path=args.structure_index,
        )
    elif args.cmd == "finalize":
        finalize_work_state(work_state_path=args.work_state)


if __name__ == "__main__":
    main()
