"""QA Agent 输入输出 Schema。

详细规则见 docs/AGENTS.md § 7 与 docs/QA.md。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent_io import AgentInputBase, AgentOutputBase
from .analyst import AnalysisResult
from .competitor import CompetitorProfile
from .reporter import ReportDraft


class QAStatus(str, Enum):
    PASS = "pass"
    NEEDS_REVISION = "needs_revision"
    REJECT = "reject"


class QADimension(str, Enum):
    FACT_CONSISTENCY = "fact_consistency"
    EVIDENCE_COMPLETENESS = "evidence_completeness"
    SCHEMA_COMPLETENESS = "schema_completeness"
    LOGIC_CONSISTENCY = "logic_consistency"
    FRESHNESS = "freshness"
    EXPRESSION = "expression"


class QADimensionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    dimension: QADimension
    score: float = Field(ge=0, le=1)
    # 字段名为 pass_，避免与关键字冲突；序列化时仍为 "pass"
    pass_: bool = Field(alias="pass")
    notes: str = ""


class QAIssue(BaseModel):
    """单条质检问题。必须可定位到具体位置 + 给出修复建议。"""

    model_config = ConfigDict(extra="forbid")

    issue_id: str
    dimension: QADimension
    severity: Literal["minor", "major", "critical"]
    location: str = Field(
        description="精确定位，e.g. 'report.sections[3].paragraphs[2]'",
    )
    problem: str
    suggested_fix: str
    target_agent: Literal["collector", "extractor", "analyst", "reporter"]
    required_inputs: dict = Field(
        default_factory=dict,
        description="给目标 Agent 的补充指令载荷",
    )


class QARouting(BaseModel):
    """路由决策：把控制流回到指定上游 Agent。"""

    model_config = ConfigDict(extra="forbid")

    target_agent: Literal["collector", "extractor", "analyst", "reporter"]
    reason: str
    payload: dict = Field(
        default_factory=dict,
        description="作为 qa_feedback 传给目标 Agent",
    )


class QAFeedback(BaseModel):
    """统一的反馈消息，由 Orchestrator 注入到目标 Agent 的输入。"""

    model_config = ConfigDict(extra="forbid")

    from_verdict_id: str
    issues: list[QAIssue] = Field(default_factory=list)
    instructions: str = ""
    must_address: list[str] = Field(
        default_factory=list,
        description="必须解决的 issue_id 列表",
    )


class QAVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    overall_status: QAStatus
    dimension_results: dict[QADimension, QADimensionResult] = Field(default_factory=dict)
    issues: list[QAIssue] = Field(default_factory=list)
    routing: list[QARouting] = Field(default_factory=list)
    blocking: bool = Field(description="True=必须重做才能发布，False=可发布但有改进建议")


class QAInput(AgentInputBase):
    draft: ReportDraft
    analysis: AnalysisResult
    profiles: dict[str, CompetitorProfile]
    evidence_store_handle: str | None = None
    prior_verdicts: list[QAVerdict] = Field(
        default_factory=list,
        description="历史质检结果，用于防死循环",
    )


class QAOutput(AgentOutputBase):
    verdict: QAVerdict
