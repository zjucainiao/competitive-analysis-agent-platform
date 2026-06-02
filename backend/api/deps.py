"""FastAPI 依赖注入 helpers。

每个 ``Depends`` 都从 ``app.state`` 取实例，由 ``app.lifespan`` 在启动时装配。
"""

from __future__ import annotations

from fastapi import Request

from backend.orchestrator import AgentRegistry, Orchestrator
from backend.storage import Storage


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_agent_registry(request: Request) -> AgentRegistry:
    return request.app.state.agent_registry


__all__ = ["get_agent_registry", "get_orchestrator", "get_storage"]
