"""Executor 单测：input 解包 / 控制节点 / 重试 / 超时。

注意：本文件**不**调用真实 Agent。涉及 Agent 调用的端到端验证都在
``test_real_smoke.py`` 和 API 层 ``test_real_full_chain.py`` 里。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.agents.collector import build_default_registry
from backend.orchestrator import AgentRegistry, NullTracer
from backend.orchestrator.executor import Executor
from backend.orchestrator.planner import Planner
from backend.schemas import (
    AgentOutputBase,
    AgentStatus,
    AnalystOutput,
    CollectorOutput,
    DAGNode,
    DAGPlan,
    ExtractorOutput,
    NodeStatus,
    NodeType,
    Project,
    QAOutput,
    QAStatus,
    QAVerdict,
    ReporterOutput,
)
from backend.schemas.evidence import CollectDimension


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PROJECT_FILE = (
    _REPO_ROOT / "fixtures" / "mock_data" / "projects" / "collab_saas_demo.json"
)


def _load_demo_project() -> Project:
    data = json.loads(_DEMO_PROJECT_FILE.read_text(encoding="utf-8"))
    return Project.model_validate(data)


def _node_by_id(plan: DAGPlan, node_id: str) -> DAGNode:
    return next(n for n in plan.nodes if n.node_id == node_id)


class _FakeLLM:
    """占位 LLM；本文件不会真的调 .chat。"""

    def chat(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("unit test should not hit LLM")


@pytest.fixture
def project() -> Project:
    return _load_demo_project()


@pytest.fixture
def plan(project: Project) -> DAGPlan:
    return Planner().plan(project)


@pytest.fixture
def registry() -> AgentRegistry:
    """构造一个 real-mode registry，但不会被本文件实际调用 LLM。"""
    return AgentRegistry(
        llm=_FakeLLM(),
        tracer=NullTracer(),
        tools=build_default_registry(),
    )


@pytest.fixture
def executor(registry: AgentRegistry, project: Project) -> Executor:
    return Executor(registry=registry, project=project, trace_id="trace_test")


# ---------- 控制节点（不调用 Agent） ----------


async def test_start_node_returns_success_immediately(
    plan: DAGPlan, executor: Executor
) -> None:
    start = _node_by_id(plan, "start")
    result = await executor.execute(start, outputs={})
    assert result.status == NodeStatus.SUCCESS
    assert result.output is None
    assert result.duration_ms == 0


async def test_end_node_returns_success(plan: DAGPlan, executor: Executor) -> None:
    end = _node_by_id(plan, "end")
    result = await executor.execute(end, outputs={})
    assert result.status == NodeStatus.SUCCESS


async def test_parallel_join_returns_success(
    plan: DAGPlan, executor: Executor
) -> None:
    join = _node_by_id(plan, "join_extract")
    assert join.node_type == NodeType.PARALLEL_JOIN
    result = await executor.execute(join, outputs={})
    assert result.status == NodeStatus.SUCCESS


# ---------- input 解包（不实际调用 Agent） ----------


async def test_collector_input_dimensions_from_metadata(
    plan: DAGPlan, executor: Executor
) -> None:
    """构造的 CollectorInput 应读 metadata 里的 collect_dimensions。"""
    node = _node_by_id(plan, "collect.notion")
    inp = executor._build_collector_input(node, qa_feedback=None)
    assert inp.product_name == "Notion"
    assert inp.industry == "collaboration_saas"
    assert set(inp.dimensions) == {
        CollectDimension.HOMEPAGE,
        CollectDimension.FEATURES,
        CollectDimension.PRICING,
        CollectDimension.HELP_DOCS,
        CollectDimension.REVIEWS,
    }


async def test_collector_missing_metadata_fails_fast(
    project: Project, registry: AgentRegistry
) -> None:
    bad_node = DAGNode(
        node_id="collect.broken",
        project_id=project.project_id,
        node_type=NodeType.AGENT_CALL,
        agent_name="collector",
        input_refs=["start"],
        metadata={},
    )
    ex = Executor(registry=registry, project=project)
    result = await ex.execute(bad_node, outputs={})
    assert result.status == NodeStatus.FAILED
    assert result.error is not None
    assert result.error.code == "INPUT_BUILD_FAILED"
    assert result.error.retriable is False


async def test_extractor_industry_schema_id_derived(
    plan: DAGPlan, executor: Executor, project: Project
) -> None:
    extractor_node = _node_by_id(plan, "extract.notion")
    fake_collector_out = CollectorOutput(
        agent_name="collector",
        agent_version="1.0.0",
        task_id="collect.notion",
        trace_id="trace_test",
        span_id="span_x",
        status=AgentStatus.SUCCESS,
        confidence=0.9,
        self_critique="",
        raw_sources=[],
        coverage_by_dimension={},
    )
    inp = executor._build_extractor_input(
        extractor_node, outputs={"collect.notion": fake_collector_out}, qa_feedback=None
    )
    assert inp.industry_schema_id == "collaboration_saas_v1"


async def test_extractor_missing_upstream_input_fails(
    plan: DAGPlan, executor: Executor
) -> None:
    extractor_node = _node_by_id(plan, "extract.notion")
    result = await executor.execute(extractor_node, outputs={})
    assert result.status == NodeStatus.FAILED
    assert result.error.code == "INPUT_BUILD_FAILED"


async def test_reporter_input_requires_analyst(
    plan: DAGPlan, executor: Executor
) -> None:
    from backend.orchestrator.executor import BuildInputError

    reporter_node = _node_by_id(plan, "reporter")
    with pytest.raises(BuildInputError):
        executor._build_reporter_input(reporter_node, outputs={}, qa_feedback=None)


async def test_latest_output_helper_picks_highest_revision() -> None:
    """同时存在 base id + 多个 _v 版本时，选 revision 最高的。"""
    outputs: dict[str, AgentOutputBase] = {
        "analyst": _make_analyst_output("analyst"),
        "analyst_v2": _make_analyst_output("analyst_v2"),
        "analyst_v3": _make_analyst_output("analyst_v3"),
    }
    picked = Executor._latest_output(outputs, prefix_or_id="analyst")
    assert picked.task_id == "analyst_v3"


async def test_qa_input_collects_prior_verdicts(
    plan: DAGPlan, executor: Executor
) -> None:
    qa_node = _node_by_id(plan, "qa")
    outputs = _build_synthetic_outputs()
    outputs["qa_v1"] = _make_qa_output(verdict_id="vd_old")

    inp = executor._build_qa_input(qa_node, outputs=outputs, qa_feedback=None)
    assert len(inp.prior_verdicts) == 1
    assert inp.prior_verdicts[0].verdict_id == "vd_old"


# ---------- 重试 / 超时（用桩 agent 直接塞 registry._cache） ----------


async def test_node_timeout_triggers_retry_then_failure(
    project: Project, plan: DAGPlan, registry: AgentRegistry
) -> None:
    """超时 max_retries 次后失败；非 hybrid 模式不降级（hybrid 已移除）。"""

    class _SlowAgent:
        name = "collector"

        def invoke(self, inp: Any, **kwargs: Any) -> Any:
            import time

            time.sleep(0.5)
            raise RuntimeError("unreachable")

    registry._cache["collector"] = _SlowAgent()  # type: ignore[assignment]
    node = _node_by_id(plan, "collect.notion").model_copy(
        update={"timeout_ms": 50, "max_retries": 1}
    )

    proj_real = project.model_copy(update={"mode": "real"})
    ex = Executor(registry=registry, project=proj_real, backoff_base=0.0)
    result = await ex.execute(node, outputs={})
    assert result.status == NodeStatus.FAILED
    assert result.error.code == "LLM_TIMEOUT"
    # max_retries=1 → 2 次尝试
    assert result.metadata["attempts"] == 2


# ---------- helpers ----------


def _make_analyst_output(tag: str) -> AnalystOutput:
    from backend.schemas import AnalysisResult

    return AnalystOutput(
        agent_name="analyst",
        agent_version="1.0.0",
        task_id=tag,
        trace_id="trace_test",
        span_id=f"span_{tag}",
        status=AgentStatus.SUCCESS,
        confidence=0.85,
        self_critique="",
        result=AnalysisResult(
            target_product="Notion",
            competitors=["ClickUp"],
            dimensions={},
        ),
    )


def _make_qa_output(*, verdict_id: str) -> QAOutput:
    return QAOutput(
        agent_name="qa",
        agent_version="1.0.0",
        task_id=f"qa_{verdict_id}",
        trace_id="trace_test",
        span_id=f"span_{verdict_id}",
        status=AgentStatus.SUCCESS,
        confidence=0.9,
        self_critique="",
        verdict=QAVerdict(
            verdict_id=verdict_id,
            overall_status=QAStatus.PASS,
            dimension_results={},
            issues=[],
            routing=[],
            blocking=False,
        ),
    )


def _build_synthetic_outputs() -> dict[str, AgentOutputBase]:
    """造一份足以走通 _build_qa_input 的 outputs。"""
    from backend.agents.extractor.fixtures import load_mock_profile
    from backend.schemas import ReportDraft

    outputs: dict[str, AgentOutputBase] = {}
    notion = load_mock_profile("Notion")
    if notion is None:
        pytest.skip("notion fixture missing")
    outputs["extract.notion"] = ExtractorOutput(
        agent_name="extractor",
        agent_version="1.0.0",
        task_id="extract.notion",
        trace_id="trace_test",
        span_id="span_e",
        status=AgentStatus.SUCCESS,
        confidence=0.9,
        self_critique="",
        profile=notion,
        evidences=[],
        schema_version="1.0.0",
    )
    outputs["analyst"] = _make_analyst_output("base")
    outputs["reporter"] = ReporterOutput(
        agent_name="reporter",
        agent_version="1.0.0",
        task_id="reporter",
        trace_id="trace_test",
        span_id="span_r",
        status=AgentStatus.SUCCESS,
        confidence=0.85,
        self_critique="",
        draft=ReportDraft(
            report_id="rpt_1",
            version=1,
            template_id="standard_v1",
            sections=[],
            summary="placeholder",
            metadata={},
        ),
    )
    return outputs
