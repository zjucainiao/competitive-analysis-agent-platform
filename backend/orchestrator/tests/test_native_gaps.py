"""Phase 2 Stage A —— native 引擎 7 gaps 的回归测试。

复用 ``test_native_graph`` 的桩 Agent / 假 registry / demo Project,验证:

- gap 7：analyst 失败时 reporter/qa fail-soft(无 KeyError,产 failed NodeRun,
  run aborted 收尾)。
- gap 1：失败 run 的结果流里含 FAILED 的 NodeExecutionResult。
- gap 2：native run 跑完 ``get_project(pid).metrics`` 非空。
- gap 3：``_persist_node_llm_calls`` 在落每个 output 时被调用(spy)。
- gap 4：QA 返工后第 2 轮 reporter 的 build_reporter_input 收到非空 qa_feedback。
- gap 5：先跑一次再 ``resume(pid, project)``(ORCH_ENGINE=native)不崩(走 native
  续跑而非 legacy OrchestratorState schema)。
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.orchestrator.tests.test_native_graph import (
    _block_reporter_verdict,
    _FakeRegistry,
    _load_demo_project,
    _pass_verdict,
    _StubQA,
)
from backend.schemas import (
    AgentError,
    AgentStatus,
    AnalysisResult,
    AnalystOutput,
    NodeStatus,
)
from backend.storage import build_storage


@pytest.fixture
def memory_storage():
    """内存三件套(state_store / event_bus / checkpointer)。"""
    return build_storage(mode="memory")


@pytest.fixture
def two_product_project():
    return _load_demo_project(products=["Notion", "Asana"])


# ---------- gap 7 / gap 1：analyst 失败 → 下游 fail-soft + 广播 FAILED ----------


class _FailingAnalyst:
    """始终返回 FAILED(无 output)的 analyst 桩,触发下游 fail-soft 路径。"""

    name = "analyst"
    mock = False

    def invoke(self, inp: Any, *, trace_id: str, span_id: str, node_id: str) -> AnalystOutput:
        return AnalystOutput(
            agent_name="analyst",
            agent_version="1.0.0",
            task_id=inp.task_id,
            trace_id=trace_id,
            span_id=span_id,
            status=AgentStatus.FAILED,
            confidence=0.0,
            self_critique="",
            result=AnalysisResult(
                target_product=inp.target_product,
                competitors=list(inp.competitors),
                dimensions={},
            ),
            errors=[
                AgentError(
                    code="AGENT_FAILED",
                    message="analyst deliberately failed",
                    severity="error",
                    retriable=False,
                )
            ],
        )


def _failing_analyst_registry() -> _FakeRegistry:
    reg = _FakeRegistry(_StubQA([_pass_verdict()]))
    reg._agents["analyst"] = _FailingAnalyst()
    return reg


@pytest.mark.asyncio
async def test_gap7_analyst_failure_failsoft(
    monkeypatch, two_product_project, memory_storage
):
    """analyst FAILED → reporter/qa 不 KeyError,run aborted 优雅收尾。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(registry=_failing_analyst_registry(), storage=memory_storage)
    plan = orch.plan(two_product_project)

    # 不应抛 KeyError;能跑完
    _results = [r async for r in orch.run(plan, two_product_project)]

    # analyst output 为 None → 不应落库 analyst/reporter/qa output
    saved = await memory_storage.state_store.list_node_outputs(
        two_product_project.project_id
    )
    assert "analyst" not in saved
    assert "reporter" not in saved
    # 投影 plan 应已落库(收尾正常)
    plan2 = await memory_storage.state_store.get_dag_plan(
        two_product_project.project_id
    )
    assert plan2 is not None


@pytest.mark.asyncio
async def test_gap1_failed_node_broadcast(
    monkeypatch, two_product_project, memory_storage
):
    """失败 run 的结果流里含 status=FAILED 的 NodeExecutionResult(广播失败节点)。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(registry=_failing_analyst_registry(), storage=memory_storage)
    plan = orch.plan(two_product_project)
    results = [r async for r in orch.run(plan, two_product_project)]

    failed = [r for r in results if r.status == NodeStatus.FAILED]
    assert failed, "expected at least one FAILED NodeExecutionResult in stream"
    # 失败节点 id 来自投影 _node_id(analyst/reporter/qa 之一)
    failed_ids = {r.node_id for r in failed}
    assert failed_ids & {"analyst", "reporter", "qa"}, (
        f"expected analyst/reporter/qa among failed ids, got {failed_ids}"
    )


# ---------- gap 2：native run 后 project.metrics 非空 ----------


@pytest.mark.asyncio
async def test_gap2_metrics_persisted(
    monkeypatch, two_product_project, memory_storage
):
    """native run 跑完,get_project(pid).metrics is not None。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    # 先把 project 落库(_persist_metrics 走 get_project → save_project)
    await memory_storage.state_store.save_project(two_product_project)

    orch = Orchestrator(
        registry=_FakeRegistry(_StubQA([_pass_verdict()])), storage=memory_storage
    )
    plan = orch.plan(two_product_project)
    _ = [r async for r in orch.run(plan, two_product_project)]

    persisted = await memory_storage.state_store.get_project(
        two_product_project.project_id
    )
    assert persisted is not None
    assert persisted.metrics is not None, "ProjectMetrics 未在 native run 后落库"
    assert len(persisted.metrics_history) >= 1


