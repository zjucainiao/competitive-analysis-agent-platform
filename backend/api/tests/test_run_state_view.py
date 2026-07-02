"""RunStateView 端点的 API 测试（Phase 2 Stage B）。

驱动一次 **native** run（ORCH_ENGINE=native + 桩 registry，跑在 memory storage），
然后断言：
- ``GET /run-state``（live）返回含 5 阶段、按产品 instances、reporter revisions、
  verdicts、metrics 的 RunStateView；
- ``GET /runs/{run_id}/view``（historical）从落库快照装配，history 已被填充；
- 旧的 ``/state`` 端点行为不变（仍返回 DAGPlan 形状）。

桩 registry 复用 orchestrator 集成测试里的 _FakeRegistry / _StubQA，不触真实 LLM。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.orchestrator import Orchestrator
from backend.orchestrator.tests.test_native_graph import (
    _block_reporter_verdict,
    _FakeRegistry,
    _pass_verdict,
    _StubQA,
)


@pytest.fixture
def native_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """native 引擎 + 桩 registry 的 TestClient。

    create_app 启动时 AgentRegistry.from_env 需要某个 LLM key（值任意，不真调）；
    启动后用桩 registry 覆盖 app.state，并重建 orchestrator 指向桩。
    """
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "*")
    monkeypatch.setenv("ORCH_ENGINE", "native")

    app = create_app(mode="memory")
    with TestClient(app) as c:
        # 用一次返工序列的桩 QA：reporter 会跑 2 轮 → reporter 有 2 revisions。
        registry = _FakeRegistry(_StubQA([_block_reporter_verdict(), _pass_verdict()]))
        c.app.state.agent_registry = registry
        c.app.state.orchestrator = Orchestrator(
            registry=registry, storage=c.app.state.storage
        )
        yield c


def _register(client: TestClient, email: str) -> dict[str, str]:
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_project(client: TestClient, headers: dict) -> str:
    r = client.post(
        "/api/projects",
        json={
            "project_name": "view test",
            "target_product": "Notion",
            "competitors": ["Asana"],
            "industry": "collaboration_saas",
            "analysis_dimensions": ["feature_comparison"],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _run_to_completion(client: TestClient, pid: str, headers: dict) -> str:
    """触发一次 run 并轮询 /runs 直到该 run 终态；返回 run_id。"""
    r = client.post(f"/api/projects/{pid}/run", headers=headers)
    assert r.status_code == 202, r.text
    deadline = time.time() + 20
    while time.time() < deadline:
        runs = client.get(f"/api/projects/{pid}/runs", headers=headers).json()["runs"]
        if runs and runs[-1]["final_status"] is not None:
            return runs[-1]["run_id"]
        time.sleep(0.05)
    raise AssertionError("run did not complete in time")


def test_run_state_view_live_after_native_run(native_client: TestClient) -> None:
    headers = _register(native_client, "alice@example.com")
    pid = _create_project(native_client, headers)
    _run_to_completion(native_client, pid, headers)

    r = native_client.get(f"/api/projects/{pid}/run-state", headers=headers)
    assert r.status_code == 200, r.text
    view = r.json()

    # 5 个静态阶段始终存在，按顺序
    assert [s["stage"] for s in view["stages"]] == [
        "collect",
        "extract",
        "analyst",
        "reporter",
        "qa",
    ]

    stages = {s["stage"]: s for s in view["stages"]}
    # collect/extract 按产品出 instances
    collect_products = {i["product"] for i in stages["collect"]["instances"]}
    extract_products = {i["product"] for i in stages["extract"]["instances"]}
    assert collect_products == {"Notion", "Asana"}
    assert extract_products == {"Notion", "Asana"}
    # 产品阶段 revisions 为空
    assert stages["collect"]["revisions"] == []

    # reporter 经一次返工 → 2 个 revisions（round 1,2），v2 run_ref 带后缀
    reporter_rounds = [r_["round"] for r_ in stages["reporter"]["revisions"]]
    assert reporter_rounds == [1, 2]
    refs = {r_["round"]: r_["run_ref"] for r_ in stages["reporter"]["revisions"]}
    assert refs[2] == "reporter_v2"
    # 非产品阶段 instances 为空
    assert stages["reporter"]["instances"] == []

    # verdicts 透传（返工序列共 2 条）
    assert len(view["verdicts"]) >= 2
    # metrics 已计算并挂上
    assert view["metrics"] is not None
    assert view["status"] in ("done", "aborted")
    assert view["qa_round"] >= 1


def test_run_state_overlays_node_output_edits(
    native_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-NODEOUTPUTS-VS-CHECKPOINT：人工编辑写进 node_outputs 后，/run-state 应反映它，

    而不是一直显示 checkpoint 里的旧内容。用 monkeypatch 让 list_node_outputs 返回一份
    被编辑过的 reporter(summary 改了)，断言视图 outputs['reporter'] 取到编辑后的内容。
    """
    from backend.schemas import ReportDraft, ReporterOutput

    headers = _register(native_client, "dave@example.com")
    pid = _create_project(native_client, headers)
    _run_to_completion(native_client, pid, headers)

    edited = ReporterOutput(
        agent_name="reporter",
        agent_version="1.0.0",
        task_id="reporter",
        trace_id="t",
        span_id="s",
        status="success",
        confidence=0.9,
        self_critique="",
        tokens_input=0,
        tokens_output=0,
        cost_usd=0.0,
        duration_ms=0,
        errors=[],
        draft=ReportDraft(
            report_id="rpt_1",
            version=1,
            template_id="standard_v1",
            sections=[],
            summary="HUMAN_EDITED_SUMMARY",
            metadata={},
        ),
    )
    store = native_client.app.state.storage.state_store

    async def _fake_list(project_id: str):
        return {"reporter": edited}

    monkeypatch.setattr(store, "list_node_outputs", _fake_list)

    r = native_client.get(f"/api/projects/{pid}/run-state", headers=headers)
    assert r.status_code == 200, r.text
    view = r.json()
    # 持久化(编辑后) node_outputs 覆盖 checkpoint 同名 ref → 视图取到编辑内容
    assert view["outputs"]["reporter"]["draft"]["summary"] == "HUMAN_EDITED_SUMMARY"


