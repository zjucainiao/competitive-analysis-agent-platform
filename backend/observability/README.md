# `backend/observability` · I 窗口产出

OTLP / Jaeger Tracer 实现，满足 [`backend.agents._base.TracerProtocol`](../agents/_base.py)。

## 最小用法

```python
from backend.observability import build_tracer_from_env
tracer = build_tracer_from_env(service_name="competitive-analysis-agent")
agent = Collector(llm=..., tools=..., tracer=tracer)
agent.invoke(inp, trace_id="...", span_id="...")
```

未配置 `OTEL_EXPORTER_OTLP_ENDPOINT` 时自动降级 `NullTracer`，单测 / 离线演示零配置可用。

## 起 Jaeger 验收

```bash
docker compose up -d jaeger

export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=competitive-analysis-agent

pytest -m e2e backend/agents
open http://localhost:16686         # service: competitive-analysis-agent
```

UI 应展示：

```
agent.collector
 ├── llm.chat (model=gpt-4o-mini, tokens=128/45, cost=$0.000046)
 ├── llm.chat (model=gpt-4o-mini, tokens=210/89)
 └── tool.scrape.firecrawl (duration=1.2s)
agent.extractor
 └── llm.chat (model=deepseek-chat, tokens=1450/620)
...
```

详细字段语义见 [docs/OBSERVABILITY.md](../../docs/OBSERVABILITY.md) § 13。

## 跟其他 I 窗口产出的协作

- `backend/agents/_base.py` `_TrackingLLMWrapper`：每次 `self.llm.chat()` 自动调 `span.add_llm_call(...)`
- `backend/llm/pricing.py`：算 `cost_usd`，落 OTel attribute `llm.cost_usd`
- `backend/tools/sanitizer.py`：写 attribute 前对 prompt / response / tool args 脱敏

环境变量遵循 [OTel SDK 标准](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/)。
