"""RedisEventBus e2e 测试。

依赖：REDIS_URL 环境变量（形如 `redis://localhost:6379/0`）。
未设时由 conftest.py 自动 skip。

启动方式：
    docker compose up -d redis
    export REDIS_URL=redis://localhost:6379/0
    pytest backend/storage/tests/test_redis_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from backend.schemas import NodeExecutionResult, NodeStatus
from backend.storage.redis_bus import RedisEventBus


pytestmark = pytest.mark.redis


@pytest.fixture
async def bus():
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set")
    b = RedisEventBus(url)
    yield b
    await b.close()


def _channel() -> str:
    return f"test:{uuid.uuid4().hex[:8]}:nodes"


async def test_redis_publish_and_subscribe(bus):
    channel = _channel()
    received: list[NodeExecutionResult] = []

    async def consumer():
        async for msg in bus.subscribe(channel):
            received.append(msg)
            if len(received) == 2:
                break

    task = asyncio.create_task(consumer())
    # Redis 订阅生效需要一小段时间（实际 pubsub.subscribe RTT）
    await asyncio.sleep(0.2)

    p1 = NodeExecutionResult(
        project_id="p1", node_id="n1", status=NodeStatus.SUCCESS
    )
    p2 = NodeExecutionResult(
        project_id="p1", node_id="n2", status=NodeStatus.RUNNING
    )
    await bus.publish(channel, p1)
    await bus.publish(channel, p2)

    await asyncio.wait_for(task, timeout=5.0)
    assert [m.node_id for m in received] == ["n1", "n2"]


async def test_redis_no_replay_before_subscribe(bus):
    channel = _channel()
    payload = NodeExecutionResult(
        project_id="p1", node_id="n1", status=NodeStatus.SUCCESS
    )
    await bus.publish(channel, payload)  # 没人订阅

    received: list[NodeExecutionResult] = []

    async def consumer():
        async for msg in bus.subscribe(channel):
            received.append(msg)
            break

    task = asyncio.create_task(consumer())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
    task.cancel()
    assert received == []


async def test_redis_publish_after_close(bus):
    await bus.close()
    payload = NodeExecutionResult(
        project_id="p1", node_id="n1", status=NodeStatus.SUCCESS
    )
    with pytest.raises(RuntimeError):
        await bus.publish(_channel(), payload)
