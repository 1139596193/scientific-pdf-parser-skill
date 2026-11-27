# Literature Knowledge Graph Quality Rules & 18-Rule Audit Specification

This document defines the deterministic audit rules enforced by `scripts/kg_auditor.py`, the 4-tier epistemic boundaries, cross-paper isolation rules, and hotfix remediation patterns.

---

## 1. Honest Status Semantics (`blocker` vs `scientific_finding`)

- **`status = "failed"`**: Zero extractable text units across all input files.
- **`status = "partial"`**: Any `HIGH` severity `blocker` issue is active, graph lacks nodes/edges, `< 3` grounded queries, or source coverage is blocked by unreadable files.
- **`status = "success"`**: `high_blocker_count == 0`, all nodes and edges align verbatim to source anchors, independent paper metadata is strictly isolated, and `>= 3` queries are graph-grounded.
- **Important**: Preserved scientific conflicts (`SCIENTIFIC_CONFLICT_PRESERVED`) and honestly answered `unreported` boundary queries are `scientific_finding` items and **NEVER** downgrade `status` from `success` to `partial`.

---

## 2. The 18 Deterministic Audit Rules (`kg_auditor.py`)

| # | Audit Code | Severity | Category | Trigger Condition | 1-Round `hotfix` Action |
|-|-|-|-|-|-|
| 1 | `ROOT_METADATA_LEAKAGE` | `HIGH` | `blocker` | Secondary paper inherits `case.json` root DOI/title/authors or shares DOI/title across distinct hashes | Fix via `pubinfo_extractor` manifest/XML; ensure `secondary` never copies primary metadata |
| 2 | `FAKE_PDF_PAGE_IN_JATS` | `HIGH` | `blocker` | `locator_scheme=="jats_paragraph"` and node/edge/citation carries non-null `page_number` or `"page 1"` | Replace record via `replaces_id` with `page_number` omitted/`null` and exact `paragraph_id` |
| 3 | `SAME_PAPER_MULTI_FORMAT_AS_CROSS_PAPER` | `HIGH` | `blocker` | `secondary-material.json` + `secondary-publisher.xml` (`verification_twin`) claimed as cross-paper merge/evidence | Remove invalid `merge_decision` (`remove_merge_ids`) and restrict node `source_ids` |
| 4 | `UNGROUNDED_NODE` | `HIGH` | `blocker` | Node lacks `evidence` or `verbatim_quote` fails alignment (`QUOTE_NOT_FOUND` / empty quote) | Copy exact substring from `inspect` / `anchors_compact.txt` and patch via `replaces_id` |
| 5 | `UNGROUNDED_EDGE` | `HIGH` | `blocker` | Edge lacks `evidence` or `verbatim_quote` fails alignment to its source block | Copy exact substring and locator via `replaces_id` |
| 6 | `DANGLING_EDGE_ENDPOINT` | `HIGH` | `blocker` | Edge `subject_id` or `object_id` missing from `nodes` | Add missing node or remove/replace dangling edge |
| 7 | `INVALID_ENTITY_TYPE` | `HIGH` | `blocker` | Node `entity_type` outside the 8 controlled types in `kg_ontology.json` | Update `entity_type` via `replaces_id` |
| 8 | `INVALID_RELATION_TYPE` | `HIGH` | `blocker` | Edge `relation` outside 35 controlled predicates, `co_occurrence_only` used, or invalid `epistemic_status` | Replace with valid ontology predicate and 1 of 4 valid epistemic levels |
| 9 | `RELATION_TYPE_CONSTRAINT_VIOLATION` | `MEDIUM` | `quality` | Subject/object `entity_type` violates ontology `allowed_subject_types` / `allowed_object_types` | Check relation direction or entity type |
| 10 | `EPISTEMIC_UPGRADE_VIOLATION` | `HIGH` | `blocker` | Quote is in `references`, `introduction` with citation markers, or speculative text (`may`/`might`/`推测`), yet labeled `direct_experimental` | Downgrade `epistemic_status` to `reported_prior_work` or `author_speculation` via `replaces_id` |
| 11 | `CAUSAL_STRENGTH_OVERCLAIM` | `HIGH` | `blocker` | Associative/hedged quote (`correlates with`, `associated with`, `suggests`) upgraded to causal predicate (`causes`, `inhibits`, `activates`, `promotes`, `suppresses`) | Downgrade predicate to `correlates_with` / `associated_with` via `replaces_id` |
| 12 | `GENERIC_TERM_OVERMERGE` | `HIGH` | `blocker` | Cross-paper generic/context-sensitive term (`control group`, `catalyst`, `CNN`, `baseline`, `dataset`, `mice`, `patients`, `accuracy`, etc.) merged into one node | Split into paper-scoped nodes (`N-P1`, `N-P2`), remove bad merge (`remove_merge_ids`), log in `non_merge_decisions` |
| 13 | `UNLOGGED_DUPLICATE_SURFACE` | `MEDIUM` | `quality` | Multiple nodes share canonical name without merge or `non_merge_decisions` entry | Auto-logged by `kg_assembler` or add explicit `non_merge_decisions` |
| 14 | `QUERY_GRAPH_DRIFT` | `HIGH` | `blocker` | Query claims non-existent `node_id`/`edge_id` (`drift_detected`) or answered with zero provenance citations | Update query target IDs or mark `expected_unreported=true` |
| 15 | `INSUFFICIENT_QUERY_COVERAGE` | `HIGH`/`MEDIUM` | `blocker`/`quality` | Fewer than 3 queries executed on non-empty graph (`HIGH` if 0, `MEDIUM` if 1–2) | Add queries (`kg_query_engine` auto-synthesizes up to 3) |
| 16 | `ORPHAN_NODE_WARNING` | `MEDIUM` | `quality` | Node has 0 connected edges | Connect via evidence-grounded edge or prune unused node |
| 17 | `SOURCE_COVERAGE_BLOCKER` | `HIGH` | `blocker` | Critical input file missing or scanned PDF yielded `unextractable_asset` with 0 readable text | Inspect rendered PNG (`220 DPI`) or report honest `partial` |
| 18 | `SCIENTIFIC_CONFLICT_PRESERVED` | `INFO` | `scientific_finding` | Cross-paper or intra-paper polarity/quantitative conflicts preserved with `preserved_without_averaging` | Informational confirmation (never downgrades `status`) |

---

## 3. Epistemic Stratification & Non-Merge Guardrails

1. **`direct_experimental`**: Only for first-hand measurements/experiments/computations reported by the paper's own authors (typically `methods`/`results`).
2. **`reported_prior_work`**: Mandatory when quoting `introduction`, `related work`, or any sentence carrying `[12]` / `(Author, Year)` citation attribution.
3. **`author_speculation`**: Mandatory for hedged hypotheses (`may`, `might`, `could`, `suggests`, `hypothesize`, `可能`, `推测`).
4. **`definition_or_setup`**: Terminology definitions, protocol setup (`uses_method`, `evaluates_on`, `targets_task`), and structural composition.
5. **Non-Merge Mandate**: Generic nouns (`control group`, `baseline`, `catalyst`, `sample`, `mice`, `patients`, `CNN`, `ResNet`, `transformer`, `dataset`, `accuracy`, `model`, `algorithm`, `对照组`, `催化剂`, `模型`, `数据集`) represent distinct physical samples or trained instances in separate studies. Always split by `source_id` and record in `non_merge_decisions`.
