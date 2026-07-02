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
from backend.orchestrator.nodes import make_nodes
from backend.orchestrator.run_state import RunState
from backend.schemas import (
    AgentStatus,
    AnalysisResult,
    AnalystOutput,
    CollectorOutput,
    ExtractorOutput,
    Project,
    QADimension,
    QADimensionResult,
    QAOutput,
    QARouting,
    QAStatus,
    QAVerdict,
    ReportDraft,
    ReporterOutput,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PROJECT_FILE = _REPO_ROOT / "fixtures" / "mock_data" / "projects" / "collab_saas_demo.json"


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


def _block_verdict(target: str, vid: str = "v_block") -> QAVerdict:
    """阻塞 verdict，路由到指定上游 agent。"""
    return QAVerdict(
        verdict_id=vid,
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={},
        issues=[],
        routing=[QARouting(target_agent=target, reason="rework", payload={})],  # type: ignore[arg-type]
        blocking=True,
    )


def _block_reporter_verdict(vid: str = "v_block") -> QAVerdict:
    return _block_verdict("reporter", vid)


def _block_reporter_verdict_scored(score: float, vid: str) -> QAVerdict:
    """阻塞回 reporter，且带一个维度分数 —— 用于跨轮「无提升即停」(规则 2.5) 判断。"""
    return QAVerdict(
        verdict_id=vid,
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={
            QADimension.FACT_CONSISTENCY: QADimensionResult(
                dimension=QADimension.FACT_CONSISTENCY, score=score, pass_=False
            )
        },
        issues=[],
        routing=[QARouting(target_agent="reporter", reason="rework", payload={})],  # type: ignore[arg-type]
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
    registry = _FakeRegistry(_StubQA([_block_reporter_verdict(), _pass_verdict()]))
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "cycle")

    reporter_runs = [h for h in final["history"] if h.node == "reporter"]
    assert len(reporter_runs) == 2
    # 第二个 reporter NodeRun round == 2，且 output_ref 版本化为 reporter_v2
    assert reporter_runs[1].round == 2
    assert reporter_runs[0].output_ref == "reporter"
    assert reporter_runs[1].output_ref == "reporter_v2"
    # 版本化修复(P1-a)：v1 与 v2 各占独立 key，互不覆盖，历史可如实回放
    assert final["outputs"]["reporter"].draft.version == 1
    assert final["outputs"]["reporter_v2"].draft.version == 2
    assert final["aborted"] is False


async def test_round_cap_aborts() -> None:
    products = ["Notion"]
    project = _load_demo_project(products=products)
    # 每轮阻塞回 reporter，但分数**持续提升**(0.30→0.50→0.70)→ 不触发「无提升即停」，
    # 一路跑到轮次上限熔断（这才是本测试要覆盖的 max_rounds 路径）。
    registry = _FakeRegistry(
        _StubQA(
            [
                _block_reporter_verdict_scored(0.30, "v1"),
                _block_reporter_verdict_scored(0.50, "v2"),
                _block_reporter_verdict_scored(0.70, "v3"),
            ]
        )
    )
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "cap")

    assert final["aborted"] is True
    assert "max_rounds" in final["abort_reason"]
    # P2-MAXROUNDS：_MAX_QA_ROUNDS=3 → 恰好 3 版草稿(reporter / _v2 / _v3)后熔断，
    # 不再多跑一轮到 reporter_v4(旧 off-by-one)。
    reporter_runs = [h for h in final["history"] if h.node == "reporter"]
    assert len(reporter_runs) == 3
    assert max(h.round for h in reporter_runs) == 3
    assert "reporter_v4" not in final["outputs"]


async def test_no_improvement_aborts_early() -> None:
    """返工一轮后维度均分没提升(同分 0.40) → 规则 2.5 提前熔断，不跑满 max_rounds。

    省掉「原地打转」的昂贵 LLM 轮次；best-round 在发布层兜底，绝不发更差版本。
    """
    products = ["Notion"]
    project = _load_demo_project(products=products)
    registry = _FakeRegistry(
        _StubQA(
            [
                _block_reporter_verdict_scored(0.40, "v1"),
                _block_reporter_verdict_scored(0.40, "v2"),
                _block_reporter_verdict_scored(0.40, "v3"),
            ]
        )
    )
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "noimprove")

    assert final["aborted"] is True
    assert "no meaningful improvement" in final["abort_reason"]
    # round1 reporter + round2 reporter(返工) 后，round2 QA 发现没提升 → 熔断。
    # 即只跑 2 版草稿，而非跑满 3 版(max_rounds)。
    reporter_runs = [h for h in final["history"] if h.node == "reporter"]
    assert len(reporter_runs) == 2


# ---------- A3: 非 reporter 三条返工支线端到端流转 ----------


