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

## 实现状态

- v1 已落地，6 维度全部覆盖，LLM 不可用时所有 checker 都有规则路径降级。
- Mock 模式按 `inp.draft.version` 切 fixture：v1 → `needs_revision`，v2 → `pass`，
  专供 Orchestrator 反馈闭环演示。
- 严重度权重：minor=1 / major=5 / critical=20；`total_weight` 0→pass、≤10→
  needs_revision 非阻塞、>10→阻塞、>25 或 ≥2 critical → reject。
- 防死循环：同 `dimension+location` 累计出现 ≥ 3 次自动降级 minor；
  `prior_verdicts` 累计 ≥ 5 强制 `blocking=False` + 注入 `MAX_RETRY_REACHED` 告警。
- 测试 19 例覆盖：mock 双分支、真实 6 维度跑通、缺引用 / 过期 evidence /
  禁用词 / 价格冲突 / 防死循环 / `_post_validate` 强校验。

```bash
pytest backend/agents/qa/
```

## 已知限制 / TODO

- v1：entailment 用 LLM（成本较高），v2 可探索专门 NLI 模型
- v1：freshness 检查仅看 `collected_at`，未对内容是否已变化做主动验证
- v1：`logic_consistency` 规则路径仅覆盖价格冲突 + SWOT 同义反复，复杂语义矛盾依赖 LLM
- v1：fixture profile 的 `industry_extension` 填充率偏低，schema_completeness 会
  对此报警；后续可视情况调整阈值或补齐 fixture
