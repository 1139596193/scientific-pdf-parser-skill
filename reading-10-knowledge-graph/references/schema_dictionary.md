# Literature Knowledge Graph Schema Dictionary (`task_10_kg`)

Complete data dictionary for `patch` batches, assembled graph deliverables (`knowledge_graph.json`, `result.json`), GraphML attributes, CSV exports, and dual-locator evidence coordinates.

---

## 1. Dual-Locator Coordinate System

| Mode | `ingest_mode` | `locator_scheme` | `anchor_id` Format | `paragraph_id` | `page_number` |
|-|-|-|-|-|-|
| **Mode A (JATS Dual-Paper Corpus)** | `jats_corpus` | `jats_paragraph` | `{source_id}::{paragraph_id}` | Required (e.g. `p-002`, `sec2-p01`) | **Strictly `null`** (never fabricate `1`) |
| **Mode B (PDF / Text Files)** | `document_files` | `pdf_page_block` / `text_block` | `P{page}-B{seq:03d}` / `{doc_id}-P{page}-B{seq:03d}` | `null` | Integer physical page (`>= 1`) |

---

## 2. `patch` Batch JSON Schema (`draft_batches/batch_NNN_<stage>.json`)

Top-level keys (all arrays default to `[]` if omitted):
- `batch_id` (str, optional): Auto-assigned `B001`, `B002`, ... if omitted.
- `stage` (str): Recommended values `v1_primary`, `v2_secondary_update`, `hotfix`.
- `nodes` (`list[Node]`): Entity nodes to insert or replace (`replaces_id`).
- `edges` (`list[Edge]`): Directed relation edges to insert or replace (`replaces_id`).
- `merge_decisions` (`list[MergeDecision]`): Explicit justification for cross-paper or intra-paper entity merges.
- `non_merge_decisions` (`list[NonMergeDecision]`): Explicit isolation log for homonyms/generic context-sensitive terms kept separate.
- `conflicts` (`list[Conflict]`): Explicit cross-paper or intra-paper scientific discrepancies (`resolution_policy="preserved_without_averaging"`).
- `queries` (`list[Query]`): Research queries executed deterministically by `kg_query_engine.py` during `finish`.
- `remove_node_ids`, `remove_edge_ids`, `remove_merge_ids`, `remove_non_merge_ids`, `remove_conflict_ids`, `remove_query_ids` (`list[str]`): Hotfix deletion lists.

### 2.1 `Node` Fields
| Field | Type | Required | Constraints / Notes |
|-|-|-|-|
| `node_id` | `str` | Yes | Deterministic ID, e.g. `N-001`, `N-P1-01` |
| `canonical_name` | `str` | Yes | Grounded surface/canonical label from paper text |
| `entity_type` | `str` | Yes | Controlled 8-enum: `Researcher`, `Material_or_Chemical`, `Method_or_Model`, `Task_or_Problem`, `Dataset_or_Sample`, `Metric_or_Property`, `Experimental_Condition`, `Finding_or_Claim` |
| `aliases` | `list[str]` | No | Single `str` auto-coerced to `[str]` |
| `source_ids` | `list[str]` | Yes | Independent paper IDs (`primary`, `secondary`, `D1`); single `str` auto-coerced; never include `verification_twin` XML as separate paper |
| `context_scope` | `str` | Recommended | Species, batch, dataset, temperature/pH scope |
| `acronym_expansion` | `str` | No | Verified full name when merging `Full Name (ACR)` |
| `evidence` | `list[Evidence]` | Yes | Single `dict` auto-wrapped to `[dict]`; >= 1 aligned `verbatim_quote` |
| `replaces_id` | `str` | No | Hotfix replacement target `node_id` |

