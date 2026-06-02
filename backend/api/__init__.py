"""FastAPI 层 —— Orchestrator 的 HTTP / WebSocket 入口。

启动方式::

    uvicorn backend.api.app:app --reload

或在 Python 里::

    from backend.api import create_app
    app = create_app(mode="memory", agent_mode="mock")

模式参数：
- ``mode``: ``memory`` / ``postgres``（storage 层）
- ``agent_mode``: ``mock`` / ``hybrid`` / ``real``（AgentRegistry）

完整路由清单见 ``backend.api.routes``。
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
