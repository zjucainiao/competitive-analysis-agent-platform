"""「相对语义」来源权威度矩阵 —— 共享给 Collector（采集时打分）与 QA（消费时校正）。

核心思想：来源权威度**不是来源自带的绝对属性**，而取决于「它在回答哪个维度」。
- 厂商正典维度（定价/功能/文档/变更/案例/集成）：官方页最权威，第三方偏低。
- user_reviews（口碑）：评论聚合站才是正典，**官方页是营销话术**（低权威）。
- 其他维度（blog/news/other）：折中。

Collector 采集时按「来源类型 × 采集维度」打 ``source_authority``；QA 消费一条证据时，
按「来源类型 × **该证据被用到的报告段落主题维度**」重算，纠正「评论证据采集维度=reviews
(0.92) 却被用到 pricing 段落却仍按 0.92」的错位（见 ``authority_for`` + ``ANALYSIS_TO_COLLECT``）。
"""

from __future__ import annotations

from backend.schemas import AnalysisDimension, CollectDimension
from backend.schemas.evidence import SourceClass

# 来源类型 × 维度 → 权威度（0-1）。三张表按「该维度的正典来源是谁」分档。
_AUTHORITY_VENDOR_CANONICAL = {"official": 0.95, "review": 0.6, "other": 0.5}
_AUTHORITY_REVIEWS = {"official": 0.5, "review": 0.92, "other": 0.6}
_AUTHORITY_DEFAULT = {"official": 0.8, "review": 0.7, "other": 0.6}

# 厂商正典维度：官方页是权威源。
_VENDOR_CANONICAL_DIMS = frozenset(
    {
        CollectDimension.HOMEPAGE,
        CollectDimension.FEATURES,
        CollectDimension.PRICING,
        CollectDimension.HELP_DOCS,
        CollectDimension.CHANGELOG,
        CollectDimension.CASES,
        CollectDimension.APP_MARKET,
    }
)

# 报告段落主题维度（AnalysisDimension）→ 权威矩阵维度（CollectDimension）。
# 综合判断型维度（SWOT/差异化/定位）来源天然多元、无单一正典采集维度 → None：
# QA 对这些段落**保守不做**跨维度校正，沿用证据采集时的 source_authority（避免误校正）。
ANALYSIS_TO_COLLECT: dict[AnalysisDimension, CollectDimension | None] = {
    AnalysisDimension.FEATURE_COMPARISON: CollectDimension.FEATURES,
    AnalysisDimension.PRICING_COMPARISON: CollectDimension.PRICING,
    AnalysisDimension.USER_FEEDBACK: CollectDimension.REVIEWS,
    AnalysisDimension.SWOT: None,
    AnalysisDimension.DIFFERENTIATION: None,
    AnalysisDimension.POSITIONING: None,
}


def authority_for(source_class: SourceClass, dimension: CollectDimension) -> float:
    """按「来源类型 × 维度」查相对权威度。

    例：authority_for("official", PRICING)=0.95；authority_for("official", REVIEWS)=0.5；
    authority_for("review", REVIEWS)=0.92；authority_for("review", PRICING)=0.6。
    """
    if dimension is CollectDimension.REVIEWS:
        table = _AUTHORITY_REVIEWS
    elif dimension in _VENDOR_CANONICAL_DIMS:
        table = _AUTHORITY_VENDOR_CANONICAL
    else:
        table = _AUTHORITY_DEFAULT
    return table[source_class]


__all__ = ["ANALYSIS_TO_COLLECT", "authority_for"]
