"""Collector 测试夹具。

提供 FakeSearch / FakeScrape / FakeRobots / FakeLimiter，便于注入 ToolRegistry 模拟各种链路。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.agents.collector.tools import (
    ScrapeResult,
    SearchHit,
    SimpleToolRegistry,
)
from backend.schemas import CollectDimension, CollectorInput

# ---------- 输入工厂 ----------


def make_collector_input(
    *,
    product_name: str = "Notion",
    dimensions: list[CollectDimension] | None = None,
    fallback_to_mock: bool = True,
    respect_robots_txt: bool = True,
    allow_paid_content: bool = False,
    max_pages_per_dimension: int = 3,
) -> CollectorInput:
    return CollectorInput(
        task_id="task-test",
        project_id="proj-test",
        trace_id="trace-test",
        span_id="span-test",
        product_name=product_name,
        official_url=f"https://www.{product_name.lower()}.so/" if product_name == "Notion" else None,
        industry="collaboration_saas",
        dimensions=dimensions
        or [
            CollectDimension.HOMEPAGE,
            CollectDimension.FEATURES,
            CollectDimension.PRICING,
            CollectDimension.HELP_DOCS,
        ],
        constraints={
            "max_pages_per_dimension": max_pages_per_dimension,
            "timeout_seconds": 30,
            "respect_robots_txt": respect_robots_txt,
            "allow_paid_content": allow_paid_content,
            "fallback_to_mock": fallback_to_mock,
        },  # type: ignore[arg-type]
    )


# ---------- Fakes ----------


@dataclass
class FakeSearch:
    name: str = "search.tavily"
    enabled: bool = True
    fixed: dict[str, list[SearchHit]] = field(default_factory=dict)
    call_log: list[str] = field(default_factory=list)

    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]:
        self.call_log.append(query)
        # 简单匹配：取 query 中的"维度关键词"找 fixed map
        for key, hits in self.fixed.items():
            if key in query.lower():
                return hits[:max_results]
        return self.fixed.get("*", [])[:max_results]


@dataclass
class FakeScrape:
    name: str = "scrape.firecrawl"
    enabled: bool = True
    url_to_result: dict[str, ScrapeResult] = field(default_factory=dict)
    default: ScrapeResult | None = None
    raise_on: set[str] = field(default_factory=set)
    call_log: list[str] = field(default_factory=list)

    def scrape(self, url: str) -> ScrapeResult:
        self.call_log.append(url)
        if url in self.raise_on:
            raise RuntimeError("simulated scrape exception")
        if url in self.url_to_result:
            return self.url_to_result[url]
        if self.default is not None:
            return self.default.model_copy(update={"url": url, "final_url": url})
        return ScrapeResult(
            url=url, final_url=url, http_status=None, fetched_with=self.name, error="not_configured"
        )


@dataclass
class FakeRobots:
    decisions: dict[str, bool] = field(default_factory=dict)
    default_allow: bool = True

    def is_allowed(self, url: str) -> bool:
        for pat, allow in self.decisions.items():
            if pat in url:
                return allow
        return self.default_allow


@dataclass
class FakeLimiter:
    acquired: list[str] = field(default_factory=list)
    raise_on: bool = False
    on_acquire: Callable[[str], None] | None = None

    def acquire(self, host: str) -> None:
        if self.raise_on:
            raise RuntimeError("simulated limiter exception")
        self.acquired.append(host)
        if self.on_acquire is not None:
            self.on_acquire(host)


@dataclass
class NullLLM:
    """空 LLM。Collector 真实模式下 LLM 是可选的（启发式 fallback），
    但 BaseAgent 强制非 mock 时 llm != None，所以测试需要一个桩。"""

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("NullLLM.chat called — Collector should fall back to heuristics")

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class StubResponse:
    """模拟 LLMResponse：携带 Collector._record_llm_usage 关心的字段。"""

    parsed: Any = None
    content: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    model: str = ""


@dataclass
class FakeLLM:
    """可配置 LLM。按 response_format 类型映射到预设响应；同时把每次调用的
    tokens / cost 设成可观察值，方便测试 Collector 的 token 累加逻辑。"""

    by_response_format: dict[type, Any] = field(default_factory=dict)
    call_log: list[dict[str, Any]] = field(default_factory=list)
    raise_on_call: bool = False
    tokens_in_per_call: int = 0
    tokens_out_per_call: int = 0
    cost_usd_per_call: float = 0.0
    model_name: str = "fake-model"

    def chat(
        self,
        *,
        system: str,
        messages: list[dict],
        response_format: type | None = None,
        **kwargs: Any,
    ) -> Any:
        self.call_log.append(
            {"system": system, "messages": messages, "response_format": response_format}
        )
        if self.raise_on_call:
            raise RuntimeError("simulated LLM failure")
        canned = self.by_response_format.get(response_format) if response_format else None
        return StubResponse(
            parsed=canned,
            tokens_input=self.tokens_in_per_call,
            tokens_output=self.tokens_out_per_call,
            cost_usd=self.cost_usd_per_call,
            model=self.model_name,
        )

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]


@dataclass
class NullTracer:
    """空 Tracer。返回上下文管理器但不做任何持久化。"""

    @contextmanager
    def span(self, **kwargs: Any) -> Iterator[Any]:
        yield self

    def set_output(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


# ---------- pytest fixtures ----------


@pytest.fixture()
def make_registry() -> Callable[..., SimpleToolRegistry]:
    """工厂：按需注入 fake 工具构造 ToolRegistry。"""

    def _factory(
        *,
        search: FakeSearch | None = None,
        firecrawl: FakeScrape | None = None,
        playwright: FakeScrape | None = None,
        robots: FakeRobots | None = None,
        limiter: FakeLimiter | None = None,
        extra: dict[str, object] | None = None,
    ) -> SimpleToolRegistry:
        reg = SimpleToolRegistry()
        reg.register("search.tavily", search or FakeSearch(enabled=False))
        reg.register(
            "scrape.firecrawl",
            firecrawl or FakeScrape(name="scrape.firecrawl", enabled=False),
        )
        reg.register(
            "scrape.playwright",
            playwright or FakeScrape(name="scrape.playwright", enabled=False),
        )
        reg.register("robots_checker", robots or FakeRobots())
        reg.register("domain_rate_limiter", limiter or FakeLimiter())
        for k, v in (extra or {}).items():
            reg.register(k, v)
        return reg

    return _factory
