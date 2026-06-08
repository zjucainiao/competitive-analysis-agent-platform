"""Reporter 输入输出 Schema。

详细契约见 docs/AGENTS.md § 6。
关键约束：每个事实性 ReportParagraph 必须有非空 evidence_ids。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent_io import AgentInputBase, AgentOutputBase
from .analyst import AnalysisResult


class ReportParagraph(BaseModel):
    """报告中的单个段落。"""

    model_config = ConfigDict(extra="forbid")

    paragraph_id: str
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(
        default_factory=list,
        description=(
            "schema 层**有意允许为空**(default=[])，便于 Reporter 分步构建草稿；"
            "‘非软结论段落必须非空’这一条件性约束**不在本模型**强制，而由 "
            "Reporter._post_validate 在输出校验阶段拒绝（见 reporter/agent.py）。"
        ),
    )
    is_quantitative: bool = Field(
        default=False,
        description="True 时含数字/价格/百分比/版本号，QA 会做更严格校验",
    )
    is_soft_conclusion: bool = Field(
        default=False,
        description="True 时允许 evidence_ids 为空（'可能'、'通常' 等模糊表述）",
    )


class ReportSection(BaseModel):
    """报告章节。"""

    model_config = ConfigDict(extra="forbid")

    section_id: str
    title: str
    order: int
    paragraphs: list[ReportParagraph] = Field(default_factory=list)


class ReportDraft(BaseModel):
    """Reporter 的核心产出。"""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    version: int = Field(
        ge=1,
        description="QA 退回重做时递增；旧版本保留供回放",
    )
    template_id: str
    sections: list[ReportSection] = Field(default_factory=list)
    summary: str
    metadata: dict = Field(
        default_factory=dict,
        description="字数 / claim 数 / evidence 数 等统计",
    )


class ReporterInput(AgentInputBase):
    project_name: str
    analysis: AnalysisResult
    template_id: str = Field(description="standard_v1 / investor_v1 / pm_v1 / ...")
    output_format: Literal["markdown", "html"] = "markdown"
    target_audience: str | None = None
    qa_feedback: dict | None = None
    prior_draft: ReportDraft | None = Field(
        default=None,
        description=(
            "返工时上一版草稿。配合 qa_feedback.must_address 做『定向改稿』："
            "只重写被 QA 命中 location 的 section，其余 section 原样复用，"
            "让反馈真正有抓手（而非整篇无状态重生成）。None → 全篇生成（首轮/兜底）。"
        ),
    )


class ReporterOutput(AgentOutputBase):
    draft: ReportDraft
