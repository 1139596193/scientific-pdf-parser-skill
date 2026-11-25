# Patch Schema, Field Dictionary & Contract Mapping Reference

## 1. JSON Patch Templates (`/app/work/patches/*.json`)

`note_assembler.py` reads `*.json` patches in sorted filename order and merges them incrementally by `section_id`, `kp_id`, and `result_id`.

### `01_paper_card.json`
```json
{
  "paper_card": {
    "venue_or_source": {"value": "not_reported_in_source", "provenance_status": "not_reported_in_source"},
    "one_line_takeaway": {"value": "...", "anchor_ids": ["P1-B005", "P13-B008"]},
    "research_problem": {"value": "...", "anchor_ids": ["P1-B005"]},
    "research_gap": {"value": "...", "anchor_ids": ["P1-B009"]},
    "methodology_summary": {"value": "...", "anchor_ids": ["P6-B017", "P7-B009"]},
    "limitations_and_assumptions": {"value": "...", "anchor_ids": ["P13-B002", "P13-B008"]},
    "reusability_and_takeaways": {"value": "...", "anchor_ids": ["P12-B016"]},
    "key_results": [
      {
        "result_id": "RES-001",
        "metric": "Accuracy",
        "value": "84.3%",
        "baseline": "Standard QDP",
        "condition": "n up to 10 bits",
        "summary": "Achieves 84.3% under phase oracle evaluation.",
        "anchor_ids": ["P11-B014"],
        "verbatim_quote": "exact short excerpt from source anchor"
      }
    ],
    "tags": ["quantum-privacy", "differential-privacy"]
  }
}
```

### `02_sections.json`
```json
{
  "section_summaries": [
    {
      "section_id": "S04",
      "summary": "Concise narrative summary of section S04 preserving argumentative flow.",
      "key_points": [
        {
          "text": "Atomic claim supported by the source anchor.",
          "anchor_ids": ["P1-B009"],
          "verbatim_quote": "short exact excerpt in source language"
        }
      ]
    }
  ]
}
```

### `03_knowledge_points.json`
```json
{
  "knowledge_points": [
    {
      "kp_id": "KP-001",
      "category": "method_mechanism",
      "title": "Entanglement Amplification Calibration",
      "claim": "Select mechanisms with small entanglement amplification and calibrate by EA-QDP parameter.",
      "detail": "Confirmed across simulation and hardware runs; see Table 3.",
      "anchor_ids": ["P12-B016"],
      "verbatim_quote": "choose a mechanism with small entanglement amplification",
      "tags": ["calibration", "defense"]
    }
  ]
}
```

### `04_cross_doc.json` (Multi-Document Cases Only)
```json
{
  "cross_document_synthesis": {
    "comparison_table": [
      {
        "dimension": "Threat Model",
        "D1": "Hidden entangled reference system",
        "D2": "Multi-tenant crosstalk noise",
        "anchor_ids": ["D1-P1-B005", "D2-P1-B004"]
      }
    ],
    "synthesis_notes": [
      {
        "note": "D1 addresses adversarial entanglement while D2 models hardware multi-tenancy.",
        "anchor_ids": ["D1-P1-B005", "D2-P1-B004"]
      }
    ]
  }
}
```

## 2. Standard Enumerations

- **`provenance_status`**:
  - `reported`: Explicitly stated in cited source anchors.
  - `partially_reported`: Indirectly or incompletely stated; note missing conditions in `uncertainties`.
  - `not_reported_in_source`: Absent from source material. Any placeholder (`""`, `TBD`, `N/A`, `Unknown`, `None`, `待定`, `未知`) is automatically normalized to `not_reported_in_source`.
- **`knowledge_points.category`** (7 canonical types):
  1. `concept_definition` — Formal definitions, terminology, problem formulations
  2. `method_mechanism` — Architectures, workflows, algorithms, defense/attack mechanisms
  3. `formula_algorithm` — Key equations, complexity bounds, optimization formulations
  4. `empirical_finding` — Quantitative results, ablation outcomes, observed phenomena
  5. `dataset_metric` — Benchmarks, datasets, experimental parameters, evaluation metrics
  6. `limitation_risk` — Assumptions, failure modes, threats to validity, hardware limits
  7. `future_direction` — Open problems, stated future extensions
- **Anchor ID Format**:
  - Single-document case: `P{page}-B{seq:03d}` (e.g. `P3-B014`).
  - Multi-document case: `D{doc_idx}-P{page}-B{seq:03d}` (e.g. `D2-P1-B004`).

## 3. Output Contract & Status Gate (`result.json`)

- `render_deliverables.py` preserves the exact `case_id` and recursively populates every field declared in `/input/case.json -> output_contract` (supporting JSON Schema `properties`/`required` as well as flat or nested type maps).
- Canonical fields (`paper_card`, `section_summaries`, `knowledge_points`, `cross_document_synthesis`, `sources_index`, `uncertainty`, `quality_notices`, `manual_review_checklist`, `evidence_audit_summary`, `change_log`, `artifacts`) are always included alongside mapped `output_contract` fields.
- **`status` Gate**:
  - `"success"`: Only emitted when `evidence_audit.json` passes with `blocking_issues_count == 0`, `body_section_coverage >= 0.85`, and `has_unreadable_source == false`.
  - `"partial"`: Emitted automatically whenever unreadable scanned pages exist, blocking audit issues remain, or body coverage `< 0.85`, accompanied by `status_reason` and `manual_review_checklist`.
