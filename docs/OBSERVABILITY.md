# 可观测性：Trace、Token、决策回放

> 本文档定义全链路可观测设计。对应评分要点：「每个 Agent 的 Prompt、输入、输出、决策过程、Token 消耗均有日志 / Trace 可查」。

---

## 1. 目标

- 任意 Agent 调用都能完整回放：system prompt + messages + LLM 响应 + 工具调用 + 输出
- Token 消耗按 Agent / 任务 / 项目维度可聚合
- 用户在前端可"时间旅行"：选任意时刻看 DAG 状态、查任意节点的完整执行细节
- 错误诊断：失败时 trace 一键定位到出错的 LLM 调用 / 工具调用

---

## 2. 三层模型

```
Trace (项目级)
  └── Span (Agent 调用级)
        ├── LLM Call (单次 LLM 调用)
        └── Tool Call (单次工具调用)
```

- `trace_id`：一个项目从开始到结束共用一个 trace_id
- `span_id`：每次 Agent 调用一个 span_id；feedback 重做也是新 span（不复用）
- `call_id`：每次 LLM / 工具调用一个 call_id

---

## 3. 数据模型

完整定义见 [SCHEMA.md](SCHEMA.md) § 7。简要：

```python
class TraceRecord(BaseModel):
    trace_id: str
    span_id:  str
    parent_span_id: str | None

    agent_name: str
    agent_version: str
    node_id: str | None

    started_at: datetime
    ended_at:   datetime | None
    status:     AgentStatus

    llm_calls:  list[LLMCallRecord]
    tool_calls: list[ToolCallRecord]

    input_snapshot:  dict       # 脱敏后的输入
    output_snapshot: dict       # 脱敏后的输出

    tokens_input:  int
    tokens_output: int
    cost_usd:      float
    duration_ms:   int

    self_critique: str
    confidence:    float
```

---

## 4. 必须记录的字段

任何 LLM 调用必须记录：

- **完整 system prompt**（外置文件 + 渲染后实际值都存）
- **完整 messages**（含 user / assistant 历史）
- **完整 response**（含 tool_use blocks）
- 模型名、temperature、max_tokens
- input_tokens / output_tokens
- finish_reason
- 调用耗时

任何工具调用必须记录：

- 工具名 + 参数（如 search query）
- 工具返回（完整）
- 耗时 + error

---

## 5. Trace 注入机制

```python
# BaseAgent.invoke()（伪代码）
def invoke(self, inp, *, trace_id, span_id):
    parent_span_id = current_span_id()
    with self.tracer.span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_name=self.name,
    ) as span:
        try:
            out = self._run(inp)
            span.set_output(out)
            span.set_status("success")
        except Exception as e:
            span.set_error(e)
            span.set_status("failed")
            raise
        finally:
            span.flush()  # 写 PG + 实时推送 WebSocket
```

所有 LLMProvider 调用、Tool 调用自动 attach 到当前 span。

---

## 6. 存储

### 6.1 关系库

```sql
CREATE TABLE traces (
  trace_id text PRIMARY KEY,
  project_id text,
  started_at timestamptz,
  ended_at   timestamptz,
  total_tokens_input  bigint,
  total_tokens_output bigint,
  total_cost_usd      numeric,
  total_duration_ms   bigint
);

CREATE TABLE spans (
  span_id text PRIMARY KEY,
  trace_id text REFERENCES traces(trace_id),
  parent_span_id text,
  agent_name text,
  agent_version text,
  node_id text,
  started_at timestamptz,
  ended_at   timestamptz,
  status text,
  tokens_input  int,
  tokens_output int,
  cost_usd numeric,
  duration_ms int,
  self_critique text,
  confidence real,
  input_snapshot  jsonb,
  output_snapshot jsonb
);

CREATE TABLE llm_calls (
  call_id text PRIMARY KEY,
  span_id text REFERENCES spans(span_id),
  model text,
  system_prompt text,
  messages jsonb,
  response jsonb,
  tokens_input int,
  tokens_output int,
  finish_reason text,
  duration_ms int,
  created_at timestamptz
);

CREATE TABLE tool_calls (
  call_id text PRIMARY KEY,
  span_id text REFERENCES spans(span_id),
  tool_name text,
  arguments jsonb,
  result jsonb,
  duration_ms int,
  error text,
  created_at timestamptz
);
```

