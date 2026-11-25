# 科研文献高保真双语翻译 Skill（Academic Literature Translation）

本 Skill 面向真实科研文献（PDF / Markdown / LaTeX / HTML / DOCX / TXT）的跨语种高质量翻译与核验需求而构建，聚焦解决科研翻译中的四大核心痛点：**术语稳、段落全、关系准、可核验**，专为在有限模型请求预算（如 Harbor / DeepSeek Harness 的 64 轮 / 900 秒限制）下稳定产出可复核的双语对照结果而设计。

---

## 1. 核心痛点解决与确定性增强机制

相比仅靠通用提示词（Prompt）直接翻译的基础 Agent，本 Skill 通过**确定性 Python 预处理底座 + 编译式术语硬锁 + 6 维译后代码审计**形成可复现的工程化闭环：

| 核心痛点 | 基础 Agent 常见退化模式 | 本 Skill 确定性代码机制 |
| :--- | :--- | :--- |
| **术语稳** | 术语仅写在上下文提示中，长文后半段或图注出现同词异译（如 `差分隐私` ↔ `微分隐私`）。 | `term_extractor.py` 从全文抽取缩略语与名词短语，支持导入外部 `.csv/.tsv/.json/.md` 词表并编译正则锁（兼容英文复数与连字符变体），按批次注入 `batch_glossary`，并在译后强制核验规范译名与禁译词。 |
| **段落全** | 双栏论文阅读顺序错乱、跨页/跨栏长句被拦腰斩断、长文超过轮次上限导致后半篇漏译或写入摘要糊弄。 | `pdf_engine/layout_parser.py` 执行垂直 Band 拓扑保序、页眉页脚隔离、数学碎片聚簇与**跨栏/跨页断段智能接续**；`case_ingestor.py` 实现预算自适应控批（长文默认压入 ≤18 批）+ 每批即时 `merge_checkpoint.py` 早落盘。 |
| **关系准** | 数字小数点、单位、方括号文献引用（如 `[8]-[10]`）、图表公式编号（`Eq. (10)`, `Table 1`）、否定极性（`not`/`without`）与边界词（`at most`/`only`）在译文中悄悄丢失或翻转。 | `entity_guard.py` 采用屏蔽式流水线提取每段数值、引用区间展开、交叉引用及逻辑极性指纹；`translation_verifier.py` 逐段比对并生成精准的 `repair_queue.json` 进行外科手术式定向返修。 |
| **可核验** | 译文无段号锚点、难以回查原文；遇到纯扫描件或公式图片时强行“脑补”编造文本。 | 每段输出 1:1 绑定 `unit_id`、`section_path`、`page_span` 与 PDF 物理坐标 `evidence_anchors`；对无原生文字层或识别失败页面诚实标注 `untranslatable_asset` 与 `uncertainty`，绝不伪造内容。 |

---

## 2. 系统架构与五步执行流水线

```text
[输入材料 /input/] ──► 1. case_ingestor.py (多格式解析 + PDF 诊断 + 跨页续段 + 自适应控批)
                              │
                              ▼ (source_units.json + batches/batch_*.json)
                       2. term_extractor.py & entity_guard.py (术语抽取编译 + 实体/逻辑指纹注入)
                              │
                              ▼ (glossary_compiled.json + enriched batches)
                       3. Agent 分批翻译 ──► merge_checkpoint.py (容错合流 + Diff变更追踪 + 早落盘)
                              │
                              ▼ (merged_translations.json + change_records.json)
                       4. translation_verifier.py (6维硬审计 → repair_queue.json → 最多2轮返修)
                              │
                              ▼ (verification_report.json)
                       5. render_deliverables.py & validate_output.py (多视图交付 + 契约硬校验)
```

### 模块职责一览

