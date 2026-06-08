"""采集实时进度的跨线程事件钩子。

Collector 在抓取过程中**逐条**产出来源时调 ``emit_collect_progress``；编排器在原生
流式执行期间用 ``set_collect_progress_emitter`` 注入一个「把进度推到事件总线」的回调。

为什么用 ContextVar：与 trace / user_prompt_override 同一套机制——``run_agent_node``
经 ``asyncio.to_thread`` 调 agent（会把当前 contextvars 复制进工作线程），collector
再用 ``contextvars.copy_context()`` 把它带进各维度并行 worker。于是编排器在主 loop
设的回调，能一路下沉到 collector 的抓取循环里被调用。

约束：emitter 由 worker 线程调用，**必须线程安全**（编排器侧用
``run_coroutine_threadsafe`` 调回主 loop）。未注入 emitter 时 ``emit_collect_progress``
是 no-op，且任何异常都被吞掉——实时进度是纯观测，绝不影响采集主流程。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Callable

_emitter: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "collect_progress_emitter", default=None
)


def set_collect_progress_emitter(
    fn: Callable[[dict[str, Any]], None] | None,
) -> Token:
    """注入进度回调，返回 token 供 ``reset_collect_progress_emitter`` 复位。"""
    return _emitter.set(fn)


def reset_collect_progress_emitter(token: Token) -> None:
    try:
        _emitter.reset(token)
    except Exception:  # noqa: BLE001
        pass


def emit_collect_progress(payload: dict[str, Any]) -> None:
    """逐条来源进度（无注入或出错时静默 no-op，不影响采集）。"""
    fn = _emitter.get()
    if fn is None:
        return
    try:
        fn(payload)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "emit_collect_progress",
    "reset_collect_progress_emitter",
    "set_collect_progress_emitter",
]
