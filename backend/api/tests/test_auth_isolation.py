"""鉴权 + 用户隔离 e2e：注册/登录、JWT 守卫、跨用户越权拦截。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    # 隔离测试需要多个用户，放开注册白名单
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "*")
    app = create_app(mode="memory")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def restricted_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """白名单仅 owner@allowed.com，用于验证关闭对外注册。"""
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "owner@allowed.com")
    app = create_app(mode="memory")
    with TestClient(app) as c:
        yield c


def test_registration_closed_for_non_allowlisted(restricted_client: TestClient) -> None:
    """白名单外邮箱注册 → 403；白名单内 → 201。"""
    blocked = restricted_client.post(
        "/api/auth/register",
        json={"email": "stranger@evil.com", "password": "secret123"},
    )
    assert blocked.status_code == 403

    allowed = restricted_client.post(
        "/api/auth/register",
        json={"email": "owner@allowed.com", "password": "secret123"},
    )
    assert allowed.status_code == 201


def test_login_blocked_for_non_allowlisted(restricted_client: TestClient) -> None:
    """非白名单邮箱即便要登录也 403（不进密码校验）。"""
    r = restricted_client.post(
        "/api/auth/login",
        json={"email": "stranger@evil.com", "password": "whatever"},
    )
    assert r.status_code == 403


def _register(client: TestClient, email: str) -> dict[str, str]:
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123", "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_project(client: TestClient, headers: dict, name: str) -> str:
    r = client.post(
        "/api/projects",
        json={
            "project_name": name,
            "target_product": "Notion",
            "competitors": ["Asana"],
            "industry": "collaboration_saas",
            "analysis_dimensions": ["feature_comparison"],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def test_register_then_login(client: TestClient) -> None:
    _register(client, "alice@example.com")
    # 重复注册 → 409
    dup = client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    assert dup.status_code == 409
    # 登录拿到 token
    login = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "secret123"},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]
    # 错密码 → 401
    bad = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "nope"},
    )
    assert bad.status_code == 401


def test_create_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/projects",
        json={
            "project_name": "x",
            "target_product": "Notion",
            "competitors": ["Asana"],
            "industry": "collaboration_saas",
            "analysis_dimensions": ["feature_comparison"],
        },
    )
    assert r.status_code == 401


def test_owner_set_from_token_not_client(client: TestClient) -> None:
    """owner 由 JWT 派生：即便 body 想塞 owner 也被 extra=forbid 拒绝。"""
    alice = _register(client, "alice@example.com")
    # 带多余 owner 字段 → 422（schema 不允许）
    r = client.post(
        "/api/projects",
        json={
            "project_name": "x",
            "owner": "hacker",
            "target_product": "Notion",
            "competitors": ["Asana"],
            "industry": "collaboration_saas",
            "analysis_dimensions": ["feature_comparison"],
        },
        headers=alice,
    )
    assert r.status_code == 422


def test_user_isolation(client: TestClient) -> None:
    alice = _register(client, "alice@example.com")
    bob = _register(client, "bob@example.com")

    a_pid = _create_project(client, alice, "alice 的项目")
    b_pid = _create_project(client, bob, "bob 的项目")

    # 各自只看到自己的项目
    a_list = client.get("/api/projects", headers=alice).json()["projects"]
    b_list = client.get("/api/projects", headers=bob).json()["projects"]
    a_ids = {p["project_id"] for p in a_list}
    b_ids = {p["project_id"] for p in b_list}
    assert a_pid in a_ids and b_pid not in a_ids
    assert b_pid in b_ids and a_pid not in b_ids

    # bob 直接 GET alice 的项目 → 403
    forbidden = client.get(f"/api/projects/{a_pid}", headers=bob)
    assert forbidden.status_code == 403

    # bob 删 alice 的项目 → 403
    del_forbidden = client.delete(f"/api/projects/{a_pid}", headers=bob)
    assert del_forbidden.status_code == 403

    # alice 自己能拿到
    ok = client.get(f"/api/projects/{a_pid}", headers=alice)
    assert ok.status_code == 200
    assert ok.json()["owner"]  # owner 是 alice 的 user_id（非空）


def test_unauthenticated_list_401(client: TestClient) -> None:
    assert client.get("/api/projects").status_code == 401
