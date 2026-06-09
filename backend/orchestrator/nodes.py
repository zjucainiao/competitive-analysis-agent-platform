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
    BuildInputError,
    build_analyst_input,
    build_collector_input,
    build_extractor_input,
    build_qa_input,
    build_reporter_input,
    new_span_id,
)
from backend.orchestrator.routing import decide_qa_route
from backend.orchestrator.run_agent import AgentRunResult, run_agent_node
from backend.orchestrator.run_state import NodeRun, latest_output, versioned_ref
from backend.schemas import AgentStatus
from backend.schemas.evidence import CollectDimension

# QA 路由轮次上限(与 decide_qa_route 的 max_rounds 一致)
_MAX_QA_ROUNDS = 3

# 各节点单次执行超时(ms)。run_agent_node 默认 60s，对**真实采集/抽取**远远不够：
# collector 一个产品要 search + 抓多页 + 每页 page_type 分类 LLM + 身份校验 LLM
# （第三方噪音多的产品如 Figma 可达 100+ 次 LLM 调用），60s 必然撞超时 → 节点 failed
# → 下游全 "upstream output missing" 连锁失败。给足超时（仍受 collector 内部
# constraints.timeout_seconds 与抓取上限约束，不会真跑到上限）。
_NODE_TIMEOUT_MS: dict[str, int] = {
    "collector": 300_000,
    "extractor": 300_000,
    "analyst": 240_000,
    "reporter": 240_000,
    "qa": 180_000,
}

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


