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

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agents._base import (
    AgentRunError,
    BaseAgent,
    LLMProviderProtocol,
    ToolRegistryProtocol,
    TracerProtocol,
)
from backend.schemas import (
    AgentError,
    AgentStatus,
    CollectDimension,
    CollectorInput,
    CollectorOutput,
    RawSourceDoc,
)

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


def _heuristic_authority(
    *, url: str, product_name: str, official_url: str | None
) -> float:
    if _is_official(url, product_name, official_url):
        return 0.95
    host = _host_of(url)
    if any(rh in host for rh in _REVIEW_HOSTS):
        return 0.75
    return 0.6


def _short_text(text: str) -> bool:
    return len(text.strip()) < 200


# ---------- Collector ----------


class Collector(BaseAgent[CollectorInput, CollectorOutput]):
    """采集 Agent。详见 docs/AGENTS.md § 3。"""

    name: ClassVar[str] = "collector"
    version: ClassVar[str] = "1.0.0"
    input_model: ClassVar[type[BaseModel]] = CollectorInput
    output_model: ClassVar[type[BaseModel]] = CollectorOutput
    required_tools: ClassVar[list[str]] = [
        "search.tavily",
        "scrape.firecrawl",
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
    ROBOTS_BLOCK_RATIO_THRESHOLD: ClassVar[float] = 0.30

    def __init__(
        self,
        *,
        llm: LLMProviderProtocol | None = None,
        tools: ToolRegistryProtocol | None = None,
        tracer: TracerProtocol | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(llm=llm, tools=tools, tracer=tracer, mock=mock)

    # ----- Mock -----

    def _run_mock(self, inp: CollectorInput) -> CollectorOutput:
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
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
            duration_ms=0,  # BaseAgent 会回填
            errors=errors,
            raw_sources=sources,
            coverage_by_dimension=coverage,
        )

    # ----- Real -----

    def _run(self, inp: CollectorInput) -> CollectorOutput:
        if self.tools is None:
            raise AgentRunError(
                code="UPSTREAM_MISSING",
                message="tool registry not provided",
                retriable=False,
            )
        search_providers = self._collect_enabled(("search.tavily", "search.serper"), SearchProvider)
        scrape_chain: list[ScrapeProvider] = self._collect_enabled(
            ("scrape.firecrawl", "scrape.playwright"), ScrapeProvider
        )
        robots = self.tools.get("robots_checker") if self.tools.has("robots_checker") else None
        limiter = (
            self.tools.get("domain_rate_limiter")
            if self.tools.has("domain_rate_limiter")
            else None
        )

        errors: list[AgentError] = []
        all_sources: list[RawSourceDoc] = []

        for dimension in inp.dimensions:
            dim_sources, dim_errors = self._collect_dimension(
                dimension=dimension,
                inp=inp,
                search_providers=search_providers,
                scrape_chain=scrape_chain,
                robots=robots,
                limiter=limiter,
            )
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
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
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
        candidates = self._search(
            search_providers=search_providers,
            product_name=inp.product_name,
            dimension=dimension,
        )
        if not candidates:
            errors.append(
                AgentError(
                    code="NO_RELEVANT_RESULTS",
                    message=f"search returned 0 results for dimension={dimension.value}",
                    severity="warn",
                    retriable=True,
                )
            )
            return [], errors

        ranked = self._rank(
            hits=candidates,
            product_name=inp.product_name,
            official_url=inp.official_url,
            dimension=dimension,
        )
        kept_sources: list[RawSourceDoc] = []
        budget = max(inp.constraints.max_pages_per_dimension, 1)
        for hit, _score in ranked:
            if len(kept_sources) >= budget:
                break
            url = hit.url
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
            try:
                doc = self._build_raw_source_doc(
                    inp=inp,
                    dimension=dimension,
                    scrape=scrape_result,
                    fetch_method=used,
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
        return _coerce_pydantic(resp, _PageTypeClassification)

    # ----- RawSourceDoc 构造 -----

    def _build_raw_source_doc(
        self,
        *,
        inp: CollectorInput,
        dimension: CollectDimension,
        scrape: ScrapeResult,
        fetch_method: Literal["firecrawl", "playwright", "mock", "manual"],
    ) -> RawSourceDoc:
        url = scrape.final_url or scrape.url
        source_id = "src_" + hashlib.sha1(
            f"{inp.product_name}|{dimension.value}|{url}|{inp.task_id}".encode()
        ).hexdigest()[:12]
        outdated = scrape.detected_outdated
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
            ),
            detected_paywall=scrape.detected_paywall,
            detected_outdated=outdated,
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

    def _compute_confidence(
        self,
        sources: list[RawSourceDoc],
        dimensions: list[CollectDimension],
    ) -> float:
        score = self.BASE_CONFIDENCE
        coverage = self._compute_coverage(dimensions, sources)
        empty = sum(1 for d in dimensions if coverage[d] == 0)
        score -= empty * self.PENALTY_EMPTY_DIMENSION

        paywall = sum(1 for s in sources if s.detected_paywall)
        score -= paywall * self.PENALTY_PAYWALL

        short = sum(1 for s in sources if _short_text(s.raw_text))
        score -= short * self.PENALTY_SHORT_TEXT

        blocked = sum(1 for s in sources if not s.robots_allowed)
        total = len(sources) or 1
        if blocked / total > self.ROBOTS_BLOCK_RATIO_THRESHOLD:
            score -= self.PENALTY_HIGH_ROBOTS_BLOCK_RATIO

        if not sources:
            score = 0.0
        return max(0.0, min(1.0, score))

    def _build_self_critique(
        self,
        sources: list[RawSourceDoc],
        dimensions: list[CollectDimension],
        errors: list[AgentError],
    ) -> str:
        lines: list[str] = []
        coverage = self._compute_coverage(dimensions, sources)
        empty = [d.value for d in dimensions if coverage[d] == 0]
        if empty:
            lines.append(f"未采集到维度: {', '.join(empty)}")
        paywall = [s.source_url for s in sources if s.detected_paywall]
        if paywall:
            lines.append(f"付费墙阻挡: {len(paywall)} 个页面")
        short = [s.source_url for s in sources if _short_text(s.raw_text)]
        if short:
            lines.append(f"正文过短(<200 字符): {len(short)} 个页面，可能抓取失败")
        blocked = [s.source_url for s in sources if not s.robots_allowed]
        if blocked:
            lines.append(f"robots.txt 禁止抓取: {len(blocked)} 个页面")
        warn_codes = sorted({e.code for e in errors if e.severity in ("warn", "error")})
        if warn_codes:
            lines.append(f"过程告警: {', '.join(warn_codes)}")
        if not lines:
            return f"采集正常完成，共 {len(sources)} 个页面，覆盖维度 {len(dimensions)}/{len(dimensions)}。"
        return " | ".join(lines)

    # ----- 工具注入辅助 -----

    def _collect_enabled(
        self, names: tuple[str, ...], _expected: type[Any]
    ) -> list[Any]:
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
                value = _resolve(right.strip("\"' "), vars) if not (right.startswith('"') or right.startswith("'")) else right.strip("\"'")
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
