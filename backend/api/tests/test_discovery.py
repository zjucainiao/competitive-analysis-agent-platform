"""``POST /api/discover-competitors`` 端点测试。

重点回归（评审 P0 #4）：同步 LLM 客户端（openai.OpenAI 底层阻塞）必须跑在
线程池（``asyncio.to_thread``）而不是事件循环线程——否则一次数秒的 LLM 调用
会把整个事件循环卡死，所有并发请求（含 WebSocket 心跳）挂起。

断言方式：假 LLM 在 ``chat()`` 内探测 ``asyncio.get_running_loop()``——
若拿得到 running loop 说明仍在事件循环线程里直调（阻塞），测试失败。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.observability import NullTracer
from backend.orchestrator import AgentRegistry


class _FakeLLM:
    """假 LLM provider：返回固定 JSON，并记录 chat() 是否在事件循环内执行。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.ran_in_event_loop: bool | None = None

    def chat(self, *, system: str, messages: list[dict], **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
            self.ran_in_event_loop = True  # 在事件循环线程里直调 → 会阻塞
        except RuntimeError:
            self.ran_in_event_loop = False  # 在线程池 worker 里 → 正确
        return SimpleNamespace(content=self._content)


class _BoomLLM:
    """chat() 必炸的假 LLM，验证失败兜底路径（200 + error + 空列表）。"""

    def chat(self, **kwargs: Any) -> Any:
        raise RuntimeError("provider down")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """memory storage 的 TestClient。registry 由各测试自行替换成假 LLM。"""
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "*")
    # 环境无关：即便 shell 里残留 STORAGE_MODE=postgres 也能发 token
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-0123456789abcdef0123456789abcdef")

    app = create_app(mode="memory")
    with TestClient(app) as c:
        yield c


def _auth_headers(client: TestClient) -> dict[str, str]:
    r = client.post(
        "/api/auth/register",
        json={"email": "discover@test.com", "password": "secret123"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _install_llm(client: TestClient, llm: Any) -> None:
    client.app.state.agent_registry = AgentRegistry(llm=llm, tracer=NullTracer())


def test_discover_competitors_runs_llm_off_event_loop(client: TestClient) -> None:
    """端点正常返回竞品列表，且 LLM 调用不在事件循环线程内直调。"""
    fake = _FakeLLM(
        json.dumps(
            {
                "competitors": [
                    {
                        "name": "飞书",
                        "reason": "同为一体化协作平台，目标用户重合",
                        "official_url": "https://www.feishu.cn",
                    },
                    {
                        "name": "企业微信",
                        "reason": "同做企业 IM + 办公协同",
                        "official_url": None,
                    },
                ]
            },
            ensure_ascii=False,
        )
    )
    _install_llm(client, fake)
    headers = _auth_headers(client)

    r = client.post(
        "/api/discover-competitors",
        json={"target_product": "钉钉", "industry": "collaboration_saas"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert [c["name"] for c in body["competitors"]] == ["飞书", "企业微信"]

    # 核心断言：同步 chat() 必须跑在线程池，不能阻塞事件循环
    assert fake.ran_in_event_loop is False, (
        "llm.chat() 在事件循环线程内直调，会阻塞所有并发请求；"
        "应挪进 asyncio.to_thread"
    )


def test_discover_competitors_llm_failure_returns_empty_with_error(
    client: TestClient,
) -> None:
    """LLM 炸了也返 200 + 空列表 + error，前端兜底走手动输入。"""
    _install_llm(client, _BoomLLM())
    headers = _auth_headers(client)

    r = client.post(
        "/api/discover-competitors",
        json={"target_product": "钉钉"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["competitors"] == []
    assert "RuntimeError" in body["error"]
