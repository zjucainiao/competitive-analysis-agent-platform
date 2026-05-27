"""DAG 编排数据模型。

详细设计见 docs/DAG.md。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeType(str, Enum):
    START = "start"
    END = "end"
    AGENT_CALL = "agent_call"
    PARALLEL_FORK = "parallel_fork"
    PARALLEL_JOIN = "parallel_join"
    CONDITIONAL = "conditional"
    FEEDBACK = "feedback"


class NodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_REWORK = "needs_rework"
    SKIPPED = "skipped"


class DAGNode(BaseModel):
    """DAG 中的单个节点。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    project_id: str
    node_type: NodeType
    agent_name: str | None = Field(
        default=None,
        description="AGENT_CALL 类型必填；控制节点为 None",
    )
    status: NodeStatus = NodeStatus.PENDING

    input_refs: list[str] = Field(
        default_factory=list,
        description="上游节点 id 列表",
    )
    output_ref: str | None = Field(
        default=None,
        description="输出落到哪里（PG 行 id 或对象存储路径）",
    )

    retry_count: int = 0
    max_retries: int = 3
    timeout_ms: int = 60000

    started_at: datetime | None = None
    ended_at: datetime | None = None

    # 重做时挂老节点 id
    parent_node_id: str | None = Field(
        default=None,
        description="如果是 feedback 重做产生的新节点，指向被重做的老节点",
    )
    revision: int = Field(
        default=1,
        ge=1,
        description="节点版本，feedback 重做时递增",
    )

    # 元数据
    metadata: dict = Field(default_factory=dict)


class DAGEdge(BaseModel):
    """DAG 中的单条边。"""

    model_config = ConfigDict(extra="forbid")

    edge_id: str
    from_node: str
    to_node: str
    edge_type: Literal["dependency", "feedback", "conditional"] = "dependency"
    condition: str | None = Field(
        default=None,
        description="条件表达式，仅 edge_type=conditional 时使用",
    )


class DAGPlan(BaseModel):
    """完整的 DAG 计划。可来自模板加载，也可来自 Planner LLM 自适应生成。"""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    project_id: str
    template_id: str | None = Field(
        default=None,
        description="如果是模板加载，记录 template_id；自适应生成则为 None",
    )
    nodes: list[DAGNode]
    edges: list[DAGEdge]
    rationale: str = Field(
        default="",
        description="为什么生成这个 DAG（Planner 输出）",
    )
    confidence: float = Field(default=1.0, ge=0, le=1)
    complexity_score: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="衡量任务复杂度",
    )


class DAGState(BaseModel):
    """运行时 DAG 状态。Orchestrator 用 LangGraph state schema 时引用。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    plan_id: str
    nodes: dict[str, DAGNode] = Field(default_factory=dict)
    edges: list[DAGEdge] = Field(default_factory=list)
    routing_queue: list[dict] = Field(
        default_factory=list,
        description="待处理的 QARouting 队列（dict 形式以避免循环 import）",
    )
    qa_round_count: int = Field(
        default=0,
        description="QA 循环次数，超过上限强制发布",
    )
