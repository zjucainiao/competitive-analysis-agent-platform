"""Orchestrator 运行时 state schema。

``OrchestratorState`` 既是 LangGraph ``StateGraph`` 的 state，也是
``backend.storage.CheckpointerProtocol`` 的 checkpoint 载荷。一次 ``run()`` 对应
一个 LangGraph thread（``thread_id = project_id``），每轮 dispatch 后 LangGraph
自动 checkpoint。

各字段默认 reducer 为 last-write-wins：Orchestrator 的 dispatch 节点每轮返回
一份完整快照（plan / outputs / verdict_history / qa_round_count），便于回放与
人工审阅。``last_batch_results`` 字段仅承载本轮节点结果，调用方据此 yield
事件。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import (
    AgentOutputBase,
    DAGPlan,
    NodeExecutionResult,
    QAVerdict,
)


class OrchestratorState(BaseModel):
    """LangGraph StateGraph 的 state schema。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    project_id: str
    plan: DAGPlan

    # node_id -> 已完成节点的 Agent output（多态：CollectorOutput / ExtractorOutput / ...）
    outputs: dict[str, AgentOutputBase] = Field(default_factory=dict)

    # 新派生 _v{n} 节点 → 待注入的 qa_feedback 字典
    qa_feedback_by_node: dict[str, dict] = Field(default_factory=dict)

    # 每轮 QA verdict 的历史（用于 prior_verdicts + UI 回放）
    verdict_history: list[QAVerdict] = Field(default_factory=list)

    # 已完成的 QA 反馈轮次（每次成功 apply FeedbackRouter 后 +1）
    qa_round_count: int = 0

    # 本轮 dispatch 产出的节点结果；调用方读后转 yield，下一轮被覆盖
    last_batch_results: list[NodeExecutionResult] = Field(default_factory=list)

    # 流程是否被中止（QA 死循环上限 / 死锁等）
    aborted: bool = False
    abort_reason: str = ""


__all__ = ["OrchestratorState"]
