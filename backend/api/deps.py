"""FastAPI 依赖注入 helpers。

每个 ``Depends`` 都从 ``app.state`` 取实例，由 ``app.lifespan`` 在启动时装配。

鉴权：
- ``get_current_user``：从 ``Authorization: Bearer <jwt>`` 解析并加载用户，失败 401。
- ``get_owned_project``：在 current_user 之上再校验项目归属，越权 403 / 不存在 404。
  路由用它做"读/改某项目"时的统一守卫，避免每个 handler 重复写校验。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.api.security import TokenError, decode_access_token
from backend.orchestrator import AgentRegistry, Orchestrator
from backend.schemas import Project, User
from backend.storage import Storage

# auto_error=False：自己控制 401 文案 + WWW-Authenticate
_bearer = HTTPBearer(auto_error=False)


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_agent_registry(request: Request) -> AgentRegistry:
    return request.app.state.agent_registry


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    storage: Storage = Depends(get_storage),
) -> User:
    """解析 Bearer JWT → 加载 User。缺失/失效/用户不存在均 401。"""
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = decode_access_token(creds.credentials)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    user = await storage.state_store.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_owned_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    storage: Storage = Depends(get_storage),
) -> Project:
    """加载项目并校验归属。不存在 404；非本人 403。

    路由把它作为路径依赖即可拿到已鉴权的 Project，无需再查一次。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id!r} not found",
        )
    if project.owner != current_user.user_id:
        # 不泄露"存在但无权"——按惯例可返回 404，这里用 403 更直白便于前端区分
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not your project",
        )
    return project


__all__ = [
    "get_agent_registry",
    "get_current_user",
    "get_orchestrator",
    "get_owned_project",
    "get_storage",
]
