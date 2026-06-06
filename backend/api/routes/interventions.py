"""人工介入接口集中点。

涵盖：
- QA Override（接受当前 reporter 版本，停止反馈环）
- 节点级动作：retry / skip / force-start / edit-prompt 重跑
- Run 控制：pause / stop / restart
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.deps import get_orchestrator, get_owned_project, get_storage
from backend.orchestrator import Orchestrator
from backend.schemas import (
    DAGNode,
    NodeStatus,
    NodeType,
    ProjectMetrics,
    ProjectStatus,
    ReporterOutput,
)
from backend.storage import Storage

# 本路由所有端点都是 /projects/{project_id}/...，统一在路由级强制"已登录 + 属于本人"
# （get_owned_project 从路径取 project_id，做 401/403/404）。
router = APIRouter(tags=["interventions"], dependencies=[Depends(get_owned_project)])

_log = logging.getLogger(__name__)


# ============================================================
# 工具：定位 / 修改 plan 节点
# ============================================================


async def _load_plan_or_404(storage: Storage, project_id: str):
    plan = await storage.state_store.get_dag_plan(project_id)
    if plan is None:
        raise HTTPException(
            status_code=404, detail=f"plan for project {project_id!r} not found"
        )
    return plan


async def _cancel_running_task(request: Request, project_id: str) -> bool:
    """取消后台 run 任务（如果在跑）；返回是否真的取消了。"""
    running: dict[str, asyncio.Task] = request.app.state.running_tasks
    task = running.get(project_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return True
    return False


def _latest_reporter_key(outputs: dict[str, Any]) -> str:
    """outputs 里取 revision 最高的 reporter 节点 id。"""
    versioned = sorted(
        (k for k in outputs if k.startswith("reporter_v")),
        reverse=True,
    )
    return versioned[0] if versioned else "reporter"


def _count_paragraphs(reporter_out: ReporterOutput) -> int:
    if not reporter_out or not reporter_out.draft:
        return 0
    return sum(len(s.paragraphs) for s in reporter_out.draft.sections)


def _bump_manual_edits(
    project, total_paragraphs: int, increment: int = 1
) -> ProjectMetrics:
    metrics = project.metrics or ProjectMetrics()
    new_edits = metrics.manual_edits + increment
    new_rate = min(new_edits / max(total_paragraphs, 1), 1.0) if total_paragraphs else 0.0
    return metrics.model_copy(update={"manual_edits": new_edits, "edit_rate": new_rate})


# ============================================================
# QA Override
# ============================================================


class QAOverrideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    accepted_report_node_id: str
    skipped_node_ids: list[str]
    manual_edits: int
    edit_rate: float
    overridden_verdict_id: str | None = None


@router.post(
    "/projects/{project_id}/override", response_model=QAOverrideResponse, tags=["qa"]
)
async def override_qa(
    project_id: str,
    request: Request,
    storage: Storage = Depends(get_storage),
) -> QAOverrideResponse:
    """用户接受当前 reporter 版本，停掉反馈环。

    动作：
    1. 取消后台 run task（如还在跑）
    2. plan 中所有 PENDING 节点 → SKIPPED
    3. 最新 QA verdict 的 blocking 改 False，重存一份
    4. Project.metrics.manual_edits +1，重算 edit_rate
    5. Project.status → DONE
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    await _cancel_running_task(request, project_id)

    plan = await _load_plan_or_404(storage, project_id)
    skipped_ids: list[str] = []
    new_nodes = []
    for node in plan.nodes:
        if node.status == NodeStatus.PENDING:
            new_nodes.append(node.model_copy(update={"status": NodeStatus.SKIPPED}))
            skipped_ids.append(node.node_id)
        else:
            new_nodes.append(node)
    await storage.state_store.save_dag_plan(plan.model_copy(update={"nodes": new_nodes}))

    overridden_verdict_id: str | None = None
    verdicts = await storage.state_store.list_qa_verdicts(project_id)
    if verdicts:
        last = verdicts[-1]
        overridden = last.model_copy(update={"blocking": False})
        await storage.state_store.save_qa_verdict(project_id, overridden)
        overridden_verdict_id = last.verdict_id

    outputs = await storage.state_store.list_node_outputs(project_id)
    final_reporter_key = _latest_reporter_key(outputs)
    reporter_out = outputs.get(final_reporter_key)
    total_paragraphs = _count_paragraphs(reporter_out) if isinstance(reporter_out, ReporterOutput) else 0
    new_metrics = _bump_manual_edits(project, total_paragraphs)

    updated = project.model_copy(
        update={"status": ProjectStatus.DONE, "metrics": new_metrics}
    )
    await storage.state_store.save_project(updated)

    return QAOverrideResponse(
        project_id=project_id,
        accepted_report_node_id=final_reporter_key,
        skipped_node_ids=skipped_ids,
        manual_edits=new_metrics.manual_edits,
        edit_rate=new_metrics.edit_rate,
        overridden_verdict_id=overridden_verdict_id,
    )


