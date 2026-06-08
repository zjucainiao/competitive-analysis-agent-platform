"""Agent 通用输入输出基类。所有 Agent 的 *Input / *Output 都继承自这里。"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    NEEDS_REWORK = "needs_rework"
    FAILED = "failed"


class AgentError(BaseModel):
    """Agent 执行中产生的错误描述，可累积在 AgentOutputBase.errors 中。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="错误码，见 docs/AGENTS.md § 2.5")
    message: str
    severity: Literal["warn", "error", "fatal"] = "error"
    retriable: bool = True
    details: dict = Field(default_factory=dict)


class AgentInputBase(BaseModel):
    """所有 Agent 输入的基类字段。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    project_id: str
    trace_id: str
    span_id: str


class AgentOutputBase(BaseModel):
    """所有 Agent 输出的基类字段。

    子类必须额外提供业务字段，但以下字段是统一约定。
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    agent_version: str
    task_id: str
    trace_id: str
    span_id: str

    status: AgentStatus
    confidence: float = Field(ge=0, le=1, description="本次输出整体置信度")
    self_critique: str = Field(
        description="自评估文本，confidence < 0.6 时必须填具体原因",
    )

    # 度量
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0

    # 错误累积
    errors: list[AgentError] = Field(default_factory=list)

    # 输入快照（可观测）：本次 invoke 收到的输入的**紧凑摘要**（计数 + 关键名，已脱敏）。
    # 由 BaseAgent.invoke 出口统一填充（见 observability.io_snapshot.summarize_agent_input），
    # 随 outputs 流到前端，让 node detail 的「输入」区与「输出」区对称可观测。
    # 默认空 dict → 旧数据/未填充场景安全（extra="forbid" 只拒未知字段，不拒缺省字段）。
    input_snapshot: dict = Field(default_factory=dict)
