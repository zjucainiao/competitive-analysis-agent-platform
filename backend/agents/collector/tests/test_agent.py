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
from backend.agents.collector.agent import _ReviewsFinding, _ReviewSource
from backend.agents.collector.tests.conftest import (
    FakeLimiter,
    FakeLLM,
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
    """搜索零结果 + 无 official_url seed + 无 fallback → 彻底拿不到源 → FAILED。"""
    search = FakeSearch(fixed={"*": []})  # 搜索零结果
    reg = make_registry(search=search)

    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="UnknownProduct",  # 不会触发 conftest 内的 Notion seed
        dimensions=[CollectDimension.HOMEPAGE, CollectDimension.PRICING],
        fallback_to_mock=False,
    )
    assert inp.official_url is None  # 确保没有 seed 路径
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


# ---------- 9. REVIEWS 维度：LLM 联网搜索路径 ----------


def test_reviews_via_llm_emits_one_doc_per_source(make_registry) -> None:
    """REVIEWS 维度：LLM 返回带 sources 的 _ReviewsFinding → 每个 source 一条 RawSourceDoc。"""
    finding = _ReviewsFinding(
        overall_rating=4.5,
        review_count=5421,
        positive_themes=["易上手", "AI 功能强", "模板丰富"],
        negative_themes=["移动端弱", "大库卡顿"],
        sample_quotes=[
            "We replaced Confluence + Trello with Notion within a month.",
            "AI summaries save me 2 hours a week.",
        ],
        sources=[
            _ReviewSource(
                name="G2",
                url="https://www.g2.com/products/notion/reviews",
                excerpt="G2 综合 4.7/5 (5,000+ reviews)，用户夸 AI 与协作。",
            ),
            _ReviewSource(
                name="Capterra",
                url="https://www.capterra.com/p/176532/Notion/",
                excerpt="Capterra 4.6/5 (1,800+ reviews)，价格点是常见抱怨。",
            ),
        ],
    )
    fake_llm = FakeLLM(by_response_format={_ReviewsFinding: finding})
    reg = make_registry()
    agent = Collector(llm=fake_llm, tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.REVIEWS],
        fallback_to_mock=False,
    )

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    review_docs = [s for s in out.raw_sources if s.dimension is CollectDimension.REVIEWS]
    assert len(review_docs) == 2
    urls = {str(s.source_url).rstrip("/") for s in review_docs}
    assert "https://www.g2.com/products/notion/reviews" in urls
    # Capterra URL 含数字 ID，pydantic HttpUrl 不会规范化掉
    assert any("capterra.com" in u for u in urls)

    g2 = next(s for s in review_docs if "g2.com" in str(s.source_url))
    # Extractor 最小依赖：overall_rating 必须出现在 raw_text 里
    assert "4.5" in g2.raw_text
    # 来源平台名 + 主题词也要落进 raw_text，便于下游 evidence 锁定
    assert "G2" in g2.raw_text
    assert "AI" in g2.raw_text or "易上手" in g2.raw_text
    assert g2.fetch_method == "search"  # LLM 联网搜索归到 search literal
    assert g2.source_type == "user_reviews"

    # LLM 路径成功后，应跳过传统 search + scrape 链；coverage 来自 reviews_finding_to_docs
    assert out.coverage_by_dimension[CollectDimension.REVIEWS] == 2


def test_reviews_via_llm_empty_finding_falls_through_to_seed(make_registry) -> None:
    """LLM 返回空 finding（小众产品）时，落到 host seed 兜底路径；
    没 scraper 可用时主链最终 NO_RELEVANT_RESULTS，但 errors 同时包含 LLM 路径痕迹。"""
    empty = _ReviewsFinding(overall_rating=None, sources=[])
    fake_llm = FakeLLM(by_response_format={_ReviewsFinding: empty})
    reg = make_registry()
    agent = Collector(llm=fake_llm, tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="UnknownTinyProduct",
        dimensions=[CollectDimension.REVIEWS],
        fallback_to_mock=False,
    )

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # LLM 路径报了 NO_RELEVANT_RESULTS
    assert any(
        e.code == "NO_RELEVANT_RESULTS" and "LLM web search" in e.message
        for e in out.errors
    )
    # FakeLLM 被调用过一次（验证 LLM 主路径真的走了）
    assert any(
        c["response_format"] is _ReviewsFinding for c in fake_llm.call_log
    )


