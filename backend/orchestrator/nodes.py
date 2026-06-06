"""原生 LangGraph 节点函数工厂。

``make_nodes(registry, *, project)`` 返回 {node_name: callable} 闭包字典,
闭包捕获 (registry, project)。节点分两类:

- **normal 节点**(START→node / node→node 边到达):入参是 ``RunState`` Pydantic
  实例,可属性访问全局 channel(state.outputs / state.products …)。
  含 collect_dispatch / extract_dispatch / analyst / reporter / qa。
- **Send-target 节点**(经 ``Send("node", payload)`` 到达):入参是 ``payload``
  原始 dict,**看不到任何全局 channel**。含 collect_one / extract_one。
  因此 dispatch 节点必须把 worker 需要的一切打包进 Send payload。

经验事实(LangGraph 1.2.4 实测):
- Send payload 是 partial dict,**不**按 RunState 校验,worker 直接拿原 dict。
- normal 节点返回 dict / Command(update=...) 时,reducer(merge_outputs /
  append_list)会正确合并并行 Send 分支的并发写。
- ``add_edge("collect_one","extract_dispatch")`` 让 extract_dispatch 在**所有**
  collect_one 完成后只跑一次(barrier),看到合并后的全局 state。
"""
from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END
from langgraph.types import Command, Send

from backend.orchestrator.inputs import (
    build_analyst_input,
    build_collector_input,
    build_extractor_input,
    build_qa_input,
    build_reporter_input,
)
from backend.orchestrator.routing import decide_qa_route
from backend.orchestrator.run_agent import AgentRunResult, run_agent_node
from backend.orchestrator.run_state import NodeRun
from backend.schemas import AgentStatus
from backend.schemas.evidence import CollectDimension

# QA 路由轮次上限(与 decide_qa_route 的 max_rounds 一致)
_MAX_QA_ROUNDS = 3

# Collector 默认采集维度。
# 注意:**不能**用 project.analysis_dimensions —— 那是 analyst 级枚举
# (feature_comparison/swot…),不是合法 CollectDimension,会让
# build_collector_input 里的 CollectDimension(d) 直接抛 ValueError。
# 这里复用 4 张行业模板里完全一致的 collect_dimensions 标准集
# (homepage/features/pricing/help_docs/user_reviews)。
_DEFAULT_COLLECT_DIMS = [
    CollectDimension.HOMEPAGE.value,
    CollectDimension.FEATURES.value,
    CollectDimension.PRICING.value,
    CollectDimension.HELP_DOCS.value,
    CollectDimension.REVIEWS.value,  # .value == "user_reviews"
]

# AgentStatus → NodeRun.status 字符串
_STATUS_MAP: dict[AgentStatus, str] = {
    AgentStatus.SUCCESS: "success",
    AgentStatus.PARTIAL: "partial",
    AgentStatus.NEEDS_REWORK: "needs_rework",
    AgentStatus.FAILED: "failed",
}


def _node_run(
    res: AgentRunResult,
    *,
    node: str,
    agent: str,
    output_ref: str,
    product: str | None = None,
    round_: int = 1,
) -> NodeRun:
    """把一次 AgentRunResult 折叠成一条 history NodeRun 记录。"""
    return NodeRun(
        node=node,
        agent=agent,
        product=product,
        round=round_,
        status=_STATUS_MAP.get(res.status, "failed"),
        span_id=res.span_id,
        started_at=res.started_at.isoformat() if res.started_at else None,
        ended_at=res.ended_at.isoformat() if res.ended_at else None,
        output_ref=output_ref,
    )


