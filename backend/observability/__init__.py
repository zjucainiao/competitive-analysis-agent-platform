"""Observability 层 —— OTLP / Jaeger 接入入口。

主要导出：
- ``OTLPTracer`` / ``NullTracer``：实现 ``backend.agents._base.TracerProtocol``
- ``OTLPSpan`` / ``NullSpan``：实现 BaseAgent 期望的 span 接口
  （``set_output / set_error / add_llm_call / add_tool_call``）
- ``build_tracer_from_env()``：按环境变量装配，缺 ``OTEL_EXPORTER_OTLP_ENDPOINT``
  自动降级到 ``NullTracer``，单测 / 离线演示零配置可用

接入方式（O 窗口 / API 层）::

    from backend.observability import build_tracer_from_env
    tracer = build_tracer_from_env(service_name="competitive-analysis-agent")
    agent = Collector(llm=..., tools=..., tracer=tracer)

详细字段语义见 docs/OBSERVABILITY.md。
"""

from __future__ import annotations

from .tracer import (
    NullSpan,
    NullTracer,
    OTLPSpan,
    OTLPTracer,
    build_tracer_from_env,
)

__all__ = [
    "NullSpan",
    "NullTracer",
    "OTLPSpan",
    "OTLPTracer",
    "build_tracer_from_env",
]
