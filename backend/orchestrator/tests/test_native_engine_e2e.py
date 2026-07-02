"""端到端验证 ``ORCH_ENGINE=native`` 分支接入 ``Orchestrator.run()``。

复用 ``test_native_graph`` 的桩 Agent / 假 registry / demo Project,配合
``build_storage(mode="memory")`` 的内存三件套,跑通整条原生流水线,并断言:
- ``run()`` 产出含 reporter 的 ``NodeExecutionResult`` 流;
- 节点输出被真正落进 ``state_store``(reporter + qa 均可查回)。

仅在 env 置位时走 native 分支;默认 legacy 路径不受影响(见
``test_native_engine_legacy_default_untouched``)。
"""

from __future__ import annotations

import pytest

from backend.orchestrator.tests.test_native_graph import (
    _block_reporter_verdict,
    _FakeRegistry,
    _load_demo_project,
    _pass_verdict,
    _StubQA,
)
from backend.storage import build_storage


@pytest.fixture
def stub_registry() -> _FakeRegistry:
    """桩 registry:QA 单轮即 pass(不触发返工)。"""
    return _FakeRegistry(_StubQA([_pass_verdict()]))


@pytest.fixture
def two_product_project():
    """两产品 demo Project(target=Notion, competitor=Asana)。"""
    return _load_demo_project(products=["Notion", "Asana"])


@pytest.fixture
def memory_storage():
    """内存三件套(state_store / event_bus / checkpointer)。"""
    return build_storage(mode="memory")


# 区分两套引擎落库结果的判据:native 用真实产品字符串做扇出引用键
# (``collect.Notion`` 首字母大写,取自 project.target_product),legacy 模板
# 用小写化的 product slug(``collect.notion``)且带 start/join_extract/end 等
# 结构节点。靠首字母大小写即可可靠区分本次到底走了哪条引擎分支。
_NATIVE_MARKER = "collect.Notion"  # native only
_LEGACY_MARKER = "collect.notion"  # legacy template only


