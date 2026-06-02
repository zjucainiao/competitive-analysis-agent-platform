"""Orchestrator —— DAG 编排主类。

负责把 ``Planner`` / ``Executor`` / ``FeedbackRouter`` 接到 LangGraph
``StateGraph``，并通过 ``backend.storage`` 持久化 checkpoint 与节点状态。

LangGraph 图的形状（v1 固定）::

    START
      ↓
    dispatch ←──┐
      ↓         │ should_continue: 还有 PENDING/RUNNING → loop
      └────────→┘
        should_continue: 全部 SUCCESS/FAILED/SKIPPED → END
      ↓
    END

``dispatch`` 节点一轮做的事：

1. 取所有 READY 节点（``status=PENDING`` 且 input_refs 全部 SUCCESS）
2. 并发上限内 ``asyncio.gather`` 执行
3. 把结果写回 ``outputs`` 与 ``plan.nodes[i].status``
4. 如果有 QA 节点完成且 verdict.routing 非空且未阻断 → 调用 ``FeedbackRouter``
   生成新节点 / 重置下游
5. 落 ``state_store.save_node_output`` + 广播 ``event_bus.publish``
6. 返回 state 增量

LangGraph 的 checkpoint 会在每轮 dispatch 后自动落盘；崩溃可由 ``resume()``
从最近 checkpoint 继续。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from langgraph.graph import END, START, StateGraph

from backend.schemas import (
    AgentOutputBase,
    DAGEdge,
    DAGNode,
    DAGPlan,
    NodeExecutionResult,
    NodeStatus,
    NodeType,
    Project,
    QAOutput,
    QAVerdict,
)
from backend.storage import Storage
from backend.storage.langgraph_adapter import to_langgraph_saver

from .agent_registry import AgentRegistry
from .executor import Executor
from .feedback_router import FeedbackOutcome, FeedbackRouter
from .planner import Planner
from .state import OrchestratorState

_DEFAULT_MAX_PARALLEL = 4

_TERMINAL_STATUSES = frozenset(
    [NodeStatus.SUCCESS, NodeStatus.FAILED, NodeStatus.SKIPPED]
)


class Orchestrator:
    """DAG 编排主类。"""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        storage: Storage,
        planner: Planner | None = None,
        feedback_router: FeedbackRouter | None = None,
        max_parallel: int = _DEFAULT_MAX_PARALLEL,
    ) -> None:
        self.registry = registry
        self.storage = storage
        self.planner = planner or Planner()
        self.feedback_router = feedback_router or FeedbackRouter()
        self.max_parallel = max_parallel

    # ----- 公开 API -----

    def plan(self, project: Project, *, template_id: str | None = None) -> DAGPlan:
        return self.planner.plan(project, template_id=template_id)

    async def run(
        self,
        plan: DAGPlan,
        project: Project,
    ) -> AsyncIterator[NodeExecutionResult]:
        """执行 DAG，每个节点完成时 yield 一个 ``NodeExecutionResult``。

        持久化语义：
        - DAGPlan 一次 ``save_dag_plan``
        - 每个节点完成后 ``save_node_output`` + ``publish(project:{pid}:nodes, result)``
        - LangGraph 自动 checkpoint state（thread_id=project_id）
        """
        await self.storage.state_store.save_dag_plan(plan)

        executor = Executor(registry=self.registry, project=project)
        compiled = self._build_compiled_graph(executor, project)

        initial = OrchestratorState(project_id=project.project_id, plan=plan)
        config = {"configurable": {"thread_id": project.project_id}}

        async for snap in compiled.astream(
            initial.model_dump(), config=config, stream_mode="values"
        ):
            # LangGraph 0.2+：stream_mode="values" 给出整个 state 字典快照
            state = OrchestratorState.model_validate(snap)
            for result in state.last_batch_results:
                yield result

    async def resume(
        self, project_id: str, project: Project
    ) -> AsyncIterator[NodeExecutionResult]:
        """从 checkpoint 继续。"""
        plan = await self.storage.state_store.get_dag_plan(project_id)
        if plan is None:
            raise ValueError(f"no DAGPlan persisted for project {project_id!r}")

        executor = Executor(registry=self.registry, project=project)
        compiled = self._build_compiled_graph(executor, project)
        config = {"configurable": {"thread_id": project_id}}

        # 传 None 让 LangGraph 从 checkpoint 加载
        async for snap in compiled.astream(None, config=config, stream_mode="values"):
            state = OrchestratorState.model_validate(snap)
            for result in state.last_batch_results:
                yield result

    # ----- LangGraph 构建 -----

    def _build_compiled_graph(self, executor: Executor, project: Project) -> Any:
        builder = StateGraph(OrchestratorState)

        async def dispatch_node(state: OrchestratorState) -> dict[str, Any]:
            return await self._dispatch_step(state, executor, project)

        def should_continue(state: OrchestratorState) -> str:
            if state.aborted:
                return "done"
            if _all_terminal(state.plan):
                return "done"
            return "continue"

        builder.add_node("dispatch", dispatch_node)
        builder.add_edge(START, "dispatch")
        builder.add_conditional_edges(
            "dispatch",
            should_continue,
            {"continue": "dispatch", "done": END},
        )

        saver = to_langgraph_saver(self.storage.checkpointer)
        return builder.compile(checkpointer=saver)

    # ----- dispatch 实现 -----

    async def _dispatch_step(
        self,
        state: OrchestratorState,
        executor: Executor,
        project: Project,
    ) -> dict[str, Any]:
        plan = state.plan
        outputs = dict(state.outputs)
        verdict_history = list(state.verdict_history)
        qa_feedback_by_node = dict(state.qa_feedback_by_node)
        qa_round_count = state.qa_round_count

        # 1. 找 READY 节点
        ready = _find_ready_nodes(plan, outputs)
        if not ready:
            return {
                "aborted": True,
                "abort_reason": (
                    "no ready nodes but DAG not terminal (deadlock or all failed)"
                ),
                "last_batch_results": [],
            }

        # 2. 并发上限
        batch = ready[: self.max_parallel]

        # 3. 并发执行
        results: list[NodeExecutionResult] = await asyncio.gather(
            *[
                executor.execute(
                    node,
                    outputs,
                    qa_feedback=qa_feedback_by_node.get(node.node_id),
                )
                for node in batch
            ]
        )

        # 4. 应用结果到 plan + outputs
        new_plan = _apply_node_results(plan, results)
        for r in results:
            if r.output is not None:
                outputs[r.node_id] = r.output

        # 5. 持久化 + 广播（顺序：先落盘后广播，避免 WS 拿到没存的引用）
        channel = f"project:{project.project_id}:nodes"
        for r in results:
            if r.output is not None:
                await self.storage.state_store.save_node_output(
                    project.project_id, r.node_id, r.output
                )
            await self.storage.state_store.update_node_status(
                project.project_id, r.node_id, r.status
            )
            await self.storage.event_bus.publish(channel, r)

        # 6. QA 反馈路由
        new_plan, qa_feedback_by_node, verdict_history, qa_round_count = (
            await self._process_qa_routing(
                results=results,
                plan=new_plan,
                outputs=outputs,
                verdict_history=verdict_history,
                qa_feedback_by_node=qa_feedback_by_node,
                qa_round_count=qa_round_count,
                project=project,
            )
        )

        # 7. 落最新 plan
        await self.storage.state_store.save_dag_plan(new_plan)

        # 8. 终态时算 ProjectMetrics 并写回 Project.metrics（v1 跑完一次的指标快照）
        if _all_terminal(new_plan):
            await self._persist_metrics(
                project=project,
                plan=new_plan,
                outputs=outputs,
                verdicts=verdict_history,
                qa_round_count=qa_round_count,
            )

        return {
            "plan": new_plan,
            "outputs": outputs,
            "qa_feedback_by_node": qa_feedback_by_node,
            "verdict_history": verdict_history,
            "qa_round_count": qa_round_count,
            "last_batch_results": results,
        }

    async def _persist_metrics(
        self,
        *,
        project: Project,
        plan: DAGPlan,
        outputs: dict[str, AgentOutputBase],
        verdicts: list[QAVerdict],
        qa_round_count: int,
    ) -> None:
        """终态时把 ProjectMetrics 写回 storage + 追加到 metrics_history（sparkline 用）。"""
        from datetime import datetime, timezone

        from backend.schemas.project import ProjectMetricsSnapshot

        from .metrics import compute_project_metrics

        # 拿最新 project（manual_edits 可能被 PATCH 路径增量过，不能用入参的 stale 副本）
        latest_project = await self.storage.state_store.get_project(project.project_id)
        base_project = latest_project or project

        metrics = compute_project_metrics(
            plan=plan,
            outputs=outputs,
            verdicts=verdicts,
            qa_round_count=qa_round_count,
        )
        # 保留 PATCH 路径累加的 manual_edits/edit_rate（compute_project_metrics 算的是 0）
        if base_project.metrics is not None:
            metrics = metrics.model_copy(
                update={
                    "manual_edits": base_project.metrics.manual_edits,
                    "edit_rate": base_project.metrics.edit_rate,
                }
            )

        snapshot = ProjectMetricsSnapshot(
            captured_at=datetime.now(timezone.utc), metrics=metrics
        )
        new_history = list(base_project.metrics_history) + [snapshot]
        updated = base_project.model_copy(
            update={"metrics": metrics, "metrics_history": new_history}
        )
        await self.storage.state_store.save_project(updated)

    async def _process_qa_routing(
        self,
        *,
        results: list[NodeExecutionResult],
        plan: DAGPlan,
        outputs: dict[str, AgentOutputBase],
        verdict_history: list[QAVerdict],
        qa_feedback_by_node: dict[str, dict],
        qa_round_count: int,
        project: Project,
    ) -> tuple[DAGPlan, dict[str, dict], list[QAVerdict], int]:
        """检查本轮 results 里是否有 QA 完成，若有 routing 则派生 _v 节点。"""
        for r in results:
            output = r.output
            if not isinstance(output, QAOutput):
                continue
            verdict = output.verdict
            verdict_history.append(verdict)
            await self.storage.state_store.save_qa_verdict(project.project_id, verdict)

            if not verdict.routing:
                continue
            if verdict.blocking is False:
                # 软退出：不阻断，记录但不重做
                continue

            outcome: FeedbackOutcome = self.feedback_router.apply(
                verdict=verdict,
                plan=plan,
                qa_round_count=qa_round_count,
            )
            if outcome.aborted:
                # 触顶后不再重做，由 should_continue 在下一轮判定 DAG 终止
                # 同时把 qa_round_count 提到 max，防止后续仍 routing
                qa_round_count = max(
                    qa_round_count, self.feedback_router.max_rounds
                )
                continue

            plan = _apply_feedback_outcome(plan, outcome)
            qa_feedback_by_node.update(outcome.qa_feedback_by_node)
            qa_round_count += 1

        return plan, qa_feedback_by_node, verdict_history, qa_round_count


# ---------- 纯函数：plan 调度逻辑 ----------


def _find_ready_nodes(
    plan: DAGPlan, outputs: dict[str, AgentOutputBase]
) -> list[DAGNode]:
    """status=PENDING 且所有 input_refs 均为 SUCCESS / SKIPPED 的节点。"""
    by_id = {n.node_id: n for n in plan.nodes}
    ready: list[DAGNode] = []
    for node in plan.nodes:
        if node.status != NodeStatus.PENDING:
            continue
        deps_ok = True
        for ref in node.input_refs:
            upstream = by_id.get(ref)
            if upstream is None:
                deps_ok = False
                break
            if upstream.status not in (NodeStatus.SUCCESS, NodeStatus.SKIPPED):
                deps_ok = False
                break
        if deps_ok:
            ready.append(node)
    return ready


def _all_terminal(plan: DAGPlan) -> bool:
    return all(n.status in _TERMINAL_STATUSES for n in plan.nodes)


def _apply_node_results(
    plan: DAGPlan, results: list[NodeExecutionResult]
) -> DAGPlan:
    """把一批 NodeExecutionResult 应用到 plan.nodes 的状态字段。"""
    results_by_id = {r.node_id: r for r in results}
    new_nodes: list[DAGNode] = []
    for node in plan.nodes:
        r = results_by_id.get(node.node_id)
        if r is None:
            new_nodes.append(node)
            continue
        new_nodes.append(
            node.model_copy(
                update={
                    "status": r.status,
                    "started_at": r.started_at,
                    "ended_at": r.ended_at,
                }
            )
        )
    return plan.model_copy(update={"nodes": new_nodes})


def _apply_feedback_outcome(plan: DAGPlan, outcome: FeedbackOutcome) -> DAGPlan:
    """把 FeedbackOutcome 应用到 plan：追加新节点 / 边，重置下游状态 / input_refs。"""
    new_nodes: list[DAGNode] = []
    for node in plan.nodes:
        updates: dict[str, Any] = {}
        if node.node_id in outcome.node_status_resets:
            updates["status"] = outcome.node_status_resets[node.node_id]
            updates["retry_count"] = 0
            updates["started_at"] = None
            updates["ended_at"] = None
        if node.node_id in outcome.node_input_refs_updates:
            updates["input_refs"] = outcome.node_input_refs_updates[node.node_id]
        new_nodes.append(node.model_copy(update=updates) if updates else node)

    new_nodes.extend(outcome.new_nodes)
    new_edges: list[DAGEdge] = list(plan.edges) + list(outcome.new_edges)
    return plan.model_copy(update={"nodes": new_nodes, "edges": new_edges})


__all__ = ["Orchestrator"]
