"""Extractor 测试夹具。

提供 ScriptedLLM / NullTracer，便于注入 BaseAgent 真实模式跑通而不真打 LLM。
真实数据走 fixtures/mock_data/raw_sources/<product>/*.json。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from backend.schemas import CollectDimension, ExtractorInput, RawSourceDoc

_REPO_ROOT = Path(__file__).resolve().parents[4]
_RAW_SOURCE_DIR = _REPO_ROOT / "fixtures" / "mock_data" / "raw_sources"


# ---------- 输入工厂 ----------


def load_raw_sources(product_name: str, dimensions: list[str] | None = None) -> list[RawSourceDoc]:
    """从 fixtures 读取某产品的 raw sources。`dimensions` 不传 = 全取。"""
    slug = product_name.strip().lower()
    base = _RAW_SOURCE_DIR / slug
    if not base.exists():
        return []
    out: list[RawSourceDoc] = []
    for fp in sorted(base.glob("*.json")):
        if dimensions is not None and fp.stem not in dimensions:
            continue
        data = json.loads(fp.read_text(encoding="utf-8"))
        out.append(RawSourceDoc.model_validate(data))
    return out


def make_extractor_input(
    *,
    product_name: str = "Notion",
    raw_sources: list[RawSourceDoc] | None = None,
    industry_schema_id: str = "collaboration_saas_v1",
    schema_fields: list[str] | None = None,
    qa_feedback: dict | None = None,
) -> ExtractorInput:
    if raw_sources is None:
        raw_sources = load_raw_sources(product_name)
    return ExtractorInput(
        task_id="task-ext-test",
        project_id="proj-ext-test",
        trace_id="trace-ext-test",
        span_id="span-ext-test",
        product_name=product_name,
        industry_schema_id=industry_schema_id,
        raw_sources=raw_sources,
        schema_fields=schema_fields,
        qa_feedback=qa_feedback,
    )


# ---------- LLM 桩 ----------


@dataclass
class LLMReply:
    """ScriptedLLM 的单步回放。parsed 直接放 pydantic 实例或 dict。"""

    parsed: Any = None
    content: str = ""
    tokens_input: int = 50
    tokens_output: int = 50


@dataclass
class ScriptedLLM:
    """按 response_format 类型轮询脚本回复。

    用法：注册 `{prompt_signature: [LLMReply, ...]}`，
    其中 ``prompt_signature`` 取自 user message 的关键词（如 "source_id=" 后的 id）。
    简单情况下也可以注册 ``{type: [LLMReply, ...]}``，按 response_format 类名匹配。
    """

    by_signature: dict[str, list[LLMReply]] = field(default_factory=dict)
    by_type: dict[str, list[LLMReply]] = field(default_factory=dict)
    call_log: list[dict[str, Any]] = field(default_factory=list)

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        response_format: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> LLMReply:
        user = messages[-1]["content"] if messages else ""
        self.call_log.append(
            {
                "system_head": system[:60],
                "user_head": user[:120],
                "rf": response_format.__name__ if response_format else None,
            }
        )
        # 优先按签名匹配
        for sig, replies in self.by_signature.items():
            if sig in user and replies:
                return replies.pop(0)
        # 兜底按 response_format 类型
        if response_format is not None:
            replies = self.by_type.get(response_format.__name__, [])
            if replies:
                return replies.pop(0)
        return LLMReply()

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class NullTracer:
    @contextmanager
    def span(self, **kwargs: Any) -> Iterator[Any]:
        yield self

    def set_output(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


# ---------- pytest fixtures ----------


@pytest.fixture()
def notion_raw_sources() -> list[RawSourceDoc]:
    return load_raw_sources("notion")


@pytest.fixture()
def asana_raw_sources() -> list[RawSourceDoc]:
    return load_raw_sources("asana")


@pytest.fixture()
def homepage_dimension() -> CollectDimension:
    return CollectDimension.HOMEPAGE
