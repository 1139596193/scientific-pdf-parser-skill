# Nanopublication Scientific Reading & Evidence Grounding Skill

面向科研文献精读、原子断言拆解（Nanopublication）、最小充分逐字证据绑定、防幻觉来源元数据溯源与可复核科研问答（Grounded QA）的标准化 Skill。

---

## 1. 设计目标

解决科研阅读 Agent 常见的四类核心问题：
1. **拆得细**：通过原子化判据与 30 词受控关系词表，拒绝整段摘要倾倒与复合多指标混写。
2. **对得上**：通过连字/断行归一、同页跨块滑窗与高阈值模糊定位引擎，逐条核验 `evidence_quote` 及其精确 `P{page}-B{seq}` 块定位。
3. **追得回**：通过确定性 `pubinfo_extractor.py` 对 `title`、`authors`、`year`、`venue`、`doi`、`arxiv_id`、`pmid`、`url`、`license` 绑定 `field_sources` 溯源，缺失字段诚实置 `null`；严格区分一手实证与二手引用。
4. **读得懂**：在保留原始 `case_id` 与任意嵌套 `output_contract` 的同时，一键导出 W3C `nanopubs.jsonld`、W3C `nanopubs.trig`、`nanopubs.csv`、`result.json`、`report.md` 与 `audit_report.json`。

---

## 2. 架构模块清单

| 模块 / 资产 | 路径 | 职责说明 |
|---|---|---|
| **Skill 主指令** | `SKILL.md` | DeepSeek Harness 调用入口、6 步确定性流水线、预算守卫与异常降级规则 |
| **PDF 引擎** | `scripts/pdf_engine/` | 四级兜底（`pymupdf/fitz -> pdfplumber -> pdftotext -> stdlib_zlib`）、双栏保序、公式/散文分离与 OCR 降级 |
| **材料摄取器** | `scripts/case_ingestor.py` | 多格式递归扫描、`/input/case.json` + `task.md` 解析、`research_questions` 与 `target_formats` 提取 |
| **结构索引器** | `scripts/structure_indexer.py` | 10 枚举 `section_role` 归一、块级引用/定量/图表信号标注、Token 预算安全阅读包 |
| **实体与逻辑守卫** | `scripts/entity_guard.py` | 数值/单位/英文数词等价、科学限定条件提取（温度/pH/剂量/`n`/`p`/上下文）、跨语种否定与因果升级检测 |
| **来源元数据提取器** | `scripts/pubinfo_extractor.py` | 逐字段来源绑定与 `verify_pubinfo_against_source` 防幻觉校验 |
| **逐字证据对齐器** | `scripts/evidence_aligner.py` | 精确/归一/同页跨块/模糊对齐，检出 `PARAGRAPH_DUMP`、`TOO_SHORT_FRAGMENT`、`BLOCK_ID_DRIFT` |
| **增量合成与冲突图** | `scripts/nanopub_assembler.py` | `init` 早落盘、`patch` 小批增量合成（sha256 Trusty ID + 7 级证据分级）、`finalize` 跨章节去重升格与极性/定量冲突图 |
| **8 维确定性审计器** | `scripts/nanopub_auditor.py` | 覆盖原子性、SPO/词表、引文对齐、充分性、数值/极性/因果、二手引用、PubInfo、QA 完整性的 8 维门禁 |
| **多格式渲染器** | `scripts/render_deliverables.py` | 递归适配嵌套 `output_contract`，生成 `result.json`、`nanopubs.jsonld`、`nanopubs.trig`、`nanopubs.csv`、`report.md` |
| **强门禁校验器** | `scripts/validate_output.py` | 契约完整性、TriG 语法闭合、JSON-LD 四图与 CSV 行数一致、反 `status="success"` 造假校验 |
| **离线全量自测** | `scripts/self_test.py` | 零外部依赖全链路确定性单元测试与回归套件 |

---

## 3. 生态与规范资产（`references/`）

- `references/relation_vocab.json`：6 大类共 30 个受控科研谓词（含 URI、对称性、反义词、极性、因果强度与中英别名）。
- `references/schema_dictionary.md`：Canonical Nanopublication 四图模型与 Grounded QA 完整字段字典。
- `references/quality_rules.md`：8 维可复现质量判据与 `T1`–`T7` 证据强度分级规范。
- `references/academic_invariants.json`：跨学科逻辑极性词典与 SI/学术单位表。
- `references/examples_20_nanopubs.json`：覆盖 5 大学科与 3 类边界场景的 20 条可复用 Nanopublication 样例。
- `references/mini_benchmark.json`：6 组可复用离线陷阱基准题集。

---

## 4. 最小调用示例与离线自测

### 4.1 离线全链路自测（纯标准库，零外部依赖）
```bash
python -B scripts/self_test.py
```

### 4.2 独立运行标准流水线
```bash
python -B scripts/case_ingestor.py --input-dir /input --work-dir /app/work
python -B scripts/structure_indexer.py --input-dir /input --work-dir /app/work --output-dir /app/output
python -B scripts/pubinfo_extractor.py --work-dir /app/work
python -B scripts/nanopub_assembler.py init --ingest-manifest /app/work/case_manifest.json --structure-index /app/work/evidence_index.json --pubinfo /app/work/pubinfo.json --work-state /app/work/work_state.json --output-dir /app/output
python -B scripts/nanopub_assembler.py patch --work-state /app/work/work_state.json --patch-json /app/work/patch_001.json
python -B scripts/nanopub_assembler.py finalize --work-state /app/work/work_state.json
python -B scripts/nanopub_auditor.py --work-state /app/work/work_state.json --structure-index /app/work/evidence_index.json --pubinfo /app/work/pubinfo.json --out /app/work/audit_report.json
python -B scripts/render_deliverables.py --work-state /app/work/work_state.json --audit-report /app/work/audit_report.json --output-dir /app/output
python -B scripts/validate_output.py --output-dir /app/output --ingest-manifest /app/work/case_manifest.json
```

---

## 5. 依赖与已知边界限制

- **运行环境**：全部核心脚本顶层仅依赖 Python 3.11+ 标准库；解析原生/复杂 PDF 时按 `pymupdf -> pdfplumber -> pdftotext -> stdlib_zlib` 优先级动态探测与降级。
- **已知限制**：
  1. 对纯扫描版无文本层 PDF，当运行镜像未安装 `tesseract`/`pytesseract` 或 OCR 不可读时，系统诚实输出 `unextractable_asset` 并降级为 `partial` 或 `unanswerable`，绝不编造文本。
  2. 对仅出现于矢量/栅格图内坐标轴而未在正文或图注写明的数值，标注 `REQUIRES_VISUAL_TABLE_OR_FIGURE` 并列入 `manual_review_checklist`。
  3. 当原论文未标注 DOI、arXiv ID 或许可证时，对应 `pubinfo` 字段保持 `null`。
