"""ProjectMetrics 计算器 —— 把一次 run 的 plan / outputs / verdicts 汇总成业务指标。

调用入口：
    metrics = compute_project_metrics(plan, outputs, verdicts, qa_round_count)

由 ``Orchestrator._dispatch_step`` 在判定 plan 终态时调用，结果写回
``Project.metrics`` 并落 ``state_store.save_project()``。

v1 公式（详见 docs/METRICS.md）：

- ``accuracy``           : 最新 QAVerdict.dimension_results 所有维度分数算术平均
- ``coverage``           : QA schema_completeness 维度分数（等价于 profile 必填覆盖）
- ``edit_rate``          : 人工修正率（manual_edits / 报告段落数）。本函数算 0；
  真实值由 reports/evidence 的 PATCH 路径累加，``_persist_metrics`` 跨重跑保留
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

from typing import Any

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
    # 版本化 key 下同一节点可能有多轮(reporter/reporter_v2、extract.X/extract.X_v2)。
    # 计数类指标(证据数/抓取数/token)按「每节点最新轮」聚合,避免把返工轮重复计入。
    # 注意:发布择优 best_round_reporter_key 在 API 层单独用**完整** outputs 调用,
    # 不走这里,故此处去重不影响择优。
    from backend.orchestrator.run_state import latest_outputs

    outputs = latest_outputs(outputs)
    duration_seconds = _compute_duration(plan)
    total_tokens, total_cost = _aggregate_tokens_and_cost(outputs)
    evidence_count = _count_evidences(outputs)
    fields_filled_ratio, accuracy = _scores_from_last_verdict(verdicts)
    per_round_accuracy, round_delta, best_round = _scores_per_round(verdicts)
    real, mock = _fetch_counts(outputs)

    return ProjectMetrics(
        accuracy=accuracy,
        coverage=fields_filled_ratio,
        # 0 占位：真实 edit_rate 由 PATCH 路径维护，_persist_metrics 会保留旧值
        edit_rate=0.0,
        evidence_count=evidence_count,
        fields_filled_ratio=fields_filled_ratio,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        duration_seconds=duration_seconds,
        qa_round_count=qa_round_count,
        per_round_accuracy=per_round_accuracy,
        round_delta=round_delta,
        best_round=best_round,
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


def _verdict_mean_score(verdict: QAVerdict) -> float:
    """单份 verdict 的维度均分（所有 dimension_results.score 算术平均）。"""
    dr = verdict.dimension_results
    if not dr:
        return 0.0
    scores = [float(r.score) for r in dr.values()]
    return sum(scores) / len(scores) if scores else 0.0


def _scores_per_round(
    verdicts: list[QAVerdict],
) -> tuple[list[float], list[float], int]:
    """跨轮质量序列。

    返回 ``(per_round_accuracy, round_delta, best_round)``：
    - ``per_round_accuracy``：每轮 verdict 的维度均分（verdict 顺序 == 轮次顺序）
    - ``round_delta``：相邻轮差值 ``score[i] - score[i-1]``（长度 = 轮数-1）
    - ``best_round``：维度均分最高的轮(1-based)，0 表示无 verdict；
      **并列时取较晚轮**（更多返工、更可能是最终打磨版），故不改善时退化为最后一轮。
    """
    per_round = [round(_verdict_mean_score(v), 6) for v in verdicts]
    deltas = [round(per_round[i] - per_round[i - 1], 6) for i in range(1, len(per_round))]
    best_round = 0
    if per_round:
        best_idx = 0
        best_score = per_round[0]
        for i in range(1, len(per_round)):
            if per_round[i] >= best_score:  # 并列取较晚轮
                best_score = per_round[i]
                best_idx = i
        best_round = best_idx + 1
    return per_round, deltas, best_round


def best_round_reporter_key(outputs: dict[str, Any], verdicts: list[QAVerdict]) -> str:
    """发布择优：挑维度均分最高那一轮对应的 reporter 输出 key。

    **契约：``verdicts`` 必须按轮次升序(round1 在前)**。注意 storage 的
    ``list_qa_verdicts`` 返回的是 created_at **DESC**(最新在前)，调用方须先
    ``reversed`` 再传入，否则会选错轮次(P1P2-VERDICT-ORDER)。``compute_project_metrics``
    走 native ``RunState.verdicts``(append 升序)天然满足。

    映射：第 r 轮(1-based) reporter 落键 ``reporter``(r==1) 或 ``reporter_v{r}``。
    无 verdict / 该 key 不在 outputs → 退回「最高 revision」(旧行为)。并列取较晚轮
    （见 ``_scores_per_round``），故无改善时与旧行为一致。
    """

    def _fallback() -> str:
        versioned = sorted((k for k in outputs if k.startswith("reporter_v")), reverse=True)
        return versioned[0] if versioned else "reporter"

    if not verdicts:
        return _fallback()
    _, _, best_round = _scores_per_round(verdicts)
    if best_round <= 0:
        return _fallback()
    key = "reporter" if best_round == 1 else f"reporter_v{best_round}"
    return key if key in outputs else _fallback()


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


__all__ = ["best_round_reporter_key", "compute_project_metrics"]
