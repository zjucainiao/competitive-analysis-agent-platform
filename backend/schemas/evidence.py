"""Evidence（证据）与 RawSourceDoc（原始来源）数据模型。

详细使用规则见 docs/EVIDENCE.md。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

# 产品身份校验状态（来源/证据内容是否真的属于 product_name 标注的产品）。
# - unvalidated：未做校验（默认；mock / 旧数据 / 校验跳过）
# - confirmed：内容确属目标产品
# - mismatch：内容属于**别的**产品（如分析钉钉却抓到飞书评价）—— QA 据此返工
# - ambiguous：提到目标产品但无法确证（对比页/跨语言别名漏命中等），仅浮出不强返工
IdentityStatus = Literal["unvalidated", "confirmed", "mismatch", "ambiguous"]

# 来源类型粗分类（用于「相对语义」权威度：权威度 = f(来源类型, 消费维度)）。
# - official：目标产品官方页（官网/官方文档）
# - review：评论聚合站（G2 / Capterra / TrustRadius …）
# - other：其他第三方（博客/媒体/论坛/社媒等）
# Collector 抓取时按 URL 判定并填入；None=未判定（旧数据/mock）→ 下游不做跨维度校正。
SourceClass = Literal["official", "review", "other"]

# 信任级别（WI-1）：抓取的外部内容默认 untrusted（可能含间接 prompt injection）；
# trusted 预留给非外部来源（系统生成 / 人工录入，目前未使用）。配合 injection_guard
# 的 tainted 标记，让下游对不可信内容做数据区隔离 + QA 据此提权。
TrustLevel = Literal["trusted", "untrusted"]


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

    # ---- 产品身份校验（从源文档 RawSourceDoc 继承；详见 docs/QA.md）----
    # product_name 是「声称」的产品；以下三字段记录「内容实际像哪个产品」的检测结果，
    # 让 QA 的 identity_consistency 维度能发现「抓错产品」（如分析钉钉却引用了飞书评价）。
    detected_product_name: str | None = Field(
        default=None,
        description="内容检测出的产品名；None 表示未检测",
    )
    identity_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="该证据确属 product_name 的置信度（0-1）；None 表示未评估",
    )
    identity_status: IdentityStatus = Field(
        default="unvalidated",
        description="身份校验结论：unvalidated/confirmed/mismatch/ambiguous",
    )
    source_class: SourceClass | None = Field(
        default=None,
        description=(
            "来源类型粗分类（official/review/other）；从 RawSourceDoc 继承。"
            "供 QA 做「相对语义」跨维度权威度校正——None 表示未判定，下游不校正。"
        ),
    )

    # ---- 不可信内容 / 间接注入标记（从 RawSourceDoc 继承；WI-1）----
    trust_level: TrustLevel = Field(
        default="untrusted",
        description="证据原文信任级别；源自抓取的外部内容默认 untrusted",
    )
    tainted: bool = Field(
        default=False,
        description="injection_guard 在源文本中检出疑似 prompt injection 模式",
    )
    taint_reasons: list[str] = Field(
        default_factory=list,
        description="命中的注入模式名（injection_guard 的 matched_patterns）",
    )


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
    # fetch_method 语义：search/firecrawl/playwright/manual=真实抓取链；mock=测试夹具；
    # llm_synthesis=LLM 合成文本（REVIEWS 维度联网搜索路径的产物，**非真实抓取**，
    # 下游必须区别消费：低权威、身份不作 confirmed）。
    fetch_method: Literal["search", "firecrawl", "playwright", "mock", "manual", "llm_synthesis"]
    http_status: int | None = None
    robots_allowed: bool = True
    source_authority: float = Field(ge=0, le=1, default=0.7)
    detected_paywall: bool = False
    detected_outdated: bool = Field(
        default=False,
        description="页面 last-modified 早于 1 年",
    )

    # ---- 产品身份校验（Collector 抓取后填，Extractor 继承到 Evidence）----
    # product_name 是「声称采集的」产品；以下记录「页面内容实际像哪个产品」，
    # 用来拦截「搜索/排序选错源」导致抓到别的产品内容的情况。
    detected_product_name: str | None = Field(
        default=None,
        description="页面内容检测出的产品名；None 表示未检测",
    )
    identity_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="该页面确属 product_name 的置信度（0-1）；None 表示未评估",
    )
    identity_status: IdentityStatus = Field(
        default="unvalidated",
        description="身份校验结论：unvalidated/confirmed/mismatch/ambiguous",
    )
    source_class: SourceClass | None = Field(
        default=None,
        description=(
            "来源类型粗分类（official/review/other），Collector 按 URL 判定。"
            "Extractor 透传到 Evidence，供 QA 跨维度权威度校正；None=未判定。"
        ),
    )

    # ---- 不可信内容 / 间接注入标记（Collector 抓取后用 injection_guard 标，
    # Extractor 继承到 Evidence；WI-1）----
    trust_level: TrustLevel = Field(
        default="untrusted",
        description="抓取的外部内容默认 untrusted（可能含间接 prompt injection）",
    )
    tainted: bool = Field(
        default=False,
        description="injection_guard 在 raw_text 中检出疑似注入模式",
    )
    taint_reasons: list[str] = Field(
        default_factory=list,
        description="命中的注入模式名",
    )
