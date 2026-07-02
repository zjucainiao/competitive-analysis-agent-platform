"""人工介入接口集中点。

涵盖：
- QA Override（接受当前 reporter 版本，停止反馈环）
- 节点级动作：retry / skip / force-start / edit-prompt 重跑
- Run 控制：pause / stop / restart
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from backend.api.deps import get_orchestrator, get_owned_project, get_storage
from backend.api.run_lifecycle import drive_run_to_completion, is_native_engine
from backend.orchestrator import Orchestrator
from backend.orchestrator.metrics import best_round_reporter_key
from backend.schemas import (
    NodeStatus,
    ProjectMetrics,
    ProjectStatus,
    ReporterOutput,
    RunRef,
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
        raise HTTPException(status_code=404, detail=f"plan for project {project_id!r} not found")
    return plan


async def _cancel_running_task(request: Request, project_id: str) -> bool:
    """取消后台 run 任务（如果在跑）；返回是否真的取消了。"""
    running: dict[str, asyncio.Task] = request.app.state.running_tasks
    task = running.get(project_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return True
    return False


async def _start_fresh_run(
    orch: Orchestrator,
    storage: Storage,
    request: Request,
    project: Any,
    *,
    seed_state: dict[str, Any] | None = None,
    task_label: str = "rerun",
) -> tuple[str, bool]:
    """新建一次 run 身份(RunRef) + 后台从头重跑，可带 ``seed_state`` 注入定向意图
    (P1-INTERVENE 的 prompt_override / P1-AUTOREWORK 的 rework 种子)。
    返回 (run_id, 是否取消了旧任务)。

    run_id 贯穿 checkpoint thread / node_outputs 作用域 / 快照主键 / live-read，与
    start_run 一致(P2-RUNSCOPE)。收尾（RunRef 终态 + RunSnapshot + 异常兜底）统一
    走共享 drive_run_to_completion——修复前这里只回写 project status，导致 restart/
    retry/edit-prompt 起的 run 永远 final_status=None、无快照(P1)。

    P1-TOCTOU：整段「取消旧任务 → 建 RunRef → create_task → 登记」持 spawn 锁，
    与 start_run 互斥，避免并发路径各起一个 run、后登记者把先登记的 task 顶成孤儿。
    """
    spawn_lock: asyncio.Lock = request.app.state.run_spawn_lock
    async with spawn_lock:
        cancelled = await _cancel_running_task(request, project.project_id)
        new_plan = orch.plan(project)
        run_id = f"run_{ULID()}"
        new_run = RunRef(
            run_id=run_id,
            plan_id=new_plan.plan_id,
            started_at=datetime.now(UTC),
            final_status=None,
        )
        project_with_run = project.model_copy(
            update={
                "runs": [*project.runs, new_run],
                "status": ProjectStatus.RUNNING,
            }
        )
        await storage.state_store.save_project(project_with_run)

        task = asyncio.create_task(
            drive_run_to_completion(
                orch.run(new_plan, project_with_run, run_id=run_id, seed_state=seed_state),
                storage=storage,
                orch=orch,
                project=project_with_run,
                run_id=run_id,
                label=task_label,
            ),
            name=f"{task_label}-{project.project_id}-{run_id}",
        )
        request.app.state.running_tasks[project.project_id] = task
    return run_id, cancelled


def _count_paragraphs(reporter_out: ReporterOutput) -> int:
    if not reporter_out or not reporter_out.draft:
        return 0
    return sum(len(s.paragraphs) for s in reporter_out.draft.sections)


def _bump_manual_edits(project, total_paragraphs: int, increment: int = 1) -> ProjectMetrics:
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


@router.post("/projects/{project_id}/override", response_model=QAOverrideResponse, tags=["qa"])
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
    # list_qa_verdicts 按 created_at DESC(最新在前)返回 → 翻成升序后 [-1] 才是最新一轮，
    # 否则会把最旧那条 verdict 当作"最新"去 override(P1P2-VERDICT-ORDER)。
    verdicts = list(reversed(await storage.state_store.list_qa_verdicts(project_id)))
    if verdicts:
        last = verdicts[-1]
        overridden = last.model_copy(update={"blocking": False})
        await storage.state_store.save_qa_verdict(project_id, overridden)
        overridden_verdict_id = last.verdict_id

    outputs = await storage.state_store.list_node_outputs(project_id)
    # 发布择优：用户接受时落定的是**历史最优轮**报告（与 markdown 导出 meta.py 同口径），
    # 而非最后一轮——避免「返工反而变差却照发最后一版」。verdicts 已按轮次升序，
    # 无 verdict / 无改善时 best_round_reporter_key 退回「最高 revision」(旧行为)。
    final_reporter_key = best_round_reporter_key(outputs, verdicts)
    reporter_out = outputs.get(final_reporter_key)
    total_paragraphs = (
        _count_paragraphs(reporter_out) if isinstance(reporter_out, ReporterOutput) else 0
    )
    new_metrics = _bump_manual_edits(project, total_paragraphs)

    updated = project.model_copy(update={"status": ProjectStatus.DONE, "metrics": new_metrics})
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
    orch: Orchestrator = Depends(get_orchestrator),
) -> NodeActionResponse:
    """重跑节点。

    native(默认)：固定 5 阶段流水线无「单节点重跑」语义，retry 直接**从头重跑**一遍
    (新 run 身份，节点真正重新执行)，避免只改 native 不消费的 DAGPlan 而按钮空转
    (P1-INTERVENE)。legacy：保留旧行为(置节点 PENDING + 重置下游，由 executor 续跑)。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    if is_native_engine():
        await _start_fresh_run(orch, storage, request, project, task_label="retry")
        return NodeActionResponse(
            project_id=project_id,
            node_id=node_id,
            new_status=NodeStatus.PENDING,
            affected_downstream=[],
        )

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

    if is_native_engine():
        # native 是固定 5 阶段流水线，无「单节点跳过」语义。诚实返回 409 而非静默改
        # 一份 native 不执行的 DAGPlan、让按钮假装成功(P1-INTERVENE)。
        raise HTTPException(
            status_code=409,
            detail=(
                "native 引擎是固定流水线，不支持单节点 skip；"
                "如需调整请用 retry 重跑或 edit-prompt 改写提示词。"
            ),
        )

    plan = await _load_plan_or_404(storage, project_id)
    if not any(n.node_id == node_id for n in plan.nodes):
        raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")

    new_nodes = [
        n.model_copy(update={"status": NodeStatus.SKIPPED}) if n.node_id == node_id else n
        for n in plan.nodes
    ]
    await storage.state_store.save_dag_plan(plan.model_copy(update={"nodes": new_nodes}))

    return NodeActionResponse(
        project_id=project_id,
        node_id=node_id,
        new_status=NodeStatus.SKIPPED,
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

    if is_native_engine():
        # 同 skip：native 固定流水线无「强制启动单节点」语义。
        raise HTTPException(
            status_code=409,
            detail=("native 引擎是固定流水线，不支持单节点 force-start；如需重跑请用 retry。"),
        )

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
        project_id=project_id,
        node_id=node_id,
        new_status=NodeStatus.READY,
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
    orch: Orchestrator = Depends(get_orchestrator),
) -> NodeActionResponse:
    """用户覆盖该节点默认 prompt 后重跑。

    native 引擎(默认)：把 override 经 ``seed_state.prompt_override_by_node`` 注入一次
    **从头重跑**的 RunState —— 对应节点跑到时由 run_agent_node 把它注入 ContextVar →
    system prompt(P1-INTERVENE：之前只写 DAGPlan.metadata，native 根本不读)。
    legacy 引擎：保留旧行为(写节点 metadata + 重置下游，由 executor 续跑时读取)。
    """
    from backend.orchestrator.run_state import split_versioned

    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    if is_native_engine():
        # node_id(投影 id，可能带 _v2)→ prompt_override_by_node 的逻辑键(去版本后缀)。
        # 例：reporter_v2→reporter、collect.Notion_v2→collect.Notion、analyst→analyst。
        override_key, _ = split_versioned(node_id)
        await _start_fresh_run(
            orch,
            storage,
            request,
            project,
            seed_state={"prompt_override_by_node": {override_key: req.prompt_override}},
            task_label="edit-prompt",
        )
        return NodeActionResponse(
            project_id=project_id,
            node_id=node_id,
            new_status=NodeStatus.PENDING,
            affected_downstream=[],
        )

    # ---- legacy 引擎：写节点 metadata + 重置下游（由 executor 续跑读取）----
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
        project_id=project_id,
        action="stopped",
        cancelled_task=cancelled,
        plan_status_reset=plan_reset,
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
        project_id=project_id,
        action="paused",
        cancelled_task=cancelled,
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

    # P1-TOCTOU：409 检查与 create_task 之间隔着 plan 加载 await，持 spawn 锁原子化
    # （与 start_run 同法），并发双击 resume 只成功一个。
    spawn_lock: asyncio.Lock = request.app.state.run_spawn_lock
    async with spawn_lock:
        running_tasks: dict[str, asyncio.Task] = request.app.state.running_tasks
        if project_id in running_tasks and not running_tasks[project_id].done():
            raise HTTPException(status_code=409, detail="run is already in progress")

        # plan 仍需加载以确认它存在（404 保护），但不再作为参数传给 resume()。
        # orch.resume() 内部按 ORCH_ENGINE 路由：
        #   native  → _resume_native(astream(None) 从 checkpoint 续跑)
        #   legacy  → 从 legacy OrchestratorState checkpoint 续跑
        # 两条路径都不需要调用方传入 plan。
        await _load_plan_or_404(storage, project_id)

        # resume 延续「当前 run」的身份（最近一条 RunRef）：结束时由共享收尾回写
        # 该 RunRef 终态并落快照——pause 中断过的 run 也满足「有终态 + 快照」不变式。
        # 无 RunRef（历史遗留数据）时退化为只回写 project status。
        resume_run_id = project.runs[-1].run_id if project.runs else None
        task = asyncio.create_task(
            drive_run_to_completion(
                orch.resume(project_id, project),
                storage=storage,
                orch=orch,
                project=project,
                run_id=resume_run_id,
                label="resume",
            ),
            name=f"resume-{project_id}",
        )
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
    不会被覆盖——save_dag_plan 按 plan_id 主键）。

    新建 run 身份(RunRef) + 共享收尾 + spawn 锁全部收敛在 _start_fresh_run
    （与 retry / edit-prompt 同一条路径，P1 收尾不变式 + P1-TOCTOU 一处修）。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    _run_id, cancelled = await _start_fresh_run(
        orch, storage, request, project, task_label="restart"
    )

    return RunControlResponse(
        project_id=project_id,
        action="restarted",
        cancelled_task=cancelled,
        plan_status_reset=True,
    )
