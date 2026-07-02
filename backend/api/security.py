"""鉴权底层工具：密码哈希（bcrypt）+ JWT 签发/校验。

设计：
- 密码用 bcrypt（自带盐，慢哈希抗暴力）。
- JWT(HS256) 里只放 ``sub``=user_id 与 ``exp``，不放任何敏感字段。
- 密钥从 ``JWT_SECRET`` 读；缺失时按部署形态区别对待：
  - memory（重启即丢的本地试用默认）→ 回退开发默认值并告警，保持低摩擦；
  - postgres（持久化 = 生产形态，prod compose 强制注入 ``STORAGE_MODE=postgres``）
    → 直接拒绝启动（``ensure_jwt_secret``），杜绝用仓库里公开的字符串伪造 token。
  本地 postgres 验证（localdb 等）可显式设 ``JWT_ALLOW_INSECURE_DEV=1`` 豁免。

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


_MISSING_SECRET_HINT = (
    "JWT_SECRET 未配置：生产形态（postgres 持久化）拒绝使用仓库内置的开发密钥启动，"
    "否则任何人都能用公开字符串伪造任意用户 token。"
    "请在 .env.prod 里设置 JWT_SECRET（生成：openssl rand -hex 32）后重启；"
    "纯本地调试确需跳过时，可显式设 JWT_ALLOW_INSECURE_DEV=1（自担风险）。"
)


def _dev_fallback_allowed(storage_mode: str) -> bool:
    """JWT_SECRET 缺失时是否允许回退到开发默认值。

    取舍：以 storage 形态为生产判据（prod compose 强制 ``STORAGE_MODE=postgres``，
    仓库没有单独的 APP_ENV），memory 即本地试用、自动豁免；本地 postgres 验证
    属于少数场景，用显式环境变量豁免，避免误伤真生产。
    """
    if storage_mode == "memory":
        return True
    return os.getenv("JWT_ALLOW_INSECURE_DEV") == "1"


def ensure_jwt_secret(storage_mode: str) -> None:
    """启动期闸门：生产形态缺 JWT_SECRET 直接抛错拒启（create_app 的 lifespan 调用）。

    这是唯一强制点：生产形态过不了它 app 就起不来，签发/校验 token 的路由
    自然不可达，所以 ``_secret`` 里无需再按 env 二次判断（那会跟测试显式传
    的 mode 脱节、误伤本地 memory 用例）。
    """
    if os.getenv("JWT_SECRET"):
        return
    if not _dev_fallback_allowed(storage_mode):
        raise RuntimeError(_MISSING_SECRET_HINT)
    logger.warning(
        "JWT_SECRET 未设置，使用不安全的开发默认值。生产部署必须设置 JWT_SECRET。"
    )


def _secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        # 生产形态在启动闸门（ensure_jwt_secret）就已拒启；能走到这里的只有
        # 开发/测试形态，回退开发默认值并持续告警，避免弱密钥被无感使用。
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
    "ensure_jwt_secret",
    "TokenError",
    "ACCESS_TOKEN_TTL_HOURS",
    "allowed_emails",
    "is_email_allowed",
]
