"""鉴权底层工具：密码哈希（bcrypt）+ JWT 签发/校验。

设计：
- 密码用 bcrypt（自带盐，慢哈希抗暴力）。
- JWT(HS256) 里只放 ``sub``=user_id 与 ``exp``，不放任何敏感字段。
- 密钥从 ``JWT_SECRET`` 读；缺省给一个**仅供本地开发**的弱默认值并告警，
  生产部署必须在环境里显式设置（阶段③ compose 会注入）。

get_current_user 依赖在 deps.py，依赖本模块的 decode_access_token。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

logger = logging.getLogger(__name__)

_DEV_FALLBACK_SECRET = "dev-only-insecure-change-me-please-set-JWT_SECRET-in-prod"
JWT_ALGORITHM = "HS256"
# token 有效期（小时）；默认 7 天
ACCESS_TOKEN_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "168"))


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        logger.warning(
            "JWT_SECRET 未设置，使用不安全的开发默认值。生产部署必须设置 JWT_SECRET。"
        )
        return _DEV_FALLBACK_SECRET
    return secret


# ---------- 注册/登录白名单 ----------
#
# 通过 ``AUTH_ALLOWED_EMAILS``（逗号分隔，大小写不敏感）控制谁能注册 + 登录：
#   - 不设 / 设为 ``*``：完全开放（任何人可注册登录）—— 开源默认
#   - 设为邮箱列表：仅这些邮箱可用（私有部署锁定自己的账号）
# 注册接口始终保留；白名单只是在注册/登录时挡住非授权邮箱。
#
# 开源默认开放；站长自己的实例在**本地 .env**（已 gitignore）里设
# ``AUTH_ALLOWED_EMAILS=你的邮箱`` 即可锁成单账号，不污染开源默认值。


def allowed_emails() -> set[str] | None:
    """返回允许的 email 集合（小写）。``None`` 表示完全开放。"""
    raw = os.getenv("AUTH_ALLOWED_EMAILS")
    if raw is None:
        return None  # 默认开放注册（开源友好）
    raw = raw.strip()
    if not raw or raw == "*":
        return None
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_email_allowed(email: str) -> bool:
    allow = allowed_emails()
    if allow is None:  # 开放模式
        return True
    return email.strip().lower() in allow


# ---------- 密码哈希 ----------


def hash_password(plain: str) -> str:
    """bcrypt 哈希，返回可入库的字符串。"""
    digest = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
    return digest.decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    """常数时间校验明文与哈希是否匹配。哈希损坏时返回 False 而非抛错。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------


class TokenError(Exception):
    """token 缺失 / 过期 / 签名无效的统一异常（deps 转 401）。"""


def create_access_token(user_id: str) -> str:
    """签发 access token，sub=user_id。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ACCESS_TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    """校验 token 并返回 user_id（sub）。失败抛 TokenError。"""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("token expired") from e
    except jwt.PyJWTError as e:
        raise TokenError("invalid token") from e
    sub = payload.get("sub")
    if not sub or not isinstance(sub, str):
        raise TokenError("token missing subject")
    return sub


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
    "TokenError",
    "ACCESS_TOKEN_TTL_HOURS",
    "allowed_emails",
    "is_email_allowed",
]
