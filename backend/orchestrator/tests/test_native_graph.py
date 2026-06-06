"""原生 LangGraph 图集成测试。

用桩 Agent + 假 registry 跑通完整 4 阶段流水线(collect→extract→analyst→
reporter→qa),验证 Send 扇出、barrier 汇聚、QA Command 回环与轮次上限熔断。
**不调用真实 LLM**:桩 Agent 直接返回 schema-valid 输出(profile 复用
extractor mock fixtures),保证经 build_*_input 后能在图里流转。

桩 Agent 的 invoke 签名与 run_agent_node 契约一致:
``invoke(input_obj, *, trace_id, span_id, node_id) -> AgentOutputBase``。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.agents.extractor.fixtures import load_mock_profile
from backend.orchestrator.graph import build_native_graph
from backend.orchestrator.run_state import RunState
from backend.schemas import (
    AgentStatus,
    AnalysisResult,
    AnalystOutput,
    CollectorOutput,
    ExtractorOutput,
    Project,
    QAOutput,
    QARouting,
    QAStatus,
    QAVerdict,
    ReportDraft,
    ReporterOutput,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PROJECT_FILE = (
    _REPO_ROOT / "fixtures" / "mock_data" / "projects" / "collab_saas_demo.json"
)


def _load_demo_project(*, products: list[str]) -> Project:
    """加载 demo Project,把 target_product/competitors 改成给定产品集。

    products[0] 作为 target_product,其余作为 competitors;这样
    build_analyst_input 能拿到合法的 target+competitors。
    """
    data = json.loads(_DEMO_PROJECT_FILE.read_text(encoding="utf-8"))
    proj = Project.model_validate(data)
    return proj.model_copy(
        update={
            "target_product": products[0],
            "competitors": products[1:],
        }
    )


# ---------- 桩 Agent(返回 schema-valid 输出) ----------


class _StubCollector:
    """返回带空 raw_sources 的 CollectorOutput(extractor 只需 raw_sources 非 None)。"""

    name = "collector"
    mock = False

    def invoke(self, inp: Any, *, trace_id: str, span_id: str, node_id: str) -> CollectorOutput:
        return CollectorOutput(
            agent_name="collector",
            agent_version="1.0.0",
            task_id=inp.task_id,
            trace_id=trace_id,
            span_id=span_id,
            status=AgentStatus.SUCCESS,
            confidence=0.9,
            self_critique="",
            raw_sources=[],
            coverage_by_dimension={},
        )


class _StubExtractor:
    """复用 extractor mock fixtures 产出 CompetitorProfile(按 product_name)。"""

    name = "extractor"
    mock = False

    def invoke(self, inp: Any, *, trace_id: str, span_id: str, node_id: str) -> ExtractorOutput:
        profile = load_mock_profile(inp.product_name)
        if profile is None:
            pytest.skip(f"mock profile for {inp.product_name} missing")
        return ExtractorOutput(
            agent_name="extractor",
            agent_version="1.0.0",
            task_id=inp.task_id,
            trace_id=trace_id,
            span_id=span_id,
            status=AgentStatus.SUCCESS,
            confidence=0.9,
            self_critique="",
            profile=profile,
            evidences=[],
            schema_version="1.0.0",
        )


class _StubAnalyst:
    name = "analyst"
    mock = False

    def invoke(self, inp: Any, *, trace_id: str, span_id: str, node_id: str) -> AnalystOutput:
        return AnalystOutput(
            agent_name="analyst",
            agent_version="1.0.0",
            task_id=inp.task_id,
            trace_id=trace_id,
            span_id=span_id,
            status=AgentStatus.SUCCESS,
            confidence=0.85,
            self_critique="",
            result=AnalysisResult(
                target_product=inp.target_product,
                competitors=list(inp.competitors),
                dimensions={},
            ),
        )


class _StubReporter:
    """每次 invoke 产出一份 draft;version 由 _calls 自增(供回环验证 v2)。"""

    name = "reporter"
    mock = False

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, inp: Any, *, trace_id: str, span_id: str, node_id: str) -> ReporterOutput:
        self.calls += 1
        return ReporterOutput(
            agent_name="reporter",
            agent_version="1.0.0",
            task_id=inp.task_id,
            trace_id=trace_id,
            span_id=span_id,
            status=AgentStatus.SUCCESS,
            confidence=0.85,
            self_critique="",
            draft=ReportDraft(
                report_id=f"rpt_{self.calls}",
                version=self.calls,
                template_id=inp.template_id,
                sections=[],
                summary=f"draft v{self.calls}",
                metadata={},
            ),
        )


def _pass_verdict(vid: str = "v_pass") -> QAVerdict:
    return QAVerdict(
        verdict_id=vid,
        overall_status=QAStatus.PASS,
        dimension_results={},
        issues=[],
        routing=[],
        blocking=False,
    )


def _block_reporter_verdict(vid: str = "v_block") -> QAVerdict:
    return QAVerdict(
        verdict_id=vid,
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={},
        issues=[],
        routing=[QARouting(target_agent="reporter", reason="rewrite", payload={})],
        blocking=True,
    )


class _StubQA:
    """可编程 verdict 序列;第 N 次 invoke 返回 verdicts[min(N-1, len-1)]。"""

    name = "qa"
    mock = False

    def __init__(self, verdicts: list[QAVerdict]) -> None:
        self._verdicts = verdicts
        self.calls = 0

    def invoke(self, inp: Any, *, trace_id: str, span_id: str, node_id: str) -> QAOutput:
        idx = min(self.calls, len(self._verdicts) - 1)
        verdict = self._verdicts[idx]
        self.calls += 1
        return QAOutput(
            agent_name="qa",
            agent_version="1.0.0",
            task_id=inp.task_id,
            trace_id=trace_id,
            span_id=span_id,
            status=AgentStatus.SUCCESS,
            confidence=0.9,
            self_critique="",
            verdict=verdict,
        )


class _FakeRegistry:
    """实现 run_agent_node 需要的最小 registry 协议。

    .get(name)               → 缓存桩实例
    .make_reporter(...)      → 复用同一 reporter 桩(便于断言 calls)
    .make_qa(...)            → 复用同一 qa 桩
    evidence_provider/evidence_db 参数被忽略(桩不需要 evidence)。
    """

    def __init__(self, qa: _StubQA) -> None:
        self._reporter = _StubReporter()
        self._qa = qa
        self._agents: dict[str, Any] = {
            "collector": _StubCollector(),
            "extractor": _StubExtractor(),
            "analyst": _StubAnalyst(),
            "reporter": self._reporter,
            "qa": self._qa,
        }

    def get(self, name: str) -> Any:
        return self._agents[name]

    def make_reporter(self, *, evidence_provider: Any = None) -> Any:
        return self._reporter

    def make_qa(self, *, evidence_db: Any = None) -> Any:
        return self._qa


def _initial_state(project: Project, products: list[str]) -> dict:
    return RunState(
        project_id=project.project_id,
        run_id="run_test",
        analysis_mode=project.analysis_mode.value,
        products=list(products),
    ).model_dump()


async def _run(app: Any, project: Project, products: list[str], thread: str) -> dict:
    return await app.ainvoke(
        _initial_state(project, products),
        {"configurable": {"thread_id": thread}},
    )


# ---------- tests ----------


async def test_multi_product_runs_all_stages() -> None:
    products = ["Notion", "Asana"]
    project = _load_demo_project(products=products)
    registry = _FakeRegistry(_StubQA([_pass_verdict()]))
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "multi")

    keys = set(final["outputs"].keys())
    assert keys == {
        "collect.Notion",
        "collect.Asana",
        "extract.Notion",
        "extract.Asana",
        "analyst",
        "reporter",
        "qa",
    }
    # history 应含每个阶段的 NodeRun
    nodes = sorted(h.node for h in final["history"])
    assert nodes.count("collect") == 2
    assert nodes.count("extract") == 2
    assert "analyst" in nodes and "reporter" in nodes and "qa" in nodes
    assert final["aborted"] is False


async def test_single_product() -> None:
    products = ["Notion"]
    project = _load_demo_project(products=products)
    registry = _FakeRegistry(_StubQA([_pass_verdict()]))
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "single")

    keys = set(final["outputs"].keys())
    assert keys == {"collect.Notion", "extract.Notion", "analyst", "reporter", "qa"}
    assert sum(1 for h in final["history"] if h.node == "collect") == 1
    assert sum(1 for h in final["history"] if h.node == "extract") == 1
    assert final["aborted"] is False


async def test_qa_cycle_produces_reporter_v2() -> None:
    products = ["Notion"]
    project = _load_demo_project(products=products)
    # round 1: block→reporter; round 2: pass
    registry = _FakeRegistry(
        _StubQA([_block_reporter_verdict(), _pass_verdict()])
    )
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "cycle")

    reporter_runs = [h for h in final["history"] if h.node == "reporter"]
    assert len(reporter_runs) == 2
    # 第二个 reporter NodeRun round == 2
    assert reporter_runs[1].round == 2
    # 最终 reporter draft 是 v2
    assert final["outputs"]["reporter"].draft.version == 2
    assert final["aborted"] is False


async def test_round_cap_aborts() -> None:
    products = ["Notion"]
    project = _load_demo_project(products=products)
    # 永远阻塞回 reporter → 触发轮次上限熔断
    registry = _FakeRegistry(_StubQA([_block_reporter_verdict()]))
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "cap")

    assert final["aborted"] is True
    assert "max_rounds" in final["abort_reason"]
