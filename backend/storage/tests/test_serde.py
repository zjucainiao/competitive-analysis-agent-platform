"""serde：AgentOutputBase 多态序列化/反序列化。"""

from __future__ import annotations

import pytest

from backend.schemas import (
    AgentStatus,
    CollectDimension,
    CollectorOutput,
    ReportDraft,
    ReporterOutput,
    ReportSection,
)
from backend.storage.serde import dump_output, load_output


def test_dump_load_collector_output_roundtrip():
    out = CollectorOutput(
        agent_name="collector",
        agent_version="1.0.0",
        task_id="t1",
        trace_id="tr",
        span_id="sp",
        status=AgentStatus.SUCCESS,
        confidence=0.92,
        self_critique="",
        raw_sources=[],
        coverage_by_dimension={d: 0 for d in CollectDimension},
    )
    payload = dump_output(out)
    assert payload["agent_name"] == "collector"
    rebuilt = load_output(payload)
    assert isinstance(rebuilt, CollectorOutput)
    assert rebuilt.task_id == "t1"
    assert rebuilt.confidence == pytest.approx(0.92)


def test_load_unknown_agent_raises():
    with pytest.raises(ValueError, match="unknown agent_name"):
        load_output({"agent_name": "ghost", "task_id": "x"})


def test_load_missing_agent_name_raises():
    with pytest.raises(ValueError, match="missing 'agent_name'"):
        load_output({"task_id": "x"})


def test_dump_load_reporter_output_roundtrip():
    draft = ReportDraft(
        report_id="rpt_1",
        version=1,
        template_id="standard_v1",
        sections=[
            ReportSection(
                section_id="s1", title="overview", order=1, paragraphs=[]
            )
        ],
        summary="",
        metadata={},
    )
    out = ReporterOutput(
        agent_name="reporter",
        agent_version="1.0.0",
        task_id="t1",
        trace_id="tr",
        span_id="sp",
        status=AgentStatus.SUCCESS,
        confidence=0.8,
        self_critique="",
        draft=draft,
    )
    payload = dump_output(out)
    rebuilt = load_output(payload)
    assert isinstance(rebuilt, ReporterOutput)
    assert rebuilt.draft.report_id == "rpt_1"
    assert rebuilt.draft.sections[0].section_id == "s1"
