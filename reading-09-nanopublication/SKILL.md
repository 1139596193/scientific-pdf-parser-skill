---
name: nanopublication-scientific-qa
description: "科研阅读问答与 Nanopublication 知识抽取技能：从真实论文 PDF/DOCX/LaTeX/MD 中抽取可独立判真假的原子科研断言、绑定最小充分逐字原文证据与 P{page}-B{seq} 定位、提取防幻觉 PubInfo 来源元数据、回答科研问题并校验因果升级/二手引用/冲突，导出符合 W3C 规范的 result.json、nanopubs.jsonld、nanopubs.trig、nanopubs.csv 与 report.md。适用于 Nanopublication 生成、科研阅读问答依据绑定、断言拆解与质量核验任务。"
---

# Nanopublication 科研阅读：原子断言、逐字证据与来源溯源工作流

本 Skill 面向科研论文阅读与知识单元构建，围绕四大核心标准运行：
1. **拆得细**：每条 `assertion.statement` 仅表达一个可独立判断真假的原子命题，严禁将整段摘要或复合多指标句塞入单条断言。
2. **对得上**：每条断言绑定最小充分逐字引文（`evidence_quote`，建议 1–3 句）并由确定性对齐器核验 `[P{page}-B{seq}]` 与字符跨度。
3. **追得回**：来源元数据（`pubinfo`）逐字段绑定提取来源，缺失字段诚实置 `null`，严禁脑补 DOI/arXiv ID/年份/作者；区分一手发现（`primary_study_finding`）与二手引用（`cited_prior_work`）。
4. **读得懂**：严格保留 `/input/case.json` 原始 `case_id` 与嵌套 `output_contract`，同步输出 `result.json`、`nanopubs.jsonld`、`nanopubs.trig`、`nanopubs.csv`、`report.md` 与 `audit_report.json`。

---

## 核心路径与运行红线

- **Skill 脚本与规范目录**：`/skills/weekly-skill/scripts/` 与 `/skills/weekly-skill/references/`（本地复用时即本目录 `scripts/` 与 `references/`）。
- **标准输入**：`/input/case.json` 与 `/input/task.md` 及原始论文材料（无预提取答案层）。
- **工作与交付目录**：工作中间产物写入 `/app/work/`；最终可复核交付物写入 `/app/output/`。
- **执行红线**：
  - 始终使用 `python3 -B`（或 `python -B`）运行脚本，避免产生缓存污染。
  - 先读取实际解析文本，绝不根据文件名或标签猜测论文内容。
  - 遇到扫描无文本层、字体乱码、纯图表数值不可读、缺失 DOI 或原文未报告的问题，必须诚实降级 `status = "partial"` 或 `"unanswerable"` 并写入 `uncertainty` 与 `manual_review_checklist`。

---

## 6 步确定性执行流水线

### 第 1 步：材料摄取与问题识别
```bash
python3 -B /skills/weekly-skill/scripts/case_ingestor.py \
  --input-dir /input \
  --work-dir /app/work
```
输出 `/app/work/source_units.json`、`/app/work/ingest_manifest.json`、`/app/work/case_manifest.json`（自动提取 `research_questions`、`target_formats` 与原始 `output_contract`）。

### 第 2 步：双栏版面索引与分包阅读包构建
```bash
python3 -B /skills/weekly-skill/scripts/structure_indexer.py \
  --input-dir /input \
  --work-dir /app/work \
  --output-dir /app/output
```
生成 `/app/work/evidence_index.json`（含 `P{page}-B{seq:03d}`、10 枚举 `section_role`、`has_citation_markers`、`has_quantitative_signals`、`referenced_assets`）、`/app/work/paper_signals.json`、受 Token 预算保护的 `/app/work/reading_packets/packet_*.md`，以及可直接检索与逐字复制引文的 `/app/work/anchors_compact.txt`（无需自建转储脚本）。

