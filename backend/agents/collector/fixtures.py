"""Collector mock 数据源。

每个产品 × 维度 提供一份 RawSourceDoc。所有 source_url 都用 HTTPS 真实域名占位，
内容是基于公开信息的简短中性描述，便于下游 Extractor 与 Analyst 拿到信号。

不构成投资 / 采购建议。所有数据仅用于本平台 demo。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from backend.schemas import CollectDimension, RawSourceDoc

# 默认 mock 时间戳：固定值，使快照可复现
_MOCK_COLLECTED_AT = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _make_source_id(product: str, dimension: CollectDimension, url: str) -> str:
    raw = f"{product}|{dimension.value}|{url}"
    return "src_" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def _doc(
    *,
    product: str,
    dimension: CollectDimension,
    url: str,
    title: str,
    text: str,
    language: str = "en",
    authority: float = 0.95,
    method: str = "mock",
    paywall: bool = False,
    outdated: bool = False,
) -> RawSourceDoc:
    return RawSourceDoc(
        source_id=_make_source_id(product, dimension, url),
        product_name=product,
        dimension=dimension,
        source_url=url,
        source_type="html",
        title=title,
        raw_html=None,
        raw_text=text,
        summary=None,
        language=language,
        collected_at=_MOCK_COLLECTED_AT,
        fetch_method=method,  # type: ignore[arg-type]
        http_status=200,
        robots_allowed=True,
        source_authority=authority,
        detected_paywall=paywall,
        detected_outdated=outdated,
    )


# ---------- Notion ----------

_NOTION = {
    CollectDimension.HOMEPAGE: _doc(
        product="Notion",
        dimension=CollectDimension.HOMEPAGE,
        url="https://www.notion.so/",
        title="Notion – The all-in-one workspace for your notes, tasks, wikis, and databases",
        text=(
            "Notion is the connected workspace where better, faster work happens. "
            "Write, plan, share — all in one place. Teams use Notion as docs, project "
            "trackers, knowledge bases, and lightweight databases. Founded in 2013, "
            "headquartered in San Francisco. Notion AI is integrated for summarization, "
            "writing assistance, and Q&A across your workspace."
        ),
    ),
    CollectDimension.FEATURES: _doc(
        product="Notion",
        dimension=CollectDimension.FEATURES,
        url="https://www.notion.so/product",
        title="Product · Notion",
        text=(
            "Core building blocks: pages, databases, blocks. Views include table, board, "
            "calendar, timeline (Gantt), list, and gallery. Real-time collaboration with "
            "cursor presence and comments. Notion AI provides summaries, action items, "
            "translation, and Q&A grounded in workspace content. Integrations include "
            "Slack, GitHub, Jira, Figma, Google Drive."
        ),
    ),
    CollectDimension.PRICING: _doc(
        product="Notion",
        dimension=CollectDimension.PRICING,
        url="https://www.notion.so/pricing",
        title="Pricing · Notion",
        text=(
            "Free plan: unlimited blocks for individuals, 7-day page history, up to 10 "
            "guest collaborators. Plus: $10 per user per month billed monthly, $8 billed "
            "annually. Business: $18 per user per month billed monthly, $15 billed "
            "annually, SAML SSO and private team spaces. Enterprise: custom contract with "
            "audit log, advanced security, customer success. Notion AI add-on: $10 per "
            "user per month."
        ),
    ),
    CollectDimension.HELP_DOCS: _doc(
        product="Notion",
        dimension=CollectDimension.HELP_DOCS,
        url="https://www.notion.so/help",
        title="Help Center · Notion",
        text=(
            "Notion help center covers getting started, sharing & permissions, databases "
            "deep dive, integrations, team admin (Business / Enterprise), security and "
            "compliance (SOC 2 Type 2, ISO 27001, HIPAA available on Enterprise), and "
            "Notion AI usage. Permission model: workspace > team space > page-level."
        ),
    ),
}

# ---------- ClickUp ----------

_CLICKUP = {
    CollectDimension.HOMEPAGE: _doc(
        product="ClickUp",
        dimension=CollectDimension.HOMEPAGE,
        url="https://clickup.com/",
        title="ClickUp™ | One app to replace them all",
        text=(
            "ClickUp is one app to replace them all — tasks, docs, goals, chat, whiteboards, "
            "and dashboards in one place. Founded in 2017, headquartered in San Diego. "
            "Trusted by teams at Google, Booking.com, and others. Strong focus on "
            "automations and configurability for project management workflows."
        ),
    ),
    CollectDimension.FEATURES: _doc(
        product="ClickUp",
        dimension=CollectDimension.FEATURES,
        url="https://clickup.com/features",
        title="Features · ClickUp",
        text=(
            "15+ views including List, Board, Calendar, Gantt, Timeline, Mind Map, "
            "Workload, Activity. Custom statuses, custom fields, dependencies, recurring "
            "tasks. Native time tracking. Goals and OKRs. Whiteboards and docs. "
            "Automations builder with 100+ pre-built recipes. ClickUp AI assists with "
            "writing, summarization, and task generation. Integrations with 1000+ tools "
            "via native connectors and Zapier."
        ),
    ),
    CollectDimension.PRICING: _doc(
        product="ClickUp",
        dimension=CollectDimension.PRICING,
        url="https://clickup.com/pricing",
        title="Pricing · ClickUp",
        text=(
            "Free Forever: 100MB storage, unlimited tasks, unlimited members. Unlimited: "
            "$7 per user per month billed annually, $10 monthly — unlimited storage, "
            "integrations, dashboards. Business: $12 per user per month annually, $19 "
            "monthly — Google SSO, advanced automations and time tracking. Business Plus: "
            "$19 per user per month annually. Enterprise: custom — SAML SSO, MSA, "
            "dedicated success manager. ClickUp AI add-on: $5 per member per month."
        ),
    ),
    CollectDimension.HELP_DOCS: _doc(
        product="ClickUp",
        dimension=CollectDimension.HELP_DOCS,
        url="https://help.clickup.com/",
        title="ClickUp Help Center",
        text=(
            "Help center covers Spaces, Folders, Lists, Tasks hierarchy; views and "
            "filtering; automations recipes; permissions (Owner / Admin / Member / Guest); "
            "security (SOC 2 Type 2, ISO 27001, GDPR), data residency options (US / EU). "
            "Detailed API documentation and webhook reference available."
        ),
    ),
}

# ---------- Asana ----------

_ASANA = {
    CollectDimension.HOMEPAGE: _doc(
        product="Asana",
        dimension=CollectDimension.HOMEPAGE,
        url="https://asana.com/",
        title="Manage your team's work, projects & tasks online · Asana",
        text=(
            "Asana helps teams orchestrate their work — from daily tasks to strategic "
            "initiatives. Founded in 2008 by Dustin Moskovitz and Justin Rosenstein, "
            "headquartered in San Francisco, NYSE: ASAN. Asana Intelligence applies AI "
            "to status updates, smart goals, and workflow insights."
        ),
    ),
    CollectDimension.FEATURES: _doc(
        product="Asana",
        dimension=CollectDimension.FEATURES,
        url="https://asana.com/product",
        title="Product overview · Asana",
        text=(
            "Views: List, Board, Timeline (Gantt), Calendar, Portfolios, Workload. "
            "Custom fields, rules-based automations, forms intake. Goals link work to "
            "company OKRs across portfolios. Asana Intelligence offers smart summaries, "
            "smart status, and goal recommendations. Integrates with Slack, Microsoft "
            "Teams, Salesforce, Adobe Creative Cloud."
        ),
    ),
    CollectDimension.PRICING: _doc(
        product="Asana",
        dimension=CollectDimension.PRICING,
        url="https://asana.com/pricing",
        title="Pricing · Asana",
        text=(
            "Personal: free for up to 10 users, basic features. Starter: $10.99 per user "
            "per month billed annually, $13.49 monthly — Timeline view, dashboards, "
            "unlimited free guests. Advanced: $24.99 per user per month annually, $30.49 "
            "monthly — goals, portfolios, advanced reporting and automations. Enterprise "
            "and Enterprise+: custom pricing — SAML, SCIM, data residency, audit log API."
        ),
    ),
    CollectDimension.HELP_DOCS: _doc(
        product="Asana",
        dimension=CollectDimension.HELP_DOCS,
        url="https://asana.com/guide",
        title="Asana Guide",
        text=(
            "Asana Guide covers getting started, projects & tasks, portfolios, goals, "
            "rules and forms, admin console for org-wide settings, security (SOC 2 Type 2, "
            "ISO 27001/27017/27018, HIPAA on Enterprise+, GDPR, CCPA), data residency "
            "options (US / EU / Australia / Japan / Germany)."
        ),
    ),
}


# Per-product registry
_BY_PRODUCT: dict[str, dict[CollectDimension, RawSourceDoc]] = {
    "Notion": _NOTION,
    "ClickUp": _CLICKUP,
    "Asana": _ASANA,
}


def get_mock_sources(
    product: str, dimensions: list[CollectDimension]
) -> list[RawSourceDoc]:
    """按产品 + 维度组合返回 mock RawSourceDoc 列表。

    未覆盖的产品 / 维度返回空（由 Collector 转 self_critique 提示）。
    """
    bucket = _BY_PRODUCT.get(product, {})
    out: list[RawSourceDoc] = []
    for d in dimensions:
        doc = bucket.get(d)
        if doc is not None:
            # 每次返回新对象，避免外部修改污染缓存
            out.append(doc.model_copy(deep=True))
    return out


def known_products() -> list[str]:
    return list(_BY_PRODUCT.keys())


def known_dimensions(product: str) -> list[CollectDimension]:
    return list(_BY_PRODUCT.get(product, {}).keys())


__all__ = ["get_mock_sources", "known_dimensions", "known_products"]