def make_nodes(registry: Any, *, project: Any) -> dict[str, Callable]:
    """构造原生图的节点闭包字典。

    Args:
        registry: 提供 .get / .make_reporter / .make_qa 的 AgentRegistry。
        project: 当前运行的 Project(节点从中取 dims / target / template 等)。

    Returns:
        {node_name: node_callable} —— 直接喂给 StateGraph.add_node。
    """
    trace_id = f"trace_{project.project_id}"
    dims = _DEFAULT_COLLECT_DIMS

    # ---------- collector 阶段 ----------

    def collect_dispatch(state) -> Command:
        """normal 节点:对每个目标产品扇出一个 collect_one Send。

        targets = rework_products(返工时) or products(首跑)。
        round = qa_round + 1(首跑 qa_round=0 → round=1)。
        """
        targets = state.rework_products or state.products
        round_ = state.qa_round + 1
        return Command(
            goto=[
                Send("collect_one", {"product": p, "round": round_})
                for p in targets
            ]
        )

    async def collect_one(payload: dict) -> dict:
        """Send-target:**入参是原始 dict**,看不到全局 state。

        从 payload 取 product / round,跑 collector,产出 outputs + history。
        collector 不依赖上游 evidence,故 outputs={} 传 run_agent_node 即可。
        """
        product = payload["product"]
        round_ = payload.get("round", 1)
        inp = build_collector_input(
            project,
            trace_id=trace_id,
            product=product,
            official_url=None,
            dims=dims,
            qa_feedback=None,
        )
        res = await run_agent_node(
            registry,
            "collector",
            inp,
            outputs={},
            trace_id=trace_id,
            node_id=f"collect.{product}",
        )
        ref = f"collect.{product}"
        return {
            "outputs": {ref: res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="collect",
                    agent="collector",
                    output_ref=ref,
                    product=product,
                    round_=round_,
                )
            ],
        }

    # ---------- extractor 阶段 ----------

    def extract_dispatch(state) -> Command:
        """normal 节点:把对应 collector output 打包进 Send,扇出 extract_one。

        worker 读不到全局 state,所以 collector_output 必须随 payload 一起带过去。
        """
        targets = state.rework_products or state.products
        round_ = state.qa_round + 1
        return Command(
            goto=[
                Send(
                    "extract_one",
                    {
                        "product": p,
                        "collector_output": state.outputs.get(f"collect.{p}"),
                        "round": round_,
                    },
                )
                for p in targets
            ]
        )

    async def extract_one(payload: dict) -> dict:
        """Send-target:从 payload 取 product + collector_output,跑 extractor。"""
        product = payload["product"]
        round_ = payload.get("round", 1)
        inp = build_extractor_input(
            project,
            trace_id=trace_id,
            product=product,
            collector_output=payload["collector_output"],
            qa_feedback=None,
        )
        res = await run_agent_node(
            registry,
            "extractor",
            inp,
            outputs={},
            trace_id=trace_id,
            node_id=f"extract.{product}",
        )
        ref = f"extract.{product}"
        return {
            "outputs": {ref: res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="extract",
                    agent="extractor",
                    output_ref=ref,
                    product=product,
                    round_=round_,
                )
            ],
        }

    # ---------- analyst / reporter / qa(全局 normal 节点) ----------

    async def analyst(state) -> dict:
        """normal 节点:聚合所有 extract.* profile 跑 analyst。"""
        round_ = state.qa_round + 1
        inp = build_analyst_input(
            project,
            trace_id=trace_id,
            outputs=state.outputs,
            qa_feedback=None,
        )
        res = await run_agent_node(
            registry,
            "analyst",
            inp,
            outputs=state.outputs,
            trace_id=trace_id,
            node_id="analyst",
        )
        return {
            "outputs": {"analyst": res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="analyst",
                    agent="analyst",
                    output_ref="analyst",
                    round_=round_,
                )
            ],
        }

    async def reporter(state) -> dict:
        """normal 节点:基于 analyst result 跑 reporter(回环时产新版 draft)。"""
        round_ = state.qa_round + 1
        inp = build_reporter_input(
            project,
            trace_id=trace_id,
            analyst_output=state.outputs["analyst"],
            qa_feedback=None,
        )
        res = await run_agent_node(
            registry,
            "reporter",
            inp,
            outputs=state.outputs,
            trace_id=trace_id,
            node_id="reporter",
        )
        return {
            "outputs": {"reporter": res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="reporter",
                    agent="reporter",
                    output_ref="reporter",
                    round_=round_,
                )
            ],
        }

    async def qa(state) -> Command:
        """normal 节点:跑 QA,据 verdict 经 decide_qa_route 决定回环目标。

        - verdict 为 None(QA 失败无输出)→ 直接 END。
        - 否则记录 verdict,调 decide_qa_route 得 (goto, route_update),
          goto 可能是 END / collect_dispatch / extract_dispatch / analyst /
          reporter;route_update 含 qa_round / rework_* / aborted 等。
        """
        round_ = state.qa_round + 1
        inp = build_qa_input(
            project,
            trace_id=trace_id,
            reporter_output=state.outputs["reporter"],
            analyst_output=state.outputs["analyst"],
            outputs=state.outputs,
            prior_verdicts=list(state.verdicts),
        )
        res = await run_agent_node(
            registry,
            "qa",
            inp,
            outputs=state.outputs,
            trace_id=trace_id,
            node_id="qa",
        )
        update: dict[str, Any] = {
            "outputs": {"qa": res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="qa",
                    agent="qa",
                    output_ref="qa",
                    round_=round_,
                )
            ],
        }

        verdict = getattr(res.output, "verdict", None)
        if verdict is None:
            return Command(goto=END, update=update)

        update["verdicts"] = [verdict]
        goto, route_update = decide_qa_route(
            verdict,
            qa_round=state.qa_round,
            max_rounds=_MAX_QA_ROUNDS,
            products=state.products,
        )
        update.update(route_update)
        return Command(goto=goto, update=update)

    return {
        "collect_dispatch": collect_dispatch,
        "collect_one": collect_one,
        "extract_dispatch": extract_dispatch,
        "extract_one": extract_one,
        "analyst": analyst,
        "reporter": reporter,
        "qa": qa,
    }


__all__ = ["make_nodes"]
