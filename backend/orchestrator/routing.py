"""QA verdict → 原生图回环决策。

移植 feedback_router 的产品收窄逻辑，作为纯函数供 LangGraph 图节点调用。
不依赖 LangGraph 图结构，仅导入 END 哨兵值。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END

# 上游→下游顺序；多 routing 取最上游单一目标（最上游重跑必带动下游）
_AGENT_ORDER = ["collector", "extractor", "analyst", "reporter"]

# 原生图中各 Agent 的入口节点名称
_AGENT_TO_ENTRY: dict[str, str] = {
    "collector": "collect_dispatch",
    "extractor": "extract_dispatch",
    "analyst": "analyst",
    "reporter": "reporter",
}

# 与 feedback_router._PRODUCT_STR_KEYS / _PRODUCT_LIST_KEYS 保持一致
_PRODUCT_STR_KEYS = ("product", "competitor")
_PRODUCT_LIST_KEYS = (
    "products_missing",
    "competitors_involved",
    "products",
    "competitors",
)


def _wanted_products(issues: list) -> set[str]:
    """从 issues.required_inputs 提取被点名的产品名集合。

    调用方负责预先将 issues 过滤至 target_agent == chosen（与
    feedback_router.apply() 中 relevant_issues 的做法保持一致），
    本函数只做 required_inputs 字段展开，不再重复过滤。
    """
    out: set[str] = set()
    for i in issues:
        ri = getattr(i, "required_inputs", None) or {}
        for k in _PRODUCT_STR_KEYS:
            v = ri.get(k)
            if isinstance(v, str) and v.strip():
                out.add(v)
        for k in _PRODUCT_LIST_KEYS:
            v = ri.get(k)
            if isinstance(v, list):
                out.update(x for x in v if isinstance(x, str) and x.strip())
    return out


def decide_qa_route(
    verdict: Any,
    *,
    qa_round: int,
    max_rounds: int,
    products: list[str],
) -> tuple[Any, dict]:
    """根据 QA verdict 决定图回环目标，返回 (goto, state_update)。

    规则（按优先级）：
    1. qa_round >= max_rounds → 强制发布，goto=END，state_update["aborted"]=True。
    2. routing 为空或 blocking=False → 正常结束，goto=END。
    3. 否则，取 routing 中最上游 target_agent，映射到图入口节点名称。
       - 对 per-product Agent（collector/extractor），从 issues.required_inputs
         收窄 rework_products；收窄为空时回退到全量 products，绝不丢返工。
       - reporter/analyst 为全局 Agent，rework_products=[]。

    Args:
        verdict: QAVerdict 实例。
        qa_round: 当前已完成的 QA 轮次数。
        max_rounds: 允许的最大 QA 轮次数。
        products: 本次任务涉及的所有产品名列表。

    Returns:
        (goto, state_update_dict)，goto=END 表示收尾。
    """
    # 规则 1：轮次上限 → 强制终止
    if qa_round >= max_rounds:
        return END, {
            "aborted": True,
            "abort_reason": (
                f"qa_round={qa_round} >= max_rounds={max_rounds}; force-publish"
            ),
        }

    # 规则 2：无路由指令或非阻塞 → 正常结束
    routing = getattr(verdict, "routing", None) or []
    if not routing or getattr(verdict, "blocking", True) is False:
        return END, {}

    # 规则 3：取最上游 target_agent
    targets = {r.target_agent for r in routing}
    chosen = next((a for a in _AGENT_ORDER if a in targets), None)
    if chosen is None:
        return END, {}

    # 对 per-product Agent 收窄返工产品集合
    per_product = chosen in ("collector", "extractor")
    if per_product:
        all_issues = getattr(verdict, "issues", []) or []
        # 镜像 feedback_router.apply() 的做法：只保留目标 Agent 的 issues
        relevant = [i for i in all_issues if getattr(i, "target_agent", None) == chosen]
        rework: list[str] = sorted(_wanted_products(relevant))
        if not rework:
            # 收窄不到就全量重做，绝不丢返工
            rework = list(products)
    else:
        rework = []

    qa_feedback_by_node = _build_qa_feedback_by_node(
        verdict, chosen=chosen, rework=rework, qa_round=qa_round + 1
    )

    return _AGENT_TO_ENTRY[chosen], {
        "qa_round": qa_round + 1,
        "rework_target": chosen,
        "rework_products": rework,
        "qa_feedback_by_node": qa_feedback_by_node,
    }


# 逻辑 Agent → rework 节点入口前缀(per-product 用于拼 ``{prefix}.{product}``)
_AGENT_NODE_PREFIX: dict[str, str] = {
    "collector": "collect",
    "extractor": "extract",
}


def _build_qa_feedback_by_node(
    verdict: Any, *, chosen: str, rework: list[str], qa_round: int
) -> dict[str, dict]:
    """为 chosen 返工目标构造 ``{entry_node_id: qa_feedback_payload}``。

    复用 feedback_router._build_qa_feedback_payload 生成的 payload(与 legacy
    完全同形:含 from_verdict_id / issues / instructions / must_address /
    revision)。键名约定(与 nodes.py 读取处一一对应):

    - collector → ``collect.{product}``(rework 里每个产品一个键)
    - extractor → ``extract.{product}``
    - analyst   → ``analyst``
    - reporter  → ``reporter``

    chosen 名下若有多条 routing,取第一条生成 payload(legacy 亦按 routing 逐条
    处理,这里聚合为单一目标已足够覆盖返工指令)。
    """
    from backend.orchestrator.feedback_router import _build_qa_feedback_payload

    routing_list = getattr(verdict, "routing", None) or []
    chosen_routing = next(
        (r for r in routing_list if r.target_agent == chosen), None
    )
    if chosen_routing is None:
        return {}
    payload = _build_qa_feedback_payload(verdict, chosen_routing, qa_round=qa_round)

    prefix = _AGENT_NODE_PREFIX.get(chosen)
    if prefix is not None:
        # per-product:每个待返工产品一个键
        return {f"{prefix}.{p}": payload for p in rework}
    # 全局 Agent:键即节点名
    return {chosen: payload}


__all__ = ["decide_qa_route"]
