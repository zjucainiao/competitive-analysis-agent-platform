"""Pydantic schemas — the single source of truth for cross-module data.

所有跨 Agent / 跨模块的数据结构都在此目录内定义。
任何字段变更必须 bump SCHEMA_VERSION 并经过架构窗口 review。
"""

SCHEMA_VERSION = "1.1.0"
# 1.1.0: Evidence 增加可选 source_published_at（向后兼容，旧 JSON 默认 None）

# Agent IO base
from .agent_io import (
    AgentError,
    AgentInputBase,
    AgentOutputBase,
    AgentStatus,
)

# Evidence & raw sources
from .evidence import (
    Evidence,
    EvidenceLocation,
    RawSourceDoc,
)

# Competitor profile (universal + extensions)
from .competitor import (
    CompetitiveAnalysis,
    CompetitorProfile,
    Feature,
    FeatureModule,
    FeatureProfile,
    FieldStatus,
    FreeTrialInfo,
    Insight,
    Integration,
    PlanAvailability,
    PricingModel,
    PricingPlan,
    PricingProfile,
    ProductBasicInfo,
    SecurityProfile,
    UserFeedbackProfile,
    UserSegment,
    FeedbackTheme,
    PainPoint,
    TypicalReview,
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

# Per-agent IO
from .collector import (
    CollectConstraints,
    CollectDimension,
    CollectorInput,
    CollectorOutput,
)
from .extractor import (
    ExtractorInput,
    ExtractorOutput,
)
from .analyst import (
    AnalysisClaim,
    AnalysisDimension,
    AnalysisResult,
    AnalystInput,
    AnalystOutput,
    DimensionAnalysis,
)
from .reporter import (
    ReportDraft,
    ReportParagraph,
    ReportSection,
    ReporterInput,
    ReporterOutput,
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
)

# Infrastructure
from .trace import (
    LLMCallRecord,
    ToolCallRecord,
    TraceRecord,
)
from .dag import (
    DAGEdge,
    DAGNode,
    DAGPlan,
    DAGState,
    NodeStatus,
    NodeType,
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
from .orchestrator import (
    NodeExecutionRequest,
    NodeExecutionResult,
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
from .user import (
    User,
    UserPublic,
)

__all__ = [
    "SCHEMA_VERSION",
    # user / auth
    "User",
    "UserPublic",
    # agent_io
    "AgentError",
    "AgentInputBase",
    "AgentOutputBase",
    "AgentStatus",
    # evidence
    "Evidence",
    "EvidenceLocation",
    "RawSourceDoc",
    # competitor
    "CompetitiveAnalysis",
    "CompetitorProfile",
    "Feature",
    "FeatureModule",
    "FeatureProfile",
    "FeedbackTheme",
    "FieldStatus",
    "FreeTrialInfo",
    "Insight",
    "Integration",
    "PainPoint",
    "PlanAvailability",
    "PricingModel",
    "PricingPlan",
    "PricingProfile",
    "ProductBasicInfo",
    "SecurityProfile",
    "TypicalReview",
    "UserFeedbackProfile",
    "UserSegment",
    # industry
    "CollaborationSaasExtension",
    "CrmSaasExtension",
    "CrossBorderEcommerceSaasExtension",
    "EduSaasExtension",
    "IndustryExtensionUnion",
    "MaturityScore",
    # collector
    "CollectConstraints",
    "CollectDimension",
    "CollectorInput",
    "CollectorOutput",
    # extractor
    "ExtractorInput",
    "ExtractorOutput",
    # analyst
    "AnalysisClaim",
    "AnalysisDimension",
    "AnalysisResult",
    "AnalystInput",
    "AnalystOutput",
    "DimensionAnalysis",
    # reporter
    "ReportDraft",
    "ReportParagraph",
    "ReportSection",
    "ReporterInput",
    "ReporterOutput",
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
    # trace
    "LLMCallRecord",
    "ToolCallRecord",
    "TraceRecord",
    # dag
    "DAGEdge",
    "DAGNode",
    "DAGPlan",
    "DAGState",
    "NodeStatus",
    "NodeType",
    # project
    "AnalysisMode",
    "Project",
    "ProjectMetrics",
    "ProjectMetricsSnapshot",
    "ProjectStatus",
    "RunRef",
    "RunSnapshot",
    # orchestrator
    "NodeExecutionRequest",
    "NodeExecutionResult",
    # run_view
    "PRODUCT_STAGES",
    "STAGE_AGENT",
    "STATIC_STAGES",
    "RunStageView",
    "RunStateView",
    "StageInstance",
    "StageRevision",
]