async def test_qa_cycle_reworks_analyst() -> None:
    """round1 block→analyst,round2 pass：analyst 跑两次、第二轮 round==2、收敛。"""
    products = ["Notion"]
    project = _load_demo_project(products=products)
    registry = _FakeRegistry(_StubQA([_block_verdict("analyst"), _pass_verdict()]))
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "cycle_analyst")

    analyst_runs = [h for h in final["history"] if h.node == "analyst"]
    assert len(analyst_runs) == 2
    assert max(h.round for h in analyst_runs) == 2
    # analyst→reporter→qa：reporter 也应重跑一次
    assert len([h for h in final["history"] if h.node == "reporter"]) == 2
    assert final["aborted"] is False


async def test_qa_cycle_reworks_extractor() -> None:
    """round1 block→extractor,round2 pass：extract 重跑(经 analyst/reporter 回到 qa)。"""
    products = ["Notion"]
    project = _load_demo_project(products=products)
    registry = _FakeRegistry(_StubQA([_block_verdict("extractor"), _pass_verdict()]))
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "cycle_extractor")

    extract_runs = [h for h in final["history"] if h.node == "extract"]
    assert len(extract_runs) == 2  # 单产品 × 2 轮
    assert max(h.round for h in extract_runs) == 2
    # 重抽后必经 analyst→reporter→qa 再评一次
    assert len([h for h in final["history"] if h.node == "analyst"]) == 2
    assert final["aborted"] is False


async def test_qa_cycle_reworks_collector() -> None:
    """round1 block→collector,round2 pass：collect 重采(整条下游重跑)。"""
    products = ["Notion"]
    project = _load_demo_project(products=products)
    registry = _FakeRegistry(_StubQA([_block_verdict("collector"), _pass_verdict()]))
    app = build_native_graph(registry, project=project)

    final = await _run(app, project, products, "cycle_collector")

    collect_runs = [h for h in final["history"] if h.node == "collect"]
    assert len(collect_runs) == 2  # 单产品 × 2 轮
    assert max(h.round for h in collect_runs) == 2
    assert len([h for h in final["history"] if h.node == "extract"]) == 2
    assert final["aborted"] is False


# ---------- P1-c: 上游失败时构造输入不崩图，降级为 failed 节点 ----------


async def test_extract_one_failsoft_when_collector_output_none() -> None:
    """collector 失败(collector_output=None) → extract_one 不抛 BuildInputError，

    而是返回一条 failed 抽取 NodeRun(无 outputs)，让图继续优雅收尾。
    """
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)
    out = await nodes["extract_one"]({"product": "Notion", "collector_output": None, "round": 1})
    assert out["outputs"] == {}
    assert len(out["history"]) == 1
    run = out["history"][0]
    assert run.node == "extract" and run.status == "failed"
    assert run.output_ref is None


def test_collect_dispatch_forwards_prompt_override() -> None:
    """P1-INTERVENE：collect_dispatch 把 prompt_override_by_node 注入 Send payload。"""
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)
    state = RunState(
        project_id=project.project_id,
        run_id="r",
        analysis_mode="competitive_compare",
        products=["Notion"],
        prompt_override_by_node={"collect.Notion": "MY_OVERRIDE"},
    )
    cmd = nodes["collect_dispatch"](state)
    payloads = [s.arg for s in cmd.goto]
    assert payloads and payloads[0]["prompt_override"] == "MY_OVERRIDE"


async def test_collect_one_passes_prompt_override_to_agent(
    monkeypatch: Any,
) -> None:
    """P1-INTERVENE：Send-target collect_one 把 payload 的 prompt_override 传给 run_agent_node。"""
    import backend.orchestrator.nodes as nodes_mod

    project = _load_demo_project(products=["Notion"])
    captured: dict[str, Any] = {}
    real = nodes_mod.run_agent_node

    async def _spy(registry, agent_name, inp, **kw):
        captured["override"] = kw.get("user_prompt_override")
        return await real(registry, agent_name, inp, **kw)

    monkeypatch.setattr(nodes_mod, "run_agent_node", _spy)
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)
    await nodes["collect_one"](
        {"product": "Notion", "round": 1, "qa_feedback": None, "prompt_override": "OV"}
    )
    assert captured["override"] == "OV"


async def test_analyst_failsoft_when_no_profiles() -> None:
    """所有 extractor 失败(无 profiles) → analyst 不抛 BuildInputError，降级 failed。"""
    project = _load_demo_project(products=["Notion"])
    nodes = make_nodes(_FakeRegistry(_StubQA([_pass_verdict()])), project=project)
    state = RunState(
        project_id=project.project_id,
        run_id="run_failsoft",
        analysis_mode="competitive_compare",
        products=["Notion"],
    )  # outputs 为空 → profiles_from_outputs 返回 {} → build_analyst_input 抛
    out = await nodes["analyst"](state)
    assert out["outputs"] == {}
    assert len(out["history"]) == 1
    run = out["history"][0]
    assert run.node == "analyst" and run.status == "failed"
    assert run.output_ref is None
