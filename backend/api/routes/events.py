"""WebSocket 实时事件流。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.security import TokenError, decode_access_token

router = APIRouter(tags=["events"])

_log = logging.getLogger(__name__)

# WS 关闭码：1008 = policy violation（鉴权失败）
_WS_POLICY_VIOLATION = 1008


@router.websocket("/projects/{project_id}/events")
async def project_events(websocket: WebSocket, project_id: str) -> None:
    """订阅 ``project:{id}:nodes`` channel，推送 NodeExecutionResult。

    鉴权：浏览器 WebSocket 不能带 Authorization header，故 token 走 query param
    ``?token=<jwt>``。校验通过且项目属于该用户才订阅；否则握手后立即按 1008 关闭。

    协议：每条消息是一个 ``NodeExecutionResult`` 的 JSON 序列化。客户端断开
    （含正常关闭）即停止推送。
    """
    storage = websocket.app.state.storage

    # 1) 解析 token（query param）
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=_WS_POLICY_VIOLATION, reason="missing token")
        return
    try:
        user_id = decode_access_token(token)
    except TokenError:
        await websocket.close(code=_WS_POLICY_VIOLATION, reason="invalid token")
        return

    # 2) 校验项目归属
    project = await storage.state_store.get_project(project_id)
    if project is None or project.owner != user_id:
        # 不存在或非本人：统一 1008，不泄露存在性
        await websocket.close(code=_WS_POLICY_VIOLATION, reason="forbidden")
        return

    await websocket.accept()
    channel = f"project:{project_id}:nodes"
    try:
        async for result in storage.event_bus.subscribe(channel):
            await websocket.send_json(result.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        _log.exception("WS event stream failed for %s", project_id)
        await websocket.close(code=1011)
