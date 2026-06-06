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
import logging
import os
from typing import Any, AsyncIterator

from langgraph.graph import END, START, StateGraph
from ulid import ULID

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
from backend.observability.llm_call_log import list_calls
from backend.storage import Storage
from backend.storage.langgraph_adapter import to_langgraph_saver

from .agent_registry import AgentRegistry
from .executor import Executor
from .feedback_router import FeedbackOutcome, FeedbackRouter
from .planner import Planner
from .state import OrchestratorState

_log = logging.getLogger(__name__)

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

        引擎选择由 ``ORCH_ENGINE`` 环境变量控制（默认 ``legacy``，行为不变）：
        ``native`` 时改走 ``backend.orchestrator.graph`` 的原生 LangGraph 图。
        """
        if os.getenv("ORCH_ENGINE", "legacy") == "native":
            async for r in self._run_native(plan, project):
                yield r
            return

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

    async def _run_native(
        self,
        plan: DAGPlan,
        project: Project,
    ) -> AsyncIterator[NodeExecutionResult]:
        """原生 LangGraph 引擎执行(``ORCH_ENGINE=native``)。

        与 legacy ``run`` 行为对齐的对外契约:
        - **不**落 legacy 形状的占位 plan(gap 6):legacy planner 的 node id 形如
          ``collect.notion``(小写 slug),与 native 投影的 ``collect.Notion`` 不一致,
          落它只会污染前端形状。改由跑完后的投影 plan 提供唯一正确形状。
        - 每个新出现的 ``outputs`` 引用(``collect.Notion`` / ``reporter`` 等)落一次
          ``save_node_output`` 并广播 ``project:{pid}:nodes``,同时 yield 一个
          ``NodeExecutionResult``。
        - 失败节点(output=None)亦广播一条 FAILED NodeExecutionResult(gap 1),
          避免前端一直显示"运行中"。
        - checkpoint 复用项目 checkpointer(经 ``to_langgraph_saver``),其 serde
          已能 round-trip outputs 里的 Pydantic ``AgentOutputBase``(与 legacy 同链路)。

        实际的流式落库 + 终态投影/指标/verdict 持久化逻辑抽到 ``_stream_native``,
        与 ``_resume_native``(从 checkpoint 续跑)共用(DRY)。
        """
        from .graph import build_native_graph
        from .run_state import RunState as _RunState

        graph = build_native_graph(
            self.registry,
            project=project,
            checkpointer=to_langgraph_saver(self.storage.checkpointer),
        )
        init = _RunState(
            project_id=project.project_id,
            run_id=f"run_{ULID()}",
            analysis_mode=project.analysis_mode.value,
            products=[project.target_product, *project.competitors],
        ).model_dump()
        async for res in self._stream_native(graph, init, project):
            yield res

    async def _resume_native(
        self, project_id: str, project: Project
    ) -> AsyncIterator[NodeExecutionResult]:
        """native 引擎下从 checkpoint 续跑(gap 5)。

        重建同一张 native 图(挂同一 checkpointer),以 ``astream(None, ...)`` 让
        LangGraph 从 thread_id=project_id 的最近 checkpoint 加载 RunState 续跑,
        复用 ``_stream_native`` 做与首跑一致的落库/广播/投影。
        """
        from .graph import build_native_graph

        graph = build_native_graph(
            self.registry,
            project=project,
            checkpointer=to_langgraph_saver(self.storage.checkpointer),
        )
        async for res in self._stream_native(graph, None, project):
            yield res

    async def _stream_native(
        self,
        graph: Any,
        init: dict[str, Any] | None,
        project: Project,
    ) -> AsyncIterator[NodeExecutionResult]:
        """原生图流式执行 + 持久化(首跑/续跑共用)。

        ``init`` 为 RunState dump(首跑)或 None(从 checkpoint 续跑)。

        ``outputs`` 在 ``stream_mode="values"`` 下是跨 superstep 合并后的全量 dict;
        ``seen`` dict 按对象 id 去重：同一 Python 对象不重复落库/广播/yield,
        但 QA 返工后 reporter 产出新对象时(id 不同)会重新落库,确保存储始终是最新 draft。

        ``history`` 在 values 快照里是 live ``NodeRun`` 对象列表;扫描其中
        status=="failed" 且尚未广播的项,按 projection 的 ``_node_id`` 规则派生
        node_id,广播一条 FAILED 结果(``seen_failed`` 去重)。
        """
        from .projection import _node_id, run_state_to_dagplan
        from .run_state import RunState as _RunState

        config = {"configurable": {"thread_id": project.project_id}}
        channel = f"project:{project.project_id}:nodes"

        seen: dict[str, int] = {}  # ref -> id() of last-persisted output object
        seen_failed: set[str] = set()  # 已广播过的失败节点 node_id
        final_state: dict[str, Any] | None = None
        async for snap in graph.astream(init, config=config, stream_mode="values"):
            final_state = snap
            for ref, out in snap["outputs"].items():
                if out is None:
                    continue
                if seen.get(ref) == id(out):  # already persisted THIS exact output object
                    continue
                seen[ref] = id(out)
                # 先落盘后广播：避免 WS 拿到尚未持久化的引用。
                await self.storage.state_store.save_node_output(
                    project.project_id, ref, out
                )
                res = NodeExecutionResult(
                    project_id=project.project_id,
                    node_id=ref,
                    status=NodeStatus.SUCCESS,
                    output=out,
                )
                # gap 3:顺带把该节点本轮 LLM 调用流水从 ring buffer 落库
                # (observability 永不阻塞主流程)。
                try:
                    await self._persist_node_llm_calls(project.project_id, res)
                except Exception as exc:  # noqa: BLE001
                    _log.warning("native _persist_node_llm_calls failed: %s", exc, exc_info=True)
                await self.storage.event_bus.publish(channel, res)
                yield res

            # gap 1:广播失败节点。history 里 status=="failed" 的项尚未在 outputs
            # 出现(output=None),不广播则前端永远卡"运行中"。
            for run in snap.get("history", []):
                status = getattr(run, "status", None) or (
                    run.get("status") if isinstance(run, dict) else None
                )
                if status != "failed":
                    continue
                node = getattr(run, "node", None) or (
                    run.get("node") if isinstance(run, dict) else None
                )
                product = getattr(run, "product", None) if not isinstance(
                    run, dict
                ) else run.get("product")
                round_ = getattr(run, "round", None) if not isinstance(
                    run, dict
                ) else run.get("round", 1)
                if node is None:
                    continue
                nid = _node_id(node, product, round_ or 1)
                if nid in seen_failed:
                    continue
                seen_failed.add(nid)
                fail_res = NodeExecutionResult(
                    project_id=project.project_id,
                    node_id=nid,
                    status=NodeStatus.FAILED,
                )
                # plan 此刻可能尚未落库(占位 plan 被跳过),update_node_status
                # 会 KeyError;包 try/except,纯观测不阻塞。
                try:
                    await self.storage.state_store.update_node_status(
                        project.project_id, nid, NodeStatus.FAILED
                    )
                except Exception:  # noqa: BLE001
                    pass
                await self.storage.event_bus.publish(channel, fail_res)
                yield fail_res

        # 跑完用投影 plan 覆盖(供旧 /state + 前端 Phase 2 前消费)。
        # astream 快照里 history 是 NodeRun 对象,projection 契约要的是 dict 列表,
        # 故先 model_validate→model_dump 归一化(与 projection 文档约定一致)。
        if final_state is not None:
            normalized = _RunState.model_validate(final_state).model_dump()
            proj_plan, out_map = run_state_to_dagplan(normalized, project=project)
            await self.storage.state_store.save_dag_plan(proj_plan)

            # 持久化所有 QA verdict(legacy 引擎在 _process_qa_routing 里逐条落库;
            # native 引擎在此从 final_state["verdicts"] 一次性落库)。
            # astream stream_mode="values" 下 verdicts 是累积合并列表;
            # 元素可能是 QAVerdict 对象(直接使用)或 dict(跨 serde 边界反序列化后);
            # 两种情况均兼容,确保 list_qa_verdicts 可回放全部轮次。
            raw_verdicts: list[Any] = final_state.get("verdicts", [])
            verdict_objs: list[QAVerdict] = []
            for raw in raw_verdicts:
                if isinstance(raw, QAVerdict):
                    verdict = raw
                else:
                    verdict = QAVerdict.model_validate(raw)
                verdict_objs.append(verdict)
                await self.storage.state_store.save_qa_verdict(
                    project.project_id, verdict
                )

            # gap 2:终态算 ProjectMetrics 写回 Project.metrics + 追加 metrics_history
            # (与 legacy _persist_metrics 同链路)。outputs 取 final_state["outputs"]
            # ——key 即 node_id(reporter / qa / collect.Notion …),compute_project_metrics
            # 直接消费。失败不阻塞主流程(观测层)。
            try:
                await self._persist_metrics(
                    project=project,
                    plan=proj_plan,
                    outputs=_native_outputs_for_metrics(final_state["outputs"]),
                    verdicts=verdict_objs,
                    qa_round_count=int(final_state.get("qa_round", 0) or 0),
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("native _persist_metrics failed: %s", exc, exc_info=True)

    async def resume(
        self, project_id: str, project: Project
    ) -> AsyncIterator[NodeExecutionResult]:
        """从 checkpoint 继续。

        引擎选择同 ``run``:``ORCH_ENGINE=native`` 时走 ``_resume_native``
        (从 native checkpoint 续跑),否则走 legacy OrchestratorState 图(行为不变)。
        """
        if os.getenv("ORCH_ENGINE", "legacy") == "native":
            async for r in self._resume_native(project_id, project):
                yield r
            return

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

        # 2.5 先把本批次标记 RUNNING 并落库 + 广播：让前端 DAG / Trace 在节点
        #     执行的这几分钟里显示"运行中"高亮，而不是一直 pending（看着像卡住）。
        #     纯观测，失败不阻塞主流程。
        running_channel = f"project:{project.project_id}:nodes"
        for node in batch:
            try:
                await self.storage.state_store.update_node_status(
                    project.project_id, node.node_id, NodeStatus.RUNNING
                )
                await self.storage.event_bus.publish(
                    running_channel,
                    NodeExecutionResult(
                        project_id=project.project_id,
                        node_id=node.node_id,
                        status=NodeStatus.RUNNING,
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

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
                # 顺带把该节点本轮的 LLM 调用流水从 ring buffer 落库，
                # 让 Trace tab 在进程重启后仍可查（observability 永不阻塞主流程）。
                await self._persist_node_llm_calls(project.project_id, r)
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

    async def _persist_node_llm_calls(self, project_id: str, result: Any) -> None:
        """把某节点本轮的 LLM 调用从 ring buffer 落到 state_store。

        在节点 invoke（跑在 to_thread 工作线程）结束后的 async 上下文里调用，
        此时 ring buffer 已有该节点的记录。trace_id 取自 output（与 BaseAgent
        进入时 set 的 contextvar 一致），按 node_id 精确过滤。
        失败永不抛 —— 观测层不能搞挂主流程。
        """
        out = getattr(result, "output", None)
        if out is None:
            return
        try:
            recs = list_calls(
                trace_id=getattr(out, "trace_id", None),
                node_id=getattr(result, "node_id", None),
                limit=500,
            )
            if recs:
                await self.storage.state_store.append_llm_calls(
                    project_id, [r.to_dict() for r in recs]
                )
        except Exception:  # noqa: BLE001
            pass

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


def _native_outputs_for_metrics(
    outputs: dict[str, Any],
) -> dict[str, AgentOutputBase]:
    """把 native final_state["outputs"] 归一为 ``{node_id: AgentOutputBase}``。

    剔除 None 值(失败节点)。values 流式快照里元素已是 Pydantic 对象;若个别
    元素是 dict(理论上的 serde 边界),用 ``load_output`` 还原成具体子类,
    保证 compute_project_metrics 里的 isinstance / 属性访问成立。还原失败的项
    直接跳过(指标尽力而为,不阻塞)。
    """
    from backend.storage.serde import load_output

    result: dict[str, AgentOutputBase] = {}
    for nid, out in outputs.items():
        if out is None:
            continue
        if isinstance(out, AgentOutputBase):
            result[nid] = out
        elif isinstance(out, dict):
            try:
                result[nid] = load_output(out)
            except Exception:  # noqa: BLE001
                continue
    return result


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
        update: dict[str, Any] = {
            "status": r.status,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
        }
        # 失败节点：output 为 None 时错误本会丢失。把错误写进 node.metadata，
        # 让 /state 返回、前端能显示"为什么失败"（如超时）而不是只一个红块。
        if r.status == NodeStatus.FAILED and r.error is not None:
            meta = dict(node.metadata)
            meta["error"] = {
                "code": r.error.code,
                "message": r.error.message,
                "severity": r.error.severity,
            }
            attempts = (r.metadata or {}).get("attempts")
            if attempts is not None:
                meta["attempts"] = attempts
            update["metadata"] = meta
        new_nodes.append(node.model_copy(update=update))
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
