"""run_agent_node：在原生 LangGraph 节点里安全地调一个同步 agent。

负责编排层关切：resolve agent（reporter/qa 运行时注入 evidence）、重试/退避/
超时、trace contextvar、节点级 prompt override。**不复制 agent 内部自检**——
agent.invoke 自己跑 self-critique/_post_validate，这里只看 FAILED 决定重试；
超时不重试（同步 invoke 无协作式取消，重试会与僵尸线程并发烧配额），直接失败。
SUCCESS / PARTIAL / NEEDS_REWORK 都是"agent 跑完了，输出可用"，直接透传。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.orchestrator.inputs import new_span_id
from backend.schemas import AgentError, AgentOutputBase, AgentStatus, Evidence

_BACKOFF_MULT = 4


@dataclass
class AgentRunResult:
    """run_agent_node 的返回结构。

    status   — 最终 AgentStatus（FAILED 表示重试耗尽、超时或不可重试错误）。
    output   — 成功时的 AgentOutputBase；FAILED 时为 None。
    error    — 失败时的 AgentError；成功时为 None。
    attempts — 实际尝试次数（含最终失败那次）。
    span_id  — 最后一次尝试的 span_id。
    started_at / ended_at — UTC 时间戳。
    """

    status: AgentStatus
    output: AgentOutputBase | None
    error: AgentError | None
    attempts: int
    span_id: str
    started_at: datetime
    ended_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _collect_evidences(outputs: dict[str, AgentOutputBase]) -> dict[str, Evidence]:
    """从所有 extract.* outputs 中汇总 evidence_id -> Evidence 字典。

    多个 Extractor 的输出按遍历顺序后写覆盖（实际 evidence_id 跨产品全局唯一，
    不会冲突）。
    """
    from backend.orchestrator.run_state import latest_outputs

    db: dict[str, Evidence] = {}
    # 收敛到每产品最新轮,避免返工后把 v1 旧证据混入 reporter/qa 的证据库
    for nid, out in latest_outputs(outputs).items():
        if not nid.startswith("extract."):
            continue
        for ev in getattr(out, "evidences", None) or []:
            db[ev.evidence_id] = ev
    return db


def _resolve_agent(registry, agent_name: str, outputs: dict[str, AgentOutputBase]):
    """为 agent_name 选择正确的 Agent 实例。

    reporter / qa 需要把当前 outputs 里的 Evidence 汇总后注入构造期（防止
    QA fallback 到 mock fixtures、Reporter 数字校验跑空）。其他 agent 走
    registry 缓存实例。
    """
    if agent_name == "reporter":
        from backend.agents.reporter.tools import StaticEvidenceProvider

        ev_db = _collect_evidences(outputs)
        return registry.make_reporter(evidence_provider=StaticEvidenceProvider(ev_db))
    if agent_name == "qa":
        ev_db = _collect_evidences(outputs)
        return registry.make_qa(evidence_db=ev_db)
    return registry.get(agent_name)


async def run_agent_node(
    registry,
    agent_name: str,
    input_obj: Any,
    *,
    outputs: dict[str, AgentOutputBase],
    trace_id: str,
    node_id: str,
    max_retries: int = 3,
    timeout_ms: int = 60_000,
    user_prompt_override: str | None = None,
    backoff_base: float = 1.0,
) -> AgentRunResult:
    """在原生节点里安全地调一次同步 agent，带重试/退避/超时。

    Parameters
    ----------
    registry:
        提供 .get(agent_name) / .make_reporter(...) / .make_qa(...) 的 AgentRegistry。
    agent_name:
        "collector" / "extractor" / "analyst" / "reporter" / "qa" 等。
    input_obj:
        传给 agent.invoke 的输入对象（任意类型，由调用方构造）。
    outputs:
        当前运行已完成节点的 {node_id: AgentOutputBase} 映射，用于 evidence 汇总。
    trace_id:
        本次运行的全局 trace ID。
    node_id:
        当前节点 ID，注入 trace contextvar。
    max_retries:
        额外重试次数（总尝试次数 = max_retries + 1）。
    timeout_ms:
        单次调用超时，毫秒。
    user_prompt_override:
        节点级用户提示词覆盖，None 表示不覆盖。
    backoff_base:
        退避基数（秒）；测试时传 0.0 跳过真实 sleep。

    Returns
    -------
    AgentRunResult：包含最终 status、output（或 error）、尝试次数等信息。
    """
    from backend.agents._base import reset_user_prompt_override, set_user_prompt_override
    from backend.observability.llm_call_log import reset_trace_context, set_trace_context
    from backend.schemas import validate_qa_feedback

    # P2-b：qa_feedback 在 Agent input 里是裸 dict（不被 Pydantic 校验结构），
    # 在所有 agent 的统一入口对其做一次容错结构校验，畸形 payload 早 warning。
    validate_qa_feedback(getattr(input_obj, "qa_feedback", None))

    agent = _resolve_agent(registry, agent_name, outputs)
    started = _now()
    last_error: AgentError | None = None
    span_id = new_span_id()

    for attempt in range(max_retries + 1):
        span_id = new_span_id()
        ctx_token = set_trace_context(
            trace_id=trace_id,
            span_id=span_id,
            node_id=node_id,
            agent_name=agent_name,
        )
        override_token = set_user_prompt_override(user_prompt_override)
        try:
            try:
                output = await asyncio.wait_for(
                    asyncio.to_thread(
                        agent.invoke,
                        input_obj,
                        trace_id=trace_id,
                        span_id=span_id,
                        node_id=node_id,
                    ),
                    timeout=timeout_ms / 1000.0,
                )
            except TimeoutError:
                # 超时不重试：asyncio.wait_for 超时只取消这里的 await，
                # to_thread 里的同步 agent.invoke 没有协作式取消、仍会在后台
                # 线程跑完（僵尸线程）。此时若重试，会与僵尸线程并发执行同一
                # agent——重复烧 LLM 配额、trace 交叉、线程池被占满。直接判
                # 节点失败（fail-soft 链路会接住），最多只留 1 份僵尸线程。
                return AgentRunResult(
                    status=AgentStatus.FAILED,
                    output=None,
                    error=AgentError(
                        code="LLM_TIMEOUT",
                        message=(
                            f"node {node_id} timed out after {timeout_ms}ms "
                            f"(attempt {attempt + 1}; timeout is not retried)"
                        ),
                        severity="error",
                        retriable=False,
                    ),
                    attempts=attempt + 1,
                    span_id=span_id,
                    started_at=started,
                    ended_at=_now(),
                )
            except Exception as exc:
                last_error = AgentError(
                    code="UNEXPECTED",
                    message=f"{type(exc).__name__}: {exc}",
                    severity="error",
                    retriable=True,
                )
            else:
                # Agent 自带状态判断；FAILED 才决定重试
                if output.status == AgentStatus.FAILED:
                    first = output.errors[0] if output.errors else None
                    last_error = first or AgentError(
                        code="AGENT_FAILED",
                        message="agent returned FAILED status without errors",
                        severity="error",
                        retriable=True,
                    )
                    # 不可重试错误（如 INPUT_INVALID）立即终止
                    if not last_error.retriable:
                        return AgentRunResult(
                            status=AgentStatus.FAILED,
                            output=None,
                            error=last_error,
                            attempts=attempt + 1,
                            span_id=span_id,
                            started_at=started,
                            ended_at=_now(),
                        )
                else:
                    # SUCCESS / PARTIAL / NEEDS_REWORK：agent 跑完了，透传
                    return AgentRunResult(
                        status=output.status,
                        output=output,
                        error=None,
                        attempts=attempt + 1,
                        span_id=span_id,
                        started_at=started,
                        ended_at=_now(),
                    )
        finally:
            reset_user_prompt_override(override_token)
            reset_trace_context(ctx_token)

        # 还有重试次数则退避
        if attempt < max_retries:
            await asyncio.sleep(backoff_base * (_BACKOFF_MULT**attempt))

    # 全部重试耗尽
    return AgentRunResult(
        status=AgentStatus.FAILED,
        output=None,
        error=last_error,
        attempts=max_retries + 1,
        span_id=span_id,
        started_at=started,
        ended_at=_now(),
    )


__all__ = ["AgentRunResult", "run_agent_node"]
