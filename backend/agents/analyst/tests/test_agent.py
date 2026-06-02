"""Analyst Agent 单元测试。

覆盖：
1. mock 模式全维度跑通：3 竞品 + 6 维度 → SUCCESS + 全部 evidence 合法
2. mock 模式部分 profile 缺失 → PARTIAL + PROFILE_INCOMPLETE 告警
3. 启发式语义：pricing comparison_matrix + cheapest entry claim 命中真实数据
4. 启发式语义：feature_comparison 识别 ClickUp 自动化领先 Notion
5. _scrub_claims：丢弃纯非法 evidence claim，保留部分非法并降置信
6. _post_validate：dimension 缺失 → AgentRunError → status=NEEDS_REWORK
7. _post_validate：claim 引用 pool 外 evidence_id → AgentRunError
8. AnalystInput extra=forbid Schema 严格性
9. 低 profile 覆盖率 → low confidence + 非空 self_critique
10. collect_profile_evidence_ids 汇总所有 evidence_refs
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.analyst import Analyst, collect_profile_evidence_ids
from backend.agents.analyst.fixtures import (
    load_competitor_profile,
    load_competitor_profiles,
    load_demo_input,
)
from backend.agents.analyst.tests.conftest import NullLLM, NullTracer
from backend.schemas import (
    AgentStatus,
    AnalysisClaim,
    AnalysisDimension,
    AnalystInput,
    AnalystOutput,
    DimensionAnalysis,
)

# ---------- 1. mock 全维度跑通 ----------


def test_mock_full_pipeline_returns_success() -> None:
    agent = Analyst(mock=True)
    inp = load_demo_input(
        target="Notion",
        competitors=["ClickUp", "Asana"],
        dimensions=[
            AnalysisDimension.FEATURE_COMPARISON,
            AnalysisDimension.PRICING_COMPARISON,
            AnalysisDimension.SWOT,
            AnalysisDimension.DIFFERENTIATION,
            AnalysisDimension.POSITIONING,
            AnalysisDimension.USER_FEEDBACK,
        ],
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert isinstance(out, AnalystOutput)
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    assert out.result.target_product == "Notion"
    assert out.result.competitors == ["ClickUp", "Asana"]
    # 每个请求的维度都必须有占位 DimensionAnalysis
    assert set(out.result.dimensions.keys()) == set(inp.dimensions)
    # BaseAgent 注入的元数据
    assert out.agent_name == "analyst"
    assert out.agent_version == "1.0.0"
    assert out.trace_id == "trace-demo"

    # 至少有一个维度产出了 ≥1 条 claim
    total_claims = sum(len(d.claims) for d in out.result.dimensions.values())
    assert total_claims >= 3

    # 所有 claim 的 evidence_ids 都在合法池内
    valid_pool: set[str] = set()
    for p in inp.profiles.values():
        valid_pool.update(collect_profile_evidence_ids(p))
    for analysis in out.result.dimensions.values():
        for claim in analysis.claims:
            assert claim.evidence_ids, f"claim {claim.claim_id} has empty evidence_ids"
            assert all(e in valid_pool for e in claim.evidence_ids), (
                f"claim {claim.claim_id} cites unknown evidence: {claim.evidence_ids}"
            )


# ---------- 2. profile 缺失 → PARTIAL ----------


def test_missing_competitor_profile_marks_partial() -> None:
    agent = Analyst(mock=True)
    # 故意缺失 Asana 的 profile
    profiles = load_competitor_profiles(["Notion", "ClickUp"])
    inp = AnalystInput(
        task_id="t",
        project_id="p",
        trace_id="tr",
        span_id="sp",
        target_product="Notion",
        competitors=["ClickUp", "Asana"],  # 但声称对比 Asana
        profiles=profiles,
        dimensions=[AnalysisDimension.PRICING_COMPARISON, AnalysisDimension.SWOT],
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status in (AgentStatus.PARTIAL, AgentStatus.NEEDS_REWORK)
    assert any(e.code == "PROFILE_INCOMPLETE" for e in out.errors)
    assert "Asana" in next(
        e.message for e in out.errors if e.code == "PROFILE_INCOMPLETE"
    )


# ---------- 3. pricing 启发式语义 ----------


def test_pricing_comparison_matrix_and_cheapest_claim() -> None:
    agent = Analyst(mock=True)
    inp = load_demo_input(
        target="Notion",
        competitors=["ClickUp", "Asana"],
        dimensions=[AnalysisDimension.PRICING_COMPARISON],
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    pricing = out.result.dimensions[AnalysisDimension.PRICING_COMPARISON]
    assert pricing.comparison_matrix is not None
    entry = pricing.comparison_matrix["entry_paid_usd"]
    assert entry["ClickUp"] == 7.0
    assert entry["Notion"] == 10.0
    assert entry["Asana"] == pytest.approx(10.99)
    # advanced 档：Notion 15 / ClickUp 12 / Asana 24.99
    advanced = pricing.comparison_matrix["advanced_paid_usd"]
    assert advanced["Asana"] == pytest.approx(24.99)

    # cheapest claim 应指向 ClickUp
    cheapest_claims = [c for c in pricing.claims if c.claim_id == "cl_price_entry_low"]
    assert cheapest_claims, "缺少 cl_price_entry_low claim"
    assert "ClickUp" in cheapest_claims[0].text


# ---------- 4. feature_comparison 启发式语义 ----------


def test_feature_comparison_surfaces_workflow_automation_gap() -> None:
    agent = Analyst(mock=True)
    inp = load_demo_input(
        target="Notion",
        competitors=["ClickUp", "Asana"],
        dimensions=[AnalysisDimension.FEATURE_COMPARISON],
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    feat = out.result.dimensions[AnalysisDimension.FEATURE_COMPARISON]
    assert feat.comparison_matrix is not None
    # workflow_automation 应在矩阵里
    assert "workflow_automation" in feat.comparison_matrix
    wf_row = feat.comparison_matrix["workflow_automation"]
    assert wf_row.get("ClickUp") == "advanced"
    assert wf_row.get("Notion") == "basic"
    # 至少一条 claim 涉及 workflow_automation 强弱差异
    wf_claims = [c for c in feat.claims if "工作流自动化" in c.text]
    assert wf_claims, "未找到 workflow_automation 差异 claim"


# ---------- 5. _scrub_claims：丢弃 / 降置信 ----------


def test_scrub_claims_drops_fully_invalid_and_demotes_partial() -> None:
    agent = Analyst(mock=True)
    pool = {"ev_real_a", "ev_real_b"}
    analysis = DimensionAnalysis(
        dimension=AnalysisDimension.FEATURE_COMPARISON,
        summary="test",
        claims=[
            # 完全合法 — 保留原置信
            AnalysisClaim(
                claim_id="c_ok",
                text="ok",
                products_involved=["X"],
                evidence_ids=["ev_real_a"],
                confidence=0.9,
            ),
            # 部分非法 — 保留有效 evidence + 降置信
            AnalysisClaim(
                claim_id="c_partial",
                text="partial",
                products_involved=["X"],
                evidence_ids=["ev_real_b", "ev_fake_x"],
                confidence=0.9,
            ),
            # 全部非法 — 丢弃
            AnalysisClaim(
                claim_id="c_dropped",
                text="dropped",
                products_involved=["X"],
                evidence_ids=["ev_fake_y"],
                confidence=0.9,
            ),
        ],
        comparison_matrix=None,
        confidence=0.9,
    )
    cleaned, dropped = agent._scrub_claims(analysis, pool)

    assert dropped == 1
    kept_ids = [c.claim_id for c in cleaned.claims]
    assert "c_ok" in kept_ids
    assert "c_partial" in kept_ids
    assert "c_dropped" not in kept_ids
    partial = next(c for c in cleaned.claims if c.claim_id == "c_partial")
    assert partial.evidence_ids == ["ev_real_b"]
    assert partial.confidence == pytest.approx(0.8)  # 0.9 - 0.1


# ---------- 6. _post_validate dimension 缺失 ----------


def test_post_validate_rejects_missing_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    """伪造一个会跳过某个 dimension 的 _run_mock，验证 _post_validate 抓住。"""
    agent = Analyst(mock=True)
    inp = load_demo_input(
        dimensions=[AnalysisDimension.PRICING_COMPARISON, AnalysisDimension.SWOT],
    )

    original = agent._build_output

    def faulty_build(inp_: AnalystInput, *, allow_llm: bool) -> AnalystOutput:
        out = original(inp_, allow_llm=allow_llm)
        # 故意删除 SWOT
        out.result.dimensions.pop(AnalysisDimension.SWOT, None)
        return out

    monkeypatch.setattr(agent, "_build_output", faulty_build)

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    # BaseAgent 把 AgentRunError 转成 NEEDS_REWORK + errors 累积
    assert out.status is AgentStatus.NEEDS_REWORK
    assert any(e.code == "DIMENSION_NOT_APPLICABLE" for e in out.errors)


# ---------- 7. _post_validate 拦截 pool 外 evidence ----------


def test_post_validate_rejects_hallucinated_evidence_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Analyst(mock=True)
    inp = load_demo_input(
        dimensions=[AnalysisDimension.PRICING_COMPARISON],
    )

    original = agent._build_output

    def hallucinating_build(inp_: AnalystInput, *, allow_llm: bool) -> AnalystOutput:
        out = original(inp_, allow_llm=allow_llm)
        target_dim = out.result.dimensions[AnalysisDimension.PRICING_COMPARISON]
        if target_dim.claims:
            # 注入一个 pool 外 evidence id
            poisoned = target_dim.claims[0].model_copy(
                update={"evidence_ids": ["ev_hallucinated_xyz"]}
            )
            target_dim.claims[0] = poisoned
        else:
            # 没有 claim 时构造一个
            target_dim.claims.append(
                AnalysisClaim(
                    claim_id="cl_injected",
                    text="hallucinated",
                    products_involved=["Notion"],
                    evidence_ids=["ev_hallucinated_xyz"],
                    confidence=0.9,
                )
            )
        return out

    monkeypatch.setattr(agent, "_build_output", hallucinating_build)

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status is AgentStatus.NEEDS_REWORK
    assert any(e.code == "INSUFFICIENT_EVIDENCE" for e in out.errors)


# ---------- 8. Schema 严格性 ----------


def test_analyst_input_rejects_unknown_field() -> None:
    profiles = load_competitor_profiles(["Notion"])
    with pytest.raises(ValidationError):
        AnalystInput(  # type: ignore[call-arg]
            task_id="t",
            project_id="p",
            trace_id="tr",
            span_id="sp",
            target_product="Notion",
            competitors=[],
            profiles=profiles,
            dimensions=[AnalysisDimension.PRICING_COMPARISON],
            unknown_field="oops",
        )


# ---------- 9. 低覆盖率 profile → low confidence + 非空 critique ----------


def test_sparse_profile_yields_low_confidence_and_nonempty_critique() -> None:
    """构造极简 profile：只有 basic_info.name + pricing.pricing_model，
    其他字段空白 → 覆盖率低 → confidence < 0.6 → status=NEEDS_REWORK + self_critique 必填。"""
    from datetime import UTC, datetime

    from backend.schemas import (
        CompetitorProfile,
        PricingModel,
        PricingProfile,
        ProductBasicInfo,
    )

    sparse_target = CompetitorProfile(
        profile_id="profile_sparse_target",
        schema_version="1.0.0",
        industry="collaboration_saas",
        basic_info=ProductBasicInfo(name="SparseTarget", category="x"),
        pricing=PricingProfile(pricing_model=PricingModel.SUBSCRIPTION),
        extracted_at=datetime.now(tz=UTC),
    )
    sparse_competitor = CompetitorProfile(
        profile_id="profile_sparse_c",
        schema_version="1.0.0",
        industry="collaboration_saas",
        basic_info=ProductBasicInfo(name="SparseC", category="x"),
        pricing=PricingProfile(pricing_model=PricingModel.SUBSCRIPTION),
        extracted_at=datetime.now(tz=UTC),
    )
    inp = AnalystInput(
        task_id="t",
        project_id="p",
        trace_id="tr",
        span_id="sp",
        target_product="SparseTarget",
        competitors=["SparseC"],
        profiles={"SparseTarget": sparse_target, "SparseC": sparse_competitor},
        dimensions=[
            AnalysisDimension.PRICING_COMPARISON,
            AnalysisDimension.SWOT,
            AnalysisDimension.FEATURE_COMPARISON,
        ],
    )
    agent = Analyst(mock=True)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.confidence < 0.6
    assert out.status in (AgentStatus.NEEDS_REWORK, AgentStatus.FAILED)
    # confidence < 0.6 时 BaseAgent 强制 self_critique 非空
    assert out.self_critique.strip() != ""


# ---------- 10. collect_profile_evidence_ids ----------


def test_collect_profile_evidence_ids_includes_all_refs() -> None:
    """Notion fixture 含 basic_info / features / pricing / user_feedback / competitive /
    industry_extension 各处的 evidence_id，全部应被汇总进 pool。"""
    profile = load_competitor_profile("Notion")
    ids = collect_profile_evidence_ids(profile)
    # 至少包含 fixture 里出现的几个 evidence_ids
    expected_subset = {
        "ev_notion_home_01",  # basic_info / features / competitive / user_feedback
        "ev_notion_feature_01",  # features.ai_capabilities + industry_extension.ai_assistance
        "ev_notion_price_01",  # pricing.plans
        "ev_notion_price_02",  # pricing.plans
    }
    missing = expected_subset - ids
    assert not missing, f"未汇总的 evidence_ids: {missing}"


# ---------- 11. 真实模式（无 LLM 响应 → 启发式 fallback） ----------


def test_real_mode_with_failing_llm_falls_back_to_heuristic() -> None:
    agent = Analyst(llm=NullLLM(), tracer=NullTracer(), mock=False)
    inp = load_demo_input(
        target="Notion",
        competitors=["ClickUp", "Asana"],
        dimensions=[AnalysisDimension.PRICING_COMPARISON],
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # LLM 失败 → 启发式接管 → 仍然产出正常分析
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    pricing = out.result.dimensions[AnalysisDimension.PRICING_COMPARISON]
    assert pricing.claims  # heuristic 应产出 claim
    # 每条 LLM 失败都记一条 warn（LLM_SCHEMA_INVALID）
    assert any(e.code == "LLM_SCHEMA_INVALID" for e in out.errors)
