"""Crawl4AI 端到端测试。

跑法（需要联网 + 已装 chromium）::

    python -m playwright install chromium  # 一次性
    pytest backend/agents/collector/tests/test_e2e_crawl4ai.py -v -s

目标：
1. 直接 vs 抓取对比 — 同一 SPA 页（notion.com 首页），httpx 抠 < 100 字，crawl4ai > 300 字
2. 接入 Collector 主链 — `build_default_registry(enable_crawl4ai=True)` 跑 Notion HOMEPAGE，
   拿到 fetch_method='playwright' 的 RawSourceDoc，正文显著更丰富
"""

from __future__ import annotations

import pytest
from dotenv import load_dotenv

from backend.agents.collector import (
    Collector,
    Crawl4AIScraper,
    OpenAICompatibleLLM,
    build_default_registry,
)
from backend.agents.collector.tests.conftest import NullTracer, make_collector_input
from backend.agents.collector.tools import HttpxScraper
from backend.schemas import AgentStatus, CollectDimension

pytestmark = pytest.mark.e2e

load_dotenv(".env")


def _network_or_skip() -> None:
    try:
        import httpx

        httpx.get("https://duckduckgo.com/", timeout=5.0)
    except Exception as e:
        pytest.skip(f"network unreachable: {e}")


def _crawl4ai_or_skip() -> None:
    try:
        import crawl4ai  # noqa: F401
    except ImportError as e:
        pytest.skip(f"crawl4ai not installed: {e}")


def test_crawl4ai_vs_httpx_on_notion_homepage() -> None:
    """同一 SPA URL，crawl4ai 抠的正文应该比 httpx 显著多。"""
    _network_or_skip()
    _crawl4ai_or_skip()
    url = "https://www.notion.com/"

    httpx_result = HttpxScraper().scrape(url)
    crawl_result = Crawl4AIScraper(headless=True).scrape(url)

    print(
        f"\n[crawl4ai-cmp] httpx text_len={len(httpx_result.text)} "
        f"status={httpx_result.http_status} error={httpx_result.error!r}"
    )
    print(
        f"[crawl4ai-cmp] crawl4ai text_len={len(crawl_result.text)} "
        f"status={crawl_result.http_status} error={crawl_result.error!r}"
    )
    print(f"[crawl4ai-cmp] httpx preview: {httpx_result.text[:200]!r}")
    print(f"[crawl4ai-cmp] crawl4ai preview: {crawl_result.text[:200]!r}")

    assert crawl_result.error is None, f"crawl4ai failed: {crawl_result.error}"
    assert crawl_result.text, "crawl4ai returned empty text"
    # SPA 痛点的核心断言：crawl4ai 必须显著更丰富
    assert len(crawl_result.text) > len(httpx_result.text) * 3, (
        f"expected crawl4ai to extract >3x more text than httpx; "
        f"got crawl4ai={len(crawl_result.text)} vs httpx={len(httpx_result.text)}"
    )
    assert len(crawl_result.text) > 300, "crawl4ai text too short for a SPA-heavy homepage"


def test_collector_with_crawl4ai_main_chain_notion_homepage() -> None:
    """启用 Crawl4AI 后，Collector 主链应该把 Notion HOMEPAGE 抓得更丰富。"""
    _network_or_skip()
    _crawl4ai_or_skip()

    llm = OpenAICompatibleLLM.from_env()
    registry = build_default_registry(enable_crawl4ai=True)
    agent = Collector(
        llm=llm,
        tools=registry,
        tracer=NullTracer(),
        mock=False,
    )

    inp = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.HOMEPAGE],
        max_pages_per_dimension=2,
        fallback_to_mock=False,
    )

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    print(
        f"\n[crawl4ai-chain] status={out.status} confidence={out.confidence:.2f} "
        f"duration_ms={out.duration_ms}"
    )
    for s in out.raw_sources:
        print(
            f"  - [{s.dimension.value}] fetch_method={s.fetch_method} "
            f"text_len={len(s.raw_text)} title={(s.title or '')[:50]!r} "
            f"url={s.source_url}"
        )
    for e in out.errors:
        print(f"  ! {e.severity} {e.code}: {e.message}")
    print(f"[crawl4ai-chain] self_critique = {out.self_critique}")

    assert out.status is not AgentStatus.FAILED, f"FAILED with errors={out.errors}"
    playwright_sources = [s for s in out.raw_sources if s.fetch_method == "playwright"]
    assert playwright_sources, (
        "expected at least 1 RawSourceDoc with fetch_method='playwright' "
        f"(crawl4ai), got methods={[s.fetch_method for s in out.raw_sources]}"
    )
    # 主链路里 crawl4ai 抠到的正文必须比之前 httpx-only 跑出的 38 字明显多
    best = max((len(s.raw_text) for s in playwright_sources), default=0)
    assert best > 300, f"playwright source text too short: {best}"
