"""DAG 运行控制路由。"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from ulid import ULID

from backend.api.deps import get_orchestrator, get_owned_project, get_storage
from backend.api.run_lifecycle import drive_run_to_completion, read_native_run_state
from backend.api.schemas import RunStartedResponse
from backend.orchestrator import Orchestrator
from backend.orchestrator.run_state import RunState
from backend.orchestrator.run_view import run_state_to_view
from backend.schemas import Project, ProjectStatus, RunSnapshot, RunStateView
from backend.schemas.project import RunRef
from backend.storage import Storage

router = APIRouter(tags=["runs"])

_log = logging.getLogger(__name__)


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
    # P1-TOCTOU：「检查 running_tasks → create_task」之间有多个 await（save_project
    # 等），并发双击会双双通过检查、起两个 run。用进程级 spawn 锁把「检查 + 建
    # RunRef + create_task + 登记」整段原子化——同一时刻只有一个请求能走完，其余
    # 在锁后看到未完成的 task，仍按原语义返回 409。
    spawn_lock: asyncio.Lock = request.app.state.run_spawn_lock
    async with spawn_lock:
        running_tasks: dict[str, asyncio.Task] = request.app.state.running_tasks
        if project_id in running_tasks and not running_tasks[project_id].done():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"project {project_id!r} already running",
            )

        plan = orch.plan(project)
        run_id = f"run_{ULID()}"
        started_at = datetime.now(UTC)

        # 把这次 run 的 metadata 追加到 project.runs（前端可看 run 历史时间线）
        new_run = RunRef(
            run_id=run_id,
            plan_id=plan.plan_id,
            started_at=started_at,
            final_status=None,
        )
        project_with_run = project.model_copy(
            update={"runs": [*project.runs, new_run], "status": ProjectStatus.RUNNING}
        )
        await storage.state_store.save_project(project_with_run)

        # 复用本次 run_id，让 native RunState.run_id 与 RunRef/快照/URL 一致(P2-a)；
        # 收尾（RunRef 终态 + RunSnapshot + 异常兜底）统一走共享 drive_run_to_completion。
        task = asyncio.create_task(
            drive_run_to_completion(
                orch.run(plan, project_with_run, run_id=run_id),
                storage=storage,
                orch=orch,
                project=project_with_run,
                run_id=run_id,
                label="start",
            ),
            name=f"run-{project_id}-{run_id}",
        )
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
            (
                p
                for p in _VIEW_STAGE_PREFIXES
                if nid == p or nid.startswith(p + ".") or nid.startswith(p + "_v")
            ),
            None,
        )
        if stage is None:
            continue
        product: str | None = None
        round_ = 1
        rest = nid[len(stage) :]
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
        status_str = "success" if node.status.value in ("success",) else node.status.value
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

    P1-NODEOUTPUTS-VS-CHECKPOINT：人工修改(段落 PATCH /reports、证据 dispute /evidence)
    只写 ``node_outputs``，**不**回写 checkpoint。故无论走 checkpoint 还是兜底，都用持久化
    ``node_outputs`` **覆盖**同名 ref，让工作台刷新能反映最新编辑(与导出口径一致)，而不是
    一直显示 checkpoint 里的旧内容。

    始终返回含 5 个静态阶段的骨架；metrics 取 ``project.metrics``。
    """
    persisted = await storage.state_store.list_node_outputs(project_id)
    # list_node_outputs 返回 AgentOutput 对象；视图契约要求 outputs 为 dict —— 归一成
    # dict（与 checkpoint 路径的 model_dump 形态一致，前端按字段存在性判别类型）。
    persisted_dicts = {
        nid: (out.model_dump(mode="json") if hasattr(out, "model_dump") else out)
        for nid, out in persisted.items()
    }
    # P2-RUNSCOPE：读「当前 run」那条 thread(= 最近 RunRef 的 run_id)，与执行侧一致。
    state = await read_native_run_state(orch, project)
    if state is None:
        # 兜底：从持久化 outputs 构造一个最小 RunState dump（history 空）。
        state = RunState(
            project_id=project_id,
            run_id=project.runs[-1].run_id if project.runs else "",
            analysis_mode=project.analysis_mode.value,
            products=[project.target_product, *project.competitors],
        ).model_dump()
        state["outputs"] = persisted_dicts
    elif persisted_dicts:
        # checkpoint 为底，持久化 node_outputs(含人工编辑)按 ref 覆盖同名 key
        state["outputs"] = {**(state.get("outputs") or {}), **persisted_dicts}
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
        "qa_round": sum(1 for v in snapshot.verdicts) - 1 if len(snapshot.verdicts) > 1 else 0,
        "aborted": snapshot.final_status == "aborted",
        "abort_reason": "",
    }
    return run_state_to_view(state, project=project, metrics=snapshot.metrics)
