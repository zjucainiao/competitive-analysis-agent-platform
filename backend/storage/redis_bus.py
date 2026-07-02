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
from collections.abc import AsyncIterator

from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError

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
            # 用 get_message 轮询（timeout=1s）而非 listen()：空闲时 redis 连接
            # 约 5s 触发读超时（redis-py/redislite 默认），listen() 会把它当致命
            # 错误抛出、杀掉整条订阅——表现为 WS 每 5s 闪断一次。1s 轮询既不会
            # 撞上 5s 超时，又能让真实消息亚秒级投递；空闲读超时被吞掉继续等。
            while True:
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                except RedisTimeoutError:
                    # 空闲读超时不是错误：保持订阅，继续等下一条
                    continue
                if msg is None:
                    continue
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
                await pubsub.aclose()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._client.aclose()


__all__ = ["RedisEventBus"]
