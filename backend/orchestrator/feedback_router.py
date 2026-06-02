"""FeedbackRouter —— QA 反馈 → 派生 ``_v{n+1}`` 节点 + 下游回 PENDING。

调用模式（由 Orchestrator 主类调度）::

    router = FeedbackRouter(max_rounds=3)
    outcome = router.apply(
        verdict=qa_output.verdict,
        plan=current_plan,
        qa_round_count=state.qa_round_count,
    )
    if not outcome.aborted:
        # 把 outcome 落到 DAGState 与 outputs
        ...

设计点（docs/DAG.md § 7）：

- ``rework_target`` = ``verdict.routing[i].target_agent`` 命中的"最近一次成功节点"。
  对 per-product 节点（collector/extractor）每个 product 各派生一个 _v 节点；
  对单节点 agent（analyst/reporter）派生 1 个 _v 节点。
- 老节点保留在 plan 里供 UI 回放；不删除老边，靠 ``input_refs`` 驱动 readiness。
- 下游所有节点（含控制节点）状态置回 PENDING；直接下游的 ``input_refs`` 中
  老节点 id 替换为新 id。
- 同一轮 verdict 多个 routing 时按 target_agent 顺序处理，互不阻塞。
- 防死循环：``qa_round_count >= max_rounds`` 直接 ``aborted=True``，由调用方
  决定是否强制发布。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from backend.schemas import (
    DAGEdge,
    DAGNode,
    DAGPlan,
    NodeStatus,
    NodeType,
    QAFeedback,
    QARouting,
    QAVerdict,
)


# QA 循环上限（docs/QA.md / DAG.md 默认 3 轮；可在构造时覆盖）
DEFAULT_MAX_ROUNDS = 3

_VERSION_SUFFIX_RE = re.compile(r"_v\d+$")


@dataclass
class FeedbackOutcome:
    """FeedbackRouter 的纯结果对象，由 Orchestrator 应用到状态。"""

    new_nodes: list[DAGNode] = field(default_factory=list)
    new_edges: list[DAGEdge] = field(default_factory=list)
    # node_id -> 新 input_refs（替代 plan.nodes[node_id].input_refs）
    node_input_refs_updates: dict[str, list[str]] = field(default_factory=dict)
    # node_id -> 应重置为的状态（通常是 PENDING）
    node_status_resets: dict[str, NodeStatus] = field(default_factory=dict)
    # new_node_id -> qa_feedback 字典（注入到 Agent input）
    qa_feedback_by_node: dict[str, dict] = field(default_factory=dict)
    # 是否流程中止（max_rounds 或无匹配 target）
    aborted: bool = False
    abort_reason: str = ""
    # 本轮处理的 verdict（供 Orchestrator 落历史）
    verdict: QAVerdict | None = None


class FeedbackRouter:
    """无状态路由器。多次 apply 产出独立的 FeedbackOutcome。"""

    def __init__(self, *, max_rounds: int = DEFAULT_MAX_ROUNDS) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        self.max_rounds = max_rounds

    def apply(
        self,
        *,
        verdict: QAVerdict,
        plan: DAGPlan,
        qa_round_count: int,
    ) -> FeedbackOutcome:
        """处理一份 QAVerdict.routing，返回应用到 DAG 的变更。"""
        if qa_round_count >= self.max_rounds:
            return FeedbackOutcome(
                aborted=True,
                abort_reason=(
                    f"qa_round_count={qa_round_count} >= max_rounds={self.max_rounds}; "
                    "force-publishing without further rework"
                ),
                verdict=verdict,
            )

        if not verdict.routing:
            return FeedbackOutcome(
                aborted=True,
                abort_reason="verdict has no routing entries",
                verdict=verdict,
            )

        by_id = {n.node_id: n for n in plan.nodes}
        outcome = FeedbackOutcome(verdict=verdict)
        # 一轮 verdict 内累积的下游 input_refs 变更（多 routing 共用）
        updated_refs: dict[str, list[str]] = {}

        any_matched = False
        for routing in verdict.routing:
            targets = _find_rework_targets(plan, routing.target_agent)
            if not targets:
                continue
            any_matched = True

            qa_round = qa_round_count + 1
            for old in targets:
                new_id = _next_versioned_id(old.node_id, old.revision)
                payload = _build_qa_feedback_payload(
                    verdict, routing, qa_round=qa_round
                )
                new_node = _spawn_rework_node(
                    old=old,
                    new_id=new_id,
                    qa_round=qa_round,
                )
                outcome.new_nodes.append(new_node)
                outcome.qa_feedback_by_node[new_id] = payload

                # 新节点的上游边：input_refs 中的每一个上游 → new_id
                for ref in new_node.input_refs:
                    outcome.new_edges.append(
                        DAGEdge(
                            edge_id=f"{ref}->{new_id}",
                            from_node=ref,
                            to_node=new_id,
                            edge_type="feedback",
                        )
                    )

                # 直接下游：边 + input_refs 替换
                for edge in plan.edges:
                    if edge.from_node != old.node_id:
                        continue
                    downstream_id = edge.to_node
                    downstream = by_id.get(downstream_id)
                    if downstream is None:
                        continue
                    outcome.new_edges.append(
                        DAGEdge(
                            edge_id=f"{new_id}->{downstream_id}",
                            from_node=new_id,
                            to_node=downstream_id,
                            edge_type="feedback",
                        )
                    )
                    current_refs = updated_refs.get(
                        downstream_id, list(downstream.input_refs)
                    )
                    new_refs = [
                        new_id if r == old.node_id else r for r in current_refs
                    ]
                    updated_refs[downstream_id] = new_refs

                # 传递下游（含控制节点）全部 reset 到 PENDING
                for nid in _bfs_downstream(plan, old.node_id):
                    outcome.node_status_resets[nid] = NodeStatus.PENDING

        if not any_matched:
            return FeedbackOutcome(
                aborted=True,
                abort_reason=(
                    f"no nodes matched any routing target_agent "
                    f"({[r.target_agent for r in verdict.routing]})"
                ),
                verdict=verdict,
            )

        outcome.node_input_refs_updates = updated_refs
        return outcome


# ---------- internals ----------


def _base_id(node_id: str) -> str:
    """剥掉末尾的 ``_v{n}`` 后缀；非版本节点原样返回。"""
    return _VERSION_SUFFIX_RE.sub("", node_id)


def _next_versioned_id(node_id: str, current_revision: int) -> str:
    return f"{_base_id(node_id)}_v{current_revision + 1}"


def _find_rework_targets(plan: DAGPlan, target_agent: str) -> list[DAGNode]:
    """按 base_id 分组取最新 revision，过滤 agent_name 匹配的 AGENT_CALL 节点。"""
    latest: dict[str, DAGNode] = {}
    for node in plan.nodes:
        if node.node_type != NodeType.AGENT_CALL:
            continue
        if node.agent_name != target_agent:
            continue
        base = _base_id(node.node_id)
        existing = latest.get(base)
        if existing is None or node.revision > existing.revision:
            latest[base] = node
    # 稳定排序：按 base_id 字典序
    return [latest[k] for k in sorted(latest)]


def _bfs_downstream(plan: DAGPlan, root_id: str) -> set[str]:
    """边图 BFS，返回所有传递下游节点 id（不含 root）。"""
    adj: dict[str, list[str]] = {}
    for edge in plan.edges:
        adj.setdefault(edge.from_node, []).append(edge.to_node)
    visited: set[str] = set()
    queue: deque[str] = deque(adj.get(root_id, []))
    while queue:
        cur = queue.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        queue.extend(adj.get(cur, []))
    return visited


def _spawn_rework_node(*, old: DAGNode, new_id: str, qa_round: int) -> DAGNode:
    """根据老节点克隆一个 _v{n+1} 新节点（status=PENDING）。"""
    metadata = dict(old.metadata)
    metadata["qa_feedback_round"] = qa_round
    return DAGNode(
        node_id=new_id,
        project_id=old.project_id,
        node_type=NodeType.AGENT_CALL,
        agent_name=old.agent_name,
        status=NodeStatus.PENDING,
        input_refs=list(old.input_refs),
        output_ref=None,
        retry_count=0,
        max_retries=old.max_retries,
        timeout_ms=old.timeout_ms,
        started_at=None,
        ended_at=None,
        parent_node_id=old.node_id,
        revision=old.revision + 1,
        metadata=metadata,
    )


def _build_qa_feedback_payload(
    verdict: QAVerdict, routing: QARouting, *, qa_round: int
) -> dict:
    """根据 verdict + routing 组装 ``qa_feedback`` dict（即将注入到 Agent input）。

    payload 的 ``revision`` 字段会被 Reporter 用来 bump ``ReportDraft.version``，
    QA mock 据此 fixture 切换；其他 Agent 忽略该字段不影响行为。
    """
    related_issues = [
        issue for issue in verdict.issues if issue.target_agent == routing.target_agent
    ]
    must_address = list(routing.payload.get("must_address", []))
    if not must_address:
        # 默认所有相关 issue 都必须解决
        must_address = [issue.issue_id for issue in related_issues]
    feedback = QAFeedback(
        from_verdict_id=verdict.verdict_id,
        issues=related_issues,
        instructions=routing.reason,
        must_address=must_address,
    )
    payload = feedback.model_dump(mode="json")
    payload["revision"] = qa_round
    return payload


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "FeedbackOutcome",
    "FeedbackRouter",
]
