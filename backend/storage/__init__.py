"""Storage 层 —— I 窗口对 O 窗口的硬契约入口。

详细契约见 docs/STORAGE.md。最小用法：

    from backend.storage import build_storage

    storage = build_storage(mode="memory")          # 单测/无 docker 默认
    await storage.state_store.save_project(project)
    await storage.event_bus.publish("project:p1:nodes", result)

    # 接入 langgraph
    from backend.storage.langgraph_adapter import to_langgraph_saver
    saver = to_langgraph_saver(storage.checkpointer)

切换到生产底座：

    storage = build_storage(
        mode="postgres",
        pg_dsn="postgresql+asyncpg://app:app@localhost:5432/app",
        redis_url="redis://localhost:6379/0",
    )
    await init_storage(storage)                     # 跑建表语句
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from .protocols import (
    CheckpointerProtocol,
    EventBusProtocol,
    StateStoreProtocol,
)
from .checkpoint_types import (
    Checkpoint,
    CheckpointConfig,
    CheckpointMetadata,
    CheckpointTuple,
    make_config,
)


@dataclass
class Storage:
    """三件 storage 实例的容器。`build_storage()` 返回此对象。"""

    checkpointer: CheckpointerProtocol
    state_store: StateStoreProtocol
    event_bus: EventBusProtocol
    mode: Literal["memory", "postgres"] = "memory"

    async def close(self) -> None:
        # 关闭顺序：bus → store → checkpointer（业务上 publisher 先停）
        await self.event_bus.close()
        await self.state_store.close()
        await self.checkpointer.close()


def build_storage(
    mode: Literal["memory", "postgres"] = "memory",
    *,
    pg_dsn: str | None = None,
    redis_url: str | None = None,
) -> Storage:
    """工厂：按 mode 装配三件 storage。

    - `mode="memory"`：InMemory 三件套，无外部依赖
    - `mode="postgres"`：PG checkpointer + PG state store + Redis event bus
      - `pg_dsn` 缺省读 `POSTGRES_DSN` 环境变量
      - `redis_url` 缺省读 `REDIS_URL` 环境变量
      - 两个都必须有值，否则抛 ValueError
    """
    if mode == "memory":
        from .memory import (
            InMemoryCheckpointer,
            InMemoryEventBus,
            InMemoryStateStore,
        )

        return Storage(
            checkpointer=InMemoryCheckpointer(),
            state_store=InMemoryStateStore(),
            event_bus=InMemoryEventBus(),
            mode="memory",
        )

    if mode == "postgres":
        pg_dsn = pg_dsn or os.getenv("POSTGRES_DSN")
        redis_url = redis_url or os.getenv("REDIS_URL")
        if not pg_dsn:
            raise ValueError(
                "build_storage(mode='postgres') requires pg_dsn or $POSTGRES_DSN"
            )
        if not redis_url:
            raise ValueError(
                "build_storage(mode='postgres') requires redis_url or $REDIS_URL"
            )

        from sqlalchemy.ext.asyncio import create_async_engine

        from .postgres import PostgresCheckpointer, PostgresStateStore
        from .redis_bus import RedisEventBus

        engine = create_async_engine(pg_dsn, pool_pre_ping=True)
        # PG checkpointer 和 state store 共用同一个 engine
        return _PostgresStorage(
            checkpointer=PostgresCheckpointer(engine),
            state_store=PostgresStateStore(engine),
            event_bus=RedisEventBus(redis_url),
            mode="postgres",
            engine=engine,
        )

    raise ValueError(f"unknown storage mode: {mode!r}")


@dataclass
class _PostgresStorage(Storage):
    """带 engine 句柄的 Storage，close 时释放连接池。"""

    engine: object = None  # AsyncEngine，避免顶部 import

    async def close(self) -> None:
        await super().close()
        if self.engine is not None:
            await self.engine.dispose()  # type: ignore[attr-defined]


async def init_storage(storage: Storage) -> None:
    """跑建表语句（仅 PG 模式有效；InMemory 模式 no-op）。"""
    if storage.mode == "postgres":
        from .sql import init_schema

        engine = getattr(storage, "engine", None)
        if engine is None:
            raise RuntimeError("postgres storage missing engine handle")
        await init_schema(engine)


__all__ = [
    "Checkpoint",
    "CheckpointConfig",
    "CheckpointMetadata",
    "CheckpointTuple",
    "CheckpointerProtocol",
    "EventBusProtocol",
    "StateStoreProtocol",
    "Storage",
    "build_storage",
    "init_storage",
    "make_config",
]
