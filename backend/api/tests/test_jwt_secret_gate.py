"""JWT_SECRET 启动闸门：生产形态（postgres）缺密钥必须拒绝启动，开发形态保持低摩擦。

背景：早期版本 JWT_SECRET 缺失时无条件回退到仓库里硬编码的开发默认值，
生产漏配 .env.prod 时任何人都能用公开字符串伪造任意用户 token（P0）。

闸门在 lifespan 启动最前面（先于 build_storage），所以拒启用例不需要真 Postgres；
postgres 正向路径（配了密钥 / 显式豁免）直接测 ensure_jwt_secret，避免单测连库。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.api.security import (
    create_access_token,
    decode_access_token,
    ensure_jwt_secret,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离宿主 .env（app 模块 import 时已 load_dotenv）：默认无密钥、无豁免。"""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_ALLOW_INSECURE_DEV", raising=False)
    monkeypatch.delenv("STORAGE_MODE", raising=False)


def test_postgres_missing_secret_refuses_startup() -> None:
    """生产形态（postgres 持久化）缺 JWT_SECRET → lifespan 启动即拒。"""
    app = create_app(mode="postgres")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        with TestClient(app):
            pass


def test_refusal_message_is_actionable() -> None:
    """报错必须可操作：指明去 .env.prod 配置。"""
    with pytest.raises(RuntimeError, match=r"\.env\.prod"):
        ensure_jwt_secret("postgres")


def test_postgres_with_secret_passes_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """配了 JWT_SECRET 的 postgres 形态放行。"""
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    ensure_jwt_secret("postgres")  # 不抛即通过


def test_explicit_dev_exemption_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地 postgres 验证（localdb 等）可显式设 JWT_ALLOW_INSECURE_DEV=1 豁免。"""
    monkeypatch.setenv("JWT_ALLOW_INSECURE_DEV", "1")
    ensure_jwt_secret("postgres")  # 不抛即通过


def test_memory_mode_dev_fallback_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """开发默认形态（memory）缺密钥仍低摩擦：app 能启动、token 可签可验。"""
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    app = create_app(mode="memory")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
    token = create_access_token("user-1")
    assert decode_access_token(token) == "user-1"