# ---------- gap 3：落每个 output 时调用 _persist_node_llm_calls(spy) ----------


@pytest.mark.asyncio
async def test_gap3_persist_llm_calls_invoked(
    monkeypatch, two_product_project, memory_storage
):
    """每个落库的 output 都会触发一次 _persist_node_llm_calls(spy 计数)。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(
        registry=_FakeRegistry(_StubQA([_pass_verdict()])), storage=memory_storage
    )

    calls: list[str] = []
    orig = orch._persist_node_llm_calls

    async def _spy(project_id: str, result: Any) -> None:
        calls.append(getattr(result, "node_id", "?"))
        await orig(project_id, result)

    monkeypatch.setattr(orch, "_persist_node_llm_calls", _spy)

    plan = orch.plan(two_product_project)
    _ = [r async for r in orch.run(plan, two_product_project)]

    # 至少 reporter / qa / collect.* / extract.* / analyst 各触发一次
    assert calls, "_persist_node_llm_calls 从未被调用"
    assert "reporter" in calls and "qa" in calls


# ---------- gap 4：QA 返工后第 2 轮 reporter 收到非空 qa_feedback ----------


@pytest.mark.asyncio
async def test_gap4_rework_reporter_receives_qa_feedback(
    monkeypatch, two_product_project, memory_storage
):
    """round-2 reporter 的 build_reporter_input 收到非空 qa_feedback。

    用 monkeypatch 包 build_reporter_input 记录每次调用的 qa_feedback 实参。
    QA 序列 [block_reporter, pass] → 第 1 次 reporter qa_feedback=None,
    第 2 次(返工)非空。
    """
    monkeypatch.setenv("ORCH_ENGINE", "native")

    import backend.orchestrator.nodes as nodes_mod

    seen_feedback: list[Any] = []
    orig_build = nodes_mod.build_reporter_input

    def _spy_build(
        project, *, trace_id, analyst_output, qa_feedback, prior_draft=None
    ):
        seen_feedback.append(qa_feedback)
        return orig_build(
            project,
            trace_id=trace_id,
            analyst_output=analyst_output,
            qa_feedback=qa_feedback,
            prior_draft=prior_draft,
        )

    monkeypatch.setattr(nodes_mod, "build_reporter_input", _spy_build)

    from backend.orchestrator.orchestrator import Orchestrator

    rework_registry = _FakeRegistry(
        _StubQA([_block_reporter_verdict(), _pass_verdict()])
    )
    orch = Orchestrator(registry=rework_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    _ = [r async for r in orch.run(plan, two_product_project)]

    assert len(seen_feedback) >= 2, (
        f"expected >=2 reporter builds (initial + rework), got {len(seen_feedback)}"
    )
    assert seen_feedback[0] is None, "首跑 reporter 不应有 qa_feedback"
    assert seen_feedback[1] is not None, (
        "返工 reporter 应收到非空 qa_feedback(gap 4 未注入)"
    )
    # payload 形如 feedback_router._build_qa_feedback_payload 产物
    assert "from_verdict_id" in seen_feedback[1]


# ---------- gap 5：resume 走 native 续跑,不崩在 OrchestratorState schema ----------


@pytest.mark.asyncio
async def test_gap5_resume_native(
    monkeypatch, two_product_project, memory_storage
):
    """先跑一次 native,再 resume(ORCH_ENGINE=native)→ 走 native 续跑不崩。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(
        registry=_FakeRegistry(_StubQA([_pass_verdict()])), storage=memory_storage
    )
    plan = orch.plan(two_product_project)
    _ = [r async for r in orch.run(plan, two_product_project)]

    # resume:run 已完成,从 checkpoint 续跑应优雅产出(0 个或全量重放),不抛异常
    resumed = [
        r
        async for r in orch.resume(two_product_project.project_id, two_product_project)
    ]
    # 不崩即通过;且仍能查回投影 plan
    plan2 = await memory_storage.state_store.get_dag_plan(
        two_product_project.project_id
    )
    assert plan2 is not None
    assert isinstance(resumed, list)


# ---------- gap 6：native run 不写 legacy 占位 plan(小写 slug node ids) ----------


@pytest.mark.asyncio
async def test_gap6_no_legacy_placeholder_plan(
    monkeypatch, two_product_project, memory_storage
):
    """native run 后 get_dag_plan 返回投影 plan,含 native node ids(collect.Notion),
    不含 legacy 占位 plan 的小写 slug node ids(collect.notion)。
    """
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(
        registry=_FakeRegistry(_StubQA([_pass_verdict()])), storage=memory_storage
    )
    plan = orch.plan(two_product_project)
    _ = [r async for r in orch.run(plan, two_product_project)]

    persisted_plan = await memory_storage.state_store.get_dag_plan(
        two_product_project.project_id
    )
    assert persisted_plan is not None, "DAGPlan 未在 native run 后落库"

    node_ids = [n.node_id for n in persisted_plan.nodes]
    assert any(n == "collect.Notion" for n in node_ids), (
        f"expected 'collect.Notion' (projected native id) in plan nodes, got {node_ids}"
    )
    assert not any(n == "collect.notion" for n in node_ids), (
        f"legacy placeholder node id 'collect.notion' should NOT appear in projected plan, "
        f"got {node_ids}"
    )
