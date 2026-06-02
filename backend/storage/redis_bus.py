"""RedisEventBus —— `EventBusProtocol` 的 Redis pub/sub 实现。

驱动：`redis>=5.0`（async API：`redis.asyncio`）。
- publish: `redis.publish(channel, json_payload)`
- subscribe: 每次调用拿独立 `PubSub` 实例，订阅一个 channel，listen 异步迭代

`NodeExecutionResult` 序列化走 `model_dump_json()`；反序列化 `model_validate_json()`。
pub/sub 语义与 InMemory 实现一致（不 replay），方便上层 swap。
v2 升级 Redis Stream 时新增 `replay(channel, since=...)` 方法，不破现有接口。
"""

from __future__ import annotations

import contextlib
from typing import AsyncIterator

from redis.asyncio import Redis

from backend.schemas import NodeExecutionResult


class RedisEventBus:
    """实现 `EventBusProtocol`。"""

    def __init__(self, redis_url: str, *, client: Redis | None = None) -> None:
        # `client` 注入用于测试；正常路径由 build_storage 提供 url 即可
        self._url = redis_url
        self._client: Redis = client or Redis.from_url(redis_url, decode_responses=False)
        self._closed = False

    async def publish(self, channel: str, payload: NodeExecutionResult) -> None:
        if self._closed:
            raise RuntimeError("event bus closed")
        data = payload.model_dump_json().encode("utf-8")
        await self._client.publish(channel, data)

    async def subscribe(self, channel: str) -> AsyncIterator[NodeExecutionResult]:
        if self._closed:
            raise RuntimeError("event bus closed")
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                raw = msg.get("data")
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8")
                if not raw:
                    continue
                try:
                    yield NodeExecutionResult.model_validate_json(raw)
                except Exception:
                    # 单条消息坏不阻塞订阅；v2 加 metrics + dead-letter
                    continue
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
            with contextlib.suppress(Exception):
                await pubsub.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._client.aclose()


__all__ = ["RedisEventBus"]
