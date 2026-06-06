"""PATCH /api/projects/{id}/reports/{rid}/paragraphs/{pid} 单测。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.api.security import create_access_token
from backend.schemas import (
    AgentStatus,
    Project,
    ProjectStatus,
    ReportDraft,
    ReportParagraph,
    ReportSection,
    ReporterOutput,
    User,
)

_OWNER_ID = "u1"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # lifespan 会调 AgentRegistry.from_env → 要求至少一种 LLM key。测试只命中 PATCH
    # 路径不发 LLM 请求，给个假 key 兜住即可。monkeypatch 保证不污染其他测试。
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    app = create_app(mode="memory")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(client: TestClient) -> dict[str, str]:
    """往 storage 塞一个 user_id=u1 的用户，返回其 Bearer header。

    PATCH 路由经 get_owned_project 校验 owner == 当前用户，故项目 owner 也用 u1。
    """
    import asyncio

    storage = client.app.state.storage

    async def _seed_user() -> None:
        await storage.state_store.create_user(
            User(
                user_id=_OWNER_ID,
                email="u1@test.com",
                password_hash="x",
                display_name="U1",
                created_at=datetime.now(timezone.utc),
            )
        )

    asyncio.run(_seed_user())
    return {"Authorization": f"Bearer {create_access_token(_OWNER_ID)}"}


def _seed_project_and_report(client: TestClient) -> tuple[str, str]:
    """通过内部 storage 直接植入一个 project + reporter output，返回 (pid, report_node_id)。"""
    storage = client.app.state.storage

    project = Project(
        project_id="proj_test_patch",
        project_name="patch test",
        owner="u1",
        created_at=datetime.now(timezone.utc),
        target_product="Notion",
        competitors=["Asana"],
        industry="collaboration_saas",
        analysis_dimensions=[],
        status=ProjectStatus.DONE,
    )

    paragraphs = [
        ReportParagraph(
            paragraph_id="p1", text="原文 1", evidence_ids=["ev1"], is_quantitative=False,
        ),
        ReportParagraph(
            paragraph_id="p2", text="原文 2", evidence_ids=["ev2"], is_quantitative=True,
        ),
    ]
    section = ReportSection(
        section_id="sec1", title="Overview", order=1, paragraphs=paragraphs,
    )
    draft = ReportDraft(
        report_id="rpt_1", version=1, template_id="standard_v1",
        sections=[section], summary="s", metadata={},
    )
    output = ReporterOutput(
        agent_name="reporter", agent_version="1.0.0",
        task_id="reporter", trace_id="t", span_id="s",
        status=AgentStatus.SUCCESS, confidence=0.85, self_critique="",
        draft=draft,
    )

    import asyncio

    async def _save() -> None:
        await storage.state_store.save_project(project)
        await storage.state_store.save_node_output(project.project_id, "reporter", output)

    asyncio.run(_save())
    return project.project_id, "reporter"


def test_patch_paragraph_updates_text(client: TestClient, auth: dict) -> None:
    pid, report_id = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p1",
        json={"text": "改后的文案 1"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paragraph"]["text"] == "改后的文案 1"
    assert body["paragraph"]["paragraph_id"] == "p1"
    assert body["manual_edits"] == 1
    assert body["edit_rate"] == 0.5  # 1 改 / 2 段


def test_patch_paragraph_unknown_returns_404(client: TestClient, auth: dict) -> None:
    pid, report_id = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p_does_not_exist",
        json={"text": "x"},
        headers=auth,
    )
    assert r.status_code == 404


def test_patch_unknown_project_returns_404(client: TestClient, auth: dict) -> None:
    r = client.patch(
        "/api/projects/proj_nope/reports/reporter/paragraphs/p1",
        json={"text": "x"},
        headers=auth,
    )
    assert r.status_code == 404


def test_patch_requires_auth(client: TestClient) -> None:
    """不带 token → 401（鉴权隔离）。"""
    pid, report_id = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p1",
        json={"text": "x"},
    )
    assert r.status_code == 401


def test_patch_other_users_project_forbidden(client: TestClient, auth: dict) -> None:
    """已登录但项目属于别人 → 403（越权拦截）。"""
    import asyncio

    storage = client.app.state.storage
    other = Project(
        project_id="proj_other_owner",
        project_name="x",
        owner="someone_else",
        created_at=datetime.now(timezone.utc),
        target_product="X",
        competitors=[],
        industry="collaboration_saas",
        analysis_dimensions=[],
        status=ProjectStatus.DONE,
    )
    asyncio.run(storage.state_store.save_project(other))
    r = client.patch(
        "/api/projects/proj_other_owner/reports/reporter/paragraphs/p1",
        json={"text": "x"},
        headers=auth,
    )
    assert r.status_code == 403


def test_patch_non_reporter_node_rejected(client: TestClient, auth: dict) -> None:
    pid, _ = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/collect.notion/paragraphs/p1",
        json={"text": "x"},
        headers=auth,
    )
    assert r.status_code == 400


def test_patch_accumulates_edits(client: TestClient, auth: dict) -> None:
    pid, report_id = _seed_project_and_report(client)
    client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p1",
        json={"text": "改 1"},
        headers=auth,
    )
    r2 = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p2",
        json={"text": "改 2"},
        headers=auth,
    )
    assert r2.status_code == 200
    assert r2.json()["manual_edits"] == 2
    assert r2.json()["edit_rate"] == 1.0


def test_patch_flags_optional(client: TestClient, auth: dict) -> None:
    pid, report_id = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p2",
        json={"text": "修改并降级为软结论", "is_soft_conclusion": True},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["paragraph"]["is_soft_conclusion"] is True


def _reporter_output(node_id: str, n_paragraphs: int) -> ReporterOutput:
    paras = [
        ReportParagraph(
            paragraph_id=f"{node_id}_p{i}",
            text="x",
            evidence_ids=[],
            is_quantitative=False,
        )
        for i in range(n_paragraphs)
    ]
    draft = ReportDraft(
        report_id=f"rpt_{node_id}",
        version=1,
        template_id="standard_v1",
        sections=[
            ReportSection(section_id="s", title="t", order=1, paragraphs=paras)
        ],
        summary="",
        metadata={},
    )
    return ReporterOutput(
        agent_name="reporter",
        agent_version="1.0.0",
        task_id=node_id,
        trace_id="t",
        span_id="s",
        status=AgentStatus.SUCCESS,
        confidence=0.9,
        self_critique="",
        draft=draft,
    )


def test_latest_report_total_paragraphs_picks_highest_version() -> None:
    """evidence 争议重算 edit_rate 用的段落计数：取最新版本 reporter draft。"""
    from backend.api.routes.evidence import _latest_report_total_paragraphs

    outputs = {
        "reporter": _reporter_output("reporter", 2),
        "reporter_v2": _reporter_output("reporter_v2", 5),
    }
    assert _latest_report_total_paragraphs(outputs) == 5  # 取 v2
    assert _latest_report_total_paragraphs(
        {"reporter": _reporter_output("reporter", 3)}
    ) == 3
    assert _latest_report_total_paragraphs({}) == 0
