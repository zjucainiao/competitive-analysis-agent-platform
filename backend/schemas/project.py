"""Project / ProjectMetrics 数据模型。

业务指标体系详细定义见 docs/METRICS.md。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyst import AnalysisDimension
from .collector import CollectConstraints


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    RUNNING = "running"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


class ProjectMetrics(BaseModel):
    """单项目业务指标。计算公式见 docs/METRICS.md。"""

    model_config = ConfigDict(extra="forbid")

    accuracy: float = Field(default=0.0, ge=0, le=1)
    coverage: float = Field(default=0.0, ge=0, le=1)
    edit_rate: float = Field(default=0.0, ge=0, le=1)

    evidence_count: int = 0
    fields_filled_ratio: float = Field(default=0.0, ge=0, le=1)

    total_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_seconds: int = 0

    qa_round_count: int = 0

    real_fetch_count: int = 0
    mock_fetch_count: int = 0


class Project(BaseModel):
    """竞品分析项目。用户从前端创建。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    owner: str
    created_at: datetime

    target_product: str
    competitors: list[str]
    industry: str = Field(description="industry_id, e.g. 'collaboration_saas'")
    industry_schema_version: str = "1.0.0"

    analysis_dimensions: list[AnalysisDimension]
    report_template_id: str = "standard_v1"
    target_audience: str | None = None

    mode: Literal["mock", "hybrid", "real"] = "hybrid"
    collect_constraints: CollectConstraints = Field(default_factory=CollectConstraints)

    status: ProjectStatus = ProjectStatus.DRAFT
    current_report_id: str | None = None
    metrics: ProjectMetrics | None = None
