# Canonical Nanopublication + Grounded QA Data Dictionary

本文档定义 `task_09_nanopub` 产出的标准化机器可读结构（`result.json` 及导出件 `nanopublications.jsonld` / `nanopublications.trig` / `nanopublications.csv`）的字段契约、数据类型、必填性与取值枚举。

---

## 1. 顶层结构（`result.json`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 必填 | 来自 `/input/case.json` 或语义推导的案例标识，必须原样保留。 |
| `status` | enum | 必填 | `success` \| `partial` \| `unanswerable` \| `failed`。材料缺失、扫描不可读或校验阻断时不得虚报 `success`。 |
| `sources` | array[object] | 必填 | 参与分析的源文档元数据清单（对应 `pubinfo_extractor.py` 输出，含来源定位与缺失字段）。 |
| `nanopublications` | array[object] | 必填 | 原子化 Nanopublication 列表（见第 2 节）。 |
| `qa_answers` | array[object] | 条件必填 | 若输入含显式科研问题（`research_questions`），逐题给出基于 Nanopub 断言的回答与依据（见第 3 节）。 |
| `conflicts` | array[object] | 必填 | 检测到的材料内部/跨文献冲突记录（无冲突时为 `[]`）。 |
| `uncertainty` | array[object] | 必填 | 未报告、无法判断、OCR/字体风险、证据不足等诚实不确定性记录。 |
| `manual_review_checklist` | array[object] | 必填 | 需人工接管复核的具体项（如 `QUOTE_NOT_FOUND`、跨图表待视觉确认、`T6_secondary_citation`）。 |
| `audit_summary` | object | 必填 | 确定性审计统计汇总（通过率、拦截数、证据强度分布、因果升级告警数等）。 |
| `change_records` | array[object] | 必填 | 自动化修复/归一/降级记录（如块号偏移自愈、谓词归一映射、冲突标灰等）。 |

> 注意：若 `/input/case.json` 定义了 `output_contract`，所有原始 `output_contract` 字段必须在 `result.json` 顶层完整保留并回填实际结果。

---

## 2. Nanopublication 四块核心结构（`nanopublications[]`）

每条记录对应一个 **单一、独立可判真假** 的原子科研断言（遵循 W3C/Nanopub 四命名图模型：`head` / `assertion` / `provenance` / `pubinfo`）：

### 2.1 `head`（包头与图索引）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `nanopub_id` | string | 必填 | 全局唯一标识，如 `NP-D1-0001`（或 `NP-0001`）。 |
| `uri` | string | 必填 | 可解析逻辑 URI，如 `urn:nanopub:case:<case_id>:NP-0001`。 |
| `head_graph` | string | 必填 | `<uri>#Head` |
| `assertion_graph` | string | 必填 | `<uri>#assertion` |
| `provenance_graph` | string | 必填 | `<uri>#provenance` |
| `pubinfo_graph` | string | 必填 | `<uri>#pubinfo` |
| `schema_version` | string | 必填 | 固定为 `"nanopub-reading/1.0"`。 |

### 2.2 `assertion`（原子断言三元组 + 限定范围）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `statement` | string | 必填 | 一句独立完整、可单独判断真假的原子陈述（包含主谓宾与必要限定条件）。 |
| `subject` | object | 必填 | `{"label": string, "entity_type": string, "normalized_id": string\|null}` |
| `predicate` | object | 必填 | `{"id": string, "uri": string, "category": string, "raw_verb": string\|null}`；`id` 必须属于 `relation_vocab.json`。 |
| `object` | object | 必填 | `{"label": string, "entity_type": string, "value": number\|string\|null, "unit": string\|null}` |
| `polarity` | enum | 必填 | `affirmed` \| `negated` \| `inconclusive`。 |
| `qualifiers` | object | 必填 | `{"conditions": [string], "population_or_dataset": string\|null, "sample_size": string\|null, "statistics": string\|null, "temporal_or_dose": string\|null}`。 |
| `scope_completeness` | enum | 必填 | `complete` \| `partial_qualifiers` \| `unqualified`。 |

