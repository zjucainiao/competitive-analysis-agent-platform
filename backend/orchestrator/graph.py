"""原生 LangGraph 图装配。

``build_native_graph(registry, *, project, checkpointer=None)`` 把
``make_nodes`` 的节点接成完整流水线并 compile。

边的设计(经 LangGraph 1.2.4 实测验证):
- ``START → collect_dispatch``:入口。
- ``collect_dispatch`` / ``extract_dispatch`` / ``qa`` 通过返回
  ``Command(goto=[Send(...)])`` 或 ``Command(goto=...)`` **动态**扇出/路由,
  **不需要**到目标的静态边。
- worker(collect_one / extract_one)**需要**自己的出边接到下游 barrier:
  ``collect_one → extract_dispatch``、``extract_one → analyst``。barrier 节点
  在所有同名 worker 完成后只跑一次,看到合并后的全局 state。
- ``analyst → reporter → qa``:线性主链。
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import START, StateGraph

from backend.orchestrator.nodes import make_nodes
from backend.orchestrator.run_state import RunState


def build_native_graph(registry: Any, *, project: Any, checkpointer: Any = None):
    """装配并编译原生编排图。

    Args:
        registry: 提供 .get / .make_reporter / .make_qa 的 AgentRegistry。
        project: 当前运行的 Project(传给节点闭包)。
        checkpointer: 可选 LangGraph checkpointer;None 表示不持久化(测试用)。

    Returns:
        已 compile 的 CompiledStateGraph,可 .ainvoke / .astream。
    """
    g = StateGraph(RunState)

    for name, fn in make_nodes(registry, project=project).items():
        g.add_node(name, fn)

    g.add_edge(START, "collect_dispatch")
    # barrier:所有 collect_one 完成 → extract_dispatch 跑一次
    g.add_edge("collect_one", "extract_dispatch")
    # barrier:所有 extract_one 完成 → analyst 跑一次
    g.add_edge("extract_one", "analyst")
    g.add_edge("analyst", "reporter")
    g.add_edge("reporter", "qa")
    # collect_dispatch / extract_dispatch 经 Command(goto=[Send...]) 扇出;
    # qa 经 Command(goto=...) 路由 —— 均无静态出边。

    return g.compile(checkpointer=checkpointer)


__all__ = ["build_native_graph"]
