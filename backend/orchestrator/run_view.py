"""RunState → RunStateView 装配器（纯函数）。

Phase 2 Stage B 新增。把原生引擎的 ``RunState``（dump 后的 dict）投影成前端将要
消费的 ``RunStateView``：5 个静态阶段骨架，collect/extract 按产品出 instances，
analyst/reporter/qa 按轮次出 revisions；token/cost/confidence/duration 从对应
``AgentOutput``（``outputs[run_ref]``）派生。

contract：
- ``state`` 是 ``RunState.model_dump()`` 的结果——``history`` 是 **dict 列表**
  （各 key 与 ``NodeRun`` 字段一一对应），``outputs`` 是 ``{ref: output_dict}``，
  ``verdicts`` 是 dict 列表。
- run_ref 复用 ``projection._node_id``，与旧 DAGPlan 投影 / 前端 v1↔v2 命名保持一致。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.schemas import Project, ProjectMetrics
from backend.schemas.run_view import (
    PRODUCT_STAGES,
    STAGE_AGENT,
    STATIC_STAGES,
    RunStageView,
    RunStateView,
    StageInstance,
    StageRevision,
)

from .projection import _node_id

# 终态中视为「节点执行成功」的状态（与 projection 一致：partial / needs_rework 也算完成）
_SUCCESS_STATUSES = frozenset({"success", "partial", "needs_rework"})


def _metric_fields(out: Any) -> dict[str, Optional[float]]:
    """从一个 AgentOutput（dict 或对象）取 token/cost/confidence/duration。

    缺字段时返回 None（前端按缺失渲染），不强行填 0。
    """
    if out is None:
        return {
            "tokens_input": None,
            "tokens_output": None,
            "cost_usd": None,
            "confidence": None,
            "duration_ms": None,
        }

    def _get(name: str) -> Any:
        if isinstance(out, dict):
            return out.get(name)
        return getattr(out, name, None)

    return {
        "tokens_input": _get("tokens_input"),
        "tokens_output": _get("tokens_output"),
        "cost_usd": _get("cost_usd"),
        "confidence": _get("confidence"),
        "duration_ms": _get("duration_ms"),
    }


def _overall_status(history: list[dict], *, aborted: bool) -> str:
    """计算整体 run 状态：running / done / failed / aborted。

    规则（保守、健壮）：
    - aborted=True → "aborted"
    - 否则：若存在某「终端 failed」节点且其后再无该逻辑节点的成功记录 → "failed"
    - 否则：若 history 里出现过 qa 节点（说明流水线跑到末端）→ "done"
    - 否则 → "running"
    """
    if aborted:
        return "aborted"

    # 按 (node, product) 分组，取每组最后一条记录的状态：若最终态是 failed 则视为失败。
    last_status_by_key: dict[tuple[str, Optional[str]], str] = {}
    for run in history:
        key = (run.get("node"), run.get("product"))
        last_status_by_key[key] = run.get("status", "")
    if any(s == "failed" for s in last_status_by_key.values()):
        return "failed"

    has_qa = any(run.get("node") == "qa" for run in history)
    return "done" if has_qa else "running"


def run_state_to_view(
    state: dict,
    *,
    project: Project,
    metrics: ProjectMetrics | None = None,
) -> RunStateView:
    """把 ``RunState.model_dump()`` 投影为 ``RunStateView``。

    :param state: ``RunState.model_dump()``，history/verdicts 均为 dict 列表，
        outputs 为 ``{ref: output_dict}``。
    :param project: 当前 ``Project``（用于 project_id；products 优先取 state）。
    :param metrics: 可选业务指标，直接挂到视图（通常 ``project.metrics``）。
    :returns: ``RunStateView``，含 5 个静态阶段骨架。
    """
    history: list[dict] = list(state.get("history", []))
    outputs_by_ref: dict = state.get("outputs", {}) or {}
    raw_verdicts: list = list(state.get("verdicts", []))

    products: list[str] = list(
        state.get("products") or [project.target_product, *project.competitors]
    )

    # 按逻辑 node 分组 history
    by_node: dict[str, list[dict]] = {stage: [] for stage in STATIC_STAGES}
    for run in history:
        node = run.get("node")
        if node in by_node:
            by_node[node].append(run)

    stages: list[RunStageView] = []
    for stage in STATIC_STAGES:
        runs = by_node[stage]
        if stage in PRODUCT_STAGES:
            instances = _build_instances(runs, outputs_by_ref, stage)
            stages.append(
                RunStageView(stage=stage, agent=STAGE_AGENT[stage], instances=instances)
            )
        else:
            revisions = _build_revisions(runs, outputs_by_ref, stage)
            stages.append(
                RunStageView(stage=stage, agent=STAGE_AGENT[stage], revisions=revisions)
            )

    aborted = bool(state.get("aborted", False))
    abort_reason = state.get("abort_reason") or None

    return RunStateView(
        project_id=project.project_id,
        run_id=state.get("run_id"),
        status=_overall_status(history, aborted=aborted),
        products=products,
        stages=stages,
        history=history,
        verdicts=[_as_dict(v) for v in raw_verdicts],
        qa_round=int(state.get("qa_round", 0) or 0),
        aborted=aborted,
        abort_reason=abort_reason,
        metrics=metrics,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


def _build_instances(
    runs: list[dict], outputs_by_ref: dict, stage: str
) -> list[StageInstance]:
    """产品阶段：每产品取「最新一轮」NodeRun，派生 StageInstance。

    同一产品可能有多轮（QA 返工触发 per-product 重做）；以 round 最大者为准，
    确保前端 DAG 骨架显示该产品的最终态。
    """
    latest_by_product: dict[str, dict] = {}
    for run in runs:
        product = run.get("product")
        if product is None:
            continue
        cur = latest_by_product.get(product)
        if cur is None or run.get("round", 1) >= cur.get("round", 1):
            latest_by_product[product] = run

    instances: list[StageInstance] = []
    for product in sorted(latest_by_product):
        run = latest_by_product[product]
        round_ = run.get("round", 1)
        run_ref = _node_id(stage, product, round_)
        out = outputs_by_ref.get(run.get("output_ref") or run_ref)
        m = _metric_fields(out)
        instances.append(
            StageInstance(
                product=product,
                status=run.get("status", ""),
                revision=round_,
                run_ref=run_ref,
                span_id=run.get("span_id"),
                started_at=run.get("started_at"),
                ended_at=run.get("ended_at"),
                **m,
            )
        )
    return instances


def _build_revisions(
    runs: list[dict], outputs_by_ref: dict, stage: str
) -> list[StageRevision]:
    """全局阶段：每轮一条 StageRevision，按 round 升序。

    同一 round 若重复出现（理论上的 barrier 重放），取首条。
    """
    seen_rounds: set[int] = set()
    ordered: list[dict] = []
    for run in sorted(runs, key=lambda r: r.get("round", 1)):
        round_ = run.get("round", 1)
        if round_ in seen_rounds:
            continue
        seen_rounds.add(round_)
        ordered.append(run)

    revisions: list[StageRevision] = []
    for run in ordered:
        round_ = run.get("round", 1)
        run_ref = _node_id(stage, None, round_)
        out = outputs_by_ref.get(run.get("output_ref") or run_ref)
        m = _metric_fields(out)
        revisions.append(
            StageRevision(
                round=round_,
                status=run.get("status", ""),
                run_ref=run_ref,
                span_id=run.get("span_id"),
                started_at=run.get("started_at"),
                ended_at=run.get("ended_at"),
                **m,
            )
        )
    return revisions


def _as_dict(v: Any) -> dict:
    """把 verdict 归一为 dict（已是 dict 直接返回；Pydantic 对象 dump）。"""
    if isinstance(v, dict):
        return v
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    return dict(v)


__all__ = ["run_state_to_view"]
