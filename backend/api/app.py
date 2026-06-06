"""FastAPI 应用工厂。

只跑 real：自动从 ``.env`` 读 ``DEEPSEEK_API_KEY`` / ``OPENAI_API_KEY``；
找不到 key 启动时直接报错，不静默退化到 mock。
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.orchestrator import AgentRegistry, Orchestrator
from backend.schemas import SCHEMA_VERSION
from backend.storage import build_storage, init_storage

from .routes import (
    auth,
    discovery,
    events,
    evidence,
    interventions,
    meta,
    projects,
    reports,
    runs,
)

# 仓库根目录 .env 在模块装载时加载，让 uvicorn 直接启动也能拿到 LLM key
load_dotenv()

# ----- 日志配置：让每次 LLM 调用的 [LLM] 行直接出现在 uvicorn 控制台 -----
# LOG_LEVEL env 控总体；backend.llm.calls 单独保 INFO（即使 LOG_LEVEL=WARNING 也显示）。
_root_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_root_level,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("backend.llm.calls").setLevel(logging.INFO)

_log = logging.getLogger(__name__)

Mode = Literal["memory", "postgres"]


def create_app(
    *,
    mode: Mode = "memory",
    pg_dsn: str | None = None,
    redis_url: str | None = None,
    max_parallel: int = 4,
) -> FastAPI:
    """构造 FastAPI 应用。

    Lifespan 行为：
    - 启动：装配 ``Storage`` / ``AgentRegistry`` / ``Orchestrator``，``init_storage``
      在 postgres 模式下跑建表
    - 关闭：取消所有未完成的后台 run 任务，关闭 storage

    AgentRegistry 一律走 ``from_env``：读 ``DEEPSEEK_API_KEY`` / ``OPENAI_API_KEY``
    自动构造真实 LLM；无 key 直接抛 RuntimeError，不静默 fallback。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage = build_storage(mode=mode, pg_dsn=pg_dsn, redis_url=redis_url)
        await init_storage(storage)

        registry = AgentRegistry.from_env()

        orch = Orchestrator(
            registry=registry, storage=storage, max_parallel=max_parallel
        )

        app.state.storage = storage
        app.state.agent_registry = registry
        app.state.orchestrator = orch
        app.state.running_tasks = {}

        _log.info(
            "API started (mode=%s, agent_mode=real, schema=%s)",
            mode,
            SCHEMA_VERSION,
        )

        try:
            yield
        finally:
            tasks: dict[str, asyncio.Task] = app.state.running_tasks
            for project_id, task in list(tasks.items()):
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
            await storage.close()
            _log.info("API shutdown complete")

    app = FastAPI(
        title="Competitive Analysis Agent Platform",
        version="0.1.0",
        description="DAG-based multi-agent platform for B2B SaaS competitive analysis.",
        lifespan=lifespan,
    )

    # CORS：让前端（Next.js dev 默认 :3000）跨域调后端。
    # 生产部署时把 allow_origins 收紧到具体域名；dev 阶段允许所有 localhost 来源。
    _cors_origins = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173",
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "agent_mode": "real",
            "storage_mode": mode,
        }

    app.include_router(auth.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    app.include_router(evidence.router, prefix="/api")
    app.include_router(interventions.router, prefix="/api")
    app.include_router(meta.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(discovery.router, prefix="/api")
    return app


# 默认实例：``uvicorn backend.api.app:app`` 直接启动用。
# - storage 默认内存（指定 STORAGE_MODE=postgres 切换）
# - agent 永远走真实 LLM；启动时如果没有 API key 直接报错
app = create_app(
    mode=os.getenv("STORAGE_MODE", "memory"),  # type: ignore[arg-type]
    pg_dsn=os.getenv("POSTGRES_DSN"),
    redis_url=os.getenv("REDIS_URL"),
)


__all__ = ["app", "create_app"]
