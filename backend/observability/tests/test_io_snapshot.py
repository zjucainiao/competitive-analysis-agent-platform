"""``summarize_agent_input`` 摘要 + ``BaseAgent.invoke`` 填充 ``input_snapshot`` 的测试。"""

from __future__ import annotations

from types import SimpleNamespace

from backend.agents._base import BaseAgent
from backend.observability.io_snapshot import summarize_agent_input
from backend.schemas import CollectDimension, CollectorInput
from backend.schemas.agent_io import AgentInputBase, AgentOutputBase, AgentStatus


# ----- summarize_agent_input：真实 schema -----


def test_summarize_collector_input_real_schema() -> None:
    inp = CollectorInput(
        task_id="t1",
        project_id="p1",
        trace_id="tr1",
        span_id="sp1",
        product_name="Coda",
        official_url="https://coda.io",
        industry="collaboration_saas",
        dimensions=[CollectDimension.FEATURES, CollectDimension.PRICING],
    )

    snap = summarize_agent_input(inp)

    assert snap["product"] == "Coda"
    assert snap["official_url"] == "https://coda.io"
    assert snap["industry"] == "collaboration_saas"
    assert snap["dimensions"] == "2 维度"
    # 基础字段（task_id/trace_id/span_id）不应进摘要 —— 只留业务关键信息
    assert "task_id" not in snap
    assert "trace_id" not in snap


# ----- summarize_agent_input：鸭子类型覆盖各 input 形态 -----


def test_summarize_reporter_like_input_with_rework() -> None:
    """reporter 返工：analysis + prior_draft + qa_feedback。"""
    inp = SimpleNamespace(
        project_name="Coda 竞品分析",
        template_id="standard_v1",
        output_format="markdown",
        analysis=SimpleNamespace(dimensions={"pricing": 1, "features": 1, "swot": 1}),
        prior_draft=SimpleNamespace(version=1, sections=[1, 2, 3]),
        qa_feedback={"must_address": ["iss_a", "iss_b"], "revision": 2},
    )

    snap = summarize_agent_input(inp)

    assert snap["product"] == "Coda 竞品分析"
    assert snap["template_id"] == "standard_v1"
    assert snap["analysis"] == "AnalysisResult · 3 维度"
    assert snap["prior_draft"].startswith("v1")
    assert snap["qa_feedback"] == "QA 返工反馈 · 2 项必改 · 第 2 轮"


def test_summarize_qa_like_input() -> None:
    """qa：draft + profiles + prior_verdicts + upstream_statuses。"""
    inp = SimpleNamespace(
        target_product="Coda",
        draft=SimpleNamespace(version=2, sections=[1, 2, 3, 4, 5, 6]),
        profiles={"Coda": object(), "Notion": object()},
        prior_verdicts=[object(), object()],
        upstream_statuses={"collector": "needs_rework"},
    )

    snap = summarize_agent_input(inp)

    assert snap["product"] == "Coda"
    assert snap["draft"] == "ReportDraft v2 · 6 章"
    assert "2 画像" in snap["profiles"]
    assert "Coda" in snap["profiles"] and "Notion" in snap["profiles"]
    assert snap["prior_verdicts"] == "2 轮历史质检"
    assert snap["upstream"] == "collector=needs_rework"


def test_summarize_never_raises_on_garbage() -> None:
    """观测层永不抛异常：奇形怪状的对象也只返回能抽到的部分。"""
    assert summarize_agent_input(object()) == {}
    assert summarize_agent_input(SimpleNamespace(product_name="X")) == {"product": "X"}


# ----- 集成：invoke 出口统一填充 input_snapshot -----


class _DummyInput(AgentInputBase):
    product_name: str
    dimensions: list[str] = []


class _DummyOutput(AgentOutputBase):
    pass


class _DummyAgent(BaseAgent):
    name = "dummy"
    version = "1.0.0"
    input_model = _DummyInput
    output_model = _DummyOutput

    def _run(self, inp: _DummyInput) -> _DummyOutput:  # pragma: no cover - 非 mock 不走
        return self._run_mock(inp)

    def _run_mock(self, inp: _DummyInput) -> _DummyOutput:
        return _DummyOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=AgentStatus.SUCCESS,
            confidence=0.9,
            self_critique="",
        )


def test_invoke_populates_input_snapshot() -> None:
    agent = _DummyAgent(mock=True)
    inp = _DummyInput(
        task_id="t1",
        project_id="p1",
        trace_id="tr1",
        span_id="sp1",
        product_name="Coda",
        dimensions=["a", "b", "c"],
    )

    out = agent.invoke(inp, trace_id="tr1", span_id="sp1")

    assert out.input_snapshot["product"] == "Coda"
    assert out.input_snapshot["dimensions"] == "3 维度"
