"""Evidence（证据）与 RawSourceDoc（原始来源）数据模型。

详细使用规则见 docs/EVIDENCE.md。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class CollectDimension(str, Enum):
    """采集维度。Collector 按此粒度抓取。"""

    HOMEPAGE = "homepage"
    FEATURES = "features"
    PRICING = "pricing"
    HELP_DOCS = "help_docs"
    CHANGELOG = "changelog"
    CASES = "customer_cases"
    BLOG = "blog"
    REVIEWS = "user_reviews"
    APP_MARKET = "app_market"


class EvidenceLocation(BaseModel):
    """证据在原始来源文档中的位置。"""

    model_config = ConfigDict(extra="forbid")

    char_start: int | None = None
    char_end: int | None = None
    selector: str | None = Field(default=None, description="CSS selector / xpath")
    page_section: str | None = Field(default=None, description="所在小节标题")


class Evidence(BaseModel):
    """单条证据片段。报告 / 分析中的每个 claim 至少绑定 1 条。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(description="全局唯一，所有引用都用这个")
    source_id: str = Field(description="关联到 RawSourceDoc.source_id")
    product_name: str
    source_url: HttpUrl
    source_type: str = Field(description="pricing_page / review / blog / docs / ...")
    source_authority: float = Field(ge=0, le=1, description="0.95=官方页, 0.6=UGC")

    content: str = Field(description="证据原文片段，核心字段")
    content_hash: str = Field(description="用于去重")
    context_before: str | None = None
    context_after: str | None = None
    location: EvidenceLocation = Field(default_factory=EvidenceLocation)

    language: str = Field(default="en", description="ISO 639-1, e.g. 'en' / 'zh'")
    collected_at: datetime
    extracted_at: datetime
    source_published_at: datetime | None = Field(
        default=None,
        description=(
            "源文档发布/最后修改时间。来自页面 <time>、meta[name=date]、"
            "JSON-LD datePublished 或 HTTP Last-Modified。"
            "None 表示 Collector 未能识别——freshness 检查会按"
            "'无可靠日期'走中性兜底，避免把刚抓的旧文档判为新鲜。"
        ),
    )
    confidence: float = Field(ge=0, le=1, description="抽取置信度")

    tags: list[str] = Field(default_factory=list)
    embedding_id: str | None = Field(default=None, description="向量库主键，可为 None")

    # 状态标记（用户可标记 disputed，触发重审）
    disputed: bool = False


class RawSourceDoc(BaseModel):
    """Collector 输出的单个原始来源文档。Extractor 的输入。"""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    product_name: str
    dimension: CollectDimension
    source_url: HttpUrl
    source_type: str = Field(description="html / pdf / json / ...")
    title: str | None = None
    raw_html: str | None = Field(
        default=None,
        description="完整 HTML。生产环境通常仅存对象存储路径，此处可放摘要或 None",
    )
    raw_text: str = Field(description="抽正文后的纯文本")
    summary: str | None = None
    language: str = "en"

    collected_at: datetime
    fetch_method: Literal["search", "firecrawl", "playwright", "mock", "manual"]
    http_status: int | None = None
    robots_allowed: bool = True
    source_authority: float = Field(ge=0, le=1, default=0.7)
    detected_paywall: bool = False
    detected_outdated: bool = Field(
        default=False,
        description="页面 last-modified 早于 1 年",
    )
