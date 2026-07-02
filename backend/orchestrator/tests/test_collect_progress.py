"""采集实时进度：跨线程 emit → 事件总线广播。

验证编排器侧的 emitter（run_coroutine_threadsafe 调回主 loop 广播）配合
``asyncio.to_thread`` 的 contextvar 传播——即 ``run_agent_node`` 调 collector 的
真实路径——能把 collector worker 线程里的逐条来源进度送上 WS 通道。
"""

from __future__ import annotations

import asyncio

import pytest

from backend.agents._progress import (
    emit_collect_progress,
    reset_collect_progress_emitter,
    set_collect_progress_emitter,
)
from backend.schemas import NodeExecutionResult, NodeStatus
from backend.storage import build_storage


@pytest.mark.asyncio
async def test_emitter_publishes_collect_progress_cross_thread() -> None:
    storage = build_storage(mode="memory")
    bus = storage.event_bus
    channel = "project:p1:nodes"
    loop = asyncio.get_running_loop()

    def _emit(payload: dict) -> None:
        res = NodeExecutionResult(
            project_id="p1",
            node_id=f"collect.{payload.get('product')}",
            status=NodeStatus.RUNNING,
            output=None,
            metadata={"kind": "collect_progress", **payload},
        )
        asyncio.run_coroutine_threadsafe(bus.publish(channel, res), loop)

    token = set_collect_progress_emitter(_emit)
    try:

        async def first_event() -> NodeExecutionResult:
            async for res in bus.subscribe(channel):
                return res
            raise AssertionError("subscribe ended without event")

        sub = asyncio.create_task(first_event())
        # 等订阅者注册好队列（memory bus 仅给「订阅时已在」的 subscriber 派发）
        for _ in range(200):
            if bus._subscribers.get(channel):
                break
            await asyncio.sleep(0.01)

        # collector 真实路径：在 worker 线程里 emit（to_thread 复制 contextvar 过去）
        await asyncio.to_thread(
            emit_collect_progress,
            {
                "product": "飞书",
                "url": "https://feishu.cn/pricing",
                "dimension": "pricing",
                "identity_status": "confirmed",
                "title": "飞书定价",
                "detected_product_name": "飞书",
            },
        )
        res = await asyncio.wait_for(sub, timeout=5)
    finally:
        reset_collect_progress_emitter(token)

    assert res.metadata.get("kind") == "collect_progress"
    assert res.metadata.get("product") == "飞书"
    assert res.metadata.get("identity_status") == "confirmed"
    assert res.node_id == "collect.飞书"
    assert res.status == NodeStatus.RUNNING


@pytest.mark.asyncio
async def test_emit_without_emitter_is_silent() -> None:
    """未注入 emitter（如 legacy 路径 / 测试）时 emit 是 no-op，不抛。"""
    emit_collect_progress({"product": "x", "url": "https://x.com"})  # 不应抛