def test_run_view_historical_has_populated_history(native_client: TestClient) -> None:
    headers = _register(native_client, "bob@example.com")
    pid = _create_project(native_client, headers)
    run_id = _run_to_completion(native_client, pid, headers)

    # 快照里 history 应已被 native checkpoint 填充
    snap = native_client.get(
        f"/api/projects/{pid}/runs/{run_id}/state", headers=headers
    ).json()
    assert snap["history"], "RunSnapshot.history should be populated for native run"

    r = native_client.get(
        f"/api/projects/{pid}/runs/{run_id}/view", headers=headers
    )
    assert r.status_code == 200, r.text
    view = r.json()
    assert [s["stage"] for s in view["stages"]] == [
        "collect",
        "extract",
        "analyst",
        "reporter",
        "qa",
    ]
    stages = {s["stage"]: s for s in view["stages"]}
    assert {i["product"] for i in stages["collect"]["instances"]} == {"Notion", "Asana"}
    # history 非空（回放真相源）
    assert view["history"]
    # reporter 返工两轮在历史视图里也应可见
    assert [r_["round"] for r_ in stages["reporter"]["revisions"]] == [1, 2]


def test_run_state_view_unknown_run_404(native_client: TestClient) -> None:
    headers = _register(native_client, "carol@example.com")
    pid = _create_project(native_client, headers)
    r = native_client.get(
        f"/api/projects/{pid}/runs/run_nope/view", headers=headers
    )
    assert r.status_code == 404


def test_run_state_view_requires_auth(native_client: TestClient) -> None:
    headers = _register(native_client, "dave@example.com")
    pid = _create_project(native_client, headers)
    # 无 token → 401
    assert native_client.get(f"/api/projects/{pid}/run-state").status_code == 401
    # 别人项目 → 403
    other = _register(native_client, "eve@example.com")
    assert (
        native_client.get(f"/api/projects/{pid}/run-state", headers=other).status_code
        == 403
    )
