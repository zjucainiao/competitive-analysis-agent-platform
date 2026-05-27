"""Extractor 输入输出 Schema。

详细契约见 docs/AGENTS.md § 4。
"""

from __future__ import annotations

from pydantic import Field

from .agent_io import AgentInputBase, AgentOutputBase
from .competitor import CompetitorProfile
from .evidence import Evidence, RawSourceDoc


class ExtractorInput(AgentInputBase):
    product_name: str
    industry_schema_id: str = Field(
        description="industry_id + version, e.g. 'collaboration_saas_v1'",
    )
    raw_sources: list[RawSourceDoc]
    schema_fields: list[str] | None = Field(
        default=None,
        description="指定要抽取的字段路径，None=全部",
    )
    qa_feedback: dict | None = None


class ExtractorOutput(AgentOutputBase):
    profile: CompetitorProfile
    evidences: list[Evidence] = Field(default_factory=list)
    field_confidence: dict[str, float] = Field(
        default_factory=dict,
        description="字段级置信度，e.g. {'pricing.plans': 0.92}",
    )
    schema_version: str = Field(description="对应 schemas.SCHEMA_VERSION")
    unmatched_quotes: list[str] = Field(
        default_factory=list,
        description="LLM 给出的 source_quote 中匹配不上 raw_text 的部分，用于自评估",
    )
