"""Collector 产品身份校验（混合：启发式 gate + 模糊时 LLM 裁定）。

覆盖：
- 启发式确证（官方域名 / 标题+正文强命中）→ confirmed，不调 LLM。
- 启发式无法确证 → 调 LLM；LLM 判「不是目标产品」→ mismatch + detected 名 + 低置信。
- 无 LLM → 保守停在 ambiguous（绝不靠启发式硬判 mismatch）。
- 软标记：mismatch 的源**保留**在 raw_sources（不丢数据），并拉低 collector 置信。
- P4：exclude_source_urls 命中的候选直接跳过（不再抓回同一个跑题页面）。
"""
from __future__ import annotations

from datetime import UTC, datetime

from backend.agents.collector.agent import (
    Collector,
    _IdentityCheck,
    _assess_identity_heuristic,
    _domain_label,
    _identity_aliases,
)
from backend.agents.collector.tools import ScrapeResult, SearchHit
from backend.schemas import AgentStatus, CollectDimension, CollectorInput, RawSourceDoc

from .conftest import (
    FakeLLM,
    FakeScrape,
    FakeSearch,
    NullLLM,
    NullTracer,
    make_collector_input,
)


# ---------- 启发式工具 ----------


def test_domain_label_strips_www_and_tld() -> None:
    assert _domain_label("www.dingtalk.com") == "dingtalk"
    assert _domain_label("dingtalk.com") == "dingtalk"
    assert _domain_label("feishu.cn") == "feishu"
    assert _domain_label("notion.so") == "notion"


def test_identity_aliases_includes_name_and_domain_label() -> None:
    al = _identity_aliases("钉钉", "https://www.dingtalk.com/")
    assert "钉钉" in al
    assert "dingtalk" in al  # 跨语言：中文名搜不到英文页时靠域名标签兜底


def test_heuristic_confirms_official_domain_without_llm() -> None:
    status, conf, detected, decided = _assess_identity_heuristic(
        text="some content",
        title="Pricing",
        url="https://www.notion.so/pricing",
        product_name="Notion",
        official_url="https://www.notion.so/",
    )
    assert (status, decided) == ("confirmed", True)
    assert detected == "Notion" and conf >= 0.85


def test_heuristic_ambiguous_when_thirdparty_and_no_strong_hit() -> None:
    status, _conf, _detected, decided = _assess_identity_heuristic(
        text="This article mostly talks about another product.",
        title="Best tools 2026",
        url="https://thirdparty.com/roundup",
        product_name="Asana",
        official_url=None,
    )
    assert (status, decided) == ("ambiguous", False)  # 交给 LLM


# ---------- _assess_identity（含 LLM 裁定）----------


def _scrape(url: str, *, title: str, text: str) -> ScrapeResult:
    return ScrapeResult(
        url=url, final_url=url, http_status=200, text=text, title=title,
        fetched_with="scrape.firecrawl",
    )


def test_llm_flags_mismatch_when_content_is_other_product(make_registry) -> None:
    fake = FakeLLM(
        by_response_format={
            _IdentityCheck: _IdentityCheck(
                is_target_product=False,
                detected_product_name="Notion",
                confidence=0.9,
            )
        }
    )
    agent = Collector(llm=fake, tools=make_registry(), tracer=NullTracer(), mock=False)
    inp = make_collector_input(product_name="Asana", dimensions=[CollectDimension.FEATURES])
    scrape = _scrape(
        "https://thirdparty.com/notion-overview",
        title="Notion overview",
        text="Notion is a great workspace. Notion blocks, Notion databases.",
    )
    detected, conf, status = agent._assess_identity(inp=inp, scrape=scrape)
    assert status == "mismatch"
    assert detected == "Notion"
    assert conf is not None and conf <= 0.2  # 确属 Asana 的置信度应很低


def test_no_llm_stays_ambiguous_never_hard_mismatch() -> None:
    agent = Collector(mock=True)  # llm=None
    inp = make_collector_input(product_name="Asana", dimensions=[CollectDimension.FEATURES])
    scrape = _scrape(
        "https://thirdparty.com/roundup",
        title="Best tools 2026",
        text="A roundup of productivity tools.",
    )
    _detected, _conf, status = agent._assess_identity(inp=inp, scrape=scrape)
    assert status == "ambiguous"  # 没有 LLM 时绝不硬判 mismatch


# ---------- _compute_confidence 受身份惩罚 ----------


def _raw(url: str, *, identity_status: str) -> RawSourceDoc:
    return RawSourceDoc(
        source_id="src_" + url[-6:],
        product_name="Asana",
        dimension=CollectDimension.FEATURES,
        source_url=url,
        source_type="html",
        raw_text="x" * 400,
        collected_at=datetime.now(tz=UTC),
        fetch_method="firecrawl",
        identity_status=identity_status,  # type: ignore[arg-type]
    )


