# 可观测性：Trace、Token、决策回放

> 本文档定义全链路可观测设计。对应评分要点：「每个 Agent 的 Prompt、输入、输出、决策过程、Token 消耗均有日志 / Trace 可查」。
>
> **文档口径**：§ 1–12 区分「已实现」与「设计草案 / 未实现」。真正落地的两条链路是：
> 1. **进程内环形缓冲**（`backend/observability/llm_call_log.py`）→ 前端 Trace tab 实时拉流水；
> 2. **OPT-IN 的 OTLP tracer**（`backend/observability/tracer.py`）→ 配了 endpoint 才直连 Jaeger / Tempo，否则降级为 no-op。
>
> 唯一的 trace 持久化是 `llm_calls` 这张 **jsonb 表**（`backend/storage/sql.py:145-158`），由 `Orchestrator._persist_node_llm_calls`（`backend/orchestrator/orchestrator.py:621`）从环形缓冲落库。**没有** 关系型 `traces` / `spans` / `tool_calls` 表，**没有** LangSmith 双写，**没有** Redis-Stream 缓冲 / S3 大字段下沉。§ 13 的字段语义与 § 8 的决策回放 UI 是已落地内容，准确。

---

## 1. 目标

- 任意 Agent 调用都能回放：system prompt 预览 + LLM 响应预览 + token / 耗时 + finish_reason（详见 § 13 字段语义）
- Token 消耗按 项目 / 节点 / Agent 维度可聚合（前端 Trace tab + `/api/metrics/aggregate`）
- 用户在前端可查任意节点的执行细节（决策回放 UI，§ 8）
- 错误诊断：失败节点在 DAG 上高亮，点击下钻到该节点的 LLM 调用流水

> 注：早期设计稿曾提「完整时间旅行 / 任意时刻 DAG 快照回放」，**未实现**。当前是「按节点查最新流水」而非「按任意时间戳重建状态」。

---

## 2. 数据模型（实际）

实际只有两层：

```
Trace (项目级，trace_id = run 维度)
  └── LLM Call (单次 LLM 调用，记录在环形缓冲 / llm_calls jsonb 表)
```

- `trace_id`：一次 run 共用，经 `ContextVar` 关联到每条 LLM 流水（`llm_call_log.py:22`）
- `node_id` / `agent_name`：每条流水带节点与 Agent 归属，由 `BaseAgent.invoke` 入口 set / 出口 reset
- 单条记录的字段见 `LLMCallRecord`（`llm_call_log.py:27-45`）：`timestamp / trace_id / span_id / node_id / agent_name / model / phase / tokens_input / tokens_output / duration_s / finish_reason / cost_usd / prompt_preview / response_preview`

> **设计草案（未实现）**：早期稿设想的 `Trace → Span → LLM Call / Tool Call` 三层关系模型、独立 `span_id` 复用规则、`TraceRecord` / `LLMCallRecord` 关系表 schema 均**未落地**。OTLP tracer（§ 13）在导出到 Jaeger 时会构造 `agent.<name>` → `llm.chat` 的 span 树，但那是 OTel 内存中的 span，不入本地 PG。

---

## 3. 必须记录的字段（实际落地）

每条 LLM 调用记录（`push_call`，`llm_call_log.py:84-114`）：

- `model`、`phase`（tool_call / json_mode / freeform / retry）
- `tokens_input` / `tokens_output`、`cost_usd`、`finish_reason`
- `duration_s`
- `prompt_preview` / `response_preview`（**截断预览**，非完整原文）

> **设计草案（未实现）**：早期稿要求「完整 system prompt + 完整 messages 历史 + 完整 response（含 tool_use blocks）+ temperature / max_tokens」全量入库。当前环形缓冲只存**预览**，不存全量 messages 历史；temperature / max_tokens 不记录。工具调用的独立记录链路（参数 + 完整返回 + error）也**未实现**——工具调用只在 OPT-IN 的 OTLP tracer 里作为 `tool.<name>` 子 span 导出（§ 13.2），不入环形缓冲 / `llm_calls` 表。

---

## 4. Trace 注入机制（实际）

`BaseAgent.invoke()`（`backend/agents/_base.py`）在入口 `set_trace_context(trace_id, span_id, node_id, agent_name)`，出口 `reset_trace_context`。`self.llm` 被 `_TrackingLLMWrapper` 包裹，每次 `chat()` 完成后：

1. 累加 token，调 `backend.llm.pricing.estimate_cost()` 算 `cost_usd`；
2. `push_call(...)` 写入进程内环形缓冲（自动从 `ContextVar` 读 trace 上下文，无需改 LLM 接口签名）；
3. 若配置了 OTLP，同时 `span.add_llm_call(...)` 在当前 `agent.<name>` span 下开 `llm.chat` 子 span（§ 13.2）。

