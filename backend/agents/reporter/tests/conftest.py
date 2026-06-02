"""Reporter 测试夹具。

NullLLM / NullTracer：非 mock 模式下 BaseAgent 强制 llm / tracer 非空，
提供最小桩。FakeLLM：用于测试 LLM 路径返回的段落是否被引用强制门禁
正确放行 / 拒绝。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from backend.schemas import ReportSection


@dataclass
class NullLLM:
    """空 LLM 桩。chat 报错 → 触发 Reporter 启发式 fallback。"""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "NullLLM.chat called — Reporter should fall back to heuristics"
        )

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class FakeLLM:
    """按 response_format 路由：

    - ``ReportSection`` → 按 ``Section: <section_id>`` 匹配 ``by_section``
    - ``EntailmentVerdict`` → 按段落子串匹配 ``entailment_by_phrase``；
      未匹配时返回 ``entailment_default``（仍为 None 则抛错）
    - ``RepairedParagraph`` → 按段落子串匹配 ``repair_by_phrase``；
      未匹配时返回 ``repair_default``（仍为 None 则抛错）
    """

    by_section: dict[str, Any] = field(default_factory=dict)
    entailment_by_phrase: dict[str, Any] = field(default_factory=dict)
    entailment_default: Any = None
    repair_by_phrase: dict[str, Any] = field(default_factory=dict)
    repair_default: Any = None
    call_log: list[str] = field(default_factory=list)

    def chat(
        self,
        *,
        system: str,
        messages: list[dict],
        response_format: Any = None,
        **kwargs: Any,
    ) -> Any:
        user_content = next(
            (m["content"] for m in messages if m["role"] == "user"), ""
        )
        rf_name = getattr(response_format, "__name__", "")
        if rf_name == "EntailmentVerdict":
            for phrase, verdict in self.entailment_by_phrase.items():
                if phrase in user_content:
                    self.call_log.append(f"entailment:{phrase[:30]}")
                    return verdict
            if self.entailment_default is not None:
                self.call_log.append("entailment:default")
                return self.entailment_default
            raise NotImplementedError(
                f"FakeLLM has no entailment verdict for: {user_content[:120]}..."
            )
        if rf_name == "RepairedParagraph":
            for phrase, repaired in self.repair_by_phrase.items():
                if phrase in user_content:
                    self.call_log.append(f"repair:{phrase[:30]}")
                    return repaired
            if self.repair_default is not None:
                self.call_log.append("repair:default")
                return self.repair_default
            raise NotImplementedError(
                f"FakeLLM has no repair response for: {user_content[:120]}..."
            )
        # 否则按 section_id 匹配
        for section_id, section in self.by_section.items():
            if section_id in user_content:
                self.call_log.append(section_id)
                return section
        raise NotImplementedError(
            f"FakeLLM has no response for content: {user_content[:120]}..."
        )

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class NullTracer:
    """空 tracer。"""

    @contextmanager
    def span(self, **kwargs: Any) -> Iterator[Any]:
        yield self

    def set_output(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_error(self, *args: Any, **kwargs: Any) -> None:
        return None
