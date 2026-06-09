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
    mark_unresolved_from_prior,
    synthesize_threshold_issues,
)
from backend.agents.qa.tests.conftest import FakeLLM, NullLLM, NullTracer
from backend.schemas import (
    AgentStatus,
    Evidence,
    QADimension,
    QADimensionResult,
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

    fixture evidence 未落 ``source_published_at`` → freshness 全无日期、不计分默认通过。
    NullLLM.chat 抛错 → fact_consistency 拿不到真实 entailment，走「未核验」降级
    （pass_=False + 非阻塞 minor），**不再** fail-open 报满分通过。证据/逻辑/表达 3 个
    内容维度仍应通过，overall 不应到 REJECT。
    """
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert isinstance(out, QAOutput)
    # 6 维度都有结果
    assert set(out.verdict.dimension_results.keys()) == set(QADimension)
    content_dims = {
        QADimension.EVIDENCE_COMPLETENESS,
        QADimension.LOGIC_CONSISTENCY,
        QADimension.EXPRESSION,
    }
    for d in content_dims:
        assert out.verdict.dimension_results[d].pass_, (
            f"content dimension {d.value} should pass on clean fixture"
        )
    # fact_consistency：无真实 LLM → 降级「未核验」，不再 fail-open 报通过
    fc = out.verdict.dimension_results[QADimension.FACT_CONSISTENCY]
    assert fc.pass_ is False, "无 LLM 时事实一致性应降级未核验，而非 fail-open 通过"
    assert "未核验" in fc.notes
    fc_issues = [
        i for i in out.verdict.issues if i.dimension is QADimension.FACT_CONSISTENCY
    ]
    assert fc_issues, "降级应补发一条说明未核验的 issue"
    assert all(i.severity == "minor" for i in fc_issues), "降级 issue 非阻塞(minor)"
    # freshness：fixture 全无日期 → 不计入评分、默认通过(1.0)，不再永久卡 0.7。
    # 无日期 ≠ 过期；只有真带日期且过期才报警(见 test_freshness_flags_stale_*)。
    fr = out.verdict.dimension_results[QADimension.FRESHNESS]
    assert fr.score == pytest.approx(1.0, abs=1e-3)
    assert fr.pass_ is True
    fr_issues = [
        i for i in out.verdict.issues if i.dimension is QADimension.FRESHNESS
    ]
    assert not fr_issues, "全无日期不应再补发 freshness issue（无日期≠过期）"
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


def test_freshness_passes_when_all_evidence_undated() -> None:
    """fixture evidence 全部无 source_published_at → 不计入评分、默认通过(1.0)。

    取舍(2026-06-08)：无日期 ≠ 过期。Collector 现不抽发布日期，旧实现按中性 0.7
    计 → 永久卡在阈值下、把判级顶在「待修复」，纯噪音。改为无日期不参与评分、
    全无日期则 pass，不再 gating；只有真带日期且过期才报警（见下个用例）。
    """
    agent = QA(llm=NullLLM(), tracer=NullTracer())
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    fr = out.verdict.dimension_results[QADimension.FRESHNESS]
    assert fr.score == pytest.approx(1.0, abs=1e-3)
    assert fr.pass_ is True
    fr_issues = [
        i for i in out.verdict.issues if i.dimension is QADimension.FRESHNESS
    ]
    assert not fr_issues, "全无日期不应补发 freshness issue"
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


def test_mark_unresolved_from_prior_escalates_second_occurrence() -> None:
    """改动1：上一轮已要求(第 2 次出现)的 issue → 打未解决标记 + problem 加前缀；
    本轮新出现的(第 1 次)不标。severity 保持不变(不动权重/判级)。"""
    loc = "report.sections[0].paragraphs[0]"
    prior = [
        _verdict_with_issue(
            "iss_x", QADimension.LOGIC_CONSISTENCY, "major", "reporter", location=loc
        )
    ]
    prior_counts = count_prior_issue_occurrences(prior)  # 该 key 计 1 次

    recurring = _mk_issue(
        "iss_x", QADimension.LOGIC_CONSISTENCY, "major", "reporter", location=loc
    )
    fresh = _mk_issue(
        "iss_y",
        QADimension.FACT_CONSISTENCY,
        "major",
        "reporter",
        location="report.sections[1].paragraphs[0]",
    )

    out = {i.issue_id: i for i in mark_unresolved_from_prior([recurring, fresh], prior_counts)}

    assert out["iss_x"].required_inputs.get("unresolved_from_prior_round") is True
    assert out["iss_x"].problem.startswith("[上一轮已要求修复但仍未解决]")
    assert out["iss_x"].severity == "major"  # 不升级 severity
    assert out["iss_y"].required_inputs.get("unresolved_from_prior_round") is None


def test_mark_unresolved_skips_downgrade_threshold() -> None:
    """改动1：已复发到降级阈值的 issue 不在这里加压（交给 downgrade_repeated_issues 放弃）。"""
    loc = "report.sections[0].paragraphs[0]"
    pv = _verdict_with_issue(
        "iss_z", QADimension.LOGIC_CONSISTENCY, "major", "reporter", location=loc
    )
    prior = [pv for _ in range(SAME_ISSUE_MAX_OCCURRENCES - 1)]  # 计 2 次
    prior_counts = count_prior_issue_occurrences(prior)
    issue = _mk_issue(
        "iss_z", QADimension.LOGIC_CONSISTENCY, "major", "reporter", location=loc
    )
    out = mark_unresolved_from_prior([issue], prior_counts)
    assert out[0].required_inputs.get("unresolved_from_prior_round") is None


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


# ---------- A1: 接 score 入判级 + 杀静默放行 ----------


def _mk_dim(dim: QADimension, score: float, pass_: bool) -> QADimensionResult:
    return QADimensionResult(
        dimension=dim, score=score, **{"pass": pass_}  # type: ignore[arg-type]
    )


def test_synthesize_surfaces_subthreshold_dims_without_issue() -> None:
    """低分但无 issue 的维度补发；通过的 / 已有 issue 的不补。"""
    dims = {
        QADimension.EVIDENCE_COMPLETENESS: _mk_dim(
            QADimension.EVIDENCE_COMPLETENESS, 0.4, False
        ),
        QADimension.FRESHNESS: _mk_dim(QADimension.FRESHNESS, 0.7, False),
        QADimension.FACT_CONSISTENCY: _mk_dim(
            QADimension.FACT_CONSISTENCY, 0.99, True
        ),
        QADimension.EXPRESSION: _mk_dim(QADimension.EXPRESSION, 0.3, False),
    }
    existing = [_mk_issue("iss_x", QADimension.EXPRESSION, "minor", "reporter")]
    synth = synthesize_threshold_issues(dims, existing)
    by_dim = {i.dimension: i for i in synth}

    assert QADimension.FACT_CONSISTENCY not in by_dim  # 通过 → 不补
    assert QADimension.EXPRESSION not in by_dim  # 已有 issue → 不补
    # core(evidence) → major/reporter；非 core(freshness) → minor/collector
    assert by_dim[QADimension.EVIDENCE_COMPLETENESS].severity == "major"
    assert by_dim[QADimension.EVIDENCE_COMPLETENESS].target_agent == "reporter"
    assert by_dim[QADimension.FRESHNESS].severity == "minor"
    assert by_dim[QADimension.FRESHNESS].target_agent == "collector"
    # 补发的 location 非空（过 _post_validate）
    assert all(i.location.strip() for i in synth)


def test_core_dimension_fail_forces_blocking() -> None:
    """核心维度(evidence)不及格 → 即便 issue 权重为 0 也强制 needs_revision + blocking。"""
    dims = {
        QADimension.EVIDENCE_COMPLETENESS: _mk_dim(
            QADimension.EVIDENCE_COMPLETENESS, 0.5, False
        )
    }
    res = aggregate_verdict([], prior_count=0, dimension_results=dims)
    assert res.status is QAStatus.NEEDS_REVISION
    assert res.blocking is True


def test_noncore_subthreshold_does_not_force_blocking() -> None:
    """非核心维度(freshness)低分不强制阻塞（只靠补发的 minor 浮出，不主导判级）。"""
    dims = {QADimension.FRESHNESS: _mk_dim(QADimension.FRESHNESS, 0.7, False)}
    res = aggregate_verdict([], prior_count=0, dimension_results=dims)
    assert res.blocking is False


def test_core_blocking_not_forced_after_max_retry() -> None:
    """已触顶（max_retry）时核心维度不及格也不再强制阻塞 → 触顶放行兜底。"""
    dims = {
        QADimension.EVIDENCE_COMPLETENESS: _mk_dim(
            QADimension.EVIDENCE_COMPLETENESS, 0.5, False
        )
    }
    res = aggregate_verdict(
        [], prior_count=MAX_RETRY_VERDICTS, dimension_results=dims
    )
    assert res.blocking is False


def test_schema_completeness_is_core_dimension() -> None:
    """schema_completeness 现纳入核心维（数据层可真修）→ 失败应能触发阻塞返工。"""
    from backend.agents.qa.routing import CORE_DIMENSIONS

    assert QADimension.SCHEMA_COMPLETENESS in CORE_DIMENSIONS


def test_schema_fail_forces_blocking_first_round() -> None:
    """schema 首次不及格（无历史）→ 强制 needs_revision + blocking（触发一轮返工）。"""
    dims = {
        QADimension.SCHEMA_COMPLETENESS: _mk_dim(
            QADimension.SCHEMA_COMPLETENESS, 0.65, False
        )
    }
    res = aggregate_verdict([], prior_count=0, dimension_results=dims)
    assert res.status is QAStatus.NEEDS_REVISION
    assert res.blocking is True


def _mk_prior_verdict(failing_dim: QADimension) -> QAVerdict:
    """构造一条历史 verdict：指定核心维度 pass_=False（用于复发护栏测试）。"""
    return QAVerdict(
        verdict_id=f"v_prior_{failing_dim.value}",
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={failing_dim: _mk_dim(failing_dim, 0.65, False)},
        issues=[],
        routing=[],
        blocking=True,
    )


def test_core_fail_not_reblocked_after_prior_failure() -> None:
    """复发护栏：核心维上一轮已失败、本轮仍失败 → 不再强制阻塞（避免空转）。

    给每个核心维度仅一次阻塞返工机会；返工没修好就落回权重判级，交 best-round。
    issue 权重为 0 → 落回非阻塞 needs_revision，不再回灌。
    """
    dims = {
        QADimension.SCHEMA_COMPLETENESS: _mk_dim(
            QADimension.SCHEMA_COMPLETENESS, 0.65, False
        )
    }
    prior = [_mk_prior_verdict(QADimension.SCHEMA_COMPLETENESS)]
    res = aggregate_verdict(
        [], prior_count=1, dimension_results=dims, prior_verdicts=prior
    )
    assert res.blocking is False


def test_core_fail_still_blocks_when_other_core_dim_newly_fails() -> None:
    """复发护栏是按维度独立的：schema 失败过被豁免，但 evidence 首次失败仍阻塞。"""
    dims = {
        QADimension.SCHEMA_COMPLETENESS: _mk_dim(
            QADimension.SCHEMA_COMPLETENESS, 0.65, False
        ),
        QADimension.EVIDENCE_COMPLETENESS: _mk_dim(
            QADimension.EVIDENCE_COMPLETENESS, 0.5, False
        ),
    }
    prior = [_mk_prior_verdict(QADimension.SCHEMA_COMPLETENESS)]
    res = aggregate_verdict(
        [], prior_count=1, dimension_results=dims, prior_verdicts=prior
    )
    assert res.blocking is True


# ---------- A: fact_consistency 不再 fail-open（LLM 不可用→未核验降级）----------


def test_fact_consistency_degraded_when_no_llm() -> None:
    """LLM=None（无 entailment 能力）→ 有证据段落记「未核验」，pass_=False + 非阻塞。

    回归 fail-open bug：旧逻辑把有 evidence 的段落乐观计 entailed=1.0 → 报满分 PASS，
    等于「没核验却报通过」。修复后该维度降级（不计满分、补发非阻塞 minor 暴露未核验）。
    """
    from backend.agents.qa.checkers import CheckerContext, FactConsistencyChecker

    inp = load_demo_input()
    ctx = CheckerContext(
        draft=inp.draft,
        analysis=inp.analysis,
        profiles=inp.profiles,
        evidence_db=load_evidence_db(),
        llm=None,
        prompt_dir=None,
    )
    res = FactConsistencyChecker().run(ctx)

    assert res.pass_ is False, "无 LLM 不应 fail-open 报通过"
    assert res.score < FactConsistencyChecker.OVERALL_PASS_THRESHOLD
    assert "未核验" in res.notes
    degraded = [i for i in res.issues if i.required_inputs.get("degraded")]
    assert degraded, "应补发 degraded 未核验 issue"
    assert all(i.severity == "minor" for i in degraded), "degraded issue 非阻塞"
    # degraded 单独喂判级不应阻塞（基础设施降级不卡口）
    assert aggregate_verdict(res.issues, prior_count=0).blocking is False


# ---------- B: 确定性硬伤（contradicted / 数字失配）→ 可阻塞 ----------


def test_fact_consistency_contradicted_marks_hard_block() -> None:
    """LLM 判某段 contradicted → 该 issue 标 hard_block=True 且 major。"""
    from backend.agents.qa.agent import PROMPT_DIR
    from backend.agents.qa.checkers import CheckerContext, FactConsistencyChecker
    from backend.agents.qa.checkers.fact_consistency import (
        _EntailmentResponse,
        _EntailmentVerdict,
    )

    inp = load_demo_input()
    # 找一个有 evidence 的事实段落（会被送进 entailment LLM）
    target_pid = None
    for s in inp.draft.sections:
        for p in s.paragraphs:
            if p.evidence_ids and not p.is_soft_conclusion and p.text.strip():
                target_pid = p.paragraph_id
                break
        if target_pid:
            break
    assert target_pid is not None

    resp = _EntailmentResponse(
        verdicts=[
            _EntailmentVerdict(
                paragraph_id=target_pid, label="contradicted", note="与证据冲突"
            )
        ]
    )
    llm = FakeLLM(responses={"entailment": resp})
    ctx = CheckerContext(
        draft=inp.draft,
        analysis=inp.analysis,
        profiles=inp.profiles,
        evidence_db=load_evidence_db(),
        llm=llm,
        prompt_dir=str(PROMPT_DIR),
    )
    res = FactConsistencyChecker().run(ctx)
    contra = [
        i for i in res.issues if i.issue_id == f"iss_fc_contra_{target_pid}"
    ]
    assert contra, "应为 contradicted 段落开 issue"
    assert contra[0].severity == "major"
    assert contra[0].required_inputs.get("hard_block") is True


def test_hard_block_issue_forces_blocking_even_non_core() -> None:
    """fact（非 core）的 hard_block issue → aggregate_verdict 仍强制阻塞返工。"""
    issue = QAIssue(
        issue_id="iss_fc_contra_x",
        dimension=QADimension.FACT_CONSISTENCY,
        severity="major",
        location="report.sections[0].paragraphs[0]",
        problem="x",
        suggested_fix="y",
        target_agent="reporter",
        required_inputs={"hard_block": True},
    )
    res = aggregate_verdict([issue], prior_count=0)
    assert res.blocking is True
    assert res.status is QAStatus.NEEDS_REVISION


def test_hard_block_released_after_downgrade_to_minor() -> None:
    """hard_block issue 被复发降级成 minor 后不再硬阻塞（防死循环）。"""
    issue = QAIssue(
        issue_id="iss_fc_contra_x",
        dimension=QADimension.FACT_CONSISTENCY,
        severity="minor",
        location="report.sections[0].paragraphs[0]",
        problem="x (已多次出现，自动降级)",
        suggested_fix="y",
        target_agent="reporter",
        required_inputs={"hard_block": True, "downgraded_due_to_recurrence": True},
    )
    res = aggregate_verdict([issue], prior_count=0)
    assert res.blocking is False, "降级成 minor 的 hard_block 不再阻塞（weight 1）"


def test_hard_block_not_forced_after_max_retry() -> None:
    """触顶后即便有 hard_block 也不再强制阻塞 → 触顶放行兜底。"""
    issue = QAIssue(
        issue_id="iss_fc_contra_x",
        dimension=QADimension.FACT_CONSISTENCY,
        severity="major",
        location="report.sections[0].paragraphs[0]",
        problem="x",
        suggested_fix="y",
        target_agent="reporter",
        required_inputs={"hard_block": True},
    )
    res = aggregate_verdict([issue], prior_count=MAX_RETRY_VERDICTS)
    assert res.blocking is False


# ---------- C: QA 消费 source_authority（evidence_completeness 非阻塞提示）----------


def _ec_checker():
    from backend.agents.qa.checkers import (
        CheckerContext,
        EvidenceCompletenessChecker,
    )

    return CheckerContext, EvidenceCompletenessChecker


def test_low_authority_does_not_flip_evidence_pass() -> None:
    """权威 issue 在 pass_ 之后 append——压低全部证据 authority 不改本维度 score/pass_，
    只额外浮出权威 issue（消费 source_authority，但不经 core 路径强制阻塞）。"""
    CheckerContext, EvidenceCompletenessChecker = _ec_checker()
    inp = load_demo_input()
    db = load_evidence_db()
    base = EvidenceCompletenessChecker().run(
        CheckerContext(
            draft=inp.draft, analysis=inp.analysis, profiles=inp.profiles,
            evidence_db=db,
        )
    )
    low_db = {
        eid: Evidence.model_validate(
            {**e.model_dump(mode="json"), "source_authority": 0.5}
        )
        for eid, e in db.items()
    }
    low = EvidenceCompletenessChecker().run(
        CheckerContext(
            draft=inp.draft, analysis=inp.analysis, profiles=inp.profiles,
            evidence_db=low_db,
        )
    )
    # 权威 issue 不翻转本维度 pass_/score
    assert low.pass_ == base.pass_
    assert low.score == base.score
    # 但确实浮出了权威 issue（消费了 source_authority）
    auth = [
        i
        for i in low.issues
        if i.issue_id in ("iss_ec_low_authority", "iss_ec_low_authority_key")
    ]
    assert auth, "压低 authority 后应浮出权威 issue"
    assert all(i.target_agent == "collector" for i in auth)


def test_cross_dimension_authority_correction_flags_review_in_pricing() -> None:
    """评论类证据(存值 0.92)用到**定价**段落 → 跨维度重算为 0.6(<0.7) → 标关键弱源。
    证明 QA 是按「段落主题维度」重算，而非沿用证据采集时的 source_authority。"""
    CheckerContext, EvidenceCompletenessChecker = _ec_checker()
    inp = load_demo_input()
    db = load_evidence_db()
    sec = next(s for s in inp.draft.sections if "pricing" in s.section_id.lower())
    para = next(
        p
        for p in sec.paragraphs
        if p.is_quantitative and not p.is_soft_conclusion and p.evidence_ids
    )
    mod_db = dict(db)
    for eid in para.evidence_ids:
        if eid in db:
            mod_db[eid] = Evidence.model_validate(
                {
                    **db[eid].model_dump(mode="json"),
                    "source_class": "review",
                    "source_authority": 0.92,  # 采集时(口碑维度)的高存值
                }
            )
    res = EvidenceCompletenessChecker().run(
        CheckerContext(
            draft=inp.draft, analysis=inp.analysis, profiles=inp.profiles,
            evidence_db=mod_db,
        )
    )
    key = [i for i in res.issues if i.issue_id == "iss_ec_low_authority_key"]
    assert key, "评论证据(0.92)用于定价 → 重算 0.6 → 应标关键弱源（跨维度校正生效）"
    assert key[0].severity == "major"
    assert key[0].target_agent == "collector"
    assert para.paragraph_id in key[0].required_inputs["paragraph_ids"]


def test_multi_source_class_corroboration_exempts() -> None:
    """同段落证据来自 ≥2 个不同 source_class（多源互证）→ 即便都低权威也豁免。"""
    CheckerContext, EvidenceCompletenessChecker = _ec_checker()
    inp = load_demo_input()
    db = load_evidence_db()
    sec = next(s for s in inp.draft.sections if "pricing" in s.section_id.lower())
    # 注入一个引用 2 条不同 source_class 证据的定价量化段
    eids = [e for e in db][:2]
    assert len(eids) == 2
    mod_db = dict(db)
    mod_db[eids[0]] = Evidence.model_validate(
        {**db[eids[0]].model_dump(mode="json"), "source_class": "review", "source_authority": 0.92}
    )
    mod_db[eids[1]] = Evidence.model_validate(
        {**db[eids[1]].model_dump(mode="json"), "source_class": "other", "source_authority": 0.5}
    )
    inp.draft = inp.draft.model_copy(deep=True)
    sec2 = next(s for s in inp.draft.sections if "pricing" in s.section_id.lower())
    sec2.paragraphs.append(
        ReportParagraph(
            paragraph_id="p_pr_corro",
            text="Notion Plus $10/seat。",
            claim_ids=[],
            evidence_ids=[eids[0], eids[1]],
            is_quantitative=True,
            is_soft_conclusion=False,
        )
    )
    res = EvidenceCompletenessChecker().run(
        CheckerContext(
            draft=inp.draft, analysis=inp.analysis, profiles=inp.profiles,
            evidence_db=mod_db,
        )
    )
    flagged = [
        pid
        for i in res.issues
        if i.issue_id in ("iss_ec_low_authority", "iss_ec_low_authority_key")
        for pid in i.required_inputs.get("paragraph_ids", [])
    ]
    assert "p_pr_corro" not in flagged, "多源互证段落不应被标弱源"


def test_low_authority_quant_is_non_blocking() -> None:
    """单条 low-authority minor 提示不应触发返工（非阻塞）。"""
    issue = QAIssue(
        issue_id="iss_ec_low_authority",
        dimension=QADimension.EVIDENCE_COMPLETENESS,
        severity="minor",
        location="report.dimension[evidence_completeness]",
        problem="x",
        suggested_fix="y",
        target_agent="collector",
        required_inputs={},
    )
    assert aggregate_verdict([issue], prior_count=0).blocking is False


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
