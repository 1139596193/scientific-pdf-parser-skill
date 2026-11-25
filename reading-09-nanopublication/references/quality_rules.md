# Nanopublication 8 维确定性质量核验规则（Quality Rules）

本文档规定 `task_09_nanopub` 抽取、对齐、审计与导出的可复现质量判据。所有高危违规均由确定性脚本拦截或降级标注。

---

## 维度 1：原子性与粒度判据（Atomicity & Granularity）
1. **一断言一真值（Single Falsifiable Claim）**：每条 `assertion.statement` 仅表达一个可独立判断真假的科研命题，包含唯一主谓宾核心关系及必要限定条件。
2. **复合句拆分强制规则**：
   - 包含并列结论（`and`/`both...and`/`分别`/`以及` 连接多个独立实验结果）或多重因果链（`A increases B and consequently reduces C`）的原文句子，必须拆分为多条共享同一 `evidence_quote`（或各自最小片段）的原子 Nanopublication。
   - **禁止整段倾倒（No Paragraph Dump）**：单条 `statement` 不得超过 2 句或 450 字符（英文超过 75 词）；若将摘要整段直接填入 `statement`，直接判定为 `PARAGRAPH_DUMP` 失败。

## 维度 2：最小充分证据跨度判据（Minimal Sufficient Evidence Span）
1. **逐字忠实**：`provenance.evidence_quote` 必须是原文真实存在的子串（允许 PDF 软连字符/断行/连字归一化，严禁 LLM 改写、拼接不存在词句）。
2. **最小充分性阈值**：
   - **过短拦截（`TOO_SHORT_FRAGMENT`）**：`evidence_quote` 少于 20 个字符且非完整短数据句，通常不足以支撑独立真值判断，需补充上下文句或标记降级。
   - **过长倾倒（`PARAGRAPH_DUMP`）**：`evidence_quote` 超过 4 句或超过 700 字符且占所属 `block` 文本长度超过 85%，判定为粒度过粗，应精简至直接支撑断言的 1–3 句最小充分证据片段。
3. **限定条件保真**：若原文证据存在温度、pH、剂量/浓度、样本量 `n`、显著性 `p`/`CI`、时间窗或数据集/物种限制（由 `entity_guard.extract_scientific_qualifiers` 提取），断言必须保留核心限定范围，否则触发 `QUALIFIER_DROPPED` 告警。

## 维度 3：7 级证据强度分级（Evidence Tier Hierarchy）
按来源可靠性从高到低严格分级，并直接约束 `confidence` 上限：
1. **`T1_direct_data`**：正文 `results` 章节中附带明确统计量/定量实测数据的直接实验结果（置信度上限 `1.00`）。
2. **`T2_table_figure`**：直接来自实验结果表格（`TAB-n`）或图像/图注（`FIG-n`）中的定量或定性实测记录（若依赖未完全解析的图内数值，需标注待视觉复核，置信度上限 `0.95`）。
3. **`T3_methods_or_text`**：来自 `methods`、`discussion`、`conclusion`、`appendix` 或一般正文文本中的方法设计、定理证明或实证描述（置信度上限 `0.90`）。
4. **`T4_abstract_summary`**：仅在 `abstract` 中找到支撑而未回溯到正文 `results`/`methods` 或图表（标记 `ABSTRACT_ONLY_EVIDENCE`，置信度上限 `0.75`，建议优先绑定正文区块）。
5. **`T5_indirect_inference`**：需跨两句以上综合推导或结合假设得出的间接推论（`evidence_type="indirect_inference"`，必须显式说明推论链，置信度上限 `0.70`）。
6. **`T6_secondary_citation`**：证据位于 `introduction`/综述语境且带有明确文献引用标记（`[1]`、`(Smith et al., 2020)`），属于转述他人工作而非本论文一手发现（`source_attribution="cited_prior_work"`，严禁冒充本文发现，置信度上限 `0.60`）。
7. **`T7_unverified`**：引文未在原文对齐（`QUOTE_NOT_FOUND`）或源自扫描乱码不可读页（`confidence <= 0.30`，必须进入 `manual_review_checklist` 且不得计入高置信事实）。

