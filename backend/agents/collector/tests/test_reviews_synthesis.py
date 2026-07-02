"""H1：REVIEWS 维度 LLM 合成证据必须可区分、可降权（证据链完整性回归测试）。

LLM 联网搜索路径产出的 RawSourceDoc 是**模型合成文本**而非真实抓取——若 provider
无联网能力（DeepSeek 默认没有），整条可能是参数记忆幻觉。契约要求：
- ``fetch_method`` 用独立值 ``"llm_synthesis"``，下游可与真实抓取区分；
- ``identity_status`` 不得硬编码 confirmed（在合成文本上跑身份校验是循环论证——
  文本由同一个 LLM 生成、必然提到目标产品）；
- ``source_authority`` 显著低于 QA 弱源阈值（0.7），作为数字/事实唯一支撑时被浮出；
- 无真实引用 URL 时禁止伪造 G2 等真实站点 URL，用 ``.invalid`` 合成标记 URI。
"""

from __future__ import annotations

from backend.agents.collector.agent import (
    LLM_SYNTHESIS_AUTHORITY,
    Collector,
    _ReviewsFinding,
    _ReviewSource,
)
from backend.schemas import CollectDimension

from .conftest import make_collector_input

_G2_URL = "https://www.g2.com/products/notion/reviews"


def _docs_for(finding: _ReviewsFinding):
    agent = Collector(mock=True)
    inp = make_collector_input(product_name="Notion", dimensions=[CollectDimension.REVIEWS])
    return agent._reviews_finding_to_docs(inp=inp, finding=finding)


def _finding_with_g2() -> _ReviewsFinding:
    return _ReviewsFinding(
        overall_rating=4.5,
        review_count=5000,
        positive_themes=["易上手"],
        negative_themes=["移动端弱"],
        sample_quotes=["Great tool."],
        sources=[_ReviewSource(name="G2", url=_G2_URL, excerpt="G2 4.5/5")],
    )


def test_synthesis_doc_fetch_method_is_llm_synthesis() -> None:
    """合成 doc 的 fetch_method 必须是独立值，不得伪装成搜索/抓取产物。"""
    docs = _docs_for(_finding_with_g2())
    assert docs
    assert all(d.fetch_method == "llm_synthesis" for d in docs)


def test_synthesis_doc_identity_not_hardcoded_confirmed() -> None:
    """合成 doc 的身份不得硬编码 confirmed：停在 ambiguous，置信 < 0.85。"""
    docs = _docs_for(_finding_with_g2())
    assert docs
    for d in docs:
        assert d.identity_status == "ambiguous"
        assert d.identity_confidence is not None and d.identity_confidence < 0.85


def test_synthesis_doc_authority_below_weak_threshold() -> None:
    """合成 doc 的权威度显著低于 QA 弱源阈值 0.7（不再按评论站正典 0.92 高企）。"""
    docs = _docs_for(_finding_with_g2())
    assert docs
    assert all(d.source_authority == LLM_SYNTHESIS_AUTHORITY for d in docs)
    assert LLM_SYNTHESIS_AUTHORITY < 0.7


def test_no_sources_does_not_fabricate_review_site_url() -> None:
    """LLM 没给引用 URL 时，禁止伪造 G2 等真实站点 URL——用 .invalid 合成标记 URI。"""
    finding = _ReviewsFinding(overall_rating=4.2, review_count=100, sources=[])
    docs = _docs_for(finding)
    assert len(docs) == 1
    host = docs[0].source_url.host or ""
    assert "g2.com" not in host and "capterra.com" not in host and "trustradius.com" not in host
    assert host.endswith(".invalid")  # RFC 2606 保留域，保证永不指向真实站点
    # 无 URL 的纯聚合合成置信更低，且 source_class 未判定（没有可验证的来源类型）
    assert docs[0].identity_status == "ambiguous"
    assert docs[0].source_class is None


def test_real_cited_urls_are_preserved() -> None:
    """provider 真联网返回了引用 URL 时保留原 URL（可溯源路径不被修死）。"""
    docs = _docs_for(_finding_with_g2())
    assert len(docs) == 1
    assert str(docs[0].source_url).rstrip("/") == _G2_URL
    assert docs[0].source_class == "review"  # 按 URL 判定的声称来源类型
    # 内容仍要携带 Extractor 依赖的评分文本
    assert "4.5" in docs[0].raw_text
