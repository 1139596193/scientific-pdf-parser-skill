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
| **Skill 主指令** | `SKILL.md` | DeepSeek Harness 调用入口、3 步复合快轨（`bootstrap` → `patch` → `finish`）、预算守卫与异常降级规则 |
| **复合流水线入口** | `scripts/nanopub_pipeline.py` | 2 命令端到端驱动（`bootstrap` 一键摄取/索引/OCR与220 DPI渲染/PubInfo/早落盘；`finish` 一键定稿/8维审计/多格式渲染/强门禁） |
| **PDF 引擎** | `scripts/pdf_engine/` | 四级兜底（`pymupdf/fitz -> pdfplumber -> pdftotext -> stdlib_zlib`）、双栏保序、公式/散文分离与 220 DPI 页面渲染 + OCR 降级 |
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

## 5. Local Controlled A/B Verification (Non-Official)

> **Important Disclaimer (非官方评测声明)**：以下对照数据全部来自本地公开学术论文与合成边界测试集（`local_bench/stage6_v2_top2/` 与 `stage4_blind_test/`）的受控 A/B 盲测产物实测，**并非平台官方隐藏评测基准成绩**；所有指标均由 `compare_ab_benchmark.py` 从各方独立产物重算得到，无任何人工润色或夸大。

### 5.1 受控 A/B 实测对照表

| 评测维度 | Case 01 无 Skill (`baseline_case_01`) | Case 01 v2 Skill (`v2_case_01`) | Case 02 无 Skill (`baseline_case_02`) | Case 02 Skill (`stage4/case_02`) |
|---|---|---|---|---|
| **受测对象与输入** | 18 页双栏 arXiv 论文 (`Q1`–`Q4`) | 18 页双栏 arXiv 论文 (`Q1`–`Q4`) | 中文边界 PDF + 复现冲突 + 3 层契约 | 中文边界 PDF + 复现冲突 + 3 层契约 |
| **综合客观得分 (/100)** | 89.3 | **100.0 (+10.7)** | 98.0 | **100.0 (+2.0)** |
| **原子断言 / 发现数** | 36 | 26 | 20 | 25 |
| **逐字引文精确命中率** | 35/36 (97.2%) | **26/26 (100.0%)** | 20/20 (100.0%) | **25/25 (100.0%)** |
| **W3C 4-Graph (`#Head`+三元组图)** | **FAIL**（JSON-LD/TriG 缺 `#Head` 图，TriG 仅 3.0 图/条） | **PASS**（完整 4.0 图/条） | PASS（4.0 图/条） | **PASS**（4.0 图/条） |
| **嵌套 `output_contract` 覆盖率** | 100.0% | 100.0% | 100.0% | 100.0% |
| **Grounded QA 覆盖率 (`case.json`)** | 4/4 (100.0%) | 4/4 (100.0%) | 3/3 (100.0%) | 3/3 (100.0%) |
| **PubInfo 零幻觉校验** | 0 幻觉 | **0 幻觉** | 0 幻觉 | **0 幻觉** |
| **跨文献冲突检出数** | 0 | 0 | 6 | 4 |
| **`audit_report.json` 确定性审计** | 无 | **有 (HIGH=0)** | 无 | **有 (HIGH=0)** |
| **`work/` 临时辅助 `.py` 工程成本** | 5 个 / 1,556 行 | **3 个 / 455 行 (-70.8%)** | 14 个 / 2,651 行 | **2 个 / 75 行 (-97.2%)** |
| **交付状态 `status`** | `complete`（未降级反映 `Q4` 缺失） | `partial`（诚实标注 `Q4` 未报告与 PUA 边界） | `completed_with_caveats` | `success`（`audit_passed=true`） |

### 5.2 确定性审计拦截与 64 轮 DeepSeek Harness 风险压降机制

1. **审计真实拦截与原位热修闭环**：在 `v2_case_01` 实测中，`finish` 与 `entity_guard` 对数值/单位/限定条件实施确定性校验（拦截如 `NP04` 遗漏 `if and only if` 限定条件触发 `QUALIFIER_DROPPED`，经 `make_hotfix.py` 通过 `replaces_id` 原位替换 `np:sha256:59509fe9a1ddedd7` → `np:sha256:c028f4efdfdfa646`，全量 `NP01`–`NP26` 通过 `compare_claim_against_anchors` 数值防幻觉预检）。对比来看，无 Skill DSH 在 `baseline_case_02` 初期误判 Type0 字体缺 `ToUnicode` CMap 会导致常规提取器全量 U+FFFD，从而额外手写 14 个 PDF 流解码/渲染脚本（2,651 行）；而启用 Skill 后确定性底座直接给出真实字形与风险分级。
2. **复合双命令压缩交互轮次**：`nanopub_pipeline.py bootstrap` 与 `finish` 将原本需要 6–7 次工具调用的预处理与交付审计收拢为 **2 次复合调用** + `nanopub_assembler.py patch`（支持 `replaces_id` 与 `remove_np_ids` 原位热修），在 64 轮受限 Harness 预算下把更多轮次留给逐块细读与科学分析，且 `bootstrap` 阶段即落盘合法 `partial` `result.json` 兜底。
3. **扫描件/乱码页可视化与 OCR 闭环**：当遇纯扫描 PDF、混合扫描页、`font_garble_or_cid` 风险或未提取视觉图表页时，`bootstrap` 自动以 220 DPI 渲染 `<work_dir>/rendered_pages/{doc_id}_p{page:03d}.png` 并尝试 `pytesseract` / `tesseract`（`chi_sim+eng` → `eng`）双语 OCR 注入带 `src=ocr` 标记的轻量锚点，若系统无可用 OCR 则诚实降级并附上可读 PNG 路径供视觉复核。

---

## 6. 依赖与已知边界限制

- **运行环境**：全部核心脚本顶层仅依赖 Python 3.11+ 标准库；解析原生/复杂 PDF 时按 `pymupdf -> pdfplumber -> pdftotext -> stdlib_zlib` 优先级动态探测与降级。
- **已知限制**：
  1. 对纯扫描版无文本层 PDF，当运行镜像未安装 `tesseract`/`pytesseract` 或 OCR 不可读时，系统诚实输出 `unextractable_asset` 并降级为 `partial` 或 `unanswerable`，绝不编造文本。
  2. 对仅出现于矢量/栅格图内坐标轴而未在正文或图注写明的数值，标注 `REQUIRES_VISUAL_TABLE_OR_FIGURE` 并列入 `manual_review_checklist`。
  3. 当原论文未标注 DOI、arXiv ID 或许可证时，对应 `pubinfo` 字段保持 `null`。
