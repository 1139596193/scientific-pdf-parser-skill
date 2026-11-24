# Minimal Illustrative Example (最小调用示例)

> [!NOTE]
> 本文件仅为格式说明与接口示范（Mock Example），不代表任何正式测试题库的运行答案。

### 1. 示例输入：
- Case 配置文件：`/input/case.json`
- 待解析科研论文：`/input/sample_paper.pdf`

### 2. 执行命令：
```bash
python /skills/weekly-skill/scripts/run_pipeline.py \
  --pdf /input/sample_paper.pdf \
  --work-dir /app/work/parsed \
  --case-json /input/case.json
```

### 3. 生成交付物自检：
```bash
python /skills/weekly-skill/scripts/validate_output.py \
  --case-json /input/case.json \
  --result-json /app/output/result.json \
  --doc-json /app/work/parsed/structured_doc.json \
  --out-report /app/work/validation_report.json
```

### 4. 示例产出数据片选：
参考 `examples/sample_result.json`，包含了严格的 `case_id` 对齐、`document` 结构化字段、`sources` 坐标回溯与 `uncertainty` 不确定性声明。
