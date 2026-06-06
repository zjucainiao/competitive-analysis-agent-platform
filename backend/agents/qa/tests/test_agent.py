"""QA Agent 单元测试。

覆盖：
1. mock 模式：按 draft.version 切 fixture（v1 → needs_revision，v2 → pass）
2. 真实模式：完整 fixture 6 维度跑通 + overall=PASS
3. evidence_completeness：缺 evidence 段落 → reporter routing
4. evidence_completeness：维度 evidence 全空 → collector routing
5. schema_completeness：必填字段缺失 → extractor routing
6. freshness：敏感字段过期 evidence → collector routing
7. expression：禁用词 + 第一人称 → reporter routing
8. logic_consistency：同 product+plan 价格冲突 → 矛盾 issue
9. routing：多 target 分组 + 优先级排序
10. 防死循环：同 issue 出现 ≥3 次 → 自动降级 minor
11. 防死循环：prior_verdicts ≥5 → max_retry_reached + blocking=False
12. _post_validate：human 构造的不合法 verdict 被拒绝
13. QAInput extra=forbid Schema 严格
14. 不修改输入 draft
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.agents.qa import QA
from backend.agents.qa.fixtures import (
    load_demo_input,
    load_evidence_db,
)
from backend.agents.qa.routing import (
    MAX_RETRY_VERDICTS,
    SAME_ISSUE_MAX_OCCURRENCES,
    aggregate_verdict,
    build_routing,
    count_prior_issue_occurrences,
    downgrade_repeated_issues,
)
from backend.agents.qa.tests.conftest import NullLLM, NullTracer
from backend.schemas import (
    AgentStatus,
    Evidence,
    QADimension,
    QAInput,
    QAIssue,
    QAOutput,
    QAStatus,
    QAVerdict,
    ReportDraft,
    ReportParagraph,
    ReportSection,
)

# ---------- 1. mock 模式 ----------


def test_mock_v1_returns_needs_revision() -> None:
    agent = QA(mock=True)
    inp = load_demo_input()
    assert inp.draft.version == 1
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert isinstance(out, QAOutput)
    assert out.status is AgentStatus.SUCCESS
    assert out.verdict.overall_status is QAStatus.NEEDS_REVISION
    assert out.verdict.blocking is True
    # mock fixture 中 routing 直接指向 reporter
    targets = {r.target_agent for r in out.verdict.routing}
    assert "reporter" in targets


def test_mock_v2_returns_pass() -> None:
    agent = QA(mock=True)
    inp = load_demo_input()
    inp.draft.version = 2
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.verdict.overall_status is QAStatus.PASS
    assert out.verdict.blocking is False
    assert out.verdict.issues == []


def test_mock_output_contract() -> None:
    agent = QA(mock=True)
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.agent_name == "qa"
    assert out.agent_version == "1.0.0"
    assert out.trace_id == "trace-demo"
    assert out.span_id == "span-qa"
    assert out.confidence > 0


# ---------- 2. 真实模式 6 维度跑通 ----------


def test_real_full_draft_runs_all_dimensions() -> None:
    """draft_v1 + analysis_full + 3 profiles 完整跑 6 维度。

    fixture 中 industry_extension 填充率本就偏低（属于真实业务场景），
    所以 schema_completeness 可能不 pass。fixture evidence 也尚未落
    ``source_published_at``，freshness 会走中性 0.7 分（不是误报满分）。
    但事实/证据/逻辑/表达 4 个维度应当通过，且 overall 不应到 REJECT。
    """
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert isinstance(out, QAOutput)
    # 6 维度都有结果
    assert set(out.verdict.dimension_results.keys()) == set(QADimension)
    content_dims = {
        QADimension.FACT_CONSISTENCY,
        QADimension.EVIDENCE_COMPLETENESS,
        QADimension.LOGIC_CONSISTENCY,
        QADimension.EXPRESSION,
    }
    for d in content_dims:
        assert out.verdict.dimension_results[d].pass_, (
            f"content dimension {d.value} should pass on clean fixture"
        )
    # freshness 在 fixture 无日期时给中性 0.7（不是 1.0），但也不开 issue
    fr = out.verdict.dimension_results[QADimension.FRESHNESS]
    assert fr.score == pytest.approx(0.7, abs=1e-3)
    assert not any(
        i.dimension is QADimension.FRESHNESS for i in out.verdict.issues
    )
    # overall 不到 REJECT
    assert out.verdict.overall_status is not QAStatus.REJECT
    # Agent 自身成功跑完，无 fatal
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)


# ---------- 3. evidence_completeness ----------


def test_evidence_completeness_flags_missing_citation() -> None:
    """事实性段落缺 evidence_ids → reporter routing。"""
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    # 把 sec_features 第 1 段的 evidence 清掉
    sec = next(s for s in inp.draft.sections if s.section_id == "sec_features")
    sec.paragraphs[0].evidence_ids = []
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    ec = out.verdict.dimension_results[QADimension.EVIDENCE_COMPLETENESS]
    assert ec.pass_ is False
    para_issues = [
        i
        for i in out.verdict.issues
        if i.dimension is QADimension.EVIDENCE_COMPLETENESS
        and i.target_agent == "reporter"
    ]
    assert para_issues, "expected reporter-targeted evidence_completeness issue"
    assert any("sec_features" in i.required_inputs.get("section_id", "") for i in para_issues)
    # routing 中必须有 reporter
    assert any(r.target_agent == "reporter" for r in out.verdict.routing)


def test_evidence_completeness_empty_dimension_routes_to_collector() -> None:
    """整维度所有 claim 都没 evidence → critical issue + collector routing。"""
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    swot = inp.analysis.dimensions[
        __import__(
            "backend.schemas", fromlist=["AnalysisDimension"]
        ).AnalysisDimension.SWOT
    ]
    for claim in swot.claims:
        claim.evidence_ids = []
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    critical = [i for i in out.verdict.issues if i.severity == "critical"]
    assert critical, "expected critical issue for empty-evidence dimension"
    assert any(i.target_agent == "collector" for i in critical)


# ---------- 4. schema_completeness ----------


def test_schema_completeness_flags_missing_required_field() -> None:
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    # 把 Notion 的 positioning / category 全清空
    inp.profiles["Notion"].basic_info.positioning = None
    inp.profiles["Notion"].basic_info.target_users = []
    inp.profiles["Notion"].pricing.plans = []
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    sc = out.verdict.dimension_results[QADimension.SCHEMA_COMPLETENESS]
    assert sc.pass_ is False
    sc_issues = [
        i
        for i in out.verdict.issues
        if i.dimension is QADimension.SCHEMA_COMPLETENESS
        and i.target_agent == "extractor"
    ]
    assert sc_issues
    # must_address 必须告诉 Extractor 缺哪些字段
    must = sc_issues[0].required_inputs.get("must_address", [])
    assert "basic_info.positioning" in must


# ---------- 5. freshness ----------


def test_freshness_flags_stale_pricing_evidence() -> None:
    """把所有 evidence source_published_at 设到 2 年前 → 敏感字段 stale → collector。"""
    inp = load_demo_input()
    old_db = load_evidence_db()
    aged = {
        eid: Evidence.model_validate(
            {
                **ev.model_dump(mode="json"),
                "source_published_at": "2024-01-01T00:00:00Z",
            }
        )
        for eid, ev in old_db.items()
    }
    agent = QA(llm=NullLLM(), tracer=NullTracer(), evidence_db=aged)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    fr = out.verdict.dimension_results[QADimension.FRESHNESS]
    assert fr.pass_ is False
    stale_issues = [
        i for i in out.verdict.issues if i.dimension is QADimension.FRESHNESS
    ]
    assert stale_issues
    # 至少一个回到 collector
    assert any(i.target_agent == "collector" for i in stale_issues)


def test_freshness_gives_neutral_score_when_no_publish_date() -> None:
    """fixture evidence 全部无 source_published_at → 中性 0.7 而不是误报满分。"""
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    fr = out.verdict.dimension_results[QADimension.FRESHNESS]
    assert fr.score == pytest.approx(0.7, abs=1e-3)
    # 无日期不应直接开 issue（这是数据缺失，不是过期）
    assert not any(
        i.dimension is QADimension.FRESHNESS for i in out.verdict.issues
    )
    assert "无日期" in fr.notes


def test_freshness_drops_when_six_months_old_publish_date() -> None:
    """敏感字段引用了 6 个月前发布的 evidence → 分数明显下降并开 collector 路由。"""
    from datetime import datetime, timedelta, timezone

    inp = load_demo_input()
    old_db = load_evidence_db()
    six_months_ago = (
        datetime.now(timezone.utc) - timedelta(days=180)
    ).isoformat()
    dated = {
        eid: Evidence.model_validate(
            {
                **ev.model_dump(mode="json"),
                "source_published_at": six_months_ago,
            }
        )
        for eid, ev in old_db.items()
    }
    agent = QA(llm=NullLLM(), tracer=NullTracer(), evidence_db=dated)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    fr = out.verdict.dimension_results[QADimension.FRESHNESS]
    # 敏感字段（定价段）超过 SENSITIVE_MAX_DAYS=90 天 → stale
    assert fr.score < 0.85, f"expected score < 0.85 for 6-month-old refs, got {fr.score}"
    assert fr.pass_ is False
    stale_issues = [
        i for i in out.verdict.issues if i.dimension is QADimension.FRESHNESS
    ]
    assert any(i.target_agent == "collector" for i in stale_issues)


# ---------- logic_consistency 软冲突容差 ----------


def test_logic_consistency_passes_one_soft_conflict() -> None:
    """1 处 minor 软冲突仍应过阈值 0.85（不再像旧版 0.90 那样卡死）。"""
    from backend.agents.qa.checkers import CheckerContext, LogicConsistencyChecker

    inp = load_demo_input()
    ctx = CheckerContext(
        draft=inp.draft,
        analysis=inp.analysis,
        profiles=inp.profiles,
        evidence_db={},
    )
    # 构造 1 处 minor 价格冲突（同 product+plan 两个不同值）
    sec_pricing = next(
        s for s in inp.draft.sections if s.section_id == "sec_pricing"
    )
    sec_pricing.paragraphs.append(
        _mk_paragraph("p_pr_dup", "Notion Plus $11 比 ClickUp Unlimited $7 贵一点。")
    )
    checker = LogicConsistencyChecker()
    result = checker.run(ctx)
    # 1 处 major 价格冲突权重 0.10 → 0.90，仍 >= 0.85 阈值
    assert result.score >= 0.85, result.score
    assert result.pass_, f"single major conflict should still pass; got {result.notes}"


def test_logic_consistency_fails_two_hard_conflicts() -> None:
    """2 处 major 硬冲突累计 0.20 惩罚 → 0.80 < 0.85 → fail。"""
    from backend.agents.qa.checkers import CheckerContext, LogicConsistencyChecker

    inp = load_demo_input()
    ctx = CheckerContext(
        draft=inp.draft,
        analysis=inp.analysis,
        profiles=inp.profiles,
        evidence_db={},
    )
    sec_pricing = next(
        s for s in inp.draft.sections if s.section_id == "sec_pricing"
    )
    sec_pricing.paragraphs.append(
        _mk_paragraph("p_pr_dup_a", "Notion Plus $11 比 ClickUp Unlimited $7 贵一点。")
    )
    sec_pricing.paragraphs.append(
        _mk_paragraph("p_pr_dup_b", "Asana Starter $13 比 Notion Plus $9 贵一点。")
    )
    checker = LogicConsistencyChecker()
    result = checker.run(ctx)
    assert result.score < 0.85
    assert result.pass_ is False


# ---------- 5b. coverage_density ----------


def test_coverage_density_passes_on_full_draft() -> None:
    """demo draft 每个维度都被实质展开 → 满分、无 issue。"""
    from backend.agents.qa.checkers import CheckerContext, CoverageDensityChecker

    inp = load_demo_input()
    ctx = CheckerContext(
        draft=inp.draft,
        analysis=inp.analysis,
        profiles=inp.profiles,
        evidence_db={},
    )
    result = CoverageDensityChecker().run(ctx)
    assert result.pass_, result.notes
    assert result.score == pytest.approx(1.0)
    assert result.issues == []


def test_coverage_density_flags_thin_section_to_reporter() -> None:
    """某维度有带证据 claim，但报告章节只剩软占位 → major → reporter。"""
    from backend.agents.qa.checkers import CheckerContext, CoverageDensityChecker

    inp = load_demo_input()
    # feature_comparison 维度本有 3 条带证据 claim；把对应章节掏空成单段软占位
    sec = next(s for s in inp.draft.sections if s.section_id == "sec_features")
    placeholder = _mk_paragraph("p_features_placeholder", "暂无与功能相关的分析结论。")
    placeholder.is_soft_conclusion = True
    placeholder.is_quantitative = False
    sec.paragraphs = [placeholder]

    ctx = CheckerContext(
        draft=inp.draft,
        analysis=inp.analysis,
        profiles=inp.profiles,
        evidence_db={},
    )
    result = CoverageDensityChecker().run(ctx)
    assert result.pass_ is False
    majors = [i for i in result.issues if i.severity == "major"]
    assert majors, "thin section should raise a major issue"
    assert all(i.target_agent == "reporter" for i in majors)
    assert any(i.required_inputs.get("dimension") == "feature_comparison" for i in majors)


# ---------- 6. expression ----------


def test_expression_flags_banned_terms_and_first_person() -> None:
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    sec = next(s for s in inp.draft.sections if s.section_id == "sec_features")
    sec.paragraphs[0].text = "我们认为 Notion 是行业唯一的完美方案。"
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    ex_issues = [
        i for i in out.verdict.issues if i.dimension is QADimension.EXPRESSION
    ]
    banned_problems = [i for i in ex_issues if "绝对化" in i.problem]
    fp_problems = [i for i in ex_issues if "第一人称" in i.problem]
    assert banned_problems, "expected banned-terms issue"
    assert fp_problems, "expected first-person issue"
    # 都回到 reporter
    assert all(i.target_agent == "reporter" for i in ex_issues)


# ---------- 7. logic_consistency ----------


def test_logic_consistency_flags_price_conflict() -> None:
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    sec_pricing = next(
        s for s in inp.draft.sections if s.section_id == "sec_pricing"
    )
    # 在 pricing 章节插入和 p_pr_01 矛盾的段落
    sec_pricing.paragraphs.append(
        ReportParagraph(
            paragraph_id="p_pr_conflict",
            text="Notion Plus $18/seat/月，比 ClickUp Unlimited $7 更贵。",
            claim_ids=[],
            evidence_ids=["ev_notion_price_01", "ev_clickup_price_01"],
            is_quantitative=True,
            is_soft_conclusion=False,
        )
    )
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    lc_issues = [
        i for i in out.verdict.issues
        if i.dimension is QADimension.LOGIC_CONSISTENCY
    ]
    assert lc_issues, "expected logic_consistency issue for price conflict"
    assert any("Notion" in i.problem and "Plus" in i.problem for i in lc_issues)


# ---------- 8. routing ----------


def test_routing_groups_by_target_and_prioritizes_upstream() -> None:
    issues = [
        _mk_issue("iss_r1", QADimension.EVIDENCE_COMPLETENESS, "major", "reporter"),
        _mk_issue("iss_r2", QADimension.EVIDENCE_COMPLETENESS, "major", "reporter"),
        _mk_issue("iss_e1", QADimension.SCHEMA_COMPLETENESS, "major", "extractor"),
        _mk_issue("iss_c1", QADimension.EVIDENCE_COMPLETENESS, "critical", "collector"),
    ]
    routing = build_routing(issues, blocking=True)
    targets = [r.target_agent for r in routing]
    assert targets == ["collector", "extractor", "reporter"]
    reporter = next(r for r in routing if r.target_agent == "reporter")
    assert set(reporter.payload["must_address"]) == {"iss_r1", "iss_r2"}


def test_routing_marks_non_blocking_in_reason() -> None:
    issues = [_mk_issue("iss_x", QADimension.EXPRESSION, "minor", "reporter")]
    routing = build_routing(issues, blocking=False)
    assert routing[0].reason.startswith("(non-blocking)")


# ---------- 9. 防死循环 ----------


def test_downgrade_repeated_issues() -> None:
    """同一 issue 出现 ≥ SAME_ISSUE_MAX_OCCURRENCES 次后降级为 minor。"""
    prior_v = _verdict_with_issue(
        "iss_repeat",
        QADimension.EXPRESSION,
        "major",
        "reporter",
        location="report.sections[0].paragraphs[0]",
    )
    prior = [prior_v for _ in range(SAME_ISSUE_MAX_OCCURRENCES - 1)]
    prior_counts = count_prior_issue_occurrences(prior)
    new_issue = _mk_issue(
        "iss_repeat", QADimension.EXPRESSION, "major", "reporter",
        location="report.sections[0].paragraphs[0]",
    )
    adjusted, downgraded = downgrade_repeated_issues([new_issue], prior_counts)
    assert downgraded
    assert adjusted[0].severity == "minor"
    assert adjusted[0].required_inputs.get("downgraded_due_to_recurrence") is True


def test_max_retry_releases_blocking_via_agent() -> None:
    """prior_verdicts 累计 ≥ MAX_RETRY_VERDICTS → blocking=False + MAX_RETRY_REACHED 错误。"""
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    # 故意构造一个会失败的 draft
    sec = next(s for s in inp.draft.sections if s.section_id == "sec_features")
    sec.paragraphs[0].evidence_ids = []
    # 注入 5 个 prior_verdicts
    inp.prior_verdicts = [
        _verdict_with_issue(
            "iss_history",
            QADimension.EVIDENCE_COMPLETENESS,
            "major",
            "reporter",
            verdict_id=f"prior_{i}",
        )
        for i in range(MAX_RETRY_VERDICTS)
    ]
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.verdict.blocking is False, "max retry should release blocking"
    assert any(e.code == "MAX_RETRY_REACHED" for e in out.errors)


def test_aggregate_verdict_pass_when_no_issues() -> None:
    res = aggregate_verdict([], prior_count=0)
    assert res.status is QAStatus.PASS
    assert res.blocking is False


def test_aggregate_verdict_reject_when_many_criticals() -> None:
    issues = [
        _mk_issue(f"iss_c_{i}", QADimension.FACT_CONSISTENCY, "critical", "reporter")
        for i in range(2)
    ]
    res = aggregate_verdict(issues, prior_count=0)
    assert res.status is QAStatus.REJECT
    assert res.blocking is True


# ---------- 10. _post_validate ----------


def test_post_validate_rejects_missing_dimension() -> None:
    """子类强校验 6 维度必须齐全。"""
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    bogus = QAOutput(
        agent_name="qa",
        agent_version="1.0.0",
        task_id=inp.task_id,
        trace_id=inp.trace_id,
        span_id=inp.span_id,
        status=AgentStatus.SUCCESS,
        confidence=0.9,
        self_critique="",
        tokens_input=0,
        tokens_output=0,
        cost_usd=0.0,
        duration_ms=0,
        errors=[],
        verdict=QAVerdict(
            verdict_id="v_bogus",
            overall_status=QAStatus.PASS,
            dimension_results={},  # 故意缺
            issues=[],
            routing=[],
            blocking=False,
        ),
    )
    with pytest.raises(Exception) as exc:
        agent._post_validate(bogus, inp)
    assert "missing dimensions" in str(exc.value)


# ---------- 11. QAInput 严格 schema ----------


def test_qainput_rejects_extra_fields() -> None:
    inp = load_demo_input()
    payload = inp.model_dump(mode="json")
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        QAInput.model_validate(payload)


# ---------- 12. 不修改输入 draft ----------


def test_qa_does_not_mutate_draft() -> None:
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    snapshot = inp.draft.model_dump(mode="json")
    agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert inp.draft.model_dump(mode="json") == snapshot


# ---------- helpers ----------


def _mk_issue(
    issue_id: str,
    dim: QADimension,
    severity: str,
    target: str,
    *,
    location: str = "report.sections[0].paragraphs[0]",
) -> QAIssue:
    return QAIssue(
        issue_id=issue_id,
        dimension=dim,
        severity=severity,  # type: ignore[arg-type]
        location=location,
        problem="x",
        suggested_fix="y",
        target_agent=target,  # type: ignore[arg-type]
        required_inputs={},
    )


def _mk_paragraph(pid: str, text: str) -> ReportParagraph:
    return ReportParagraph(
        paragraph_id=pid,
        text=text,
        claim_ids=[],
        evidence_ids=[],
        is_quantitative=True,
        is_soft_conclusion=False,
    )


def _verdict_with_issue(
    issue_id: str,
    dim: QADimension,
    severity: str,
    target: str,
    *,
    location: str = "report.sections[0].paragraphs[0]",
    verdict_id: str = "v",
) -> QAVerdict:
    return QAVerdict(
        verdict_id=verdict_id,
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={},
        issues=[
            _mk_issue(issue_id, dim, severity, target, location=location)
        ],
        routing=[],
        blocking=True,
    )


# 避免未使用 import 警告
_ = (ReportDraft, ReportSection, datetime, timezone)
