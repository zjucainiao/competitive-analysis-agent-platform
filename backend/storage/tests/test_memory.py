"""InMemory 三件套单测。"""

from __future__ import annotations

import asyncio

import pytest

from backend.schemas import (
    NodeExecutionResult,
    NodeStatus,
    ProjectStatus,
)
from backend.storage import build_storage, make_config
from backend.storage.memory import (
    InMemoryCheckpointer,
    InMemoryEventBus,
    InMemoryStateStore,
)
from backend.storage.protocols import (
    CheckpointerProtocol,
    EventBusProtocol,
    StateStoreProtocol,
)


# ---------- Protocol conformance ----------


def test_protocol_conformance():
    """三件 InMemory 应满足对应 Protocol（runtime_checkable）。"""
    cp = InMemoryCheckpointer()
    ss = InMemoryStateStore()
    eb = InMemoryEventBus()
    assert isinstance(cp, CheckpointerProtocol)
    assert isinstance(ss, StateStoreProtocol)
    assert isinstance(eb, EventBusProtocol)


def test_build_storage_memory_default():
    storage = build_storage(mode="memory")
    assert storage.mode == "memory"
    assert isinstance(storage.checkpointer, CheckpointerProtocol)
    assert isinstance(storage.state_store, StateStoreProtocol)
    assert isinstance(storage.event_bus, EventBusProtocol)


# ---------- Checkpointer ----------


async def test_checkpointer_aput_then_aget_latest():
    cp = InMemoryCheckpointer()
    cfg = make_config("thread-1")
    cp_payload = {"v": 1, "channel_values": {"x": 1}}
    new_cfg = await cp.aput(cfg, cp_payload, {"step": 1}, {})
    assert new_cfg["configurable"]["checkpoint_id"] is not None  # type: ignore[index]

    got = await cp.aget_tuple(cfg)
    assert got is not None
    assert got.checkpoint["channel_values"] == {"x": 1}  # type: ignore[index]
    assert got.metadata["step"] == 1  # type: ignore[index]


async def test_checkpointer_parent_chain():
    cp = InMemoryCheckpointer()
    cfg = make_config("thread-1")
    cfg1 = await cp.aput(cfg, {"v": 1, "channel_values": {"a": 1}}, {"step": 0}, {})
    # child checkpoint：把 cfg1 当作 parent
    cfg2 = await cp.aput(
        cfg1, {"v": 1, "channel_values": {"a": 2}}, {"step": 1}, {}
    )
    got = await cp.aget_tuple(cfg2)
    assert got is not None
    assert got.parent_config is not None
    assert got.parent_config["configurable"]["checkpoint_id"] == cfg1["configurable"][  # type: ignore[index]
        "checkpoint_id"
    ]


async def test_checkpointer_alist_orders_desc():
    cp = InMemoryCheckpointer()
    cfg = make_config("thread-1")
    ids: list[str] = []
    for i in range(3):
        c = await cp.aput(cfg, {"v": 1, "channel_values": {"i": i}}, {"step": i}, {})
        ids.append(c["configurable"]["checkpoint_id"])  # type: ignore[index, arg-type]

    seen = [t async for t in cp.alist(cfg)]
    assert [t.config["configurable"]["checkpoint_id"] for t in seen] == list(  # type: ignore[index]
        reversed(ids)
    )


async def test_checkpointer_aput_writes_then_pending():
    cp = InMemoryCheckpointer()
    cfg = make_config("thread-1")
    cfg1 = await cp.aput(cfg, {"v": 1, "channel_values": {}}, {}, {})
    await cp.aput_writes(cfg1, [("ch1", "v1"), ("ch2", "v2")], task_id="task-A")
    got = await cp.aget_tuple(cfg1)
    assert got is not None
    pending = sorted(got.pending_writes, key=lambda w: w[1])
    assert pending == [("task-A", "ch1", "v1"), ("task-A", "ch2", "v2")]


# ---------- StateStore ----------


async def test_state_store_project_crud(make_project):
    ss = InMemoryStateStore()
    project = make_project(owner="alice")
    await ss.save_project(project)
    fetched = await ss.get_project(project.project_id)
    assert fetched is not None
    assert fetched.project_id == project.project_id
    assert fetched.owner == "alice"


async def test_state_store_list_projects_filters(make_project):
    ss = InMemoryStateStore()
    p1 = make_project(owner="alice", status=ProjectStatus.RUNNING)
    p2 = make_project(owner="bob", status=ProjectStatus.RUNNING)
    p3 = make_project(owner="alice", status=ProjectStatus.DONE)
    await ss.save_project(p1)
    await ss.save_project(p2)
    await ss.save_project(p3)

    by_alice = await ss.list_projects(owner="alice")
    assert {p.project_id for p in by_alice} == {p1.project_id, p3.project_id}

    running = await ss.list_projects(status=ProjectStatus.RUNNING)
    assert {p.project_id for p in running} == {p1.project_id, p2.project_id}


async def test_state_store_update_project_status(make_project):
    ss = InMemoryStateStore()
    project = make_project(status=ProjectStatus.DRAFT)
    await ss.save_project(project)
    await ss.update_project_status(project.project_id, ProjectStatus.RUNNING)
    updated = await ss.get_project(project.project_id)
    assert updated is not None
    assert updated.status == ProjectStatus.RUNNING


async def test_state_store_dag_plan_roundtrip(make_project, make_dag_plan):
    ss = InMemoryStateStore()
    project = make_project()
    await ss.save_project(project)
    plan = make_dag_plan(project.project_id)
    await ss.save_dag_plan(plan)
    got = await ss.get_dag_plan(project.project_id)
    assert got is not None
    assert got.plan_id == plan.plan_id
    assert len(got.nodes) == 2


