"""API 层的 Pydantic 请求 / 响应模型。

复用 ``backend.schemas`` 的核心模型；API 层只新增"用户输入 → 系统补全"的
封装类型（如 ProjectCreateRequest）和聚合响应（ProjectStateResponse）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from backend.schemas import (
    AgentOutputBase,
    AnalysisDimension,
    AnalysisMode,
    CollectConstraints,
    DAGPlan,
    Project,
    QAVerdict,
)


class ProjectCreateRequest(BaseModel):
    """POST /api/projects 请求体。

    省略系统补全字段：project_id / created_at / status。

    ``analysis_mode`` 三选项：
    - ``competitive_compare``（默认）：1+ 竞品，标准对比流程
    - ``single_research``：0 竞品也允许，跳过对比维度
    - ``auto_discover``：0 竞品也允许，建议先调
      ``POST /api/discover-competitors`` 把候选竞品填回 ``competitors``
    """

    model_config = ConfigDict(extra="forbid")

    project_name: str
    # owner 不再由客户端传入：服务端从 JWT 的当前用户派生（防越权伪造归属）。
    target_product: str
    competitors: list[str]
    analysis_mode: AnalysisMode = AnalysisMode.COMPETITIVE_COMPARE
    industry: str = Field(default="collaboration_saas")
    industry_schema_version: str = "1.0.0"
    analysis_dimensions: list[AnalysisDimension] = Field(
        default_factory=lambda: [
            AnalysisDimension.FEATURE_COMPARISON,
            AnalysisDimension.PRICING_COMPARISON,
            AnalysisDimension.SWOT,
            AnalysisDimension.DIFFERENTIATION,
        ]
    )
    report_template_id: str = "standard_v1"
    target_audience: str | None = None
    # API 层只接受 real。Project schema 本体仍允许 mock/hybrid（Agent 单元测试用）。
    mode: Literal["real"] = "real"
    # 默认禁用 fallback_to_mock，保证 Collector 失败不走 mock 兜底
    collect_constraints: CollectConstraints = Field(
        default_factory=lambda: CollectConstraints(fallback_to_mock=False)
    )


class RunStartedResponse(BaseModel):
    """POST /api/projects/{id}/run 响应。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    plan_id: str
    thread_id: str
    started_at: datetime


class ProjectStateResponse(BaseModel):
    """GET /api/projects/{id}/state 响应。

    ``outputs`` 是多态 AgentOutput 字典（CollectorOutput / ExtractorOutput / ...
    都继承自 AgentOutputBase）。用 ``SerializeAsAny`` 让 Pydantic 用实际子类的
    序列化器，否则下行 JSON 只会保留基类字段，丢掉 ``result`` / ``draft`` /
    ``profile`` / ``raw_sources`` / ``verdict`` 等业务字段。
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project: Project
    plan: DAGPlan | None
    outputs: dict[str, SerializeAsAny[AgentOutputBase]] = Field(default_factory=dict)
    verdicts: list[QAVerdict] = Field(default_factory=list)


class ProjectListResponse(BaseModel):
    """GET /api/projects 响应。"""

    model_config = ConfigDict(extra="forbid")

    projects: list[Project]


__all__ = [
    "ProjectCreateRequest",
    "ProjectListResponse",
    "ProjectStateResponse",
    "RunStartedResponse",
]