### 第 3 步：防幻觉来源元数据提取与早落盘保底
```bash
python3 -B /skills/weekly-skill/scripts/pubinfo_extractor.py \
  --work-dir /app/work

python3 -B /skills/weekly-skill/scripts/nanopub_assembler.py init \
  --ingest-manifest /app/work/case_manifest.json \
  --structure-index /app/work/evidence_index.json \
  --pubinfo /app/work/pubinfo.json \
  --work-state /app/work/work_state.json \
  --output-dir /app/output
```
`init` 会立即在 `/app/output/result.json` 写出合法且诚实的早落盘 `partial` 骨架，防止 64 轮/900 秒预算耗尽导致空卷。

### 第 4 步：分阅读包增量提取原子断言与问答补丁（`patch`）
逐个阅读 `/app/work/reading_packets/packet_*.md`，每读完 1 个阅读包即生成 1 份小批量补丁 JSON（每次建议 **4–8 条**原子断言，严防单次 16k Token 截断），并调用 `patch` 合入：

```bash
python3 -B /skills/weekly-skill/scripts/nanopub_assembler.py patch \
  --work-state /app/work/work_state.json \
  --patch-json /app/work/patch_001.json
```

#### 精简 JSON Patch 骨架示例（可直接照抄结构）
```json
{
  "nanopublications": [
    {
      "np_id": "TMP-01",
      "doc_id": "D1",
      "block_id": "P6-B002",
      "subject": {"label": "Sparse Router", "entity_type": "model"},
      "predicate": "outperforms",
      "object": {"label": "Dense Attention (by 6.4% F1)", "entity_type": "baseline", "value": 6.4, "unit": "%"},
      "polarity": "affirmed",
      "statement": "Sparse Router outperforms Dense Attention by 6.4% F1 score on LongBench at 128k context (p < 0.01).",
      "qualifiers": {
        "conditions": ["128k context"],
        "population_or_dataset": "LongBench",
        "sample_size": null,
        "statistics": "p < 0.01",
        "temporal_or_dose": null
      },
      "evidence_quote": "On LongBench (128k context), Sparse Router outperforms Dense Attention by 6.4% F1 score (p < 0.01).",
      "evidence_type": "direct_empirical",
      "attribution_type": "primary_study_finding"
    }
  ],
  "qa_answers": [
    {
      "question_id": "Q1",
      "question": "What performance gain does Sparse Router achieve?",
      "answer_status": "answered",
      "answer": "On LongBench (128k context), Sparse Router outperforms Dense Attention by 6.4% F1 score (p < 0.01).",
      "supporting_nanopub_ids": ["TMP-01"]
    }
  ]
}
```

**抽取铁律**：
- `predicate` 必须尽量匹配 `/skills/weekly-skill/references/relation_vocab.json` 中的 **30 个受控谓词**（`causes`, `inhibits`, `activates`, `promotes`, `suppresses`, `mediates`, `requires`, `increases`, `decreases`, `has_value`, `no_significant_effect_on`, `achieves_metric`, `outperforms`, `underperforms`, `comparable_to`, `differs_from`, `correlates_with`, `positively_correlates_with`, `negatively_correlates_with`, `associated_with`, `composed_of`, `part_of`, `located_in`, `measured_by`, `derived_from`, `applicable_to`, `supports`, `contradicts`, `replicates`, `fails_to_replicate`）。
- 复合句（含 `respectively` / 分号多指标 / 多组对照）**必须拆成多条**原子断言。
- 证据若仅写 `correlated with` / `associated with` / `suggests` / `相关`，严禁选用因果谓词或写入 `causes` / `proves` / `导致`。
- 原文含温度、pH、剂量、样本量 `n`、显著性 `p`/`CI`、`in vitro`/`in vivo` 等条件时，必须保留于 `statement` 或 `qualifiers`。
- `introduction`/`discussion` 中转述 `[n]` 或 `(Author et al., Year)` 且非本文贡献句，必须设为 `attribution_type: "cited_prior_work"`、`evidence_type: "secondary_citation"`。