### 2.3 `provenance`（最小充分证据绑定与定位）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `evidence_quote` | string | 必填 | 原文逐字精确引文（1–4 句为宜，严禁整段大块倾倒或人工改写）。 |
| `evidence_type` | enum | 必填 | `direct_empirical` \| `table_figure_value` \| `methodological_spec` \| `indirect_inference` \| `secondary_citation` \| `author_claim_abstract`。 |
| `evidence_tier` | enum | 必填 | `T1_direct_data` \| `T2_table_figure` \| `T3_methods_or_text` \| `T4_abstract_summary` \| `T5_indirect_inference` \| `T6_secondary_citation` \| `T7_unverified`。 |
| `source_attribution` | enum | 必填 | `primary_study_finding` \| `cited_prior_work` \| `ambiguous`。 |
| `location` | object | 必填 | `{"doc_id": string, "page": int, "printed_page": string, "block_id": string, "matched_block_ids": [string], "section_id": string, "section_title": string, "section_role": string, "char_start": int\|null, "char_end": int\|null, "referenced_assets": [string]}`。 |
| `provenance.alignment_status` / `alignment_status` | enum | 必填 | `EXACT_MATCH` \| `NORMALIZED_MATCH` \| `CROSS_BLOCK_MATCH` \| `FUZZY_PARTIAL` \| `QUOTE_NOT_FOUND`。 |
| `extraction_method` | enum | 可选 | `native`（默认原生文本） \| `ocr_tesseract`（由 `pytesseract` / CLI `tesseract` 提取） \| `ocr_unavailable`（扫描件无可用文本层）。OCR 产物在 `anchors` 与 `anchors_compact.txt` 中附带 `src=ocr`、`ocr_engine` 及 `rendered_page_png`（`<work_dir>/rendered_pages/{doc_id}_p{page:03d}.png`）。 |
| `confidence` | number | 必填 | `[0.0, 1.0]` 区间数值置信度。 |
| `confidence_basis` | string | 必填 | 可复现的置信度依据（说明为何为该分值，如直接实验数据+精确对齐+完整统计显著性）。 |

### 2.4 `pubinfo`（来源与版本可追溯元数据）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `doc_id` | string | 必填 | 文档标识（如 `D1`）。 |
| `title` | string\|null | 必填 | 论文标题（无法确定则为 `null`，严禁猜测）。 |
| `authors` | array[string] | 必填 | 作者列表（未提取到为 `[]`）。 |
| `year` | int\|null | 必填 | 发表/预印本年份。 |
| `venue` | string\|null | 必填 | 期刊、会议或预印本平台名称。 |
| `doi` | string\|null | 必填 | 精确提取并验证的 DOI（如 `10.1038/...`）。 |
| `arxiv_id` | string\|null | 必填 | 精确提取并验证的 arXiv ID（含版本号如 `2611.25775v1`）。 |
| `pmid` | string\|null | 必填 | PubMed ID。 |
| `url` | string\|null | 必填 | 文档或仓库 URL。 |
| `version_or_preprint` | string\|null | 必填 | 版本/预印本标记。 |
| `license` | string\|null | 必填 | 协议信息（如 CC-BY-4.0）。 |
| `field_sources` | object | 必填 | 每个非空字段的提取锚点溯源（如 `{"doi": "pdf_text:P1-B003"}`）。 |
| `extracted_by` | string | 必填 | 生成器与版本标识，如 `task_09_nanopub/pubinfo_extractor.py`。 |

---

## 3. Grounded QA 回答结构（`qa_answers[]`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `question_id` | string | 必填 | 问题编号（如 `Q1`）。 |
| `question` | string | 必填 | 原始问题文本。 |
| `answer_status` | enum | 必填 | `answered` \| `partially_answered` \| `not_reported` \| `conflicting_evidence` \| `unverifiable`。 |
| `answer` | string | 必填 | 基于已验证 Nanopub 的明确回答；若未报告或无法判断，必须显式说明边界，禁止补造。 |
| `supporting_nanopub_ids` | array[string] | 必填 | 支撑该回答的 `nanopub_id` 列表。 |
| `evidence_locations` | array[object] | 必填 | 直接回查位置汇总 `[{"doc_id", "page", "block_id", "section_role", "evidence_quote"}]`。 |
| `uncertainty_note` | string\|null | 必填 | 未覆盖的边界、样本局限或冲突说明。 |

---

## 4. 冲突与不确定性结构（`conflicts[]` / `uncertainty[]`）

- **`conflicts[]`**：`{"conflict_id", "conflict_type" ("internal_self_contradiction" | "cross_document_contradiction" | "quantitative_conflict" | "claim_vs_evidence_mismatch"), "nanopub_ids": [string], "description": string, "evidence_locations": [...], "resolution_status": "unresolved_reported" | "scoped_by_condition"}`
- **`uncertainty[]`**：`{"uncertainty_id", "category", "uncertainty_nature": "source_or_coverage_blocker" | "analytical_finding", "severity": "high" | "medium" | "low", "doc_id": string|null, "pages": [int], "rendered_pages"?: [string], "message": string}`
  - **Source/Coverage Blockers**（当 `severity="high"` 时强制将 `status` 降级为 `partial`/`unanswerable`）：`scanned_document_unreadable` \| `formula_pua_unrecoverable` \| `audit_high_issues` \| `unextractable_asset` \| `source_unreadable` \| `ocr_unavailable` \| `missing_evidence`（兼容 `scanned_unreadable` / `garbled_fonts` / `ocr_extracted_text` / `font_garble_or_cid`）。
  - **Analytical Findings**（科学文献客观分歧或分析性注记，当源文完整且全部 QA 已忠实回答并记录于 `conflicts[]` 时允许 `status="success"`）：`scientific_conflict` \| `literature_conflict` \| `analytical_note`（或配合 `conflicts[]` 使用 `cross_document_unresolved_conflict` / `unresolved_scientific_conflict`）。
