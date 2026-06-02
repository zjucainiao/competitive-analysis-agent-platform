"""ProjectMetrics 计算器 —— 把一次 run 的 plan / outputs / verdicts 汇总成业务指标。

调用入口：
    metrics = compute_project_metrics(plan, outputs, verdicts, qa_round_count)

由 ``Orchestrator._dispatch_step`` 在判定 plan 终态时调用，结果写回
``Project.metrics`` 并落 ``state_store.save_project()``。

v1 公式（详见 docs/METRICS.md）：

- ``accuracy``           : 最新 QAVerdict.dimension_results 所有维度分数算术平均
- ``coverage``           : QA schema_completeness 维度分数（等价于 profile 必填覆盖）
- ``edit_rate``          : 人工编辑过的段落占报告总段落比例（v1 未接前端 PATCH，固定 0）
- ``evidence_count``     : ∑ ExtractorOutput.evidences 长度
- ``fields_filled_ratio``: 同 coverage（保留独立字段方便前端做不同维度展示）
- ``total_tokens``       : ∑ AgentOutputBase.(tokens_input + tokens_output)
- ``total_cost_usd``     : ∑ AgentOutputBase.cost_usd（豆包 EP 走方舟控制台 → 0）
- ``duration_seconds``   : max(node.ended_at) − min(node.started_at)
- ``qa_round_count``     : 反馈环跑了几轮（FeedbackRouter 维护）
- ``real_fetch_count``   : RawSourceDoc.url 指向真实 HTTP(s) URL 的条数
- ``mock_fetch_count``   : RawSourceDoc 来自 fixtures / file:// 的条数
"""

from __future__ import annotations

from backend.schemas import (
    AgentOutputBase,
    CollectorOutput,
    DAGPlan,
    ExtractorOutput,
    ProjectMetrics,
    QADimension,
    QAVerdict,
)


def compute_project_metrics(
    *,
    plan: DAGPlan,
    outputs: dict[str, AgentOutputBase],
    verdicts: list[QAVerdict],
    qa_round_count: int,
) -> ProjectMetrics:
    """从最终 state 派生 ProjectMetrics。"""
    duration_seconds = _compute_duration(plan)
    total_tokens, total_cost = _aggregate_tokens_and_cost(outputs)
    evidence_count = _count_evidences(outputs)
    fields_filled_ratio, accuracy = _scores_from_last_verdict(verdicts)
    real, mock = _fetch_counts(outputs)

    return ProjectMetrics(
        accuracy=accuracy,
        coverage=fields_filled_ratio,
        edit_rate=0.0,
        evidence_count=evidence_count,
        fields_filled_ratio=fields_filled_ratio,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        duration_seconds=duration_seconds,
        qa_round_count=qa_round_count,
        real_fetch_count=real,
        mock_fetch_count=mock,
    )


# ---------- 内部计算 ----------


def _compute_duration(plan: DAGPlan) -> int:
    starts = [n.started_at for n in plan.nodes if n.started_at is not None]
    ends = [n.ended_at for n in plan.nodes if n.ended_at is not None]
    if not starts or not ends:
        return 0
    return max(int((max(ends) - min(starts)).total_seconds()), 0)


def _aggregate_tokens_and_cost(
    outputs: dict[str, AgentOutputBase],
) -> tuple[int, float]:
    total_tokens = 0
    total_cost = 0.0
    for out in outputs.values():
        total_tokens += int(out.tokens_input or 0) + int(out.tokens_output or 0)
        total_cost += float(out.cost_usd or 0.0)
    return total_tokens, total_cost


def _count_evidences(outputs: dict[str, AgentOutputBase]) -> int:
    count = 0
    for out in outputs.values():
        if isinstance(out, ExtractorOutput):
            count += len(out.evidences or [])
    return count


def _scores_from_last_verdict(
    verdicts: list[QAVerdict],
) -> tuple[float, float]:
    """返回 (fields_filled_ratio, accuracy)。无 verdict 返回 (0.0, 0.0)。"""
    if not verdicts:
        return 0.0, 0.0
    last = verdicts[-1]
    if not last.dimension_results:
        return 0.0, 0.0
    sc = last.dimension_results.get(QADimension.SCHEMA_COMPLETENESS)
    fields_filled_ratio = float(sc.score) if sc else 0.0
    scores = [float(r.score) for r in last.dimension_results.values()]
    accuracy = sum(scores) / len(scores) if scores else 0.0
    return fields_filled_ratio, accuracy


def _fetch_counts(outputs: dict[str, AgentOutputBase]) -> tuple[int, int]:
    real = 0
    mock = 0
    for out in outputs.values():
        if not isinstance(out, CollectorOutput):
            continue
        for src in out.raw_sources or []:
            # RawSourceDoc 自带 fetch_method —— mock 数据走 fetch_method="mock"
            if getattr(src, "fetch_method", None) == "mock":
                mock += 1
            else:
                real += 1
    return real, mock


__all__ = ["compute_project_metrics"]
