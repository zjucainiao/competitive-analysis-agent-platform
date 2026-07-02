"""证据原文逐字（verbatim）保证的回归测试。

对应评审缺陷 H4：
1. fuzzy 命中时 Evidence.content 曾直接落 LLM 的 source_quote（可能是转述），
   现在必须落 linker 定位到的**原文窗口文本**；
2. 精确命中时 ``_original_index`` 的 12 字符锚点近似在「原文空白与规范化文本
   不一致」时会切错 matched_text / 偏移，现在必须用规范化映射精确回定位；
3. consolidation 补漏 claims 挂 raw_sources[0] 占位后，证据归属必须以
   EvidenceLinker 重定位的真实 source 为准。

不变量（贯穿所有用例）：
- Evidence.content 必须是源文档 raw_text 的逐字切片；
- location.char_start/char_end 存在时，raw_text[start:end] == content；
- fuzzy 命中的 confidence 上限必须低于精确命中（1.0）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.agents.extractor import Extractor
from backend.agents.extractor.tests.conftest import (
    LLMReply,
    NullTracer,
    ScriptedLLM,
    make_extractor_input,
)
from backend.agents.extractor.tools import EvidenceLinker, _normalize_for_match
from backend.schemas import CollectDimension, FieldStatus, RawSourceDoc

# ---------- 构造夹具 ----------

# fuzzy 用例：原文带换行 / 逗号，LLM 转述改成 "and" 串联 + "30-day" 连字符
FUZZY_RAW_TEXT = (
    "Notion pricing overview.\n\n"
    "Plus plan includes unlimited blocks for teams,\nunlimited file uploads, "
    "and 30 day page history.\nBusiness at $15 per seat/month."
)
FUZZY_PARAPHRASE = (
    "Plus plan includes unlimited blocks for teams and unlimited file uploads "
    "and 30-day page history"
)

# 精确命中但空白不一致：原文里有连续空格 + 换行，quote 是规范化后的单空格版本
WS_RAW_TEXT = (
    "Pricing overview.\n\n"
    "Plus at $10   per seat/month,\nbilled annually. Business at $15 per seat/month."
)
WS_QUOTE = "Plus at $10 per seat/month, billed annually."


def _make_source(
    source_id: str,
    raw_text: str,
    *,
    dimension: CollectDimension = CollectDimension.PRICING,
) -> RawSourceDoc:
    return RawSourceDoc(
        source_id=source_id,
        product_name="Notion",
        dimension=dimension,
        source_url="https://example.com/page",
        source_type="html",
        raw_text=raw_text,
        collected_at=datetime.now(tz=UTC),
        fetch_method="mock",
    )


# ---------- 1. Linker 级：fuzzy 命中返回原文窗口 ----------


def test_fuzzy_link_matched_text_is_verbatim_window() -> None:
    """fuzzy 命中时 matched_text 必须是 raw_text 的逐字切片（而非规范化文本），
    且 location 带精确偏移、confidence 低于精确命中。"""
    src = _make_source("src_fuzzy", FUZZY_RAW_TEXT)
    linker = EvidenceLinker()
    r = linker.link(FUZZY_PARAPHRASE, [src])

    assert r.matched is True
    assert r.match_type == "fuzzy"
    assert r.source_id == "src_fuzzy"
    # 核心断言：matched_text 是原文逐字文本
    assert r.matched_text is not None
    assert r.matched_text in FUZZY_RAW_TEXT
    # 偏移精确：切片回原文必须完全一致
    assert r.location.char_start is not None
    assert r.location.char_end is not None
    assert FUZZY_RAW_TEXT[r.location.char_start : r.location.char_end] == r.matched_text
    # fuzzy 置信分级：上限低于精确命中的 1.0
    assert r.confidence <= EvidenceLinker.FUZZY_CONFIDENCE_CAP < 1.0


# ---------- 2. Linker 级：精确命中 + 空白不一致 → 偏移不切错 ----------


def test_exact_link_offsets_survive_whitespace_mismatch() -> None:
    """原文空白（连续空格/换行）与规范化 quote 不一致时，精确命中的
    char_start/char_end 必须落在真实边界上，matched_text 不得被切短。"""
    src = _make_source("src_ws", WS_RAW_TEXT)
    linker = EvidenceLinker()
    r = linker.link(WS_QUOTE, [src])

    assert r.matched is True
    assert r.match_type == "exact"
    assert r.confidence == 1.0
    assert r.matched_text is not None
    # 不得因空白差异把尾巴切掉（旧实现会切成 "billed annuall"）
    assert r.matched_text.endswith("annually.")
    # 偏移必须与 matched_text 完全一致
    assert r.location.char_start is not None
    assert r.location.char_end is not None
    assert WS_RAW_TEXT[r.location.char_start : r.location.char_end] == r.matched_text
    # 规范化后与 quote 语义等价（逐字来自原文，只允许空白/引号差异）
    assert _normalize_for_match(r.matched_text).lower() == _normalize_for_match(WS_QUOTE).lower()


# ---------- 3. Agent 级：fuzzy 命中的 Evidence.content 是原文窗口而非转述 ----------


def test_fuzzy_evidence_content_is_source_window_not_paraphrase() -> None:
    """LLM 返回转述 quote、原文存在相似窗口 → Evidence.content 必须是原文窗口，
    绝不落 LLM 的转述文本；且 evidence_refs 仍能按 source_quote 绑回该证据。"""
    src = _make_source("src_fuzzy_pricing", FUZZY_RAW_TEXT)
    claims = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "Plus plan has unlimited blocks",
                "source_quote": FUZZY_PARAPHRASE,
                "confidence": 0.9,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={"src_fuzzy_pricing": [LLMReply(parsed=claims)]},
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=[src])
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert len(out.evidences) == 1
    ev = out.evidences[0]
    # 核心承诺：content 是源文档逐字文本，不是 LLM 转述
    assert ev.content != FUZZY_PARAPHRASE
    assert ev.content in FUZZY_RAW_TEXT
    # 偏移与 content 一致
    assert ev.location.char_start is not None
    assert ev.location.char_end is not None
    assert FUZZY_RAW_TEXT[ev.location.char_start : ev.location.char_end] == ev.content
    # fuzzy 置信分级：低于精确命中
    assert ev.confidence <= EvidenceLinker.FUZZY_CONFIDENCE_CAP < 1.0
    # 命中即不算 unmatched；字段状态 VERIFIED 且 evidence_refs 绑定成功
    assert FUZZY_PARAPHRASE not in out.unmatched_quotes
    assert out.profile.field_status.get("basic_info.positioning") is FieldStatus.VERIFIED
    assert ev.evidence_id in out.profile.basic_info.evidence_refs.get("positioning", [])


# ---------- 4. Agent 级：精确命中 + 空白不一致 → content 与偏移一致 ----------


def test_exact_evidence_content_matches_location_slice() -> None:
    """空白不一致的精确命中：Evidence.content 必须等于 raw_text[start:end]，
    不得因锚点近似切错。"""
    src = _make_source("src_ws_pricing", WS_RAW_TEXT)
    claims = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "subscription",
                "source_quote": WS_QUOTE,
                "confidence": 0.9,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={"src_ws_pricing": [LLMReply(parsed=claims)]},
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=[src])
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert len(out.evidences) == 1
    ev = out.evidences[0]
    assert ev.content in WS_RAW_TEXT
    assert ev.content.endswith("annually.")
    assert ev.location.char_start is not None
    assert ev.location.char_end is not None
    assert WS_RAW_TEXT[ev.location.char_start : ev.location.char_end] == ev.content
    assert ev.confidence == 1.0


# ---------- 5. Agent 级：consolidation 占位 source 由 linker 重定位 ----------


def test_consolidation_claims_relink_to_true_source() -> None:
    """consolidation 补漏 claims 挂 raw_sources[0] 占位，但证据归属必须以
    EvidenceLinker 反查命中的真实 source 为准（这里 quote 逐字来自第二个 source）。"""
    src_home = _make_source(
        "src_home",
        "Notion is the connected workspace where better, faster work happens.",
        dimension=CollectDimension.HOMEPAGE,
    )
    src_pricing = _make_source(
        "src_pricing",
        "Notion offers four plans: Free, Plus at $10 per seat/month, and Enterprise.",
    )
    homepage_claims = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "connected workspace",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.9,
            },
        ]
    }
    consolidation_claims = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "freemium",
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.9,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={
            "src_home": [LLMReply(parsed=homepage_claims)],
            "src_pricing": [LLMReply(parsed={"claims": []})],
            "CONSOLIDATION PASS": [LLMReply(parsed=consolidation_claims)],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=[src_home, src_pricing])
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # consolidation 补出的 pricing_model 证据必须归属真实命中的 src_pricing，
    # 而非占位的 raw_sources[0]（src_home）
    pricing_evs = [e for e in out.evidences if "pricing.pricing_model" in e.tags]
    assert pricing_evs, "consolidation 补出的 claim 应当成功铸造 evidence"
    assert all(e.source_id == "src_pricing" for e in pricing_evs)
    # 且 content 逐字来自 src_pricing 原文
    for e in pricing_evs:
        assert e.content in src_pricing.raw_text
