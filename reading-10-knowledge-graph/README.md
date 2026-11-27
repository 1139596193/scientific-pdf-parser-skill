# Literature Knowledge Graph Construction Skill (`reading-10-knowledge-graph`)

面向 **阅读15｜文献知识图谱构建**（Challenge ID: `product-arena-10-760d1a0b`）的确定性双模引擎、受控本体、防误并守卫、子图问答与 18 项质量审计 Skill（v1.1）。

---

## 1. 核心目标与四大防线

1. **抽得准**：实体严格限定于 `kg_ontology.json` 定义的 8 大类（`Researcher`, `Material_or_Chemical`, `Method_or_Model`, `Task_or_Problem`, `Dataset_or_Sample`, `Metric_or_Property`, `Experimental_Condition`, `Finding_or_Claim`），每个节点与每条边均经 `evidence_aligner.py` 逐字对齐原文。
2. **连得对**：35 个有向科研谓词覆盖 7 大类别，结合 4 层认知证据状态（`direct_experimental`、`reported_prior_work`、`author_speculation`、`definition_or_setup`），禁止 `co_occurrence_only` 成边，拦截引言综述转述升格与相关性夸大因果。
3. **对得齐（含 7 项非合并护栏）**：
   - 区分篇内缩写绑定（如 `Copper Metal-Organic Framework (Cu-MOF)`）与高危通用语境敏感词（`control group`, `baseline`, `catalyst`, `CNN`, `ResNet`, `dataset`, `mice`, `patients`, `accuracy` 等），跨篇通用词强制隔离并记录于 `non_merge_decisions`；
   - **工艺路线与实验阶段隔离（`synthesis_route_and_lifecycle_stage_isolation`）**：同一化学式体系若制备工艺路线不同（如 ALD vs sol-gel、干压 vs 烧结）须保持独立实例节点；跨篇定量比较须校验实验生命周期阶段（如初始/化成后 vs 长循环后），禁止跨阶段做无物理意义的倍率归因；
   - 严禁 `case.json` 根 DOI/标题/作者串用到 `secondary` 论文，严禁把 `secondary-publisher.xml`（`verification_twin`）计为第 3 篇或跨篇证据。
4. **查得到、画得出、回得去**：
   - 支持 6 类标准化子图查询（含确定性 BFS 多跳最短路径、跨篇对比、冲突消歧、溯源反查与未报告边界拒答）；
   - 导出 8 项交付物（`result.json`、`knowledge_graph.json`、`knowledge_graph.graphml`、`nodes.csv`、`edges.csv`、`knowledge_graph_report.md`、`kg_overview.svg`、`kg_overview.png`），其中 `kg_overview.png` 内置 **三级渲染兜底**（`pymupdf` → `Pillow` → 纯 Python 标准库 `zlib+struct` 5×7 点阵光栅化器），离线无第三方图形库环境亦可零摩擦直出高分辨率 PNG。

---

## 2. 目录结构与模块清单

| 路径 | 职责说明 |
|-|-|
| `SKILL.md` | Agent 执行入口指令（`bootstrap` → `inspect` → `patch` → `finish` → `query`） |
| `scripts/kg_pipeline.py` | 统一 5 命令 CLI，适配 64 轮 Harbor 预算（含早落盘与热修闭环） |
| `scripts/case_ingestor.py` | 双模摄取器（Mode A：JATS 双论文语料包；Mode B：PDF/DOCX/LaTeX/HTML/MD/TXT） |
| `scripts/structure_indexer.py` | 双定位锚点索引（`{source_id}::{paragraph_id}` 与 `P{page}-B{seq}`、`anchors_compact.txt`） |
| `scripts/pubinfo_extractor.py` | 独立论文元数据提取与 `detect_root_metadata_leakage` 防串用校验 |
| `scripts/entity_guard.py` | 实体规范化、混合大小写缩写绑定、7 项非合并护栏与认知一致性检查 |
| `scripts/evidence_aligner.py` | 双定位逐字短引对齐与 `verify_cross_paper_independence` 核验 |
| `scripts/kg_assembler.py` | 增量批次合成（`v1_primary` → `v2_secondary_update` → `hotfix`）、自动冲突/拒并探测与 Diff |
| `scripts/kg_query_engine.py` | 确定性子图检索（6 类模板 + BFS 多跳路径 + 问答防漂移 + 3 问兜底） |
| `scripts/kg_auditor.py` | 18 项硬核审计规则与 `determine_honest_status` 门禁 |
| `scripts/render_deliverables.py` | 全套 8 项交付物渲染器（含 Mermaid 子图与三级兜底 SVG/PNG 预览） |
| `scripts/validate_output.py` | 交付完整性、GraphML XML 合法性与诚实状态一致性门禁 |
| `scripts/self_test.py` | 纯标准库 41 项离线全覆盖自测套件（无需外部测试文件） |
| `scripts/pdf_engine/` | 多级兜底 PDF 解析与 220 DPI 页面渲染 / OCR 模块 |
| `references/` | `kg_ontology.json`、`entity_alignment_rules.json`、`query_templates.json`、`schema_dictionary.md`、`quality_rules.md`、`reference_kg_example.json`、`mini_benchmark.json` |

