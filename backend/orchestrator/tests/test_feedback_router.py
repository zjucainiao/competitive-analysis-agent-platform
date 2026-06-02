"""FeedbackRouter 单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.orchestrator.feedback_router import (
    FeedbackOutcome,
    FeedbackRouter,
)
from backend.orchestrator.planner import Planner
from backend.schemas import (
    DAGPlan,
    NodeStatus,
    NodeType,
    Project,
    QADimension,
    QAFeedback,
    QAIssue,
    QARouting,
    QAStatus,
    QAVerdict,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PROJECT_FILE = (
    _REPO_ROOT / "fixtures" / "mock_data" / "projects" / "collab_saas_demo.json"
)


# ---------- fixtures ----------


def _make_project() -> Project:
    data = json.loads(_DEMO_PROJECT_FILE.read_text(encoding="utf-8"))
    return Project.model_validate(data)


def _make_plan(project: Project | None = None) -> DAGPlan:
    return Planner().plan(project or _make_project())


def _make_verdict(
    *,
    routings: list[QARouting] | None = None,
    issues: list[QAIssue] | None = None,
    blocking: bool = True,
) -> QAVerdict:
    return QAVerdict(
        verdict_id="vd_1",
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={},
        issues=issues or [],
        routing=routings or [],
        blocking=blocking,
    )


def _issue(
    *,
    target: str,
    dimension: QADimension = QADimension.FACT_CONSISTENCY,
    issue_id: str = "iss_1",
    severity: str = "major",
) -> QAIssue:
    return QAIssue(
        issue_id=issue_id,
        dimension=dimension,
        severity=severity,  # type: ignore[arg-type]
        location="report.sections[0].paragraphs[0]",
        problem="placeholder problem",
        suggested_fix="placeholder fix",
        target_agent=target,  # type: ignore[arg-type]
    )


# ---------- abort paths ----------


def test_aborts_when_at_max_rounds() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    router = FeedbackRouter(max_rounds=3)
    outcome = router.apply(verdict=verdict, plan=plan, qa_round_count=3)
    assert outcome.aborted is True
    assert "qa_round_count=3" in outcome.abort_reason
    assert outcome.new_nodes == []


def test_aborts_when_no_routing_entries() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)
    assert outcome.aborted is True
    assert "no routing entries" in outcome.abort_reason


def test_aborts_when_no_targets_match() -> None:
    """routing 指向不存在的 agent → 无匹配 → aborted。"""
    plan = _make_plan()
    # 用一个 plan 里没有节点的合法值（"collector" 有节点；尝试 plan 删 collector 模拟）
    # 简化：plan 拿 analyst 单一节点版本，再 routing 到 reporter——还在；那构造一个空 plan
    empty_plan = DAGPlan(
        plan_id="plan_empty",
        project_id="p1",
        template_id=None,
        nodes=[],
        edges=[],
        rationale="empty",
        confidence=1.0,
        complexity_score=0.0,
    )
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=empty_plan, qa_round_count=0)
    assert outcome.aborted is True
    assert "no nodes matched" in outcome.abort_reason


def test_max_rounds_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FeedbackRouter(max_rounds=0)


# ---------- analyst rework ----------


def test_analyst_rework_creates_v2_node() -> None:
    plan = _make_plan()
    verdict = _make_verdict(
        routings=[QARouting(target_agent="analyst", reason="missing pricing evidence")],
        issues=[_issue(target="analyst")],
    )
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    assert outcome.aborted is False
    assert len(outcome.new_nodes) == 1
    new = outcome.new_nodes[0]
    assert new.node_id == "analyst_v2"
    assert new.agent_name == "analyst"
    assert new.revision == 2
    assert new.parent_node_id == "analyst"
    assert new.status == NodeStatus.PENDING
    assert new.input_refs == ["join_extract"]
    # qa_feedback payload bound to new node
    assert "analyst_v2" in outcome.qa_feedback_by_node
    fb = outcome.qa_feedback_by_node["analyst_v2"]
    assert fb["from_verdict_id"] == "vd_1"
    assert fb["instructions"] == "missing pricing evidence"
    assert fb["must_address"] == ["iss_1"]


def test_analyst_rework_redirects_reporter_input_refs() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    # reporter 的 input_refs 由 ["analyst"] 替换为 ["analyst_v2"]
    assert outcome.node_input_refs_updates["reporter"] == ["analyst_v2"]


def test_analyst_rework_resets_all_downstream_status() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    expected_reset = {"reporter", "qa", "end"}
    assert set(outcome.node_status_resets) == expected_reset
    for nid, status in outcome.node_status_resets.items():
        assert status == NodeStatus.PENDING


def test_analyst_rework_creates_correct_edges() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    edge_pairs = {(e.from_node, e.to_node) for e in outcome.new_edges}
    # 上游 → new
    assert ("join_extract", "analyst_v2") in edge_pairs
    # new → 直接下游
    assert ("analyst_v2", "reporter") in edge_pairs
    # 所有新边都是 feedback 类型
    for e in outcome.new_edges:
        assert e.edge_type == "feedback"


# ---------- collector rework (per-product) ----------


def test_collector_rework_creates_versioned_per_product() -> None:
    plan = _make_plan()
    verdict = _make_verdict(
        routings=[QARouting(target_agent="collector", reason="paywall blocked")]
    )
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    new_ids = {n.node_id for n in outcome.new_nodes}
    assert new_ids == {
        "collect.asana_v2",
        "collect.clickup_v2",
        "collect.notion_v2",
    }
    for n in outcome.new_nodes:
        assert n.revision == 2
        assert n.input_refs == ["start"]
        assert n.agent_name == "collector"
        # metadata 应该带 qa_feedback_round
        assert n.metadata["qa_feedback_round"] == 1
        # product 字段透传
        assert n.metadata["product"] in {"Notion", "ClickUp", "Asana"}


def test_collector_rework_redirects_extractor_input_refs() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="collector", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    assert outcome.node_input_refs_updates["extract.notion"] == ["collect.notion_v2"]
    assert outcome.node_input_refs_updates["extract.clickup"] == ["collect.clickup_v2"]
    assert outcome.node_input_refs_updates["extract.asana"] == ["collect.asana_v2"]


def test_collector_rework_resets_full_downstream() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="collector", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    # collect → extract → join → analyst → reporter → qa → end 全部应该回 PENDING
    expected = {
        "extract.asana",
        "extract.clickup",
        "extract.notion",
        "join_extract",
        "analyst",
        "reporter",
        "qa",
        "end",
    }
    assert set(outcome.node_status_resets) == expected


# ---------- 多轮版本递增 ----------


def test_double_apply_increments_to_v3() -> None:
    """analyst → analyst_v2，再 apply 一次 → analyst_v3。"""
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r1")])

    router = FeedbackRouter()
    outcome1 = router.apply(verdict=verdict, plan=plan, qa_round_count=0)
    assert outcome1.new_nodes[0].node_id == "analyst_v2"

    # 把 v2 节点加入 plan 模拟第一轮已落地
    plan2 = plan.model_copy(
        update={
            "nodes": plan.nodes + outcome1.new_nodes,
            "edges": plan.edges + outcome1.new_edges,
        }
    )
    verdict2 = _make_verdict(
        routings=[QARouting(target_agent="analyst", reason="r2")]
    )
    outcome2 = router.apply(verdict=verdict2, plan=plan2, qa_round_count=1)
    assert outcome2.new_nodes[0].node_id == "analyst_v3"
    assert outcome2.new_nodes[0].revision == 3
    assert outcome2.new_nodes[0].parent_node_id == "analyst_v2"


# ---------- qa_feedback payload ----------


def test_qa_feedback_payload_carries_only_target_issues() -> None:
    plan = _make_plan()
    issues = [
        _issue(target="analyst", issue_id="iss_a"),
        _issue(target="reporter", issue_id="iss_r"),
    ]
    verdict = _make_verdict(
        routings=[QARouting(target_agent="analyst", reason="r")],
        issues=issues,
    )
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)
    payload = outcome.qa_feedback_by_node["analyst_v2"]
    # 只把 analyst 相关的 issue 给 analyst_v2
    assert [i["issue_id"] for i in payload["issues"]] == ["iss_a"]
    assert payload["must_address"] == ["iss_a"]


def test_qa_feedback_must_address_overridable_via_payload() -> None:
    plan = _make_plan()
    verdict = _make_verdict(
        routings=[
            QARouting(
                target_agent="analyst",
                reason="r",
                payload={"must_address": ["iss_X"]},
            )
        ],
        issues=[_issue(target="analyst", issue_id="iss_a")],
    )
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)
    payload = outcome.qa_feedback_by_node["analyst_v2"]
    assert payload["must_address"] == ["iss_X"]


def test_qa_feedback_payload_validates_as_qafeedback() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)
    payload = outcome.qa_feedback_by_node["analyst_v2"]
    # payload 含 revision 字段（Reporter 专用，Agent 之间约定）
    assert payload["revision"] == 1
    # 把 revision 摘掉后必须能反序列化回 QAFeedback
    qaf_payload = {k: v for k, v in payload.items() if k != "revision"}
    rebuilt = QAFeedback.model_validate(qaf_payload)
    assert rebuilt.from_verdict_id == "vd_1"


def test_qa_feedback_payload_revision_increments_with_round() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=1)
    payload = outcome.qa_feedback_by_node["analyst_v2"]
    # qa_round = qa_round_count + 1 = 2
    assert payload["revision"] == 2


# ---------- 控制节点不应被作为 rework 目标 ----------


def test_control_nodes_never_versioned() -> None:
    plan = _make_plan()
    verdict = _make_verdict(routings=[QARouting(target_agent="analyst", reason="r")])
    outcome = FeedbackRouter().apply(verdict=verdict, plan=plan, qa_round_count=0)

    for n in outcome.new_nodes:
        assert n.node_type == NodeType.AGENT_CALL