def test_reviews_via_llm_failure_does_not_break_flow(make_registry) -> None:
    """LLM 抛异常时被捕获记 TOOL_FAILED，不影响 Collector 走兜底链。"""
    fake_llm = FakeLLM(raise_on_call=True)
    reg = make_registry()
    agent = Collector(llm=fake_llm, tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.REVIEWS],
        fallback_to_mock=False,
    )

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # LLM 路径报错被吃掉
    assert any(
        e.code == "TOOL_FAILED" and "reviews_finder" in e.message
        for e in out.errors
    )
    # 没 scraper enabled 时主链也拿不到东西，但流程不崩
    assert out.status in (AgentStatus.FAILED, AgentStatus.PARTIAL)


# ---------- 10. LLM token / cost 累加（配合 I 窗口） ----------


def test_collector_accumulates_llm_token_usage_into_output(make_registry) -> None:
    """每次 LLM 调用的 tokens_input/output/cost_usd 都应累加到 CollectorOutput。

    REVIEWS 维度只会触发一次 LLM（reviews_finder），所以累加值 = 单次配置。
    """
    finding = _ReviewsFinding(
        overall_rating=4.6,
        sources=[
            _ReviewSource(
                name="G2",
                url="https://www.g2.com/products/notion/reviews",
                excerpt="G2 4.7/5",
            ),
        ],
    )
    fake_llm = FakeLLM(
        by_response_format={_ReviewsFinding: finding},
        tokens_in_per_call=120,
        tokens_out_per_call=85,
        cost_usd_per_call=0.00123,
        model_name="fake-doubao",
    )
    reg = make_registry()
    agent = Collector(llm=fake_llm, tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.REVIEWS],
        fallback_to_mock=False,
    )

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 一次 reviews_finder LLM call 的 token 全部落到 output
    assert out.tokens_input == 120
    assert out.tokens_output == 85
    assert abs(out.cost_usd - 0.00123) < 1e-9


def test_collector_resets_token_accumulator_between_invocations(make_registry) -> None:
    """同一 Collector 实例被反复 invoke 时，每次 token 计数都从 0 开始。

    防御 Orchestrator 复用 Collector 跑多个产品时 token 跨次叠加。
    """
    finding = _ReviewsFinding(
        overall_rating=4.5,
        sources=[
            _ReviewSource(
                name="G2",
                url="https://www.g2.com/products/x/reviews",
                excerpt="X",
            ),
        ],
    )
    fake_llm = FakeLLM(
        by_response_format={_ReviewsFinding: finding},
        tokens_in_per_call=50,
        tokens_out_per_call=30,
        cost_usd_per_call=0.001,
    )
    reg = make_registry()
    agent = Collector(llm=fake_llm, tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.REVIEWS],
        fallback_to_mock=False,
    )

    out1 = agent.invoke(inp, trace_id="t1", span_id="s1")
    out2 = agent.invoke(inp, trace_id="t2", span_id="s2")

    # 两次 invoke 的 token 数应该都等于单次配置，而不是相加
    assert out1.tokens_input == 50
    assert out2.tokens_input == 50
    assert out2.cost_usd == pytest.approx(0.001)