---

## 3. 快速运行与离线自测

### 3.1 运行离线全覆盖自测（纯标准库，零外部依赖）

```bash
python -B scripts/self_test.py
```

实测输出（**Non-Official Local Benchmark（仅本地实测，非官方 Harbor 评分）**）：`41 / 41 ALL_PASSED`（含 AST CLI 零漂移校验、BOM/CRLF/路径泄漏检查、18 项审计规则逐条触发、双模端到端回归，以及屏蔽 `pymupdf` 与 `PIL` 时的纯标准库 `rendered_stdlib_rasterizer` PNG 降级实测）。

### 3.2 端到端标准工作流示例（示意命令行）

```bash
python -B scripts/kg_pipeline.py bootstrap --input /input --work /app/work --output /app/output
python -B scripts/kg_pipeline.py inspect --work /app/work --source primary --role results --limit 15
python -B scripts/kg_pipeline.py patch --work /app/work --batch-file /app/work/batch_v1.json --stage v1_primary
python -B scripts/kg_pipeline.py patch --work /app/work --batch-file /app/work/batch_v2.json --stage v2_secondary_update
python -B scripts/kg_pipeline.py finish --work /app/work --output /app/output
python -B scripts/kg_pipeline.py query --work /app/work --question "Compare catalyst performance across papers." --type cross_paper_comparison
```

---

## 4. 真实非官方本地实证（Non-Official Local Benchmark）

> **声明**：本节全部数据均为本地开发环境真实运行记录（**Non-Official Local Benchmark（仅本地实测，非官方 Harbor 评分）**），不代表官方赛题评测平台最终得分。

### 4.1 本地高压压测与 3 次重复稳定性实测

| 压测套件 | 测试规模与输入形态 | 真实观测结果 |
|-|-|-|
| 离线零依赖自测 (`self_test.py`) | 41 项单元测试与管道集成测试 | `41 / 41 PASS`，`failed_count = 0`，0 外部路径/第三方包依赖 |
| 高复杂度双论文 JATS × 3 次重复 | 28 段落、16 节点、18 边、5 冲突、4 拒并、8 查询 + XML 核对副本（年份冲突） | **3/3 `EXACT_MATCH`**；单轮耗时 `2.633s / 2.586s / 2.718s`（均值 `2.646s`）；准确检出 JSON vs XML 年份差异与 28 条全覆盖去重 |
| 真实双语多 PDF 联测 (Mode B) | 英-英双 PDF 组合 & 中-英双语 PDF 组合 | 双场景 `status="success"`（`docs=2`、`anchors=1167 / 1165`、SVG+PNG 完整），**0 `FutureWarning` / `DeprecationWarning`** |
| D1–D4 对抗性异常降级测试 | D1 空白扫描件 / D2 损坏+缺失文件 / D3 缺失次要文献 / D4 畸形补丁 | **0 崩溃**：D1–D4 均诚实降级为 `status="partial"`，准确计量 `D3 boot_docs=1`，D1/D2/D4 按预期触发 1–5 条 `HIGH` 审计阻断 |

### 4.2 WorkBuddy 外部冷启动双盲 A/B 实证（`blind-kg-01` — Non-Official Local Benchmark）

在全新未见课题（固态电池 `LiNbO3` 包覆 `NCM811` 正极界面研究：25 °C/4.3 V vs 55 °C/4.5 V 热电化学衰变与 `secondary-publisher.xml` 年份差异校验）上，由外部独立 Agent 在无泄题提示的沙箱中分别完成 Run A（无 Skill 基线）与 Run B（挂载本 Skill 冷启动），使用 10 维客观脚本 `evaluate_ab.py` 评定：