@pytest.mark.asyncio
async def test_native_engine_persists_outputs(
    monkeypatch, stub_registry, two_product_project, memory_storage
):
    """native 分支跑完后,reporter/qa 输出落进 state_store。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(registry=stub_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    results = [r async for r in orch.run(plan, two_product_project)]

    assert any(r.node_id == "reporter" for r in results)
    saved = await memory_storage.state_store.list_node_outputs(two_product_project.project_id)
    assert "reporter" in saved and "qa" in saved
    # 确认确实走了 native 引擎(而非 legacy 也恰好落了 reporter/qa)
    assert _NATIVE_MARKER in saved
    assert _LEGACY_MARKER not in saved
    # native 扇出键带真实产品名,出现在结果流里
    assert any(r.node_id == _NATIVE_MARKER for r in results)


@pytest.mark.asyncio
async def test_default_engine_is_native(
    monkeypatch, stub_registry, two_product_project, memory_storage
):
    """Phase 2 起 ORCH_ENGINE 默认 native:不设环境变量时走 native 引擎
    (落库出现 native-only 的真实产品名引用键 collect.Notion)。"""
    monkeypatch.delenv("ORCH_ENGINE", raising=False)
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(registry=stub_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    results = [r async for r in orch.run(plan, two_product_project)]

    assert results
    saved = await memory_storage.state_store.list_node_outputs(two_product_project.project_id)
    # 默认走 native:有 native marker,没有 legacy 模板形状的小写 marker
    assert _NATIVE_MARKER in saved
    assert _LEGACY_MARKER not in saved


@pytest.mark.asyncio
async def test_legacy_engine_reachable_via_flag(
    monkeypatch, stub_registry, two_product_project, memory_storage
):
    """legacy 引擎仍可经 ORCH_ENGINE=legacy 显式回退(Phase 3 前不删)。"""
    monkeypatch.setenv("ORCH_ENGINE", "legacy")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(registry=stub_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    results = [r async for r in orch.run(plan, two_product_project)]

    assert results
    saved = await memory_storage.state_store.list_node_outputs(two_product_project.project_id)
    # 显式 legacy:legacy 模板形状,无 native marker
    assert _LEGACY_MARKER in saved
    assert _NATIVE_MARKER not in saved


@pytest.mark.asyncio
async def test_run_id_threaded_into_native_state(
    monkeypatch, stub_registry, two_product_project, memory_storage
):
    """P2-a:API 传入的 run_id 应成为 native RunState.run_id。

    否则 LIVE 视图(读 checkpoint 的 run_id)与 历史/快照/URL(用 API run_id)
    会指向不同 ULID,run identity 不一致。
    """
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.graph import build_native_graph
    from backend.orchestrator.orchestrator import Orchestrator
    from backend.storage.langgraph_adapter import to_langgraph_saver

    orch = Orchestrator(registry=stub_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    pinned = "run_pinned_p2a"
    _ = [r async for r in orch.run(plan, two_product_project, run_id=pinned)]

    # 从 checkpoint 读回 RunState.run_id(与 LIVE 视图同路径)。
    # 显式传了 run_id → checkpoint 落在 run-scoped thread(project::run_id)。
    from backend.orchestrator.orchestrator import native_thread_config

    graph = build_native_graph(
        orch.registry,
        project=two_product_project,
        checkpointer=to_langgraph_saver(memory_storage.checkpointer),
    )
    config = native_thread_config(two_product_project.project_id, pinned)
    snapshot = await graph.aget_state(config)
    assert snapshot.values["run_id"] == pinned


@pytest.mark.asyncio
async def test_rework_native_targets_reporter_only(
    monkeypatch, stub_registry, two_product_project, memory_storage
):
    """P1-AUTOREWORK：rework_native 只重跑 reporter(+qa)，不从 collect 重跑整个项目。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    orch = Orchestrator(registry=stub_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    _ = [r async for r in orch.run(plan, two_product_project)]

    before = await memory_storage.state_store.list_node_outputs(two_product_project.project_id)
    assert "reporter" in before and "reporter_v2" not in before

    fb = {
        "reporter": {
            "from_verdict_id": "v",
            "issues": [],
            "instructions": "avoid disputed evidence",
            "must_address": [],
            "revision": 1,
        }
    }
    results = [r async for r in orch.rework_native(two_product_project, qa_feedback_by_node=fb)]
    assert results, "rework_native 应触发并产出节点结果"

    after = await memory_storage.state_store.list_node_outputs(two_product_project.project_id)
    # 定向返工：reporter_v2 产出；collect/extract 未再产新版本(仍只有 v1)
    assert "reporter_v2" in after
    assert "collect.Notion_v2" not in after
    assert "extract.Notion_v2" not in after


@pytest.mark.asyncio
async def test_native_engine_persists_reworked_output(
    monkeypatch, two_product_project, memory_storage
):
    """QA 返工后 reporter 的第二次 draft 必须被落库(不能被 seen_refs 去重掉)。

    _StubQA 序列: [block_reporter, pass]
      - 第 1 次 reporter → draft.version == 1
      - QA 阻断 → reporter 再跑
      - 第 2 次 reporter → draft.version == 2
      - 第 2 次 QA → pass

    Bug(修复前): seen_refs 集合在第 1 次 reporter 后就把 "reporter" 加入,
      第 2 次 reporter 产出新对象时被跳过 → state_store 保留 version==1(旧)。
    Fix: 改用 seen dict 按 id() 去重 → 新对象 id 不同 → 重新落库。

    版本化(P1-a)后: round1 落键 "reporter"(v1)、round2 落键 "reporter_v2"(v2),
    两轮各自独立落库、互不覆盖 —— 既验证返工产物没丢,也验证 v1 历史被保留。
    """
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    rework_registry = _FakeRegistry(_StubQA([_block_reporter_verdict(), _pass_verdict()]))
    orch = Orchestrator(registry=rework_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    results = [r async for r in orch.run(plan, two_product_project)]

    # 流里应包含两次 reporter 结果(reporter + reporter_v2)
    reporter_results = [r for r in results if r.node_id.startswith("reporter")]
    assert len(reporter_results) == 2, (
        f"expected 2 reporter results (initial + rework), got {len(reporter_results)}"
    )

    # v1 与 v2 各占独立 key 落库:reporter==v1(历史保留)、reporter_v2==v2(返工产物)
    persisted_v1 = await memory_storage.state_store.get_node_output(
        two_product_project.project_id, "reporter"
    )
    persisted_v2 = await memory_storage.state_store.get_node_output(
        two_product_project.project_id, "reporter_v2"
    )
    assert persisted_v1 is not None and persisted_v1.draft.version == 1, (
        "round-1 reporter (v1) should be preserved under key 'reporter', not overwritten"
    )
    assert persisted_v2 is not None, "reworked reporter_v2 was not persisted at all"
    assert persisted_v2.draft.version == 2, (
        f"expected reworked draft version 2 under 'reporter_v2', got {persisted_v2.draft.version}"
    )
