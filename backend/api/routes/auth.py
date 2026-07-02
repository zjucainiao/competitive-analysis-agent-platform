"""鉴权路由：注册 / 登录 / 当前用户。

- ``POST /api/auth/register``：邮箱+密码注册，成功直接返回 token（注册即登录）。
- ``POST /api/auth/login``：邮箱+密码换 token。
- ``GET  /api/auth/me``：用 Bearer token 取当前用户信息。

token 是 JWT(HS256)，前端存起来后在 ``Authorization: Bearer`` 里带上。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from backend.api.deps import get_current_user, get_storage
from backend.api.security import (
    ACCESS_TOKEN_TTL_HOURS,
    create_access_token,
    hash_password,
    is_email_allowed,
    verify_password,
)
from backend.schemas import User, UserPublic
from backend.storage import Storage

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str = Field(min_length=8, max_length=128)
    display_name: str = ""


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class TokenResponse(BaseModel):
    """登录/注册成功返回。token_type 固定 bearer。"""
    model_config = ConfigDict(extra="forbid")
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user: UserPublic


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    storage: Storage = Depends(get_storage),
) -> TokenResponse:
    email = _normalize_email(req.email)
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="invalid email format")
    if not is_email_allowed(email):
        # 注册已关闭（仅白名单邮箱）。接口保留，非授权邮箱一律挡住。
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="registration is closed",
        )

    user = User(
        user_id=f"user_{ULID()}",
        email=email,
        password_hash=hash_password(req.password),
        display_name=req.display_name.strip(),
        created_at=datetime.now(UTC),
    )
    try:
        await storage.state_store.create_user(user)
    except ValueError as e:  # email 已注册
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already registered",
        ) from e

    token = create_access_token(user.user_id)
    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_TTL_HOURS * 3600,
        user=UserPublic.of(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    storage: Storage = Depends(get_storage),
) -> TokenResponse:
    email = _normalize_email(req.email)
    if not is_email_allowed(email):
        # 白名单外账号即便历史上存在过也禁止登录
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account not permitted",
        )
    user = await storage.state_store.get_user_by_email(email)
    # 统一文案，避免邮箱枚举：存在与否、密码对错都回同一条 401
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="incorrect email or password",
        )
    token = create_access_token(user.user_id)
    return TokenResponse(
        access_token=token,
        expires_in=ACCESS_TOKEN_TTL_HOURS * 3600,
        user=UserPublic.of(user),
    )


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.of(current_user)
