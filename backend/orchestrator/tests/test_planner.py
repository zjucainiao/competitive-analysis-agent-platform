"""Planner 单测：YAML 模板 → DAGPlan 展开正确性。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.orchestrator.planner import (
    Planner,
    TemplateNotFoundError,
)
from backend.schemas import (
    NodeStatus,
    NodeType,
    Project,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PROJECT_FILE = _REPO_ROOT / "fixtures" / "mock_data" / "projects" / "collab_saas_demo.json"


def _load_demo_project() -> Project:
    data = json.loads(_DEMO_PROJECT_FILE.read_text(encoding="utf-8"))
    return Project.model_validate(data)


# ---------- happy path ----------


def test_plan_demo_project_has_expected_node_count() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)

    # 1 start + 3 collect + 3 extract + 1 join + 1 analyst + 1 reporter + 1 qa + 1 end = 12
    assert len(plan.nodes) == 12


def test_plan_demo_project_has_expected_node_ids() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)

    expected_ids = {
        "start",
        "collect.notion",
        "collect.clickup",
        "collect.asana",
        "extract.notion",
        "extract.clickup",
        "extract.asana",
        "join_extract",
        "analyst",
        "reporter",
        "qa",
        "end",
    }
    actual_ids = {n.node_id for n in plan.nodes}
    assert actual_ids == expected_ids


def test_plan_node_types() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)
    by_id = {n.node_id: n for n in plan.nodes}

    assert by_id["start"].node_type == NodeType.START
    assert by_id["end"].node_type == NodeType.END
    assert by_id["join_extract"].node_type == NodeType.PARALLEL_JOIN
    for nid in ["collect.notion", "extract.clickup", "analyst", "reporter", "qa"]:
        assert by_id[nid].node_type == NodeType.AGENT_CALL


def test_plan_agent_names() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)
    by_id = {n.node_id: n for n in plan.nodes}

    assert by_id["collect.notion"].agent_name == "collector"
    assert by_id["extract.asana"].agent_name == "extractor"
    assert by_id["analyst"].agent_name == "analyst"
    assert by_id["reporter"].agent_name == "reporter"
    assert by_id["qa"].agent_name == "qa"
    # 控制节点 agent_name 必须为 None
    assert by_id["start"].agent_name is None
    assert by_id["end"].agent_name is None
    assert by_id["join_extract"].agent_name is None


def test_plan_input_refs_simple_chain() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)
    by_id = {n.node_id: n for n in plan.nodes}

    assert by_id["start"].input_refs == []
    assert by_id["collect.notion"].input_refs == ["start"]
    assert by_id["extract.notion"].input_refs == ["collect.notion"]
    assert by_id["analyst"].input_refs == ["join_extract"]
    assert by_id["reporter"].input_refs == ["analyst"]
    assert by_id["qa"].input_refs == ["reporter"]
    assert by_id["end"].input_refs == ["qa"]


def test_plan_wildcard_join() -> None:
    """``depends_on: [extract.*]`` 应展开到所有 extract.* 节点，按字典序排序。"""
    project = _load_demo_project()
    plan = Planner().plan(project)
    by_id = {n.node_id: n for n in plan.nodes}

    deps = by_id["join_extract"].input_refs
    assert sorted(deps) == ["extract.asana", "extract.clickup", "extract.notion"]
    # 也确认顺序稳定（字典序）
    assert deps == sorted(deps)


def test_plan_edges() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)

    # 3 (start→collect) + 3 (collect→extract) + 3 (extract→join) + 1 + 1 + 1 + 1 = 13
    assert len(plan.edges) == 13
    pairs = {(e.from_node, e.to_node) for e in plan.edges}
    assert ("start", "collect.notion") in pairs
    assert ("collect.notion", "extract.notion") in pairs
    assert ("extract.notion", "join_extract") in pairs
    assert ("join_extract", "analyst") in pairs
    assert ("analyst", "reporter") in pairs
    assert ("reporter", "qa") in pairs
    assert ("qa", "end") in pairs


def test_plan_initial_node_state() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)
    for node in plan.nodes:
        assert node.status == NodeStatus.PENDING
        assert node.retry_count == 0
        assert node.revision == 1
        assert node.parent_node_id is None
        assert node.started_at is None
        assert node.ended_at is None


def test_plan_product_metadata_on_agent_nodes() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)
    by_id = {n.node_id: n for n in plan.nodes}

    # Collector 节点带 product + 模板里声明的 collect_dimensions
    notion = by_id["collect.notion"].metadata
    assert notion["product"] == "Notion"
    assert notion["collect_dimensions"] == [
        "homepage",
        "features",
        "pricing",
        "help_docs",
        "user_reviews",
    ]

    # Extractor 节点仅带 product
    assert by_id["extract.clickup"].metadata == {"product": "ClickUp"}

    # 非 for_each 展开的节点没有 product
    assert "product" not in by_id["analyst"].metadata
    assert "product" not in by_id["join_extract"].metadata


def test_plan_basic_fields() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)

    assert plan.project_id == project.project_id
    assert plan.template_id == "collab_saas_standard_v1"
    assert plan.plan_id.startswith("plan_")
    assert "Notion" in plan.rationale
    assert plan.confidence == 1.0
    assert 0 < plan.complexity_score <= 1


def test_plan_node_timeout_and_retries() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project)
    by_id = {n.node_id: n for n in plan.nodes}

    # 真实 LLM 跑很慢，节点超时根据实测调高，且模板值不得低于事故后精调的下限表
    # （plan_directives.NODE_TIMEOUT_FLOOR_MS）——native 引擎消费 plan 超时后，
    # 过小的模板值会直接造成回归：
    # collector 300s（含豆包联网搜索 5 维度 + 身份校验），extractor 600s（consolidation pass）
    assert by_id["collect.notion"].timeout_ms == 300000
    assert by_id["collect.notion"].max_retries == 2
    assert by_id["extract.notion"].timeout_ms == 600000
    # analyst 是 240s，max_retries=2
    assert by_id["analyst"].timeout_ms == 240000
    assert by_id["analyst"].max_retries == 2


def test_list_templates() -> None:
    templates = Planner().list_templates()
    assert "collab_saas_standard" in templates


# ---------- error paths ----------


def test_unknown_template_raises() -> None:
    project = _load_demo_project()
    with pytest.raises(TemplateNotFoundError):
        Planner().plan(project, template_id="nonexistent_template")


def test_empty_competitors_still_works_with_only_target() -> None:
    """target_plus_competitors 至少含 target，单产品也应正常 plan。

    Planner 会把入边 ≤ 1 的 parallel_join 节点剪枝（``join_extract`` 在单产品下
    没意义），所以 single-product 比 multi-product 少 1 个 join 节点。
    剪枝后剩 7 个：start + collect + extract + analyst + reporter + qa + end。
    """
    project = _load_demo_project()
    project = project.model_copy(update={"competitors": []})
    plan = Planner().plan(project)

    ids = {n.node_id for n in plan.nodes}
    assert len(plan.nodes) == 7
    assert "collect.notion" in ids
    assert "extract.notion" in ids
    # 其他竞品节点不应存在
    assert not any(nid.startswith("collect.clickup") for nid in ids)
    # join_extract 在单产品下被剪掉
    assert "join_extract" not in ids
    # analyst 的 input_refs 应该直接指向 extract.notion，而不是 join_extract
    analyst = next(n for n in plan.nodes if n.node_id == "analyst")
    assert "extract.notion" in analyst.input_refs
    assert "join_extract" not in analyst.input_refs


def test_explicit_template_id_override() -> None:
    project = _load_demo_project()
    plan = Planner().plan(project, template_id="collab_saas_standard")
    assert plan.template_id == "collab_saas_standard_v1"
