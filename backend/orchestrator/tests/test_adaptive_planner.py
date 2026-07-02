"""AdaptivePlanner 单测：用 stub LLM 验证 DAG 生成正确，不发真实请求。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from backend.orchestrator.adaptive_planner import (
    AdaptivePlanner,
    _AdaptivePlanOutput,
    _AdaptiveProduct,
)
from backend.orchestrator.planner import Planner
from backend.schemas import (
    AnalysisDimension,
    CollectConstraints,
    Project,
    ProjectStatus,
)


def _make_project(
    target: str = "Notion",
    competitors: tuple[str, ...] = ("Asana", "ClickUp"),
    industry: str = "collaboration_saas",
) -> Project:
    return Project(
        project_id="proj_adaptive_test",
        project_name="adaptive test",
        owner="u",
        created_at=datetime(2026, 6, 2, tzinfo=UTC),
        target_product=target,
        competitors=list(competitors),
        industry=industry,
        industry_schema_version="1.0.0",
        analysis_dimensions=[AnalysisDimension.FEATURE_COMPARISON],
        report_template_id="standard_v1",
        mode="real",
        collect_constraints=CollectConstraints(fallback_to_mock=False),
        status=ProjectStatus.DRAFT,
    )


class _StubLLM:
    """假 LLM：``chat`` 返回预设的 _AdaptivePlanOutput。"""

    def __init__(self, plan_output: _AdaptivePlanOutput | None) -> None:
        self.plan_output = plan_output

    def chat(self, **kwargs: Any) -> Any:
        class _Resp:
            pass

        r = _Resp()
        r.parsed = self.plan_output
        r.content = ""
        r.tokens_input = 100
        r.tokens_output = 50
        r.cost_usd = 0.0
        return r


def _canned_output(products: list[str], dims: list[str] | None = None) -> _AdaptivePlanOutput:
    return _AdaptivePlanOutput(
        rationale="LLM 推断这些 SaaS 都有官网，covering homepage / pricing 已足够",
        products=[
            _AdaptiveProduct(name=p, official_url=f"https://{p.lower()}.com", notes="")
            for p in products
        ],
        collect_dimensions=dims or ["homepage", "pricing"],
        confidence=0.9,
    )


# ---------- 基础形状 ----------


def test_adaptive_plan_returns_standard_shape() -> None:
    project = _make_project()
    out = _canned_output(["Notion", "Asana", "ClickUp"])
    plan = AdaptivePlanner(llm=_StubLLM(out)).plan(project)

    # 3 个产品 × (collect + extract) + start/end/join/analyst/reporter/qa = 12 节点
    assert len(plan.nodes) == 12
    by_id = {n.node_id: n for n in plan.nodes}
    for nid in [
        "start",
        "collect.notion",
        "collect.asana",
        "collect.clickup",
        "extract.notion",
        "extract.asana",
        "extract.clickup",
        "join_extract",
        "analyst",
        "reporter",
        "qa",
        "end",
    ]:
        assert nid in by_id, f"missing {nid}"


def test_adaptive_plan_seeds_official_url() -> None:
    project = _make_project(target="Notion", competitors=("Asana",))
    out = _AdaptivePlanOutput(
        rationale="seed test",
        products=[
            _AdaptiveProduct(name="Notion", official_url="https://notion.so", notes="文档协作"),
            _AdaptiveProduct(name="Asana", official_url="https://asana.com", notes="项目管理"),
        ],
        collect_dimensions=["homepage", "pricing"],
        confidence=0.85,
    )
    plan = AdaptivePlanner(llm=_StubLLM(out)).plan(project)
    by_id = {n.node_id: n for n in plan.nodes}
    assert by_id["collect.notion"].metadata["official_url"] == "https://notion.so"
    assert by_id["collect.asana"].metadata["official_url"] == "https://asana.com"
    assert by_id["collect.notion"].metadata["notes"] == "文档协作"


def test_adaptive_plan_covers_missing_product_with_null_url() -> None:
    """LLM 漏掉某个 competitor 时，Adaptive 应自动补一个 null URL 节点。"""
    project = _make_project(target="Notion", competitors=("UnknownProduct",))
    out = _AdaptivePlanOutput(
        rationale="leak test",
        products=[
            _AdaptiveProduct(name="Notion", official_url="https://notion.so", notes=""),
            # 漏掉 UnknownProduct
        ],
        collect_dimensions=["homepage"],
        confidence=0.5,
    )
    plan = AdaptivePlanner(llm=_StubLLM(out)).plan(project)
    by_id = {n.node_id: n for n in plan.nodes}
    assert "collect.unknownproduct" in by_id
    assert by_id["collect.unknownproduct"].metadata["official_url"] is None


def test_adaptive_plan_dimensions_select_subset() -> None:
    project = _make_project(target="Notion", competitors=("Asana",))
    out = _canned_output(["Notion", "Asana"], dims=["homepage", "pricing"])
    plan = AdaptivePlanner(llm=_StubLLM(out)).plan(project)
    by_id = {n.node_id: n for n in plan.nodes}
    assert by_id["collect.notion"].metadata["collect_dimensions"] == ["homepage", "pricing"]


def test_adaptive_plan_invalid_dimension_dropped() -> None:
    project = _make_project(target="Notion", competitors=("Asana",))
    out = _AdaptivePlanOutput(
        rationale="invalid dim test",
        products=[
            _AdaptiveProduct(name="Notion", official_url=None, notes=""),
            _AdaptiveProduct(name="Asana", official_url=None, notes=""),
        ],
        collect_dimensions=["homepage", "magic_unknown_dim", "pricing"],
        confidence=0.7,
    )
    plan = AdaptivePlanner(llm=_StubLLM(out)).plan(project)
    by_id = {n.node_id: n for n in plan.nodes}
    dims = by_id["collect.notion"].metadata["collect_dimensions"]
    assert "homepage" in dims and "pricing" in dims
    assert "magic_unknown_dim" not in dims


def test_adaptive_plan_falls_back_when_all_dimensions_invalid() -> None:
    project = _make_project(target="Notion", competitors=("Asana",))
    out = _AdaptivePlanOutput(
        rationale="all invalid",
        products=[
            _AdaptiveProduct(name="Notion", official_url=None, notes=""),
            _AdaptiveProduct(name="Asana", official_url=None, notes=""),
        ],
        collect_dimensions=["totally_made_up"],
        confidence=0.3,
    )
    plan = AdaptivePlanner(llm=_StubLLM(out)).plan(project)
    by_id = {n.node_id: n for n in plan.nodes}
    # 全部非法 → 走 _DEFAULT_DIMENSIONS（5 个）
    dims = by_id["collect.notion"].metadata["collect_dimensions"]
    assert len(dims) == 5


def test_adaptive_plan_rationale_marked() -> None:
    project = _make_project()
    plan = AdaptivePlanner(llm=_StubLLM(_canned_output(["Notion", "Asana", "ClickUp"]))).plan(
        project
    )
    assert plan.rationale.startswith("[adaptive]")
    assert plan.template_id is None


def test_adaptive_plan_failed_llm_raises() -> None:
    project = _make_project()
    with pytest.raises(RuntimeError, match="LLM returned no parseable plan"):
        AdaptivePlanner(llm=_StubLLM(None)).plan(project)


# ---------- Planner.plan(mode="adaptive") 转发 ----------


def test_planner_mode_adaptive_uses_llm() -> None:
    project = _make_project()
    out = _canned_output(["Notion", "Asana", "ClickUp"])
    planner = Planner(llm=_StubLLM(out))
    plan = planner.plan(project, mode="adaptive")
    assert plan.template_id is None  # adaptive 出来的
    assert "[adaptive]" in plan.rationale


def test_planner_mode_adaptive_requires_llm() -> None:
    project = _make_project()
    planner = Planner()  # 不传 llm
    with pytest.raises(RuntimeError, match="requires llm"):
        planner.plan(project, mode="adaptive")


def test_planner_mode_auto_falls_back_to_template_on_llm_failure() -> None:
    """adaptive 抛错 → auto 模式自动回退 template，DAG 仍然出来。"""
    project = _make_project()
    planner = Planner(llm=_StubLLM(None))  # LLM 必然失败
    plan = planner.plan(project, mode="auto")
    # 落回 template 路径 → template_id 非 None
    assert plan.template_id is not None
    assert plan.template_id.startswith("collab_saas_standard")
