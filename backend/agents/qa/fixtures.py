"""QA 测试 / demo 用 fixture 装载器。

从 ``fixtures/mock_data/`` 加载 ReportDraft / AnalysisResult / CompetitorProfile，
组装成 QAInput；供单元测试、Orchestrator 联调脚本复用。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.schemas import (
    AnalysisResult,
    CompetitorProfile,
    Evidence,
    QAInput,
    QAVerdict,
    ReportDraft,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_ROOT = _REPO_ROOT / "fixtures" / "mock_data"
_REPORT_DRAFTS = _FIXTURE_ROOT / "report_drafts"
_ANALYSIS = _FIXTURE_ROOT / "analysis_results"
_PROFILES = _FIXTURE_ROOT / "competitor_profiles"
_EVIDENCES = _FIXTURE_ROOT / "evidences"
_QA_VERDICTS = _FIXTURE_ROOT / "qa_verdicts"


def load_report_draft(name: str = "draft_v1") -> ReportDraft:
    path = _REPORT_DRAFTS / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"report draft fixture not found: {path}")
    return ReportDraft.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_analysis_result(name: str = "analysis_full") -> AnalysisResult:
    path = _ANALYSIS / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"analysis fixture not found: {path}")
    return AnalysisResult.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def load_competitor_profile(name: str) -> CompetitorProfile:
    path = _PROFILES / f"{name.lower().replace(' ', '')}.json"
    if not path.exists():
        raise FileNotFoundError(f"profile fixture not found: {path}")
    return CompetitorProfile.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def load_competitor_profiles(
    names: list[str],
) -> dict[str, CompetitorProfile]:
    return {n: load_competitor_profile(n) for n in names}


def load_evidence_db() -> dict[str, Evidence]:
    path = _EVIDENCES / "evidence_db.jsonl"
    out: dict[str, Evidence] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ev = Evidence.model_validate(json.loads(line))
        out[ev.evidence_id] = ev
    return out


def load_qa_verdict(name: str) -> QAVerdict:
    """name ∈ {pass, needs_revision}."""
    path = _QA_VERDICTS / f"{name}.json"
    return QAVerdict.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_demo_input(
    *,
    draft_name: str = "draft_v1",
    analysis_name: str = "analysis_full",
    profile_names: list[str] | None = None,
    prior_verdicts: list[QAVerdict] | None = None,
    task_id: str = "task-demo",
    project_id: str = "proj-demo",
    trace_id: str = "trace-demo",
    span_id: str = "span-qa",
) -> QAInput:
    """组装 demo 用 QAInput（默认 Notion / ClickUp / Asana 三件套）。"""
    profile_names = profile_names if profile_names is not None else [
        "Notion",
        "ClickUp",
        "Asana",
    ]
    return QAInput(
        task_id=task_id,
        project_id=project_id,
        trace_id=trace_id,
        span_id=span_id,
        draft=load_report_draft(draft_name),
        analysis=load_analysis_result(analysis_name),
        profiles=load_competitor_profiles(profile_names),
        evidence_store_handle=None,
        prior_verdicts=list(prior_verdicts or []),
    )


__all__ = [
    "load_analysis_result",
    "load_competitor_profile",
    "load_competitor_profiles",
    "load_demo_input",
    "load_evidence_db",
    "load_qa_verdict",
    "load_report_draft",
]
