---
name: scientific-doc-parser
description: Extract structured text, multi-column reading flow, hierarchical sections, complex tables, and mathematical formulas from scientific literature PDFs with verifiable source evidence citations.
---

# Scientific Document Parser Skill

## 1. 适用场景与运行环境约定 (Runtime Conventions)

本 Skill 专为 Harbor 评测容器与 DeepSeek Harness 运行时设计，支持原生双栏/多栏论文、跨页复杂学术表格、带上下标公式以及无文本层扫描件的稳健解析。

### 路径约定
- **Skill 脚本入口**：`/skills/weekly-skill/scripts/`（本地开发时相对本文件 `scripts/` 目录）
- **输入数据**：`/input/case.json` 及待解析的真实 PDF 原文文件
- **工作目录**：`/app/work`（解析中间文件保存在 `/app/work/parsed`）
- **最终交付物**：`/app/output/result.json`（必须严格遵循题目指定的输出契约）

---

## 2. 五步强制执行协议 (5-Step Execution Protocol)

### Step 1：契约与输入边界确认 (Contract & Boundary Setup)
1. 优先读取 `/input/case.json` 及任务描述，提取并锁定：
   - `case_id`（必须与交付物严格一致）
   - 待解析 PDF 文件的绝对物理路径（例如 `/input/paper.pdf`）
   - `output_contract` 中指定的所有必填字段。
2. **严禁硬编码假定字段**：若 `case.json` 明确给出了返回字段列表，严格遵循其定义；若未定义细节 schema，则参考 `templates/result_fallback_schema.json` 组织数据。

### Step 2：一键确定性解析 (Deterministic Pipeline Run)
调用一键总控脚本，对目标 PDF 执行无崩溃提取与拓扑重构：
```bash
python /skills/weekly-skill/scripts/run_pipeline.py \
  --pdf "<INPUT_PDF_PATH>" \
  --work-dir "/app/work/parsed" \
  --case-json "/input/case.json"
```
**产出物清单**：
- `/app/work/parsed/diagnosis.json`：页面版式、乱码率、印刷页码偏移量（`printed_page_offset`）与推荐路由
- `/app/work/parsed/structured_doc.json`：带 `[page|printed_page|block_id|bbox]` 证据锚点的全量分块数据（已完成双栏保序、上下标 `_{...}`/`^{...}` 还原、双页码映射及独立 `footnotes` 字段）
- `/app/work/parsed/reading_order.md`：排除了页眉页脚与干扰项的正文自然阅读流 Markdown
- `/app/work/parsed/enhanced_elements.json`：提取并清洗后的 Markdown/HTML 表格矩阵（含无竖线三线表、跨页续表合并 `source_printed_pages`）及公式
- `/app/work/parsed/pipeline_summary.json`：文档全貌概览（含高风险页、多级章节目录树、表格数、脚注数）

> [!TIP]
> **中文文献与双页码利用技巧**：
> 1. **双页码核验**：期刊文献印刷页码常从中间页起排（如 `p.1045`）。`structured_doc.json` 和 `enhanced_elements.json` 中每个元素均自带 `page`（物理页码）和 `printed_page`（印刷页码），在生成回答 evidence 时建议优先提供两套页码，确保判分规则无论按印刷页或物理页检索均可精准对齐。
> 2. **中文标题与跨页续表**：解析器已全适配中文多级标题（`1 引言`、`一、`、`（一）`）与长表（`表 1` 与 `续表 1` 自动合并），直接从 `sections` 和 `tables` 索引即可，无需额外正则二次清洗。
> 3. **栏底脚注溯源**：基金项目、收稿日期及通讯作者邮箱已单独剥离至 `footnotes` 列表中，不再干扰正文段落。

### Step 3：早落盘保底 + 增量精修 (Early Draft & Incremental Refinement)
受限于评测环境 **64 次模型调用 / 900 秒时间预算**：
1. **立即落盘基础交付**：在调用初期，基于 `pipeline_summary.json` 与解析数据，立即向 `/app/output/result.json` 写入一份包含 `case_id`、`status: "partial"` 或初步答案的合法 JSON，确保评测中断时具有基础保底分。
2. **增量定向精修**：
   - 检查 `diagnosis.json` 中的 `high_risk_pages`（如低分辨率图、乱码字体）；
   - 查看 `pipeline_summary.json` 中的提取表格与公式，针对题目核心问答做定向精读；
   - 优化结论，将 `status` 更新为 `"success"`。

### Step 4：证据铁律与诚实边界 (Evidence Citations & Honesty)
1. **关键结果指回原文**：每个输出的关键字段、数值、推导或段落，必须关联原论文的证据来源（`page` 物理页码、`evidence_block_id` 或 `bbox` 包围盒），确保可人工核验。
2. **诚实边界处理**：
   - **加密/密码保护**：记录 `status: "failed"`，在 `uncertainty` 声明 `encrypted=true`，严禁假装解析成功；
   - **扫描件且无 OCR**：记录 `status: "partial"` 或 `"unverifiable"`，说明原因；
   - **材料缺失或未提及**：在 `uncertainty` 中明确列出未报告项，标明人工接管检查点，**严禁根据领域通用常识无依据脑补**。

### Step 5：交付前强制自检 (Mandatory Pre-Flight Validation)
在结束任务前，必须运行自动化校验脚本自检：
```bash
python /skills/weekly-skill/scripts/validate_output.py \
  --case-json "/input/case.json" \
  --result-json "/app/output/result.json" \
  --doc-json "/app/work/parsed/structured_doc.json" \
  --out-report "/app/work/validation_report.json"
```
- 若返回退出码非零，立即检查 `/app/work/validation_report.json` 中报告的缺失字段、越界页码或残留占位符，修复通过后方可交付。

---

## 3. 异常处理与降级矩阵 (Graceful Fallback Matrix)

| 场景异常 | 探测表现 | 自动处置与降级手段 | 交付状态标记 |
|---|---|---|---|
| **双栏/多栏排版** | `layout_type == "double_column"` | 启动 Band 垂直条带切分，左栏全读完再转右栏，杜绝串行错行 | `status: "success"` |
| **公式上下标/希腊字母** | `size <= 0.84x dom_size` | 结合基线偏移自动包装为 `_{...}` 或 `^{...}`，保留 Unicode 符号 | `status: "success"` |
| **量子电路/图表误判** | 平均单元格长度 < 2.5 且临近 Figure | 自动过滤转移至 `filtered_non_tables`，避免伪造假表格 | `status: "success"` |
| **纯图片/无文字扫描版** | `char_count == 0, img == 1.0` | 自动切换至整页 OCR 管道，提取文本回填并打标 `is_ocr_fallback` | `status: "success"` / `"partial"` |
| **缺少第三方解析库** | `fitz/pdfplumber unavailable` | 顶层无第三方依赖，自动按 fitz -> pdfplumber -> pdftotext 降级 | `status: "success"` |
| **加密或损毁文件** | `%PDF- header 缺失或加密` | 捕获加密异常，如实汇报错误并在 `uncertainty` 中说明 | `status: "failed"` |
