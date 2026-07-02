"""OTLP / Jaeger Tracer 实现 —— 替换 orchestrator/tracing.py 的 NullTracer。

实现 ``backend.agents._base.TracerProtocol``：``span(...)`` 返回 context manager，
支持 ``set_output / set_error / add_llm_call / add_tool_call`` 钩子。

落地方式：
- 每次 Agent ``invoke()`` 开一个**根 span**（``agent.<name>``），attribute 记
  ``agent.name / agent.version / trace_id / span_id / node_id``
- ``add_llm_call(...)`` 在当前 span 下开 **子 span**（``llm.chat``），attribute
  记 ``model / tokens_input / tokens_output / cost_usd / finish_reason / duration_ms``
  以及 ``system_prompt`` / ``messages`` / ``response`` 的脱敏摘要
- ``add_tool_call(...)`` 类似，子 span 名 ``tool.<tool_name>``
- ``set_output / set_error`` 设根 span 的状态 + 附 output_snapshot dict

OTLP HTTP exporter 优先（依赖少、易调试，curl Jaeger 4318/v1/traces 就能验）；
配置全走标准 OTel 环境变量（``OTEL_EXPORTER_OTLP_ENDPOINT`` /
``OTEL_SERVICE_NAME`` / ``OTEL_RESOURCE_ATTRIBUTES`` …），没设默认指向
``http://localhost:4318``，docker-compose 起的 Jaeger 即可消费。

PII 脱敏：``add_llm_call`` / ``add_tool_call`` 写 attribute 前过
``backend.tools.sanitize``，遵守 docs/COMPLIANCE.md § 4.1。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from backend.tools import sanitize

_logger = logging.getLogger("backend.observability")


# ---------- Null implementations (默认 / fallback) ----------


class NullSpan:
    """空 span：方法都无副作用。BaseAgent 在没 tracer 时也走这条。"""

    def set_output(self, _out: Any) -> None:
        return None

    def set_error(self, _err: Any) -> None:
        return None

    def add_llm_call(self, *args: Any, **kwargs: Any) -> None:
        return None

    def add_tool_call(self, *args: Any, **kwargs: Any) -> None:
        return None


class NullTracer:
    """``TracerProtocol`` 的空实现，单测 / 离线演示用。"""

    @contextmanager
    def span(
        self,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        agent_name: str,
        agent_version: str,
        node_id: str | None = None,
    ) -> Iterator[NullSpan]:
        yield NullSpan()


# ---------- OTLP implementation ----------


# 写入 OTel span attribute 时单字段的最大长度（防止 prompt/response 把 trace 撑爆）
_MAX_ATTR_LEN = 4000


def _trim(text: str, limit: int = _MAX_ATTR_LEN) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 12] + f"…[+{len(text) - limit + 12}]"


def _safe_json(value: Any, limit: int = _MAX_ATTR_LEN) -> str:
    """value → JSON 字符串，长度截断 + PII 脱敏。"""
    if value is None:
        return ""
    try:
        if hasattr(value, "model_dump"):
            data = value.model_dump(mode="json")
        elif isinstance(value, (dict, list)):
            data = value
        else:
            data = str(value)
        raw = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        raw = str(value)
    return _trim(sanitize(raw), limit)


class OTLPSpan:
    """包一个 OTel span，暴露 BaseAgent 期望的接口。

    保留 ``_otel_span``（OTel SDK 的 Span 对象）+ ``_otel_tracer``（用于开子 span）。
    """

    def __init__(self, otel_span: Any, otel_tracer: Any) -> None:
        self._otel_span = otel_span
        self._otel_tracer = otel_tracer

    def set_output(self, out: Any) -> None:
        try:
            self._otel_span.set_attribute("agent.output_snapshot", _safe_json(out))
            status = getattr(out, "status", None)
            if status is not None:
                self._otel_span.set_attribute("agent.status", str(status))
            confidence = getattr(out, "confidence", None)
            if confidence is not None:
                self._otel_span.set_attribute("agent.confidence", float(confidence))
            tin = int(getattr(out, "tokens_input", 0) or 0)
            tout = int(getattr(out, "tokens_output", 0) or 0)
            cost = float(getattr(out, "cost_usd", 0.0) or 0.0)
            if tin:
                self._otel_span.set_attribute("agent.tokens_input", tin)
            if tout:
                self._otel_span.set_attribute("agent.tokens_output", tout)
            if cost:
                self._otel_span.set_attribute("agent.cost_usd", cost)
        except Exception:
            pass

    def set_error(self, err: BaseException) -> None:
        from opentelemetry.trace import Status, StatusCode  # type: ignore

        try:
            self._otel_span.record_exception(err)
            self._otel_span.set_status(Status(StatusCode.ERROR, str(err)))
        except Exception:
            pass

    def add_llm_call(
        self,
        *,
        model: str = "",
        system_prompt: str = "",
        messages: Any = None,
        response: Any = None,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost_usd: float = 0.0,
        finish_reason: str | None = None,
        duration_ms: int = 0,
        phase: str = "",
    ) -> None:
        """在当前 agent span 下开子 span ``llm.chat``。"""
        try:
            from opentelemetry import trace  # type: ignore

            ctx = trace.set_span_in_context(self._otel_span)
            with self._otel_tracer.start_as_current_span("llm.chat", context=ctx) as child:
                child.set_attribute("llm.model", model or "")
                child.set_attribute("llm.tokens_input", int(tokens_input or 0))
                child.set_attribute("llm.tokens_output", int(tokens_output or 0))
                if cost_usd:
                    child.set_attribute("llm.cost_usd", float(cost_usd))
                if finish_reason:
                    child.set_attribute("llm.finish_reason", finish_reason)
                if duration_ms:
                    child.set_attribute("llm.duration_ms", int(duration_ms))
                if phase:
                    child.set_attribute("llm.phase", phase)
                if system_prompt:
                    child.set_attribute("llm.system_prompt", _trim(sanitize(system_prompt)))
                if messages is not None:
                    child.set_attribute("llm.messages", _safe_json(messages))
                if response is not None:
                    child.set_attribute("llm.response", _safe_json(response))
        except Exception:
            pass

    def add_tool_call(
        self,
        *,
        tool_name: str,
        arguments: Any = None,
        result: Any = None,
        duration_ms: int = 0,
        error: str | None = None,
    ) -> None:
        try:
            from opentelemetry import trace  # type: ignore

            ctx = trace.set_span_in_context(self._otel_span)
            with self._otel_tracer.start_as_current_span(f"tool.{tool_name}", context=ctx) as child:
                child.set_attribute("tool.name", tool_name)
                if arguments is not None:
                    child.set_attribute("tool.arguments", _safe_json(arguments))
                if result is not None:
                    child.set_attribute("tool.result", _safe_json(result))
                if duration_ms:
                    child.set_attribute("tool.duration_ms", int(duration_ms))
                if error:
                    from opentelemetry.trace import Status, StatusCode  # type: ignore

                    child.set_attribute("tool.error", error)
                    child.set_status(Status(StatusCode.ERROR, error))
        except Exception:
            pass


class OTLPTracer:
    """OTel SDK + OTLP HTTP exporter。

    实例化时 setup TracerProvider；多次实例化会被 OTel 内部 dedupe。
    ``span(...)`` 是同步 context manager，进入时开 root span（``agent.<name>``），
    退出时自动关闭。
    """

    def __init__(
        self,
        *,
        service_name: str = "competitive-analysis-agent",
        endpoint: str | None = None,
        resource_attributes: dict[str, str] | None = None,
    ) -> None:
        # 延迟 import：OTel SDK 仅在真用时加载
        from opentelemetry import trace  # type: ignore
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore

        attrs = {"service.name": service_name}
        if resource_attributes:
            attrs.update(resource_attributes)
        resource = Resource.create(attrs)

        provider = TracerProvider(resource=resource)
        exporter_kwargs: dict[str, Any] = {}
        if endpoint:
            # 用户传完整 OTLP 端点（如 http://localhost:4318/v1/traces）
            exporter_kwargs["endpoint"] = endpoint
        exporter = OTLPSpanExporter(**exporter_kwargs)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        # 注意：set_tracer_provider 是全局副作用；多次 build_tracer_from_env
        # 在同一进程内会被忽略（OTel SDK 内部 dedupe），生产 OK。单测套
        # 通过 fixture 控制只 setup 一次。
        trace.set_tracer_provider(provider)
        self._provider = provider
        self._tracer = trace.get_tracer(service_name)
        _logger.info(
            "OTLPTracer ready: service=%s endpoint=%s",
            service_name,
            endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "default"),
        )

    @contextmanager
    def span(
        self,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        agent_name: str,
        agent_version: str,
        node_id: str | None = None,
    ) -> Iterator[OTLPSpan]:
        started = time.monotonic()
        with self._tracer.start_as_current_span(f"agent.{agent_name}") as otel_span:
            try:
                otel_span.set_attribute("agent.name", agent_name)
                otel_span.set_attribute("agent.version", agent_version)
                # 平台自定义的逻辑 trace/span 标识与 OTel trace_id 不同：
                # 它来自 docs/OBSERVABILITY.md 的项目级 span 模型，作为 attribute 保留
                otel_span.set_attribute("app.trace_id", trace_id)
                otel_span.set_attribute("app.span_id", span_id)
                if parent_span_id:
                    otel_span.set_attribute("app.parent_span_id", parent_span_id)
                if node_id:
                    otel_span.set_attribute("dag.node_id", node_id)
            except Exception:
                pass

            wrapped = OTLPSpan(otel_span, self._tracer)
            try:
                yield wrapped
            except Exception as e:
                wrapped.set_error(e)
                raise
            finally:
                duration_ms = int((time.monotonic() - started) * 1000)
                try:
                    otel_span.set_attribute("agent.duration_ms", duration_ms)
                except Exception:
                    pass

    def shutdown(self) -> None:
        """flush + 关闭 BatchSpanProcessor。优雅退出用。"""
        try:
            self._provider.shutdown()
        except Exception:
            pass


# ---------- Factory ----------


def build_tracer_from_env(
    *,
    service_name: str | None = None,
    endpoint: str | None = None,
):
    """按环境变量装配 tracer，未配 OTLP 时降级到 ``NullTracer``。

    优先级：
    1. 显式参数 ``endpoint``
    2. ``OTEL_EXPORTER_OTLP_ENDPOINT`` 环境变量
    3. ``OTEL_TRACES_EXPORTER`` 显式设为 ``"none"`` → ``NullTracer``
    4. 都没有 → ``NullTracer``

    服务名同优先级：参数 > ``OTEL_SERVICE_NAME`` > 默认 ``competitive-analysis-agent``。
    """
    if os.getenv("OTEL_TRACES_EXPORTER", "").lower() == "none":
        _logger.info("OTEL_TRACES_EXPORTER=none, using NullTracer")
        return NullTracer()

    effective_endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not effective_endpoint:
        _logger.info("no OTLP endpoint configured, using NullTracer")
        return NullTracer()

    effective_name = service_name or os.getenv("OTEL_SERVICE_NAME") or "competitive-analysis-agent"
    try:
        return OTLPTracer(
            service_name=effective_name,
            endpoint=effective_endpoint
            if effective_endpoint.endswith("/v1/traces")
            else None,  # endpoint 不带路径时让 SDK 自己拼
        )
    except Exception as e:
        # OTel SDK 安装异常 / endpoint 不可达不应阻塞 Agent 启动
        _logger.warning("OTLPTracer init failed (%s), falling back to NullTracer", e)
        return NullTracer()


__all__ = [
    "NullSpan",
    "NullTracer",
    "OTLPSpan",
    "OTLPTracer",
    "build_tracer_from_env",
]
