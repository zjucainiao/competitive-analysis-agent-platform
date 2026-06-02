"""Orchestrator ↔ Agent 接口契约。

`NodeExecutionRequest` / `NodeExecutionResult` 在 docs/AGENTS.md § 8.2 中已经
冻结，是 Orchestrator 给 Agent 派活、Agent 把结果交还 Orchestrator 的载体。

storage 层的 `EventBusProtocol.publish(channel, NodeExecutionResult)`
也复用此类型作为消息载荷，因此把它放在 schemas 而不是 storage 内部。

`AgentInputBase` / `AgentOutputBase` 是多态的（Collector/Extractor/...），
本契约用基类引用 + Pydantic discriminator-less 的方式存放，序列化时由各
Agent 子类自带的 `agent_name` 字段保证可重建。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .agent_io import AgentError, AgentInputBase, AgentOutputBase
from .dag import NodeStatus


class NodeExecutionRequest(BaseModel):
    """Orchestrator 派给 Agent 的执行请求。

    `input` 是多态 AgentInputBase（CollectorInput / ExtractorInput / ...）；
    Orchestrator 不关心子类型，照 dict 透传给目标 Agent 的 `invoke()`。
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project_id: str
    task_id: str
    node_id: str
    agent_name: str
    input: AgentInputBase
    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    enqueued_at: datetime | None = None


class NodeExecutionResult(BaseModel):
    """Agent 执行完一次后回交给 Orchestrator 的结果。

    Orchestrator 据此推进 DAG（更新节点状态、判断是否触发反馈边）；
    EventBus 用它作为 publish 载荷推送到前端 WebSocket。
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project_id: str
    node_id: str
    status: NodeStatus
    output: AgentOutputBase | None = None
    error: AgentError | None = None
    next_nodes: list[str] = Field(
        default_factory=list,
        description="Orchestrator 推进 DAG 时下一批待激活节点 id",
    )

    # 透传可观测字段，前端 timeline 直接读
    trace_id: str | None = None
    span_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0

    # 自由扩展字段（如 retry 计数、降级标志）
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "NodeExecutionRequest",
    "NodeExecutionResult",
]
