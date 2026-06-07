"""DAG 运行控制路由。"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from ulid import ULID

from backend.api.deps import get_orchestrator, get_owned_project, get_storage
from backend.api.schemas import RunStartedResponse
from backend.orchestrator import Orchestrator
from backend.orchestrator.run_state import RunState
from backend.orchestrator.run_view import run_state_to_view
from backend.schemas import Project, ProjectStatus, RunSnapshot, RunStateView
from backend.schemas.project import RunRef
from backend.storage import Storage
from backend.storage.serde import dump_output

router = APIRouter(tags=["runs"])

_log = logging.getLogger(__name__)


def _is_native() -> bool:
    """当前是否走原生 LangGraph 引擎（与 Orchestrator.run 同判据）。"""
    return os.getenv("ORCH_ENGINE", "native") == "native"


async def _read_native_run_state(
    orch: Orchestrator, project: Project
) -> dict | None:
    """从 LangGraph checkpoint 读取该项目最近一次 native run 的 RunState。

    重建同一张 native 图（挂同一 checkpointer）后 ``aget_state`` 只读 checkpoint、
    **不**触发任何节点执行；无 checkpoint（从未跑过 native）时返回 None。

    返回 ``RunState.model_dump()``（history/verdicts 归一为 dict，outputs 仍是
    AgentOutput 对象 —— 装配器只取其 metric 字段，对象/dict 皆可）。
    """
    from backend.orchestrator.graph import build_native_graph
    from backend.storage.langgraph_adapter import to_langgraph_saver

    try:
        graph = build_native_graph(
            orch.registry,
            project=project,
            checkpointer=to_langgraph_saver(orch.storage.checkpointer),
        )
        config = {"configurable": {"thread_id": project.project_id}}
        snapshot = await graph.aget_state(config)
    except Exception:  # noqa: BLE001
        _log.exception(
            "read native checkpoint failed project=%s", project.project_id
        )
        return None

    values: dict[str, Any] = getattr(snapshot, "values", None) or {}
    if not values:
        return None
    # values 里 history 是 NodeRun 对象、outputs 是 AgentOutput 对象；
    # model_validate→model_dump 归一为装配器契约要求的 dict 形态。
    try:
        return RunState.model_validate(values).model_dump()
    except Exception:  # noqa: BLE001
        # 兼容极端情况：直接退回原始 values（装配器对 dict/对象都健壮）。
        return values


async def read_native_run_history(
    orch: Orchestrator, project: Project
) -> list[dict]:
    """读取 native checkpoint 里 RunState.history 的 dict 列表（供 RunSnapshot 落库）。

    无 checkpoint 或读取失败时返回空列表（best-effort，不阻塞快照持久化）。
    """
    state = await _read_native_run_state(orch, project)
    if not state:
        return []
    return list(state.get("history", []))


@router.post(
    "/projects/{project_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunStartedResponse,
)
async def start_run(
    project_id: str,
    request: Request,
    storage: Storage = Depends(get_storage),
    orch: Orchestrator = Depends(get_orchestrator),
    project: Project = Depends(get_owned_project),
) -> RunStartedResponse:
    running_tasks: dict[str, asyncio.Task] = request.app.state.running_tasks
    if project_id in running_tasks and not running_tasks[project_id].done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"project {project_id!r} already running",
        )

    plan = orch.plan(project)
    run_id = f"run_{ULID()}"
    started_at = datetime.now(timezone.utc)

    # 把这次 run 的 metadata 追加到 project.runs（前端可看 run 历史时间线）
    new_run = RunRef(
        run_id=run_id, plan_id=plan.plan_id, started_at=started_at, final_status=None,
    )
    project_with_run = project.model_copy(
        update={"runs": list(project.runs) + [new_run], "status": ProjectStatus.RUNNING}
    )
    await storage.state_store.save_project(project_with_run)

    async def _run_in_bg() -> None:
        final_status = "done"
        try:
            async for _ in orch.run(plan, project_with_run):
                pass
        except Exception:  # noqa: BLE001
            _log.exception("orchestrator run failed for project_id=%s", project_id)
            final_status = "failed"

        # 写回该 RunRef 的 ended_at + final_status
        latest = await storage.state_store.get_project(project_id)
        if latest is not None:
            updated_runs = []
            for r in latest.runs:
                if r.run_id == run_id:
                    updated_runs.append(
                        r.model_copy(
                            update={
                                "ended_at": datetime.now(timezone.utc),
                                "final_status": final_status,
                            }
                        )
                    )
                else:
                    updated_runs.append(r)
            new_status = (
                ProjectStatus.DONE if final_status == "done" else ProjectStatus.FAILED
            )
            await storage.state_store.save_project(
                latest.model_copy(update={"runs": updated_runs, "status": new_status})
            )

            # 落不可变 RunSnapshot（按 (project_id, run_id) 主键）。
            # 与 latest 状态独立：之后再启动新 run 不会污染这份历史。
            try:
                final_plan = await storage.state_store.get_dag_plan(project_id)
                final_outputs = await storage.state_store.list_node_outputs(project_id)
                final_verdicts = await storage.state_store.list_qa_verdicts(project_id)
                # native 引擎：从 checkpoint 取最终 history（回放真相源），落进快照。
                # legacy 引擎无 native RunState，history 保持空（向后兼容）。
                history: list[dict] = []
                if _is_native():
                    try:
                        history = await read_native_run_history(orch, project_with_run)
                    except Exception:  # noqa: BLE001
                        _log.exception(
                            "read native history for snapshot failed project=%s",
                            project_id,
                        )
                if final_plan is not None:
                    snapshot = RunSnapshot(
                        project_id=project_id,
                        run_id=run_id,
                        captured_at=datetime.now(timezone.utc),
                        plan=final_plan,
                        outputs={
                            nid: dump_output(out) for nid, out in final_outputs.items()
                        },
                        verdicts=final_verdicts,
                        metrics=latest.metrics,
                        final_status=final_status,
                        history=history,
                    )
                    await storage.state_store.save_run_snapshot(snapshot)
            except Exception:  # noqa: BLE001
                _log.exception(
                    "save_run_snapshot failed (non-fatal) project=%s run=%s",
                    project_id,
                    run_id,
                )

    task = asyncio.create_task(_run_in_bg(), name=f"run-{project_id}-{run_id}")
    running_tasks[project_id] = task

    return RunStartedResponse(
        project_id=project_id,
        plan_id=plan.plan_id,
        thread_id=project_id,
        started_at=started_at,
    )


class RunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    runs: list[RunRef]


@router.get("/projects/{project_id}/runs", response_model=RunListResponse)
async def list_runs(
    project_id: str,
    storage: Storage = Depends(get_storage),
    project: Project = Depends(get_owned_project),
) -> RunListResponse:
    """单项目所有 run 的 metadata 时间线（前端的 Rerun 按钮 / run 切换器需要）。

    每次 run 的完整 state 单独通过 ``GET /projects/{id}/runs/{run_id}/state`` 取
    （由 RunSnapshot 持久化，与 latest 状态相互独立）。
    """
    return RunListResponse(project_id=project_id, runs=list(project.runs))


@router.get("/projects/{project_id}/runs/{run_id}/state")
async def get_run_snapshot(
    project_id: str,
    run_id: str,
    storage: Storage = Depends(get_storage),
    _project: Project = Depends(get_owned_project),
) -> dict:
    """取某次 run 终态时的完整 state 快照。

    返回 ``RunSnapshot`` 的 JSON 表示（outputs 是已序列化的 dict，前端按 agent_name
    分发渲染即可；要还原成 Pydantic 对象用 ``backend.storage.serde.load_output``）。
    """
    snapshot = await storage.state_store.get_run_snapshot(project_id, run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"run snapshot not found: project={project_id!r} run={run_id!r}",
        )
    return snapshot.model_dump(mode="json")


# 注：旧 `GET /projects/{id}/state`(DAGPlan 形状)已随 Stage D 删除 —— 前端改为单一
# 数据源 `/run-state`(RunStateView),自行投影出所需的 DAGPlan(见前端
# run-view-to-state.ts)。RunState→DAGPlan 的投影逻辑仍在 orchestrator 内部用于
# metrics 计算(projection.run_state_to_dagplan),不再对外暴露。


# ---------- RunStateView 端点（原生引擎前端视图 · 工作流步进器数据源） ----------


# legacy plan node_id → 逻辑 node 的反推（best-effort）。native 投影 node_id 形如
# collect.Notion / reporter / reporter_v2；据前缀判定逻辑阶段。
_VIEW_STAGE_PREFIXES: tuple[str, ...] = (
    "collect",
    "extract",
    "analyst",
    "reporter",
    "qa",
)


def _history_from_snapshot_plan(snapshot: RunSnapshot) -> list[dict]:
    """旧快照（history 空）时，从 plan.nodes best-effort 重建 NodeRun-as-dict 列表。

    仅用于 ``/runs/{id}/view`` 的向后兼容路径：新写入的 native 快照已带 history，
    走不到这里。无法可靠还原 round/started_at 等，尽力而为。
    """
    history: list[dict] = []
    for node in snapshot.plan.nodes:
        nid = node.node_id
        # 解析逻辑阶段 + 产品 + 轮次
        stage = next(
            (p for p in _VIEW_STAGE_PREFIXES if nid == p or nid.startswith(p + ".")
             or nid.startswith(p + "_v")),
            None,
        )
        if stage is None:
            continue
        product: str | None = None
        round_ = 1
        rest = nid[len(stage):]
        if rest.startswith("."):
            tail = rest[1:]
            if "_v" in tail:
                product, _, ver = tail.partition("_v")
                round_ = int(ver) if ver.isdigit() else 1
            else:
                product = tail
        elif rest.startswith("_v"):
            ver = rest[2:]
            round_ = int(ver) if ver.isdigit() else 1
        status_str = (
            "success" if node.status.value in ("success",) else node.status.value
        )
        history.append(
            {
                "node": stage,
                "agent": node.agent_name or stage,
                "product": product,
                "round": round_,
                "status": status_str,
                "span_id": nid,
                "started_at": None,
                "ended_at": None,
                "output_ref": node.output_ref or nid,
            }
        )
    return history


@router.get(
    "/projects/{project_id}/run-state",
    response_model=RunStateView,
)
async def get_run_state_view(
    project_id: str,
    storage: Storage = Depends(get_storage),
    orch: Orchestrator = Depends(get_orchestrator),
    project: Project = Depends(get_owned_project),
) -> RunStateView:
    """LIVE RunStateView：当前/最近一次 run 的原生引擎视图（前端 Stage D 目标）。

    数据源优先级：
    1. native checkpoint（``graph.aget_state`` 只读不跑）——含 live history/outputs；
    2. 无 checkpoint（从未跑 native）时，从持久化的 ``list_node_outputs`` 兜底，
       history 留空（前端按持久化 outputs 渲染最终态）；二者皆无则返回空 5 阶段骨架。

    始终返回含 5 个静态阶段的骨架；metrics 取 ``project.metrics``。
    """
    state = await _read_native_run_state(orch, project)
    if state is None:
        # 兜底：从持久化 outputs 构造一个最小 RunState dump（history 空）。
        outputs = await storage.state_store.list_node_outputs(project_id)
        state = RunState(
            project_id=project_id,
            run_id=project.runs[-1].run_id if project.runs else "",
            analysis_mode=project.analysis_mode.value,
            products=[project.target_product, *project.competitors],
        ).model_dump()
        state["outputs"] = {nid: out for nid, out in outputs.items()}
    return run_state_to_view(state, project=project, metrics=project.metrics)


@router.get(
    "/projects/{project_id}/runs/{run_id}/view",
    response_model=RunStateView,
)
async def get_run_view(
    project_id: str,
    run_id: str,
    storage: Storage = Depends(get_storage),
    project: Project = Depends(get_owned_project),
) -> RunStateView:
    """HISTORICAL RunStateView：从不可变 RunSnapshot 装配某次 run 的视图。

    snapshot.history（native 快照）为真相源；若为空（旧快照），从 snapshot.plan +
    outputs best-effort 重建 history。verdicts/metrics 取自快照。
    """
    snapshot = await storage.state_store.get_run_snapshot(project_id, run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"run snapshot not found: project={project_id!r} run={run_id!r}",
        )
    history = list(snapshot.history) or _history_from_snapshot_plan(snapshot)
    state: dict[str, Any] = {
        "project_id": project_id,
        "run_id": run_id,
        "products": [project.target_product, *project.competitors],
        "outputs": dict(snapshot.outputs),
        "history": history,
        "verdicts": [v.model_dump(mode="json") for v in snapshot.verdicts],
        "qa_round": sum(1 for v in snapshot.verdicts) - 1
        if len(snapshot.verdicts) > 1
        else 0,
        "aborted": snapshot.final_status == "aborted",
        "abort_reason": "",
    }
    return run_state_to_view(state, project=project, metrics=snapshot.metrics)
