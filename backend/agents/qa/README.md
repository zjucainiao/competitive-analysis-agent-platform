# QA Agent · 质检审查

> 详细契约见 [docs/AGENTS.md § 7](../../../docs/AGENTS.md#7-qa质检-agent) 和 [docs/QA.md](../../../docs/QA.md)。

## 职责

对 `ReportDraft` 进行 **6 维度**审查，输出 `QAVerdict` + 路由决策。**不修改报告**，只负责诊断和路由。

## 输入 / 输出

- Input：`QAInput`（含 draft + analysis + profiles + evidence_store_handle + prior_verdicts）
- Output：`QAOutput`（含 `verdict: QAVerdict`，含 issues + routing + blocking）

## 6 个检查维度

| Dimension | 检查 |
|---|---|
| `fact_consistency` | LLM entailment：段落是否被 evidence 蕴含 |
| `evidence_completeness` | 关键结论 / 段落 evidence 覆盖率 |
| `schema_completeness` | Profile 必填字段填充率 |
| `logic_consistency` | 报告前后是否矛盾 |
| `freshness` | 引用 evidence 是否过期 |
| `expression` | 表达规范性（禁用词、第一人称、绝对化） |

## 实现位置

```
backend/agents/qa/
├── agent.py
├── checkers/
│   ├── fact_consistency.py
│   ├── evidence_completeness.py
│   ├── schema_completeness.py
│   ├── logic_consistency.py
│   ├── freshness.py
│   └── expression.py
├── routing.py                # ISSUE_TYPE → TARGET_AGENT
├── prompts/
│   ├── entailment.md
│   ├── contradiction.md
│   └── expression.md
├── README.md
└── tests/
```

## 关键约束

- 6 维度独立 prompt，禁止合并
- 每个 issue 必须指向具体段落 / 句子（`location` 字段精确到 `report.sections[3].paragraphs[2]`）
- 路由决策必须附 payload（告诉上游 Agent 具体要补什么）
- 同一 issue 反复出现 ≥ 3 次 → 标 `severity=minor` + `blocking=False`，避免死循环
- 不修改报告（report draft 是 immutable，只产 verdict）

## 与业务指标的关联

- 直接贡献：accuracy / coverage / qa_pass_rate
- 所有 verdict 完整持久化，用于 trace 回放

## 已知限制 / TODO

- v1：entailment 用 LLM（成本较高），v2 可探索专门 NLI 模型
- v1：freshness 检查仅看 collected_at，未对内容是否已变化做主动验证

## 责任窗口

**Q 窗口**。M0 后开始，M2 完成 v1（至少 3 维度），M3 完成 6 维度。