## 维度 4：受控关系词表与因果强度防升级（Controlled Relation & Causal Guard）
1. `assertion.predicate.id` 必须严格属于 `relation_vocab.json` 定义的 30 个受控谓词之一。
2. **禁止因果升级（No Causal Upgrade）**：当 `evidence_quote` 仅使用相关/观察性/推测性表达（如 `correlated with`、`associated with`、`linked to`、`suggests`、`may`、`相关`、`关联`），而断言使用了强因果表达（`causes`、`proves`、`leads to`、`determines`、`导致`、`证明`）或选用了 `causal` 类谓词时，判定为 `CAUSAL_STRENGTH_UPGRADE` 高危错误。
3. **极性与数值守恒**：不得丢弃或反转否定词（`not`/`no`/`fail to`/`无显著`/`未`），不得篡改或无中生有数值、单位与小数精度。

## 维度 5：去重与合并优先级规则（Deduplication & Merge Priority）
当多条候选断言表达相同的 `(subject, predicate, object, qualifiers)` 语义三元组（包括对称谓词如 `correlates_with` / `comparable_to` 的主客体交换等价）：
1. 优先保留 **证据等级更高**（`T1` > `T2` > `T3` > `T4` > `T5` > `T6` > `T7`）的记录；
2. 同等级下优先保留 **对齐状态更严格**（`EXACT_MATCH` > `NORMALIZED_MATCH` > `CROSS_BLOCK_MATCH` > `FUZZY_PARTIAL`）且 **限定条件更完整** 的记录；
3. 被合并的次优证据位置作为补充 `secondary_evidence_locations` 保留或记入 `change_records`，不丢弃溯源信息。

## 维度 6：冲突判定规则（Conflict Detection）
1. **内部/跨文真冲突**：同一或不同文档中，相同主体与客体在同一限定条件下出现极性相反或互斥谓词（如 `increases` vs `decreases`、`supports` vs `contradicts`、`outperforms` vs `underperforms`、`affirmed` vs `negated`），且各自对齐到真实原文证据。
2. **处置要求**：严禁静默覆盖或擅自选边站队；必须同时保留双方 Nanopublication，在 `conflicts[]` 中记录冲突类型、双方 `nanopub_id` 与原文位置，并在对应 `qa_answers` 中置 `answer_status="conflicting_evidence"`。
3. **条件分化非冲突**：若两者处于不同实验条件（如不同温度、不同数据集、不同浓度），则通过 `qualifiers` 区分并记录 `resolution_status="scoped_by_condition"`。

## 维度 7：二手引用归属判据（Secondary Citation Attribution）
1. 若匹配块 `has_citation_markers == true` 或引文片段内部包含 `[n]` / `(Author et al., Year)` 且位于 `introduction` / `related_work`：
   - 核查主语或句式是否为转述前人研究（如 `Smith et al. [3] showed...`、`Prior work [12] reported...`、`已有研究表明 [5]...`）。
   - 若属实，必须设置 `source_attribution = "cited_prior_work"`、`evidence_tier = "T6_secondary_citation"`，不得写成当前论文的原生实证结论。
   - 若 `references` 锁区中存在对应条目，可关联参考文献信息但不得伪造未在文中出现的一手实验细节。

## 维度 8：PubInfo 防幻觉与异常降级规则（Anti-Hallucination & Graceful Degradation）
1. **PubInfo 零脑补**：`title`、`authors`、`year`、`venue`、`doi`、`arxiv_id`、`pmid`、`url`、`license` 必须带有真实提取源（`field_sources`）；原文未提供且 `case.json` 未声明的字段必须为 `null`（或 `[]`）并列入 `missing_fields`，严禁凭模型记忆补全 DOI/年份/作者。
2. **异常诚实降级**：遇到扫描版无文本层、字体 CID/PUA 乱码、加密损坏 PDF、证据跨图表无法仅靠文本确认、或 QA 问题在原文完全未报告时：
   - 不得伪造 `success` 或编造答案；
   - 必须在 `uncertainty[]` 与 `manual_review_checklist[]` 标明页码、块号及人工接管建议，将对应 `answer_status` 设为 `not_reported` 或 `unverifiable`，并在全局 `status` 诚实标注 `partial` 或 `unanswerable`。
