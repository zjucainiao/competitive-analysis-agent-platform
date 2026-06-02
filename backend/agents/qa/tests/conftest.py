"""QA 测试夹具。

NullLLM / NullTracer：BaseAgent 非 mock 模式强制 llm / tracer 非空，
提供最小桩；NullLLM.chat 抛错触发 checker 走规则路径。
FakeLLM：返回预置 entailment / contradiction / expression 响应，用于
检验 LLM 路径下 fact / logic / expression checker 的行为。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NullLLM:
    """空 LLM 桩。chat 报错 → checker 走规则降级。"""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "NullLLM.chat called — QA checker should fall back to rules"
        )

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class FakeLLM:
    """按 system + 响应模型匹配返回预置对象。

    使用方式：
        llm = FakeLLM(responses={"entailment": entailment_resp_model})
    当 prompt 的 system 含某个关键字时返回对应响应；否则抛错走规则路径。
    """

    responses: dict[str, Any] = field(default_factory=dict)
    call_log: list[str] = field(default_factory=list)

    def chat(self, *, system: str, messages: list[dict], **kwargs: Any) -> Any:
        for key, resp in self.responses.items():
            if key in system or key in messages[0].get("content", ""):
                self.call_log.append(key)
                return resp
        raise NotImplementedError(
            f"FakeLLM has no response matching system/user (known keys: "
            f"{list(self.responses.keys())})"
        )

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