def _build_failed_run(
    *, node: str, agent: str, product: str | None, round_: int
) -> dict:
    """构造一个「构造输入即失败」的 node 返回(无 outputs + 一条 failed NodeRun)。

    fail-soft:``build_*_input`` 在 ``run_agent_node`` 之前调用,若上游缺失(collector
    output=None / 无 profiles)会抛 ``BuildInputError``。捕获它返回 failed NodeRun,
    让异常不冲出 LangGraph 节点、整条图优雅降级(下游 reporter/qa 已有 None fail-soft,
    最终 qa 节点统一收尾标 aborted),而不是把整个 run 直接打断成不可展示的崩溃。
    """
    return {
        "outputs": {},
        "history": [
            NodeRun(
                node=node,
                agent=agent,
                product=product,
                round=round_,
                status="failed",
                span_id=new_span_id(),
                output_ref=None,
            )
        ],
    }


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

        worker 看不到全局 state,故把该产品对应的 qa_feedback(若返工有)打包进
        Send payload(键 ``collect.{product}``,见 routing.decide_qa_route 文档)。
        """
        targets = state.rework_products or state.products
        round_ = state.qa_round + 1
        fb = state.qa_feedback_by_node
        ov = state.prompt_override_by_node
        return Command(
            goto=[
                Send(
                    "collect_one",
                    {
                        "product": p,
                        "round": round_,
                        "qa_feedback": fb.get(f"collect.{p}"),
                        # worker 看不到全局 state，节点级 prompt 覆盖随 payload 带过去
                        "prompt_override": ov.get(f"collect.{p}"),
                    },
                )
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
        try:
            inp = build_collector_input(
                project,
                trace_id=trace_id,
                product=product,
                official_url=None,
                dims=dims,
                qa_feedback=payload.get("qa_feedback"),
            )
        except BuildInputError:
            return _build_failed_run(
                node="collect", agent="collector", product=product, round_=round_
            )
        ref = versioned_ref(f"collect.{product}", round_)
        res = await run_agent_node(
            registry,
            "collector",
            inp,
            outputs={},
            trace_id=trace_id,
            node_id=ref,
            user_prompt_override=payload.get("prompt_override"),
            timeout_ms=_NODE_TIMEOUT_MS["collector"],
        )
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
        fb = state.qa_feedback_by_node
        ov = state.prompt_override_by_node
        return Command(
            goto=[
                Send(
                    "extract_one",
                    {
                        "product": p,
                        # 取该产品「最新轮」collect 产物(返工后裸 key 可能仍是 v1)
                        "collector_output": latest_output(
                            state.outputs, f"collect.{p}"
                        ),
                        "round": round_,
                        "qa_feedback": fb.get(f"extract.{p}"),
                        "prompt_override": ov.get(f"extract.{p}"),
                    },
                )
                for p in targets
            ]
        )

    async def extract_one(payload: dict) -> dict:
        """Send-target:从 payload 取 product + collector_output,跑 extractor。"""
        product = payload["product"]
        round_ = payload.get("round", 1)
        try:
            inp = build_extractor_input(
                project,
                trace_id=trace_id,
                product=product,
                collector_output=payload["collector_output"],
                qa_feedback=payload.get("qa_feedback"),
            )
        except BuildInputError:
            # 上游 collector 失败/无 raw_sources → 不崩图,记一条 failed 抽取节点
            return _build_failed_run(
                node="extract", agent="extractor", product=product, round_=round_
            )
        ref = versioned_ref(f"extract.{product}", round_)
        res = await run_agent_node(
            registry,
            "extractor",
            inp,
            outputs={},
            trace_id=trace_id,
            node_id=ref,
            user_prompt_override=payload.get("prompt_override"),
            timeout_ms=_NODE_TIMEOUT_MS["extractor"],
        )
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
        try:
            inp = build_analyst_input(
                project,
                trace_id=trace_id,
                outputs=state.outputs,
                qa_feedback=state.qa_feedback_by_node.get("analyst"),
            )
        except BuildInputError:
            # 全部上游 extractor 失败 → 无 profiles → 不崩图,记 failed analyst,
            # 下游 reporter/qa 的 None fail-soft 接力优雅收尾。
            return _build_failed_run(
                node="analyst", agent="analyst", product=None, round_=round_
            )
        ref = versioned_ref("analyst", round_)
        res = await run_agent_node(
            registry,
            "analyst",
            inp,
            outputs=state.outputs,
            trace_id=trace_id,
            node_id=ref,
            user_prompt_override=state.prompt_override_by_node.get("analyst"),
            timeout_ms=_NODE_TIMEOUT_MS["analyst"],
        )
        return {
            "outputs": {ref: res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="analyst",
                    agent="analyst",
                    output_ref=ref,
                    round_=round_,
                )
            ],
        }

    async def reporter(state) -> dict:
        """normal 节点:基于 analyst result 跑 reporter(回环时产新版 draft)。

        fail-soft:若上游 analyst 输出缺失/为 None(analyst 失败),不再无脑
        ``state.outputs["analyst"]`` 触 KeyError,而是直接早退返回一条 failed
        NodeRun(无 outputs),让 qa 节点接力判断并优雅终止。
        """
        round_ = state.qa_round + 1
        analyst_output = latest_output(state.outputs, "analyst")
        if analyst_output is None:
            return {
                "outputs": {},
                "history": [
                    NodeRun(
                        node="reporter",
                        agent="reporter",
                        round=round_,
                        status="failed",
                        span_id=new_span_id(),
                        output_ref=None,
                    )
                ],
            }
        # B1 定向改稿：返工轮(round_>=2)把上一版 draft 传进去，让 reporter 只重写
        # 被 QA 命中的 section，其余复用 → 反馈真正有抓手。首轮无 prior。
        prior_reporter = (
            latest_output(state.outputs, "reporter") if round_ >= 2 else None
        )
        prior_draft = getattr(prior_reporter, "draft", None)
        inp = build_reporter_input(
            project,
            trace_id=trace_id,
            analyst_output=analyst_output,
            qa_feedback=state.qa_feedback_by_node.get("reporter"),
            prior_draft=prior_draft,
        )
        ref = versioned_ref("reporter", round_)
        res = await run_agent_node(
            registry,
            "reporter",
            inp,
            outputs=state.outputs,
            trace_id=trace_id,
            node_id=ref,
            user_prompt_override=state.prompt_override_by_node.get("reporter"),
            timeout_ms=_NODE_TIMEOUT_MS["reporter"],
        )
        return {
            "outputs": {ref: res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="reporter",
                    agent="reporter",
                    output_ref=ref,
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
        reporter_output = latest_output(state.outputs, "reporter")
        analyst_output = latest_output(state.outputs, "analyst")
        # fail-soft:上游 reporter / analyst 任一缺失(上游失败)→ 直接 END,
        # 不构造 QAInput(否则 .draft / .result 会 AttributeError),记一条 failed
        # NodeRun 并标记 aborted,让 _run_native 投影与终态逻辑优雅收尾。
        if reporter_output is None or analyst_output is None:
            return Command(
                goto=END,
                update={
                    "outputs": {},
                    "history": [
                        NodeRun(
                            node="qa",
                            agent="qa",
                            round=round_,
                            status="failed",
                            span_id=new_span_id(),
                            output_ref=None,
                        )
                    ],
                    "aborted": True,
                    "abort_reason": "upstream output missing",
                },
            )
        inp = build_qa_input(
            project,
            trace_id=trace_id,
            reporter_output=reporter_output,
            analyst_output=analyst_output,
            outputs=state.outputs,
            prior_verdicts=list(state.verdicts),
        )
        ref = versioned_ref("qa", round_)
        res = await run_agent_node(
            registry,
            "qa",
            inp,
            outputs=state.outputs,
            trace_id=trace_id,
            node_id=ref,
            user_prompt_override=state.prompt_override_by_node.get("qa"),
            timeout_ms=_NODE_TIMEOUT_MS["qa"],
        )
        update: dict[str, Any] = {
            "outputs": {ref: res.output} if res.output is not None else {},
            "history": [
                _node_run(
                    res,
                    node="qa",
                    agent="qa",
                    output_ref=ref,
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
            prior_verdicts=list(state.verdicts),
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
