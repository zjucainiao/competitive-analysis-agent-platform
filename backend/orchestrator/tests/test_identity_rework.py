"""P4：身份 mismatch → collector 返工收敛闭环（native）。

QA 的 identity issue 带 mismatch_source_urls → decide_qa_route 装配
collect.{product} 反馈 → build_collector_input 解出 exclude_source_urls →
collector 重采时跳过这些跑题页面，使返工真正收敛（不再抓回同一坏源）。
"""

from __future__ import annotations

from backend.agents.qa.routing import build_routing
from backend.orchestrator.inputs import (
    _exclude_urls_from_feedback,
    build_collector_input,
)
from backend.orchestrator.routing import decide_qa_route
from backend.orchestrator.tests.test_native_graph import _load_demo_project
from backend.schemas import QADimension, QAIssue, QAStatus, QAVerdict


def _mismatch_verdict(product: str, url: str) -> QAVerdict:
    issue = QAIssue(
        issue_id="iss_identity_mismatch_x",
        dimension=QADimension.IDENTITY_CONSISTENCY,
        severity="major",
        location=f"evidence[product={product}]",
        problem="抓错产品",
        suggested_fix="排除跑题源重采",
        target_agent="collector",
        required_inputs={"product": product, "mismatch_source_urls": [url]},
    )
    routing = build_routing([issue], blocking=True)
    return QAVerdict(
        verdict_id="v_identity",
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={},
        issues=[issue],
        routing=routing,
        blocking=True,
    )


def test_exclude_urls_extracted_from_feedback_payload() -> None:
    fb = {
        "issues": [
            {"required_inputs": {"mismatch_source_urls": ["https://x.com/a", "https://x.com/b"]}},
            {"required_inputs": {"product": "Lark"}},  # 无 mismatch urls → 忽略
        ]
    }
    assert _exclude_urls_from_feedback(fb) == ["https://x.com/a", "https://x.com/b"]


def test_no_feedback_means_no_exclusion() -> None:
    assert _exclude_urls_from_feedback(None) == []
    assert _exclude_urls_from_feedback({}) == []


def test_identity_route_threads_exclude_urls_into_collector_input() -> None:
    product = "Lark"
    bad_url = "https://thirdparty.com/dingtalk-vs-lark"
    verdict = _mismatch_verdict(product, bad_url)

    goto, update = decide_qa_route(verdict, qa_round=0, max_rounds=3, products=[product])
    # 路由回 collector 入口，且按 required_inputs.product 收窄到该产品
    assert goto == "collect_dispatch"
    assert update["rework_target"] == "collector"
    assert product in update["rework_products"]

    payload = update["qa_feedback_by_node"][f"collect.{product}"]
    project = _load_demo_project(products=[product])
    inp = build_collector_input(
        project,
        trace_id="t",
        product=product,
        official_url=None,
        dims=["homepage"],
        qa_feedback=payload,
    )
    # collector 拿到排除清单 → 重采跳过跑题页面
    assert bad_url in inp.exclude_source_urls
