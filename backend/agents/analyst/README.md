# Analyst Agent · 竞品分析

> 详细契约见 [docs/AGENTS.md § 5](../../../docs/AGENTS.md#5-analyst分析-agent)。

## 职责

对多个 `CompetitorProfile` 进行**多维度对比分析**，输出 `AnalysisResult`。每个 `claim` 必须绑定 `evidence_ids`。

## 输入 / 输出

- Input：`AnalystInput`（含 target_product + competitors + profiles + dimensions）
- Output：`AnalystOutput`（含 `result: AnalysisResult`）

## 支持的维度

- `feature_comparison` 功能对比
- `pricing_comparison` 定价对比
- `user_feedback` 用户反馈洞察
- `swot` SWOT 分析
- `differentiation_opportunities` 差异化机会
- `positioning` 产品定位对比

## 关键工具

- LLM 推理（带 RAG）
- `evidence.retrieve(claim_text)`：按 claim 反查支撑证据

## 实现位置

```
backend/agents/analyst/
├── agent.py
├── prompts/
│   ├── feature_comparison.md
│   ├── pricing_comparison.md
│   ├── swot.md
│   ├── differentiation.md
│   └── positioning.md
├── tools.py
├── README.md
└── tests/
```

## 关键约束

- 每个 `AnalysisClaim.evidence_ids` ≥ 1，否则拒绝
- 鼓励输出 `counter_evidence_ids`（反例），体现严谨
- 各维度独立 prompt，避免互相污染
- evidence 必须来自输入 profile 的 evidence_refs 池，不允许外部引入

## 已知限制 / TODO

- v1：feature 对比矩阵基于关键词匹配（不一定精准）
- v1：differentiation 维度对 niche 竞品效果有限
- v2：引入 self-consistency 提升关键结论稳定性

## 责任窗口

**A 窗口**。M0 后开始，M1 完成 v1。
