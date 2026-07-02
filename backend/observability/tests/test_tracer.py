"""Observability tracer 单测。

OTLPTracer 真接 Jaeger 是 e2e 测试（需要 docker-compose 起 Jaeger，
然后访问 http://localhost:16686 人工验收）。本文件只测：
- build_tracer_from_env 无 OTLP 配置时降级 NullTracer
- OTEL_TRACES_EXPORTER=none 强制走 NullTracer
- NullSpan / NullTracer 的 no-op 语义
- OTLP 实例化失败时也降级（mock OTel SDK 报错）
"""

from __future__ import annotations

import pytest

from backend.observability import (
    NullSpan,
    NullTracer,
    build_tracer_from_env,
)

# ---------- NullTracer 行为 ----------


def test_null_tracer_span_is_context_manager():
    tracer = NullTracer()
    with tracer.span(
        trace_id="tr",
        span_id="sp",
        parent_span_id=None,
        agent_name="dummy",
        agent_version="1.0.0",
    ) as span:
        assert isinstance(span, NullSpan)
        # 所有钩子都应是 no-op，不抛
        span.set_output({"a": 1})
        span.set_error(RuntimeError("ignore"))
        span.add_llm_call(model="x", tokens_input=1)
        span.add_tool_call(tool_name="t", arguments={})


def test_null_span_methods_return_none():
    span = NullSpan()
    assert span.set_output("x") is None
    assert span.set_error(Exception()) is None
    assert span.add_llm_call() is None
    assert span.add_tool_call() is None


# ---------- Factory 降级 ----------


def test_build_tracer_no_endpoint_falls_back(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    tracer = build_tracer_from_env()
    assert isinstance(tracer, NullTracer)


def test_build_tracer_explicit_none(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    tracer = build_tracer_from_env()
    # OTEL_TRACES_EXPORTER=none 优先级高于 endpoint
    assert isinstance(tracer, NullTracer)


def test_build_tracer_sdk_failure_degrades(monkeypatch):
    """OTLPTracer 初始化抛异常时应吞掉并返回 NullTracer。"""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)

    from backend.observability import tracer as tracer_mod

    real_otlp = tracer_mod.OTLPTracer

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("simulated OTel init failure")

    monkeypatch.setattr(tracer_mod, "OTLPTracer", _Boom)
    try:
        result = build_tracer_from_env()
        assert isinstance(result, NullTracer)
    finally:
        monkeypatch.setattr(tracer_mod, "OTLPTracer", real_otlp)


# ---------- OTLPTracer 实例化 ----------


@pytest.mark.otlp
def test_otlp_tracer_can_instantiate():
    """需要 opentelemetry-sdk 实际装上。无 endpoint 时仍能 instantiate
    （OTLPSpanExporter 默认 endpoint = http://localhost:4318/v1/traces）。"""
    pytest.importorskip("opentelemetry.sdk")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")

    from backend.observability import OTLPTracer

    tracer = OTLPTracer(service_name="cap-test")
    try:
        with tracer.span(
            trace_id="tr",
            span_id="sp",
            parent_span_id=None,
            agent_name="dummy",
            agent_version="1.0.0",
        ) as span:
            span.add_llm_call(
                model="gpt-4o-mini",
                system_prompt="hello",
                messages=[{"role": "user", "content": "hi"}],
                response="hello back",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
                finish_reason="stop",
                duration_ms=120,
            )
            span.set_output({"status": "success"})
    finally:
        tracer.shutdown()


# ---------- PII 脱敏 ----------


def test_otlp_span_pii_sanitized_in_attributes():
    """``OTLPSpan.add_llm_call`` 写 attribute 前应过 sanitize（依赖 backend.tools.sanitize）。

    用 RecordingSpan mock 验证写入的 attribute 已脱敏。
    """
    pytest.importorskip("opentelemetry.sdk")
    from backend.observability.tracer import OTLPSpan

    class _RecOtelSpan:
        def __init__(self):
            self.attrs = {}

        def set_attribute(self, k, v):
            self.attrs[k] = v

    class _RecOtelTracer:
        def start_as_current_span(self, name, **kw):
            from contextlib import contextmanager

            rec = _RecOtelSpan()
            self.last = rec

            @contextmanager
            def _cm():
                yield rec

            return _cm()

    rec_tracer = _RecOtelTracer()
    rec_span = _RecOtelSpan()
    span = OTLPSpan(rec_span, rec_tracer)

    span.add_llm_call(
        model="gpt-4o-mini",
        system_prompt="contact me at alice@example.com",
        messages=[{"role": "user", "content": "my phone is 13812345678"}],
        response="ok",
        tokens_input=1,
        tokens_output=1,
    )

    sys_attr = rec_tracer.last.attrs.get("llm.system_prompt", "")
    msg_attr = rec_tracer.last.attrs.get("llm.messages", "")
    assert "alice@example.com" not in sys_attr
    assert "13812345678" not in msg_attr
    assert "[REDACTED]" in sys_attr
