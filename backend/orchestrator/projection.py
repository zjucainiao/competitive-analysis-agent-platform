"""RunState → DAGPlan 形状投影。

Stage D 后**已不再对前端暴露**(旧 `/state` 路由已删；前端改为单一数据源
`/run-state`，自行投影出 DAGPlan，见前端 run-view-to-state.ts)。本模块现为
**orchestrator 内部机制**：`run_state_to_dagplan` 供 metrics 计算
(compute_project_metrics 需要 DAGPlan)，`_node_id` 供 run_view 复用键法。

contract：
- 接受 ``RunState.model_dump()`` 产出的 ``state: dict``。
- ``state["history"]`` 是 **dict 列表**（非 NodeRun 对象），各 dict 的 key 与
  ``NodeRun`` 字段名一一对应：node / agent / product / round / status /
  span_id / output_ref 等。
- ``state["outputs"]`` 是 ``{ref_str: output_dict}``。
- 返回 ``(DAGPlan, {node_id: output_dict})``。
"""
from __future__ import annotations

from datetime import datetime
from ulid import ULID

from backend.schemas import DAGEdge, DAGNode, DAGPlan, NodeStatus, NodeType, Project

# 流水线主链顺序(用于投影边):collect → extract → analyst → reporter → qa
_PIPELINE_ORDER: list[str] = ["collect", "extract", "analyst", "reporter", "qa"]

# 逻辑节点名 → agent_name（填写 DAGNode.agent_name）
_AGENT_OF: dict[str, str] = {
    "collect": "collector",
    "extract": "extractor",
    "analyst": "analyst",
    "reporter": "reporter",
    "qa": "qa",
}

# 终态中被视为"节点执行成功"的状态字符串集合
# （needs_rework / partial 表示节点本身正常完成，只是结果触发了 QA 反馈）
_SUCCESS_STATUSES = frozenset({"success", "partial", "needs_rework"})


def _parse_iso_dt(value: str | None) -> datetime | None:
    """将 ISO 8601 字符串解析为 datetime；None 或空字符串返回 None。

    NodeRun.started_at / ended_at 由 run_agent_node 写入 ISO 字符串；
    control 节点或失败节点可能缺失。
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _node_id(node: str, product: str | None, round_: int) -> str:
    """计算投影后的节点 ID。

    - 带产品后缀：``collect.Notion``
    - round > 1 追加版本后缀：``reporter_v2``（前端 v1↔v2 回放依赖此命名）
    """
    base = f"{node}.{product}" if product else node
    return base if round_ <= 1 else f"{base}_v{round_}"


def run_state_to_dagplan(state: dict, *, project: Project) -> tuple[DAGPlan, dict]:
    """将 RunState.model_dump() 投影为旧 DAGPlan + node_id→output 映射。

    :param state: ``RunState.model_dump()`` 的结果，history 中每条记录为 dict。
    :param project: 当前项目 ``Project`` 实例，用于填写 project_id。
    :returns: ``(DAGPlan, out_map)``，其中 out_map 键为投影节点 ID，值为 output dict。
    """
    history: list[dict] = state["history"]
    outputs_by_ref: dict = state["outputs"]

    nodes: list[DAGNode] = []
    out_map: dict = {}
    seen: set[str] = set()
    # stage → 该阶段所有 (node_id, product, round)，用于建主链边 + 修订父子链
    by_stage: dict[str, list[tuple[str, str | None, int]]] = {}

    for run in history:
        node_name: str = run["node"]
        product: str | None = run.get("product")
        round_: int = run.get("round", 1)

        nid = _node_id(node_name, product, round_)
        # 同一 nid 可能因重复 Send/barrier 出现多次；只取第一条
        if nid in seen:
            continue
        seen.add(nid)
        by_stage.setdefault(node_name, []).append((nid, product, round_))

        run_status: str = run.get("status", "")
        dag_status = (
            NodeStatus.SUCCESS if run_status in _SUCCESS_STATUSES else NodeStatus.FAILED
        )

        # 修订节点(round>1)指向同阶段同产品的上一轮节点,前端据 parent_node_id
        # 把它渲染成 feedback 子节点(v1↔v2 回放)
        parent_id = _node_id(node_name, product, round_ - 1) if round_ > 1 else None

        nodes.append(
            DAGNode(
                node_id=nid,
                project_id=project.project_id,
                node_type=NodeType.AGENT_CALL,
                agent_name=_AGENT_OF.get(node_name),
                status=dag_status,
                input_refs=[],
                output_ref=run.get("output_ref"),
                retry_count=0,
                max_retries=3,
                timeout_ms=60_000,
                started_at=_parse_iso_dt(run.get("started_at")),
                ended_at=_parse_iso_dt(run.get("ended_at")),
                parent_node_id=parent_id,
                revision=round_,
                metadata=({"product": product} if product else {}),
            )
        )

        ref = run.get("output_ref")
        if ref and ref in outputs_by_ref:
            out_map[nid] = outputs_by_ref[ref]

    edges = _build_pipeline_edges(by_stage)

    plan = DAGPlan(
        plan_id=f"plan_{ULID()}",
        project_id=project.project_id,
        template_id="native",
        nodes=nodes,
        edges=edges,
        rationale="native-graph projection",
        confidence=1.0,
        complexity_score=min(1.0, len(nodes) / 20.0),
    )
    return plan, out_map


def _build_pipeline_edges(
    by_stage: dict[str, list[tuple[str, str | None, int]]]
) -> list[DAGEdge]:
    """据各阶段节点重建流水线主链边,让前端 DAG 视图能正确分层布局。

    边语义(仅取每阶段 round=1 的"基"节点连主链;修订节点靠 parent_node_id):
    - collect.{p} → extract.{p}(按产品配对;无配对则 collect.{p} → 各 analyst 入口)
    - extract.{p}(或 collect.{p})→ analyst
    - analyst → reporter → qa(相邻阶段基节点直连)
    """
    edges: list[DAGEdge] = []

    def _add(frm: str, to: str) -> None:
        edges.append(
            DAGEdge(
                edge_id=f"{frm}->{to}",
                from_node=frm,
                to_node=to,
                edge_type="dependency",
            )
        )

    # 每阶段的 round=1 基节点(product → node_id);单节点阶段 key=None
    base: dict[str, dict[str | None, str]] = {}
    for stage, members in by_stage.items():
        base[stage] = {p: nid for (nid, p, r) in members if r == 1}

    collects = base.get("collect", {})
    extracts = base.get("extract", {})
    analyst_id = next(iter(base.get("analyst", {}).values()), None)
    reporter_id = next(iter(base.get("reporter", {}).values()), None)
    qa_id = next(iter(base.get("qa", {}).values()), None)

    # collect.{p} → extract.{p}
    for product, c_id in collects.items():
        e_id = extracts.get(product)
        if e_id:
            _add(c_id, e_id)

    # extract.{p} → analyst(无 extract 时退化为 collect.{p} → analyst)
    if analyst_id:
        upstreams = extracts or collects
        for up_id in upstreams.values():
            _add(up_id, analyst_id)

    # analyst → reporter → qa
    if analyst_id and reporter_id:
        _add(analyst_id, reporter_id)
    if reporter_id and qa_id:
        _add(reporter_id, qa_id)

    return edges
