"""端到端真实采集测试。

跑法（要求联网；DEEPSEEK_API_KEY 可选，缺失则跳过 LLM 路径）::

    pytest backend/agents/collector/tests/test_e2e_real.py -m e2e -v -s

默认 `pytest`（pyproject 里 ``addopts = '-m "not e2e"'``）会**反选**本文件，
避免单元测试受外网影响；必须显式带 ``-m e2e`` 才会执行。
"""

from __future__ import annotations

import pytest
from dotenv import load_dotenv

from backend.agents.collector import (
    Collector,
    OpenAICompatibleLLM,
    build_default_registry,
)
from backend.agents.collector.tests.conftest import NullTracer, make_collector_input
from backend.schemas import AgentStatus, CollectDimension

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True, scope="module")
def _load_env() -> None:
    """e2e 被显式选中执行时才读 .env 补 key。

    不能放模块级：即便 `-m "not e2e"` 反选，收集阶段仍会 import 本模块，
    模块级 load_dotenv 会把开发者 .env 的 POSTGRES_DSN / REDIS_URL 泄漏进
    进程环境，破坏 storage 测试「无环境变量自动 skip」的约定。
    override=False：不覆盖 shell 已导出的变量。
    """
    load_dotenv(".env", override=False)


def _network_or_skip() -> None:
    """简单连通性检查。无外网时跳过。"""
    try:
        import httpx

        httpx.get("https://duckduckgo.com/", timeout=5.0)
    except Exception as e:
        pytest.skip(f"network unreachable: {e}")


def test_real_collect_notion_homepage_pricing() -> None:
    """跑一次 Notion 的 HOMEPAGE + PRICING 真采。

    依赖：DuckDuckGo 公开搜索 + HttpxScraper（无 key）+ DeepSeek（可选）。
    断言：至少拿到 1 个真实 RawSourceDoc，且 fetch_method 来自真实链路（不是 mock）。
    """
    _network_or_skip()

    llm = OpenAICompatibleLLM.from_env()  # 有 key 就用 DeepSeek，没有走启发式
    registry = build_default_registry()

    agent = Collector(
        llm=llm,
        tools=registry,
        tracer=NullTracer(),
        mock=False,
    )
    inp = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.HOMEPAGE, CollectDimension.PRICING],
        max_pages_per_dimension=2,
        fallback_to_mock=False,
    )

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    real_methods = {"firecrawl", "playwright", "manual"}
    real_sources = [s for s in out.raw_sources if s.fetch_method in real_methods]

    # 打印一些观察值，便于在 `-s` 模式下肉眼检查
    print(
        "\n[e2e] llm_enabled =",
        llm is not None,
        "model =",
        getattr(llm, "model", None),
    )
    print(
        "[e2e] status =",
        out.status,
        "confidence =",
        round(out.confidence, 3),
        "duration_ms =",
        out.duration_ms,
    )
    print("[e2e] coverage =", {d.value: n for d, n in out.coverage_by_dimension.items()})
    for s in out.raw_sources:
        print(
            f"  - [{s.dimension.value}] {s.fetch_method} {s.http_status} {s.source_url} "
            f"title={(s.title or '')[:50]!r} text_len={len(s.raw_text)}"
        )
    for e in out.errors:
        print(f"  ! {e.severity} {e.code}: {e.message}")
    print("[e2e] self_critique =", out.self_critique)

    assert out.status is not AgentStatus.FAILED, f"unexpected FAILED, errors={out.errors}"
    assert real_sources, (
        "expected at least 1 real-fetched RawSourceDoc, "
        f"got {len(out.raw_sources)} sources with methods="
        f"{[s.fetch_method for s in out.raw_sources]}"
    )
    # 每个真实源至少有非空正文（SPA / JS 渲染页 httpx+readability 抠的可能较短，
    # 这是 HttpxScraper 已知限制，由 self_critique "正文过短" 警告自治理；
    # 真实抓取不阻塞，只要正文非空就视作成功落地。）
    for s in real_sources:
        assert s.raw_text and len(s.raw_text) >= 20, f"raw_text empty/too short: {s.source_url}"
    # final_url 级别应该去重过：HOMEPAGE 维度不应该有同一最终 URL 的重复条目
    seen_final: set[str] = set()
    for s in real_sources:
        key = str(s.source_url).rstrip("/")
        assert key not in seen_final, f"duplicate final_url: {key}"
        seen_final.add(key)


def test_real_collect_notion_user_reviews_via_llm_websearch() -> None:
    """REVIEWS 维度走 LLM 联网搜索路径（豆包 Seed EP 内置 web search）。

    断言：至少 1 条 fetch_method='search' 的 RawSourceDoc，
    且其 raw_text 包含可被 Extractor 抽出的 overall_rating（数字）+ 来源平台名。
    没 LLM key 时跳过。
    """
    _network_or_skip()

    llm = OpenAICompatibleLLM.from_env()
    if llm is None:
        pytest.skip("no LLM key configured; this test requires DOUBAO/DEEPSEEK/OPENAI key")

    registry = build_default_registry()
    agent = Collector(
        llm=llm,
        tools=registry,
        tracer=NullTracer(),
        mock=False,
    )
    inp = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.REVIEWS],
        max_pages_per_dimension=3,
        fallback_to_mock=False,
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    review_docs = [s for s in out.raw_sources if s.dimension is CollectDimension.REVIEWS]
    print(
        f"\n[reviews-e2e] llm model = {llm.model}  status = {out.status}  "
        f"confidence = {out.confidence:.2f}  duration_ms = {out.duration_ms}"
    )
    print(f"[reviews-e2e] got {len(review_docs)} review docs:")
    for s in review_docs:
        print(
            f"  - [{s.fetch_method}] {s.source_url}\n"
            f"    title={(s.title or '')[:60]!r}  text_len={len(s.raw_text)}\n"
            f"    raw_text preview: {s.raw_text[:200]!r}"
        )
    for e in out.errors:
        print(f"  ! {e.severity} {e.code}: {e.message}")

    assert review_docs, (
        f"expected >=1 REVIEWS RawSourceDoc, got 0. errors={out.errors}"
    )
    # 至少一条是 LLM 联网搜索产出（fetch_method='search'）
    llm_origin = [s for s in review_docs if s.fetch_method == "search"]
    assert llm_origin, (
        f"expected >=1 source with fetch_method='search' (LLM web search), "
        f"got methods={[s.fetch_method for s in review_docs]}"
    )
    # Extractor 抽 overall_rating 的最小依赖：raw_text 里必须含一个 0-5 数字 + 平台名
    import re
    rating_pattern = re.compile(r"\b[0-5]([.,]\d)?\b")
    platform_pattern = re.compile(
        r"\b(G2|Capterra|TrustRadius|Software Advice|Gartner)\b", re.I
    )
    for s in llm_origin:
        assert rating_pattern.search(s.raw_text), (
            f"raw_text missing rating number: {s.raw_text[:200]!r}"
        )
        assert platform_pattern.search(s.raw_text), (
            f"raw_text missing platform name: {s.raw_text[:200]!r}"
        )