# ============================================================
# 节点级动作
# ============================================================


class NodeActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    node_id: str
    new_status: NodeStatus
    affected_downstream: list[str] = []


def _reset_downstream(plan, root_node_id: str) -> tuple[list, list[str]]:
    """把 root_node_id 的所有传递下游（按 edges 走）重置为 PENDING。

    返回 (新 nodes 列表, 重置的 node_id 列表)。
    """
    adj: dict[str, list[str]] = {}
    for edge in plan.edges:
        adj.setdefault(edge.from_node, []).append(edge.to_node)

    visited: set[str] = set()
    queue = list(adj.get(root_node_id, []))
    while queue:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        queue.extend(adj.get(cur, []))

    new_nodes = []
    for node in plan.nodes:
        if node.node_id in visited:
            new_nodes.append(
                node.model_copy(
                    update={
                        "status": NodeStatus.PENDING,
                        "retry_count": 0,
                        "started_at": None,
                        "ended_at": None,
                    }
                )
            )
        else:
            new_nodes.append(node)
    return new_nodes, sorted(visited)


@router.post(
    "/projects/{project_id}/nodes/{node_id}/retry",
    response_model=NodeActionResponse,
    tags=["node-actions"],
)
async def retry_node(
    project_id: str,
    node_id: str,
    request: Request,
    storage: Storage = Depends(get_storage),
) -> NodeActionResponse:
    """重跑节点：把节点状态置回 PENDING + 重置所有传递下游为 PENDING。

    需要先停掉 in-flight run task；前端可立刻触发 POST /run 重新启动调度。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    await _cancel_running_task(request, project_id)

    plan = await _load_plan_or_404(storage, project_id)
    target = next((n for n in plan.nodes if n.node_id == node_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")

    new_nodes, downstream = _reset_downstream(plan, node_id)
    new_nodes = [
        n.model_copy(
            update={
                "status": NodeStatus.PENDING,
                "retry_count": 0,
                "started_at": None,
                "ended_at": None,
            }
        )
        if n.node_id == node_id
        else n
        for n in new_nodes
    ]
    await storage.state_store.save_dag_plan(plan.model_copy(update={"nodes": new_nodes}))

    return NodeActionResponse(
        project_id=project_id,
        node_id=node_id,
        new_status=NodeStatus.PENDING,
        affected_downstream=downstream,
    )


@router.post(
    "/projects/{project_id}/nodes/{node_id}/skip",
    response_model=NodeActionResponse,
    tags=["node-actions"],
)
async def skip_node(
    project_id: str,
    node_id: str,
    storage: Storage = Depends(get_storage),
) -> NodeActionResponse:
    """跳过节点：标 SKIPPED；下游若已 SUCCESS 不动，若 PENDING 保持（dispatch
    时会把跳过的当 SKIPPED 通过 deps 检查）。"""
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    plan = await _load_plan_or_404(storage, project_id)
    if not any(n.node_id == node_id for n in plan.nodes):
        raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")

    new_nodes = [
        n.model_copy(update={"status": NodeStatus.SKIPPED}) if n.node_id == node_id else n
        for n in plan.nodes
    ]
    await storage.state_store.save_dag_plan(plan.model_copy(update={"nodes": new_nodes}))

    return NodeActionResponse(
        project_id=project_id, node_id=node_id, new_status=NodeStatus.SKIPPED,
    )


@router.post(
    "/projects/{project_id}/nodes/{node_id}/force-start",
    response_model=NodeActionResponse,
    tags=["node-actions"],
)
async def force_start_node(
    project_id: str,
    node_id: str,
    storage: Storage = Depends(get_storage),
) -> NodeActionResponse:
    """强制启动 PENDING 节点：标 READY（dispatch 立刻调度它，跳过 deps 检查）。

    实际实现：把节点状态从 PENDING 改 READY，并把它所有 input_refs 中 PENDING 的
    上游标为 SKIPPED（让 _find_ready_nodes 的 deps 检查能过）。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    plan = await _load_plan_or_404(storage, project_id)
    target = next((n for n in plan.nodes if n.node_id == node_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")
    if target.status != NodeStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"node {node_id!r} status={target.status.value} (need PENDING)",
        )

    upstream_ids = set(target.input_refs)
    new_nodes = []
    for node in plan.nodes:
        if node.node_id == node_id:
            new_nodes.append(node.model_copy(update={"status": NodeStatus.READY}))
        elif node.node_id in upstream_ids and node.status not in (
            NodeStatus.SUCCESS,
            NodeStatus.SKIPPED,
        ):
            new_nodes.append(node.model_copy(update={"status": NodeStatus.SKIPPED}))
        else:
            new_nodes.append(node)
    await storage.state_store.save_dag_plan(plan.model_copy(update={"nodes": new_nodes}))

    return NodeActionResponse(
        project_id=project_id, node_id=node_id, new_status=NodeStatus.READY,
    )


class EditPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_override: str = Field(
        min_length=10,
        description=(
            "替换默认 prompt 的用户文本。会作为 qa_feedback.user_prompt 注入到节点 metadata，"
            "Agent 在 _run 时如果识别该字段会优先使用。"
        ),
    )


@router.post(
    "/projects/{project_id}/nodes/{node_id}/edit-prompt",
    response_model=NodeActionResponse,
    tags=["node-actions"],
)
async def edit_prompt_and_rerun(
    project_id: str,
    node_id: str,
    req: EditPromptRequest,
    request: Request,
    storage: Storage = Depends(get_storage),
) -> NodeActionResponse:
    """用户覆盖该节点默认 prompt 后重跑。

    实现：往该节点 metadata 写入 `user_prompt_override`，然后等同 retry。
    Agent 内部是否真消费此字段取决于各 Agent 实现（v1 由 BaseAgent 在 invoke 时
    把它 merge 进 qa_feedback 字段透传，Agent prompt 模板里读取 ``{user_prompt_override}``）。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    await _cancel_running_task(request, project_id)

    plan = await _load_plan_or_404(storage, project_id)
    target = next((n for n in plan.nodes if n.node_id == node_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")

    new_nodes, downstream = _reset_downstream(plan, node_id)
    final_nodes = []
    for n in new_nodes:
        if n.node_id == node_id:
            new_meta = dict(n.metadata)
            new_meta["user_prompt_override"] = req.prompt_override
            final_nodes.append(
                n.model_copy(
                    update={
                        "status": NodeStatus.PENDING,
                        "retry_count": 0,
                        "started_at": None,
                        "ended_at": None,
                        "metadata": new_meta,
                    }
                )
            )
        else:
            final_nodes.append(n)
    await storage.state_store.save_dag_plan(plan.model_copy(update={"nodes": final_nodes}))

    return NodeActionResponse(
        project_id=project_id,
        node_id=node_id,
        new_status=NodeStatus.PENDING,
        affected_downstream=downstream,
    )


# ============================================================
# Run 控制
# ============================================================


class RunControlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    action: Literal["paused", "resumed", "stopped", "restarted"]
    cancelled_task: bool
    plan_status_reset: bool = False


@router.post(
    "/projects/{project_id}/runs/current/stop",
    response_model=RunControlResponse,
    tags=["run-control"],
)
async def stop_run(
    project_id: str,
    request: Request,
    storage: Storage = Depends(get_storage),
) -> RunControlResponse:
    """硬停：取消任务 + 所有 PENDING/READY/RUNNING 节点改 SKIPPED + status=FAILED。"""
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    cancelled = await _cancel_running_task(request, project_id)

    plan_reset = False
    plan = await storage.state_store.get_dag_plan(project_id)
    if plan is not None:
        new_nodes = []
        for node in plan.nodes:
            if node.status in (NodeStatus.PENDING, NodeStatus.READY, NodeStatus.RUNNING):
                new_nodes.append(node.model_copy(update={"status": NodeStatus.SKIPPED}))
                plan_reset = True
            else:
                new_nodes.append(node)
        await storage.state_store.save_dag_plan(plan.model_copy(update={"nodes": new_nodes}))

    await storage.state_store.update_project_status(project_id, ProjectStatus.FAILED)
    return RunControlResponse(
        project_id=project_id, action="stopped",
        cancelled_task=cancelled, plan_status_reset=plan_reset,
    )


@router.post(
    "/projects/{project_id}/runs/current/pause",
    response_model=RunControlResponse,
    tags=["run-control"],
)
async def pause_run(
    project_id: str,
    request: Request,
) -> RunControlResponse:
    """软暂停：取消后台任务但保留 plan（PENDING 不动）。前端可后续 POST /resume 恢复。

    注意：当前正在 RUNNING 的节点会被中断（Agent.invoke 不响应 cancel；但其后台
    协程会被取消，下次 dispatch 起的轮次自然不会触发）。
    """
    cancelled = await _cancel_running_task(request, project_id)
    return RunControlResponse(
        project_id=project_id, action="paused", cancelled_task=cancelled,
    )


@router.post(
    "/projects/{project_id}/runs/current/resume",
    response_model=RunControlResponse,
    tags=["run-control"],
)
async def resume_run(
    project_id: str,
    request: Request,
    storage: Storage = Depends(get_storage),
    orch: Orchestrator = Depends(get_orchestrator),
) -> RunControlResponse:
    """恢复：从最近一份 plan 继续调度 PENDING 节点。"""
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    running_tasks: dict[str, asyncio.Task] = request.app.state.running_tasks
    if project_id in running_tasks and not running_tasks[project_id].done():
        raise HTTPException(status_code=409, detail="run is already in progress")

    plan = await _load_plan_or_404(storage, project_id)

    async def _continue_in_bg() -> None:
        try:
            async for _ in orch.run(plan, project):
                pass
            await storage.state_store.update_project_status(project_id, ProjectStatus.DONE)
        except Exception:  # noqa: BLE001
            _log.exception("resume run failed for project_id=%s", project_id)
            await storage.state_store.update_project_status(project_id, ProjectStatus.FAILED)

    task = asyncio.create_task(_continue_in_bg(), name=f"resume-{project_id}")
    running_tasks[project_id] = task
    await storage.state_store.update_project_status(project_id, ProjectStatus.RUNNING)
    return RunControlResponse(project_id=project_id, action="resumed", cancelled_task=False)


@router.post(
    "/projects/{project_id}/runs/current/restart",
    response_model=RunControlResponse,
    tags=["run-control"],
)
async def restart_run(
    project_id: str,
    request: Request,
    storage: Storage = Depends(get_storage),
    orch: Orchestrator = Depends(get_orchestrator),
) -> RunControlResponse:
    """从头重跑：取消旧任务 + 用新 plan 起 run（旧 plan 历史保留在 storage 的旧主键下，
    不会被覆盖——save_dag_plan 按 plan_id 主键）。"""
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    cancelled = await _cancel_running_task(request, project_id)

    new_plan = orch.plan(project)
    await storage.state_store.update_project_status(project_id, ProjectStatus.RUNNING)

    async def _run_in_bg() -> None:
        try:
            async for _ in orch.run(new_plan, project):
                pass
            await storage.state_store.update_project_status(project_id, ProjectStatus.DONE)
        except Exception:  # noqa: BLE001
            _log.exception("restart run failed for project_id=%s", project_id)
            await storage.state_store.update_project_status(project_id, ProjectStatus.FAILED)

    running_tasks: dict[str, asyncio.Task] = request.app.state.running_tasks
    task = asyncio.create_task(_run_in_bg(), name=f"restart-{project_id}")
    running_tasks[project_id] = task

    return RunControlResponse(
        project_id=project_id, action="restarted",
        cancelled_task=cancelled, plan_status_reset=True,
    )
