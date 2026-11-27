# 学术润色外部术语表与风格规则模板（Glossary & Style Template）

`glossary_abbr_guard.py` 支持自动发现并加载输入目录中的外部术语表（`.csv`、`.tsv`、`.json`、`.md`）以及 `case.json` 内嵌术语配置。

## 核心字段说明

| 字段名 | 必填 | 含义 | 示例 |
|---|---|---|---|
| `source_term` | 是 | 原文术语或常见变体 | `differential privacy` |
| `preferred_term` | 否* | 润色后全篇统一使用的首选规范表达 | `differential privacy` / `差分隐私` |
| `forbidden_terms` | 否 | 禁止出现的错译/非规范混用词（多词可用 `\|` 或 `;` 分隔） | `difference privacy\|差异隐私` |
| `do_not_edit` | 否 | 布尔值（`true`/`false`），锁定不可改写的专有名词/符号/品牌 | `true` |
| `abbr` | 否 | 规范缩写形式（用于首次定义与回扫校验） | `DP` |
| `notes` | 否 | 领域背景、语域约束或人工备注 | `首次出现使用 differential privacy (DP)` |

> \*若 `do_not_edit` 为 `true` 且未显式给出 `preferred_term`，默认锁定 `source_term` 本身；若同一 `source_term` 对应多个冲突的 `preferred_term`，或同一词既在 `preferred_term` 又在 `forbidden_terms` 中，系统将自动触发 `GLOSSARY_CONFLICT_*` 告警并请求人工确认，绝不静默覆盖。

---

## 1. CSV 示例（`glossary.csv`）

```csv
source_term,preferred_term,forbidden_terms,do_not_edit,abbr,notes
differential privacy,differential privacy,difference privacy|differential secrecy,true,DP,Define as differential privacy (DP) on first mention
量子通道,量子信道,量子管道|量子通道,false,,统一使用中国物理学会规范术语“量子信道”
p-value,P value,p value|P-value,false,,Follow target medical journal style
```

## 2. TSV 示例（`glossary.tsv`）

```tsv
source_term	preferred_term	forbidden_terms	do_not_edit	abbr	notes
convolutional neural network	convolutional neural network	convolution neural network	false	CNN	Define once in Abstract and once in Body
```

## 3. JSON 示例（`glossary.json`）

```json
{
  "target_style": "ieee_acm",
  "spelling_variant": "US",
  "terms": [
    {
      "source_term": "quantum differential privacy",
      "preferred_term": "quantum differential privacy",
      "forbidden_terms": ["quantum difference privacy"],
      "do_not_edit": true,
      "abbr": "QDP",
      "notes": "Keep acronym QDP uppercase"
    },
    {
      "source_term": "entanglement entropy",
      "preferred_term": "entanglement entropy",
      "forbidden_terms": ["entangling entropy"],
      "do_not_edit": false,
      "abbr": "",
      "notes": "Preferred over entangling entropy"
    }
  ]
}
```

## 4. Markdown 表格示例（`glossary.md`）

```markdown
| source_term | preferred_term | forbidden_terms | do_not_edit | abbr | notes |
|---|---|---|---|---|---|
| local differential privacy | local differential privacy | localized differential privacy | false | LDP | Use LDP after first definition |
| 优化算法 | 优化算法 | 最优化方法 | false | | 全篇统一为“优化算法” |
```
