"""Extractor 把源文档的身份校验结论继承到 Evidence。

抓错产品的源（identity_status=mismatch）抽出的证据也应携带 mismatch，
让下游 QA 的 identity_consistency 维度能据此发现「这条证据其实来自别的产品」。
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.agents.extractor.agent import Extractor
from backend.agents.extractor.tools import LinkResult
from backend.schemas import CollectDimension, EvidenceLocation, RawSourceDoc

from .conftest import make_extractor_input


def _mismatch_source() -> RawSourceDoc:
    return RawSourceDoc(
        source_id="s_mismatch",
        product_name="钉钉",  # 声称采集的是钉钉
        dimension=CollectDimension.REVIEWS,
        source_url="https://thirdparty.com/lark-review",
        source_type="html",
        raw_text="飞书 is a great collaboration tool. 飞书 messaging and docs.",
        collected_at=datetime.now(tz=UTC),
        fetch_method="firecrawl",
        detected_product_name="飞书",
        identity_confidence=0.1,
        identity_status="mismatch",
    )


def test_mint_evidence_inherits_identity_from_source() -> None:
    agent = Extractor(mock=True)
    inp = make_extractor_input(product_name="钉钉", raw_sources=[_mismatch_source()])
    link = LinkResult(
        source_id="s_mismatch",
        matched_text="飞书 messaging and docs",
        location=EvidenceLocation(),
        confidence=0.8,
        matched=True,
    )
    ev = agent._mint_evidence(
        inp=inp, link=link, quote="飞书 messaging and docs", tag="user_feedback"
    )
    # product_name 仍是「声称的」钉钉，但身份字段暴露了它其实来自飞书
    assert ev.product_name == "钉钉"
    assert ev.identity_status == "mismatch"
    assert ev.detected_product_name == "飞书"
    assert ev.identity_confidence == 0.1


def test_mint_evidence_default_unvalidated_for_legacy_source() -> None:
    """旧源（无身份字段）继承到的 Evidence 维持 unvalidated，不误报。"""
    agent = Extractor(mock=True)
    src = _mismatch_source().model_copy(
        update={
            "detected_product_name": None,
            "identity_confidence": None,
            "identity_status": "unvalidated",
        }
    )
    inp = make_extractor_input(product_name="钉钉", raw_sources=[src])
    link = LinkResult(
        source_id="s_mismatch",
        matched_text="x",
        location=EvidenceLocation(),
        confidence=0.8,
        matched=True,
    )
    ev = agent._mint_evidence(inp=inp, link=link, quote="飞书 messaging and docs", tag="t")
    assert ev.identity_status == "unvalidated"
    assert ev.detected_product_name is None
