# Analyst Agent · 竞品分析

> 多维度竞品对比分析。把若干 `CompetitorProfile` 转成结构化 `AnalysisResult`，
> 每条结论都绑定来自输入 profile 池的 `evidence_ids`，杜绝幻觉。

完整契约见 [docs/AGENTS.md § 5](../../../docs/AGENTS.md#5-analyst分析-agent)；
幻觉抑制设计见 [docs/HALLUCINATION_CONTROL.md](../../../docs/HALLUCINATION_CONTROL.md)。

## 1. 输入 / 输出

| 字段 | 说明 |
|---|---|
| `AnalystInput.target_product` | 主视角产品名 |
| `AnalystInput.competitors` | 对比竞品名列表 |
| `AnalystInput.profiles` | `{product_name -> CompetitorProfile}` |
| `AnalystInput.dimensions` | 请求分析的维度子集（6 选 N） |
| `AnalystInput.evidence_store_handle` | 可选 RAG handle（v1 透传不消费） |
| `AnalystInput.qa_feedback` | QA 回流反馈（v1 透传不深度消费） |
| `AnalystOutput.result` | `AnalysisResult`（含 `dimensions: dict[Dim, DimensionAnalysis]`） |

## 2. 维度产出

| 维度 | 启发式产出 | comparison_matrix |
|---|---|---|
| `feature_comparison` | industry_extension capability × maturity 矩阵 + 显著差异 claim + 目标差异化亮点 | `{<field>: {<product>: <level>}}` |
| `pricing_comparison` | entry / advanced 档对比 + 最低 / 最高溢价 claim + 定价模式趋同 claim | `{entry_paid_usd / advanced_paid_usd: {<product>: usd}}` |
| `swot` | S/W/O/T 各最多 2 条（`qualifier` 字段标识象限） | None |
| `differentiation_opportunities` | 竞品痛点 → 机会、共同弱项 + 目标 advanced 能力 → 锚点 | None |
| `positioning` | 各产品 positioning 单挑 + 目标用户重叠提示 | `{positioning: {<product>: <statement>}}` |
| `user_feedback` | 每产品首个 positive theme + 首个 pain point | `{overall_rating: {<product>: float}}` |

## 3. 运行方式

### Mock（演示用）

```python
from backend.agents.analyst import Analyst
from backend.agents.analyst.fixtures import load_demo_input

agent = Analyst(mock=True)
inp = load_demo_input(target="Notion", competitors=["ClickUp", "Asana"])
out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
for dim, analysis in out.result.dimensions.items():
    print(dim.value, "→", len(analysis.claims), "claims")
```

### Real（LLM 加持）

```python
agent = Analyst(llm=my_llm_provider, tracer=my_tracer)
out = agent.invoke(inp, trace_id="trace-1", span_id="span-analyst")
```

LLM 不可用 / 返回失败时，自动回退到 `dimensions.py` 里的启发式分析器，保证产出结构合法。
LLM 路径与启发式路径产出形状一致，下游不感知差异。

## 4. 幻觉抑制（核心）

- **Evidence Pool 门禁**：先聚合所有输入 profile 的 `evidence_refs` / `evidence_ids` 形成合法池。
  - `_scrub_claims` 过滤每条 claim：仅保留池内 evidence；纯非法 → 丢弃；部分非法 → 降置信 0.1。
  - `_post_validate` 兜底：如有 claim 仍引用池外 evidence → 抛 `INSUFFICIENT_EVIDENCE` → BaseAgent 转 `NEEDS_REWORK`。
- **Schema 约束**：每条 `AnalysisClaim.evidence_ids` 至少 1 条（Pydantic `min_length=1`）。
- **Confidence 调控**：启发式置信度按"维度完整性 + claim 数 + profile 覆盖率"加权；
  低于 0.6 时 BaseAgent 自动强制 `self_critique` 非空。
- **counter_evidence_ids**：启发式在 SWOT 与 differentiation 中不主动产出 counter，
  但 Schema 保留字段，等 LLM 路径或 QA 回流再填充。

## 5. 错误码

| Code | 含义 | 触发 |
|---|---|---|
| `PROFILE_INCOMPLETE` | 某竞品 profile 缺失 | `inp.competitors` 中产品在 `inp.profiles` 找不到 |
| `INSUFFICIENT_EVIDENCE` | 丢弃幻觉 claim / 兜底拦截 | `_scrub_claims` / `_post_validate` |
| `DIMENSION_NOT_APPLICABLE` | 请求的维度未在产出中 | `_post_validate` 兜底（理论上 `_build_output` 会覆盖所有 dim） |
| `LLM_SCHEMA_INVALID` | LLM 响应解析失败 | 真实模式下 LLM 路径异常 → fallback 启发式 |

## 6. 测试

```bash
pytest backend/agents/analyst -q
```

覆盖：mock 全维度、profile 缺失 PARTIAL、启发式语义（pricing/feature）、`_scrub_claims`、
`_post_validate` 双 case、Schema 严格性（`extra=forbid`）、低覆盖率低置信、evidence 池汇总、
真实模式 LLM 失败回落到启发式。

## 7. 实现布局

```
backend/agents/analyst/
├── __init__.py            # 公共出口
├── agent.py               # Analyst 类（_run / _run_mock / _scrub / _post_validate）
├── dimensions.py          # 6 个维度的启发式分析器 + evidence 池聚合
├── fixtures.py            # 从 fixtures/mock_data/competitor_profiles/*.json 装载 AnalystInput
├── prompts/               # 6 维度 LLM prompt（## System / ## User 分段）
├── README.md
└── tests/
```

## 8. 已知限制 / TODO

- v1 不消费 `qa_feedback` 内的具体 issue 指导，等 M3 闭环时联调 Q 窗口。
- v1 不接 RAG（`evidence_store_handle` 透传不使用），等 I 窗口提供 Evidence store 后切换。
- LLM 路径目前没有 JSON-schema 重试，靠启发式 fallback 兜底；下一轮接入 LLMProvider 后补 2 次重试 + 错误注入。
- 自一致性（self-consistency）目前未启用，关键维度（SWOT / DIFFERENTIATION）可在 P2 加入 N=3 采样。
