"""QA 路由 / 整体判定 / 防死循环。

模块职责：把各维度 checker 各自产出的 issues 聚合成：
- 按 target_agent 装配的 QARouting 列表
- 整体 overall_status / blocking
- 防死循环：根据 prior_verdicts 中的累计次数降级反复出现的 issue
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.schemas import (
    AgentError,
    QADimension,
    QADimensionResult,
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


# ----- A1: 维度策略（接 score 入判级 + 杀静默放行） -----
#
# 维度 → (返工目标 agent, 是否核心维度)。
# - 任何维度 score < 阈值(pass_=False)但 checker 没出 issue → 由
#   ``synthesize_threshold_issues`` 补发,杜绝「低分静默放行」。
# - core=True 的维度不及格 → 触发一轮 blocking 返工(``aggregate_verdict``)。
#   设 core 的判准:**既重要、又能被返工真正修好**——
#   · evidence_completeness:缺引用 → reporter 补引用即可达标(实测 0.71→1.0)。
#   · schema_completeness:必填字段缺 → extractor 重抽 / collector 补采,
#     属「数据层可真修」,与 evidence 同类,故纳入 core(原 False,2026-06-08 调整)。
#   fact_consistency 阈值 0.95 现实达不到、reporter 也难修(实测 0.6→0.6),
#   设 core 只会每次空转到触顶——故只浮出、不强阻塞(阈值重标见后续)。
# - 防空转:核心维只给**一次**阻塞机会。某核心维上一轮已失败、返工后仍不达标
#   (源数据本就缺、修不动)→ 不再强制阻塞,落回权重判级,交 best-round 发布。
#   见 ``aggregate_verdict`` 的复发护栏 ``_core_dims_failed_before``。
DIMENSION_POLICY: dict[QADimension, tuple[str, bool]] = {
    QADimension.FACT_CONSISTENCY: ("reporter", False),
    QADimension.EVIDENCE_COMPLETENESS: ("reporter", True),
    QADimension.COVERAGE_DENSITY: ("reporter", False),
    QADimension.LOGIC_CONSISTENCY: ("reporter", False),
    QADimension.EXPRESSION: ("reporter", False),
    QADimension.SCHEMA_COMPLETENESS: ("extractor", True),
    QADimension.FRESHNESS: ("collector", False),
    # identity_consistency：抓错产品 → 回 collector 重采。设 core：抓到别的产品
    # 的内容进了报告是硬伤、且**能被真修**（collector 排除跑题源后重采即收敛），
    # 与 evidence/schema 同属「数据层可真修」，故纳入 core 触发一轮 blocking 返工。
    QADimension.IDENTITY_CONSISTENCY: ("collector", True),
}
CORE_DIMENSIONS = frozenset(d for d, (_, core) in DIMENSION_POLICY.items() if core)

_DIMENSION_FIX_HINT: dict[QADimension, str] = {
    QADimension.FACT_CONSISTENCY: "复核相关段落，确保每条事实可由引用证据字面推出",
    QADimension.EVIDENCE_COMPLETENESS: "为缺引用的事实段落补齐 evidence_ids",
    QADimension.SCHEMA_COMPLETENESS: "重新抽取以补齐必填/扩展字段，降低 unverified 占比",
    QADimension.COVERAGE_DENSITY: "为信息稀薄的章节补充实质性段落",
    QADimension.LOGIC_CONSISTENCY: "消解段落间的口径/数据矛盾",
    QADimension.FRESHNESS: "补采更新的来源，替换过期/无日期证据",
    QADimension.EXPRESSION: "去除绝对化/第一人称/过度推断表述",
    QADimension.IDENTITY_CONSISTENCY: "排除跑题来源，重新采集确属目标产品的内容",
}


def synthesize_threshold_issues(
    dimension_results: dict[QADimension, QADimensionResult],
    existing_issues: list[QAIssue],
) -> list[QAIssue]:
    """对 ``pass_=False`` 但当前无 issue 的维度补发一条 issue，杀掉静默放行。

    core 维度 → major(进入加权判级 + 触发 blocking)；其余 → minor(浮出、低权重，
    不主导判级,避免在 reporter 改不动的维度上空转)。location 用维度级稳定串，
    跨轮 dedupe 一致(复用 ``downgrade_repeated_issues``)。
    """
    dims_with_issue = {i.dimension for i in existing_issues}
    out: list[QAIssue] = []
    for dim, res in dimension_results.items():
        if res.pass_ or dim in dims_with_issue:
            continue
        target, core = DIMENSION_POLICY.get(dim, ("reporter", False))
        out.append(
            QAIssue(
                issue_id=f"iss_dim_{dim.value}",
                dimension=dim,
                severity="major" if core else "minor",  # type: ignore[arg-type]
                location=f"report.dimension[{dim.value}]",
                problem=(
                    f"{dim.value} 维度得分 {res.score:.2f} 低于通过阈值，"
                    "checker 未生成具体 issue（已拦截静默放行）。"
                ),
                suggested_fix=_DIMENSION_FIX_HINT.get(dim, "复核该维度并修正"),
                target_agent=target,  # type: ignore[arg-type]
                required_inputs={
                    "synthesized": True,
                    "score": round(float(res.score), 3),
                },
            )
        )
    return out


def escalate_by_self_status(
    issues: list[QAIssue], upstream_statuses: dict[str, str] | None
) -> list[QAIssue]:
    """把「自评 needs_rework 的上游 Agent」名下的 minor issue 升级为 major。

    第 ⑤ 条的落法：agent 自己发现问题打了 ``needs_rework``——这本是纯历史状态、
    不影响控制流。这里让 QA **消费**它作为判级信号：若某上游 agent 自评不达标，
    且 QA 也确实为它开了 issue，则把该 agent 名下的 minor 升级为 major（拔高权重，
    可能让一个原本非阻塞的问题进入 blocking）。

    刻意只**加权已有 issue**，不凭空造 issue —— 避免「agent 一自评 needs_rework
    就强制返工」的失控循环；没有实打实问题时不放大。控制流仍由 QA verdict 决定。
    """
    flagged = {
        a for a, s in (upstream_statuses or {}).items() if s == "needs_rework"
    }
    if not flagged:
        return issues
    out: list[QAIssue] = []
    for issue in issues:
        if issue.target_agent in flagged and issue.severity == "minor":
            new_required = dict(issue.required_inputs)
            new_required["escalated_by_self_status"] = True
            out.append(
                QAIssue(
                    issue_id=issue.issue_id,
                    dimension=issue.dimension,
                    severity="major",
                    location=issue.location,
                    problem=issue.problem + "（上游自评 needs_rework，升级权重）",
                    suggested_fix=issue.suggested_fix,
                    target_agent=issue.target_agent,
                    required_inputs=new_required,
                )
            )
        else:
            out.append(issue)
    return out


# ----- 整体判定 -----


@dataclass
class OverallVerdict:
    status: QAStatus
    blocking: bool
    confidence: float
    total_weight: int
    max_retry_reached: bool


def _core_dims_failed_before(
    prior_verdicts: list[QAVerdict] | None,
) -> set[QADimension]:
    """历史 verdict 中已经失败(pass_=False)过的**核心**维度集合。

    复发护栏:核心维度失败会触发一轮阻塞返工;但若该维度上一轮已失败、返工后
    仍没修好,再阻塞只会空转到触顶(max_rounds)。故每个核心维度只给**一次**阻塞
    机会——失败过即落入本集合,``aggregate_verdict`` 不再据它强制阻塞,改交权重
    判级 + best-round 兜底。可真修的(evidence 补引用 / schema 重抽)一轮即收敛;
    改不动的(源数据本就缺)不空转。
    """
    if not prior_verdicts:
        return set()
    failed: set[QADimension] = set()
    for v in prior_verdicts:
        for dim, res in (getattr(v, "dimension_results", None) or {}).items():
            if dim in CORE_DIMENSIONS and not res.pass_:
                failed.add(dim)
    return failed


def _has_hard_block_issue(issues: list[QAIssue]) -> bool:
    """是否存在「确定性硬伤」issue：标了 ``hard_block`` 且仍为 major/critical。

    B：fact_consistency 的 contradicted 段落 / 量化字面失配属**确定性错误**（报告写了
    与证据冲突的事实 / 对不上的数字）——即便该维度非 core（阈值 0.95 现实达不到、不宜
    整维度强阻塞），这类明确硬伤也应一票阻塞返工一轮，而非只浮出。

    只认仍为 major/critical：复发 ≥ ``SAME_ISSUE_MAX_OCCURRENCES`` 次被
    ``downgrade_repeated_issues`` 降为 minor 后不再硬阻塞，与 MAX_RETRY / max_rounds
    一道保证终止，改不掉的硬伤最终落 best-round 发布，不空转。
    """
    return any(
        i.required_inputs.get("hard_block") is True
        and i.severity in ("major", "critical")
        for i in issues
    )


def aggregate_verdict(
    issues: list[QAIssue],
    prior_count: int,
    dimension_results: dict[QADimension, QADimensionResult] | None = None,
    prior_verdicts: list[QAVerdict] | None = None,
) -> OverallVerdict:
    """根据 issues + 历史 verdict 数计算整体状态。

    A1：若传入 ``dimension_results`` 且**核心维度**(CORE_DIMENSIONS)不及格，则强制
    至少 needs_revision 且 blocking=True——让「低置信核心维度」真正触发返工，而不是
    只看 issue 权重。已达最大重试(max_retry_reached)时不再强制，交由触顶放行 +
    best-round 兜底,避免死循环。

    复发护栏:已在 ``prior_verdicts`` 中失败过的核心维度**不再**强制阻塞(见
    ``_core_dims_failed_before``)——给每个核心维度一次返工机会,修不好就发布最优轮,
    不空转。``prior_verdicts`` 缺省(None)时退化为旧行为(每次失败都阻塞)。
    """
    base = _base_verdict(issues, prior_count)
    if base.max_retry_reached:
        return base

    force_block = False
    if dimension_results:
        already_failed = _core_dims_failed_before(prior_verdicts)
        if any(
            not res.pass_
            for dim, res in dimension_results.items()
            if dim in CORE_DIMENSIONS and dim not in already_failed
        ):
            force_block = True
    # B：确定性硬伤（事实冲突 / 数字失配）即便所属维度非 core 也阻塞一轮返工。
    if _has_hard_block_issue(issues):
        force_block = True

    if force_block:
        status = (
            base.status
            if base.status == QAStatus.REJECT
            else QAStatus.NEEDS_REVISION
        )
        return OverallVerdict(
            status=status,
            blocking=True,
            confidence=min(base.confidence, 0.6),
            total_weight=base.total_weight,
            max_retry_reached=False,
        )
    return base


def _base_verdict(issues: list[QAIssue], prior_count: int) -> OverallVerdict:
    """纯 issue-权重判级（A1 之前的原逻辑）。"""
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

    blocking=False 时**仍返回** routing，但仅作**展示/审计**用（让前端 / verdict
    呈现「本可返工的目标与原因」），instructions 中标注 'non-blocking'。
    编排器**不会**据非阻塞 routing 回灌：native ``decide_qa_route`` 规则 2 与
    legacy ``orchestrator`` 在 ``blocking is False`` 时一律直接 END（带短板发布）。
    即「是否返工」只由 ``blocking`` 决定，routing 列表本身不触发回环。
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
    "CORE_DIMENSIONS",
    "DIMENSION_POLICY",
    "MAX_RETRY_VERDICTS",
    "OverallVerdict",
    "SAME_ISSUE_MAX_OCCURRENCES",
    "SEVERITY_WEIGHTS",
    "aggregate_verdict",
    "build_routing",
    "count_prior_issue_occurrences",
    "downgrade_repeated_issues",
    "escalate_by_self_status",
    "max_retry_error",
    "synthesize_threshold_issues",
]
