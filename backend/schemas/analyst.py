"""Analyst 输入输出 Schema。

详细契约见 docs/AGENTS.md § 5。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .agent_io import AgentInputBase, AgentOutputBase
from .competitor import CompetitorProfile


class AnalysisDimension(str, Enum):
    FEATURE_COMPARISON = "feature_comparison"
    PRICING_COMPARISON = "pricing_comparison"
    USER_FEEDBACK = "user_feedback"
    SWOT = "swot"
    DIFFERENTIATION = "differentiation_opportunities"
    POSITIONING = "positioning"


class AnalysisClaim(BaseModel):
    """单条分析结论。每条 claim 必须有 evidence_ids 支撑。"""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    products_involved: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1, description="至少 1 条支撑证据")
    confidence: float = Field(ge=0, le=1)
    counter_evidence_ids: list[str] = Field(
        default_factory=list,
        description="反例证据，体现严谨",
    )
    qualifier: str | None = Field(
        default=None,
        description="限定条件，例如 '针对中型团队场景'",
    )


class DimensionAnalysis(BaseModel):
    """单一维度的分析结果。"""

    model_config = ConfigDict(extra="forbid")

    dimension: AnalysisDimension
    summary: str
    claims: list[AnalysisClaim] = Field(default_factory=list)
    comparison_matrix: dict | None = Field(
        default=None,
        description="对比矩阵，feature/pricing 维度使用",
    )
    confidence: float = Field(ge=0, le=1)


class AnalysisResult(BaseModel):
    """Analyst 的核心产出。"""

    model_config = ConfigDict(extra="forbid")

    target_product: str
    competitors: list[str]
    dimensions: dict[AnalysisDimension, DimensionAnalysis] = Field(default_factory=dict)


class AnalystInput(AgentInputBase):
    target_product: str
    competitors: list[str]
    profiles: dict[str, CompetitorProfile] = Field(
        description="product_name -> CompetitorProfile",
    )
    dimensions: list[AnalysisDimension]
    evidence_store_handle: str | None = Field(
        default=None,
        description="供 RAG 检索 evidence 详情的句柄，I 窗口实现具体接口",
    )
    qa_feedback: dict | None = None


class AnalystOutput(AgentOutputBase):
    result: AnalysisResult
