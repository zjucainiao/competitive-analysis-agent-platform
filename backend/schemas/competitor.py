"""CompetitorProfile：单个竞品的完整画像。

通用字段 + 行业扩展。所有字段强类型，禁止 dict[str, Any]。
详细字段说明见 docs/SCHEMA.md § 2。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .industry import IndustryExtensionUnion


class FieldStatus(str, Enum):
    """字段级状态。配合 CompetitorProfile.field_status 使用。"""

    VERIFIED = "verified"  # 有 evidence 支撑
    UNVERIFIED = "unverified"  # LLM 抽取但 evidence 匹配失败
    UNKNOWN = "unknown"  # 原文未提及
    CONFLICTING = "conflicting"  # 多源冲突


# ---------- Basic Info ----------


class UserSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    size_range: str | None = None
    industry: str | None = None


class ProductBasicInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    company: str | None = None
    official_website: HttpUrl | None = None
    category: str
    positioning: str | None = None
    target_users: list[UserSegment] = Field(default_factory=list)
    main_scenarios: list[str] = Field(default_factory=list)
    founded_year: int | None = None
    headquarters: str | None = None
    languages_supported: list[str] = Field(default_factory=list)

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


# ---------- Features ----------


class PlanAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    free: bool = False
    paid: bool = False
    enterprise_only: bool = False
    plan_names: list[str] = Field(default_factory=list)


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    availability: PlanAvailability = Field(default_factory=PlanAvailability)
    tags: list[str] = Field(default_factory=list)


class FeatureModule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_name: str
    features: list[str] = Field(default_factory=list)
    maturity: Literal["preview", "beta", "ga"] | None = None


class Integration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    type: Literal["native", "marketplace", "api", "webhook"]
    notes: str | None = None


class SecurityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sso_support: list[str] = Field(default_factory=list)
    audit_log: bool | None = None
    data_residency: list[str] = Field(default_factory=list)
    compliance: list[str] = Field(default_factory=list)
    permission_model: str | None = None


class FeatureProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_features: list[Feature] = Field(default_factory=list)
    feature_modules: list[FeatureModule] = Field(default_factory=list)
    differentiated_features: list[Feature] = Field(default_factory=list)
    integration_capabilities: list[Integration] = Field(default_factory=list)
    security_and_permission: SecurityProfile | None = None
    ai_capabilities: list[Feature] = Field(default_factory=list)

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


# ---------- Pricing ----------


class PricingModel(str, Enum):
    FREE = "free"
    FREEMIUM = "freemium"
    SUBSCRIPTION = "subscription"
    USAGE_BASED = "usage_based"
    HYBRID = "hybrid"
    OPEN_SOURCE = "open_source"


class FreeTrialInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    duration_days: int | None = None
    requires_credit_card: bool | None = None


class PricingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    price_per_seat_monthly_usd: float | None = None
    price_per_seat_annual_usd: float | None = None
    min_seats: int | None = None
    max_seats: int | None = Field(default=None, description="None 表示不限")
    target_segment: str | None = None
    included_features: list[str] = Field(default_factory=list)
    limits: dict[str, str] = Field(default_factory=dict)


class PricingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pricing_model: PricingModel
    plans: list[PricingPlan] = Field(default_factory=list)
    free_trial: FreeTrialInfo | None = None
    billing_cycle: list[str] = Field(default_factory=list)
    currency_supported: list[str] = Field(default_factory=list)
    enterprise_contact_required: bool = False

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


# ---------- User Feedback ----------


class FeedbackTheme(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str
    mention_count: int | None = None
    sentiment: Literal["positive", "negative", "mixed"]
    sample_quotes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PainPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pain: str
    affected_segment: str | None = None
    severity: Literal["low", "medium", "high"]
    evidence_ids: list[str] = Field(default_factory=list)


class TypicalReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    rating: float | None = None
    quote: str
    reviewer_role: str | None = None
    review_date: datetime | None = None
    evidence_id: str


class UserFeedbackProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_rating: float | None = None
    review_count: int | None = None
    review_sources: list[str] = Field(default_factory=list)

    positive_themes: list[FeedbackTheme] = Field(default_factory=list)
    negative_themes: list[FeedbackTheme] = Field(default_factory=list)
    user_pain_points: list[PainPoint] = Field(default_factory=list)
    typical_reviews: list[TypicalReview] = Field(default_factory=list)

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


# ---------- Competitive Self-Assessment ----------


class Insight(BaseModel):
    """SWOT 等 self-assessment 中的单条洞察。

    注意：这是 profile 自带的视角，真正的多产品对比由 Analyst 产出
    AnalysisResult / AnalysisClaim。
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    rationale: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class CompetitiveAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strengths: list[Insight] = Field(default_factory=list)
    weaknesses: list[Insight] = Field(default_factory=list)
    opportunities: list[Insight] = Field(default_factory=list)
    threats: list[Insight] = Field(default_factory=list)
    recommendations: list[Insight] = Field(default_factory=list)


# ---------- Top-level Profile ----------


class CompetitorProfile(BaseModel):
    """单个竞品的完整画像，由 Extractor 产出。"""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    schema_version: str = Field(description="对应 schemas.SCHEMA_VERSION")
    industry: str = Field(description="industry_id, e.g. 'collaboration_saas'")

    basic_info: ProductBasicInfo
    features: FeatureProfile = Field(default_factory=FeatureProfile)
    pricing: PricingProfile
    user_feedback: UserFeedbackProfile = Field(default_factory=UserFeedbackProfile)
    competitive: CompetitiveAnalysis = Field(default_factory=CompetitiveAnalysis)

    industry_extension: IndustryExtensionUnion | None = None

    extracted_at: datetime
    field_confidence: dict[str, float] = Field(
        default_factory=dict,
        description="字段路径 -> [0,1] 置信度",
    )
    field_status: dict[str, FieldStatus] = Field(default_factory=dict)