| 脚本/资源路径 | 核心职责 |
| :--- | :--- |
| `SKILL.md` | Agent 运行核心指令集（4 模式路由、6 条铁律、5 步 CLI 工作流、异常处理决策表）。 |
| `scripts/case_ingestor.py` | 递归扫描子目录，读取 `case.json`/`task.md` 契约与范围约束；统一提取 PDF/DOCX/LaTeX/HTML/MD/TXT 原子单元并控批（零单元自动兜底）。 |
| `scripts/pdf_engine/` | 包含 `pdf_diagnose.py`（健康度诊断）、`layout_parser.py`（双栏 Band 保序与跨页缝合）、`table_formula_ocr.py`（表格重建与局部 OCR）。 |
| `scripts/term_extractor.py` | `extract`（缩略语对/高频短语多路抽取，导入外部词表并过滤章节噪声）与 `compile`（词形正则编译与索引）。 |
| `scripts/entity_guard.py` | 提取数值/复合单位（如 `GB/s`, `18.5\%`）、文献引用（含 `[7]---[9]`、`\cite{}`）、图表交叉引用（含 `\ref{}`）、行内公式、否定/边界触发词，注入批次 `guard_hints`。 |
| `scripts/merge_checkpoint.py` | 容错解析（支持 Markdown 代码围栏）、拦截误翻 `context_tail`、生成 Diff 中心切片 `change_records` 并刷新早落盘。 |
| `scripts/translation_verifier.py` | 覆盖率、长度比、术语一致性（防子串掩蔽）、实体保真、逻辑极性、格式洁净度 6 维确定性审计。 |
| `scripts/render_deliverables.py` | 递归适配自定义/嵌套 `output_contract` 契约，附加 `U000X.S1..Sn` 句级边界对齐并生成多视图交付物。 |
| `scripts/validate_output.py` | 硬校验 `case_id`、嵌套契约字段与类型检查、`status` 诚实度（防漏译报 success）与占位符残留，不通过则返回非零码。 |
| `scripts/self_test.py` | 零外部文件依赖的一键离线回归测试套件（15 个独立测试用例）。 |
| `resources/academic_invariants.json` | 跨学科否定/边界/让步逻辑触发词典、SI 学术单位表及模型元文本泄露正则。 |
| `templates/glossary_template.json` | 标准化可复用术语表 JSON Schema 模板。 |

---

## 3. 目录结构与零崩溃依赖说明

### 目录树
```text
.
├── SKILL.md                              # Skill 主体定义（YAML frontmatter + 工作流）
├── README.md                             # 本说明文档
├── resources/
│   └── academic_invariants.json          # 跨学科规则库
├── templates/
│   └── glossary_template.json            # 术语表标准模板
├── examples/
│   └── minimal_example/                  # 自包含最小调用示例
└── scripts/
    ├── case_ingestor.py
    ├── term_extractor.py
    ├── entity_guard.py
    ├── merge_checkpoint.py
    ├── translation_verifier.py
    ├── render_deliverables.py
    ├── validate_output.py
    ├── self_test.py
    └── pdf_engine/
        ├── __init__.py
        ├── pdf_diagnose.py
        ├── layout_parser.py
        └── table_formula_ocr.py
```

### 运行环境与依赖（Zero-Crash Design）
- **核心保障**：全部 `.py` 脚本在模块顶层**仅导入 Python ≥ 3.9 标准库**，在无任何第三方依赖的裸 Python 环境中亦不会因 `ImportError` 崩溃，可直接处理 Markdown、TXT、DOCX（基于标准库 `zipfile` + `xml.etree` 解析 `word/document.xml`）及纯文本流。
- **PDF 与 OCR 推荐后端（函数内动态探测并优雅降级）**：
  - 首选 PDF 解析：`PyMuPDF` (`fitz`) → 降级顺序：`pdfplumber` → 系统 CLI (`pdftotext` / `pdfinfo`)；
  - 扫描件/局部 OCR：`pytesseract` + `Pillow` → 降级：系统 `tesseract` CLI；若均不可用，自动生成 `untranslatable_asset` 占位单元并标注 `uncertainty`，绝不伪造文本。
  - 安装参考：`pip install pymupdf pdfplumber pytesseract pillow`

---

## 4. 输入输出约定与核心格式规范

### 输入约定
- 支持输入目录（含递归子目录）包含 `case.json`（可选，定义 `case_id`、`instructions`、`output_contract`）、`task.md`（可选任务指令与页码/章节范围约束）、科研材料（`.pdf` / `.docx` / `.tex` / `.html` / `.md` / `.txt`）及预设术语表（`*glossary*.csv/.tsv/.json/.md`）。

### 原子单元与对齐约定
- 每个单元拥有全局严格递增的确定性编号 `unit_id`（如 `U0001`, `U0002`），保留 `section_path`、`page_span: [start, end]`、`printed_page_span`、`evidence_anchors`（PDF bounding box）及多句单元的句级边界锚点 `sentence_alignment`（`[S1]`..`[Sn]`）。
- **翻译策略 (`translate_policy`)**：
  - `translate`：标题、摘要、章节名、正文段落、图表题注、表内容、脚注；
  - `preserve_source`：行间数学公式（`display_equation`）、代码段、首页作者角标元数据（`metadata`）及默认状态下的参考文献条目（`reference`，除非任务明确要求翻译）；
  - `manual_review_asset`：缺失文字层且无法可靠 OCR 的扫描页或图片资产。