落库时机：`Orchestrator` 在节点完成后调 `_persist_node_llm_calls`（`orchestrator.py:365 / 580 / 621`），把该节点的流水写入 `llm_calls` jsonb 表。

> **设计草案（未实现）**：早期稿的 `span.flush()` 同步「写 PG + 实时推送 WebSocket」**未实现**；环形缓冲是内存 `deque(maxlen=10000)`，前端**轮询** `/api/llm-calls` 拉取，不走 WebSocket 推送。

---

## 5. 存储（实际）

### 5.1 进程内环形缓冲（前端 Trace tab 的实时源）

`backend/observability/llm_call_log.py`：`deque(maxlen=10000)`，**进程内、重启清空**。前端 Trace tab 经 `/api/llm-calls` / `/api/projects/{id}/llm-calls`（`backend/api/routes/meta.py`）拉流水。

### 5.2 唯一的持久化：`llm_calls` jsonb 表

`backend/storage/sql.py:145-158`：

```sql
CREATE TABLE IF NOT EXISTS llm_calls (
    seq        bigserial PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    node_id    text,
    agent_name text,
    ts         double precision NOT NULL DEFAULT 0,
    payload    jsonb NOT NULL          -- 整条 LLMCallRecord 序列化
);
```

由 `Orchestrator._persist_node_llm_calls`（`orchestrator.py:621`）从环形缓冲落库。**这是全系统唯一的 trace 持久化。**

> **设计草案（未实现）**：早期稿设想的关系型 `traces` / `spans` / `tool_calls` 三表、`span → llm_calls` 外键、`input_snapshot` / `output_snapshot` jsonb 列、`> 256KB` 大字段落 S3 / 本地文件再存指针——**均未实现**。I/O 快照另由 `backend/observability/io_snapshot.py` 处理，与上述关系模型无关。

### 5.3 外部 Trace 系统

**无 LangSmith / LANGCHAIN_API_KEY 集成。** 早期稿提到的「双写 LangSmith + 本地」**不存在**，请勿据此配置。唯一的外部 trace 出口是 OPT-IN 的 OTLP exporter（§ 13），直连 Jaeger / Tempo，与 LangSmith 无关。

---

## 6. Token 计量

### 6.1 聚合（实际）

- 单节点 / 单 Agent / 单项目：前端 Trace tab 按 `node_id` / `agent_name` / `trace_id` 过滤环形缓冲流水（`list_calls`，`llm_call_log.py:120`）。
- 项目级业务指标 `total_tokens` / `total_cost_usd`：由 `compute_project_metrics`（`backend/orchestrator/metrics.py`）汇总，落 `Project.metrics`，详见 [METRICS.md](METRICS.md)。
- 跨项目：`/api/metrics/aggregate`（`backend/api/routes/meta.py`）在应用层遍历项目求和，**非** SQL view。

> **设计草案（未实现）**：早期稿的「单模型 / 时间窗 SQL view 多维聚合」**未实现**。

### 6.2 成本计算

每次 LLM 调用的 `cost_usd` 由 `backend.llm.pricing.estimate_cost()` 算出（`cost = tokens_input * P_in + tokens_output * P_out`，价目表见 `backend/llm/pricing.py`）。注意豆包 EP 走方舟控制台计费，此处估为 0。

### 6.3 限额

> **设计草案（未实现）**：早期稿的「项目级软 / 硬上限、超额暂停 / 中止」**未实现**。当前仅在单次 LLM 调用层面受 `max_tokens` 约束。

---

## 7. （原「性能」一节——设计草案，未实现）

早期稿设想的「Trace 写入走 Redis Stream 缓冲、worker 异步落 PG、大字段单独落对象存储、前端游标分页」**均未实现**。实际写入是同步落环形缓冲（内存），节点完成后批量落 `llm_calls` 表；前端按 `limit`（默认 200）拉流水，无游标分页。

---

## 8. 决策回放 UI

> 这是已落地的前瞻性亮点（前端 `frontend/src/components/trace/`：`trace-layout.tsx` / `trace-row.tsx` / `trace-summary.tsx` / `llm-call-detail.tsx` / `diff-sheet.tsx`，及节点详情抽屉 `frontend/src/components/dag/node-detail-sheet.tsx`）。
>
> **口径**：这是 **环形缓冲流水 + 持久化 `llm_calls` jsonb 表** 之上的 UI，**不是** § 2 设计草案里的 PG span 关系模型回放。展示的是每节点的 LLM 调用流水（含预览、token、耗时、finish_reason），以及返工轮次 v1 vs v2 的 diff。

