"""plan_directives —— native 引擎消费 Planner 产物的测试。

覆盖五条链路（修「native 引擎完全不消费 Planner 产物」的架构缺陷）：

① ``extract_plan_directives``：模板 plan / adaptive 形状 plan → JSON-可序列化指令集
   （official_url / collect_dims / 每节点 timeout_ms、max_retries），含钳底与 fail-soft；
② ``collect_dispatch`` / ``extract_dispatch``：Send payload 带上 plan 的
   official_url / dims / 超时重试，缺省回退（Send-target 看不到全局 state，payload 必须自带）；
③ 节点消费：collect_one / analyst 的 run_agent_node 调用优先取 plan 值，
   缺省回退 ``NODE_TIMEOUT_FLOOR_MS`` 硬编码表；
④ 端到端（仿 test_native_engine_e2e 的 stub 方式）：带 official_url 的 plan →
   collector input 里收到该 URL；
⑤ 向后兼容：无 plan_directives 的旧 checkpoint 形状 state 照常跑完。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.orchestrator.adaptive_planner import AdaptivePlanner
from backend.orchestrator.graph import build_native_graph
from backend.orchestrator.nodes import make_nodes
from backend.orchestrator.plan_directives import (
    NODE_TIMEOUT_FLOOR_MS,
    extract_plan_directives,
    resolve_node_limits,
)
from backend.orchestrator.planner import Planner
from backend.orchestrator.run_state import RunState
from backend.orchestrator.tests.test_adaptive_planner import (
    _canned_output,
    _make_project,
    _StubLLM,
)
from backend.orchestrator.tests.test_native_graph import (
    _FakeRegistry,
    _load_demo_project,
    _pass_verdict,
    _StubCollector,
    _StubQA,
)
from backend.schemas import CollectorOutput, DAGNode, NodeStatus, NodeType
from backend.storage import build_storage

# ============================================================
# ① 提取函数：模板 plan / adaptive plan → plan_directives
# ============================================================


def test_extract_from_template_plan() -> None:
    """模板 plan：product_urls 官网种子 + collect_dimensions + 每节点超时/重试全部提出来。"""
    project = _load_demo_project(products=["Notion", "Asana"])
    plan = Planner().plan(project)

    d = extract_plan_directives(plan)

    # 产品键是**显示名**（取自节点 metadata.product，不靠 slug 反推）
    assert d["products"]["Notion"]["official_url"] == "https://www.notion.so"
    assert d["products"]["Asana"]["official_url"] == "https://asana.com"
    assert d["products"]["Notion"]["collect_dims"] == [
        "homepage",
        "features",
        "pricing",
        "help_docs",
        "user_reviews",
    ]

    # 节点超时/重试：模板值已对齐到不低于事故精调下限
    assert d["nodes"]["collector"]["timeout_ms"] >= NODE_TIMEOUT_FLOOR_MS["collector"]
    assert d["nodes"]["collector"]["max_retries"] == 2
    assert d["nodes"]["extractor"]["timeout_ms"] == 600_000  # 高于下限的值保留
    assert d["nodes"]["extractor"]["max_retries"] == 1
    assert d["nodes"]["qa"]["timeout_ms"] >= NODE_TIMEOUT_FLOOR_MS["qa"]


def test_extract_from_adaptive_plan() -> None:
    """adaptive 形状 plan：LLM 推断的 official_url / dims 落进指令集；reporter 600s 保留。"""
    project = _make_project(target="Notion", competitors=("Asana",))
    out = _canned_output(["Notion", "Asana"], dims=["homepage", "pricing"])
    plan = AdaptivePlanner(llm=_StubLLM(out)).plan(project)

    d = extract_plan_directives(plan)

    assert d["products"]["Notion"]["official_url"] == "https://notion.com"
    assert d["products"]["Asana"]["official_url"] == "https://asana.com"
    assert d["products"]["Notion"]["collect_dims"] == ["homepage", "pricing"]
    # adaptive 给 reporter 600s（高于 240s 下限）——必须保留，不得被下限反向压低
    assert d["nodes"]["reporter"]["timeout_ms"] == 600_000
    assert d["nodes"]["analyst"]["timeout_ms"] >= NODE_TIMEOUT_FLOOR_MS["analyst"]


def _mini_plan_with_collector(timeout_ms: int, metadata: dict) -> Any:
    """手造一个只含单 collector 节点的最小 plan 形状（duck-type 即可）。"""
    node = DAGNode(
        node_id="collect.x",
        project_id="p1",
        node_type=NodeType.AGENT_CALL,
        agent_name="collector",
        status=NodeStatus.PENDING,
        timeout_ms=timeout_ms,
        max_retries=2,
        metadata=metadata,
    )
    return SimpleNamespace(nodes=[node])


def test_extract_clamps_timeout_to_floor() -> None:
    """plan 里过小的超时被钳到 NODE_TIMEOUT_FLOOR_MS（事故后精调的下限），防回归。"""
    plan = _mini_plan_with_collector(60_000, {"product": "X"})
    d = extract_plan_directives(plan)
    assert d["nodes"]["collector"]["timeout_ms"] == NODE_TIMEOUT_FLOOR_MS["collector"]


def test_extract_drops_invalid_dims() -> None:
    """非法 CollectDimension 值被过滤（否则 build_collector_input 会 ValueError 崩节点）。"""
    plan = _mini_plan_with_collector(
        300_000,
        {"product": "X", "collect_dimensions": ["homepage", "bogus_dim", "pricing"]},
    )
    d = extract_plan_directives(plan)
    assert d["products"]["X"]["collect_dims"] == ["homepage", "pricing"]


def test_extract_none_plan_returns_empty() -> None:
    assert extract_plan_directives(None) == {}


def test_extract_failsoft_on_malformed_plan() -> None:
    """病态 plan 对象 → 空指令集（fail-soft，不新增失败模式）。"""
    assert extract_plan_directives(object()) == {}


def test_extract_is_json_serializable() -> None:
    """指令集必须经得起 checkpoint serde 往返（纯 dict/str/int/list）。"""
    import json

    project = _load_demo_project(products=["Notion"])
    d = extract_plan_directives(Planner().plan(project))
    assert json.loads(json.dumps(d)) == d


def test_resolve_node_limits_prefers_plan() -> None:
    d = {"nodes": {"reporter": {"timeout_ms": 600_000, "max_retries": 2}}}
    assert resolve_node_limits(d, "reporter") == (600_000, 2)


def test_resolve_node_limits_fallback() -> None:
    """无指令集 → 回退硬编码下限表 + 现行缺省重试次数。"""
    assert resolve_node_limits({}, "reporter") == (NODE_TIMEOUT_FLOOR_MS["reporter"], 3)
    assert resolve_node_limits(None, "collector") == (NODE_TIMEOUT_FLOOR_MS["collector"], 3)


# ============================================================
# ② dispatch：Send payload 自带 plan 元数据（worker 看不到全局 state）
# ============================================================


def _directives_for_notion() -> dict:
    return {
        "products": {
            "Notion": {
                "official_url": "https://www.notion.so",
                "collect_dims": ["homepage", "pricing"],
            }
        },
        "nodes": {
            "collector": {"timeout_ms": 480_000, "max_retries": 2},
            "extractor": {"timeout_ms": 600_000, "max_retries": 1},
        },
    }


def _state(project: Any, products: list[str], directives: dict | None = None) -> RunState:
    return RunState(
        project_id=project.project_id,
        run_id="r",
        analysis_mode="competitive_compare",
        products=products,
        plan_directives=directives or {},
    )


def test_collect_dispatch_payload_carries_plan_metadata() -> None:
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)
    cmd = nodes["collect_dispatch"](_state(project, ["Notion"], _directives_for_notion()))
    payload = cmd.goto[0].arg
    assert payload["official_url"] == "https://www.notion.so"
    assert payload["collect_dims"] == ["homepage", "pricing"]
    assert payload["timeout_ms"] == 480_000
    assert payload["max_retries"] == 2


def test_collect_dispatch_payload_fallback_without_plan() -> None:
    """plan 缺该产品 / 无指令集 → 回退 None URL + 缺省 dims + 下限表超时。"""
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)
    cmd = nodes["collect_dispatch"](_state(project, ["Notion"]))
    payload = cmd.goto[0].arg
    assert payload["official_url"] is None
    assert payload["collect_dims"] is None
    assert payload["timeout_ms"] == NODE_TIMEOUT_FLOOR_MS["collector"]
    assert payload["max_retries"] == 3


def test_extract_dispatch_payload_carries_limits() -> None:
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)
    cmd = nodes["extract_dispatch"](_state(project, ["Notion"], _directives_for_notion()))
    payload = cmd.goto[0].arg
    assert payload["timeout_ms"] == 600_000
    assert payload["max_retries"] == 1


# ============================================================
# ③ 节点消费：run_agent_node 超时/重试优先取 plan 值，缺省回退
# ============================================================


async def _spy_run_agent(monkeypatch: Any, captured: dict) -> None:
    """monkeypatch nodes.run_agent_node，记录 kwargs 后透传真实实现。"""
    import backend.orchestrator.nodes as nodes_mod

    real = nodes_mod.run_agent_node

    async def _spy(registry, agent_name, inp, **kw):
        captured["input"] = inp
        captured["timeout_ms"] = kw.get("timeout_ms")
        captured["max_retries"] = kw.get("max_retries")
        return await real(registry, agent_name, inp, **kw)

    monkeypatch.setattr(nodes_mod, "run_agent_node", _spy)


async def test_collect_one_uses_payload_limits_and_seed(monkeypatch: Any) -> None:
    """Send payload 里的 plan 元数据（URL/dims/超时/重试）传进 run_agent_node 与 input。"""
    captured: dict[str, Any] = {}
    await _spy_run_agent(monkeypatch, captured)
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)

    await nodes["collect_one"](
        {
            "product": "Notion",
            "round": 1,
            "official_url": "https://www.notion.so",
            "collect_dims": ["homepage", "pricing"],
            "timeout_ms": 480_000,
            "max_retries": 2,
        }
    )
    assert captured["timeout_ms"] == 480_000
    assert captured["max_retries"] == 2
    assert captured["input"].official_url == "https://www.notion.so"
    assert [d.value for d in captured["input"].dimensions] == ["homepage", "pricing"]


async def test_collect_one_fallback_without_plan_keys(monkeypatch: Any) -> None:
    """旧 payload（无 plan 键）→ 回退下限表超时 / 缺省重试 / None URL / 缺省 dims。"""
    captured: dict[str, Any] = {}
    await _spy_run_agent(monkeypatch, captured)
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)

    await nodes["collect_one"]({"product": "Notion", "round": 1})
    assert captured["timeout_ms"] == NODE_TIMEOUT_FLOOR_MS["collector"]
    assert captured["max_retries"] == 3
    assert captured["input"].official_url is None
    assert len(captured["input"].dimensions) == 5  # _DEFAULT_COLLECT_DIMS 标准集


async def test_analyst_uses_plan_directives_limits(monkeypatch: Any) -> None:
    """normal 节点（analyst）直接从 state.plan_directives 取超时/重试。"""
    from backend.agents.extractor.fixtures import load_mock_profile
    from backend.orchestrator.tests.test_native_graph import _StubExtractor

    if load_mock_profile("Notion") is None:
        pytest.skip("mock profile for Notion missing")
    captured: dict[str, Any] = {}
    await _spy_run_agent(monkeypatch, captured)
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)

    extract_out = _StubExtractor().invoke(
        SimpleNamespace(task_id="extract.Notion", product_name="Notion"),
        trace_id="t",
        span_id="s",
        node_id="extract.Notion",
    )
    state = _state(
        project, ["Notion"], {"nodes": {"analyst": {"timeout_ms": 480_000, "max_retries": 1}}}
    )
    state = state.model_copy(update={"outputs": {"extract.Notion": extract_out}})

    await nodes["analyst"](state)
    assert captured["timeout_ms"] == 480_000
    assert captured["max_retries"] == 1


async def test_analyst_fallback_without_directives(monkeypatch: Any) -> None:
    from backend.agents.extractor.fixtures import load_mock_profile
    from backend.orchestrator.tests.test_native_graph import _StubExtractor

    if load_mock_profile("Notion") is None:
        pytest.skip("mock profile for Notion missing")
    captured: dict[str, Any] = {}
    await _spy_run_agent(monkeypatch, captured)
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)

    extract_out = _StubExtractor().invoke(
        SimpleNamespace(task_id="extract.Notion", product_name="Notion"),
        trace_id="t",
        span_id="s",
        node_id="extract.Notion",
    )
    state = _state(project, ["Notion"]).model_copy(
        update={"outputs": {"extract.Notion": extract_out}}
    )

    await nodes["analyst"](state)
    assert captured["timeout_ms"] == NODE_TIMEOUT_FLOOR_MS["analyst"]
    assert captured["max_retries"] == 3


# ============================================================
# ④ 端到端：带 official_url 的 plan → collector input 收到该 URL
# ============================================================


class _RecordingCollector(_StubCollector):
    """记录每次 invoke 收到的 CollectorInput（并发 append，list 线程安全）。"""

    def __init__(self) -> None:
        self.inputs: list[Any] = []

    def invoke(self, inp: Any, *, trace_id: str, span_id: str, node_id: str) -> CollectorOutput:
        self.inputs.append(inp)
        return super().invoke(inp, trace_id=trace_id, span_id=span_id, node_id=node_id)


@pytest.mark.asyncio
async def test_native_run_seeds_official_url_from_plan(monkeypatch: Any) -> None:
    """orch.plan 的官网种子经 plan_directives 流到 collector input（不再永远走搜索兜底）。"""
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator

    project = _load_demo_project(products=["Notion", "Asana"])
    registry = _FakeRegistry(_StubQA([_pass_verdict()]))
    recording = _RecordingCollector()
    registry._agents["collector"] = recording

    storage = build_storage(mode="memory")
    orch = Orchestrator(registry=registry, storage=storage)
    plan = orch.plan(project)  # 模板 plan：product_urls 命中 Notion / Asana
    results = [r async for r in orch.run(plan, project)]

    assert any(r.node_id == "reporter" for r in results)
    by_product = {inp.product_name: inp for inp in recording.inputs}
    assert by_product["Notion"].official_url == "https://www.notion.so"
    assert by_product["Asana"].official_url == "https://asana.com"


# ============================================================
# ⑤ 向后兼容：旧 checkpoint 形状（无 plan_directives 键）照常跑
# ============================================================


async def test_state_without_plan_directives_still_runs() -> None:
    """旧 checkpoint 复算：初始 state 缺 plan_directives 键 → 全链路照常收尾。"""
    project = _load_demo_project(products=["Notion"])
    registry = _FakeRegistry(_StubQA([_pass_verdict()]))
    app = build_native_graph(registry, project=project)

    state = RunState(
        project_id=project.project_id,
        run_id="run_oldckpt",
        analysis_mode=project.analysis_mode.value,
        products=["Notion"],
    ).model_dump()
    state.pop("plan_directives", None)  # 模拟旧 checkpoint 无此字段

    final = await app.ainvoke(state, {"configurable": {"thread_id": "oldckpt"}})
    assert "reporter" in final["outputs"]
    assert final["aborted"] is False
