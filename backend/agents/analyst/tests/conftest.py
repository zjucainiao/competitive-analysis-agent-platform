"""Analyst 测试夹具。

NullLLM / NullTracer：非 mock 模式下 BaseAgent 强制 llm / tracer 非空，
提供最小桩。FakeLLM：用于测试 LLM 路径返回的幻觉 claim 是否被正确过滤。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from backend.schemas import DimensionAnalysis


@dataclass
class NullLLM:
    """空 LLM 桩。chat 报错 → 触发 Analyst 启发式 fallback。"""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("NullLLM.chat called — Analyst should fall back to heuristics")

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class FakeLLM:
    """按维度返回预置 DimensionAnalysis。命中 user prompt 中的 dimension 字符串即返回。

    没命中时抛错，触发 Analyst 走启发式 fallback。
    """

    by_dimension: dict[str, DimensionAnalysis] = field(default_factory=dict)
    call_log: list[str] = field(default_factory=list)

    def chat(self, *, system: str, messages: list[dict], **kwargs: Any) -> Any:
        user_content = next((m["content"] for m in messages if m["role"] == "user"), "")
        for dim_key, analysis in self.by_dimension.items():
            if dim_key in user_content:
                self.call_log.append(dim_key)
                return analysis
        raise NotImplementedError(f"FakeLLM has no response for: {messages}")

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class NullTracer:
    """空 tracer。返回 self 作为 span，所有方法 no-op。"""

    @contextmanager
    def span(self, **kwargs: Any) -> Iterator[Any]:
        yield self

    def set_output(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_error(self, *args: Any, **kwargs: Any) -> None:
        return None