### 6.2 对象存储（可选）

特别大的 prompt / response（如长文本抓取后塞给 LLM 的 messages）超过 256KB 时落 S3 / 本地文件，PG 只存指针。

### 6.3 外部 Trace 系统

可选接入 LangSmith：
- 通过环境变量 `LANGCHAIN_API_KEY` 启用
- BaseAgent.invoke() 同时双写 LangSmith + 本地

不强依赖外部，本地 trace 表是 source of truth。

---

## 7. Token 计量

### 7.1 多维度聚合

通过 SQL view / 计算字段提供：

| 视角 | 聚合 | 用途 |
|---|---|---|
| 单 span | sum(llm_calls.tokens) | 单次 Agent 调用消耗 |
| 单 Agent | sum 全部该 agent 的 span | "Reporter Agent 平均消耗 X tokens" |
| 单项目 | sum 项目下所有 span | 项目成本核算 |
| 单模型 | sum 按 model 分组 | 切换模型对比 |
| 时间窗 | sum 按天/周 | 业务运营 |

### 7.2 成本计算

每个模型在 `LLMProvider` 注册其价格：

```python
PRICING = {
    "claude-opus-4-7":   {"input": 15/1e6, "output": 75/1e6},   # USD per token
    "claude-sonnet-4-6": {"input": 3/1e6,  "output": 15/1e6},
    "deepseek-chat":     {"input": 0.14/1e6, "output": 0.28/1e6},
}
```

`cost_usd = tokens_input * P_in + tokens_output * P_out`。

### 7.3 限额

- 项目级软上限：超过 → 警告 + 暂停
- 项目级硬上限：超过 → 中止
- 单次调用上限：超过 max_tokens 即失败

---

## 8. 决策回放 UI

这是**前瞻性亮点之一**（详见 [INNOVATIONS.md](INNOVATIONS.md) § 3）。

### 8.1 时间轴视图

```
┌────────────────────────────────────────────────────┐
│ Project: 协作办公竞品 · trace abc123               │
├────────────────────────────────────────────────────┤
│ 14:00  ┃ Collector(Notion)       success  4.2s    │
│ 14:00  ┃ Collector(ClickUp)      success  3.8s    │
│ 14:01  ┃ Collector(Asana)        success  5.1s    │
│ 14:01  ┃ Extractor(Notion)       success  12.4s   │
│ ...                                                │
│ 14:03  ┃ Reporter                success  18.6s   │
│ 14:04  ┃ QA                      revise   8.2s    │
│        │   ↳ issues: missing_citation × 3         │
│        │   ↳ routing: → Reporter                  │
│ 14:04  ┃ Reporter_v2             success  9.1s    │
│ 14:05  ┃ QA_v2                   pass     7.5s    │
└────────────────────────────────────────────────────┘
```

### 8.2 节点详情抽屉

点击任一节点：

```
┌──────────────────────────────────────────────┐
│ Reporter · span_xyz · v1                     │
├──────────────────────────────────────────────┤
│ [Overview] [LLM Calls] [Tool Calls] [I/O]    │
│                                              │
│ Overview                                     │
│   Status:     success                        │
│   Duration:   18.6s                          │
│   Tokens:     8,432 in / 2,103 out           │
│   Cost:       $0.32                          │
│   Confidence: 0.84                           │
│   Self-critique: "已为所有量化结论提供引用… "│
│                                              │
│ LLM Calls (5)                                │
│   #1 sonnet-4-6  3.2s  1234/421 tokens      │
│   #2 sonnet-4-6  4.1s  1789/512 tokens      │
│   ...                                        │
│                                              │
│ Tool Calls (0)                               │
│                                              │
│ Input / Output (展开查看完整 JSON)            │
└──────────────────────────────────────────────┘
```

### 8.3 LLM 调用详情

点击单次 LLM call：

```
┌──────────────────────────────────────────────┐
│ LLM Call #1 · span_xyz                       │
├──────────────────────────────────────────────┤
│ Model:        claude-sonnet-4-6              │
│ Temperature:  0.5                            │
│ Max tokens:   4096                           │
│ Finish reason: stop                          │
│                                              │
│ System prompt:                               │
│   ┃ You are a competitive analysis report   │
│   ┃ writer. Output strict JSON conforming…  │
│                                              │
│ Messages:                                    │
│   user:    "Compose the pricing section..." │
│   assistant: { ... structured output ... }  │
│                                              │
│ [复制 prompt] [作为 fixture 导出]            │
└──────────────────────────────────────────────┘
```

