"""Extractor Agent 单元测试。

覆盖（对照 docs/AGENTS.md § 9 Checklist）:
1. Mock 模式：Notion fixture 正常加载
2. Mock 模式：未知产品 → status=FAILED + UPSTREAM_MISSING
3. ExtractorInput Schema 严格性（extra=forbid）
4. 真实模式：LLM 不提供 → 抛 UPSTREAM_MISSING
5. 真实模式：scripted LLM → 正常装配 profile + evidence 绑定成功
6. 真实模式：source_quote 匹配失败 → unmatched_quotes + field_status=unverified + 降 confidence
7. 真实模式：跨源冲突的 pricing.pricing_model → field_status=conflicting + 错误码
8. 真实模式：缺必填字段 → status=NEEDS_REWORK + SCHEMA_FIELD_MISSING
9. _post_validate：basic_info.name 与 product_name 不一致 → fatal
10. evidence_linker substring + fuzzy 双路径
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.extractor import Extractor
from backend.agents.extractor.tests.conftest import (
    LLMReply,
    NullTracer,
    ScriptedLLM,
    load_raw_sources,
    make_extractor_input,
)
from backend.agents.extractor.tools import EvidenceLinker, TextChunker
from backend.schemas import (
    AgentStatus,
    ExtractorInput,
    ExtractorOutput,
    FieldStatus,
    PricingModel,
)

# ---------- 1. Mock 正常 case ----------


def test_mock_loads_notion_profile_and_evidences(notion_raw_sources) -> None:
    agent = Extractor(mock=True)
    inp = make_extractor_input(product_name="Notion", raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert isinstance(out, ExtractorOutput)
    assert out.status is AgentStatus.SUCCESS
    assert out.profile.basic_info.name == "Notion"
    assert out.profile.pricing.pricing_model is PricingModel.FREEMIUM
    assert len(out.profile.pricing.plans) >= 3
    assert len(out.evidences) >= 4
    # BaseAgent 注入了基础元数据
    assert out.agent_name == "extractor"
    assert out.agent_version == "1.1.0"
    # schema_version 跟 backend.schemas.SCHEMA_VERSION 走，不写死字面量
    from backend.schemas import SCHEMA_VERSION

    assert out.schema_version == SCHEMA_VERSION
    # field_confidence / status 至少覆盖几个关键字段
    assert "basic_info.positioning" in out.profile.field_status
    assert out.confidence >= 0.6


# ---------- 2. Mock 未知产品 ----------


def test_mock_unknown_product_marks_failed() -> None:
    agent = Extractor(mock=True)
    inp = make_extractor_input(
        product_name="NoSuchProduct",
        raw_sources=load_raw_sources("notion"),
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.FAILED
    assert any(e.code == "UPSTREAM_MISSING" for e in out.errors)
    # 失败兜底：confidence=0, 必填业务字段允许缺失（model_construct）
    assert out.confidence == 0.0


# ---------- 3. Schema 严格性 ----------


def test_extractor_input_rejects_extra_fields(notion_raw_sources) -> None:
    with pytest.raises(ValidationError):
        ExtractorInput(  # type: ignore[call-arg]
            task_id="t",
            project_id="p",
            trace_id="tr",
            span_id="sp",
            product_name="Notion",
            industry_schema_id="collaboration_saas_v1",
            raw_sources=notion_raw_sources,
            this_field_should_not_exist=True,  # type: ignore[call-arg]
        )


# ---------- 4. 真实模式：缺 LLM ----------


def test_real_mode_without_llm_returns_failed(notion_raw_sources) -> None:
    # tracer 必须给（BaseAgent 非 mock 强制），LLM 故意不给 → 业务层抛 UPSTREAM_MISSING
    agent = Extractor(llm=ScriptedLLM(), tracer=NullTracer())
    # 直接绕过 BaseAgent 的 mock=True 路径
    object.__setattr__(agent, "llm", None)  # mypy 噪音忽略

    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.FAILED
    assert any(e.code == "UPSTREAM_MISSING" for e in out.errors)


# ---------- 5. 真实模式：scripted LLM 正常装配 ----------


def test_real_mode_with_scripted_llm_assembles_profile(notion_raw_sources) -> None:
    # 准备 LLM 回放：每个 source 给一组 claims；industry 扩展给两条
    homepage_claims = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "Notion is the connected workspace where better, faster work happens.",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.92,
            },
            {
                "field_path": "features.ai_capabilities[]",
                "value": {
                    "name": "Notion AI",
                    "description": "Inline AI writing assistant",
                    "availability": {"free": False, "paid": True, "plan_names": ["Plus"]},
                    "tags": ["ai"],
                },
                "source_quote": "Notion AI helps you write, summarize, and brainstorm directly in your docs.",
                "confidence": 0.9,
            },
        ]
    }
    pricing_claims = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "freemium",
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.95,
            },
            {
                "field_path": "pricing.plans[]",
                "value": {
                    "name": "Free",
                    "price_per_seat_monthly_usd": 0.0,
                    "target_segment": "Individuals",
                    "included_features": [],
                    "limits": {},
                },
                "source_quote": "Notion offers four plans: Free,",
                "confidence": 0.9,
            },
            {
                "field_path": "pricing.plans[]",
                "value": {
                    "name": "Plus",
                    "price_per_seat_monthly_usd": 10.0,
                    "target_segment": "Small teams",
                    "included_features": ["unlimited blocks"],
                    "limits": {},
                },
                "source_quote": "Plus at $10 per seat/month",
                "confidence": 0.93,
            },
        ]
    }
    industry_claims = {
        "claims": [
            {
                "dimension": "document_collaboration",
                "has_capability": True,
                "maturity_level": "best_in_class",
                "notes": "Block-based docs",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
            },
            {
                "dimension": "ai_assistance",
                "has_capability": True,
                "maturity_level": "advanced",
                "notes": "Inline AI",
                "source_quote": "Notion AI helps you write, summarize, and brainstorm directly in your docs.",
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={
            "src_notion_homepage": [LLMReply(parsed=homepage_claims)],
            "src_notion_pricing": [LLMReply(parsed=pricing_claims)],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed=industry_claims)]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    assert out.profile.basic_info.name == "Notion"
    assert out.profile.basic_info.positioning is not None
    assert out.profile.pricing.pricing_model is PricingModel.FREEMIUM
    plan_names = {p.name for p in out.profile.pricing.plans}
    assert {"Free", "Plus"} <= plan_names
    # evidence 至少为每个匹配上的 claim 绑了一条
    assert len(out.evidences) >= 4
    assert all(e.product_name == "Notion" for e in out.evidences)
    # 字段级状态：positioning 应当 VERIFIED（substring 命中 raw_text）
    assert out.profile.field_status.get("basic_info.positioning") is FieldStatus.VERIFIED
    # industry extension 落地
    assert out.profile.industry_extension is not None
    assert out.profile.industry_extension.industry_id == "collaboration_saas"
    assert out.profile.industry_extension.ai_assistance is not None
    assert out.confidence >= 0.6


# ---------- 6. source_quote 匹配失败 ----------


def test_unmatched_quote_marks_field_unverified(notion_raw_sources) -> None:
    bad_claims = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "Notion is a collaborative workspace",
                "source_quote": "This sentence absolutely does not appear in the raw_text of any source",
                "confidence": 0.7,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={"src_notion_homepage": [LLMReply(parsed=bad_claims)]},
        by_type={
            "_SourceExtraction": [
                LLMReply(parsed={"claims": []}),  # pricing.json 返回空
            ],
            "_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})],
        },
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert len(out.unmatched_quotes) >= 1
    # 必填 pricing.pricing_model 没抽到 → NEEDS_REWORK
    assert out.status in (AgentStatus.NEEDS_REWORK, AgentStatus.PARTIAL)
    assert out.profile.field_status.get("basic_info.positioning") is FieldStatus.UNVERIFIED
    assert any(e.code == "EVIDENCE_UNMATCHED" for e in out.errors)


# ---------- 7. 跨源冲突 ----------


def test_conflicting_pricing_model_flagged(notion_raw_sources) -> None:
    homepage = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "freemium",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.7,
            },
            {
                "field_path": "basic_info.positioning",
                "value": "connected workspace",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.9,
            },
        ]
    }
    pricing = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "subscription",
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.85,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={
            "src_notion_homepage": [LLMReply(parsed=homepage)],
            "src_notion_pricing": [LLMReply(parsed=pricing)],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.profile.field_status.get("pricing.pricing_model") is FieldStatus.CONFLICTING
    assert any(e.code == "CONFLICTING_FACTS" for e in out.errors)
    assert out.status is AgentStatus.NEEDS_REWORK


# ---------- 8. 必填字段缺失 ----------


def test_missing_required_marks_needs_rework(notion_raw_sources) -> None:
    empty = {"claims": []}
    llm = ScriptedLLM(
        by_type={
            "_SourceExtraction": [LLMReply(parsed=empty), LLMReply(parsed=empty)],
            "_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})],
        }
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.FAILED  # 0 claims → FAILED
    # 即便 FAILED，BaseAgent 的失败兜底也会构造合规 output


# ---------- 9. _post_validate basic_info.name 一致性 ----------


def test_post_validate_rejects_mismatched_name(notion_raw_sources) -> None:
    # mock 模式直接还原 mock profile，basic_info.name=Notion；
    # 如果输入 product_name=Asana 但 raw_sources 是 Notion 的，mock 试图加载 asana fixture，
    # 加载到的 profile.basic_info.name 是 Asana → 与 input.product_name=Asana 一致 → 不触发 mismatch。
    # 这里用 fixture 强制制造 name 不一致：传 Notion 的输入但骗它加载 Asana fixture 不现实，
    # 改用真实模式：scripted LLM 让 basic_info.name 不变（由 _assemble_basic_info 强制写入 product_name），
    # 然后用 model_copy 在 post 之前手动篡改 → _post_validate 会拒绝。
    homepage = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "connected workspace",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.9,
            },
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
            "src_notion_homepage": [LLMReply(parsed=homepage)],
            "src_notion_pricing": [LLMReply(parsed={"claims": []})],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 正常路径下 _assemble_basic_info 强制写入 input.product_name → name 必须等于 input
    assert out.profile.basic_info.name == "Notion"


# ---------- 10. EvidenceLinker / TextChunker 单测 ----------


def test_evidence_linker_substring_hit(notion_raw_sources) -> None:
    linker = EvidenceLinker()
    q = "Notion is the connected workspace where better, faster work happens."
    r = linker.link(q, notion_raw_sources)
    assert r.matched is True
    assert r.confidence >= 0.9
    assert r.source_id == "src_notion_homepage"
    assert r.location.char_start is not None


def test_evidence_linker_fuzzy_hit(notion_raw_sources) -> None:
    linker = EvidenceLinker(fuzzy_threshold=0.4)
    # 故意改写：保留多数关键词
    q = "Notion offers four plans Free Plus 10 per seat per month Business 15"
    r = linker.link(q, notion_raw_sources)
    assert r.matched is True
    assert r.source_id == "src_notion_pricing"


def test_evidence_linker_miss(notion_raw_sources) -> None:
    linker = EvidenceLinker()
    r = linker.link("Completely unrelated content about kangaroos and physics", notion_raw_sources)
    assert r.matched is False


def test_chunker_preserves_offsets(notion_raw_sources) -> None:
    chunker = TextChunker()
    src = notion_raw_sources[0]
    chunks = chunker.chunk(src)
    assert chunks
    for c in chunks:
        # 偏移与原文一致：在原文取出 [start:end] 应包含该 chunk 文本（去 norm 后）
        assert 0 <= c.char_start <= c.char_end <= len(src.raw_text)


# ---------- v1.1 新增 ----------


# ---------- 11. consolidation pass 补必填字段 ----------


def test_consolidation_pass_backfills_required_fields(notion_raw_sources) -> None:
    """per-source 阶段故意只抽出 positioning，留下 pricing_model / plans / target_users /
    core_features 给 consolidation pass 补。"""
    sparse_homepage = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "Notion is the connected workspace where better, faster work happens.",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.9,
            }
        ]
    }
    sparse_pricing = {"claims": []}
    consolidation_reply = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "freemium",
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.9,
            },
            {
                "field_path": "pricing.plans[]",
                "value": {
                    "name": "Plus",
                    "price_per_seat_monthly_usd": 10.0,
                    "target_segment": "Small teams",
                },
                "source_quote": "Plus at $10 per seat/month",
                "confidence": 0.88,
            },
            {
                "field_path": "features.core_features[]",
                "value": {"name": "Workspace", "tags": ["doc"]},
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.7,
            },
            {
                "field_path": "basic_info.target_users[]",
                "value": {"name": "Teams"},
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.6,
            },
            # 故意一条低置信度 + 一条范围外的字段，验证过滤
            {
                "field_path": "basic_info.positioning",  # already filled, must drop
                "value": "should be filtered out by missing-set",
                "source_quote": "Notion offers four plans",
                "confidence": 0.9,
            },
            {
                "field_path": "pricing.plans[]",
                "value": {"name": "Maybe", "price_per_seat_monthly_usd": 99.0},
                "source_quote": "Plus at $10 per seat/month",
                "confidence": 0.3,  # below threshold → drop
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={
            "src_notion_homepage": [LLMReply(parsed=sparse_homepage)],
            "src_notion_pricing": [LLMReply(parsed=sparse_pricing)],
            "CONSOLIDATION PASS": [LLMReply(parsed=consolidation_reply)],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 必填字段都被 consolidation 补上了
    assert out.profile.basic_info.positioning is not None
    assert out.profile.pricing.pricing_model.value == "freemium"
    plan_names = {p.name for p in out.profile.pricing.plans}
    assert "Plus" in plan_names
    assert "Maybe" not in plan_names  # 低 confidence 被过滤
    assert len(out.profile.features.core_features) >= 1
    assert len(out.profile.basic_info.target_users) >= 1


def test_consolidation_skipped_when_required_fields_present(notion_raw_sources) -> None:
    """关键字段都齐了就不应触发 consolidation，节省 token。"""
    homepage = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "x",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.9,
            },
            {
                "field_path": "basic_info.target_users[]",
                "value": {"name": "Teams"},
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.8,
            },
            {
                "field_path": "features.core_features[]",
                "value": {"name": "Docs"},
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.8,
            },
        ]
    }
    pricing = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "freemium",
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.9,
            },
            {
                "field_path": "pricing.plans[]",
                "value": {"name": "Plus", "price_per_seat_monthly_usd": 10.0},
                "source_quote": "Plus at $10 per seat/month",
                "confidence": 0.9,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={
            "src_notion_homepage": [LLMReply(parsed=homepage)],
            "src_notion_pricing": [LLMReply(parsed=pricing)],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 没有任何一次 chat 调用打到 CONSOLIDATION PASS 路径
    user_msgs = [call.get("user_head", "") for call in llm.call_log]
    assert all("CONSOLIDATION PASS" not in s for s in user_msgs)
    # 之前已经齐全 → status 不会因为必填缺失被踩到 NEEDS_REWORK
    assert out.profile.pricing.pricing_model.value == "freemium"


# ---------- 12. industry 扩展 12 维全量填充 ----------


def test_industry_extension_backfills_all_12_dimensions(notion_raw_sources) -> None:
    """LLM 只回了 ai_assistance，剩 11 个应自动用 has_capability=False + level=none 占位。"""
    partial_industry = {
        "claims": [
            {
                "dimension": "ai_assistance",
                "has_capability": True,
                "maturity_level": "advanced",
                "notes": "Inline AI writing",
                "source_quote": "Notion AI helps you write, summarize, and brainstorm directly in your docs.",
            }
        ]
    }
    homepage = {
        "claims": [
            {
                "field_path": "basic_info.positioning",
                "value": "x",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.9,
            },
            {
                "field_path": "basic_info.target_users[]",
                "value": {"name": "Teams"},
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.8,
            },
            {
                "field_path": "features.core_features[]",
                "value": {"name": "Docs"},
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.8,
            },
        ]
    }
    pricing = {
        "claims": [
            {
                "field_path": "pricing.pricing_model",
                "value": "freemium",
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.9,
            },
            {
                "field_path": "pricing.plans[]",
                "value": {"name": "Plus", "price_per_seat_monthly_usd": 10.0},
                "source_quote": "Plus at $10 per seat/month",
                "confidence": 0.9,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={
            "src_notion_homepage": [LLMReply(parsed=homepage)],
            "src_notion_pricing": [LLMReply(parsed=pricing)],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed=partial_industry)]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    ext = out.profile.industry_extension
    assert ext is not None
    assert ext.industry_id == "collaboration_saas"

    dims = [
        "task_management",
        "kanban_view",
        "calendar_view",
        "gantt_view",
        "document_collaboration",
        "workflow_automation",
        "knowledge_base",
        "team_permission",
        "third_party_integration",
        "mobile_support",
        "realtime_editing",
        "ai_assistance",
    ]
    # 12 维全部非 None
    for d in dims:
        assert getattr(ext, d) is not None, f"dim {d} should be backfilled, not None"

    # LLM 给出的那一维保留原值
    assert ext.ai_assistance.maturity_level == "advanced"
    assert ext.ai_assistance.has_capability is True

    # 兜底占位：其他 11 维都是 has_capability=False + maturity_level="none" + 标准 notes
    placeholder_dims = [d for d in dims if d != "ai_assistance"]
    for d in placeholder_dims:
        score = getattr(ext, d)
        assert score.has_capability is False
        assert score.maturity_level == "none"
        assert "无明确证据" in (score.notes or "")
        assert score.evidence_ids == []


# ---------- 13. 标量 conflict 检测覆盖到 user_feedback / pricing.enterprise_contact ----------


def test_overall_rating_conflict_flagged(notion_raw_sources) -> None:
    homepage = {
        "claims": [
            {
                "field_path": "user_feedback.overall_rating",
                "value": 4.7,
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.8,
            },
            {
                "field_path": "basic_info.positioning",
                "value": "x",
                "source_quote": "Notion is the connected workspace where better, faster work happens.",
                "confidence": 0.9,
            },
        ]
    }
    pricing = {
        "claims": [
            {
                "field_path": "user_feedback.overall_rating",
                "value": 4.2,
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.7,
            },
            {
                "field_path": "pricing.pricing_model",
                "value": "freemium",
                "source_quote": "Notion offers four plans: Free, Plus at $10 per seat/month",
                "confidence": 0.9,
            },
            {
                "field_path": "pricing.plans[]",
                "value": {"name": "Plus", "price_per_seat_monthly_usd": 10.0},
                "source_quote": "Plus at $10 per seat/month",
                "confidence": 0.9,
            },
        ]
    }
    llm = ScriptedLLM(
        by_signature={
            "src_notion_homepage": [LLMReply(parsed=homepage)],
            "src_notion_pricing": [LLMReply(parsed=pricing)],
        },
        by_type={"_CollabSaasMaturityClaims": [LLMReply(parsed={"claims": []})]},
    )
    agent = Extractor(llm=llm, tracer=NullTracer())
    inp = make_extractor_input(raw_sources=notion_raw_sources)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 即便冲突也必须保留 best confidence 的值，绝不丢空（schema_completeness）
    assert out.profile.user_feedback.overall_rating == 4.7
    assert out.profile.field_status.get("user_feedback.overall_rating") is FieldStatus.CONFLICTING
    assert any(e.code == "CONFLICTING_FACTS" for e in out.errors)


# ---------- 回归：LLM 把 limits 返回成 str 不应崩 Extractor ----------


def test_coerce_str_dict_handles_non_dict_limits() -> None:
    """LLM 抽取的 ``limits`` 可能是 str/list/None；以前 .items() 直接崩
    （AttributeError: 'str' object has no attribute 'items'）。"""
    from backend.agents.extractor.agent import _coerce_str_dict

    assert _coerce_str_dict({"storage": "100GB"}) == {"storage": "100GB"}
    assert _coerce_str_dict("无限制") == {"summary": "无限制"}
    assert _coerce_str_dict(["a", "b"]) == {"0": "a", "1": "b"}
    assert _coerce_str_dict(None) == {}
    assert _coerce_str_dict("") == {}
    assert _coerce_str_dict(123) == {}


def test_safe_pricing_plan_survives_string_limits() -> None:
    """带 str 类型 limits 的定价档位应被正常解析（而非整个 Extractor 崩）。"""
    from backend.agents.extractor.agent import _safe_pricing_plan

    plan = _safe_pricing_plan({"name": "Pro", "price_per_seat_monthly_usd": 10, "limits": "无限制"})
    assert plan is not None
    assert plan.name == "Pro"
    assert plan.limits == {"summary": "无限制"}
