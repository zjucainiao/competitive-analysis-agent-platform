"""ProjectMetrics 计算器单测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.orchestrator.metrics import (
    best_round_reporter_key,
    compute_project_metrics,
)
from backend.schemas import (
    AgentOutputBase,
    AgentStatus,
    AnalysisResult,
    AnalystOutput,
    CollectorOutput,
    DAGEdge,
    DAGNode,
    DAGPlan,
    Evidence,
    ExtractorOutput,
    NodeStatus,
    NodeType,
    PricingProfile,
    ProductBasicInfo,
    QADimension,
    QADimensionResult,
    QAStatus,
    QAVerdict,
    RawSourceDoc,
)
from backend.schemas.competitor import CompetitorProfile, PricingModel
from backend.schemas.evidence import CollectDimension

_T0 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)


def _node(node_id: str, *, agent: str | None, start_offset: int, duration: int) -> DAGNode:
    return DAGNode(
        node_id=node_id,
        project_id="p1",
        node_type=NodeType.AGENT_CALL if agent else NodeType.START,
        agent_name=agent,
        status=NodeStatus.SUCCESS,
        started_at=_T0 + timedelta(seconds=start_offset),
        ended_at=_T0 + timedelta(seconds=start_offset + duration),
    )


def _plan() -> DAGPlan:
    return DAGPlan(
        plan_id="plan_x",
        project_id="p1",
        template_id=None,
        nodes=[
            _node("start", agent=None, start_offset=0, duration=0),
            _node("collect.notion", agent="collector", start_offset=1, duration=60),
            _node("extract.notion", agent="extractor", start_offset=62, duration=90),
            _node("analyst", agent="analyst", start_offset=155, duration=30),
        ],
        edges=[
            DAGEdge(edge_id="e1", from_node="start", to_node="collect.notion"),
            DAGEdge(edge_id="e2", from_node="collect.notion", to_node="extract.notion"),
            DAGEdge(edge_id="e3", from_node="extract.notion", to_node="analyst"),
        ],
    )


def _collector_output(*, real_urls: int, mock_urls: int) -> CollectorOutput:
    sources: list[RawSourceDoc] = []
    for i in range(real_urls):
        sources.append(
            RawSourceDoc(
                source_id=f"src_real_{i}",
                product_name="Notion",
                source_url=f"https://example.com/p{i}",  # type: ignore[arg-type]
                source_type="html",
                dimension=CollectDimension.HOMEPAGE,
                raw_text="x" * 200,
                title="t",
                collected_at=_T0,
                fetch_method="firecrawl",
                robots_allowed=True,
            )
        )
    for i in range(mock_urls):
        sources.append(
            RawSourceDoc(
                source_id=f"src_mock_{i}",
                product_name="Notion",
                source_url=f"https://example.com/mock_{i}",  # type: ignore[arg-type]
                source_type="html",
                dimension=CollectDimension.HOMEPAGE,
                raw_text="x" * 200,
                title="t",
                collected_at=_T0,
                fetch_method="mock",
                robots_allowed=True,
            )
        )
    return CollectorOutput(
        agent_name="collector",
        agent_version="1.0.0",
        task_id="collect.notion",
        trace_id="t",
        span_id="s",
        status=AgentStatus.SUCCESS,
        confidence=0.9,
        self_critique="",
        tokens_input=100,
        tokens_output=80,
        cost_usd=0.005,
        raw_sources=sources,
        coverage_by_dimension={CollectDimension.HOMEPAGE: real_urls + mock_urls},
    )


def _extractor_output(evidence_n: int) -> ExtractorOutput:
    evs = [
        Evidence(
            evidence_id=f"ev_{i}",
            source_id="src_real_0",
            product_name="Notion",
            source_url="https://example.com/p0",  # type: ignore[arg-type]
            source_type="homepage",
            source_authority=0.9,
            content="some evidence text " + str(i),
            content_hash=f"h{i}",
            collected_at=_T0,
            extracted_at=_T0,
            confidence=0.9,
        )
        for i in range(evidence_n)
    ]
    profile = CompetitorProfile(
        profile_id="pf",
        schema_version="1.1.0",
        industry="collaboration_saas",
        basic_info=ProductBasicInfo(name="Notion", category="协作"),
        pricing=PricingProfile(pricing_model=PricingModel.FREEMIUM),
        extracted_at=_T0,
    )
    return ExtractorOutput(
        agent_name="extractor",
        agent_version="1.0.0",
        task_id="extract.notion",
        trace_id="t",
        span_id="s",
        status=AgentStatus.SUCCESS,
        confidence=0.85,
        self_critique="",
        tokens_input=500,
        tokens_output=300,
        cost_usd=0.02,
        profile=profile,
        evidences=evs,
        schema_version="1.1.0",
    )


def _analyst_output() -> AnalystOutput:
    return AnalystOutput(
        agent_name="analyst",
        agent_version="1.0.0",
        task_id="analyst",
        trace_id="t",
        span_id="s",
        status=AgentStatus.SUCCESS,
        confidence=0.88,
        self_critique="",
        tokens_input=800,
        tokens_output=400,
        cost_usd=0.03,
        result=AnalysisResult(target_product="Notion", competitors=["Asana"], dimensions={}),
    )


def _verdict(scores: dict[QADimension, float]) -> QAVerdict:
    return QAVerdict(
        verdict_id="vd_1",
        overall_status=QAStatus.PASS,
        dimension_results={
            dim: QADimensionResult(
                dimension=dim,
                score=score,
                **{"pass": score >= 0.7},  # type: ignore[arg-type]
                notes="",
            )
            for dim, score in scores.items()
        },
        issues=[],
        routing=[],
        blocking=False,
    )


# ---------- 单测 ----------


def test_empty_inputs_zero_metrics() -> None:
    m = compute_project_metrics(
        plan=DAGPlan(plan_id="p", project_id="p1", template_id=None, nodes=[], edges=[]),
        outputs={},
        verdicts=[],
        qa_round_count=0,
    )
    assert m.accuracy == 0.0
    assert m.coverage == 0.0
    assert m.total_tokens == 0
    assert m.evidence_count == 0
    assert m.duration_seconds == 0
    assert m.qa_round_count == 0


def test_full_run_metrics() -> None:
    plan = _plan()
    outputs: dict[str, AgentOutputBase] = {
        "collect.notion": _collector_output(real_urls=3, mock_urls=1),
        "extract.notion": _extractor_output(evidence_n=10),
        "analyst": _analyst_output(),
    }
    verdicts = [
        _verdict(
            {
                QADimension.SCHEMA_COMPLETENESS: 0.85,
                QADimension.FACT_CONSISTENCY: 0.9,
                QADimension.EVIDENCE_COMPLETENESS: 1.0,
                QADimension.EXPRESSION: 0.95,
            }
        )
    ]

    m = compute_project_metrics(
        plan=plan, outputs=outputs, verdicts=verdicts, qa_round_count=2,
    )

    # 时间：从 t+0 (start) 到 t+185 (analyst 结束) = 185s
    assert m.duration_seconds == 185

    # token: 100+80 + 500+300 + 800+400 = 2180
    assert m.total_tokens == 2180

    # cost: 0.005 + 0.02 + 0.03 = 0.055
    assert m.total_cost_usd == pytest.approx(0.055)

    # evidence
    assert m.evidence_count == 10

    # 来自 schema_completeness 维度
    assert m.coverage == pytest.approx(0.85)
    assert m.fields_filled_ratio == pytest.approx(0.85)

    # accuracy: (0.85 + 0.9 + 1.0 + 0.95) / 4 = 0.925
    assert m.accuracy == pytest.approx(0.925)

    # fetch counts: 3 real + 1 mock
    assert m.real_fetch_count == 3
    assert m.mock_fetch_count == 1

    # qa rounds
    assert m.qa_round_count == 2

    # edit_rate 暂时 0
    assert m.edit_rate == 0.0


def test_metrics_handles_verdict_without_schema_dim() -> None:
    """如果 verdict 没 schema_completeness，coverage 应回退到 0。"""
    plan = _plan()
    m = compute_project_metrics(
        plan=plan, outputs={},
        verdicts=[_verdict({QADimension.FRESHNESS: 0.7})],
        qa_round_count=0,
    )
    assert m.coverage == 0.0
    assert m.accuracy == pytest.approx(0.7)


def test_metrics_uses_last_verdict() -> None:
    """多份 verdict 时 coverage/accuracy 仍用最后一份（语义不变，best 另存字段）。"""
    plan = _plan()
    verdicts = [
        _verdict({QADimension.SCHEMA_COMPLETENESS: 0.3}),  # 旧的
        _verdict({QADimension.SCHEMA_COMPLETENESS: 0.9}),  # 最新
    ]
    m = compute_project_metrics(plan=plan, outputs={}, verdicts=verdicts, qa_round_count=1)
    assert m.coverage == pytest.approx(0.9)


# ---------- A4: 跨轮 delta + best-round ----------


def test_per_round_series_and_delta_positive() -> None:
    """round2 比 round1 高 → delta 为正、best_round=2。"""
    plan = _plan()
    verdicts = [
        _verdict({QADimension.FACT_CONSISTENCY: 0.6, QADimension.EXPRESSION: 0.6}),  # 0.6
        _verdict({QADimension.FACT_CONSISTENCY: 0.8, QADimension.EXPRESSION: 1.0}),  # 0.9
    ]
    m = compute_project_metrics(plan=plan, outputs={}, verdicts=verdicts, qa_round_count=1)
    assert m.per_round_accuracy == [pytest.approx(0.6), pytest.approx(0.9)]
    assert m.round_delta == [pytest.approx(0.3)]
    assert m.best_round == 2


def test_best_round_picks_earlier_when_rework_worsens() -> None:
    """返工把质量改差(round2<round1) → best_round=1、delta 为负。"""
    plan = _plan()
    verdicts = [
        _verdict({QADimension.FACT_CONSISTENCY: 0.9, QADimension.EXPRESSION: 0.9}),  # 0.9
        _verdict({QADimension.FACT_CONSISTENCY: 0.5, QADimension.EXPRESSION: 0.5}),  # 0.5
    ]
    m = compute_project_metrics(plan=plan, outputs={}, verdicts=verdicts, qa_round_count=1)
    assert m.round_delta == [pytest.approx(-0.4)]
    assert m.best_round == 1


def test_best_round_tie_prefers_latest() -> None:
    """两轮并列(无改善) → best_round 取较晚轮，退化为最后一轮行为。"""
    plan = _plan()
    verdicts = [
        _verdict({QADimension.FACT_CONSISTENCY: 0.866}),
        _verdict({QADimension.FACT_CONSISTENCY: 0.866}),
    ]
    m = compute_project_metrics(plan=plan, outputs={}, verdicts=verdicts, qa_round_count=1)
    assert m.best_round == 2


def test_best_round_reporter_key_selects_best_scoring_output() -> None:
    outputs = {"reporter": object(), "reporter_v2": object()}
    # round1 高 → 发 round1 的 "reporter"
    better_first = [
        _verdict({QADimension.FACT_CONSISTENCY: 0.9}),
        _verdict({QADimension.FACT_CONSISTENCY: 0.5}),
    ]
    assert best_round_reporter_key(outputs, better_first) == "reporter"
    # round2 高 → 发 "reporter_v2"
    better_second = [
        _verdict({QADimension.FACT_CONSISTENCY: 0.5}),
        _verdict({QADimension.FACT_CONSISTENCY: 0.9}),
    ]
    assert best_round_reporter_key(outputs, better_second) == "reporter_v2"
    # 无 verdict → 退回最高 revision
    assert best_round_reporter_key(outputs, []) == "reporter_v2"
    # 平局 → 取较晚轮
    tie = [
        _verdict({QADimension.FACT_CONSISTENCY: 0.8}),
        _verdict({QADimension.FACT_CONSISTENCY: 0.8}),
    ]
    assert best_round_reporter_key(outputs, tie) == "reporter_v2"


def test_best_round_requires_ascending_order_storage_is_desc() -> None:
    """P1P2-VERDICT-ORDER：storage 按 DESC 返回；直接喂会选错，reversed 回升序才对。

    round1 维度均分更高(返工把质量改差) → 正确应发 round1 的 "reporter"。
    """
    outputs = {"reporter": object(), "reporter_v2": object()}
    asc = [  # 真实轮次升序：round1 高、round2 低
        _verdict({QADimension.FACT_CONSISTENCY: 0.9}),
        _verdict({QADimension.FACT_CONSISTENCY: 0.5}),
    ]
    desc = list(reversed(asc))  # storage list_qa_verdicts 的实际返回顺序(最新在前)
    # 直接喂 DESC（修复前的 bug）→ 把最新轮误当 round1 → 选错
    assert best_round_reporter_key(outputs, desc) == "reporter_v2"
    # 消费端 reversed 回升序（修复后）→ 选对（round1 更优）
    assert best_round_reporter_key(outputs, list(reversed(desc))) == "reporter"
