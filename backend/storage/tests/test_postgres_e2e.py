"""PostgresStateStore + PostgresCheckpointer e2e 测试。

依赖：POSTGRES_DSN 环境变量（形如 `postgresql+asyncpg://app:app@localhost:5432/app`）。
未设时由 conftest.py 自动 skip 整个文件（pytest_collection_modifyitems）。

启动方式：
    docker compose up -d postgres
    export POSTGRES_DSN=postgresql+asyncpg://app:app@localhost:5432/app
    pytest backend/storage/tests/test_postgres_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from backend.schemas import NodeStatus, ProjectStatus
from backend.storage import init_storage, make_config
from backend.storage.postgres import PostgresCheckpointer, PostgresStateStore

pytestmark = pytest.mark.postgres


@pytest.fixture
async def engine():
    """每个测试拿独立 engine，跑完释放。"""
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN not set")
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(dsn, pool_pre_ping=True)
    # 跑建表
    from backend.storage.sql import init_schema

    await init_schema(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def state_store(engine):
    return PostgresStateStore(engine)


@pytest.fixture
async def checkpointer(engine):
    return PostgresCheckpointer(engine)


# ----- StateStore -----


async def test_pg_project_roundtrip(state_store, make_project):
    project = make_project(project_id=f"pg-test-{uuid.uuid4().hex[:8]}")
    await state_store.save_project(project)
    try:
        fetched = await state_store.get_project(project.project_id)
        assert fetched is not None
        assert fetched.project_id == project.project_id
        assert fetched.owner == project.owner
    finally:
        # 清理：通过 update 把它移走或直接删；这里依赖 ON DELETE CASCADE 测，留作弃数据
        pass


async def test_pg_list_projects_filter(state_store, make_project):
    owner = f"pg-owner-{uuid.uuid4().hex[:6]}"
    p1 = make_project(owner=owner, status=ProjectStatus.RUNNING)
    p2 = make_project(owner=owner, status=ProjectStatus.DONE)
    p3 = make_project(owner=owner, status=ProjectStatus.RUNNING)
    for p in [p1, p2, p3]:
        await state_store.save_project(p)
    running = await state_store.list_projects(owner=owner, status=ProjectStatus.RUNNING)
    assert {p.project_id for p in running} == {p1.project_id, p3.project_id}


async def test_pg_update_project_status(state_store, make_project):
    project = make_project(status=ProjectStatus.DRAFT)
    await state_store.save_project(project)
    await state_store.update_project_status(project.project_id, ProjectStatus.RUNNING)
    got = await state_store.get_project(project.project_id)
    assert got is not None
    assert got.status == ProjectStatus.RUNNING


async def test_pg_dag_plan_roundtrip(state_store, make_project, make_dag_plan):
    project = make_project()
    await state_store.save_project(project)
    plan = make_dag_plan(project.project_id)
    await state_store.save_dag_plan(plan)
    got = await state_store.get_dag_plan(project.project_id)
    assert got is not None
    assert got.plan_id == plan.plan_id


async def test_pg_update_node_status(state_store, make_project, make_dag_plan):
    project = make_project()
    await state_store.save_project(project)
    plan = make_dag_plan(project.project_id)
    await state_store.save_dag_plan(plan)
    await state_store.update_node_status(project.project_id, "n1", NodeStatus.SUCCESS)
    got = await state_store.get_dag_plan(project.project_id)
    assert got is not None
    n1 = next(n for n in got.nodes if n.node_id == "n1")
    assert n1.status == NodeStatus.SUCCESS


async def test_pg_node_output_polymorphic(state_store, make_project, make_collector_output):
    project = make_project()
    await state_store.save_project(project)
    out = make_collector_output()
    await state_store.save_node_output(project.project_id, "n1", out)
    got = await state_store.get_node_output(project.project_id, "n1")
    assert got is not None
    assert type(got).__name__ == "CollectorOutput"
    assert got.confidence == pytest.approx(out.confidence)


async def test_pg_qa_verdicts_ordered_desc(state_store, make_project, make_qa_verdict):
    import asyncio

    project = make_project()
    await state_store.save_project(project)
    v1 = make_qa_verdict()
    v2 = make_qa_verdict()
    await state_store.save_qa_verdict(project.project_id, v1)
    await asyncio.sleep(0.01)
    await state_store.save_qa_verdict(project.project_id, v2)
    verdicts = await state_store.list_qa_verdicts(project.project_id)
    ids = [v.verdict_id for v in verdicts]
    # v2 比 v1 后写入，应排前
    assert ids.index(v2.verdict_id) < ids.index(v1.verdict_id)


async def test_pg_llm_calls_persist_filter_and_survive(engine, state_store, make_project):
    project = make_project(project_id=f"pg-llm-{uuid.uuid4().hex[:8]}")
    await state_store.save_project(project)
    pid = project.project_id

    def _rec(ts, node_id, agent, phase):
        return {
            "timestamp": ts,
            "trace_id": f"trace_{pid}",
            "span_id": None,
            "node_id": node_id,
            "agent_name": agent,
            "model": "doubao",
            "phase": phase,
            "tokens_input": 10,
            "tokens_output": 20,
            "duration_s": 0.5,
            "finish_reason": "stop",
            "cost_usd": 0.0,
            "prompt_preview": f"{phase} preview",
            "response_preview": "ok",
        }

    await state_store.append_llm_calls(
        pid,
        [
            _rec(1.0, "collect.coda", "collector", "tool_call"),
            _rec(2.0, "reporter", "reporter", "tool_call"),
            _rec(3.0, "reporter_v2", "reporter", "json_mode"),
        ],
    )

    # 倒序（最新优先）
    all_calls = await state_store.list_llm_calls(pid)
    assert [c["node_id"] for c in all_calls] == [
        "reporter_v2",
        "reporter",
        "collect.coda",
    ]
    # node_id 过滤
    only_v2 = await state_store.list_llm_calls(pid, node_id="reporter_v2")
    assert len(only_v2) == 1 and only_v2[0]["phase"] == "json_mode"
    # agent_name 过滤
    reporters = await state_store.list_llm_calls(pid, agent_name="reporter")
    assert {c["node_id"] for c in reporters} == {"reporter", "reporter_v2"}

    # 「重启存活」：换全新 store 实例（同 engine = 同 DB），数据仍在
    fresh = PostgresStateStore(engine)
    survived = await fresh.list_llm_calls(pid)
    assert len(survived) == 3
    assert survived[0]["prompt_preview"] == "json_mode preview"


# ----- Checkpointer -----


async def test_pg_checkpoint_put_get_latest(checkpointer):
    tid = f"thread-{uuid.uuid4().hex[:8]}"
    cfg = make_config(tid)
    cfg1 = await checkpointer.aput(cfg, {"v": 1, "channel_values": {"x": 1}}, {"step": 0}, {})
    cfg2 = await checkpointer.aput(cfg1, {"v": 1, "channel_values": {"x": 2}}, {"step": 1}, {})
    # 不指定 checkpoint_id 应拿到最新
    latest = await checkpointer.aget_tuple(make_config(tid))
    assert latest is not None
    assert latest.checkpoint["channel_values"]["x"] == 2  # type: ignore[index]
    assert (
        latest.config["configurable"]["checkpoint_id"]
        == cfg2["configurable"][  # type: ignore[index]
            "checkpoint_id"
        ]
    )
    assert latest.parent_config is not None
    assert (
        latest.parent_config["configurable"]["checkpoint_id"]  # type: ignore[index]
        == cfg1["configurable"]["checkpoint_id"]  # type: ignore[index]
    )


async def test_pg_checkpoint_alist_orders_desc(checkpointer):
    tid = f"thread-{uuid.uuid4().hex[:8]}"
    cfg = make_config(tid)
    import asyncio

    ids: list[str] = []
    for i in range(3):
        c = await checkpointer.aput(cfg, {"v": 1, "channel_values": {"i": i}}, {"step": i}, {})
        ids.append(c["configurable"]["checkpoint_id"])  # type: ignore[index]
        await asyncio.sleep(0.005)  # 保证 created_at 有差

    seen = [t async for t in checkpointer.alist(cfg)]
    seen_ids = [t.config["configurable"]["checkpoint_id"] for t in seen]  # type: ignore[index]
    # 至少前三条来自这次写入，且严格倒序
    indices = [seen_ids.index(i) for i in ids]
    assert indices == sorted(indices, reverse=True)


async def test_pg_checkpoint_aput_writes(checkpointer):
    tid = f"thread-{uuid.uuid4().hex[:8]}"
    cfg = make_config(tid)
    cfg1 = await checkpointer.aput(cfg, {"v": 1, "channel_values": {}}, {}, {})
    await checkpointer.aput_writes(cfg1, [("ch1", "v1"), ("ch2", "v2")], task_id="task-A")
    await checkpointer.aput_writes(cfg1, [("ch3", "v3")], task_id="task-B")
    got = await checkpointer.aget_tuple(cfg1)
    assert got is not None
    by_task = {(t, ch): val for t, ch, val in got.pending_writes}
    assert by_task[("task-A", "ch1")] == "v1"
    assert by_task[("task-A", "ch2")] == "v2"
    assert by_task[("task-B", "ch3")] == "v3"


# ----- build_storage / init_storage -----


async def test_build_storage_postgres_end_to_end(make_project):
    """完整工厂路径：build_storage(postgres) → init_storage → CRUD → close。"""
    pg_dsn = os.getenv("POSTGRES_DSN")
    redis_url = os.getenv("REDIS_URL")
    if not pg_dsn or not redis_url:
        pytest.skip("POSTGRES_DSN and REDIS_URL both required")

    from backend.storage import build_storage

    storage = build_storage(mode="postgres", pg_dsn=pg_dsn, redis_url=redis_url)
    try:
        await init_storage(storage)
        project = make_project()
        await storage.state_store.save_project(project)
        got = await storage.state_store.get_project(project.project_id)
        assert got is not None
    finally:
        await storage.close()
