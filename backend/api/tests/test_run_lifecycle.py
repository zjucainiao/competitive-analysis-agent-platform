"""Run 生命周期收尾 + 控制面并发保护（P1 修复回归）。

覆盖两个评审确认的 P1 缺陷：

缺陷 1 —— 收尾不一致：原本只有 start_run 的后台任务回写 RunRef.final_status 并落
RunSnapshot；restart / retry / edit-prompt / evidence auto-rework 只改 project
status，它们发起的 run 的 RunRef 永远 final_status=None、无快照，
``/runs/{id}/state`` 对它们 404。修后所有后台 run 路径统一走共享收尾
（backend/api/run_lifecycle.py），不变式：每个真实执行的 run 都有
RunRef + 终态 + 快照。

缺陷 2 —— 并发与单进程假设：
- start_run 的「检查 running_tasks → create_task」之间隔着多个 await，
  并发双击可起两个 run（TOCTOU）。修后同一时刻只成功一个，另一个 409。
- running_tasks / ring buffer 都是进程内状态，多 worker 部署下 409 防重 /
  stop / pause 全部静默失效。修后 lifespan 检测多 worker 环境变量即拒启。

桩 registry 复用 orchestrator 集成测试里的 _FakeRegistry / _StubQA，不触真实 LLM。
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.orchestrator import Orchestrator
from backend.orchestrator.tests.test_native_graph import (
    _FakeRegistry,
    _pass_verdict,
    _StubQA,
)
from backend.schemas import Evidence, ExtractorOutput

# ---------- 公共环境 ----------


def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app 启动所需的最小环境：假 LLM key + 开放注册 + native 引擎。"""
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "*")
    monkeypatch.setenv("ORCH_ENGINE", "native")


def _install_stub_registry(app) -> None:
    """lifespan 启动后把 registry / orchestrator 换成桩（QA 恒 PASS，单轮跑完）。"""
    registry = _FakeRegistry(_StubQA([_pass_verdict()]))
    app.state.agent_registry = registry
    app.state.orchestrator = Orchestrator(registry=registry, storage=app.state.storage)


@pytest.fixture
def native_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _stub_env(monkeypatch)
    app = create_app(mode="memory")
    with TestClient(app) as c:
        _install_stub_registry(c.app)
        yield c


