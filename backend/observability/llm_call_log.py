"""LLM 调用记录的进程内环形缓冲 —— 让前端 Trace tab 能拿到每次调用流水。

设计：

- v1 用进程内 ``deque(maxlen=10000)``，重启清空，单进程演示足够
- 真上线时换接 ``backend.storage`` 加 ``llm_calls`` 表 + 持久化（schema 已经
  设计完，见 ``TraceRecord`` / ``LLMCallRecord``）
- ``trace_id`` / ``node_id`` / ``agent_name`` 通过 ``ContextVar`` 关联，由
  ``BaseAgent.invoke`` 入口 set / 出口 reset，``_log_llm_call`` 自动从
  contextvar 读，无需改 LLM 接口签名

读：``list_calls(trace_id=..., node_id=..., agent_name=..., limit=100)``。
"""

from __future__ import annotations

import contextvars
import time
from collections import deque
from dataclasses import asdict, dataclass

_TRACE_CONTEXT: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "llm_trace_context", default=None
)

# 单条记录的 schema
@dataclass(slots=True)
class LLMCallRecord:
    timestamp: float                 # epoch seconds
    trace_id: str | None
    span_id: str | None
    node_id: str | None
    agent_name: str | None
    model: str
    phase: str                       # tool_call / json_mode / freeform / retry
    tokens_input: int
    tokens_output: int
    duration_s: float
    finish_reason: str | None
    cost_usd: float
    prompt_preview: str = ""
    response_preview: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_BUFFER: deque[LLMCallRecord] = deque(maxlen=10000)


# ----- ContextVar API -----


def set_trace_context(
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    node_id: str | None = None,
    agent_name: str | None = None,
):
    """进入 Agent.invoke 时调用；返回 token 供 reset。"""
    return _TRACE_CONTEXT.set(
        {
            "trace_id": trace_id,
            "span_id": span_id,
            "node_id": node_id,
            "agent_name": agent_name,
        }
    )


def reset_trace_context(token) -> None:
    _TRACE_CONTEXT.reset(token)


def current_trace_context() -> dict:
    """取当前 contextvar，缺省返回空 dict（不抛错）。"""
    return _TRACE_CONTEXT.get() or {}


# ----- 写 -----


def push_call(
    *,
    model: str,
    phase: str,
    tokens_input: int,
    tokens_output: int,
    duration_s: float,
    finish_reason: str | None,
    cost_usd: float = 0.0,
    prompt_preview: str = "",
    response_preview: str = "",
) -> None:
    ctx = current_trace_context()
    _BUFFER.append(
        LLMCallRecord(
            timestamp=time.time(),
            trace_id=ctx.get("trace_id"),
            span_id=ctx.get("span_id"),
            node_id=ctx.get("node_id"),
            agent_name=ctx.get("agent_name"),
            model=model,
            phase=phase,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            duration_s=duration_s,
            finish_reason=finish_reason,
            cost_usd=cost_usd,
            prompt_preview=prompt_preview,
            response_preview=response_preview,
        )
    )


# ----- 读 -----


def list_calls(
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    node_id: str | None = None,
    agent_name: str | None = None,
    since_ts: float | None = None,
    limit: int = 200,
) -> list[LLMCallRecord]:
    """倒序返回（最新优先）。filter 都是精确匹配。"""
    out: list[LLMCallRecord] = []
    for rec in reversed(_BUFFER):
        if trace_id is not None and rec.trace_id != trace_id:
            continue
        if span_id is not None and rec.span_id != span_id:
            continue
        if node_id is not None and rec.node_id != node_id:
            continue
        if agent_name is not None and rec.agent_name != agent_name:
            continue
        if since_ts is not None and rec.timestamp < since_ts:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def clear_calls() -> None:
    """测试用：清空 buffer。"""
    _BUFFER.clear()


__all__ = [
    "LLMCallRecord",
    "clear_calls",
    "current_trace_context",
    "list_calls",
    "push_call",
    "reset_trace_context",
    "set_trace_context",
]
