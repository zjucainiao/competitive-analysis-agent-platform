"""枚举 → 读者向中文标签的集中映射。

后端有多处需要把内部枚举（``feature_comparison`` / ``collaboration_saas``）
渲染成交付物里的中文（报告抬头、概览段、来源列表）。集中在一处，避免
reporter / api 各写一份漂移。前端 wizard 的标签与此保持一致。
"""

from __future__ import annotations

# AnalysisDimension.value → 中文（与前端 wizard DIMENSIONS 对齐）
DIMENSION_LABELS: dict[str, str] = {
    "feature_comparison": "功能对比",
    "pricing_comparison": "定价对比",
    "user_feedback": "用户口碑",
    "swot": "SWOT",
    "differentiation_opportunities": "差异化机会",
    "positioning": "市场定位",
}

# industry_id → 中文（与前端 wizard INDUSTRIES 对齐）
INDUSTRY_LABELS: dict[str, str] = {
    "collaboration_saas": "协作办公 SaaS",
    "crm_saas": "CRM SaaS",
    "cross_border_ecommerce_saas": "跨境电商 SaaS",
    "edu_saas": "教育 SaaS",
}


def dimension_label(value: str) -> str:
    """``feature_comparison`` → ``功能对比``；未知值原样返回。"""
    return DIMENSION_LABELS.get(value, value)


def industry_label(value: str) -> str:
    """``collaboration_saas`` → ``协作办公 SaaS``；容忍 ``_v1`` 版本后缀；未知值原样返回。"""
    if not value:
        return value
    key = value
    # 容忍 industry_schema_id 形式（collaboration_saas_v1）
    for known in INDUSTRY_LABELS:
        if value == known or value.startswith(known + "_v"):
            key = known
            break
    return INDUSTRY_LABELS.get(key, value)