### 8.1 时间轴 / 节点列表视图

按节点（含返工版本 reporter / reporter_v2 等）列出执行行，标注状态、耗时、token，QA 返工行展开 issue 与 routing：

```
┌────────────────────────────────────────────────────┐
│ Project: 协作办公竞品 · trace abc123               │
├────────────────────────────────────────────────────┤
│ Collector(Notion)       success  4.2s              │
│ Collector(ClickUp)      success  3.8s              │
│ Extractor(Notion)       success  12.4s             │
│ ...                                                │
│ Reporter                success  18.6s             │
│ QA                      revise   8.2s              │
│   ↳ issues: missing_citation × 3                   │
│   ↳ routing: → Reporter                            │
│ Reporter_v2             success  9.1s              │
│ QA_v2                   pass     7.5s              │
└────────────────────────────────────────────────────┘
```

### 8.2 节点详情抽屉

点击任一节点（`node-detail-sheet.tsx`）展示该节点的 Overview（状态 / 耗时 / token / cost）+ 该节点下的 LLM 调用流水列表。

### 8.3 LLM 调用详情

点击单次 LLM call（`llm-call-detail.tsx`）展示 `model` / `phase` / `finish_reason` / token / `cost_usd`，以及 **prompt / response 预览**（环形缓冲只存预览，非全量原文）。

### 8.4 返工 diff 视图

`diff-sheet.tsx`：v1 vs v2 的 prompt / output diff（QA 返工时对比改了什么）。

> **口径修正**：早期稿提的「DAG 节点叠加 token / 耗时小标」「失败 span 红框点击看完整 stack trace」是设计意向；当前实现以节点列表 + 抽屉 + diff 为主，未提供 OTel 级别的完整 stack trace 内嵌。

---

## 9. 安全 / 脱敏与保留期

- **脱敏**：写入 OTLP attribute 的 prompt / response / tool args 都过 `backend/tools/sanitizer.py`（`sanitize`，`tracer.py:33,104`），去除邮箱 / 电话 / 身份证 / 信用卡 / API key / Bearer token，符合 [COMPLIANCE.md](COMPLIANCE.md) § 4.1。OTLP attribute 还会按 `_MAX_ATTR_LEN = 4000`（`tracer.py:79`）截断。
- **保留期**：`.env.example` 与 `.env.prod.example` 设有 `TRACE_RETENTION_DAYS=7`，但**该变量当前未被任何代码消费**（无清理任务读取它），即 `llm_calls` 表实际**无自动过期 / 清理逻辑**。早期稿的「90 天默认保留」是过时设计，已被 7 天的占位变量取代，且仍未生效。

---

## 10. （原「实现位置」——已更新为真实文件树）

```
backend/observability/
├── __init__.py            # 导出 build_tracer_from_env / NullTracer / OTLPTracer
├── tracer.py              # NullTracer + OTLPTracer + build_tracer_from_env（OPT-IN OTLP）
├── llm_call_log.py        # 进程内环形缓冲（deque），喂前端 Trace tab；唯一持久化经 orchestrator 落 llm_calls 表
├── io_snapshot.py         # 节点 I/O 快照
├── README.md
└── tests/

frontend/src/components/trace/   # 决策回放 UI（§ 8）
frontend/src/components/dag/node-detail-sheet.tsx
```

> **不存在以下文件**（早期稿曾列出，已删除）：`span.py`、`llm_recorder.py`、`tool_recorder.py`、`observability/sanitizer.py`（脱敏实际在 `backend/tools/sanitizer.py`）、`exporter/{postgres,langsmith,stdout}.py`。

---

## 11. 跨文档关联

- Agent 实现：`BaseAgent.invoke()`（`backend/agents/_base.py`）入口 set / 出口 reset trace 上下文，`_TrackingLLMWrapper` 自动记流水。
- Orchestrator：节点完成后 `_persist_node_llm_calls` 落 `llm_calls` 表（`orchestrator.py:621`）。
- 前端：`frontend/src/components/trace/` + `node-detail-sheet.tsx` 消费流水渲染决策回放（§ 8）。
- 指标：Token / 成本回流业务指标见 [METRICS.md](METRICS.md)。

---

## 12. （保留章节号占位）

本节原为「跨文档关联」，已合并入 § 11。保留编号以免破坏外部锚点引用。

---

## 13. 实施落地（v1.0，已落地，准确）

### 13.1 已落地组件

