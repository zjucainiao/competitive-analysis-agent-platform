"""Collector Agent — 公开信息采集。

职责：从搜索 + 抓取工具获取候选 URL → 合规过滤 → 抓正文 → 输出 RawSourceDoc 列表。
**不做语义抽取**——那是 Extractor 的事。

实现链：
    search.tavily / search.serper → URL 候选
        → url ranker（LLM 或启发式）→ top-K
        → robots_checker（合规过滤）
        → domain_rate_limiter（≤ 1 req/s/host）
        → scrape.firecrawl（主）→ scrape.playwright（fallback）→ mock fixtures（最后兜底）
        → page_type_classifier（LLM 或启发式）→ 维度复核
        → 构造 RawSourceDoc
"""

from __future__ import annotations

import contextvars
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agents._authority import authority_for
from backend.agents._base import (
    AgentRunError,
    BaseAgent,
    LLMProviderProtocol,
    ToolRegistryProtocol,
    TracerProtocol,
)
from backend.agents._progress import emit_collect_progress
from backend.schemas import (
    AgentError,
    AgentStatus,
    CollectDimension,
    CollectorInput,
    CollectorOutput,
    RawSourceDoc,
)
from backend.schemas.evidence import IdentityStatus
from backend.tools.injection_guard import scan as _scan_injection

from .fixtures import get_mock_sources
from .tools import (
    ScrapeProvider,
    ScrapeResult,
    SearchHit,
    SearchProvider,
)

PROMPT_DIR = Path(__file__).parent / "prompts"

# 每个维度对应的搜索关键词（追加在产品名后面）
DIMENSION_QUERY_KEYWORD: dict[CollectDimension, str] = {
    CollectDimension.HOMEPAGE: "official site",
    CollectDimension.FEATURES: "features product overview",
    CollectDimension.PRICING: "pricing plans",
    CollectDimension.HELP_DOCS: "help center documentation",
    CollectDimension.CHANGELOG: "changelog release notes",
    CollectDimension.CASES: "customer case study",
    CollectDimension.BLOG: "blog",
    CollectDimension.REVIEWS: "review G2 Capterra",
    CollectDimension.APP_MARKET: "app marketplace integrations",
}

# URL 路径关键词 → page type（启发式分类用）
_URL_KEYWORDS: list[tuple[tuple[str, ...], CollectDimension]] = [
    (("/pricing", "/plans", "/billing"), CollectDimension.PRICING),
    (("/help", "/docs", "/documentation", "/manual", "/support"), CollectDimension.HELP_DOCS),
    (("/changelog", "/release-notes", "/whats-new", "/release"), CollectDimension.CHANGELOG),
    (("/blog", "/news"), CollectDimension.BLOG),
    (
        ("/customers", "/case-study", "/case-studies", "/stories", "/customer-stories"),
        CollectDimension.CASES,
    ),
    (("/features", "/product", "/platform", "/capabilities"), CollectDimension.FEATURES),
    (("/marketplace", "/apps", "/integrations"), CollectDimension.APP_MARKET),
]

_REVIEW_HOSTS = (
    "g2.com",
    "capterra.com",
    "trustradius.com",
    "softwareadvice.com",
    "gartner.com",
)

# 各维度在 official_url 下常见的路径片段。
# 搜索失败时由 _seed_from_official_url 拼接出候选 URL，
# 由 scrape 真正去验证页面是否存在 + page_type_classifier 复核维度。
_DIMENSION_PATH_HINTS: dict[CollectDimension, tuple[str, ...]] = {
    CollectDimension.HOMEPAGE: ("",),
    CollectDimension.FEATURES: ("product", "features", "platform", "capabilities"),
    CollectDimension.PRICING: ("pricing", "plans"),
    CollectDimension.HELP_DOCS: ("help", "docs", "support", "guide"),
    CollectDimension.CHANGELOG: ("changelog", "release-notes", "whats-new"),
    CollectDimension.CASES: ("customers", "case-studies", "stories"),
    CollectDimension.BLOG: ("blog",),
    CollectDimension.APP_MARKET: ("integrations", "marketplace", "apps"),
    # REVIEWS 不在官网，留空；走搜索 / 已知评论站
}


# ---------- 内部用 Pydantic（LLM response 校验） ----------