### 8.4 可视化能力

- **DAG 节点颜色叠加 trace 状态**：DAG 图上每个节点带 token / 耗时小标
- **错误高亮**：失败 span 红框，点击直接看 stack trace
- **diff 视图**：v1 vs v2 prompt diff、output diff（QA 重做时特别有用）

---

## 9. 安全 / 脱敏

- `input_snapshot` / `output_snapshot` 写库前过 `sanitize()`：去除手机号 / 邮箱 / API key
- 用户上传内容默认不入 trace（除非显式同意）
- Trace 数据保留 90 天默认，可配

---

## 10. 性能

- Trace 写入异步：不阻塞 Agent 执行（用 Redis Stream 缓冲，worker 落 PG）
- 大字段（prompt / messages）单独落对象存储，PG 只存指针
- 前端时间轴用游标分页，避免一次拉太多

---

## 11. 实现位置

```
backend/observability/
├── __init__.py
├── tracer.py             # Tracer 类，BaseAgent 用
├── span.py               # Span 上下文管理
├── llm_recorder.py       # LLMProvider 自动 attach
├── tool_recorder.py
├── sanitizer.py          # 脱敏
├── exporter/
│   ├── postgres.py
│   ├── langsmith.py      # 可选
│   └── stdout.py         # 开发时调试
└── tests/
```

---

## 12. 跨文档关联

- 每个 Agent 实现窗口：参考本文档实现 `BaseAgent.invoke()` 时的 Trace 注入
- O 窗口：每个 DAGNode 执行前后开关 span
- F 窗口：消费 trace 数据渲染时间轴 + 详情抽屉

---

## 13. 实施落地（I 窗口产出，v1.0）

### 13.1 已落地组件

`backend/observability/` 提供两套 `TracerProtocol` 实现：

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

无 OTel SDK / endpoint 不通时自动降级 `NullTracer`，**Agent 启动永远不被 trace 配置阻塞**。

### 13.2 自动 LLM 子 span

`BaseAgent` 用 `_TrackingLLMWrapper` 包 `self.llm`，每次 `chat()` 完成后：

1. 累加 `tokens_input` / `tokens_output` 到本次 invoke 的 `_LLMUsageCounter`
2. 调 `backend.llm.pricing.estimate_cost()` 算 `cost_usd` 也累加
3. 在当前 span 上调 `add_llm_call(model, system_prompt, messages, response, ...)`，OTLPSpan 在父 span 下开 `llm.chat` 子 span
4. `invoke()` 退出时：`AgentOutput.tokens_input/output/cost_usd` 如果子类没自填则自动回填

效果：Jaeger UI 上一个 e2e 跑出来的 trace 树长这样：

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

所有写入 OTel attribute 的 prompt / response / tool args 都过 `backend.tools.sanitize`
（邮箱 / 电话 / 身份证 / 信用卡 / API key / Bearer token），符合 docs/COMPLIANCE.md § 4.1。

### 13.4 dev infra

```bash
# 起 Jaeger
docker compose up -d jaeger

# 配 OTLP（默认 HTTP exporter）
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=competitive-analysis-agent

# 跑 e2e
pytest backend/agents/.../tests/test_e2e_real.py -k smoke
# 打开 UI 验收
open http://localhost:16686
```

支持的环境变量都是 OTel 标准：`OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` /
`OTEL_RESOURCE_ATTRIBUTES` / `OTEL_TRACES_EXPORTER=none`（强制关闭）。

### 13.5 给 O 窗口的迁移

`backend/orchestrator/tracing.py` 目前是 NullTracer 占位。完整切换时：

```python
# backend/orchestrator/agent_registry.py（伪代码）
from backend.observability import build_tracer_from_env

def from_env():
    tracer = build_tracer_from_env()  # 代替 NullTracer()
    return Collector(llm=..., tools=..., tracer=tracer), ...
```

`backend.observability.NullTracer` 与 `orchestrator.tracing.NullTracer` 行为等价，
零运行时风险。后续 v2 接 LangSmith 双写时只改 `OTLPTracer` 实现，不动调用方。
