"""WebSocket 实时事件流。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["events"])

_log = logging.getLogger(__name__)


@router.websocket("/projects/{project_id}/events")
async def project_events(websocket: WebSocket, project_id: str) -> None:
    """订阅 ``project:{id}:nodes`` channel，推送 NodeExecutionResult。

    协议：每条消息是一个 ``NodeExecutionResult`` 的 JSON 序列化。客户端
    断开连接（包括正常关闭）即停止推送。
    """
    await websocket.accept()
    storage = websocket.app.state.storage
    channel = f"project:{project_id}:nodes"

    try:
        async for result in storage.event_bus.subscribe(channel):
            await websocket.send_json(result.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        _log.exception("WS event stream failed for %s", project_id)
        await websocket.close(code=1011)
