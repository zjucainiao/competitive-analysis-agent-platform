"""RunStateView 契约 —— 原生 LangGraph 引擎 RunState 的「前端友好」投影。

Phase 2 Stage B 新增（ADDITIVE）。前端将在 Stage D 迁移到本契约，替换旧的
DAGPlan 形状 ``ProjectStateResponse``。本契约的真相源是原生引擎的 ``RunState``
（outputs dict + history list[NodeRun] + verdicts + qa_round），与 DAGPlan 无关。

形状概览::

    RunStateView
      ├─ stages: list[RunStageView]        # 5 个静态骨架阶段，始终存在
      │    ├─ instances: list[StageInstance]   # collect/extract 按产品（非产品阶段为空）
      │    └─ revisions: list[StageRevision]   # analyst/reporter/qa 按轮次（产品阶段为空）
      ├─ history: list[dict]               # NodeRun 的 dict 投影（回放真相源）
      ├─ verdicts: list[dict]              # QAVerdict 的 dict 投影
      └─ metrics: ProjectMetrics | None

token/cost/confidence/duration 由对应 ``AgentOutput``（outputs[run_ref]）派生，
缺失时为 None。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .project import ProjectMetrics

# 5 个静态阶段骨架，按流水线顺序。即便某阶段尚未执行也始终出现（前端 DAG 骨架依赖）。
STATIC_STAGES: tuple[str, ...] = ("collect", "extract", "analyst", "reporter", "qa")

# 阶段 → agent 名（填写 RunStageView.agent）
STAGE_AGENT: dict[str, str] = {
    "collect": "collector",
    "extract": "extractor",
    "analyst": "analyst",
    "reporter": "reporter",
    "qa": "qa",
}

# 按产品扇出的阶段（用 instances），其余阶段按轮次（用 revisions）。
PRODUCT_STAGES: frozenset[str] = frozenset({"collect", "extract"})


class StageInstance(BaseModel):
    """collect/extract 阶段的「单产品」执行实例（取该产品最新一轮的 NodeRun）。"""

    model_config = ConfigDict(extra="forbid")

    product: str
    status: str  # NodeRun.status: success/partial/needs_rework/failed
    revision: int = 1  # 该产品对应 NodeRun 的最新 round
    run_ref: str | None = None  # 投影节点 ID（collect.{product} 等），用于查 outputs
    span_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    confidence: float | None = None
    duration_ms: int | None = None


class StageRevision(BaseModel):
    """analyst/reporter/qa 阶段的「单轮次」执行修订（QA 返工产生多轮）。"""

    model_config = ConfigDict(extra="forbid")

    round: int = 1
    status: str
    run_ref: str | None = None  # 投影节点 ID（reporter / reporter_v2 / ...）
    span_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    confidence: float | None = None
    duration_ms: int | None = None


class RunStageView(BaseModel):
    """单个流水线阶段视图。

    - 产品阶段（collect/extract）：``instances`` 每产品一项，``revisions`` 为空。
    - 全局阶段（analyst/reporter/qa）：``revisions`` 每轮一项，``instances`` 为空。
    """

    model_config = ConfigDict(extra="forbid")

    stage: str  # collect/extract/analyst/reporter/qa
    agent: str
    instances: list[StageInstance] = Field(default_factory=list)
    revisions: list[StageRevision] = Field(default_factory=list)


class RunStateView(BaseModel):
    """原生引擎一次 run 的完整前端视图（替代 DAGPlan 形状的 ProjectStateResponse）。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_id: str | None = None
    status: str  # running/done/failed/aborted
    products: list[str] = Field(default_factory=list)
    stages: list[RunStageView] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    verdicts: list[dict[str, Any]] = Field(default_factory=list)
    # outputs：按投影节点 ID（run_ref，如 collect.Notion / reporter / reporter_v2）键的
    # AgentOutput dump。详情面板用 outputs[run_ref] 取 self_critique / draft / verdict /
    # evidences 等深内容（instances/revisions 只带 metric，不够）。键法与 projection out_map
    # 一致，前端 findLatestReporter / aggregateEvidences 可复用。
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    qa_round: int = 0
    aborted: bool = False
    abort_reason: str | None = None
    metrics: ProjectMetrics | None = None
    computed_at: str


__all__ = [
    "PRODUCT_STAGES",
    "STAGE_AGENT",
    "STATIC_STAGES",
    "RunStageView",
    "RunStateView",
    "StageInstance",
    "StageRevision",
]
