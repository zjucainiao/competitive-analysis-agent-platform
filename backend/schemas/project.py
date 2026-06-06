"""Project / ProjectMetrics 数据模型。

业务指标体系详细定义见 docs/METRICS.md。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .analyst import AnalysisDimension
from .collector import CollectConstraints
from .dag import DAGPlan
from .qa import QAVerdict


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    PLANNING = "planning"
    RUNNING = "running"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AnalysisMode(str, Enum):
    """分析模式 —— wizard 第一步选择，决定 Planner DAG 形态 + Reporter 模板基调。

    - ``competitive_compare``：标准对比模式，1+ 竞品，跑全部维度
    - ``single_research``：单产品深度调研，0 竞品；Planner 跳过对比维度，
      Reporter 走 ``single_research_v1`` 模板（调研基调而非对比基调）
    - ``auto_discover``：用户只输 target_product，调用
      ``POST /api/discover-competitors`` 让 LLM 填 3-5 个常见竞品，然后退化为
      ``competitive_compare`` 流程
    """

    COMPETITIVE_COMPARE = "competitive_compare"
    SINGLE_RESEARCH = "single_research"
    AUTO_DISCOVER = "auto_discover"


class ProjectMetricsSnapshot(BaseModel):
    """一份指标快照 + 取样时间，用于时间序列。"""

    model_config = ConfigDict(extra="forbid")

    captured_at: datetime
    metrics: "ProjectMetrics"


class RunRef(BaseModel):
    """一次 run 的元数据。v1 不存历史 outputs（covered by latest），但 metadata 给
    前端展示「该项目跑过 N 次」时间线。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    plan_id: str
    started_at: datetime
    ended_at: datetime | None = None
    final_status: str | None = None  # "done" / "failed" / "stopped"


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

    # 用户在 WebUI 里 PATCH 段落的累计次数。Orchestrator 一次性算指标后此字段
    # 由 PATCH /api/projects/{id}/.../paragraphs/{pid} 增量更新（不重算其他字段）。
    manual_edits: int = 0


class Project(BaseModel):
    """竞品分析项目。用户从前端创建。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    owner: str
    created_at: datetime

    target_product: str
    competitors: list[str]
    # 分析模式 —— 决定 Planner DAG 形态 + Reporter 模板基调 + Analyst 启发式分支。
    # 默认 competitive_compare 保持向后兼容（已有 fixtures / Project JSON 不需要补字段）。
    analysis_mode: AnalysisMode = AnalysisMode.COMPETITIVE_COMPARE
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

    # 每次 run 终态时追加一份 metrics 快照，前端用作 sparkline 时间序列源。
    # 与 metrics（latest）配套：metrics 总是 metrics_history[-1]
    metrics_history: list[ProjectMetricsSnapshot] = Field(default_factory=list)

    # 多次 run 的 metadata 列表（v1 只存 ref，完整每次 outputs 历史等 storage 改造）
    runs: list[RunRef] = Field(default_factory=list)

    # 软删 / 归档：archived_at 非 None 即视作进入回收站；30 天后真删（外部 cron 实施）
    archived_at: datetime | None = None
    deleted_at: datetime | None = None


class RunSnapshot(BaseModel):
    """单次 run 的完整不可变快照。

    Orchestrator 在 run 终态时由 ``state_store.save_run_snapshot`` 持久化。
    与 ``Project.runs[]`` 中的 ``RunRef`` 配套（RunRef 是 metadata，RunSnapshot 是
    完整 state）。

    polymorphism：``outputs`` 是 ``dict[node_id -> dump_output(AgentOutputBase) 产出的 dict]``，
    读出后用 ``storage.serde.load_output`` 还原成具体子类。
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_id: str
    captured_at: datetime
    plan: DAGPlan
    outputs: dict[str, dict[str, Any]]
    verdicts: list[QAVerdict]
    metrics: "ProjectMetrics | None" = None
    final_status: str
    # native 引擎 RunState.history 的 dict 投影(回放真相源)。Stage A 仅占位默认空,
    # 端到端写入(RunStateView)由 Stage B 落地;此处保持向后兼容(已有快照无此字段)。
    history: list[dict[str, Any]] = Field(default_factory=list)


# Pydantic 前向引用：ProjectMetricsSnapshot.metrics / RunSnapshot.metrics 引用了下面定义的 ProjectMetrics
ProjectMetricsSnapshot.model_rebuild()
RunSnapshot.model_rebuild()