def _register(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/auth/register", json={"email": email, "password": "secret123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_project(client: TestClient, headers: dict) -> str:
    r = client.post(
        "/api/projects",
        json={
            "project_name": "lifecycle test",
            "target_product": "Notion",
            "competitors": ["Asana"],
            "industry": "collaboration_saas",
            "analysis_dimensions": ["feature_comparison"],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _wait_last_run_finalized(client: TestClient, pid: str, headers: dict) -> dict:
    """轮询 /runs 直到最后一个 RunRef 有 final_status；返回该 RunRef dict。

    这是缺陷 1 的核心断言入口：修复前 restart/retry/edit-prompt 起的 run
    final_status 永远是 None，这里会超时抛错。
    """
    deadline = time.time() + 20
    while time.time() < deadline:
        runs = client.get(f"/api/projects/{pid}/runs", headers=headers).json()["runs"]
        if runs and runs[-1]["final_status"] is not None:
            return runs[-1]
        time.sleep(0.05)
    raise AssertionError("last run never got a final_status (RunRef 收尾缺失)")


def _wait_project_settled(client: TestClient, pid: str, headers: dict) -> str:
    """轮询 project.status 直到离开 running；返回终态字符串。"""
    deadline = time.time() + 20
    while time.time() < deadline:
        status_ = client.get(f"/api/projects/{pid}", headers=headers).json()["status"]
        if status_ in ("done", "failed"):
            return status_
        time.sleep(0.05)
    raise AssertionError("project never settled")


def _run_to_completion(client: TestClient, pid: str, headers: dict) -> str:
    r = client.post(f"/api/projects/{pid}/run", headers=headers)
    assert r.status_code == 202, r.text
    return _wait_last_run_finalized(client, pid, headers)["run_id"]


def _assert_run_finalized(client: TestClient, pid: str, headers: dict, run_ref: dict) -> dict:
    """不变式断言：RunRef 有终态 + 快照存在 + /runs/{id}/state 200。返回快照 JSON。"""
    assert run_ref["final_status"] in ("done", "failed"), run_ref
    assert run_ref["ended_at"] is not None, "RunRef.ended_at 应已回写"
    r = client.get(f"/api/projects/{pid}/runs/{run_ref['run_id']}/state", headers=headers)
    assert r.status_code == 200, f"run 快照缺失: {r.text}"
    snap = r.json()
    assert snap["run_id"] == run_ref["run_id"]
    assert snap["final_status"] == run_ref["final_status"]
    return snap


# ============================================================
# 缺陷 1：restart / retry / edit-prompt / evidence-rework 收尾
# ============================================================


def test_restart_run_finalizes_runref_and_snapshot(native_client: TestClient) -> None:
    headers = _register(native_client, "restart@example.com")
    pid = _create_project(native_client, headers)
    first_run = _run_to_completion(native_client, pid, headers)

    r = native_client.post(f"/api/projects/{pid}/runs/current/restart", headers=headers)
    assert r.status_code == 200, r.text

    last = _wait_last_run_finalized(native_client, pid, headers)
    assert last["run_id"] != first_run, "restart 应新建 run 身份"
    _assert_run_finalized(native_client, pid, headers, last)


def test_retry_node_finalizes_runref_and_snapshot(native_client: TestClient) -> None:
    headers = _register(native_client, "retry@example.com")
    pid = _create_project(native_client, headers)
    first_run = _run_to_completion(native_client, pid, headers)

    r = native_client.post(f"/api/projects/{pid}/nodes/reporter/retry", headers=headers)
    assert r.status_code == 200, r.text

    last = _wait_last_run_finalized(native_client, pid, headers)
    assert last["run_id"] != first_run, "native retry 应新建 run 身份"
    _assert_run_finalized(native_client, pid, headers, last)


def test_edit_prompt_finalizes_runref_and_snapshot(native_client: TestClient) -> None:
    headers = _register(native_client, "editprompt@example.com")
    pid = _create_project(native_client, headers)
    first_run = _run_to_completion(native_client, pid, headers)

    r = native_client.post(
        f"/api/projects/{pid}/nodes/reporter/edit-prompt",
        json={"prompt_override": "请用更保守的措辞重写报告，避免夸大结论。"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    last = _wait_last_run_finalized(native_client, pid, headers)
    assert last["run_id"] != first_run
    _assert_run_finalized(native_client, pid, headers, last)


def _seed_evidence(client: TestClient, pid: str, run_id: str) -> str:
    """往首个 extract 输出里注入一条 evidence（桩 extractor 的 evidences 为空）。

    node_outputs 按 run_id 作用域存取，必须带上当前 run 的 id 保存，
    否则路由侧 list_node_outputs（取最新 run 作用域）看不到这条注入。
    """
    storage = client.app.state.storage

    async def _inject() -> str:
        outputs = await storage.state_store.list_node_outputs(pid)
        nid, out = next(
            (nid, out) for nid, out in outputs.items() if isinstance(out, ExtractorOutput)
        )
        ev = Evidence(
            evidence_id="ev_lifecycle_test",
            source_id="src_1",
            product_name="Notion",
            source_url="https://example.com/pricing",
            source_type="pricing_page",
            source_authority=0.95,
            content="Notion 定价 $10/月",
            content_hash="hash1",
            collected_at=datetime.now(UTC),
            extracted_at=datetime.now(UTC),
            confidence=0.9,
        )
        await storage.state_store.save_node_output(
            pid, nid, out.model_copy(update={"evidences": [ev]}), run_id=run_id
        )
        return ev.evidence_id

    return asyncio.run(_inject())


def test_evidence_auto_rework_refinalizes_same_run(native_client: TestClient) -> None:
    """native auto-rework 延续同一 run 身份：收尾后快照应被刷新（含返工产物）。"""
    headers = _register(native_client, "rework@example.com")
    pid = _create_project(native_client, headers)
    run_id = _run_to_completion(native_client, pid, headers)
    evidence_id = _seed_evidence(native_client, pid, run_id)

    r = native_client.patch(
        f"/api/projects/{pid}/evidence/{evidence_id}?auto_rework=true",
        json={"disputed": True, "reason": "数字对不上"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["auto_rework_triggered"] is True

    assert _wait_project_settled(native_client, pid, headers) == "done"

    # 返工是同一 run 身份的延续：RunRef 仍是该 run_id 且有终态，快照按同主键刷新
    runs = native_client.get(f"/api/projects/{pid}/runs", headers=headers).json()["runs"]
    assert runs[-1]["run_id"] == run_id
    snap = _assert_run_finalized(native_client, pid, headers, runs[-1])
    # 快照应反映返工结果：reporter 出现返工版（reporter_v2）
    assert "reporter_v2" in snap["outputs"], (
        f"rework 后快照未刷新，outputs keys={list(snap['outputs'])}"
    )


# ============================================================
# 缺陷 2a：start_run 并发双击只成功一个
# ============================================================


async def test_concurrent_start_run_only_one_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """双击并发 POST /run：一个 202，一个 409（TOCTOU 修复）。

    memory storage 的 save_project 几乎不让出事件循环，用人工延迟模拟真实
    DB 延迟，让两个请求确实交错在「检查与 create_task 之间」。
    """
    _stub_env(monkeypatch)
    app = create_app(mode="memory")
    async with app.router.lifespan_context(app):
        _install_stub_registry(app)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/auth/register",
                json={"email": "race@example.com", "password": "secret123"},
            )
            assert r.status_code == 201, r.text
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await ac.post(
                "/api/projects",
                json={
                    "project_name": "race test",
                    "target_product": "Notion",
                    "competitors": ["Asana"],
                    "industry": "collaboration_saas",
                    "analysis_dimensions": ["feature_comparison"],
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]

            store = app.state.storage.state_store
            orig_save = store.save_project

            async def _slow_save(project) -> None:
                await asyncio.sleep(0.05)  # 模拟 postgres 往返，放大竞态窗口
                await orig_save(project)

            monkeypatch.setattr(store, "save_project", _slow_save)

            r1, r2 = await asyncio.gather(
                ac.post(f"/api/projects/{pid}/run", headers=headers),
                ac.post(f"/api/projects/{pid}/run", headers=headers),
            )
            assert sorted([r1.status_code, r2.status_code]) == [202, 409], (
                f"并发双击应恰好一个成功: {r1.status_code}, {r2.status_code}"
            )
            # 只应登记一个后台 run
            monkeypatch.setattr(store, "save_project", orig_save)
            runs = (await ac.get(f"/api/projects/{pid}/runs", headers=headers)).json()["runs"]
            assert len(runs) == 1, f"应只有一个 RunRef，实际 {len(runs)}"


# ============================================================
# 缺陷 2b：多 worker 环境变量下应用拒启
# ============================================================


@pytest.mark.parametrize(
    "env_name,env_value",
    [
        ("UVICORN_WORKERS", "2"),
        ("WEB_CONCURRENCY", "4"),
        ("GUNICORN_CMD_ARGS", "--workers 4 --bind 0.0.0.0:8000"),
        ("GUNICORN_CMD_ARGS", "-w=2"),
    ],
)
def test_multi_worker_env_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, env_name: str, env_value: str
) -> None:
    """多 worker 部署迹象 → lifespan 启动即拒（run 控制面是进程内状态）。"""
    _stub_env(monkeypatch)
    monkeypatch.setenv(env_name, env_value)
    app = create_app(mode="memory")
    with pytest.raises(RuntimeError, match="worker"):
        with TestClient(app):
            pass


def test_single_worker_env_boots_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 --workers 1 / WEB_CONCURRENCY=1 不误伤。"""
    _stub_env(monkeypatch)
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--workers 1")
    app = create_app(mode="memory")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_ensure_single_worker_message_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    """报错必须说清约束与原因（中文），点名进程内 run 控制面。"""
    from backend.api.run_lifecycle import ensure_single_worker

    monkeypatch.setenv("WEB_CONCURRENCY", "8")
    with pytest.raises(RuntimeError, match="单 worker"):
        ensure_single_worker()
