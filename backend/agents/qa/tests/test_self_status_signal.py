"""⑤ 把上游 agent 自评(needs_rework)接入 QA 判级。

escalate_by_self_status：自评 needs_rework 的 agent 名下的 minor issue 升级为 major，
但不凭空造 issue（避免「一自评就强制返工」的失控循环）。
"""
from __future__ import annotations

from backend.agents.qa.routing import escalate_by_self_status
from backend.schemas import QADimension, QAIssue


def _issue(target: str, severity: str, iid: str = "iss_x") -> QAIssue:
    return QAIssue(
        issue_id=iid,
        dimension=QADimension.SCHEMA_COMPLETENESS,
        severity=severity,  # type: ignore[arg-type]
        location="profile.x",
        problem="p",
        suggested_fix="f",
        target_agent=target,  # type: ignore[arg-type]
    )


def test_minor_escalated_when_agent_self_flagged() -> None:
    issues = [_issue("extractor", "minor")]
    out = escalate_by_self_status(issues, {"extractor": "needs_rework"})
    assert out[0].severity == "major"
    assert out[0].required_inputs.get("escalated_by_self_status") is True


def test_no_escalation_without_self_flag() -> None:
    issues = [_issue("extractor", "minor")]
    out = escalate_by_self_status(issues, {"extractor": "success"})
    assert out[0].severity == "minor"


def test_does_not_fabricate_issues_for_flagged_agent() -> None:
    """自评 needs_rework 但 QA 没为它开 issue → 不凭空造（不放大）。"""
    issues = [_issue("reporter", "minor")]
    out = escalate_by_self_status(issues, {"collector": "needs_rework"})
    assert len(out) == 1
    assert out[0].severity == "minor"  # reporter 的不受 collector 自评影响


def test_major_left_unchanged() -> None:
    issues = [_issue("collector", "major")]
    out = escalate_by_self_status(issues, {"collector": "needs_rework"})
    assert out[0].severity == "major"  # 已是 major，幂等不变


def test_build_qa_input_aggregates_worst_upstream_status() -> None:
    from types import SimpleNamespace

    from backend.orchestrator.inputs import _upstream_statuses

    def out(status: str):
        return SimpleNamespace(status=SimpleNamespace(value=status))

    outputs = {
        "collect.A": out("success"),
        "collect.B": out("needs_rework"),  # 取最差 → collector=needs_rework
        "extract.A": out("success"),
        "analyst": out("success"),  # 非 collect/extract，忽略
    }
    agg = _upstream_statuses(outputs)
    assert agg["collector"] == "needs_rework"
    assert agg["extractor"] == "success"
    assert "analyst" not in agg
