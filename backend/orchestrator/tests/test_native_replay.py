"""②号约束：回放重建测试。

验证 native 引擎跑完一次 QA 返工流（QA round1 block→reporter rerun→QA round2 pass）后，
**仅凭持久化存储**（不依赖 live 事件 / 运行时内存）即可完整重建时间线：

- ``get_dag_plan``  → 含 reporter 及 reporter_v2 节点（v1↔v2 回放入口）
- ``list_qa_verdicts`` → 包含两轮 QA verdict（needs_revision + pass）
- ``list_node_outputs`` → 含 reporter / qa（节点输出可按需回放）

此测试在 Part A（_run_native 新增 save_qa_verdict 调用）之前必然 FAIL
（verdicts 未落库 → list_qa_verdicts 返回空列表）；Part A 完成后 PASS。
"""
from __future__ import annotations

import pytest

from backend.storage import build_storage
from backend.orchestrator.tests.test_native_graph import (
    _FakeRegistry,
    _StubQA,
    _block_reporter_verdict,
    _load_demo_project,
    _pass_verdict,
)


@pytest.fixture
def rework_project():
    """单产品 demo Project（Notion only，减少扇出节点，聚焦 QA 返工路径）。"""
    return _load_demo_project(products=["Notion", "Asana"])


@pytest.fixture
def rework_registry():
    """QA 序列：round1 block reporter → round2 pass。

    _StubQA 会将第 N 次 invoke 映射到 verdicts[min(N-1, len-1)]，
    即首次 QA 返回 needs_revision（block），第二次返回 pass。
    """
    return _FakeRegistry(
        _StubQA([_block_reporter_verdict(vid="v_round1"), _pass_verdict(vid="v_round2")])
    )


@pytest.fixture
def memory_storage():
    """内存三件套（state_store / event_bus / checkpointer）。"""
    return build_storage(mode="memory")


@pytest.mark.asyncio
async def test_replay_from_persisted_state(
    monkeypatch,
    rework_project,
    rework_registry,
    memory_storage,
):
    """回放重建约束：运行完毕后仅凭 storage 即可重建完整时间线。

    断言顺序：
    1. DAGPlan 含 reporter 和 reporter_v2（v1↔v2 回放标记）
    2. list_qa_verdicts 返回 >= 2 条（两轮 QA 均持久化）
    3. list_node_outputs 含 reporter / qa（节点输出可查回）

    关键约束：live 结果（results 列表）被故意丢弃；所有断言只读 storage。
    """
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(registry=rework_registry, storage=memory_storage)
    plan = orch.plan(rework_project)

    # 运行至完成；live 结果刻意丢弃（不用于任何断言）
    _ = [r async for r in orch.run(plan, rework_project)]

    # ── 1. 从 storage 重建 DAGPlan ──────────────────────────────────────────
    plan2 = await memory_storage.state_store.get_dag_plan(rework_project.project_id)
    assert plan2 is not None, "get_dag_plan 返回 None：投影 plan 未落库"

    node_ids = {n.node_id for n in plan2.nodes}
    assert "reporter" in node_ids, (
        f"reporter 节点不在投影 plan 里；现有节点：{sorted(node_ids)}"
    )
    assert "reporter_v2" in node_ids, (
        f"reporter_v2 节点不在投影 plan 里（QA 返工未产出 v2）；"
        f"现有节点：{sorted(node_ids)}"
    )

    # ── 2. 从 storage 重建 QA verdict 序列 ──────────────────────────────────
    verdicts = await memory_storage.state_store.list_qa_verdicts(rework_project.project_id)
    assert len(verdicts) >= 2, (
        f"期望 >= 2 条 QA verdict（round1 needs_revision + round2 pass），"
        f"实际得到 {len(verdicts)} 条 —— "
        f"若为 0 说明 _run_native 未调用 save_qa_verdict（Part A 未实施）"
    )

    # ── 3. 从 storage 重建节点输出 ──────────────────────────────────────────
    outs = await memory_storage.state_store.list_node_outputs(rework_project.project_id)
    assert "reporter" in outs, (
        f"reporter 输出未落库；现有 keys：{sorted(outs.keys())}"
    )
    assert "qa" in outs, (
        f"qa 输出未落库；现有 keys：{sorted(outs.keys())}"
    )
