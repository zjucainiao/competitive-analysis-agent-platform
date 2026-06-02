"""PATCH /api/projects/{id}/reports/{rid}/paragraphs/{pid} 单测。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.schemas import (
    AgentStatus,
    Project,
    ProjectStatus,
    ReportDraft,
    ReportParagraph,
    ReportSection,
    ReporterOutput,
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # lifespan 会调 AgentRegistry.from_env → 要求至少一种 LLM key。测试只命中 PATCH
    # 路径不发 LLM 请求，给个假 key 兜住即可。monkeypatch 保证不污染其他测试。
    monkeypatch.setenv("DOUBAO_API_KEY", "test_key")
    monkeypatch.setenv("DOUBAO_MODEL", "ep-test")
    app = create_app(mode="memory")
    with TestClient(app) as c:
        yield c


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


def test_patch_paragraph_updates_text(client: TestClient) -> None:
    pid, report_id = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p1",
        json={"text": "改后的文案 1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paragraph"]["text"] == "改后的文案 1"
    assert body["paragraph"]["paragraph_id"] == "p1"
    assert body["manual_edits"] == 1
    assert body["edit_rate"] == 0.5  # 1 改 / 2 段


def test_patch_paragraph_unknown_returns_404(client: TestClient) -> None:
    pid, report_id = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p_does_not_exist",
        json={"text": "x"},
    )
    assert r.status_code == 404


def test_patch_unknown_project_returns_404(client: TestClient) -> None:
    r = client.patch(
        "/api/projects/proj_nope/reports/reporter/paragraphs/p1",
        json={"text": "x"},
    )
    assert r.status_code == 404


def test_patch_non_reporter_node_rejected(client: TestClient) -> None:
    pid, _ = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/collect.notion/paragraphs/p1",
        json={"text": "x"},
    )
    assert r.status_code == 400


def test_patch_accumulates_edits(client: TestClient) -> None:
    pid, report_id = _seed_project_and_report(client)
    client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p1",
        json={"text": "改 1"},
    )
    r2 = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p2",
        json={"text": "改 2"},
    )
    assert r2.status_code == 200
    assert r2.json()["manual_edits"] == 2
    assert r2.json()["edit_rate"] == 1.0


def test_patch_flags_optional(client: TestClient) -> None:
    pid, report_id = _seed_project_and_report(client)
    r = client.patch(
        f"/api/projects/{pid}/reports/{report_id}/paragraphs/p2",
        json={"text": "修改并降级为软结论", "is_soft_conclusion": True},
    )
    assert r.status_code == 200
    assert r.json()["paragraph"]["is_soft_conclusion"] is True
