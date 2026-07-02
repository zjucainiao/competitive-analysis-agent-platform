"""run_state_to_view 装配器的纯函数单元测试（Phase 2 Stage B）。

contract: run_state_to_view 接受 RunState.model_dump() 产出的 dict
（history / verdicts 里的元素是 dict）。覆盖：
- 5 个静态阶段骨架始终存在；
- collect/extract 按产品出 instances（多产品 + 取最新轮次）；
- analyst/reporter/qa 按轮次出 revisions（QA 返工 → reporter 有 2 revisions）；
- token/cost/confidence 从 outputs 派生；
- 整体 status 推导（done / failed / aborted / running）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.orchestrator.run_state import NodeRun, RunState
from backend.orchestrator.run_view import run_state_to_view
from backend.schemas import Project, ProjectMetrics

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEMO_PROJECT_FILE = _REPO_ROOT / "fixtures" / "mock_data" / "projects" / "collab_saas_demo.json"


@pytest.fixture()
def two_product_project() -> Project:
    data = json.loads(_DEMO_PROJECT_FILE.read_text(encoding="utf-8"))
    proj = Project.model_validate(data)
    return proj.model_copy(update={"target_product": "Notion", "competitors": ["Asana"]})


def _nr(
    node: str,
    agent: str,
    *,
    product: str | None = None,
    round_: int = 1,
    status: str = "success",
    output_ref: str | None = None,
) -> NodeRun:
    return NodeRun(
        node=node,
        agent=agent,
        product=product,
        round=round_,
        status=status,
        span_id=f"span_{node}_{product or 'g'}_{round_}",
        started_at="2026-06-06T00:00:00+00:00",
        ended_at="2026-06-06T00:00:05+00:00",
        output_ref=output_ref,
    )


def _output(*, tokens_in: int, tokens_out: int, cost: float, conf: float) -> dict:
    """模拟一个 AgentOutput 的 metric 字段（dump 后的 dict 形态）。"""
    return {
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        "cost_usd": cost,
        "confidence": conf,
        "duration_ms": 4200,
    }


@pytest.fixture()
def rework_state(two_product_project: Project) -> dict:
    """多产品 + 一次 QA 返工：reporter 有 2 轮，collect 有 2 产品实例。"""
    history = [
        _nr("collect", "collector", product="Notion", output_ref="collect.Notion"),
        _nr("collect", "collector", product="Asana", output_ref="collect.Asana"),
        _nr("extract", "extractor", product="Notion", output_ref="extract.Notion"),
        _nr("extract", "extractor", product="Asana", output_ref="extract.Asana"),
        _nr("analyst", "analyst", output_ref="analyst"),
        _nr("reporter", "reporter", round_=1, output_ref="reporter"),
        _nr("qa", "qa", round_=1, status="needs_rework", output_ref="qa"),
        # 版本化(P1-a)：返工轮落独立 key reporter_v2 / qa_v2，不覆盖 round1
        _nr("reporter", "reporter", round_=2, output_ref="reporter_v2"),
        _nr("qa", "qa", round_=2, status="success", output_ref="qa_v2"),
    ]
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_rework",
        analysis_mode="competitive_compare",
        products=["Notion", "Asana"],
    )
    state.history = history
    state.qa_round = 1
    state.outputs = {
        "collect.Notion": _output(tokens_in=100, tokens_out=50, cost=0.01, conf=0.9),
        "collect.Asana": _output(tokens_in=120, tokens_out=60, cost=0.012, conf=0.8),
        "extract.Notion": _output(tokens_in=200, tokens_out=80, cost=0.02, conf=0.88),
        "extract.Asana": _output(tokens_in=210, tokens_out=85, cost=0.021, conf=0.7),
        "analyst": _output(tokens_in=300, tokens_out=150, cost=0.05, conf=0.85),
        # round1 reporter(v1) 与 round2 reporter_v2 各占独立 key、内容不同
        "reporter": _output(tokens_in=500, tokens_out=400, cost=0.1, conf=0.82),
        "reporter_v2": _output(tokens_in=520, tokens_out=420, cost=0.11, conf=0.91),
        "qa": _output(tokens_in=150, tokens_out=40, cost=0.03, conf=0.92),
        "qa_v2": _output(tokens_in=160, tokens_out=45, cost=0.032, conf=0.95),
    }
    state.verdicts = [
        {"verdict_id": "v1", "overall_status": "needs_revision"},
        {"verdict_id": "v2", "overall_status": "pass"},
    ]
    return state.model_dump()


def _stage(view, name):
    return next(s for s in view.stages if s.stage == name)


def test_five_static_stages_always_present(rework_state, two_product_project):
    view = run_state_to_view(rework_state, project=two_product_project)
    assert [s.stage for s in view.stages] == [
        "collect",
        "extract",
        "analyst",
        "reporter",
        "qa",
    ]


def test_empty_state_still_has_skeleton(two_product_project):
    """从未跑过：history 空也应返回 5 个空阶段，status=running。"""
    empty = RunState(
        project_id=two_product_project.project_id,
        run_id="run_empty",
        analysis_mode="competitive_compare",
        products=["Notion", "Asana"],
    ).model_dump()
    view = run_state_to_view(empty, project=two_product_project)
    assert len(view.stages) == 5
    assert all(not s.instances and not s.revisions for s in view.stages)
    assert view.status == "running"
    assert view.products == ["Notion", "Asana"]


def test_collect_extract_instances_per_product(rework_state, two_product_project):
    view = run_state_to_view(rework_state, project=two_product_project)
    collect = _stage(view, "collect")
    extract = _stage(view, "extract")
    assert {i.product for i in collect.instances} == {"Notion", "Asana"}
    assert {i.product for i in extract.instances} == {"Notion", "Asana"}
    # 非产品阶段 instances 必须为空
    assert _stage(view, "analyst").instances == []
    assert _stage(view, "reporter").instances == []


def test_instance_metrics_pulled_from_outputs(rework_state, two_product_project):
    view = run_state_to_view(rework_state, project=two_product_project)
    notion = next(i for i in _stage(view, "collect").instances if i.product == "Notion")
    assert notion.run_ref == "collect.Notion"
    assert notion.tokens_input == 100
    assert notion.tokens_output == 50
    assert notion.cost_usd == 0.01
    assert notion.confidence == 0.9
    assert notion.duration_ms == 4200


def test_reporter_has_two_revisions_after_rework(rework_state, two_product_project):
    view = run_state_to_view(rework_state, project=two_product_project)
    reporter = _stage(view, "reporter")
    rounds = [r.round for r in reporter.revisions]
    assert rounds == [1, 2]
    # round>1 的 run_ref 带 _v2 后缀（与 projection 命名一致）
    refs = {r.round: r.run_ref for r in reporter.revisions}
    assert refs[1] == "reporter"
    assert refs[2] == "reporter_v2"
    # 产品阶段 revisions 为空
    assert _stage(view, "collect").revisions == []


def test_qa_revisions_and_status_done(rework_state, two_product_project):
    view = run_state_to_view(rework_state, project=two_product_project)
    qa = _stage(view, "qa")
    assert [r.round for r in qa.revisions] == [1, 2]
    assert qa.revisions[0].status == "needs_rework"
    assert qa.revisions[1].status == "success"
    # 跑到 qa 且未 abort → done
    assert view.status == "done"
    assert view.qa_round == 1


def test_verdicts_and_metrics_attached(rework_state, two_product_project):
    metrics = ProjectMetrics(total_tokens=999, total_cost_usd=1.23)
    view = run_state_to_view(rework_state, project=two_product_project, metrics=metrics)
    assert len(view.verdicts) == 2
    assert view.verdicts[0]["verdict_id"] == "v1"
    assert view.metrics is not None
    assert view.metrics.total_tokens == 999


def test_rework_in_progress_reports_running_not_done(two_product_project):
    """返工进行中：round-1 qa 判 blocking(节点状态=partial)→已 dispatch round-2，
    history 里 qa 之后又冒出 collect_v2 → 必须 running，不能过早 done。

    回归 bug：QA 对 blocking reject 也返回 PARTIAL，旧逻辑「最后 qa=partial→done」
    会在返工刚触发时显示 done，再蹦出 _v2 节点 → 返工实时观感错乱。
    """
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_rip",
        analysis_mode="competitive_compare",
        products=["Notion", "Asana"],
    )
    state.history = [
        _nr("collect", "collector", product="Notion", output_ref="collect.Notion"),
        _nr("collect", "collector", product="Asana", output_ref="collect.Asana"),
        _nr("extract", "extractor", product="Notion", output_ref="extract.Notion"),
        _nr("extract", "extractor", product="Asana", output_ref="extract.Asana"),
        _nr("analyst", "analyst", output_ref="analyst"),
        _nr("reporter", "reporter", round_=1, output_ref="reporter"),
        # QA blocking reject → 节点状态是 partial（不是 needs_rework）
        _nr("qa", "qa", round_=1, status="partial", output_ref="qa"),
        # 返工轮上游已启动
        _nr(
            "collect",
            "collector",
            product="Asana",
            round_=2,
            status="partial",
            output_ref="collect.Asana_v2",
        ),
    ]
    state.qa_round = 1
    state.verdicts = [
        {"verdict_id": "v1", "blocking": True, "routing": [{"target_agent": "collector"}]}
    ]
    view = run_state_to_view(state.model_dump(), project=two_product_project)
    assert view.status == "running"


def test_blocking_qa_last_entry_still_running(two_product_project):
    """瞬态窗口：qa 刚判完 blocking、返工节点还没 append 进 history（qa 是最后一条）。
    靠 verdict.blocking+routing 也要判 running，不能因 qa=partial 就过早 done。"""
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_window",
        analysis_mode="competitive_compare",
        products=["Notion"],
    )
    state.history = [
        _nr("collect", "collector", product="Notion", output_ref="collect.Notion"),
        _nr("extract", "extractor", product="Notion", output_ref="extract.Notion"),
        _nr("analyst", "analyst", output_ref="analyst"),
        _nr("reporter", "reporter", round_=1, output_ref="reporter"),
        _nr("qa", "qa", round_=1, status="partial", output_ref="qa"),
    ]
    state.qa_round = 1
    state.verdicts = [
        {"verdict_id": "v1", "blocking": True, "routing": [{"target_agent": "collector"}]}
    ]
    view = run_state_to_view(state.model_dump(), project=two_product_project)
    assert view.status == "running"


def test_nonblocking_qa_last_entry_is_done(two_product_project):
    """终判非 blocking（END）：qa 是最后一条且 partial、但 verdict 不 blocking → done。"""
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_done",
        analysis_mode="competitive_compare",
        products=["Notion"],
    )
    state.history = [
        _nr("collect", "collector", product="Notion", output_ref="collect.Notion"),
        _nr("extract", "extractor", product="Notion", output_ref="extract.Notion"),
        _nr("analyst", "analyst", output_ref="analyst"),
        _nr("reporter", "reporter", round_=1, output_ref="reporter"),
        _nr("qa", "qa", round_=1, status="partial", output_ref="qa"),
    ]
    state.verdicts = [{"verdict_id": "v1", "blocking": False, "routing": []}]
    view = run_state_to_view(state.model_dump(), project=two_product_project)
    assert view.status == "done"


def test_aborted_status(two_product_project):
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_abort",
        analysis_mode="competitive_compare",
        products=["Notion"],
    )
    state.history = [
        _nr("collect", "collector", product="Notion", output_ref="collect.Notion"),
        _nr("reporter", "reporter", round_=1, output_ref="reporter"),
        _nr("qa", "qa", round_=1, status="needs_rework", output_ref="qa"),
    ]
    state.aborted = True
    state.abort_reason = "max_rounds reached"
    view = run_state_to_view(state.model_dump(), project=two_product_project)
    assert view.status == "aborted"
    assert view.aborted is True
    assert view.abort_reason == "max_rounds reached"


def test_outputs_keyed_by_run_ref(rework_state, two_product_project):
    """outputs 字段按 run_ref（投影节点 ID）键，详情面板靠它取深内容。

    - 每产品 / 每轮次都应有 run_ref 入口；
    - 版本化(P1-a)后 reporter 与 reporter_v2 指向**各自轮次**的独立产物（不再都指向
      最新一份）；前端 v1↔v2 diff 因此可信；
    - 键与 instances/revisions.run_ref 对得上，前端 outputs[run_ref] 可命中。
    """
    view = run_state_to_view(rework_state, project=two_product_project)
    keys = set(view.outputs)
    assert {
        "collect.Notion",
        "collect.Asana",
        "extract.Notion",
        "extract.Asana",
        "analyst",
        "reporter",
        "reporter_v2",
        "qa",
    } <= keys
    # reporter(v1) 与 reporter_v2 各自映射到 round1 / round2 的独立产物
    assert view.outputs["reporter"] == rework_state["outputs"]["reporter"]
    assert view.outputs["reporter_v2"] == rework_state["outputs"]["reporter_v2"]
    assert view.outputs["reporter"] != view.outputs["reporter_v2"]
    # instances/revisions 的 run_ref 都能在 outputs 里命中
    reporter = _stage(view, "reporter")
    for rev in reporter.revisions:
        assert rev.run_ref in view.outputs
    collect = _stage(view, "collect")
    for inst in collect.instances:
        assert inst.run_ref in view.outputs


def test_failed_status_when_terminal_node_failed(two_product_project):
    state = RunState(
        project_id=two_product_project.project_id,
        run_id="run_fail",
        analysis_mode="competitive_compare",
        products=["Notion"],
    )
    state.history = [
        _nr("collect", "collector", product="Notion", status="failed", output_ref="collect.Notion"),
    ]
    view = run_state_to_view(state.model_dump(), project=two_product_project)
    assert view.status == "failed"
    # 失败实例 status 透传
    collect = _stage(view, "collect")
    assert collect.instances[0].status == "failed"
