"""Pydantic schemas — the single source of truth for cross-module data.

所有跨 Agent / 跨模块的数据结构都在此目录内定义。
任何字段变更必须 bump SCHEMA_VERSION 并经过架构窗口 review。
"""

SCHEMA_VERSION = "1.2.0"
# 1.2.0: Evidence / RawSourceDoc 增加 trust_level / tainted / taint_reasons
#        （间接 prompt injection 防御，WI-1；向后兼容，旧 JSON 默认 untrusted/False/[]）
# 1.1.0: Evidence 增加可选 source_published_at（向后兼容，旧 JSON 默认 None）

# Agent IO base
from .agent_io import (
    AgentError,
    AgentInputBase,
    AgentOutputBase,
    AgentStatus,
)
from .analyst import (
    AnalysisClaim,
    AnalysisDimension,
    AnalysisResult,
    AnalystInput,
    AnalystOutput,
    DimensionAnalysis,
)

# Per-agent IO
from .collector import (
    CollectConstraints,
    CollectDimension,
    CollectorInput,
    CollectorOutput,
)

# Competitor profile (universal + extensions)
from .competitor import (
    CompetitiveAnalysis,
    CompetitorProfile,
    Feature,
    FeatureModule,
    FeatureProfile,
    FeedbackTheme,
    FieldStatus,
    FreeTrialInfo,
    Insight,
    Integration,
    PainPoint,
    PlanAvailability,
    PricingModel,
    PricingPlan,
    PricingProfile,
    ProductBasicInfo,
    SecurityProfile,
    TypicalReview,
    UserFeedbackProfile,
    UserSegment,
)
from .dag import (
    DAGEdge,
    DAGNode,
    DAGPlan,
    DAGState,
    NodeStatus,
    NodeType,
)

# Evidence & raw sources
from .evidence import (
    Evidence,
    EvidenceLocation,
    RawSourceDoc,
)
from .extractor import (
    ExtractorInput,
    ExtractorOutput,
)

# Industry extensions
from .industry import (
    CollaborationSaasExtension,
    CrmSaasExtension,
    CrossBorderEcommerceSaasExtension,
    EduSaasExtension,
    IndustryExtensionUnion,
    MaturityScore,
)
from .orchestrator import (
    NodeExecutionRequest,
    NodeExecutionResult,
)
from .project import (
    AnalysisMode,
    Project,
    ProjectMetrics,
    ProjectMetricsSnapshot,
    ProjectStatus,
    RunRef,
    RunSnapshot,
)
from .qa import (
    QADimension,
    QADimensionResult,
    QAFeedback,
    QAInput,
    QAIssue,
    QAOutput,
    QARouting,
    QAStatus,
    QAVerdict,
    validate_qa_feedback,
)
from .reporter import (
    ReportDraft,
    ReporterInput,
    ReporterOutput,
    ReportParagraph,
    ReportSection,
)
from .run_view import (
    PRODUCT_STAGES,
    STAGE_AGENT,
    STATIC_STAGES,
    RunStageView,
    RunStateView,
    StageInstance,
    StageRevision,
)

# Infrastructure
from .trace import (
    LLMCallRecord,
    ToolCallRecord,
    TraceRecord,
)
from .user import (
    User,
    UserPublic,
)

__all__ = [
    # run_view
    "PRODUCT_STAGES",
    "SCHEMA_VERSION",
    "STAGE_AGENT",
    "STATIC_STAGES",
    # agent_io
    "AgentError",
    "AgentInputBase",
    "AgentOutputBase",
    "AgentStatus",
    # analyst
    "AnalysisClaim",
    "AnalysisDimension",
    # project
    "AnalysisMode",
    "AnalysisResult",
    "AnalystInput",
    "AnalystOutput",
    # industry
    "CollaborationSaasExtension",
    # collector
    "CollectConstraints",
    "CollectDimension",
    "CollectorInput",
    "CollectorOutput",
    # competitor
    "CompetitiveAnalysis",
    "CompetitorProfile",
    "CrmSaasExtension",
    "CrossBorderEcommerceSaasExtension",
    # dag
    "DAGEdge",
    "DAGNode",
    "DAGPlan",
    "DAGState",
    "DimensionAnalysis",
    "EduSaasExtension",
    # evidence
    "Evidence",
    "EvidenceLocation",
    # extractor
    "ExtractorInput",
    "ExtractorOutput",
    "Feature",
    "FeatureModule",
    "FeatureProfile",
    "FeedbackTheme",
    "FieldStatus",
    "FreeTrialInfo",
    "IndustryExtensionUnion",
    "Insight",
    "Integration",
    # trace
    "LLMCallRecord",
    "MaturityScore",
    # orchestrator
    "NodeExecutionRequest",
    "NodeExecutionResult",
    "NodeStatus",
    "NodeType",
    "PainPoint",
    "PlanAvailability",
    "PricingModel",
    "PricingPlan",
    "PricingProfile",
    "ProductBasicInfo",
    "Project",
    "ProjectMetrics",
    "ProjectMetricsSnapshot",
    "ProjectStatus",
    # qa
    "QADimension",
    "QADimensionResult",
    "QAFeedback",
    "QAInput",
    "QAIssue",
    "QAOutput",
    "QARouting",
    "QAStatus",
    "QAVerdict",
    "RawSourceDoc",
    # reporter
    "ReportDraft",
    "ReportParagraph",
    "ReportSection",
    "ReporterInput",
    "ReporterOutput",
    "RunRef",
    "RunSnapshot",
    "RunStageView",
    "RunStateView",
    "SecurityProfile",
    "StageInstance",
    "StageRevision",
    "ToolCallRecord",
    "TraceRecord",
    "TypicalReview",
    # user / auth
    "User",
    "UserFeedbackProfile",
    "UserPublic",
    "UserSegment",
    "validate_qa_feedback",
]
