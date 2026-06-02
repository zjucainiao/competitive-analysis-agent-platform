"""DAG 运行控制路由。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from ulid import ULID

from backend.api.deps import get_orchestrator, get_storage
from backend.api.schemas import ProjectStateResponse, RunStartedResponse
from backend.orchestrator import Orchestrator
from backend.schemas import ProjectStatus, RunSnapshot
from backend.schemas.project import RunRef
from backend.storage import Storage
from backend.storage.serde import dump_output

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
) -> RunStartedResponse:
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id!r} not found",
        )

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
) -> RunListResponse:
    """单项目所有 run 的 metadata 时间线（前端的 Rerun 按钮 / run 切换器需要）。

    每次 run 的完整 state 单独通过 ``GET /projects/{id}/runs/{run_id}/state`` 取
    （由 RunSnapshot 持久化，与 latest 状态相互独立）。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return RunListResponse(project_id=project_id, runs=list(project.runs))


@router.get("/projects/{project_id}/runs/{run_id}/state")
async def get_run_snapshot(
    project_id: str,
    run_id: str,
    storage: Storage = Depends(get_storage),
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


@router.get(
    "/projects/{project_id}/state",
    response_model=ProjectStateResponse,
)
async def get_state(
    project_id: str,
    storage: Storage = Depends(get_storage),
) -> ProjectStateResponse:
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id!r} not found",
        )
    plan = await storage.state_store.get_dag_plan(project_id)
    outputs = await storage.state_store.list_node_outputs(project_id)
    verdicts = await storage.state_store.list_qa_verdicts(project_id)
    return ProjectStateResponse(
        project=project,
        plan=plan,
        outputs=outputs,
        verdicts=verdicts,
    )
