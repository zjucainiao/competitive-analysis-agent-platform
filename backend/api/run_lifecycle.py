"""后台 run 的共享生命周期设施（P1 修复）。

动机：原本只有 ``start_run`` 的后台任务在结束时回写 ``RunRef.final_status``
并落 ``RunSnapshot``；restart / retry / edit-prompt / evidence auto-rework 的
后台任务只改 project status——这些路径发起的 run 的 RunRef 永远
``final_status=None``、无快照，``/runs/{id}/state`` 与 ``/view`` 对它们 404。

本模块把收尾逻辑抽成共享函数，所有发起后台 run 的路径统一走
``drive_run_to_completion``，维持不变式：

    每个真实执行的 run 都有 RunRef + 终态（final_status/ended_at）+ RunSnapshot。

同时收纳 run 控制面的单进程守卫（``ensure_single_worker``）：
``running_tasks`` dict 与 LLM-call 环形缓冲都是进程内状态，多 worker 部署下
409 防重 / stop / pause 会静默失效，启动期直接拒启并说明约束。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from backend.orchestrator import Orchestrator
from backend.orchestrator.run_state import RunState
from backend.schemas import Project, ProjectStatus, RunSnapshot
from backend.storage import Storage
from backend.storage.serde import dump_output

_log = logging.getLogger(__name__)


def is_native_engine() -> bool:
    """当前是否走原生 LangGraph 引擎（与 Orchestrator.run 同判据）。"""
    return os.getenv("ORCH_ENGINE", "native") == "native"


# ============================================================
# native checkpoint 只读（LIVE 视图 / 快照 history 共用）
# ============================================================


async def read_native_run_state(
    orch: Orchestrator, project: Project, *, run_id: str | None = None
) -> dict | None:
    """从 LangGraph checkpoint 读取该项目某次 native run 的 RunState。

    重建同一张 native 图（挂同一 checkpointer）后 ``aget_state`` 只读 checkpoint、
    **不**触发任何节点执行；无 checkpoint（从未跑过 native）时返回 None。

    ``run_id`` 显式指定要读的 run（收尾落快照用，保证读的是刚结束的那次）；
    缺省取最近一条 RunRef（LIVE 视图语义，P2-RUNSCOPE）。

    返回 ``RunState.model_dump()``（history/verdicts 归一为 dict，outputs 仍是
    AgentOutput 对象 —— 装配器只取其 metric 字段，对象/dict 皆可）。
    """
    from backend.orchestrator.graph import build_native_graph
    from backend.orchestrator.orchestrator import native_thread_config
    from backend.storage.langgraph_adapter import to_langgraph_saver

    try:
        graph = build_native_graph(
            orch.registry,
            project=project,
            checkpointer=to_langgraph_saver(orch.storage.checkpointer),
        )
        if run_id is None:
            run_id = project.runs[-1].run_id if project.runs else None
        config = native_thread_config(project.project_id, run_id)
        snapshot = await graph.aget_state(config)
    except Exception:
        _log.exception("read native checkpoint failed project=%s", project.project_id)
        return None

    values: dict[str, Any] = getattr(snapshot, "values", None) or {}
    if not values:
        return None
    # values 里 history 是 NodeRun 对象、outputs 是 AgentOutput 对象；
    # model_validate→model_dump 归一为装配器契约要求的 dict 形态。
    try:
        return RunState.model_validate(values).model_dump()
    except Exception:
        # 兼容极端情况：直接退回原始 values（装配器对 dict/对象都健壮）。
        return values


async def read_native_run_history(
    orch: Orchestrator, project: Project, *, run_id: str | None = None
) -> list[dict]:
    """读取 native checkpoint 里 RunState.history 的 dict 列表（供 RunSnapshot 落库）。

    无 checkpoint 或读取失败时返回空列表（best-effort，不阻塞快照持久化）。
    """
    state = await read_native_run_state(orch, project, run_id=run_id)
    if not state:
        return []
    return list(state.get("history", []))


# ============================================================
# 共享收尾：RunRef 终态回写 + RunSnapshot 落库
# ============================================================


async def finalize_run(
    *,
    storage: Storage,
    orch: Orchestrator,
    project: Project,
    run_id: str | None,
    final_status: str,
) -> None:
    """run 结束后的统一收尾（原 start_run 内联逻辑抽出）。

    1. 回写该 RunRef 的 ``ended_at`` + ``final_status``，并把 project.status
       置 DONE/FAILED（一次 save，避免两写间隙的半更新状态）。
    2. 落不可变 ``RunSnapshot``（按 (project_id, run_id) 主键；evidence
       auto-rework 延续同一 run 身份时按同主键覆盖为最新终态）。
       快照失败只记日志（non-fatal），不影响终态回写。

    ``run_id=None``（历史遗留：无 RunRef 的 resume 等）时退化为只写 project
    status——没有 run 身份就无从回写 RunRef/快照。
    ``project`` 是发起该 run 时的项目快照，读 native history 时用它重建图。
    """
    project_id = project.project_id
    latest = await storage.state_store.get_project(project_id)
    if latest is None:
        return

    updated_runs = []
    for r in latest.runs:
        if run_id is not None and r.run_id == run_id:
            updated_runs.append(
                r.model_copy(
                    update={
                        "ended_at": datetime.now(UTC),
                        "final_status": final_status,
                    }
                )
            )
        else:
            updated_runs.append(r)
    new_status = ProjectStatus.DONE if final_status == "done" else ProjectStatus.FAILED
    await storage.state_store.save_project(
        latest.model_copy(update={"runs": updated_runs, "status": new_status})
    )

    if run_id is None:
        return

    # 落不可变 RunSnapshot（按 (project_id, run_id) 主键）。
    # 与 latest 状态独立：之后再启动新 run 不会污染这份历史。
    try:
        final_plan = await storage.state_store.get_dag_plan(project_id)
        final_outputs = await storage.state_store.list_node_outputs(project_id)
        # 翻成升序(round1..N)落快照：与 LIVE 视图(RunState.verdicts 为 append 升序)
        # 一致，且 best_round / 回放按轮次升序理解(P1P2-VERDICT-ORDER)。
        final_verdicts = list(reversed(await storage.state_store.list_qa_verdicts(project_id)))
        # native 引擎：从 checkpoint 取最终 history（回放真相源），落进快照。
        # legacy 引擎无 native RunState，history 保持空（向后兼容）。
        history: list[dict] = []
        if is_native_engine():
            try:
                history = await read_native_run_history(orch, project, run_id=run_id)
            except Exception:
                _log.exception(
                    "read native history for snapshot failed project=%s",
                    project_id,
                )
        if final_plan is not None:
            snapshot = RunSnapshot(
                project_id=project_id,
                run_id=run_id,
                captured_at=datetime.now(UTC),
                plan=final_plan,
                outputs={nid: dump_output(out) for nid, out in final_outputs.items()},
                verdicts=final_verdicts,
                metrics=latest.metrics,
                final_status=final_status,
                history=history,
            )
            await storage.state_store.save_run_snapshot(snapshot)
    except Exception:
        _log.exception(
            "save_run_snapshot failed (non-fatal) project=%s run=%s",
            project_id,
            run_id,
        )


async def drive_run_to_completion(
    run_stream: AsyncIterator[Any],
    *,
    storage: Storage,
    orch: Orchestrator,
    project: Project,
    run_id: str | None,
    label: str,
) -> None:
    """后台 run 任务的统一主体：耗尽执行流 → 共享收尾。

    所有发起后台 run 的路径（start / restart / retry / edit-prompt /
    evidence auto-rework / resume）都用它，收尾语义只此一份。

    异常兜底：执行流抛任何 Exception 都记日志并按 failed 收尾——RunRef
    仍会拿到终态与快照。``asyncio.CancelledError``（stop / pause / 被新 run
    顶替）**不**在此收尾：run 未自然终结，其语义由触发取消的一方负责
    （与修复前 start_run 的行为一致，不破坏刚合入的超时/收尾语义）。
    """
    final_status = "done"
    try:
        async for _ in run_stream:
            pass
    except Exception:
        _log.exception("%s run failed project=%s run=%s", label, project.project_id, run_id)
        final_status = "failed"
    await finalize_run(
        storage=storage,
        orch=orch,
        project=project,
        run_id=run_id,
        final_status=final_status,
    )


# ============================================================
# 单进程守卫：多 worker 部署迹象 → 启动即拒
# ============================================================

_MULTI_WORKER_HINT = (
    "检测到多 worker 部署配置（{found}）：本服务的 run 控制面"
    "（running_tasks 防重复启动 / stop / pause）与 LLM 调用实时环形缓冲"
    "都是进程内状态，多 worker 下这些能力会静默失效"
    "（409 防重形同虚设、stop/pause 可能打到不持有该 run 的进程）。"
    "请以单 worker 运行（uvicorn --workers 1，与 Dockerfile.backend 默认一致）；"
    "横向扩展需要分布式 run 状态，属 v2 工程，详见 docs/DEPLOY_PROD.md。"
)


def ensure_single_worker() -> None:
    """启动期闸门：常见多 worker 环境变量 >1 即拒启（create_app 的 lifespan 调用）。

    进程内无法可靠感知兄弟 worker 的存在，只能在启动时检查部署配置的常见
    信号（uvicorn 读 WEB_CONCURRENCY，gunicorn 读 GUNICORN_CMD_ARGS 等）。
    检测不到时不拦——这是尽力而为的护栏，约束本身写在部署文档与 Dockerfile。
    """
    found: list[str] = []
    for var in ("UVICORN_WORKERS", "WEB_CONCURRENCY", "GUNICORN_WORKERS"):
        raw = os.getenv(var, "").strip()
        if raw.isdigit() and int(raw) > 1:
            found.append(f"{var}={raw}")
    gunicorn_args = os.getenv("GUNICORN_CMD_ARGS", "")
    m = re.search(r"(?:--workers|-w)[=\s]+(\d+)", gunicorn_args)
    if m and int(m.group(1)) > 1:
        found.append(f"GUNICORN_CMD_ARGS 含 workers={m.group(1)}")
    if found:
        raise RuntimeError(_MULTI_WORKER_HINT.format(found=", ".join(found)))


__all__ = [
    "drive_run_to_completion",
    "ensure_single_worker",
    "finalize_run",
    "is_native_engine",
    "read_native_run_history",
    "read_native_run_state",
]