| 评测维度 (`evaluate_ab.py`) | Run A（无 Skill 基线） | Run B（挂载本 Skill） |
|-|-|-|
| `M01_result_and_contract` | 7.0/10.0 (`FAIL`, 缺失 `output_contract`) | **10.0/10.0 (`PASS`)** |
| `M02_independent_paper_count` | 10.0/10.0 (`PASS`, `2`) | **10.0/10.0 (`PASS`, `2`)** |
| `M03_root_metadata_isolation` | 10.0/10.0 (`PASS`, 0 泄漏) | **10.0/10.0 (`PASS`, 0 泄漏)** |
| `M04_jats_no_fake_pages` | 10.0/10.0 (`PASS`, 0 伪造页码) | **10.0/10.0 (`PASS`, 0 伪造页码)** |
| `M05_verbatim_quote_grounding` | 0.0/10.0 (`FAIL`, 未按标准 `evidence` 结构存储) | **10.0/10.0 (`PASS`, `70/70 = 100.0%` 原文命中)** |
| `M06_non_merge_guardrail` | 10.0/10.0 (`PASS`, `non_merge=4`) | **10.0/10.0 (`PASS`, `non_merge=4`)** |
| `M07_conflict_preservation` | 5.0/10.0 (`FAIL`, `conflicts=0` 未结构化落盘) | **10.0/10.0 (`PASS`, `conflicts=4`)** |
| `M08_qa_and_q4_honesty` | 0.0/10.0 (`FAIL`, 使用 `answers` 而非 `queries`) | 5.14/10.0 (`FAIL`*，已诚实标注 Q4 `answer_status="unreported"` 但字段名为 `undetermined_or_missing`) |
| `M09_diff_and_formats` | 7.0/10.0 (`FAIL`, Diff 键非标) | **10.0/10.0 (`PASS`, 标准 `v1->v2` diff + 6/6 格式)** |
| `M10_honest_status_gate` | 0.0/10.0 (`FAIL`, 非标 `status="ok"`) | **10.0/10.0 (`PASS`, `status="success"`)** |
| **总分** | **59.0 / 100.0** (`4/10 PASS`) | **95.14 / 100.0** (`9/10 PASS`, **+36.14**) |

\* **客观剖析与迭代闭环**：
- **Run A 的真实实力与结构瓶颈**：基线大模型具备极强的临场编码能力（手写 3 个定制脚本共 1,365 行代码，成功避免页码幻觉与根元数据串用，并完成 4 项通用词拆分与 6 类导出），但在无标准脚手架约束下出现了自定义 15 类节点 / 33 类边本体失控、非标 `status="ok"` 与 `answers` 字段名漂移、证据引文未进入标准 `evidence[]` 导致无法自动校验等问题。
- **Run B 的核心增益**：零手写构建脚本即交付受控 8/35/4 本体与完整契约；`70/70 (100.0%)` 短引原文命中；通过 18 项确定性审计并在第 3 轮提交 1 次靶向 `hotfix` 修复 `N-SH-01` 作用域边界后达成 `0 HIGH` 阻断；Q4（图 3 像素与附件 S1）亦已诚实触发 `answer_status="unreported"` 拒答（评分器因硬编码匹配 `unreported_fields` / 特定关键词而记 `5.14`）。
- **盲测暴露的摩擦与 v1.1 修复**：v1.0 运行 Run B 时离线环境缺少 `pymupdf`（触发 `png_preview_skipped_no_pymupdf`），Agent 需额外编写临时光栅脚本补齐 PNG。v1.1 已将纯标准库 `zlib + struct + 5×7 点阵` PNG 光栅器内置进 `render_deliverables.py`（`rendered_stdlib_rasterizer`），彻底消除无第三方图形库环境的渲染摩擦，并同步新增“工艺路线与实验阶段隔离”非合并护栏。

---

## 5. 依赖说明与已知边界

- **依赖环境**：全部脚本顶层仅依赖 Python 3.11+ 标准库。处理 Mode B PDF 时动态探测 `pymupdf`、`pdfplumber`、`pdftotext`、`pytesseract`/`tesseract`/`pdftoppm`；渲染 `kg_overview.png` 时按 `pymupdf` → `Pillow` → 内置纯标准库光栅器三级兜底，零第三方库即可完整输出全部 8 项交付物。
- **已知边界**：
  1. Mode A JATS 语料包无 PDF 页码、图像像素与补充附件，所有 `page_number` 保持 `null`；
  2. 面对纯扫描无文本层且无 OCR 环境的 PDF，系统诚实产出 `unextractable_asset` 并降级为 `partial` 或 `failed`。