`backend/observability/tracer.py` 提供两套 `TracerProtocol` 实现：

| 实现 | 用途 | 何时返回 |
|---|---|---|
| `OTLPTracer` | OTel SDK + OTLP HTTP exporter，直连 Jaeger / Tempo | 配置了 `OTEL_EXPORTER_OTLP_ENDPOINT` |
| `NullTracer` | 单测 / 离线演示 no-op | 未配置 OTLP 或 `OTEL_TRACES_EXPORTER=none` |

工厂入口：

```python
from backend.observability import build_tracer_from_env
tracer = build_tracer_from_env(service_name="competitive-analysis-agent")
agent = Collector(llm=..., tools=..., tracer=tracer)
```

**OPT-IN**：无 `OTEL_EXPORTER_OTLP_ENDPOINT` / OTel SDK 不通时自动降级 `NullTracer`，**Agent 启动永远不被 trace 配置阻塞**。生产 `.env.prod.example` 默认把 `OTEL_EXPORTER_OTLP_ENDPOINT` 注释掉，即默认 NullTracer。

### 13.2 自动 LLM / 工具子 span

`BaseAgent` 用 `_TrackingLLMWrapper` 包 `self.llm`，每次 `chat()` 完成后：

1. 累加 `tokens_input` / `tokens_output` 到本次 invoke 的用量计数；
2. 调 `backend.llm.pricing.estimate_cost()` 算 `cost_usd` 累加；
3. 在当前 span 上 `add_llm_call(...)`，`OTLPSpan` 在父 span 下开 `llm.chat` 子 span（`tracer.py:161-181`）；工具调用类似，子 span 名 `tool.<tool_name>`（`tracer.py:203`）。
4. `invoke()` 退出时回填 `AgentOutput.tokens_input/output/cost_usd`（子类未自填时）。

**span 名 / attribute（`tracer.py`）**：

- 根 span `agent.<name>`：`agent.name` / `agent.version` / `app.trace_id` / `dag.node_id`（另含 `agent.status` / `agent.confidence` / `agent.tokens_input` / `agent.tokens_output` / `agent.cost_usd`）。
- 子 span `llm.chat`：`llm.model` / `llm.tokens_input` / `llm.tokens_output` / `llm.cost_usd` / `llm.finish_reason`（外加截断脱敏后的 `llm.system_prompt`）。
- 子 span `tool.<name>`：`tool.name` / `tool.arguments` / `tool.result` / `tool.duration_ms` / `tool.error`。

Jaeger UI 上一个 e2e trace 树：

```
agent.collector
  ├── llm.chat (model=gpt-4o-mini, tokens=128/45, cost=$0.000046)
  ├── llm.chat (model=gpt-4o-mini, tokens=210/89)
  └── tool.scrape.firecrawl (duration=1.2s)
agent.extractor
  └── llm.chat (model=deepseek-chat, tokens=1450/620)
...
```

### 13.3 PII 脱敏

所有写入 OTel attribute 的 prompt / response / tool args 都过 `backend.tools.sanitize`（`tracer.py:33`）
（邮箱 / 电话 / 身份证 / 信用卡 / API key / Bearer token），并按 `_MAX_ATTR_LEN = 4000` 截断，符合 [COMPLIANCE.md](COMPLIANCE.md) § 4.1。

### 13.4 dev infra

```bash
# 起 Jaeger
docker compose up -d jaeger

# 配 OTLP（默认 HTTP exporter）
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=competitive-analysis-agent

# 跑 e2e
pytest -m e2e backend/agents
# 打开 UI 验收
open http://localhost:16686
```

支持的环境变量都是 OTel 标准：`OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` /
`OTEL_RESOURCE_ATTRIBUTES` / `OTEL_TRACES_EXPORTER=none`（强制关闭）。

### 13.5 编排接线（已完成）

> **状态更新**：早期稿写「`backend/orchestrator/tracing.py` 是 NullTracer 占位，待迁移」——**已过时**。`backend/orchestrator/tracing.py` **不存在**；tracer 已在 `AgentRegistry.from_env` 直接接线：

```python
# backend/orchestrator/agent_registry.py:159
tracer = build_tracer_from_env(service_name=service_name)
return cls(llm=llm, tracer=tracer, tools=tools, evidence_provider=evidence_provider)
```

即默认无 endpoint 时拿到 `NullTracer`，配了 endpoint 自动升级为 `OTLPTracer`，调用方无需改动。后续若接其他后端，只改 `tracer.py` 实现即可。（注：**无 LangSmith 双写计划**——早期稿提的「v2 接 LangSmith」非当前路线。）
