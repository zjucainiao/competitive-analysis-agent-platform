"""decide_qa_route() 单测 — 原生图 QA 路由决策。"""

from __future__ import annotations

from langgraph.graph import END

from backend.orchestrator.routing import decide_qa_route
from backend.schemas import QADimension, QAIssue, QARouting, QAStatus, QAVerdict

# ---------- helpers ----------


def _verdict(
    *,
    routing: list[QARouting],
    blocking: bool,
    issues: list[QAIssue] = (),
) -> QAVerdict:
    """构造最小可用的 QAVerdict。"""
    return QAVerdict(
        verdict_id="v1",
        overall_status=QAStatus.NEEDS_REVISION,
        routing=list(routing),
        blocking=blocking,
        issues=list(issues),
    )


def _issue(
    *,
    target: str,
    required_inputs: dict | None = None,
    issue_id: str = "iss_1",
) -> QAIssue:
    """构造最小可用的 QAIssue。"""
    return QAIssue(
        issue_id=issue_id,
        dimension=QADimension.FACT_CONSISTENCY,
        severity="major",
        location="report.sections[0].paragraphs[0]",
        problem="placeholder",
        suggested_fix="placeholder",
        target_agent=target,  # type: ignore[arg-type]
        required_inputs=required_inputs or {},
    )


# ---------- tests ----------


def test_cap_aborts() -> None:
    """qa_round >= max_rounds 时强制发布，返回 END + aborted=True。"""
    goto, upd = decide_qa_route(
        _verdict(
            routing=[QARouting(target_agent="reporter", reason="x", payload={})],
            blocking=True,
        ),
        qa_round=3,
        max_rounds=3,
        products=["Notion"],
    )
    assert goto == END
    assert upd["aborted"] is True


def test_no_routing_ends() -> None:
    """routing 列表为空时直接结束，无需重做。"""
    goto, _upd = decide_qa_route(
        _verdict(routing=[], blocking=False),
        qa_round=0,
        max_rounds=3,
        products=["Notion"],
    )
    assert goto == END


def test_routes_to_reporter() -> None:
    """单条 reporter routing 时 goto='reporter'，qa_round 递增，rework_products 为空。"""
    goto, upd = decide_qa_route(
        _verdict(
            routing=[QARouting(target_agent="reporter", reason="rewrite", payload={})],
            blocking=True,
        ),
        qa_round=0,
        max_rounds=3,
        products=["Notion", "Asana"],
    )
    assert goto == "reporter"
    assert upd["qa_round"] == 1
    assert upd["rework_products"] == []


def test_picks_most_upstream_target() -> None:
    """多 routing 时选最上游（extractor < reporter），映射到 'extract_dispatch'。"""
    rt = [
        QARouting(target_agent="reporter", reason="r", payload={}),
        QARouting(target_agent="extractor", reason="e", payload={}),
    ]
    goto, upd = decide_qa_route(
        _verdict(routing=rt, blocking=True),
        qa_round=0,
        max_rounds=3,
        products=["Notion", "Asana"],
    )
    assert goto == "extract_dispatch"
    assert upd["rework_target"] == "extractor"


def test_product_narrowing() -> None:
    """extractor routing + issue 指名 Asana → rework_products=['Asana']（收窄）。"""
    issue = _issue(
        target="extractor",
        required_inputs={"product": "Asana"},
        issue_id="iss_asana",
    )
    goto, upd = decide_qa_route(
        _verdict(
            routing=[QARouting(target_agent="extractor", reason="missing data", payload={})],
            blocking=True,
            issues=[issue],
        ),
        qa_round=0,
        max_rounds=3,
        products=["Notion", "Asana"],
    )
    assert goto == "extract_dispatch"
    assert upd["rework_products"] == ["Asana"]


def test_mixed_agent_issues_only_uses_chosen_agent_products() -> None:
    """回归测试：两条 issue 分属不同 Agent 时，rework_products 只含 chosen agent 的产品。

    verdict 有两条 issue：
      - extractor 指名 "Asana"
      - collector  指名 "Notion"
    routing 也列出两者。最上游是 collector，故 chosen=collector，
    rework_products 应仅为 ["Notion"]，不含 "Asana"。
    """
    issue_extractor = _issue(
        target="extractor",
        required_inputs={"product": "Asana"},
        issue_id="iss_extractor",
    )
    issue_collector = _issue(
        target="collector",
        required_inputs={"product": "Notion"},
        issue_id="iss_collector",
    )
    goto, upd = decide_qa_route(
        _verdict(
            routing=[
                QARouting(target_agent="extractor", reason="missing extract", payload={}),
                QARouting(target_agent="collector", reason="missing collect", payload={}),
            ],
            blocking=True,
            issues=[issue_extractor, issue_collector],
        ),
        qa_round=0,
        max_rounds=3,
        products=["Notion", "Asana"],
    )
    # collector is most-upstream → routed to collect_dispatch
    assert goto == "collect_dispatch"
    assert upd["rework_target"] == "collector"
    # ONLY Notion (from collector's issue), NOT Asana (extractor's issue)
    assert upd["rework_products"] == ["Notion"]


def test_qa_feedback_by_node_reporter_key() -> None:
    """reporter routing → qa_feedback_by_node has 'reporter' key with from_verdict_id."""
    goto, upd = decide_qa_route(
        _verdict(
            routing=[QARouting(target_agent="reporter", reason="rewrite section", payload={})],
            blocking=True,
        ),
        qa_round=0,
        max_rounds=3,
        products=["Notion", "Asana"],
    )
    assert goto == "reporter"
    fb = upd.get("qa_feedback_by_node", {})
    assert "reporter" in fb, (
        f"expected 'reporter' key in qa_feedback_by_node, got {list(fb.keys())}"
    )
    payload = fb["reporter"]
    assert "from_verdict_id" in payload, (
        f"expected 'from_verdict_id' in reporter payload, got {list(payload.keys())}"
    )


def test_qa_feedback_by_node_extractor_asana_key() -> None:
    """extractor routing naming product 'Asana' → qa_feedback_by_node has 'extract.Asana' key."""
    issue = _issue(
        target="extractor",
        required_inputs={"product": "Asana"},
        issue_id="iss_asana_fb",
    )
    goto, upd = decide_qa_route(
        _verdict(
            routing=[QARouting(target_agent="extractor", reason="missing data", payload={})],
            blocking=True,
            issues=[issue],
        ),
        qa_round=0,
        max_rounds=3,
        products=["Notion", "Asana"],
    )
    assert goto == "extract_dispatch"
    fb = upd.get("qa_feedback_by_node", {})
    assert "extract.Asana" in fb, (
        f"expected 'extract.Asana' key in qa_feedback_by_node, got {list(fb.keys())}"
    )
    payload = fb["extract.Asana"]
    assert "from_verdict_id" in payload, (
        f"expected 'from_verdict_id' in extract.Asana payload, got {list(payload.keys())}"
    )
