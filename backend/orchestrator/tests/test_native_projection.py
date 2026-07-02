"""投影函数 run_state_to_dagplan 的 TDD 测试。

contract: run_state_to_dagplan 接受 RunState.model_dump() 产出的 dict
（history 里的元素是 dict，不是 NodeRun 对象）。
Stage D 后 projection 不再对前端暴露，但仍是 orchestrator metrics 的内部依赖，
故本测试保留。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.orchestrator.run_state import NodeRun, RunState
from backend.schemas import Project

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PROJECT_FILE = (
    _REPO_ROOT / "fixtures" / "mock_data" / "projects" / "collab_saas_demo.json"
)


# ---------- fixtures ----------


@pytest.fixture()
def two_product_project() -> Project:
    """加载 demo 项目,设置两产品 Notion + Asana。"""
    data = json.loads(_DEMO_PROJECT_FILE.read_text(encoding="utf-8"))
    proj = Project.model_validate(data)
    return proj.model_copy(
        update={"target_product": "Notion", "competitors": ["Asana"]}
    )


def _make_node_run(
    node: str,
    agent: str,
    *,
    product: str | None = None,
    round_: int = 1,
    status: str = "success",
    output_ref: str | None = None,
) -> NodeRun:
    """辅助构造 NodeRun,span_id 自动生成。"""
    return NodeRun(
        node=node,
        agent=agent,
        product=product,
        round=round_,
        status=status,
        span_id=f"span_{node}_{product or 'none'}_{round_}",
        output_ref=output_ref,
    )


@pytest.fixture()
def sample_final_state(two_product_project: Project) -> dict:
    """正常完成 (无返工) 的 RunState.model_dump()。

    包含 collect.Notion、extract.Notion、analyst、reporter、qa 五类节点。
    """
    history = [
        _make_node_run("collect", "collector", product="Notion",
                       output_ref="collect.Notion"),
        _make_node_run("extract", "extractor", product="Notion",
                       output_ref="extract.Notion"),
        _make_node_run("analyst", "analyst", output_ref="analyst"),
        _make_node_run("reporter", "reporter", output_ref="reporter"),
        _make_node_run("qa", "qa", output_ref="qa"),
    ]
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_sample",
        analysis_mode="competitive_compare",
        products=["Notion"],
    )
    state.history = history
    state.outputs = {
        "collect.Notion": {"raw_sources": []},
        "extract.Notion": {"profile": {}},
        "analyst": {"result": {}},
        "reporter": {"draft": {}},
        "qa": {"verdict": {}},
    }
    return state.model_dump()


@pytest.fixture()
def rework_final_state(two_product_project: Project) -> dict:
    """经过 QA 返工后的 RunState.model_dump()。

    history 里有两条 reporter NodeRun：round=1 和 round=2。
    """
    history = [
        _make_node_run("collect", "collector", product="Notion",
                       output_ref="collect.Notion"),
        _make_node_run("extract", "extractor", product="Notion",
                       output_ref="extract.Notion"),
        _make_node_run("analyst", "analyst", output_ref="analyst"),
        # reporter 首跑
        _make_node_run("reporter", "reporter", round_=1, output_ref="reporter"),
        # qa 首轮 → needs_rework
        _make_node_run("qa", "qa", round_=1, status="needs_rework", output_ref="qa"),
        # reporter 返工
        _make_node_run("reporter", "reporter", round_=2, output_ref="reporter"),
        # qa 二轮 → pass
        _make_node_run("qa", "qa", round_=2, status="success", output_ref="qa"),
    ]
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_rework",
        analysis_mode="competitive_compare",
        products=["Notion"],
    )
    state.history = history
    state.outputs = {
        "collect.Notion": {"raw_sources": []},
        "extract.Notion": {"profile": {}},
        "analyst": {"result": {}},
        "reporter": {"draft": {"version": 2}},
        "qa": {"verdict": {}},
    }
    return state.model_dump()


# ---------- tests ----------


def test_projection_has_expected_nodes(
    sample_final_state: dict, two_product_project: Project
) -> None:
    """正常终态应包含所有预期节点，reporter 输出映射正确。"""
    from backend.orchestrator.projection import run_state_to_dagplan

    plan, outputs = run_state_to_dagplan(sample_final_state, project=two_product_project)
    ids = {n.node_id for n in plan.nodes}
    assert {"collect.Notion", "extract.Notion", "analyst", "reporter", "qa"} <= ids
    # 所有节点均应已填充
    assert plan.nodes
    # reporter 输出应映射到 out_map
    assert "reporter" in outputs


def test_projection_reporter_revisions_map_to_versioned_nodes(
    rework_final_state: dict, two_product_project: Project
) -> None:
    """返工终态：两轮 reporter → reporter + reporter_v2（前端 v1↔v2 回放保留）。"""
    from backend.orchestrator.projection import run_state_to_dagplan

    plan, _outputs = run_state_to_dagplan(rework_final_state, project=two_product_project)
    ids = {n.node_id for n in plan.nodes}
    assert "reporter" in ids, f"reporter missing; got {ids}"
    assert "reporter_v2" in ids, f"reporter_v2 missing; got {ids}"
    # reporter_v2 经 parent_node_id 指回 reporter（前端 feedback 子节点布局）
    rep_v2 = next(n for n in plan.nodes if n.node_id == "reporter_v2")
    assert rep_v2.parent_node_id == "reporter"


def test_projection_builds_pipeline_edges(
    sample_final_state: dict, two_product_project: Project
) -> None:
    """投影应重建流水线主链边,前端 DAG 视图据此分层布局(否则节点重叠)。"""
    from backend.orchestrator.projection import run_state_to_dagplan

    plan, _ = run_state_to_dagplan(sample_final_state, project=two_product_project)
    pairs = {(e.from_node, e.to_node) for e in plan.edges}
    assert ("collect.Notion", "extract.Notion") in pairs
    assert ("extract.Notion", "analyst") in pairs
    assert ("analyst", "reporter") in pairs
    assert ("reporter", "qa") in pairs


def test_projection_timestamps_propagated_to_dag_nodes(
    two_product_project: Project,
) -> None:
    """Fix 2：history NodeRun 中的 started_at/ended_at ISO 字符串应投影到 DAGNode。

    验证：
    - 带时间戳的 NodeRun → 对应 DAGNode.started_at/ended_at 非 None。
    - 缺失时间戳的 NodeRun → DAGNode.started_at/ended_at 为 None（无 crash）。
    - _compute_duration 对带时间戳的投影 plan 返回非零值。
    """
    from backend.orchestrator.metrics import _compute_duration
    from backend.orchestrator.projection import run_state_to_dagplan

    t_start = "2026-06-07T10:00:00+00:00"
    t_end = "2026-06-07T10:01:30+00:00"  # 90 秒后

    history = [
        {
            "node": "collect",
            "agent": "collector",
            "product": "Notion",
            "round": 1,
            "status": "success",
            "span_id": "s1",
            "started_at": t_start,
            "ended_at": t_end,
            "output_ref": "collect.Notion",
        },
        {
            "node": "analyst",
            "agent": "analyst",
            "product": None,
            "round": 1,
            "status": "success",
            "span_id": "s2",
            # started_at/ended_at 缺失 → 应优雅处理，不崩
            "output_ref": "analyst",
        },
    ]
    state = {
        "project_id": two_product_project.project_id,
        "run_id": "run_ts_test",
        "analysis_mode": "competitive_compare",
        "products": ["Notion"],
        "history": history,
        "outputs": {
            "collect.Notion": {"raw_sources": []},
            "analyst": {"result": {}},
        },
        "verdicts": [],
        "qa_round": 0,
        "rework_products": [],
        "rework_target": None,
        "qa_feedback_by_node": {},
        "aborted": False,
        "abort_reason": "",
    }

    plan, _ = run_state_to_dagplan(state, project=two_product_project)

    by_id = {n.node_id: n for n in plan.nodes}

    # collect.Notion：时间戳应被正确解析
    collect_node = by_id["collect.Notion"]
    assert collect_node.started_at is not None, "collect.Notion.started_at 应为 datetime"
    assert collect_node.ended_at is not None, "collect.Notion.ended_at 应为 datetime"

    # analyst：时间戳缺失，应为 None（不崩）
    analyst_node = by_id["analyst"]
    assert analyst_node.started_at is None
    assert analyst_node.ended_at is None

    # _compute_duration 应返回非零值（collect 节点提供了有效时间窗口）
    duration = _compute_duration(plan)
    assert duration > 0, f"duration_seconds 应 > 0，实际={duration}"
    assert duration == 90, f"预期 90 秒，实际={duration}"