class _UrlRanking(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    score: float = Field(ge=0, le=1)
    reason: str = ""
    page_type: str = "other"


class _UrlRankingList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rankings: list[_UrlRanking]


class _PageTypeClassification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_type: str
    confidence: float = Field(ge=0, le=1)
    is_paywall: bool = False
    is_outdated: bool = False
    reason: str = ""


class _PageSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    language: str = "en"


class _IdentityCheck(BaseModel):
    """LLM 对「页面是否在讲目标产品」的判定（identity_validator.md 的 response_format）。"""

    model_config = ConfigDict(extra="ignore")

    is_target_product: bool
    detected_product_name: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class _ReviewSource(BaseModel):
    """LLM 报告的单个评论站点来源。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="G2 / Capterra / TrustRadius / ...")
    url: str = Field(description="该平台上该产品的评论页 URL")
    excerpt: str = Field(default="", description="<=120 字的该来源评分+典型评价摘要")


class _ReviewsFinding(BaseModel):
    """LLM 联网搜索后的用户评价综合结果。Extractor 抽 user_feedback.overall_rating 依赖此。"""

    model_config = ConfigDict(extra="ignore")

    overall_rating: float | None = Field(
        default=None,
        ge=0,
        le=5,
        description="0-5 综合评分；多源时取算术平均；找不到必须填 null",
    )
    review_count: int | None = Field(default=None, description="评论总数（可选）")
    positive_themes: list[str] = Field(default_factory=list, description="3-5 条")
    negative_themes: list[str] = Field(default_factory=list, description="3-5 条")
    sample_quotes: list[str] = Field(default_factory=list, description="2-4 条原文")
    sources: list[_ReviewSource] = Field(default_factory=list)


# ---------- 启发式 fallback ----------


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.lower()


def _is_official(url: str, product_name: str, official_url: str | None) -> bool:
    host = _host_of(url)
    if official_url:
        official_host = _host_of(official_url)
        if official_host and host.endswith(official_host):
            return True
    # 模糊匹配：产品名出现在 host 内
    needle = product_name.lower().replace(" ", "")
    return bool(needle) and needle in host.replace(".", "")


# ---------- 产品身份校验（启发式 gate） ----------

# 常见公共后缀，取域名主标签时跳过（无需引 tldextract 这种重依赖）。
_COMMON_TLDS = frozenset(
    {"com", "cn", "net", "org", "io", "co", "ai", "app", "dev", "so", "me", "xyz"}
)
# 启发式「确属目标产品」所需的正文别名命中次数下限。
_IDENTITY_MIN_BODY_HITS = 2


def _domain_label(host: str) -> str:
    """取域名的可注册主标签：www.dingtalk.com / dingtalk.com → 'dingtalk'。

    简化实现：去掉 www. 前缀，从右往左跳过公共后缀，返回第一个非后缀段。
    """
    host = (host or "").lower().strip().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if not parts:
        return ""
    # 从右往左找第一个不是公共后缀的段
    for p in reversed(parts):
        if p not in _COMMON_TLDS:
            return p
    return parts[0]


def _identity_aliases(product_name: str, official_url: str | None) -> set[str]:
    """构造目标产品的别名集合（全小写），用于在 title/正文里命中匹配。

    来源：产品名本身（含去空格变体）+ official_url 的域名主标签
    （如 dingtalk.com → 'dingtalk'，覆盖中文名搜不到英文页的情况）。
    过滤掉过短（<2）的噪声别名。
    """
    aliases: set[str] = set()
    pn = (product_name or "").strip().lower()
    if pn:
        aliases.add(pn)
        aliases.add(pn.replace(" ", ""))
    if official_url:
        label = _domain_label(_host_of(official_url))
        if label:
            aliases.add(label)
    return {a for a in aliases if len(a) >= 2}


def _assess_identity_heuristic(
    *,
    text: str,
    title: str | None,
    url: str,
    product_name: str,
    official_url: str | None,
) -> tuple[IdentityStatus, float, str | None, bool]:
    """启发式身份 gate。返回 ``(status, identity_confidence, detected_name, decided)``。

    - 官方域名，或「标题命中别名 且 正文别名命中 ≥ 阈值」→ confirmed（高置信），decided=True，跳过 LLM。
    - 其余一律判为「需进一步裁定」：返回 ambiguous + decided=False，交给 LLM。
      （保守：启发式自己**不产** mismatch —— 别名集可能不全/跨语言，硬判错产品风险高。）
    """
    aliases = _identity_aliases(product_name, official_url)
    title_l = (title or "").lower()
    text_l = (text or "")[:4000].lower()
    official = _is_official(url, product_name, official_url)
    title_hit = any(a in title_l for a in aliases)
    body_hits = sum(text_l.count(a) for a in aliases)
    if official or (title_hit and body_hits >= _IDENTITY_MIN_BODY_HITS):
        return "confirmed", 0.9, product_name, True
    return "ambiguous", 0.4, None, False


def _heuristic_rank(
    *,
    hits: list[SearchHit],
    product_name: str,
    official_url: str | None,
    dimension: CollectDimension,
) -> list[tuple[SearchHit, float]]:
    """启发式打分。返回按分数降序排序的 (hit, score)。"""
    scored: list[tuple[SearchHit, float]] = []
    keyword = DIMENSION_QUERY_KEYWORD[dimension].split()[0].lower()
    for h in hits:
        url = (h.url or "").lower()
        title = (h.title or "").lower()
        score = 0.4
        if _is_official(h.url, product_name, official_url):
            score += 0.35
        if dimension is CollectDimension.REVIEWS:
            if any(rh in url for rh in _REVIEW_HOSTS):
                score += 0.4
        for keywords, dim in _URL_KEYWORDS:
            if dim is dimension and any(k in url for k in keywords):
                score += 0.25
                break
        if keyword in title or keyword in url:
            score += 0.1
        if h.raw_score is not None:
            score = max(score, min(1.0, h.raw_score))
        scored.append((h, min(score, 1.0)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _heuristic_classify(url: str, title: str | None) -> tuple[CollectDimension | None, float]:
    url_l = url.lower()
    title_l = (title or "").lower()
    for keywords, dim in _URL_KEYWORDS:
        if any(k in url_l for k in keywords):
            return dim, 0.7
    if any(rh in url_l for rh in _REVIEW_HOSTS):
        return CollectDimension.REVIEWS, 0.75
    if "/" not in url_l.split("//", 1)[-1].rstrip("/").split("?")[0] or url_l.endswith(".com/"):
        # 域名根路径，倾向 homepage
        return CollectDimension.HOMEPAGE, 0.55
    if "blog" in title_l:
        return CollectDimension.BLOG, 0.6
    return None, 0.0


def _is_review_host(url: str) -> bool:
    """URL 是否属已知评论聚合站（G2 / Capterra / TrustRadius …）。"""
    host = _host_of(url)
    return any(rh in host for rh in _REVIEW_HOSTS)


def _source_class(url: str, product_name: str, official_url: str | None) -> str:
    """来源类型粗分类：official（官方页）/ review（评论聚合站）/ other（其他第三方）。"""
    if _is_official(url, product_name, official_url):
        return "official"
    if _is_review_host(url):
        return "review"
    return "other"


def _heuristic_authority(
    *,
    url: str,
    product_name: str,
    official_url: str | None,
    dimension: CollectDimension,
) -> float:
    """来源权威度（**相对维度**，矩阵见 backend.agents._authority）：同一来源换个维度，
    权威度可能反转——官网在 pricing=0.95、在 user_reviews=0.5；评论站在 user_reviews=0.92。"""
    return authority_for(_source_class(url, product_name, official_url), dimension)


def _short_text(text: str) -> bool:
    return len(text.strip()) < 200


def _product_slug(product_name: str) -> str:
    """简易 slug：lowercase + 空白转连字符。G2 等评论站常用此约定。"""
    return product_name.strip().lower().replace(" ", "-")


def _seed_review_hosts(product_name: str) -> list[SearchHit]:
    """REVIEWS 维度的 host-level seed。即使 LLM 联网失败、search API 没 key，
    至少能把 G2 / Capterra 评论页 URL 喂给 scraper 试一次。"""
    slug = _product_slug(product_name)
    if not slug:
        return []
    return [
        SearchHit(
            url=f"https://www.g2.com/products/{slug}/reviews",
            title=f"{product_name} reviews | G2",
            snippet=None,
            provider="seed.review_host",
        ),
        SearchHit(
            url=f"https://www.capterra.com/p/{slug}/",
            title=f"{product_name} on Capterra",
            snippet=None,
            provider="seed.review_host",
        ),
        SearchHit(
            url=f"https://www.trustradius.com/products/{slug}/reviews",
            title=f"{product_name} reviews | TrustRadius",
            snippet=None,
            provider="seed.review_host",
        ),
    ]


def _seed_from_official_url(
    *,
    official_url: str | None,
    product_name: str,
    dimension: CollectDimension,
) -> list[SearchHit]:
    """无搜索结果时，从 official_url 拼候选 URL。

    HOMEPAGE 维度直接命中根路径；其他维度拼几个公认路径，让 scrape + classifier 真去验证。
    """
    if not official_url:
        return []
    hints = _DIMENSION_PATH_HINTS.get(dimension, ())
    if not hints:
        return []
    from urllib.parse import urljoin

    seeds: list[SearchHit] = []
    base = official_url if official_url.endswith("/") else official_url + "/"
    for path in hints:
        full = urljoin(base, path) if path else base
        title = f"{product_name} {dimension.value}".strip()
        seeds.append(SearchHit(url=full, title=title, snippet=None, provider="seed.official_url"))
    return seeds


def _dedupe_hits(hits: list[SearchHit]) -> list[SearchHit]:
    """按 URL 去重，保持首次出现顺序。"""
    seen: set[str] = set()
    out: list[SearchHit] = []
    for h in hits:
        u = (h.url or "").rstrip("/")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(h)
    return out


# ---------- Collector ----------


class Collector(BaseAgent[CollectorInput, CollectorOutput]):
    """采集 Agent。详见 docs/AGENTS.md § 3。"""

    name: ClassVar[str] = "collector"
    version: ClassVar[str] = "1.0.0"
    input_model: ClassVar[type[BaseModel]] = CollectorInput
    output_model: ClassVar[type[BaseModel]] = CollectorOutput
    required_tools: ClassVar[list[str]] = [
        "search.tavily",
        "search.duckduckgo",
        "scrape.firecrawl",
        "scrape.httpx",
        "scrape.playwright",
        "robots_checker",
        "domain_rate_limiter",
    ]

    # 启发式置信度参数
    BASE_CONFIDENCE: ClassVar[float] = 0.95
    PENALTY_EMPTY_DIMENSION: ClassVar[float] = 0.15
    PENALTY_PAYWALL: ClassVar[float] = 0.05
    PENALTY_SHORT_TEXT: ClassVar[float] = 0.05
    PENALTY_HIGH_ROBOTS_BLOCK_RATIO: ClassVar[float] = 0.10
    # 抓到别的产品（mismatch）扣重一些；提到但存疑（ambiguous）轻扣。
    # 足量 mismatch 会把 confidence 压到 SELF_CRITIQUE_THRESHOLD 以下 → 自评 NEEDS_REWORK。
    PENALTY_IDENTITY_MISMATCH: ClassVar[float] = 0.15
    PENALTY_IDENTITY_AMBIGUOUS: ClassVar[float] = 0.05
    # 身份校验 LLM 裁定的**每产品调用上限**：第三方噪音多的产品(如 Figma)会有大量
    # 模糊源，逐条上 LLM 会把单产品 LLM 调用数推高、撞节点超时。封顶后超出的模糊源
    # 退回启发式（仍标 ambiguous、仍被 QA 浮出），只是不再 LLM 逐条裁定。
    MAX_IDENTITY_LLM_CALLS: ClassVar[int] = 8
    ROBOTS_BLOCK_RATIO_THRESHOLD: ClassVar[float] = 0.30
    # 维度并行采集的最大并发线程数（防止对同一站点过猛 + 控制 LLM 并发）
    MAX_DIMENSION_WORKERS: ClassVar[int] = 5

    def __init__(
        self,
        *,
        llm: LLMProviderProtocol | None = None,
        tools: ToolRegistryProtocol | None = None,
        tracer: TracerProtocol | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(llm=llm, tools=tools, tracer=tracer, mock=mock)
        self._reset_llm_usage_acc()

    # ----- LLM usage 累加（与 I 窗口 LLMResponse.cost_usd 配套） -----

    def _reset_llm_usage_acc(self) -> None:
        """每次 invoke 入口都 reset；同一 Collector 实例可能被 Orchestrator 多次调用。"""
        self._tokens_input_acc: int = 0
        self._tokens_output_acc: int = 0
        self._cost_usd_acc: float = 0.0
        # 维度并行采集时多线程会并发累加，用锁保护 read-modify-write。
        self._usage_lock = threading.Lock()
        # 身份校验 LLM 的每产品预算（并行维度共享，扣减走 _usage_lock）。
        self._identity_llm_left: int = self.MAX_IDENTITY_LLM_CALLS

    def _record_llm_usage(self, resp: Any) -> None:
        """累计单次 LLM 调用的 token 与成本。

        ``resp`` 通常是 ``LLMResponse``；用 getattr 容错，方便测试 stub
        以及未来切换到别的 provider 实现时不强耦合。
        """
        if resp is None:
            return
        ti = int(getattr(resp, "tokens_input", 0) or 0)
        to = int(getattr(resp, "tokens_output", 0) or 0)
        cost = float(getattr(resp, "cost_usd", 0.0) or 0.0)
        with self._usage_lock:
            self._tokens_input_acc += ti
            self._tokens_output_acc += to
            self._cost_usd_acc += cost

    # ----- Mock -----

    def _run_mock(self, inp: CollectorInput) -> CollectorOutput:
        self._reset_llm_usage_acc()
        sources = get_mock_sources(inp.product_name, inp.dimensions)
        errors: list[AgentError] = []
        coverage = self._compute_coverage(inp.dimensions, sources)
        missing = [d for d, n in coverage.items() if n == 0]
        for d in missing:
            errors.append(
                AgentError(
                    code="NO_RELEVANT_RESULTS",
                    message=f"mock fixtures missing dimension={d.value} for {inp.product_name}",
                    severity="warn",
                    retriable=False,
                )
            )
        confidence = self._compute_confidence(sources, inp.dimensions)
        critique = self._build_self_critique(sources, inp.dimensions, errors)
        status = AgentStatus.SUCCESS
        if missing:
            status = AgentStatus.PARTIAL
        if confidence < self.SELF_CRITIQUE_THRESHOLD:
            status = AgentStatus.NEEDS_REWORK
        return CollectorOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=status,
            confidence=confidence,
            self_critique=critique,
            tokens_input=self._tokens_input_acc,
            tokens_output=self._tokens_output_acc,
            cost_usd=self._cost_usd_acc,
            duration_ms=0,  # BaseAgent 会回填
            errors=errors,
            raw_sources=sources,
            coverage_by_dimension=coverage,
        )

    # ----- Real -----

    def _run(self, inp: CollectorInput) -> CollectorOutput:
        self._reset_llm_usage_acc()
        if self.tools is None:
            raise AgentRunError(
                code="UPSTREAM_MISSING",
                message="tool registry not provided",
                retriable=False,
            )
        search_providers = self._collect_enabled(
            ("search.tavily", "search.serper", "search.duckduckgo"), SearchProvider
        )
        # firecrawl 优先（带 markdown + onlyMainContent），playwright 次之（JS 渲染），
        # httpx 兜底（无 key、无 JS，免费）
        scrape_chain: list[ScrapeProvider] = self._collect_enabled(
            ("scrape.firecrawl", "scrape.playwright", "scrape.httpx"), ScrapeProvider
        )
        robots = self.tools.get("robots_checker") if self.tools.has("robots_checker") else None
        limiter = (
            self.tools.get("domain_rate_limiter") if self.tools.has("domain_rate_limiter") else None
        )

        errors: list[AgentError] = []
        all_sources: list[RawSourceDoc] = []

        # 各维度相互独立（scrape 全 httpx + 工具内部已加锁，token 累加也加锁），
        # 并行采集把单产品耗时从 ~维度数×串行 降到 ~最慢单维度。
        # 关键：每个维度提交前 copy_context()，把 LLM trace contextvar（node_id /
        # trace_id）带进 worker 线程，否则并发产生的 LLM call 会丢失 node 归属。
        def _run_dimension(dimension: CollectDimension):
            return self._collect_dimension(
                dimension=dimension,
                inp=inp,
                search_providers=search_providers,
                scrape_chain=scrape_chain,
                robots=robots,
                limiter=limiter,
            )

        dims = list(inp.dimensions)
        if len(dims) <= 1:
            results = [_run_dimension(d) for d in dims]
        else:
            contexts = [contextvars.copy_context() for _ in dims]
            with ThreadPoolExecutor(max_workers=min(len(dims), self.MAX_DIMENSION_WORKERS)) as pool:
                futures = [
                    pool.submit(ctx.run, _run_dimension, dim)
                    for dim, ctx in zip(dims, contexts, strict=True)
                ]
                # 按提交顺序取结果 → all_sources 维度顺序与串行一致（确定性）
                results = [f.result() for f in futures]

        for dim_sources, dim_errors in results:
            all_sources.extend(dim_sources)
            errors.extend(dim_errors)

        # 真实链失败时，按 fallback_to_mock 兜底
        if not all_sources and inp.constraints.fallback_to_mock:
            mock_sources = get_mock_sources(inp.product_name, inp.dimensions)
            if mock_sources:
                all_sources.extend(mock_sources)
                errors.append(
                    AgentError(
                        code="FELL_BACK_TO_MOCK",
                        message="real fetch chain yielded no sources; falling back to mock fixtures",
                        severity="warn",
                        retriable=False,
                    )
                )

        coverage = self._compute_coverage(inp.dimensions, all_sources)
        confidence = self._compute_confidence(all_sources, inp.dimensions)
        critique = self._build_self_critique(all_sources, inp.dimensions, errors)

        if not all_sources:
            status = AgentStatus.FAILED
        elif any(coverage[d] == 0 for d in inp.dimensions):
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS
        if confidence < self.SELF_CRITIQUE_THRESHOLD and status is AgentStatus.SUCCESS:
            status = AgentStatus.NEEDS_REWORK

        return CollectorOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=status,
            confidence=confidence,
            self_critique=critique,
            tokens_input=self._tokens_input_acc,
            tokens_output=self._tokens_output_acc,
            cost_usd=self._cost_usd_acc,
            duration_ms=0,
            errors=errors,
            raw_sources=all_sources,
            coverage_by_dimension=coverage,
        )

    # ----- 业务级后置校验 -----

    def _post_validate(self, out: CollectorOutput, inp: CollectorInput) -> None:
        for d in inp.dimensions:
            if d not in out.coverage_by_dimension:
                raise AgentRunError(
                    code="OUTPUT_TYPE_MISMATCH",
                    message=f"coverage_by_dimension missing key {d.value}",
                    retriable=False,
                )
        # 校验所有 RawSourceDoc 都标记了 robots_allowed（默认 True，但写入路径必须显式）
        for src in out.raw_sources:
            if src.product_name != inp.product_name:
                raise AgentRunError(
                    code="OUTPUT_TYPE_MISMATCH",
                    message=(
                        f"raw_source product_name={src.product_name!r} != input "
                        f"product_name={inp.product_name!r}"
                    ),
                    retriable=False,
                )

    # ----- 私有：单维度采集 -----

    def _collect_dimension(
        self,
        *,
        dimension: CollectDimension,
        inp: CollectorInput,
        search_providers: list[SearchProvider],
        scrape_chain: list[ScrapeProvider],
        robots: Any,
        limiter: Any,
    ) -> tuple[list[RawSourceDoc], list[AgentError]]:
        errors: list[AgentError] = []

        # REVIEWS 维度走专用路径：LLM 联网搜索（豆包 Seed EP / OpenAI web_search 等）
        # 一次性拿评分 + 典型评价 + 来源 URL，直接产出 RawSourceDoc。
        # 不依赖 Tavily/Serper（用户没 key）和 DDG（反爬严格）。
        if dimension is CollectDimension.REVIEWS:
            llm_docs, llm_errors = self._collect_reviews_via_llm(inp=inp)
            errors.extend(llm_errors)
            if llm_docs:
                for doc in llm_docs:
                    emit_collect_progress(
                        {
                            "product": inp.product_name,
                            "dimension": dimension.value,
                            "url": str(doc.source_url),
                            "title": doc.title,
                            "identity_status": doc.identity_status,
                            "detected_product_name": doc.detected_product_name,
                            "source_authority": doc.source_authority,
                        }
                    )
                return llm_docs, errors
            # LLM 路径未拿到结果，落到下面的"搜索 + scrape"通用路径（host seed 兜底）

        searched = self._search(
            search_providers=search_providers,
            product_name=inp.product_name,
            dimension=dimension,
        )
        seeded = _seed_from_official_url(
            official_url=inp.official_url,
            product_name=inp.product_name,
            dimension=dimension,
        )
        # REVIEWS 维度额外加 host-level seed（G2/Capterra/TrustRadius）
        if dimension is CollectDimension.REVIEWS:
            seeded = seeded + _seed_review_hosts(inp.product_name)
        # 合并去重：搜索结果优先（已带 snippet），official_url 兜底补充
        candidates = _dedupe_hits(searched + seeded)
        # REVIEWS 维度：**指定**只从评论站（_REVIEW_HOSTS）采集——通用搜索常把 YouTube /
        # 博客顶上来，抓到的是平台框架文本（既没用、又被身份校验判成「别的产品」如 'YouTube'）。
        # 用 allowlist 指定可信来源，而非黑名单逐个挡。过滤后为空则照常 NO_RELEVANT_RESULTS
        # （_seed_review_hosts 总会注入 G2/Capterra，正常产品不会被清空）。
        if dimension is CollectDimension.REVIEWS:
            review_only = [c for c in candidates if _is_review_host(c.url)]
            dropped = len(candidates) - len(review_only)
            if dropped > 0:
                errors.append(
                    AgentError(
                        code="NON_REVIEW_SOURCE_SKIPPED",
                        message=(
                            f"reviews: dropped {dropped} non-review-site candidate(s) "
                            "(only G2/Capterra/TrustRadius… are used for reviews)"
                        ),
                        severity="warn",
                        retriable=False,
                    )
                )
            candidates = review_only
        if not candidates:
            errors.append(
                AgentError(
                    code="NO_RELEVANT_RESULTS",
                    message=(
                        f"no candidates for dimension={dimension.value} "
                        f"(search empty and no official_url seed)"
                    ),
                    severity="warn",
                    retriable=True,
                )
            )
            return [], errors
        if not searched:
            # 搜索全空但还有 seed 候选，记一条降级痕迹，不阻塞
            errors.append(
                AgentError(
                    code="NO_RELEVANT_RESULTS",
                    message=(
                        f"search returned 0 results for dimension={dimension.value}; "
                        f"falling back to official_url seeds ({len(seeded)} candidates)"
                    ),
                    severity="warn",
                    retriable=False,
                )
            )

        ranked = self._rank(
            hits=candidates,
            product_name=inp.product_name,
            official_url=inp.official_url,
            dimension=dimension,
        )
        kept_sources: list[RawSourceDoc] = []
        # final_url 级别的二次去重：candidates 可能因为 301/302 redirect 落到同一页面，
        # 例如 notion.so/ → notion.com/。在 fetch 完成后用 ScrapeResult.final_url 判重。
        fetched_finals: set[str] = set()
        budget = max(inp.constraints.max_pages_per_dimension, 1)
        # P4 返工收敛：QA 标 identity mismatch 的源 URL 经 qa_feedback 注入到
        # inp.exclude_source_urls；重采时直接跳过，避免又把同一个跑题页面抓回来。
        exclude = {u.rstrip("/") for u in (inp.exclude_source_urls or []) if u}
        for hit, _score in ranked:
            if len(kept_sources) >= budget:
                break
            url = hit.url
            if exclude and url.rstrip("/") in exclude:
                errors.append(
                    AgentError(
                        code="NO_RELEVANT_RESULTS",
                        message=f"skip QA-flagged mismatch url {url}",
                        severity="warn",
                        retriable=False,
                    )
                )
                continue
            host = _host_of(url)
            if inp.constraints.respect_robots_txt and robots is not None:
                try:
                    allowed = bool(robots.is_allowed(url))
                except Exception:
                    allowed = True
                if not allowed:
                    errors.append(
                        AgentError(
                            code="ROBOTS_BLOCKED",
                            message=f"robots.txt disallows {url}",
                            severity="warn",
                            retriable=False,
                        )
                    )
                    continue
            if limiter is not None and host:
                try:
                    limiter.acquire(host)
                except Exception:
                    pass
            scrape_result, used = self._scrape(url, chain=scrape_chain)
            if scrape_result is None or scrape_result.error or not scrape_result.text:
                errors.append(
                    AgentError(
                        code="TOOL_FAILED",
                        message=(
                            f"scrape failed for {url}: "
                            f"{scrape_result.error if scrape_result else 'no_provider'}"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )
                continue
            # 301/302 后落到同一 final_url 的，第二次直接跳过
            final_url_normalized = scrape_result.final_url.rstrip("/")
            if final_url_normalized in fetched_finals:
                continue
            # redirect 后才落到被 QA 标记的 mismatch URL → 同样跳过（P4）
            if exclude and final_url_normalized in exclude:
                continue
            fetched_finals.add(final_url_normalized)
            if scrape_result.detected_paywall and not inp.constraints.allow_paid_content:
                errors.append(
                    AgentError(
                        code="PAYWALL_DETECTED",
                        message=f"paywall blocks content at {url}",
                        severity="warn",
                        retriable=False,
                    )
                )
                continue
            # 维度复核
            detected_dim, _conf = self._classify_page(
                url=url, title=scrape_result.title, text=scrape_result.text
            )
            if detected_dim is not None and detected_dim != dimension:
                # 维度不符 → 跳过，记 warn，不致命
                errors.append(
                    AgentError(
                        code="NO_RELEVANT_RESULTS",
                        message=(
                            f"page at {url} classified as {detected_dim.value}, "
                            f"requested {dimension.value}"
                        ),
                        severity="warn",
                        retriable=False,
                    )
                )
                continue
            # 身份校验：这页内容真的是目标产品吗（防抓到别的产品的评价/功能）
            detected_name, identity_conf, identity_status = self._assess_identity(
                inp=inp, scrape=scrape_result
            )
            # 相对语义：评论聚合站页面在 user_reviews 维度是**正典源**——G2 上的
            # 「{product} reviews」天然就是讲该产品；启发式因「非官方域名」把它停在
            # ambiguous 是范畴错误。这里视作 confirmed（仅升级 ambiguous，不动 LLM
            # 实判的 mismatch=确属别的产品）。
            if (
                dimension is CollectDimension.REVIEWS
                and identity_status == "ambiguous"
                and _is_review_host(url)
            ):
                identity_status = "confirmed"
                detected_name = detected_name or inp.product_name
                identity_conf = max(identity_conf or 0.0, 0.8)
            if identity_status == "mismatch":
                errors.append(
                    AgentError(
                        code="NO_RELEVANT_RESULTS",
                        message=(
                            f"identity mismatch at {url}: content looks like "
                            f"{detected_name!r}, not {inp.product_name!r}"
                        ),
                        severity="warn",
                        retriable=False,
                    )
                )
            try:
                doc = self._build_raw_source_doc(
                    inp=inp,
                    dimension=dimension,
                    scrape=scrape_result,
                    fetch_method=used,
                    detected_product_name=detected_name,
                    identity_confidence=identity_conf,
                    identity_status=identity_status,
                )
            except ValidationError as e:
                errors.append(
                    AgentError(
                        code="LLM_SCHEMA_INVALID",
                        message=f"failed to build RawSourceDoc for {url}: {e}",
                        severity="error",
                        retriable=False,
                    )
                )
                continue
            kept_sources.append(doc)
            # 实时进度：每抓+校验完一条来源就推一条事件（含身份判定），
            # 让前端「边采边看」、身份 mismatch 当场可见。无 emitter 时 no-op。
            emit_collect_progress(
                {
                    "product": inp.product_name,
                    "dimension": dimension.value,
                    "url": str(doc.source_url),
                    "title": doc.title,
                    "identity_status": doc.identity_status,
                    "detected_product_name": doc.detected_product_name,
                    "source_authority": doc.source_authority,
                }
            )
        return kept_sources, errors

    # ----- 搜索 -----

    def _search(
        self,
        *,
        search_providers: list[SearchProvider],
        product_name: str,
        dimension: CollectDimension,
    ) -> list[SearchHit]:
        keyword = DIMENSION_QUERY_KEYWORD[dimension]
        query = f"{product_name} {keyword}"
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for sp in search_providers:
            try:
                provider_hits = sp.search(query, max_results=10)
            except Exception:
                provider_hits = []
            for h in provider_hits:
                if not h.url or h.url in seen:
                    continue
                seen.add(h.url)
                hits.append(h)
            if hits:
                # 拿到结果就停，避免烧两份配额；后续也可改成累积+去重
                break
        return hits

    # ----- 排序：LLM > 启发式 -----

    def _rank(
        self,
        *,
        hits: list[SearchHit],
        product_name: str,
        official_url: str | None,
        dimension: CollectDimension,
    ) -> list[tuple[SearchHit, float]]:
        if self.llm is not None:
            ranked = self._llm_rank(
                hits=hits,
                product_name=product_name,
                official_url=official_url,
                dimension=dimension,
            )
            if ranked:
                return ranked
        return _heuristic_rank(
            hits=hits,
            product_name=product_name,
            official_url=official_url,
            dimension=dimension,
        )

    def _llm_rank(
        self,
        *,
        hits: list[SearchHit],
        product_name: str,
        official_url: str | None,
        dimension: CollectDimension,
    ) -> list[tuple[SearchHit, float]]:
        if self.llm is None:
            return []
        prompt = (PROMPT_DIR / "url_ranker.md").read_text(encoding="utf-8")
        system, user_template = _split_prompt(prompt)
        user = _render(
            user_template,
            product_name=product_name,
            official_url=official_url,
            dimension=dimension.value,
            candidates=[{"url": h.url, "title": h.title} for h in hits],
        )
        try:
            resp = self.llm.chat(
                system=system,
                messages=[{"role": "user", "content": user}],
                response_format=_UrlRankingList,
                temperature=0.1,
                max_tokens=1500,
            )
            self._record_llm_usage(resp)
            parsed = _coerce_pydantic(resp, _UrlRankingList)
        except Exception:
            return []
        by_url = {h.url: h for h in hits}
        scored: list[tuple[SearchHit, float]] = []
        for r in parsed.rankings:
            if r.url in by_url:
                scored.append((by_url[r.url], r.score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ----- 抓取链 -----

    def _scrape(
        self,
        url: str,
        *,
        chain: list[ScrapeProvider],
    ) -> tuple[ScrapeResult | None, Literal["firecrawl", "playwright", "mock", "manual"]]:
        last: ScrapeResult | None = None
        for sp in chain:
            try:
                result = sp.scrape(url)
            except Exception as e:
                result = ScrapeResult(
                    url=url,
                    final_url=url,
                    http_status=None,
                    fetched_with=sp.name,
                    error=f"{type(e).__name__}: {e}",
                )
            last = result
            if result.error is None and result.text:
                used: Literal["firecrawl", "playwright", "mock", "manual"]
                if "firecrawl" in sp.name:
                    used = "firecrawl"
                elif "playwright" in sp.name:
                    used = "playwright"
                else:
                    used = "manual"
                return result, used
        return last, "firecrawl"

    # ----- 页面类型分类 -----

    def _classify_page(
        self,
        *,
        url: str,
        title: str | None,
        text: str,
    ) -> tuple[CollectDimension | None, float]:
        if self.llm is not None:
            try:
                cls = self._llm_classify(url=url, title=title, text=text)
            except Exception:
                cls = None
            if cls is not None:
                try:
                    return CollectDimension(cls.page_type), cls.confidence
                except ValueError:
                    return None, cls.confidence
        return _heuristic_classify(url, title)

    # ----- 产品身份校验（混合：启发式 gate + 模糊时 LLM 裁定） -----

    IDENTITY_LLM_CONFIRM_MIN: ClassVar[float] = 0.6

    def _take_identity_llm_budget(self) -> bool:
        """领一次身份校验 LLM 预算（线程安全）。耗尽返回 False → 退回启发式。"""
        with self._usage_lock:
            if self._identity_llm_left <= 0:
                return False
            self._identity_llm_left -= 1
            return True

    def _assess_identity(
        self, *, inp: CollectorInput, scrape: ScrapeResult
    ) -> tuple[str | None, float | None, IdentityStatus]:
        """判断抓到的页面是否真属于 ``inp.product_name``。

        返回 ``(detected_product_name, identity_confidence, identity_status)``，
        直接灌进 RawSourceDoc 的三个身份字段。

        混合策略：
        1. 启发式 gate 先过滤——官方域名 / 标题+正文强命中 → 直接 confirmed，不烧 LLM。
        2. 仅当启发式无法确证（第三方站 / 对比页 / 跨语言别名漏命中）才调一次 LLM 裁定。
        3. 无 LLM 或 LLM 失败 → 保守地停在 ambiguous（绝不靠启发式硬判 mismatch）。
        """
        url = scrape.final_url or scrape.url
        status, conf, detected, decided = _assess_identity_heuristic(
            text=scrape.text,
            title=scrape.title,
            url=url,
            product_name=inp.product_name,
            official_url=inp.official_url,
        )
        if decided:
            return detected, conf, status

        if self.llm is not None and self._take_identity_llm_budget():
            try:
                res = self._llm_identity(
                    product_name=inp.product_name,
                    title=scrape.title,
                    text=scrape.text,
                )
            except Exception:
                res = None
            if res is not None:
                detected = (res.detected_product_name or "").strip() or None
                c = float(res.confidence)
                if res.is_target_product:
                    # identity_confidence = 「确属目标产品」的置信度 = LLM 对该判断的置信
                    if c >= self.IDENTITY_LLM_CONFIRM_MIN:
                        return (detected or inp.product_name), round(c, 3), "confirmed"
                    return detected, round(c, 3), "ambiguous"
                # 判为「不是目标产品」：确属目标的置信度 = 1 - c（低）
                if c >= self.IDENTITY_LLM_CONFIRM_MIN:
                    return detected, round(1.0 - c, 3), "mismatch"
                return detected, round(1.0 - c, 3), "ambiguous"

        # 无 LLM / 裁定失败：保守停在启发式给的 ambiguous
        return detected, conf, status

    def _llm_identity(
        self, *, product_name: str, title: str | None, text: str
    ) -> _IdentityCheck | None:
        if self.llm is None:
            return None
        prompt = (PROMPT_DIR / "identity_validator.md").read_text(encoding="utf-8")
        system, user_template = _split_prompt(prompt)
        user = _render(
            user_template,
            product_name=product_name,
            title=title,
            text_preview=text[:1500],
        )
        resp = self.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_IdentityCheck,
            temperature=0.0,
            max_tokens=300,
        )
        self._record_llm_usage(resp)
        return _coerce_pydantic(resp, _IdentityCheck)

    # ----- REVIEWS 维度：LLM 联网搜索路径 -----

    def _collect_reviews_via_llm(
        self, *, inp: CollectorInput
    ) -> tuple[list[RawSourceDoc], list[AgentError]]:
        """对 REVIEWS 维度调一次 LLM（依赖 provider 内置联网搜索），把结果
        转成多条 RawSourceDoc 直接返回。无 LLM 或失败时返回空列表，由上层兜底。
        """
        errors: list[AgentError] = []
        if self.llm is None:
            return [], errors  # 不报错，落到 search + seed 兜底路径

        try:
            prompt = (PROMPT_DIR / "reviews_finder.md").read_text(encoding="utf-8")
        except OSError as e:
            errors.append(
                AgentError(
                    code="TOOL_FAILED",
                    message=f"reviews_finder.md unreadable: {e}",
                    severity="error",
                    retriable=False,
                )
            )
            return [], errors

        system, user_template = _split_prompt(prompt)

        # QA 反馈块：QA 标 freshness / evidence_completeness 时，本节点会以
        # ``collect.<product>.reviews_v{n+1}`` 形式重跑，inp.qa_feedback 非空。
        # 把上一轮 issue 渲染进 prompt，提示 LLM 改变搜索策略（如换 fresher 来源、
        # 避开被 disputed 的 source）。
        from backend.agents._qa_feedback import render_qa_feedback_block

        qa_block = render_qa_feedback_block(
            inp.qa_feedback,
            closing_instruction=(
                "Apply the fixes above when re-collecting reviews: if a source "
                "was flagged stale or disputed, prefer alternative review sites "
                "or newer dates; if a specific dimension was reported missing, "
                "expand coverage to surface relevant quotes. Do NOT re-emit "
                "the exact URLs / sources that QA flagged."
            ),
        )

        user = _render(
            user_template,
            product_name=inp.product_name,
            qa_feedback_block=qa_block,
        )
        try:
            resp = self.llm.chat(
                system=system,
                messages=[{"role": "user", "content": user}],
                response_format=_ReviewsFinding,
                temperature=0.2,
                max_tokens=1500,
            )
            self._record_llm_usage(resp)
            finding = _coerce_pydantic(resp, _ReviewsFinding)
        except Exception as e:
            errors.append(
                AgentError(
                    code="TOOL_FAILED",
                    message=f"reviews_finder llm call failed: {type(e).__name__}: {e}",
                    severity="warn",
                    retriable=True,
                )
            )
            return [], errors

        if finding is None:
            errors.append(
                AgentError(
                    code="LLM_SCHEMA_INVALID",
                    message="reviews_finder returned unparseable response",
                    severity="warn",
                    retriable=True,
                )
            )
            return [], errors

        # finding 拿到了，但内容可能完全空（小众产品 LLM 也没搜到）
        if not finding.sources and finding.overall_rating is None:
            errors.append(
                AgentError(
                    code="NO_RELEVANT_RESULTS",
                    message=(f"LLM web search yielded no review data for {inp.product_name}"),
                    severity="warn",
                    retriable=True,
                )
            )
            return [], errors

        docs = self._reviews_finding_to_docs(inp=inp, finding=finding)
        if not docs:
            errors.append(
                AgentError(
                    code="NO_RELEVANT_RESULTS",
                    message=(
                        f"LLM returned reviews finding but all source URLs invalid for "
                        f"{inp.product_name}"
                    ),
                    severity="warn",
                    retriable=False,
                )
            )
        return docs, errors

    def _reviews_finding_to_docs(
        self,
        *,
        inp: CollectorInput,
        finding: _ReviewsFinding,
    ) -> list[RawSourceDoc]:
        """把 LLM 的 _ReviewsFinding 转成 RawSourceDoc 列表。

        - 有 sources：每个 source 一条 RawSourceDoc，raw_text 含该来源摘要 +
          全局 themes/quotes/rating。
        - 没 sources 但有 overall_rating：造一条聚合 doc，source_url 用第一个
          可用的 review host（G2）作为代表，标记 fetch_method='search'。
        """
        docs: list[RawSourceDoc] = []
        common_lines: list[str] = []
        if finding.overall_rating is not None:
            common_lines.append(f"Overall rating: {finding.overall_rating:.1f}/5")
        if finding.review_count:
            common_lines.append(f"Review count: {finding.review_count}")
        if finding.positive_themes:
            common_lines.append("Positive themes: " + "; ".join(finding.positive_themes))
        if finding.negative_themes:
            common_lines.append("Negative themes: " + "; ".join(finding.negative_themes))
        if finding.sample_quotes:
            common_lines.append(
                "Sample reviews:\n" + "\n".join(f"- {q}" for q in finding.sample_quotes)
            )
        common_text = "\n".join(common_lines)

        sources_to_emit = finding.sources or [
            _ReviewSource(
                name="aggregated",
                url=f"https://www.g2.com/products/{_product_slug(inp.product_name)}/reviews",
                excerpt="LLM-synthesized aggregate; no individual source URL provided.",
            )
        ]

        for src in sources_to_emit:
            text = (
                f"Source: {src.name}\n{src.excerpt}\n{common_text}"
                if common_text
                else f"Source: {src.name}\n{src.excerpt}"
            )
            # 只跳过完全空白的；短文本仍让下游评估
            if not text.strip():
                continue
            try:
                source_id = (
                    "src_"
                    + hashlib.sha1(
                        f"{inp.product_name}|reviews|{src.url}|{inp.task_id}".encode()
                    ).hexdigest()[:12]
                )
                _taint = _scan_injection(text)
                doc = RawSourceDoc(
                    source_id=source_id,
                    product_name=inp.product_name,
                    dimension=CollectDimension.REVIEWS,
                    source_url=src.url,
                    source_type="user_reviews",
                    title=f"{inp.product_name} reviews on {src.name}",
                    raw_html=None,
                    raw_text=text,
                    summary=None,
                    language="en",
                    collected_at=datetime.now(tz=UTC),
                    # LLM 联网搜索的产物归类为 "search"：契约层面与 Tavily / Serper
                    # 等"搜索引擎候选 URL"一致语义。
                    fetch_method="search",
                    http_status=None,
                    robots_allowed=True,
                    # 相对语义：评论站在 user_reviews 维度是**正典源**（高权威），
                    # 不再沿用「评论站一律低于官方页」的绝对标量。
                    source_authority=authority_for("review", CollectDimension.REVIEWS),
                    detected_paywall=False,
                    detected_outdated=False,
                    # LLM 联网搜索是按「{product} reviews」定向找的，身份默认成立
                    detected_product_name=inp.product_name,
                    identity_confidence=0.85,
                    identity_status="confirmed",
                    source_class="review",
                    trust_level="untrusted",
                    tainted=_taint.tainted,
                    taint_reasons=_taint.matched_patterns,
                )
                docs.append(doc)
            except ValidationError:
                # URL 不合法 / 字段约束失败 → 跳过这一条，不阻塞其他来源
                continue
        return docs

    def _llm_classify(
        self, *, url: str, title: str | None, text: str
    ) -> _PageTypeClassification | None:
        if self.llm is None:
            return None
        prompt = (PROMPT_DIR / "page_type_classifier.md").read_text(encoding="utf-8")
        system, user_template = _split_prompt(prompt)
        user = _render(
            user_template,
            url=url,
            title=title,
            text_preview=text[:1500],
            claimed_dimension="(请基于内容独立判断)",
        )
        resp = self.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_PageTypeClassification,
            temperature=0.0,
            max_tokens=300,
        )
        self._record_llm_usage(resp)
        return _coerce_pydantic(resp, _PageTypeClassification)

    # ----- RawSourceDoc 构造 -----

    def _build_raw_source_doc(
        self,
        *,
        inp: CollectorInput,
        dimension: CollectDimension,
        scrape: ScrapeResult,
        fetch_method: Literal["firecrawl", "playwright", "mock", "manual"],
        detected_product_name: str | None = None,
        identity_confidence: float | None = None,
        identity_status: IdentityStatus = "unvalidated",
    ) -> RawSourceDoc:
        url = scrape.final_url or scrape.url
        source_id = (
            "src_"
            + hashlib.sha1(
                f"{inp.product_name}|{dimension.value}|{url}|{inp.task_id}".encode()
            ).hexdigest()[:12]
        )
        outdated = scrape.detected_outdated
        taint = _scan_injection(scrape.text)
        return RawSourceDoc(
            source_id=source_id,
            product_name=inp.product_name,
            dimension=dimension,
            source_url=url,
            source_type="html",
            title=scrape.title,
            raw_html=None,  # 大字段不入消息体，按 docs 进对象存储
            raw_text=scrape.text,
            summary=None,
            language="en" if all(ord(c) < 128 for c in scrape.text[:200]) else "zh",
            collected_at=datetime.now(tz=UTC),
            fetch_method=fetch_method,
            http_status=scrape.http_status,
            robots_allowed=True,
            source_authority=_heuristic_authority(
                url=url,
                product_name=inp.product_name,
                official_url=inp.official_url,
                dimension=dimension,
            ),
            detected_paywall=scrape.detected_paywall,
            detected_outdated=outdated,
            detected_product_name=detected_product_name,
            identity_confidence=identity_confidence,
            identity_status=identity_status,
            source_class=_source_class(url, inp.product_name, inp.official_url),
            trust_level="untrusted",
            tainted=taint.tainted,
            taint_reasons=taint.matched_patterns,
        )

    # ----- 收尾：confidence / critique / coverage -----

    @staticmethod
    def _compute_coverage(
        dimensions: list[CollectDimension], sources: list[RawSourceDoc]
    ) -> dict[CollectDimension, int]:
        coverage = {d: 0 for d in dimensions}
        for src in sources:
            if src.dimension in coverage:
                coverage[src.dimension] += 1
        return coverage

    def _is_usable(self, s: RawSourceDoc) -> bool:
        """该源是否提供了**可用内容**（用于「可用覆盖」判定）。

        评论聚合站在 user_reviews 维度是预期源——付费墙(需登录)/短文本是其固有特征，
        视为可用（相对语义豁免）。其余：短文本/付费墙/抓错产品(mismatch) → 不可用。
        """
        if s.dimension is CollectDimension.REVIEWS and _is_review_host(str(s.source_url)):
            return True
        if _short_text(s.raw_text):
            return False
        if s.detected_paywall:
            return False
        if s.identity_status == "mismatch":
            return False
        return True

    def _compute_confidence(
        self,
        sources: list[RawSourceDoc],
        dimensions: list[CollectDimension],
    ) -> float:
        """置信度按**可用覆盖**判，而非「失败页数」累加。

        关键修正：个别页付费墙/短文本/robots 是**抓取环境失败**，重采大概率同样失败；
        只要该维度还有别的**可用**源，就不该因这些失败把置信压低、把整次采集冤判
        NEEDS_REWORK（空转返工）。故只扣两类**实质缺口**：
        - 某维度无任何可用源（完全没采到，或采到的全是短文本/付费墙/抓错产品）；
        - 身份 mismatch（抓到别的产品，actionable，足量 → NEEDS_REWORK）。
        """
        if not sources:
            return 0.0
        score = self.BASE_CONFIDENCE
        usable_by_dim = {d: 0 for d in dimensions}
        for s in sources:
            if s.dimension in usable_by_dim and self._is_usable(s):
                usable_by_dim[s.dimension] += 1
        gap = sum(1 for d in dimensions if usable_by_dim[d] == 0)
        score -= gap * self.PENALTY_EMPTY_DIMENSION

        mismatch = sum(1 for s in sources if s.identity_status == "mismatch")
        score -= mismatch * self.PENALTY_IDENTITY_MISMATCH

        return max(0.0, min(1.0, score))

    def _build_self_critique(
        self,
        sources: list[RawSourceDoc],
        dimensions: list[CollectDimension],
        errors: list[AgentError],
    ) -> str:
        coverage = self._compute_coverage(dimensions, sources)
        # 分两类：**需处理**（真缺口，可能值得返工）vs **采集受限**（环境失败，重采多半同样
        # 失败、通常无需返工）。让用户一眼分清「该管的」和「噪音」。
        actionable: list[str] = []
        env: list[str] = []

        empty = [d.value for d in dimensions if coverage[d] == 0]
        if empty:
            actionable.append(f"未采集到维度: {', '.join(empty)}")
        mismatch = [s for s in sources if s.identity_status == "mismatch"]
        if mismatch:
            actionable.append(
                f"身份不符(疑似抓到别的产品): {len(mismatch)} 个页面，"
                f"例如 {mismatch[0].detected_product_name!r}"
            )
        # 维度有源但**无任何可用源**（全是短/墙/抓错）→ 实质缺口，归「需处理」
        thin = [
            d.value
            for d in dimensions
            if coverage[d] > 0 and not any(s.dimension is d and self._is_usable(s) for s in sources)
        ]
        if thin:
            actionable.append(f"维度有源但无可用内容(全部抓取受限): {', '.join(thin)}")

        paywall = [s for s in sources if s.detected_paywall]
        if paywall:
            env.append(f"付费墙阻挡: {len(paywall)} 个页面")
        short = [s for s in sources if _short_text(s.raw_text)]
        if short:
            env.append(f"正文过短(<200 字符): {len(short)} 个页面，可能抓取失败")
        blocked = [s for s in sources if not s.robots_allowed]
        if blocked:
            env.append(f"robots.txt 禁止抓取: {len(blocked)} 个页面")
        ambiguous = [s for s in sources if s.identity_status == "ambiguous"]
        if ambiguous:
            env.append(f"身份存疑(无法确证属目标产品): {len(ambiguous)} 个页面")
        warn_codes = sorted({e.code for e in errors if e.severity in ("warn", "error")})
        if warn_codes:
            env.append(f"过程告警: {', '.join(warn_codes)}")

        parts: list[str] = []
        if actionable:
            parts.append("需处理: " + " | ".join(actionable))
        if env:
            parts.append("采集受限(已跳过,通常无需返工): " + " | ".join(env))
        if not parts:
            return f"采集正常完成，共 {len(sources)} 个页面，覆盖维度 {len(dimensions)}/{len(dimensions)}。"
        return "  ".join(parts)

    # ----- 工具注入辅助 -----

    def _collect_enabled(self, names: tuple[str, ...], _expected: type[Any]) -> list[Any]:
        """从 registry 拉一组工具，过滤掉 enabled=False 的。"""
        out: list[Any] = []
        if self.tools is None:
            return out
        for name in names:
            if not self.tools.has(name):
                continue
            tool = self.tools.get(name)
            if getattr(tool, "enabled", True):
                out.append(tool)
        return out


# ---------- prompt 渲染辅助（Jinja2 子集，避免拉重依赖） ----------


def _split_prompt(prompt: str) -> tuple[str, str]:
    """Markdown prompt 拆 System / User 两段。"""
    sys_marker = "## System"
    usr_marker = "## User"
    si = prompt.find(sys_marker)
    ui = prompt.find(usr_marker)
    if si < 0 or ui < 0 or ui < si:
        return prompt.strip(), ""
    system = prompt[si + len(sys_marker) : ui].strip()
    user = prompt[ui + len(usr_marker) :].strip()
    return system, user


def _render(template: str, **vars: Any) -> str:
    """极简 Jinja2 子集：仅替换 {{ var }} 与简单 for 循环。

    Collector 三个 prompt 都只用了 {{ var }} 与 candidates 的 {% for %} 块，所以足够。
    """
    import re

    # 处理 {% for u in candidates %} ... {% endfor %}
    for_block = re.compile(
        r"{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%}(.*?){%\s*endfor\s*%}",
        re.DOTALL,
    )

    def expand_for(match: re.Match[str]) -> str:
        var_name, iter_name, body = match.group(1), match.group(2), match.group(3)
        items = vars.get(iter_name) or []
        chunks: list[str] = []
        for item in items:
            chunks.append(_render_simple(body, {var_name: item, **vars}))
        return "".join(chunks)

    rendered = for_block.sub(expand_for, template)
    return _render_simple(rendered, vars)


def _render_simple(template: str, vars: dict[str, Any]) -> str:
    import re

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        # 支持 "a or b" 简单语法
        if " or " in expr:
            left, right = (s.strip() for s in expr.split(" or ", 1))
            value = _resolve(left, vars)
            if not value:
                value = (
                    _resolve(right.strip("\"' "), vars)
                    if not (right.startswith('"') or right.startswith("'"))
                    else right.strip("\"'")
                )
            return str(value if value is not None else "")
        value = _resolve(expr, vars)
        return "" if value is None else str(value)

    return re.sub(r"{{\s*(.+?)\s*}}", repl, template)


def _resolve(expr: str, vars: dict[str, Any]) -> Any:
    """支持 a.b 取属性 / dict 取键。"""
    parts = expr.split(".")
    head = vars.get(parts[0])
    if head is None:
        return None
    cur: Any = head
    for p in parts[1:]:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            cur = getattr(cur, p, None)
        if cur is None:
            return None
    return cur


def _coerce_pydantic(resp: Any, model: type[BaseModel]) -> Any:
    """把 LLM 返回值尽量转成 model 实例。兼容多种 provider 形态。"""
    if isinstance(resp, model):
        return resp
    if hasattr(resp, "parsed") and isinstance(resp.parsed, model):
        return resp.parsed
    if hasattr(resp, "parsed") and isinstance(resp.parsed, dict):
        return model.model_validate(resp.parsed)
    if isinstance(resp, dict):
        return model.model_validate(resp)
    if hasattr(resp, "model_dump"):
        return model.model_validate(resp.model_dump())
    raise ValueError(f"cannot coerce LLM response to {model.__name__}: {type(resp).__name__}")


__all__ = ["Collector"]
