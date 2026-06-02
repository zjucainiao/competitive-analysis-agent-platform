"""QA 路由 / 整体判定 / 防死循环。

模块职责：把 6 个 checker 各自产出的 issues 聚合成：
- 按 target_agent 装配的 QARouting 列表
- 整体 overall_status / blocking
- 防死循环：根据 prior_verdicts 中的累计次数降级反复出现的 issue
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.schemas import (
    AgentError,
    QAIssue,
    QARouting,
    QAStatus,
    QAVerdict,
)

from .checkers import issue_dedupe_key

SEVERITY_WEIGHTS = {"minor": 1, "major": 5, "critical": 20}

# 防死循环阈值
SAME_ISSUE_MAX_OCCURRENCES = 3   # 同 issue ≥ 3 次 → 降级 minor + non-blocking
MAX_RETRY_VERDICTS = 5           # prior_verdicts 累计 ≥ 5 次 → 强制放行


# ----- 整体判定 -----


@dataclass
class OverallVerdict:
    status: QAStatus
    blocking: bool
    confidence: float
    total_weight: int
    max_retry_reached: bool


def aggregate_verdict(issues: list[QAIssue], prior_count: int) -> OverallVerdict:
    """根据 issues + 历史 verdict 数计算整体状态。"""
    crit = sum(1 for i in issues if i.severity == "critical")
    total_weight = sum(SEVERITY_WEIGHTS[i.severity] for i in issues)

    max_retry_reached = prior_count >= MAX_RETRY_VERDICTS

    if max_retry_reached:
        # 强制放行：blocking=False，但 status 仍反映客观
        status = QAStatus.PASS if total_weight == 0 else QAStatus.NEEDS_REVISION
        confidence = 0.55 if total_weight else 0.7
        return OverallVerdict(
            status=status,
            blocking=False,
            confidence=confidence,
            total_weight=total_weight,
            max_retry_reached=True,
        )

    if total_weight == 0:
        return OverallVerdict(
            status=QAStatus.PASS,
            blocking=False,
            confidence=0.9,
            total_weight=0,
            max_retry_reached=False,
        )
    if crit >= 2 or total_weight > 25:
        return OverallVerdict(
            status=QAStatus.REJECT,
            blocking=True,
            confidence=0.4,
            total_weight=total_weight,
            max_retry_reached=False,
        )
    if total_weight > 10:
        return OverallVerdict(
            status=QAStatus.NEEDS_REVISION,
            blocking=True,
            confidence=0.6,
            total_weight=total_weight,
            max_retry_reached=False,
        )
    return OverallVerdict(
        status=QAStatus.NEEDS_REVISION,
        blocking=False,
        confidence=0.75,
        total_weight=total_weight,
        max_retry_reached=False,
    )


# ----- 防死循环：基于 prior_verdicts 的 issue 频次降级 -----


def count_prior_issue_occurrences(
    prior_verdicts: list[QAVerdict],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in prior_verdicts:
        for issue in v.issues:
            key = issue_dedupe_key(issue.dimension, issue.location)
            counts[key] = counts.get(key, 0) + 1
    return counts


def downgrade_repeated_issues(
    issues: list[QAIssue], prior_counts: dict[str, int]
) -> tuple[list[QAIssue], list[QAIssue]]:
    """把反复出现的 issue 降级到 minor。

    返回 (new_issues, downgraded_issues)。
    downgraded_issues 仅供 trace / self_critique 报告用。
    """
    downgraded: list[QAIssue] = []
    out: list[QAIssue] = []
    for issue in issues:
        key = issue_dedupe_key(issue.dimension, issue.location)
        prior = prior_counts.get(key, 0)
        # +1 因为算上当前这次出现
        if prior + 1 >= SAME_ISSUE_MAX_OCCURRENCES and issue.severity != "minor":
            new_required = dict(issue.required_inputs)
            new_required["downgraded_due_to_recurrence"] = True
            new_required["prior_occurrences"] = prior
            new_issue = QAIssue(
                issue_id=issue.issue_id,
                dimension=issue.dimension,
                severity="minor",
                location=issue.location,
                problem=issue.problem + " (已多次出现，自动降级以避免死循环)",
                suggested_fix=issue.suggested_fix,
                target_agent=issue.target_agent,
                required_inputs=new_required,
            )
            downgraded.append(new_issue)
            out.append(new_issue)
        else:
            out.append(issue)
    return out, downgraded


# ----- routing 装配 -----


_TARGET_PRIORITY: dict[str, int] = {
    "collector": 0,
    "extractor": 1,
    "analyst": 2,
    "reporter": 3,
}


def build_routing(
    issues: list[QAIssue], blocking: bool
) -> list[QARouting]:
    """按 target_agent 聚合 issues 装配 QARouting。

    blocking=False 时仍返回 routing（让 Orchestrator 可选择性地走 revision），
    但 instructions 中标注 'non-blocking'。
    """
    if not issues:
        return []
    by_target: dict[str, list[QAIssue]] = {}
    for issue in issues:
        by_target.setdefault(issue.target_agent, []).append(issue)

    out: list[QARouting] = []
    for target in sorted(by_target, key=lambda t: _TARGET_PRIORITY.get(t, 99)):
        group = by_target[target]
        sev_counts = _severity_counts(group)
        reason_bits = [f"{n} 处 {sev}" for sev, n in sev_counts.items() if n]
        prefix = "(non-blocking) " if not blocking else ""
        out.append(
            QARouting(
                target_agent=target,  # type: ignore[arg-type]
                reason=(prefix + "；".join(reason_bits)) or prefix + "无显著问题",
                payload={
                    "must_address": sorted({i.issue_id for i in group}),
                    "instructions": _instructions_for(target, group),
                    "issues": [_issue_brief(i) for i in group],
                },
            )
        )
    return out


def _instructions_for(target: str, issues: list[QAIssue]) -> str:
    """生成 target 视角的 actionable 指令文本。"""
    dims = sorted({i.dimension.value for i in issues})
    head = {
        "collector": "Collector 重新采集相关维度的来源文档：",
        "extractor": "Extractor 重新抽取以补齐字段：",
        "analyst": "Analyst 复核相关 claim 的支撑：",
        "reporter": "Reporter 改写指定段落以解决以下问题：",
    }.get(target, "请处理以下问题：")
    fixes = []
    seen: set[str] = set()
    for issue in issues:
        if issue.suggested_fix in seen:
            continue
        seen.add(issue.suggested_fix)
        fixes.append(f"- [{issue.severity}] {issue.suggested_fix}")
        if len(fixes) >= 5:
            break
    return f"{head}（维度：{', '.join(dims)}）\n" + "\n".join(fixes)


def _issue_brief(issue: QAIssue) -> dict:
    return {
        "issue_id": issue.issue_id,
        "dimension": issue.dimension.value,
        "severity": issue.severity,
        "location": issue.location,
        "problem": issue.problem,
        "required_inputs": issue.required_inputs,
    }


def _severity_counts(issues: list[QAIssue]) -> dict[str, int]:
    counts: dict[Literal["minor", "major", "critical"], int] = {
        "minor": 0,
        "major": 0,
        "critical": 0,
    }
    for issue in issues:
        counts[issue.severity] += 1  # type: ignore[index]
    return dict(counts)


# ----- 防死循环：error 注入 -----


def max_retry_error(prior_count: int) -> AgentError:
    return AgentError(
        code="MAX_RETRY_REACHED",
        message=(
            f"prior_verdicts 累计 {prior_count} 次仍未通过，已强制放行。"
            "报告应在受影响段落附 '[未完全验证]' 注释。"
        ),
        severity="warn",
        retriable=False,
        details={"prior_count": prior_count},
    )


__all__ = [
    "MAX_RETRY_VERDICTS",
    "OverallVerdict",
    "SAME_ISSUE_MAX_OCCURRENCES",
    "SEVERITY_WEIGHTS",
    "aggregate_verdict",
    "build_routing",
    "count_prior_issue_occurrences",
    "downgrade_repeated_issues",
    "max_retry_error",
]
