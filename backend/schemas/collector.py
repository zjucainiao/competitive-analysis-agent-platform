"""Collector 输入输出 Schema。

详细契约见 docs/AGENTS.md § 3。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .agent_io import AgentInputBase, AgentOutputBase
from .evidence import CollectDimension, RawSourceDoc


class CollectConstraints(BaseModel):
    """Collector 抓取约束。"""

    model_config = ConfigDict(extra="forbid")

    max_pages_per_dimension: int = 5
    timeout_seconds: int = 60
    respect_robots_txt: bool = True
    allow_paid_content: bool = False
    fallback_to_mock: bool = Field(
        default=True,
        description="演示用，真实抓取失败时回退 Mock 数据",
    )


class CollectorInput(AgentInputBase):
    product_name: str
    official_url: str | None = None
    industry: str = Field(description="industry_id, e.g. 'collaboration_saas'")
    dimensions: list[CollectDimension]
    constraints: CollectConstraints = Field(default_factory=CollectConstraints)

    # 重做时由 QA 反馈注入（详见 qa.py · QAFeedback）
    qa_feedback: dict | None = Field(
        default=None,
        description="QA 反馈对象的序列化，类型为 QAFeedback；用 dict 避免循环依赖",
    )


class CollectorOutput(AgentOutputBase):
    raw_sources: list[RawSourceDoc] = Field(default_factory=list)
    coverage_by_dimension: dict[CollectDimension, int] = Field(
        default_factory=dict,
        description="每个维度成功采集的页面数",
    )
