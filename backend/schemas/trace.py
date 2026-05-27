"""Trace / Span / Call 数据模型。

可观测体系详细设计见 docs/OBSERVABILITY.md。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .agent_io import AgentStatus


class LLMCallRecord(BaseModel):
    """单次 LLM 调用的完整流水。BaseAgent / LLMProvider 写入。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    model: str
    system_prompt: str
    messages: list[dict]
    response: dict
    tokens_input: int = 0
    tokens_output: int = 0
    finish_reason: str | None = None
    duration_ms: int = 0
    temperature: float | None = None
    max_tokens: int | None = None
    cost_usd: float = 0.0


class ToolCallRecord(BaseModel):
    """单次工具调用流水。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    arguments: dict
    result: dict
    duration_ms: int = 0
    error: str | None = None


class TraceRecord(BaseModel):
    """单个 Span 的完整记录。

    - 一个项目从开始到结束共用一个 trace_id
    - 每次 Agent 调用一个 span_id；feedback 重做是新 span（不复用）
    - LLM / 工具调用挂在 span 下
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    agent_name: str
    agent_version: str
    node_id: str | None = None

    started_at: datetime
    ended_at: datetime | None = None
    status: AgentStatus

    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)

    input_snapshot: dict = Field(
        default_factory=dict,
        description="脱敏后的输入快照",
    )
    output_snapshot: dict = Field(
        default_factory=dict,
        description="脱敏后的输出快照",
    )

    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0

    self_critique: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)