### 第 5 步：跨章节去重升格、冲突图构建与 8 维确定性审计
```bash
python3 -B /skills/weekly-skill/scripts/nanopub_assembler.py finalize \
  --work-state /app/work/work_state.json

python3 -B /skills/weekly-skill/scripts/nanopub_auditor.py \
  --work-state /app/work/work_state.json \
  --structure-index /app/work/evidence_index.json \
  --pubinfo /app/work/pubinfo.json \
  --out /app/work/audit_report.json
```
- `finalize` 会将同一命题的 `abstract`（`T4`）断言自动合并并升格至正文实证/表格/定理（`T1`/`T2`），重定向 `qa_answers` 中的 `np_id`，并生成 `conflicts[]`（极性冲突与定量冲突）。
- 若 `audit_report.json` 中存在 `HIGH` 级问题（如 `COMPOUND_UNSPLIT_CLAIM`、`QUOTE_NOT_FOUND`、`PARAGRAPH_DUMP`、`NUMERIC_HALLUCINATION`、`POLARITY_MISMATCH`、`CAUSAL_OVERCLAIM`、`SECONDARY_CITATION_MISATTRIBUTED`、`QA_UNGROUNDED`），针对出错 `target_id` 写出局部修复补丁重新 `patch` → `finalize` → `nanopub_auditor`（最多修补 2 轮以节省预算）。

### 第 6 步：多格式导出与强门禁校验
```bash
python3 -B /skills/weekly-skill/scripts/render_deliverables.py \
  --work-state /app/work/work_state.json \
  --audit-report /app/work/audit_report.json \
  --output-dir /app/output

python3 -B /skills/weekly-skill/scripts/validate_output.py \
  --output-dir /app/output \
  --ingest-manifest /app/work/case_manifest.json
```
`render_deliverables.py` 自动输出 `result.json`、`nanopubs.jsonld`、`nanopubs.trig`、`nanopubs.csv`、`report.md`、`audit_report.json`，并严格执行诚实 `status` 降级策略；`validate_output.py` 返回码为 `0` 方可结束任务。

---

## 异常与边界处理规范

| 异常场景 | 识别信号 | 强制处置动作 |
|---|---|---|
| 扫描版无文本层 / OCR 不可用 | `unextractable_asset`、`scanned_document_unreadable` | 禁止脑补内容；将 `status` 保持 `partial` 或 `unanswerable`，写入 `uncertainty` 与 `manual_review_checklist`（附页码） |
| 字体 CID/PUA 乱码或纯图数值 | `font_garble_or_cid`、`REQUIRES_VISUAL_TABLE_OR_FIGURE` | 降低置信度并在 `manual_review_checklist` 标记需人工视觉确认 |
| 缺失 DOI / arXiv ID / 年份 | `pubinfo.missing_fields` | 保持字段为 `null`（严禁根据记忆补全），在溯源表如实列明 |
| 论文内部或跨文献结论矛盾 | `conflicts[]`（`polarity_conflict` / `quantitative_conflict`） | 同时保留双方断言，QA 置 `conflicting_evidence` 并在 `report.md` 并列呈现依据 |
| 原文未报道待答问题 | 阅读包均无相关证据 | QA 置 `answer_status: "not_reported"` 或 `"unverifiable"`，明示边界，不得虚构 `np_id` |

---

## 参考规范资源

- 受控谓词词表：[relation_vocab.json](references/relation_vocab.json)（30 个受控谓词及中英别名）
- 数据字段契约：[schema_dictionary.md](references/schema_dictionary.md)
- 8 维核验规则：[quality_rules.md](references/quality_rules.md)
- 20 条可复用样例：[examples_20_nanopubs.json](references/examples_20_nanopubs.json)
- 离线陷阱基准集：[mini_benchmark.json](references/mini_benchmark.json)