### 术语表 Schema (`templates/glossary_template.json`)
每条锁定术语支持：`term_id`、`source_term`、`aliases`（如缩写）、`canonical_target`（全篇唯一锁定译名）、`allowed_target_variants`、`forbidden_targets`（禁用的常见误译）、`do_not_translate`（布尔值）、`case_sensitive`、`rationale`（译法依据）、`source_origin`。

### 未译与不确定性标注规则
- 存在未完成批次时，`result.json` 的 `status` 必须诚实标记为 `"partial"`；
- 若出现未解决的审计 `error` 或 `manual_review_asset`，诚实标记为 `"partial_with_warnings"` 或 `"unable_to_process"`；
- 所有残缺、字体编码异常（`potential_font_garble`）、OCR 抽取项及逻辑极性告警均统一写入 `uncertainty`、`manual_review_checklist` 及 `bilingual_parallel.md` 中的 `[⚠️ 缺失文字层/模糊资产，需人工复核]` 醒目标记。

---

## 5. 快速自检与调用方式

### 一键离线回归测试（零外部文件依赖）
在 Skill 根目录运行内置 15 项全链路自测套件（全部在系统临时目录动态构造，约 1 秒完成）：

```bash
python3 scripts/self_test.py
```

### 运行最小可复现示例
完整输入示例与预期示意输出位于 [`examples/minimal_example/`](examples/minimal_example/EXPECTED_OUTPUT_ILLUSTRATIVE.md)，涵盖从切片、锁表、核验到最终契约校验的完整命令。

### 4 种任务模式说明
- **Mode A（整篇文献翻译）**：按 `SKILL.md` Step 1 → Step 5 顺序执行。
- **Mode B（指定范围翻译）**：当 `task.md` 或 `case.json` 指定如 `pages 1-2` 或 `only Abstract` 时，`case_ingestor.py` 自动过滤范围外单元为 `scope_excluded`。
- **Mode C（既有译文核验与返修）**：通过 `case_ingestor.py` + `entity_guard.py` 提取源文指纹后，使用 `merge_checkpoint.py --add-batch` 导入既有草稿译文，直接运行 `translation_verifier.py` 生成 `repair_queue.json` 并定向返修。
- **Mode D（中译英与混排文献）**：自动识别 `zh->en`，兼容中文字数统计、中文非否定词（如 `不同`/`不仅`）豁免及英文目标术语大小写不敏感匹配。

---

## 6. 跨学科复用指南

将本 Skill 迁移至**生物医学、材料化学、法学或经济学**文献仅需 3 分钟：
1. **注入领域术语表**：准备包含两列（`source_term,canonical_target`）的 `glossary.csv` 放入输入目录即可被 `case_ingestor.py` 自动发现并锁定；或使用 `templates/glossary_template.json` 扩展 `forbidden_targets`（例如医学中区分 `sensitivity -> 灵敏度` 而禁用 `敏感性`）。
2. **扩充规则词表**：如需针对特定领域增加专属单位（如生物医学 `IU/mL`, `CFU`）或逻辑限定词，直接编辑 `resources/academic_invariants.json` 的 `si_and_academic_units` 与 `logic_markers` 即可，无需修改任何 Python 核心代码。

---

## 7. 能力边界与异常处置说明

- **能力边界**：对于完全缺乏文字层的极度模糊手写扫描件、或内嵌为位图的复杂多行化学反应式/数学矩阵，脚本无法在无视觉大模型介入的情况下凭空还原公式源码，此时将自动降级为 `manual_review_asset` 并提供精确页码坐标供人工接管。
- **预算与容错保护**：针对 Harbor 64 轮请求 / 900 秒超时限制，切片器通过 `--target-max-batches 18` 自动调大单批容量以控制总交互轮次，且每完成一批立即通过 `merge_checkpoint.py` 刷新可交付的 `result.json`（保底交付 `status="partial"`），避免超时空手退出。

---

## 8. 版本、许可与维护信息

- **版本与环境**：`v1.0.0`｜要求 `Python >= 3.9`（零第三方硬依赖，完全离线可用；无任何外部账号或 API Key 依赖）。
- **自检入口**：安装后在 Skill 根目录执行 `python3 scripts/self_test.py` 复验全部 15 项全链路离线回归测试。
- **问题排查**：异常诊断、字体编码降级（`untranslatable_asset`）与返修队列查看分别对应 `$WORK_DIR/ingest_manifest.json`、`$WORK_DIR/repair_queue.json` 与 `$OUTPUT_DIR/verification_report.md`。