def test_mismatch_sources_drag_confidence_below_rework_threshold() -> None:
    agent = Collector(mock=True)
    dims = [CollectDimension.FEATURES]
    clean = [_raw(f"https://x.com/{i}", identity_status="confirmed") for i in range(2)]
    mismatched = [_raw(f"https://y.com/{i}", identity_status="mismatch") for i in range(3)]
    conf_clean = agent._compute_confidence(clean, dims)
    conf_bad = agent._compute_confidence(clean + mismatched, dims)
    assert conf_bad < conf_clean
    # 3 个 mismatch * 0.15 = 0.45 惩罚，足以压到自评 NEEDS_REWORK 阈值以下
    assert conf_bad < agent.SELF_CRITIQUE_THRESHOLD


# ---------- 流水线：软标记保留 + exclude 跳过 ----------


def test_mismatch_source_kept_not_dropped(make_registry) -> None:
    """软标记：抓到别的产品也**保留**在 raw_sources，只打 identity_status=mismatch。"""
    url = "https://thirdparty.com/notion-overview"
    search = FakeSearch(fixed={"features": [SearchHit(url=url, title="Notion overview", provider="t")]})
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        default=ScrapeResult(
            url=url, final_url=url, http_status=200,
            text="Notion is a workspace. Notion databases, Notion pages.",
            title="Notion overview", fetched_with="scrape.firecrawl",
        ),
    )
    fake = FakeLLM(
        by_response_format={
            _IdentityCheck: _IdentityCheck(
                is_target_product=False, detected_product_name="Notion", confidence=0.9
            )
        }
    )
    reg = make_registry(search=search, firecrawl=firecrawl)
    agent = Collector(llm=fake, tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Asana", dimensions=[CollectDimension.FEATURES], fallback_to_mock=False
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert len(out.raw_sources) == 1  # 没有被丢弃
    src = out.raw_sources[0]
    assert src.identity_status == "mismatch"
    assert src.detected_product_name == "Notion"


def test_collect_emits_progress_per_source(make_registry) -> None:
    """实时进度：每抓+校验完一条来源就推一条事件(含身份判定),供前端边采边看。"""
    from backend.agents._progress import (
        reset_collect_progress_emitter,
        set_collect_progress_emitter,
    )

    events: list[dict] = []
    url = "https://asana.com/features"
    search = FakeSearch(
        fixed={"features": [SearchHit(url=url, title="Asana features", provider="t")]}
    )
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        default=ScrapeResult(
            url=url, final_url=url, http_status=200,
            text="Asana features: tasks, projects, timelines. Asana workflows.",
            title="Asana features", fetched_with="scrape.firecrawl",
        ),
    )
    reg = make_registry(search=search, firecrawl=firecrawl)
    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Asana", dimensions=[CollectDimension.FEATURES], fallback_to_mock=False
    )
    token = set_collect_progress_emitter(lambda p: events.append(p))
    try:
        agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    finally:
        reset_collect_progress_emitter(token)

    assert events, "应为每条来源推一条进度事件"
    e = events[0]
    assert e["product"] == "Asana"
    assert str(e["url"]).startswith("https://asana.com/features")
    assert "identity_status" in e and e["dimension"] == "features"


def test_no_emitter_is_silent_noop(make_registry) -> None:
    """未注入 emitter 时采集照常,不报错(实时进度是纯观测)。"""
    url = "https://asana.com/features"
    search = FakeSearch(
        fixed={"features": [SearchHit(url=url, title="Asana features", provider="t")]}
    )
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        default=ScrapeResult(
            url=url, final_url=url, http_status=200, text="Asana features. Asana.",
            title="Asana features", fetched_with="scrape.firecrawl",
        ),
    )
    reg = make_registry(search=search, firecrawl=firecrawl)
    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    inp = make_collector_input(
        product_name="Asana", dimensions=[CollectDimension.FEATURES], fallback_to_mock=False
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert len(out.raw_sources) == 1  # 无 emitter 也正常产出


def test_exclude_source_urls_skips_candidate(make_registry) -> None:
    """P4：上一轮被 QA 判 mismatch 的 URL 注入 exclude_source_urls → 重采直接跳过。"""
    bad = "https://thirdparty.com/notion-overview"
    good = "https://asana.com/features"
    search = FakeSearch(
        fixed={
            "features": [
                SearchHit(url=bad, title="Notion overview", provider="t"),
                SearchHit(url=good, title="Asana features", provider="t"),
            ]
        }
    )
    firecrawl = FakeScrape(
        name="scrape.firecrawl",
        enabled=True,
        url_to_result={
            good: ScrapeResult(
                url=good, final_url=good, http_status=200,
                text="Asana features: tasks, projects, timelines. Asana workflows.",
                title="Asana features", fetched_with="scrape.firecrawl",
            ),
        },
    )
    reg = make_registry(search=search, firecrawl=firecrawl)
    agent = Collector(llm=NullLLM(), tools=reg, tracer=NullTracer(), mock=False)
    base = make_collector_input(
        product_name="Asana", dimensions=[CollectDimension.FEATURES], fallback_to_mock=False
    )
    inp = base.model_copy(update={"exclude_source_urls": [bad]})
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert bad not in firecrawl.call_log  # 被排除的页面根本没抓
    urls = [str(s.source_url) for s in out.raw_sources]
    assert all(bad.rstrip("/") not in u for u in urls)
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
