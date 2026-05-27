"""Collector Agent 单元测试。

覆盖：
1. mock 模式正常 case
2. mock 模式部分覆盖（缺失 CHANGELOG）→ status=PARTIAL + self_critique 提示
3. 真实模式：robots 阻拦 + 全链失败 + fallback_to_mock 兜底
4. 真实模式：fallback_to_mock=False + 全链失败 → status=FAILED + confidence=0
5. CollectorInput / CollectorOutput Schema 严格性（extra=forbid）
6. self_critique 强制：低 confidence 必须非空
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.collector import Collector
from backend.agents.collector.tests.conftest import (
    FakeLimiter,
    FakeRobots,
    FakeScrape,
    FakeSearch,
    NullLLM,
    NullTracer,
    make_collector_input,
)
from backend.agents.collector.tools import ScrapeResult, SearchHit
from backend.schemas import (
    AgentStatus,
    CollectDimension,
    CollectorInput,
    CollectorOutput,
)

# ---------- 1. Mock 正常 case ----------


def test_mock_full_coverage_returns_success() -> None:
    agent = Collector(mock=True)
    inp = make_collector_input(
        dimensions=[
            CollectDimension.HOMEPAGE,
            CollectDimension.FEATURES,
            CollectDimension.PRICING,
            CollectDimension.HELP_DOCS,
        ]
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert isinstance(out, CollectorOutput)
    assert out.status is AgentStatus.SUCCESS
    assert len(out.raw_sources) == 4
    assert out.confidence >= 0.8
    assert all(s.product_name == "Notion" for s in out.raw_sources)
    assert all(s.fetch_method == "mock" for s in out.raw_sources)
    assert sum(out.coverage_by_dimension.values()) == 4
    # BaseAgent 注入了基础元数据
    assert out.agent_name == "collector"
    assert out.agent_version == "1.0.0"
    assert out.trace_id == "trace-test"


# ---------- 2. Mock 部分覆盖 ----------


def test_mock_missing_dimension_marks_partial_with_critique() -> None:
    agent = Collector(mock=True)
    inp = make_collector_input(
        dimensions=[
            CollectDimension.HOMEPAGE,
            CollectDimension.CHANGELOG,  # fixtures 不覆盖
        ]
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.PARTIAL
    assert out.coverage_by_dimension[CollectDimension.CHANGELOG] == 0
    assert out.coverage_by_dimension[CollectDimension.HOMEPAGE] == 1
    assert "changelog" in out.self_critique.lower()
    assert any(e.code == "NO_RELEVANT_RESULTS" for e in out.errors)


# ---------- 3. 真实模式：robots 阻拦 + fallback_to_mock ----------


def test_real_mode_robots_blocked_then_fallback_to_mock(make_registry) -> None:
    search = FakeSearch(
        fixed={
            "official": [
                SearchHit(url="https://www.notion.so/", title="Notion home", provider="t"),
            ],
            "pricing": [
                SearchHit(url="https://www.notion.so/pricing", title="Notion pricing", provider="t"),
            ],
        }
    )
    # firecrawl / playwright 都没启用，加上 robots 阻拦 → 真实链拿不到东西
    robots = FakeRobots(decisions={"notion.so": False}, default_allow=True)
    reg = make_registry(search=search, robots=robots, limiter=FakeLimiter())

    agent = Collector(
        llm=NullLLM(),
        tools=reg,
        tracer=NullTracer(),
        mock=False,
    )

    inp = make_collector_input(
        dimensions=[CollectDimension.HOMEPAGE, CollectDimension.PRICING],
        fallback_to_mock=True,
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert any(e.code == "ROBOTS_BLOCKED" for e in out.errors)
    assert any(e.code == "FELL_BACK_TO_MOCK" for e in out.errors)
    assert len(out.raw_sources) >= 1
    assert any(s.fetch_method == "mock" for s in out.raw_sources)


# ---------- 4. 真实模式：全链失败 + 不兜底 → FAILED ----------


def test_real_mode_no_fallback_yields_failed_status(make_registry) -> None:
    search = FakeSearch(fixed={"*": []})  # 搜索零结果
    reg = make_registry(search=search)

    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(fallback_to_mock=False)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.FAILED
    assert out.confidence == 0.0
    assert len(out.raw_sources) == 0
    assert all(e.code == "NO_RELEVANT_RESULTS" for e in out.errors)
    # confidence < 0.6 但 self_critique 必须非空
    assert out.self_critique.strip() != ""


# ---------- 5. 抓取链 fallback：firecrawl 失败 → playwright 成功 ----------


def test_scrape_chain_fallback_firecrawl_to_playwright(make_registry) -> None:
    url = "https://clickup.com/pricing"
    search = FakeSearch(
        fixed={
            "pricing": [SearchHit(url=url, title="ClickUp pricing", provider="t")],
        }
    )
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        default=ScrapeResult(
            url=url,
            final_url=url,
            http_status=None,
            fetched_with="scrape.firecrawl",
            error="simulated_firecrawl_down",
        ),
    )
    playwright_text = (
        "ClickUp pricing: Free Forever, Unlimited ($7/user/mo annual), "
        "Business ($12/user/mo annual), Enterprise (custom). Full plan comparison "
        "covers storage, integrations, automations, dashboards, custom roles, SSO, "
        "audit log, and dedicated success manager for higher tiers."
    )
    playwright = FakeScrape(
        name="scrape.playwright",
        enabled=True,
        default=ScrapeResult(
            url=url,
            final_url=url,
            http_status=200,
            text=playwright_text,
            title="ClickUp pricing",
            fetched_with="scrape.playwright",
        ),
    )
    reg = make_registry(search=search, firecrawl=firecrawl, playwright=playwright)

    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="ClickUp",
        dimensions=[CollectDimension.PRICING],
        fallback_to_mock=False,
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.SUCCESS
    assert len(out.raw_sources) == 1
    src = out.raw_sources[0]
    assert src.fetch_method == "playwright"
    assert src.dimension is CollectDimension.PRICING
    assert "ClickUp pricing" in src.raw_text
    # firecrawl 与 playwright 都被尝试过
    assert firecrawl.call_log == [url]
    assert playwright.call_log == [url]


# ---------- 6. Schema 严格性：extra=forbid ----------


def test_collector_input_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CollectorInput(  # type: ignore[call-arg]
            task_id="t",
            project_id="p",
            trace_id="tr",
            span_id="sp",
            product_name="X",
            industry="i",
            dimensions=[CollectDimension.HOMEPAGE],
            unknown_field="oops",
        )


# ---------- 7. Paywall 命中 → 跳过 + 记录 ----------


def test_paywall_blocked_when_not_allowed(make_registry) -> None:
    url = "https://example.com/article"
    search = FakeSearch(fixed={"blog": [SearchHit(url=url, title="Behind Paywall", provider="t")]})
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        default=ScrapeResult(
            url=url,
            final_url=url,
            http_status=200,
            text="Some teaser content from a long article that exceeds the 200 char threshold so we can isolate the paywall detection effect on the collector's decision logic clearly here.",
            title="Behind Paywall",
            fetched_with="scrape.firecrawl",
            detected_paywall=True,
        ),
    )
    reg = make_registry(search=search, firecrawl=firecrawl)
    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="ClickUp",
        dimensions=[CollectDimension.BLOG],
        fallback_to_mock=False,
        allow_paid_content=False,
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert any(e.code == "PAYWALL_DETECTED" for e in out.errors)
    assert len(out.raw_sources) == 0


# ---------- 8. RateLimiter 被调用 ----------


def test_domain_rate_limiter_invoked(make_registry) -> None:
    url = "https://asana.com/"
    search = FakeSearch(
        fixed={"official": [SearchHit(url=url, title="Asana home", provider="t")]}
    )
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        default=ScrapeResult(
            url=url,
            final_url=url,
            http_status=200,
            text="Asana home page content " * 30,
            title="Asana",
            fetched_with="scrape.firecrawl",
        ),
    )
    limiter = FakeLimiter()
    reg = make_registry(search=search, firecrawl=firecrawl, limiter=limiter)
    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Asana",
        dimensions=[CollectDimension.HOMEPAGE],
        fallback_to_mock=False,
    )
    agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert "asana.com" in limiter.acquired
