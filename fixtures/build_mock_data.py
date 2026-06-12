"""构建 fixtures/mock_data/ 下的全套 JSON fixture。

运行:
    python -m fixtures.build_mock_data

特性:
    - 全部数据通过 Pydantic 实例化，保证 100% 符合当前 Schema
    - Schema 变更时重跑本脚本即可重建 fixture
    - 演示场景：协作办公 SaaS · Notion vs ClickUp vs Asana

输出到 fixtures/mock_data/ 下，供各 Agent 离线开发 / 测试使用。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.schemas import (
    SCHEMA_VERSION,
    AgentStatus,
    AnalysisClaim,
    AnalysisDimension,
    AnalysisResult,
    AnalystOutput,
    CollaborationSaasExtension,
    CollectConstraints,
    CollectDimension,
    CollectorOutput,
    CompetitiveAnalysis,
    CompetitorProfile,
    DimensionAnalysis,
    Evidence,
    EvidenceLocation,
    ExtractorOutput,
    Feature,
    FeatureProfile,
    FeedbackTheme,
    FieldStatus,
    Insight,
    MaturityScore,
    PainPoint,
    PlanAvailability,
    PricingModel,
    PricingPlan,
    PricingProfile,
    ProductBasicInfo,
    Project,
    ProjectStatus,
    QADimension,
    QADimensionResult,
    QAIssue,
    QAOutput,
    QARouting,
    QAStatus,
    QAVerdict,
    RawSourceDoc,
    ReportDraft,
    ReporterOutput,
    ReportParagraph,
    ReportSection,
    TypicalReview,
    UserFeedbackProfile,
    UserSegment,
)

ROOT = Path(__file__).parent / "mock_data"


def utc(year: int = 2026, month: int = 5, day: int = 27, hour: int = 14) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


# =============================================================================
# Evidence DB
# =============================================================================

EVIDENCES: list[Evidence] = [
    # ----- Notion -----
    Evidence(
        evidence_id="ev_notion_home_01",
        source_id="src_notion_homepage",
        product_name="Notion",
        source_url="https://www.notion.so/",
        source_type="homepage",
        source_authority=0.95,
        content=(
            "Notion is the connected workspace where better, faster work happens. "
            "Bring teams, projects, and tools together."
        ),
        content_hash="hash_notion_home_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.95,
        tags=["positioning", "homepage"],
    ),
    Evidence(
        evidence_id="ev_notion_price_01",
        source_id="src_notion_pricing",
        product_name="Notion",
        source_url="https://www.notion.so/pricing",
        source_type="pricing_page",
        source_authority=0.95,
        content=(
            "Notion offers four plans: Free, Plus at $10 per seat/month, "
            "Business at $15 per seat/month, and Enterprise (contact sales)."
        ),
        content_hash="hash_notion_price_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.93,
        tags=["pricing"],
    ),
    Evidence(
        evidence_id="ev_notion_price_02",
        source_id="src_notion_pricing",
        product_name="Notion",
        source_url="https://www.notion.so/pricing",
        source_type="pricing_page",
        source_authority=0.95,
        content=(
            "Plus plan includes unlimited blocks for teams, unlimited file uploads, "
            "and 30 day page history."
        ),
        content_hash="hash_notion_price_02",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.9,
        tags=["pricing", "feature"],
    ),
    Evidence(
        evidence_id="ev_notion_feature_01",
        source_id="src_notion_homepage",
        product_name="Notion",
        source_url="https://www.notion.so/",
        source_type="homepage",
        source_authority=0.95,
        content=(
            "Notion AI helps you write, summarize, and brainstorm directly in your docs."
        ),
        content_hash="hash_notion_feature_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.92,
        tags=["feature", "ai"],
    ),
    # ----- ClickUp -----
    Evidence(
        evidence_id="ev_clickup_home_01",
        source_id="src_clickup_homepage",
        product_name="ClickUp",
        source_url="https://clickup.com/",
        source_type="homepage",
        source_authority=0.95,
        content=(
            "ClickUp is one app to replace them all: tasks, docs, goals, chat — "
            "everything teams need to work in one place."
        ),
        content_hash="hash_clickup_home_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.94,
        tags=["positioning"],
    ),
    Evidence(
        evidence_id="ev_clickup_price_01",
        source_id="src_clickup_pricing",
        product_name="ClickUp",
        source_url="https://clickup.com/pricing",
        source_type="pricing_page",
        source_authority=0.95,
        content=(
            "ClickUp provides Free Forever, Unlimited at $7 per user/month, "
            "Business at $12 per user/month, and Enterprise plans."
        ),
        content_hash="hash_clickup_price_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.94,
        tags=["pricing"],
    ),
    Evidence(
        evidence_id="ev_clickup_feature_01",
        source_id="src_clickup_homepage",
        product_name="ClickUp",
        source_url="https://clickup.com/features/automations",
        source_type="features_page",
        source_authority=0.95,
        content=(
            "ClickUp Automations include 100+ pre-built automations and the ability "
            "to build custom workflow triggers across tasks, docs, and integrations."
        ),
        content_hash="hash_clickup_feature_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.91,
        tags=["feature", "automation"],
    ),
    # ----- Asana -----
    Evidence(
        evidence_id="ev_asana_home_01",
        source_id="src_asana_homepage",
        product_name="Asana",
        source_url="https://asana.com/",
        source_type="homepage",
        source_authority=0.95,
        content=(
            "Asana helps teams orchestrate their work — from daily tasks to "
            "strategic cross-functional initiatives."
        ),
        content_hash="hash_asana_home_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.94,
        tags=["positioning"],
    ),
    Evidence(
        evidence_id="ev_asana_price_01",
        source_id="src_asana_pricing",
        product_name="Asana",
        source_url="https://asana.com/pricing",
        source_type="pricing_page",
        source_authority=0.95,
        content=(
            "Asana plans: Personal (free), Starter at $10.99 per user/month, "
            "Advanced at $24.99 per user/month, plus Enterprise tiers."
        ),
        content_hash="hash_asana_price_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.92,
        tags=["pricing"],
    ),
    Evidence(
        evidence_id="ev_asana_review_01",
        source_id="src_asana_reviews",
        product_name="Asana",
        source_url="https://www.g2.com/products/asana/reviews",
        source_type="user_review",
        source_authority=0.75,
        content=(
            "Reviewers consistently praise Asana for its visual project tracking "
            "across boards, lists, and timelines, calling it best-in-class for "
            "cross-functional project coordination."
        ),
        content_hash="hash_asana_review_01",
        language="en",
        collected_at=utc(),
        extracted_at=utc(),
        confidence=0.85,
        tags=["user_review"],
    ),
]


# =============================================================================
# RawSourceDoc (Collector outputs)
# =============================================================================


def _mk_raw(
    product: str,
    source_id: str,
    dimension: CollectDimension,
    url: str,
    title: str,
    text: str,
    authority: float = 0.95,
) -> RawSourceDoc:
    return RawSourceDoc(
        source_id=source_id,
        product_name=product,
        dimension=dimension,
        source_url=url,
        source_type="html",
        title=title,
        raw_html=None,
        raw_text=text,
        summary=text[:120],
        language="en",
        collected_at=utc(),
        fetch_method="firecrawl",
        http_status=200,
        robots_allowed=True,
        source_authority=authority,
    )


RAW_SOURCES: dict[str, list[RawSourceDoc]] = {
    "notion": [
        _mk_raw(
            "Notion",
            "src_notion_homepage",
            CollectDimension.HOMEPAGE,
            "https://www.notion.so/",
            "Notion – The connected workspace",
            (
                "Notion is the connected workspace where better, faster work happens. "
                "Bring teams, projects, and tools together. Notion AI helps you write, "
                "summarize, and brainstorm directly in your docs."
            ),
        ),
        _mk_raw(
            "Notion",
            "src_notion_pricing",
            CollectDimension.PRICING,
            "https://www.notion.so/pricing",
            "Notion · Pricing",
            (
                "Notion offers four plans: Free, Plus at $10 per seat/month, "
                "Business at $15 per seat/month, and Enterprise (contact sales). "
                "Plus plan includes unlimited blocks for teams, unlimited file uploads, "
                "and 30 day page history."
            ),
        ),
    ],
    "clickup": [
        _mk_raw(
            "ClickUp",
            "src_clickup_homepage",
            CollectDimension.HOMEPAGE,
            "https://clickup.com/",
            "ClickUp – One app to replace them all",
            (
                "ClickUp is one app to replace them all: tasks, docs, goals, chat — "
                "everything teams need to work in one place."
            ),
        ),
        _mk_raw(
            "ClickUp",
            "src_clickup_pricing",
            CollectDimension.PRICING,
            "https://clickup.com/pricing",
            "ClickUp · Pricing",
            (
                "ClickUp provides Free Forever, Unlimited at $7 per user/month, "
                "Business at $12 per user/month, and Enterprise plans. "
                "ClickUp Automations include 100+ pre-built automations and the ability "
                "to build custom workflow triggers across tasks, docs, and integrations."
            ),
        ),
    ],
    "asana": [
        _mk_raw(
            "Asana",
            "src_asana_homepage",
            CollectDimension.HOMEPAGE,
            "https://asana.com/",
            "Asana – Manage your team's work, projects, & tasks online",
            (
                "Asana helps teams orchestrate their work — from daily tasks to "
                "strategic cross-functional initiatives."
            ),
        ),
        _mk_raw(
            "Asana",
            "src_asana_pricing",
            CollectDimension.PRICING,
            "https://asana.com/pricing",
            "Asana · Pricing",
            (
                "Asana plans: Personal (free), Starter at $10.99 per user/month, "
                "Advanced at $24.99 per user/month, plus Enterprise tiers."
            ),
        ),
        _mk_raw(
            "Asana",
            "src_asana_reviews",
            CollectDimension.REVIEWS,
            "https://www.g2.com/products/asana/reviews",
            "Asana reviews on G2",
            (
                "Reviewers consistently praise Asana for its visual project tracking "
                "across boards, lists, and timelines, calling it best-in-class for "
                "cross-functional project coordination."
            ),
            authority=0.75,
        ),
    ],
}


# =============================================================================
# CompetitorProfile (Extractor outputs)
# =============================================================================


def _basic(name: str, company: str, website: str, positioning: str) -> ProductBasicInfo:
    return ProductBasicInfo(
        name=name,
        company=company,
        official_website=website,
        category="协作办公 / 项目管理 SaaS",
        positioning=positioning,
        target_users=[
            UserSegment(name="中小企业团队", size_range="10-200"),
            UserSegment(name="产品 / 项目团队", size_range="5-100"),
        ],
        main_scenarios=["项目管理", "文档协作", "知识管理"],
        languages_supported=["en", "zh", "ja", "de", "fr"],
        evidence_refs={
            "positioning": [f"ev_{name.lower()}_home_01"],
        },
    )


PROFILE_NOTION = CompetitorProfile(
    profile_id="profile_notion",
    schema_version=SCHEMA_VERSION,
    industry="collaboration_saas",
    basic_info=_basic(
        "Notion",
        "Notion Labs, Inc.",
        "https://www.notion.so/",
        "All-in-one workspace for notes, docs, projects and knowledge management.",
    ),
    features=FeatureProfile(
        core_features=[
            Feature(
                name="文档",
                description="块级文档编辑",
                availability=PlanAvailability(
                    free=True, paid=True, plan_names=["Free", "Plus", "Business"]
                ),
                tags=["doc"],
            ),
            Feature(
                name="数据库",
                description="可关联的结构化数据库视图",
                availability=PlanAvailability(
                    free=True, paid=True, plan_names=["Free", "Plus", "Business"]
                ),
                tags=["database", "view"],
            ),
        ],
        ai_capabilities=[
            Feature(
                name="Notion AI",
                description="文档内 AI 写作、摘要、头脑风暴",
                availability=PlanAvailability(paid=True, plan_names=["Plus", "Business"]),
                tags=["ai"],
            ),
        ],
        evidence_refs={
            "core_features": ["ev_notion_home_01"],
            "ai_capabilities": ["ev_notion_feature_01"],
        },
    ),
    pricing=PricingProfile(
        pricing_model=PricingModel.FREEMIUM,
        plans=[
            PricingPlan(
                name="Free",
                price_per_seat_monthly_usd=0,
                target_segment="个人 / 小团队",
            ),
            PricingPlan(
                name="Plus",
                price_per_seat_monthly_usd=10,
                target_segment="小团队",
                included_features=["unlimited blocks", "30 day page history"],
            ),
            PricingPlan(
                name="Business",
                price_per_seat_monthly_usd=15,
                target_segment="中型团队",
            ),
            PricingPlan(
                name="Enterprise",
                target_segment="企业",
            ),
        ],
        billing_cycle=["monthly", "annual"],
        currency_supported=["USD"],
        enterprise_contact_required=True,
        evidence_refs={
            "plans": ["ev_notion_price_01", "ev_notion_price_02"],
        },
    ),
    user_feedback=UserFeedbackProfile(
        overall_rating=4.7,
        review_count=5200,
        review_sources=["G2", "Capterra"],
        positive_themes=[
            FeedbackTheme(
                theme="灵活度高",
                sentiment="positive",
                sample_quotes=["几乎可以拼出任何工作流"],
                evidence_ids=["ev_notion_home_01"],
            ),
        ],
    ),
    competitive=CompetitiveAnalysis(
        strengths=[
            Insight(
                text="灵活的块编辑 + 数据库组合，可塑造多种工作流",
                evidence_ids=["ev_notion_home_01"],
                confidence=0.9,
            ),
        ],
    ),
    industry_extension=CollaborationSaasExtension(
        document_collaboration=MaturityScore(
            has_capability=True,
            maturity_level="best_in_class",
            notes="块级编辑 + 多人实时协作",
            evidence_ids=["ev_notion_home_01"],
        ),
        knowledge_base=MaturityScore(
            has_capability=True,
            maturity_level="advanced",
        ),
        ai_assistance=MaturityScore(
            has_capability=True,
            maturity_level="advanced",
            notes="Notion AI 内嵌写作与摘要",
            evidence_ids=["ev_notion_feature_01"],
        ),
        task_management=MaturityScore(
            has_capability=True,
            maturity_level="standard",
            notes="基础任务管理，复杂项目场景能力一般",
        ),
        workflow_automation=MaturityScore(
            has_capability=True,
            maturity_level="basic",
        ),
    ),
    extracted_at=utc(),
    field_confidence={
        "basic_info.positioning": 0.92,
        "pricing.plans": 0.93,
        "features.ai_capabilities": 0.9,
    },
    field_status={
        "basic_info.positioning": FieldStatus.VERIFIED,
        "pricing.plans": FieldStatus.VERIFIED,
        "user_feedback.overall_rating": FieldStatus.UNVERIFIED,
    },
)


PROFILE_CLICKUP = CompetitorProfile(
    profile_id="profile_clickup",
    schema_version=SCHEMA_VERSION,
    industry="collaboration_saas",
    basic_info=_basic(
        "ClickUp",
        "ClickUp, Inc.",
        "https://clickup.com/",
        "One app to replace them all: tasks, docs, goals, chat in one place.",
    ),
    features=FeatureProfile(
        core_features=[
            Feature(
                name="任务管理",
                description="多视图任务管理（List/Board/Gantt/Timeline）",
                availability=PlanAvailability(free=True, paid=True),
                tags=["task"],
            ),
            Feature(
                name="自动化",
                description="100+ 预制自动化 + 自定义工作流触发器",
                availability=PlanAvailability(
                    paid=True, plan_names=["Unlimited", "Business"]
                ),
                tags=["automation"],
            ),
        ],
        evidence_refs={
            "core_features": ["ev_clickup_feature_01"],
        },
    ),
    pricing=PricingProfile(
        pricing_model=PricingModel.FREEMIUM,
        plans=[
            PricingPlan(
                name="Free Forever",
                price_per_seat_monthly_usd=0,
                target_segment="个人 / 试用",
            ),
            PricingPlan(
                name="Unlimited",
                price_per_seat_monthly_usd=7,
                target_segment="小团队",
            ),
            PricingPlan(
                name="Business",
                price_per_seat_monthly_usd=12,
                target_segment="中型团队",
            ),
            PricingPlan(
                name="Enterprise",
                target_segment="企业",
            ),
        ],
        billing_cycle=["monthly", "annual"],
        currency_supported=["USD"],
        enterprise_contact_required=True,
        evidence_refs={
            "plans": ["ev_clickup_price_01"],
        },
    ),
    competitive=CompetitiveAnalysis(
        strengths=[
            Insight(
                text="价格相对低，自动化能力丰富",
                evidence_ids=["ev_clickup_price_01", "ev_clickup_feature_01"],
                confidence=0.88,
            ),
        ],
    ),
    industry_extension=CollaborationSaasExtension(
        task_management=MaturityScore(
            has_capability=True,
            maturity_level="best_in_class",
        ),
        workflow_automation=MaturityScore(
            has_capability=True,
            maturity_level="advanced",
            evidence_ids=["ev_clickup_feature_01"],
        ),
        document_collaboration=MaturityScore(
            has_capability=True,
            maturity_level="standard",
        ),
    ),
    extracted_at=utc(),
    field_confidence={
        "pricing.plans": 0.94,
        "industry_extension.workflow_automation": 0.91,
    },
)


PROFILE_ASANA = CompetitorProfile(
    profile_id="profile_asana",
    schema_version=SCHEMA_VERSION,
    industry="collaboration_saas",
    basic_info=_basic(
        "Asana",
        "Asana, Inc.",
        "https://asana.com/",
        "Manage work and projects across teams from daily tasks to strategic initiatives.",
    ),
    features=FeatureProfile(
        core_features=[
            Feature(
                name="项目跨视图",
                description="List / Board / Timeline / Calendar 视图",
                availability=PlanAvailability(free=True, paid=True),
                tags=["view"],
            ),
        ],
    ),
    pricing=PricingProfile(
        pricing_model=PricingModel.FREEMIUM,
        plans=[
            PricingPlan(
                name="Personal",
                price_per_seat_monthly_usd=0,
            ),
            PricingPlan(
                name="Starter",
                price_per_seat_monthly_usd=10.99,
            ),
            PricingPlan(
                name="Advanced",
                price_per_seat_monthly_usd=24.99,
            ),
            PricingPlan(
                name="Enterprise",
            ),
        ],
        billing_cycle=["monthly", "annual"],
        currency_supported=["USD"],
        enterprise_contact_required=True,
        evidence_refs={
            "plans": ["ev_asana_price_01"],
        },
    ),
    user_feedback=UserFeedbackProfile(
        overall_rating=4.4,
        review_count=9800,
        review_sources=["G2"],
        positive_themes=[
            FeedbackTheme(
                theme="可视化项目跟踪",
                sentiment="positive",
                sample_quotes=["跨职能项目协调最强之一"],
                evidence_ids=["ev_asana_review_01"],
            ),
        ],
        user_pain_points=[
            PainPoint(
                pain="非营利 / 教育版本对个体用户限制较多",
                severity="medium",
                evidence_ids=[],
            ),
        ],
        typical_reviews=[
            TypicalReview(
                source="G2",
                rating=4.5,
                quote=(
                    "Asana is best-in-class for cross-functional project coordination "
                    "with strong visual tracking."
                ),
                evidence_id="ev_asana_review_01",
            ),
        ],
    ),
    industry_extension=CollaborationSaasExtension(
        task_management=MaturityScore(
            has_capability=True,
            maturity_level="best_in_class",
            evidence_ids=["ev_asana_review_01"],
        ),
        kanban_view=MaturityScore(
            has_capability=True,
            maturity_level="advanced",
        ),
        document_collaboration=MaturityScore(
            has_capability=True,
            maturity_level="basic",
        ),
    ),
    extracted_at=utc(),
)


PROFILES = {
    "Notion": PROFILE_NOTION,
    "ClickUp": PROFILE_CLICKUP,
    "Asana": PROFILE_ASANA,
}


# =============================================================================
# AnalysisResult (Analyst output)
# =============================================================================


ANALYSIS = AnalysisResult(
    target_product="Notion",
    competitors=["ClickUp", "Asana"],
    dimensions={
        AnalysisDimension.FEATURE_COMPARISON: DimensionAnalysis(
            dimension=AnalysisDimension.FEATURE_COMPARISON,
            summary=(
                "Notion 以文档+数据库灵活组合见长；ClickUp 在任务管理与自动化能力上"
                "更强；Asana 在视觉化项目跟踪和跨职能协调上口碑领先。"
            ),
            claims=[
                AnalysisClaim(
                    claim_id="cl_feat_001",
                    text="Notion 在文档协作与知识管理场景的灵活度领先。",
                    products_involved=["Notion", "ClickUp", "Asana"],
                    evidence_ids=["ev_notion_home_01", "ev_notion_feature_01"],
                    confidence=0.88,
                    qualifier="文档为核心的工作流",
                ),
                AnalysisClaim(
                    claim_id="cl_feat_002",
                    text="ClickUp 在自动化能力上明显强于 Notion，"
                    "适合需要复杂跨任务工作流的中型团队。",
                    products_involved=["Notion", "ClickUp"],
                    evidence_ids=["ev_clickup_feature_01"],
                    counter_evidence_ids=["ev_notion_feature_01"],
                    confidence=0.84,
                ),
                AnalysisClaim(
                    claim_id="cl_feat_003",
                    text="Asana 的多视图项目跟踪能力被用户公认为业内领先之一。",
                    products_involved=["Asana"],
                    evidence_ids=["ev_asana_review_01"],
                    confidence=0.86,
                ),
            ],
            confidence=0.85,
        ),
        AnalysisDimension.PRICING_COMPARISON: DimensionAnalysis(
            dimension=AnalysisDimension.PRICING_COMPARISON,
            summary=(
                "三者均采用 Freemium。ClickUp 入门档最便宜（$7），Notion 居中"
                "（Plus $10），Asana 较高（Starter $10.99，Advanced $24.99）。"
            ),
            claims=[
                AnalysisClaim(
                    claim_id="cl_price_001",
                    text="ClickUp Unlimited 档 $7/seat/月，是三者中入门档最低价。",
                    products_involved=["Notion", "ClickUp", "Asana"],
                    evidence_ids=[
                        "ev_clickup_price_01",
                        "ev_notion_price_01",
                        "ev_asana_price_01",
                    ],
                    confidence=0.95,
                ),
                AnalysisClaim(
                    claim_id="cl_price_002",
                    text="Asana Advanced 档 $24.99/seat/月，相较 Notion Business "
                    "（$15）与 ClickUp Business（$12）溢价明显。",
                    products_involved=["Notion", "ClickUp", "Asana"],
                    evidence_ids=[
                        "ev_asana_price_01",
                        "ev_notion_price_01",
                        "ev_clickup_price_01",
                    ],
                    confidence=0.92,
                ),
            ],
            comparison_matrix={
                "entry_paid_usd": {"Notion": 10, "ClickUp": 7, "Asana": 10.99},
                "advanced_paid_usd": {"Notion": 15, "ClickUp": 12, "Asana": 24.99},
            },
            confidence=0.93,
        ),
        AnalysisDimension.SWOT: DimensionAnalysis(
            dimension=AnalysisDimension.SWOT,
            summary="围绕 Notion 视角的 SWOT。",
            claims=[
                AnalysisClaim(
                    claim_id="cl_swot_001",
                    text="Notion 优势：文档+数据库灵活组合，AI 能力内嵌。",
                    products_involved=["Notion"],
                    evidence_ids=["ev_notion_home_01", "ev_notion_feature_01"],
                    confidence=0.87,
                ),
                AnalysisClaim(
                    claim_id="cl_swot_002",
                    text="Notion 劣势：复杂项目管理与工作流自动化能力弱于专业 PM 工具。",
                    products_involved=["Notion", "ClickUp", "Asana"],
                    evidence_ids=["ev_clickup_feature_01", "ev_asana_review_01"],
                    confidence=0.8,
                ),
            ],
            confidence=0.82,
        ),
    },
)


# =============================================================================
# Report Draft (Reporter output)
# =============================================================================


DRAFT = ReportDraft(
    report_id="rep_collab_demo_v1",
    version=1,
    template_id="standard_v1",
    summary=(
        "Notion vs ClickUp vs Asana 协作办公场景对比。Notion 在文档与 AI 能力上领先，"
        "ClickUp 在自动化与价格上有优势，Asana 在视觉化项目跟踪上口碑突出。"
    ),
    sections=[
        ReportSection(
            section_id="sec_overview",
            title="1. 竞品概览",
            order=1,
            paragraphs=[
                ReportParagraph(
                    paragraph_id="p_ov_01",
                    text=(
                        "本次对比聚焦 Notion、ClickUp、Asana 三款主流协作办公 SaaS，"
                        "覆盖核心定位、目标用户、定价模型与差异化能力。"
                    ),
                    claim_ids=[],
                    evidence_ids=[],
                    is_soft_conclusion=True,
                ),
            ],
        ),
        ReportSection(
            section_id="sec_features",
            title="2. 核心功能对比",
            order=2,
            paragraphs=[
                ReportParagraph(
                    paragraph_id="p_fe_01",
                    text=(
                        "Notion 以文档+数据库灵活组合见长，AI 能力内嵌于编辑器，"
                        "适合知识密集型工作流。"
                    ),
                    claim_ids=["cl_feat_001"],
                    evidence_ids=["ev_notion_home_01", "ev_notion_feature_01"],
                ),
                ReportParagraph(
                    paragraph_id="p_fe_02",
                    text=(
                        "ClickUp 在自动化能力上明显强于 Notion，提供 100+ 预制"
                        "自动化与自定义触发器，适合复杂跨任务工作流。"
                    ),
                    claim_ids=["cl_feat_002"],
                    evidence_ids=["ev_clickup_feature_01"],
                ),
                ReportParagraph(
                    paragraph_id="p_fe_03",
                    text=(
                        "Asana 的多视图项目跟踪能力被用户公认为业内领先之一，"
                        "尤其适用于跨职能项目协调。"
                    ),
                    claim_ids=["cl_feat_003"],
                    evidence_ids=["ev_asana_review_01"],
                ),
            ],
        ),
        ReportSection(
            section_id="sec_pricing",
            title="3. 定价策略对比",
            order=3,
            paragraphs=[
                ReportParagraph(
                    paragraph_id="p_pr_01",
                    text=(
                        "三者均采用 Freemium 模式。ClickUp Unlimited 档 $7/seat/月，"
                        "是三者中入门档最低价；Notion Plus $10/seat/月，"
                        "Asana Starter $10.99/seat/月。"
                    ),
                    claim_ids=["cl_price_001"],
                    evidence_ids=[
                        "ev_clickup_price_01",
                        "ev_notion_price_01",
                        "ev_asana_price_01",
                    ],
                    is_quantitative=True,
                ),
                ReportParagraph(
                    paragraph_id="p_pr_02",
                    text=(
                        "Asana Advanced 档 $24.99/seat/月，相较 Notion Business "
                        "$15 与 ClickUp Business $12，溢价明显，"
                        "更适合预算充足的中大型团队。"
                    ),
                    claim_ids=["cl_price_002"],
                    evidence_ids=[
                        "ev_asana_price_01",
                        "ev_notion_price_01",
                        "ev_clickup_price_01",
                    ],
                    is_quantitative=True,
                ),
            ],
        ),
        ReportSection(
            section_id="sec_swot",
            title="4. SWOT（以 Notion 为视角）",
            order=4,
            paragraphs=[
                ReportParagraph(
                    paragraph_id="p_sw_01",
                    text="优势：文档+数据库灵活组合，AI 能力内嵌于编辑器。",
                    claim_ids=["cl_swot_001"],
                    evidence_ids=["ev_notion_home_01", "ev_notion_feature_01"],
                ),
                ReportParagraph(
                    paragraph_id="p_sw_02",
                    text=(
                        "劣势：复杂项目管理与工作流自动化能力相对薄弱，"
                        "在面对深度自动化与可视化项目跟踪需求时不如专业 PM 工具。"
                    ),
                    claim_ids=["cl_swot_002"],
                    evidence_ids=["ev_clickup_feature_01", "ev_asana_review_01"],
                ),
            ],
        ),
    ],
    metadata={
        "word_count": 360,
        "claim_count": 7,
        "evidence_count": 10,
    },
)


# =============================================================================
# QA Verdicts
# =============================================================================


QA_PASS = QAVerdict(
    verdict_id="qa_pass_001",
    overall_status=QAStatus.PASS,
    dimension_results={
        QADimension.FACT_CONSISTENCY: QADimensionResult(
            dimension=QADimension.FACT_CONSISTENCY, score=0.97,
            **{"pass": True},  # type: ignore[arg-type]
            notes="所有事实性段落均被 evidence 蕴含。",
        ),
        QADimension.EVIDENCE_COMPLETENESS: QADimensionResult(
            dimension=QADimension.EVIDENCE_COMPLETENESS, score=0.94,
            **{"pass": True},
            notes="关键段落 evidence 覆盖率 ≥ 0.9。",
        ),
        QADimension.SCHEMA_COMPLETENESS: QADimensionResult(
            dimension=QADimension.SCHEMA_COMPLETENESS, score=0.88,
            **{"pass": True},
            notes="3 个 Profile 字段填充率均 ≥ 0.8。",
        ),
        QADimension.LOGIC_CONSISTENCY: QADimensionResult(
            dimension=QADimension.LOGIC_CONSISTENCY, score=0.96,
            **{"pass": True},
            notes="无前后矛盾。",
        ),
        QADimension.FRESHNESS: QADimensionResult(
            dimension=QADimension.FRESHNESS, score=0.99,
            **{"pass": True},
            notes="所有 evidence 抓取于 30 天内。",
        ),
        QADimension.EXPRESSION: QADimensionResult(
            dimension=QADimension.EXPRESSION, score=0.95,
            **{"pass": True},
            notes="表达规范，无禁用词。",
        ),
    },
    issues=[],
    routing=[],
    blocking=False,
)


QA_REVISE = QAVerdict(
    verdict_id="qa_revise_001",
    overall_status=QAStatus.NEEDS_REVISION,
    dimension_results={
        QADimension.FACT_CONSISTENCY: QADimensionResult(
            dimension=QADimension.FACT_CONSISTENCY, score=0.88,
            **{"pass": True},
            notes="多数段落通过；存在 1 处量化数据未在 evidence 找到字面。",
        ),
        QADimension.EVIDENCE_COMPLETENESS: QADimensionResult(
            dimension=QADimension.EVIDENCE_COMPLETENESS, score=0.72,
            **{"pass": False},
            notes="第 4 节有 2 个段落缺少 evidence_ids。",
        ),
        QADimension.SCHEMA_COMPLETENESS: QADimensionResult(
            dimension=QADimension.SCHEMA_COMPLETENESS, score=0.85,
            **{"pass": True},
            notes="-",
        ),
        QADimension.LOGIC_CONSISTENCY: QADimensionResult(
            dimension=QADimension.LOGIC_CONSISTENCY, score=0.94,
            **{"pass": True},
            notes="-",
        ),
        QADimension.FRESHNESS: QADimensionResult(
            dimension=QADimension.FRESHNESS, score=0.98,
            **{"pass": True},
            notes="-",
        ),
        QADimension.EXPRESSION: QADimensionResult(
            dimension=QADimension.EXPRESSION, score=0.92,
            **{"pass": True},
            notes="-",
        ),
    },
    issues=[
        QAIssue(
            issue_id="iss_001",
            dimension=QADimension.EVIDENCE_COMPLETENESS,
            severity="major",
            location="report.sections[3].paragraphs[0]",
            problem="SWOT 段落引用了 cl_swot_001，但段落自身 evidence_ids 为空。",
            suggested_fix="为该段落补充 evidence_ids，引用 cl_swot_001 关联的证据。",
            target_agent="reporter",
            required_inputs={"paragraph_id": "p_sw_01"},
        ),
        QAIssue(
            issue_id="iss_002",
            dimension=QADimension.FACT_CONSISTENCY,
            severity="minor",
            location="report.sections[2].paragraphs[1]",
            problem=(
                "段落提到 'Notion Business $15'，evidence 中虽提及 Business 档，"
                "但单价数字需精确匹配。"
            ),
            suggested_fix="确认引用 ev_notion_price_01 中的精确价格，或加入软结论限定。",
            target_agent="reporter",
            required_inputs={"paragraph_id": "p_pr_02"},
        ),
    ],
    routing=[
        QARouting(
            target_agent="reporter",
            reason="2 处段落问题，均可在 Reporter 层修复，无需重新采集或抽取。",
            payload={
                "must_address": ["iss_001", "iss_002"],
                "instructions": (
                    "1) 为 p_sw_01 补充 evidence_ids；"
                    "2) 检查 p_pr_02 的量化数据与 evidence 的字面一致性。"
                ),
            },
        ),
    ],
    blocking=True,
)


# =============================================================================
# Project
# =============================================================================


PROJECT = Project(
    project_id="proj_collab_demo",
    project_name="协作办公 SaaS 竞品分析 · Demo",
    owner="demo_user",
    created_at=utc(),
    target_product="Notion",
    competitors=["ClickUp", "Asana"],
    industry="collaboration_saas",
    industry_schema_version=SCHEMA_VERSION,
    analysis_dimensions=[
        AnalysisDimension.FEATURE_COMPARISON,
        AnalysisDimension.PRICING_COMPARISON,
        AnalysisDimension.SWOT,
        AnalysisDimension.DIFFERENTIATION,
    ],
    report_template_id="standard_v1",
    target_audience="产品经理",
    mode="hybrid",
    collect_constraints=CollectConstraints(),
    status=ProjectStatus.DRAFT,
)


# =============================================================================
# Wrapped Agent outputs (for testing BaseAgent.invoke contract end-to-end)
# =============================================================================


COLLECTOR_OUTPUT_NOTION = CollectorOutput(
    agent_name="collector",
    agent_version="1.0.0",
    task_id="t_collect_notion",
    trace_id="trace_demo",
    span_id="span_collect_notion",
    status=AgentStatus.SUCCESS,
    confidence=0.9,
    self_critique="2 dimensions (homepage, pricing) covered with high-authority sources.",
    raw_sources=RAW_SOURCES["notion"],
    coverage_by_dimension={
        CollectDimension.HOMEPAGE: 1,
        CollectDimension.PRICING: 1,
    },
    tokens_input=1200,
    tokens_output=80,
    duration_ms=4200,
)


EXTRACTOR_OUTPUT_NOTION = ExtractorOutput(
    agent_name="extractor",
    agent_version="1.0.0",
    task_id="t_extract_notion",
    trace_id="trace_demo",
    span_id="span_extract_notion",
    status=AgentStatus.SUCCESS,
    confidence=0.87,
    self_critique="所有必填字段已填充；user_feedback.review_count 来自二手数据，标 unverified。",
    profile=PROFILE_NOTION,
    evidences=[e for e in EVIDENCES if e.product_name == "Notion"],
    field_confidence=PROFILE_NOTION.field_confidence,
    schema_version=SCHEMA_VERSION,
    tokens_input=4800,
    tokens_output=900,
    duration_ms=12400,
)


ANALYST_OUTPUT = AnalystOutput(
    agent_name="analyst",
    agent_version="1.0.0",
    task_id="t_analyze",
    trace_id="trace_demo",
    span_id="span_analyze",
    status=AgentStatus.SUCCESS,
    confidence=0.86,
    self_critique="覆盖 3 维度；feature 维度有 1 条 counter_evidence 体现严谨。",
    result=ANALYSIS,
    tokens_input=6500,
    tokens_output=1800,
    duration_ms=16800,
)


REPORTER_OUTPUT = ReporterOutput(
    agent_name="reporter",
    agent_version="1.0.0",
    task_id="t_report",
    trace_id="trace_demo",
    span_id="span_report",
    status=AgentStatus.SUCCESS,
    confidence=0.84,
    self_critique="所有量化段落均与 evidence 字面一致；overview 段落为软结论。",
    draft=DRAFT,
    tokens_input=8400,
    tokens_output=2100,
    duration_ms=18600,
)


QA_OUTPUT_PASS = QAOutput(
    agent_name="qa",
    agent_version="1.0.0",
    task_id="t_qa",
    trace_id="trace_demo",
    span_id="span_qa",
    status=AgentStatus.SUCCESS,
    confidence=0.95,
    self_critique="6 维度全通过，无 blocking issue。",
    verdict=QA_PASS,
    tokens_input=7800,
    tokens_output=600,
    duration_ms=8200,
)


QA_OUTPUT_REVISE = QAOutput(
    agent_name="qa",
    agent_version="1.0.0",
    task_id="t_qa",
    trace_id="trace_demo",
    span_id="span_qa_v1",
    status=AgentStatus.SUCCESS,
    confidence=0.93,
    self_critique="发现 2 处段落 evidence 缺失，已路由回 Reporter。",
    verdict=QA_REVISE,
    tokens_input=7900,
    tokens_output=700,
    duration_ms=8400,
)


# =============================================================================
# Emit
# =============================================================================


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(data, "model_dump_json"):
        text = data.model_dump_json(indent=2, by_alias=True)
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, items: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for it in items:
        if hasattr(it, "model_dump"):
            lines.append(json.dumps(it.model_dump(mode="json"), ensure_ascii=False))
        else:
            lines.append(json.dumps(it, ensure_ascii=False, default=str))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def emit() -> None:
    # 项目
    _write_json(ROOT / "projects" / "collab_saas_demo.json", PROJECT)

    # raw sources
    for product, docs in RAW_SOURCES.items():
        for doc in docs:
            _write_json(
                ROOT / "raw_sources" / product / f"{doc.dimension.value}.json",
                doc,
            )

    # competitor profiles
    for product, profile in PROFILES.items():
        _write_json(ROOT / "competitor_profiles" / f"{product.lower()}.json", profile)

    # analysis result（含完整对象，便于一次加载）
    _write_json(ROOT / "analysis_results" / "analysis_full.json", ANALYSIS)
    # 分维度单独导出，便于 Reporter / Analyst 局部加载
    for dim, data in ANALYSIS.dimensions.items():
        _write_json(ROOT / "analysis_results" / f"{dim.value}.json", data)

    # report draft
    _write_json(ROOT / "report_drafts" / "draft_v1.json", DRAFT)

    # qa verdicts
    _write_json(ROOT / "qa_verdicts" / "pass.json", QA_PASS)
    _write_json(ROOT / "qa_verdicts" / "needs_revision.json", QA_REVISE)

    # evidence DB
    _write_jsonl(ROOT / "evidences" / "evidence_db.jsonl", EVIDENCES)

    # 各 Agent 完整 Output 示例（含 AgentOutputBase 通用字段，演示 trace / token / confidence）
    _write_json(ROOT / "agent_outputs" / "collector__notion.json", COLLECTOR_OUTPUT_NOTION)
    _write_json(ROOT / "agent_outputs" / "extractor__notion.json", EXTRACTOR_OUTPUT_NOTION)
    _write_json(ROOT / "agent_outputs" / "analyst__full.json", ANALYST_OUTPUT)
    _write_json(ROOT / "agent_outputs" / "reporter__v1.json", REPORTER_OUTPUT)
    _write_json(ROOT / "agent_outputs" / "qa__pass.json", QA_OUTPUT_PASS)
    _write_json(ROOT / "agent_outputs" / "qa__needs_revision.json", QA_OUTPUT_REVISE)

    print(f"Mock data written under {ROOT}")


if __name__ == "__main__":
    emit()
