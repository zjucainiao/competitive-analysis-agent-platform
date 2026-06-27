"""部署版本可观测：``/version`` 自报 release tag + git SHA + schema 版本。

线上以后 ``curl /version`` 就知道跑的是哪一版（git SHA / release tag），
不再靠文件 mtime / SCHEMA_VERSION 反推。构建期由 Dockerfile 把
``APP_VERSION`` / ``APP_GIT_SHA`` 注入环境变量；本地直接跑时回退 dev/unknown。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.schemas import SCHEMA_VERSION


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    app = create_app(mode="memory")
    with TestClient(app) as c:
        yield c


def test_version_reflects_build_args(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """构建参数注入的 release tag / git SHA 原样出现在 /version。"""
    monkeypatch.setenv("APP_VERSION", "v9.9.9")
    monkeypatch.setenv("APP_GIT_SHA", "deadbee")
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "v9.9.9"
    assert body["git_sha"] == "deadbee"
    assert body["schema_version"] == SCHEMA_VERSION


def test_version_defaults_when_build_args_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地直接跑（没经 Docker 构建）时回退 dev/unknown，而不是报错。"""
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("APP_GIT_SHA", raising=False)
    body = client.get("/version").json()
    assert body["version"] == "dev"
    assert body["git_sha"] == "unknown"


def test_health_includes_git_sha(client: TestClient) -> None:
    """/health 也带 git_sha，健康探针顺带能看出版本。"""
    body = client.get("/health").json()
    assert "git_sha" in body
