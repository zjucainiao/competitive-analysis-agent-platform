"""临时迁移脚手架：RunState → DAGPlan 形状，供旧 /state + 前端在 Phase 2 前消费。

Phase 3 删除本文件，同步删除 tests/test_native_projection.py。

contract：
- 接受 ``RunState.model_dump()`` 产出的 ``state: dict``。
- ``state["history"]`` 是 **dict 列表**（非 NodeRun 对象），各 dict 的 key 与
  ``NodeRun`` 字段名一一对应：node / agent / product / round / status /
  span_id / output_ref 等。
- ``state["outputs"]`` 是 ``{ref_str: output_dict}``。
- 返回 ``(DAGPlan, {node_id: output_dict})``。
"""
from __future__ import annotations

from ulid import ULID

from backend.schemas import DAGNode, DAGPlan, NodeStatus, NodeType, Project

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

    for run in history:
        node_name: str = run["node"]
        product: str | None = run.get("product")
        round_: int = run.get("round", 1)

        nid = _node_id(node_name, product, round_)
        # 同一 nid 可能因重复 Send/barrier 出现多次；只取第一条
        if nid in seen:
            continue
        seen.add(nid)

        run_status: str = run.get("status", "")
        dag_status = (
            NodeStatus.SUCCESS if run_status in _SUCCESS_STATUSES else NodeStatus.FAILED
        )

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
                started_at=None,
                ended_at=None,
                parent_node_id=None,
                revision=round_,
                metadata=({"product": product} if product else {}),
            )
        )

        ref = run.get("output_ref")
        if ref and ref in outputs_by_ref:
            out_map[nid] = outputs_by_ref[ref]

    plan = DAGPlan(
        plan_id=f"plan_{ULID()}",
        project_id=project.project_id,
        template_id="native",
        nodes=nodes,
        edges=[],          # Phase 2 前端 DAG 视图重构后补；此处留空
        rationale="native-graph projection",
        confidence=1.0,
        complexity_score=min(1.0, len(nodes) / 20.0),
    )
    return plan, out_map
