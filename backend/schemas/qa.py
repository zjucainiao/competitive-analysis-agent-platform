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
    COVERAGE_DENSITY = "coverage_density"
    # 产品身份一致性：报告引用的证据是否真的属于它标注的产品
    # （拦截「分析钉钉却引用了飞书/Slack 的内容」）。
    IDENTITY_CONSISTENCY = "identity_consistency"


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
    """统一的反馈消息，由 Orchestrator 注入到目标 Agent 的输入。

    Agent 输入 schema 里 ``qa_feedback`` 字段为 ``dict | None``（避免 schemas 间
    循环依赖），故 Pydantic 不会在 Agent input 层校验该 dict 结构。``QAFeedback``
    是该 dict 的**权威结构**：注入前一律经此模型构造 + ``model_dump``，消费侧用
    ``validate_qa_feedback`` 在边界做容错校验（见下）。
    """

    model_config = ConfigDict(extra="forbid")

    from_verdict_id: str
    issues: list[QAIssue] = Field(default_factory=list)
    instructions: str = ""
    must_address: list[str] = Field(
        default_factory=list,
        description="必须解决的 issue_id 列表",
    )
    # Reporter 据此 bump ReportDraft.version（QA mock 也按它切 fixture）。
    # 原先在 payload model_dump 后裸加一个 revision 键、绕过本 schema；现纳入模型，
    # 让整个 qa_feedback payload 都被 QAFeedback 描述/校验（修 P2-b）。
    revision: int = Field(default=0, description="当前返工轮次(供 Reporter bump 版本)")


def validate_qa_feedback(payload: "dict | None") -> None:
    """对将注入 Agent 的 ``qa_feedback`` dict 做边界结构校验（早发现坏 payload）。

    非 None 时按 ``QAFeedback`` 校验；失败只 ``log.warning`` **不抛**——fail-soft，
    不因反馈 payload 畸形而中断整条 run。正常返工路径下 payload 一律由
    ``feedback_router`` 经 ``QAFeedback`` 构造，必然通过；本校验拦截的是手搓 /
    未来新增路径产出的非法结构。
    """
    if payload is None:
        return
    try:
        QAFeedback.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "malformed qa_feedback payload (ignored, run continues): %s", exc
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
    # 上游各 Agent 的自评状态（collector/extractor 的 AgentStatus.value）。
    # 让 agent 自己发现的问题（needs_rework）不再是纯历史状态：QA 据此**加权**
    # 已有 issue（自评不达标的 agent 名下 minor → major），把自评接入判级信号
    # （不直接成回环，控制流仍集中在 QA verdict）。
    upstream_statuses: dict[str, str] = Field(
        default_factory=dict,
        description="上游 Agent 自评状态，如 {'collector':'needs_rework'}",
    )


class QAOutput(AgentOutputBase):
    verdict: QAVerdict