def test_mock_mode_reports_zero_token_usage() -> None:
    """mock 模式不走 LLM，tokens / cost 应该都是 0。"""
    agent = Collector(mock=True)
    inp = make_collector_input(
        dimensions=[CollectDimension.HOMEPAGE, CollectDimension.PRICING],
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.tokens_input == 0
    assert out.tokens_output == 0
    assert out.cost_usd == 0.0


# ---------- QA feedback 进 reviews_finder prompt ----------


def test_qa_feedback_reaches_reviews_finder_prompt(make_registry) -> None:
    """端到端：把 qa_feedback 塞进 CollectorInput，REVIEWS 维度 LLM 路径触发，
    断言 FakeLLM.call_log 里 user prompt 含 issue 文本。

    覆盖 QA → Collector reviews_finder 反馈环：``collect.<p>.reviews_v{n+1}`` 节点
    要能看到上一轮 verdict（如 freshness fail）并据此换搜索策略。
    """
    finding = _ReviewsFinding(
        overall_rating=4.5,
        review_count=100,
        positive_themes=["x"],
        negative_themes=["y"],
        sample_quotes=["q"],
        sources=[
            _ReviewSource(
                name="G2",
                url="https://www.g2.com/products/notion/reviews",
                excerpt="ok",
            )
        ],
    )
    fake_llm = FakeLLM(by_response_format={_ReviewsFinding: finding})
    reg = make_registry()
    agent = Collector(llm=fake_llm, tools=reg, tracer=NullTracer(), mock=False)

    qa_feedback = {
        "from_verdict_id": "v_collector_test",
        "revision": 1,
        "instructions": "上轮 G2 评论时间已超过 2 年，需要更新的来源",
        "must_address": ["iss_stale_reviews"],
        "issues": [
            {
                "issue_id": "iss_stale_reviews",
                "dimension": "freshness",
                "severity": "major",
                "location": "evidence[ev_notion_reviews_g2_old]",
                "problem": "G2 评论 last_updated 已是 2 年前，需要 fresher 来源",
                "suggested_fix": "搜索 last 6 months 的评论，或换 Capterra/TrustRadius",
                "target_agent": "collector",
                "required_inputs": {"avoid_evidence_ids": ["ev_notion_reviews_g2_old"]},
            }
        ],
    }

    inp_base = make_collector_input(
        product_name="Notion",
        dimensions=[CollectDimension.REVIEWS],
        fallback_to_mock=False,
    )
    inp = inp_base.model_copy(update={"qa_feedback": qa_feedback})

    agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 找 reviews_finder 那次 chat（response_format == _ReviewsFinding）
    reviews_calls = [
        c for c in fake_llm.call_log if c["response_format"] is _ReviewsFinding
    ]
    assert reviews_calls, "reviews_finder LLM 路径未触达"
    user_content = next(
        (m["content"] for m in reviews_calls[0]["messages"] if m["role"] == "user"),
        "",
    )
    assert "QA Feedback" in user_content
    assert "G2 评论时间已超过 2 年" in user_content
    assert "iss_stale_reviews" in user_content
    assert "freshness" in user_content
    assert "avoid_evidence_ids" in user_content
    assert "ev_notion_reviews_g2_old" in user_content


# ---------- 相对语义：维度×来源类型权威矩阵 + reviews 维度采集豁免 ----------


def test_authority_is_relative_to_dimension() -> None:
    """同一来源换维度，权威度反转：官网查定价=高/查口碑=低；评论站查口碑=高/查定价=低。"""
    from backend.agents.collector.agent import _heuristic_authority

    off = "https://notion.so/pricing"
    g2 = "https://www.g2.com/products/notion/reviews"
    kw = {"product_name": "Notion", "official_url": "https://notion.so"}

    assert _heuristic_authority(url=off, dimension=CollectDimension.PRICING, **kw) == 0.95
    assert _heuristic_authority(url=off, dimension=CollectDimension.REVIEWS, **kw) == 0.5
    assert _heuristic_authority(url=g2, dimension=CollectDimension.REVIEWS, **kw) == 0.92
    assert _heuristic_authority(url=g2, dimension=CollectDimension.PRICING, **kw) == 0.6


def test_reviews_llm_path_uses_relative_authority() -> None:
    """LLM 评论路径的 doc 权威度走相对矩阵（review×reviews=0.92），不再硬编 0.75。"""
    finding = _ReviewsFinding(
        overall_rating=4.5,
        sources=[
            _ReviewSource(
                name="G2",
                url="https://www.g2.com/products/notion/reviews",
                excerpt="4.5/5",
            )
        ],
    )
    agent = Collector(mock=True)
    inp = make_collector_input(product_name="Notion", dimensions=[CollectDimension.REVIEWS])
    docs = agent._reviews_finding_to_docs(inp=inp, finding=finding)
    assert docs
    assert all(d.source_authority == 0.92 for d in docs)


def _review_scrape(url: str, *, paywall: bool = False, text: str = "x") -> ScrapeResult:
    return ScrapeResult(
        url=url,
        final_url="",
        http_status=200,
        fetched_with="firecrawl",
        text=text,
        title="Notion reviews",
        detected_paywall=paywall,
    )


def test_reviews_host_exempt_from_collection_penalties() -> None:
    """评论站在 reviews 维度：付费墙/短文本/ambiguous 全豁免——采集不再判其「真实性不够」。"""
    agent = Collector(mock=True)
    inp = make_collector_input(product_name="Notion", dimensions=[CollectDimension.REVIEWS])
    g2 = "https://www.g2.com/products/notion/reviews"

    review_doc = agent._build_raw_source_doc(
        inp=inp,
        dimension=CollectDimension.REVIEWS,
        scrape=_review_scrape(g2, paywall=True, text="short"),
        fetch_method="firecrawl",
        identity_status="ambiguous",
    )
    conf = agent._compute_confidence([review_doc], [CollectDimension.REVIEWS])
    assert conf == pytest.approx(agent.BASE_CONFIDENCE)  # 0.95，零惩罚

    # 对照：同样缺陷换到 pricing 维度（评论站非该维度预期源）→ 被扣分
    pricing_doc = agent._build_raw_source_doc(
        inp=inp,
        dimension=CollectDimension.PRICING,
        scrape=_review_scrape(g2, paywall=True, text="short"),
        fetch_method="firecrawl",
        identity_status="ambiguous",
    )
    conf_p = agent._compute_confidence([pricing_doc], [CollectDimension.PRICING])
    assert conf_p < agent.BASE_CONFIDENCE


# ---------- A: REVIEWS 只从指定评论站采（allowlist，非黑名单）----------


def test_reviews_only_from_specified_review_hosts(make_registry) -> None:
    """REVIEWS 维度只从 G2/Capterra/TrustRadius 等指定评论站采——通用搜索顶上来的
    YouTube 视频评测被丢弃（指定 allowlist，修正「在 YouTube 抓评论 + 身份判成 YouTube」）。"""
    yt = "https://www.youtube.com/watch?v=coda-review"
    g2 = "https://www.g2.com/products/coda/reviews"
    search = FakeSearch(
        fixed={
            # FakeSearch 按 query 子串匹配；REVIEWS 查询是 "Coda review G2 Capterra"
            "review": [
                SearchHit(url=yt, title="Coda review video", provider="t"),
                SearchHit(url=g2, title="Coda reviews | G2", provider="t"),
            ]
        }
    )
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        default=ScrapeResult(
            url=g2,
            final_url=g2,
            http_status=200,
            text="Coda is rated 4.5/5 on G2. Users praise its flexibility. " * 3,
            title="Coda reviews",
            fetched_with="scrape.firecrawl",
        ),
    )
    reg = make_registry(search=search, firecrawl=firecrawl)
    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Coda",
        dimensions=[CollectDimension.REVIEWS],
        fallback_to_mock=False,
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    urls = {str(s.source_url) for s in out.raw_sources}
    assert not any("youtube.com" in u for u in urls), "YouTube 不该作为评论源被采"
    assert any(e.code == "NON_REVIEW_SOURCE_SKIPPED" for e in out.errors)