async def test_state_store_update_node_status(make_project, make_dag_plan):
    ss = InMemoryStateStore()
    project = make_project()
    await ss.save_project(project)
    plan = make_dag_plan(project.project_id)
    await ss.save_dag_plan(plan)
    await ss.update_node_status(project.project_id, "n1", NodeStatus.SUCCESS)
    updated = await ss.get_dag_plan(project.project_id)
    assert updated is not None
    n1 = next(n for n in updated.nodes if n.node_id == "n1")
    assert n1.status == NodeStatus.SUCCESS


async def test_state_store_node_output_polymorphic(
    make_project, make_collector_output
):
    ss = InMemoryStateStore()
    project = make_project()
    await ss.save_project(project)
    out = make_collector_output()
    await ss.save_node_output(project.project_id, "n1", out)
    fetched = await ss.get_node_output(project.project_id, "n1")
    assert fetched is not None
    assert fetched.agent_name == "collector"
    assert type(fetched).__name__ == "CollectorOutput"


async def test_state_store_qa_verdicts_ordered_desc(
    make_project, make_qa_verdict
):
    ss = InMemoryStateStore()
    project = make_project()
    await ss.save_project(project)
    v1 = make_qa_verdict()
    v2 = make_qa_verdict()
    await ss.save_qa_verdict(project.project_id, v1)
    await asyncio.sleep(0.001)
    await ss.save_qa_verdict(project.project_id, v2)
    verdicts = await ss.list_qa_verdicts(project.project_id)
    assert [v.verdict_id for v in verdicts] == [v2.verdict_id, v1.verdict_id]


async def test_state_store_llm_calls_append_list_filter():
    ss = InMemoryStateStore()

    def _rec(ts, node_id, agent, phase):
        return {
            "timestamp": ts,
            "node_id": node_id,
            "agent_name": agent,
            "phase": phase,
            "prompt_preview": f"{phase} preview",
        }

    await ss.append_llm_calls(
        "p1",
        [
            _rec(1.0, "collect.coda", "collector", "tool_call"),
            _rec(2.0, "reporter", "reporter", "tool_call"),
        ],
    )
    await ss.append_llm_calls("p1", [_rec(3.0, "reporter_v2", "reporter", "json_mode")])
    # 跨 project 隔离
    await ss.append_llm_calls("p2", [_rec(9.0, "x", "collector", "tool_call")])

    all_p1 = await ss.list_llm_calls("p1")
    assert [c["node_id"] for c in all_p1] == ["reporter_v2", "reporter", "collect.coda"]
    assert await ss.list_llm_calls("p1", node_id="reporter_v2") != []
    assert {
        c["node_id"] for c in await ss.list_llm_calls("p1", agent_name="reporter")
    } == {"reporter", "reporter_v2"}
    assert len(await ss.list_llm_calls("p2")) == 1
    assert len(await ss.list_llm_calls("p1", limit=1)) == 1


# ---------- EventBus ----------


async def test_event_bus_publish_after_subscribe():
    bus = InMemoryEventBus()
    received: list[NodeExecutionResult] = []

    async def consumer():
        async for msg in bus.subscribe("project:p1:nodes"):
            received.append(msg)
            if len(received) == 2:
                break

    payload1 = NodeExecutionResult(
        project_id="p1", node_id="n1", status=NodeStatus.SUCCESS
    )
    payload2 = NodeExecutionResult(
        project_id="p1", node_id="n2", status=NodeStatus.RUNNING
    )

    task = asyncio.create_task(consumer())
    # 给 subscriber 注册（包括 await q.get() 阻塞下来）一些时间
    await asyncio.sleep(0.05)
    await bus.publish("project:p1:nodes", payload1)
    await bus.publish("project:p1:nodes", payload2)
    await asyncio.wait_for(task, timeout=2.0)

    assert [m.node_id for m in received] == ["n1", "n2"]


async def test_event_bus_fan_out_to_multiple_subscribers():
    bus = InMemoryEventBus()
    counts: list[int] = [0, 0]

    async def consumer(idx: int):
        async for _ in bus.subscribe("ch"):
            counts[idx] += 1
            if counts[idx] >= 2:
                break

    tasks = [asyncio.create_task(consumer(i)) for i in range(2)]
    await asyncio.sleep(0.05)
    payload = NodeExecutionResult(
        project_id="p1", node_id="n1", status=NodeStatus.SUCCESS
    )
    await bus.publish("ch", payload)
    await bus.publish("ch", payload)
    await asyncio.gather(*tasks)
    assert counts == [2, 2]


async def test_event_bus_no_replay():
    """订阅前的消息不该被新 subscriber 看到。"""
    bus = InMemoryEventBus()
    payload = NodeExecutionResult(
        project_id="p1", node_id="n1", status=NodeStatus.SUCCESS
    )
    await bus.publish("ch", payload)  # 没人订阅，丢弃

    received: list[NodeExecutionResult] = []

    async def consumer():
        async for msg in bus.subscribe("ch"):
            received.append(msg)
            break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    # 没新消息，应阻塞；用 timeout 退出
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
    task.cancel()


async def test_event_bus_close_blocks_publish():
    bus = InMemoryEventBus()
    await bus.close()
    payload = NodeExecutionResult(
        project_id="p1", node_id="n1", status=NodeStatus.SUCCESS
    )
    with pytest.raises(RuntimeError):
        await bus.publish("ch", payload)


# ---------- Storage facade close ----------


async def test_storage_close_idempotent():
    storage = build_storage(mode="memory")
    await storage.close()
    # 再 close 一次不应抛
    await storage.close()