### 2.2 `Edge` Fields
| Field | Type | Required | Constraints / Notes |
|-|-|-|-|
| `edge_id` | `str` | Yes | Deterministic ID, e.g. `E-001` |
| `subject_id` | `str` | Yes | Must exist in `nodes` |
| `relation` | `str` | Yes | One of 35 controlled predicates in `references/kg_ontology.json` (`co_occurrence_only` forbidden) |
| `object_id` | `str` | Yes | Must exist in `nodes` |
| `direction` | `str` | No | Defaults to `subject_to_object` |
| `epistemic_status` | `str` | Yes | Controlled 4-enum: `direct_experimental`, `reported_prior_work`, `author_speculation`, `definition_or_setup` |
| `polarity` | `str` | No | `positive`, `negative`, `null_effect`, `neutral` (defaults from ontology) |
| `qualifiers` | `dict \| list` | Recommended | Temperature, pH, dose, sample size `n`, `p`-value, regime |
| `quantitative_value` | `str` | No | Exact reported metric/value & unit from text |
| `source_id` | `str` | Yes | Independent source paper ID (`primary`, `secondary`, `D1`) |
| `evidence` | `list[Evidence]` | Yes | Single `dict` auto-wrapped; inherits unique outer `source_id` if omitted |
| `replaces_id` | `str` | No | Hotfix replacement target `edge_id` |

### 2.3 `Evidence` Object
- `source_id` (`str`): Independent paper ID (`primary`, `secondary`, `D1`).
- `paragraph_id` (`str`, Mode A) or `anchor_id` (`str`, Mode B): Exact paragraph/block locator.
- `verbatim_quote` (`str`): Contiguous verbatim substring from the anchor text.
- `page_number` (`int | null`): Must be `null` (or omitted) in `jats_paragraph` mode.

### 2.4 `MergeDecision`, `NonMergeDecision`, `Conflict`, and `Query`
- **`MergeDecision`**: `decision_id`, `canonical_node_id`, `merged_mentions`, `merged_source_ids`, `merge_basis`, `evidence_refs`.
- **`NonMergeDecision`**: `decision_id`, `node_a_id`, `node_b_id`, `surface_term`, `reason_code`, `explanation`, `evidence_refs`.
- **`Conflict`**: `conflict_id`, `conflict_scope` (`cross_paper` | `intra_paper`), `conflict_type` (`polarity_opposite` | `quantitative_discrepancy` | `condition_mismatch` | `metadata_discrepancy`), `involved_node_ids`, `involved_edge_ids`, `summary`, `resolution_policy` (`"preserved_without_averaging"`).
- **`Query`**: `query_id`, `query_type` (6 templates in `query_templates.json`), `question`, `target_entities`, `target_relations`, `expected_unreported` (`bool`), optional `claimed_node_ids`/`claimed_edge_ids`.

---

## 3. Output Deliverables Schema (`<OUTPUT_DIR>`)

1. **`result.json`**:
   - Preserves `case_id` and all `output_contract` fields from `/input/case.json`.
   - Core fields: `status` (`success` | `partial` | `failed`), `status_reasons`, `ingest_mode`, `locator_scheme`, `independent_paper_count`, `documents` (per-paper isolated metadata), `verification_twins`, `kg_summary`, `queries`, `merge_decisions`, `non_merge_decisions`, `conflicts`, `incremental_update_diff`, `limitations_and_unreported`, `artifacts`, `audit_summary`.
2. **`knowledge_graph.json`**:
   - Complete canonical graph with aligned `evidence` details, `incremental_update_diff` (`v1_primary_counts`, `v2_total_counts`, `added_node_ids`, `added_edge_ids`, `cross_paper_merged_node_ids`, `rejected_non_merge_pairs`, `newly_introduced_conflict_ids`, `hotfix_operations`), and grounded `queries`.
3. **`knowledge_graph.graphml`**:
   - GraphML 1.0 XML (`edgedefault="directed"`). Node keys: `label`, `entity_type`, `source_ids`, `context_scope`. Edge keys: `relation`, `epistemic_status`, `polarity`, `source_id`, `locator`, `quote`.
4. **`nodes.csv` & `edges.csv`** (UTF-8 without BOM, LF):
   - `nodes.csv`: `node_id, canonical_name, entity_type, aliases, independent_source_ids, is_cross_paper_shared, context_scope, primary_source_id, primary_locator, primary_page_number, verbatim_quote`.
   - `edges.csv`: `edge_id, subject_id, relation, object_id, direction, epistemic_status, polarity, quantitative_value, qualifiers, source_id, locator, page_number, verbatim_quote`.
5. **`knowledge_graph_report.md`**, **`kg_overview.svg`**, and (when `pymupdf` is available) **`kg_overview.png`**.
