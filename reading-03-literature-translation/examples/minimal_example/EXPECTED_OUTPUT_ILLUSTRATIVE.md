# Minimal End-to-End Example

> **【Illustrative Example Output Only — Not Formal Benchmark Results】**  
> 本文件仅用于说明本 Skill 的输入契约、标准命令行调用流程及预期交付结构，不代表任何正式测试案例结果或平台评测分数。

---

## 1. 输入文件清单 (`input/`)

- [case.json](input/case.json)：包含 `case_id: "minimal_example_001"` 与目标输出契约 `output_contract`。
- [sample_excerpt.md](input/sample_excerpt.md)：虚构的微流控催化反应器科研短文节选（约 140 词，覆盖标题、含缩略语/物理单位/文献引用/否定与边界限定词的正文、图注及行间 LaTeX 公式）。
- [glossary.csv](input/glossary.csv)：外部预置术语表（锁定 `microfluidic catalytic reactor -> 微流控催化反应器`、`thermal runaway -> 热失控`）。

---

## 2. 完整命令行复现步骤

在任意临时工作目录（如 `/tmp/demo_work` 与 `/tmp/demo_out`）执行：

```bash
SKILL_DIR="<path-to-skill-root>"
IN_DIR="$SKILL_DIR/examples/minimal_example/input"
WORK_DIR="/tmp/demo_work"
OUT_DIR="/tmp/demo_out"

# Step 1: 解析切片并生成批次
python "$SKILL_DIR/scripts/case_ingestor.py" --input-dir "$IN_DIR" --work-dir "$WORK_DIR"

# Step 2: 术语候选抽取 + 编译锁表 + 实体指纹注入
python "$SKILL_DIR/scripts/term_extractor.py" extract --work-dir "$WORK_DIR"
python "$SKILL_DIR/scripts/term_extractor.py" compile \
  --work-dir "$WORK_DIR" \
  --glossary "$SKILL_DIR/examples/minimal_example/sample_glossary_locked.json"
cp "$SKILL_DIR/examples/minimal_example/sample_glossary_locked.json" "$WORK_DIR/glossary.json"
python "$SKILL_DIR/scripts/entity_guard.py" --work-dir "$WORK_DIR" --enrich-batches

# Step 3: 合流批次译文并早落盘刷新
python "$SKILL_DIR/scripts/merge_checkpoint.py" \
  --work-dir "$WORK_DIR" --output-dir "$OUT_DIR" --input-dir "$IN_DIR" \
  --add-batch "$SKILL_DIR/examples/minimal_example/sample_batch_001_out.json"

# Step 4: 6 维确定性审计
python "$SKILL_DIR/scripts/translation_verifier.py" \
  --work-dir "$WORK_DIR" \
  --translations "$WORK_DIR/merged_translations.json"

# Step 5: 最终渲染与严格契约校验
python "$SKILL_DIR/scripts/render_deliverables.py" \
  --work-dir "$WORK_DIR" --output-dir "$OUT_DIR" --input-dir "$IN_DIR" --final
python "$SKILL_DIR/scripts/validate_output.py" \
  --output-dir "$OUT_DIR" --input-dir "$IN_DIR" --strict
```

---

## 3. 预期输出结构示意（`Illustrative Output`）

### A. `bilingual_parallel.md` 片段示意

```markdown
# Bilingual Parallel Translation: `minimal_example_001`

- **Status**: `success` | **Progress**: 3/3 translatable units
- **Language Direction**: `en->zh`

---

## Section: Adaptive Thermal Control in Microfluidic Catalytic Reactors

### [U0001 | p.1 | heading]
> **Source**: Adaptive Thermal Control in Microfluidic Catalytic Reactors

**Translation**: 微流控催化反应器中的自适应热控制

- *Locked Terms*: `microfluidic catalytic reactor` → **微流控催化反应器**
- *Rationale*: Locked 'Microfluidic Catalytic Reactors' -> '微流控催化反应器'.

### [U0002 | p.1 | paragraph]
> **Source**: Microfluidic catalytic reactors (MCRs) enable rapid heat and mass transfer during exothermic chemical synthesis [1], [2]. ... steady-state oscillation amplitudes at most 0.4 K under variable feed velocities.

**Translation**: 微流控催化反应器（MCR）在放热化学合成过程中能够实现快速的传热与传质 [1], [2]。……在可变进料速度下其稳态振荡幅度至多为 0.4 K。

- *Locked Terms*: `microfluidic catalytic reactor` → **微流控催化反应器**, `thermal runaway` → **热失控**, `phase-change` → **相变**
- *Rationale*: Locked terms; preserved negation '不需要', bound '至多', units '350 K / 0.25 ms / 18.4% / 0.4 K', citations [1]-[3], and cross-refs Fig. 1 / Eq. (1).

### [U0004 | p.1 | display_equation]
> **Source**: $$\Delta T_{\max} \le \frac{Q_{\mathrm{in}}}{C_p \dot{m}} \quad (1)$$

**Preserved (`display_equation`)**: `$$\Delta T_{\max} \le \frac{Q_{\mathrm{in}}}{C_p \dot{m}} \quad (1)$$`

- *Rationale*: Preserved verbatim (display_equation).
```

### B. `result.json` 核心结构示意

```json
{
  "case_id": "minimal_example_001",
  "status": "success",
  "progress": {
    "completed_units": 3,
    "total_translatable_units": 3,
    "total_units": 4
  },
  "sources": {
    "dominant_format": "markdown",
    "glossary_files_detected": ["<input_dir>/glossary.csv"]
  },
  "uncertainty": [],
  "change_records": [],
  "verification_summary": {
    "case_id": "minimal_example_001",
    "audit_status": "PASS",
    "metrics": {
      "expected_translatable_units": 3,
      "covered_units": 3,
      "coverage_rate": 1.0,
      "total_term_checks": 5,
      "passed_term_checks": 5,
      "term_consistency_rate": 1.0,
      "total_entity_checks": 10,
      "passed_entity_checks": 10,
      "entity_fidelity_rate": 1.0,
      "error_count": 0,
      "warning_count": 0
    }
  },
  "artifacts": [
    "result.json",
    "bilingual_parallel.md",
    "glossary.md",
    "glossary.json",
    "verification_report.md"
  ]
}
```