# ---------- B: confidence 按可用覆盖 + self_critique 分类 ----------


def _doc(
    agent,
    inp,
    dim,
    *,
    url: str,
    text: str = "x" * 400,
    paywall: bool = False,
    identity_status: str = "confirmed",
    detected: str | None = None,
):
    sr = ScrapeResult(
        url=url,
        final_url=url,
        http_status=200,
        fetched_with="firecrawl",
        text=text,
        title="t",
        detected_paywall=paywall,
    )
    return agent._build_raw_source_doc(
        inp=inp,
        dimension=dim,
        scrape=sr,
        fetch_method="firecrawl",
        identity_status=identity_status,  # type: ignore[arg-type]
        detected_product_name=detected,
    )


def test_environmental_failure_with_usable_coverage_keeps_confidence() -> None:
    """某页短文本/失败，但该维度还有可用源 → confidence 不降（按可用覆盖，不按失败页数）。"""
    agent = Collector(mock=True)
    inp = make_collector_input(
        product_name="Notion", dimensions=[CollectDimension.FEATURES]
    )
    good = _doc(agent, inp, CollectDimension.FEATURES, url="https://notion.so/features")
    short = _doc(
        agent, inp, CollectDimension.FEATURES, url="https://blog.com/n", text="x"
    )
    # 维度仍有 1 个可用源 → 不扣分
    assert agent._compute_confidence(
        [good, short], [CollectDimension.FEATURES]
    ) == pytest.approx(agent.BASE_CONFIDENCE)
    # 但若该维度**只有**短文本（无可用源）→ 实质缺口，扣分
    assert (
        agent._compute_confidence([short], [CollectDimension.FEATURES])
        < agent.BASE_CONFIDENCE
    )


def test_self_critique_separates_actionable_and_environmental() -> None:
    """self_critique 把「需处理(身份不符)」与「采集受限(正文过短,信息性)」分开。"""
    agent = Collector(mock=True)
    inp = make_collector_input(
        product_name="Coda", dimensions=[CollectDimension.FEATURES]
    )
    good = _doc(agent, inp, CollectDimension.FEATURES, url="https://coda.io/features")
    short = _doc(
        agent, inp, CollectDimension.FEATURES, url="https://blog.com/c", text="x"
    )
    mism = _doc(
        agent,
        inp,
        CollectDimension.FEATURES,
        url="https://youtube.com/x",
        identity_status="mismatch",
        detected="YouTube",
    )
    crit = agent._build_self_critique(
        [good, short, mism], [CollectDimension.FEATURES], []
    )
    assert "需处理:" in crit and "采集受限" in crit
    actionable_part = crit.split("采集受限")[0]
    assert "身份不符" in actionable_part, "身份不符应在「需处理」段"
    assert "正文过短" not in actionable_part, "正文过短应在「采集受限」段，不在需处理"
    assert "正文过短" in crit
