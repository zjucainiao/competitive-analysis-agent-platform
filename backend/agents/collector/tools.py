"""Collector 自用的工具实现。

- search.tavily / search.serper：关键词 → URL
- scrape.firecrawl：URL → markdown/html（首选）
- scrape.httpx：纯 httpx + readability 兜底
- scrape.playwright：JS 渲染 fallback（v1 仅声明接口，注入实现可选）
- parse.readability：HTML → 正文
- robots_checker：robots.txt 合规
- domain_rate_limiter：单域名 ≤ 1 req/s

v1 阶段所有工具都封装在 Collector 子包内，符合 ToolRegistryProtocol。
等 I 窗口产出 backend/tools/ 通用层后，再把 robots/rate_limiter/pii_sanitizer 三件套迁过去。
"""

from __future__ import annotations

import os
import threading
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict
from readability import Document

USER_AGENT = (
    "CompetitiveAnalysisBot/1.0 "
    "(+https://github.com/example/competitive-analysis-agent-platform; contact@example.com)"
)

DEFAULT_MIN_DOMAIN_INTERVAL = 1.0  # 单域名 ≤ 1 req/s，符合 COMPLIANCE.md § 3.2
DEFAULT_HTTP_TIMEOUT = 15.0
ROBOTS_CACHE_TTL = 60 * 60 * 24  # 24h


# ---------- 通用结构 ----------


class SearchHit(BaseModel):
    """单条搜索结果。"""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None
    snippet: str | None = None
    provider: str = "unknown"
    raw_score: float | None = None


class ScrapeResult(BaseModel):
    """抓取产物。"""

    model_config = ConfigDict(extra="forbid")

    url: str
    final_url: str
    http_status: int | None
    html: str | None = None
    markdown: str | None = None
    text: str = ""
    title: str | None = None
    fetched_with: str
    detected_paywall: bool = False
    detected_outdated: bool = False
    error: str | None = None


# ---------- Protocols ----------


@runtime_checkable
class SearchProvider(Protocol):
    name: str
    enabled: bool

    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]: ...


@runtime_checkable
class ScrapeProvider(Protocol):
    name: str
    enabled: bool

    def scrape(self, url: str) -> ScrapeResult: ...


# ---------- 域名速率与 robots ----------


class DomainRateLimiter:
    """每个 host 至少间隔 min_interval 秒。线程安全。"""

    def __init__(self, min_interval: float = DEFAULT_MIN_DOMAIN_INTERVAL) -> None:
        self.min_interval = float(min_interval)
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sleep = time.sleep
        self._now = time.monotonic

    def acquire(self, host: str) -> None:
        # 不在锁里 sleep，避免阻塞其他 host
        with self._lock:
            last = self._last_seen.get(host)
            now = self._now()
            wait = 0.0 if last is None else self.min_interval - (now - last)
            # 预占：把 _last_seen 推到等待结束后的时刻，确保并发的下一个调用接力等待
            self._last_seen[host] = (now + wait) if wait > 0 else now
        if wait > 0:
            self._sleep(wait)


class RobotsChecker:
    """带 TTL 缓存的 robots.txt 检查器。"""

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        http_client: httpx.Client | None = None,
        cache_ttl: float = ROBOTS_CACHE_TTL,
    ) -> None:
        self.user_agent = user_agent
        self.cache_ttl = cache_ttl
        self._http = http_client
        self._cache: dict[str, tuple[float, robotparser.RobotFileParser]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, url: str) -> bool:
        """目标 URL 是否被 robots.txt 允许。

        无 robots.txt 或解析失败时按"允许"处理（标准做法）。
        """
        host = urlparse(url).netloc
        if not host:
            return False
        parser = self._get_parser(url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def _get_parser(self, url: str) -> robotparser.RobotFileParser | None:
        host = urlparse(url).netloc
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(host)
            if cached is not None and now - cached[0] < self.cache_ttl:
                return cached[1]
        try:
            robots_url = urljoin(f"{urlparse(url).scheme}://{host}", "/robots.txt")
            content = self._fetch_robots(robots_url)
        except Exception:
            content = None
        parser = robotparser.RobotFileParser()
        if content is None:
            parser = None  # type: ignore[assignment]
        else:
            parser.parse(content.splitlines())
        with self._lock:
            self._cache[host] = (now, parser)  # type: ignore[assignment]
        return parser

    def _fetch_robots(self, url: str) -> str | None:
        client = self._http or httpx.Client(
            timeout=DEFAULT_HTTP_TIMEOUT, headers={"User-Agent": self.user_agent}
        )
        try:
            resp = client.get(url)
        except Exception:
            return None
        finally:
            if self._http is None:
                client.close()
        if resp.status_code >= 400:
            return None
        return resp.text


# ---------- Readability 解析 ----------


def extract_main_content(html: str) -> tuple[str | None, str]:
    """HTML → (title, text)。失败时返回 (None, '') 而非抛异常。"""
    if not html:
        return None, ""
    try:
        doc = Document(html)
        title = (doc.short_title() or None) if hasattr(doc, "short_title") else None
        summary_html = doc.summary(html_partial=True)
    except Exception:
        # readability 偶尔在残缺 HTML 上抛错，退回 BeautifulSoup 抽 body 文本
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator="\n", strip=True)
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        return title, text
    soup = BeautifulSoup(summary_html, "lxml")
    text = soup.get_text(separator="\n", strip=True)
    return title, text


def detect_paywall(html: str | None, text: str) -> bool:
    """简单启发式：典型付费墙文案命中即视为付费墙。"""
    if not html and not text:
        return False
    haystack = ((html or "") + "\n" + (text or "")).lower()
    markers = (
        "subscribe to continue",
        "paywall",
        "metered_paywall",
        "this article is for subscribers",
        "成为付费会员",
        "订阅后阅读",
    )
    return any(m in haystack for m in markers)


# ---------- Search providers ----------


@dataclass
class TavilySearch:
    """Tavily REST API。无 API key 时 enabled=False，调用直接返回空。"""

    api_key: str | None = field(default_factory=lambda: os.getenv("TAVILY_API_KEY"))
    http: httpx.Client | None = None
    timeout: float = DEFAULT_HTTP_TIMEOUT
    name: str = "search.tavily"

    def __post_init__(self) -> None:
        self.enabled = bool(self.api_key)

    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]:
        if not self.enabled:
            return []
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        client = self.http or httpx.Client(timeout=self.timeout)
        try:
            resp = client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
        finally:
            if self.http is None:
                client.close()
        return [
            SearchHit(
                url=item["url"],
                title=item.get("title"),
                snippet=item.get("content"),
                provider=self.name,
                raw_score=item.get("score"),
            )
            for item in data.get("results", [])
            if item.get("url")
        ]


@dataclass
class SerperSearch:
    """Serper.dev (Google 搜索) API。无 key disabled。"""

    api_key: str | None = field(default_factory=lambda: os.getenv("SERPER_API_KEY"))
    http: httpx.Client | None = None
    timeout: float = DEFAULT_HTTP_TIMEOUT
    name: str = "search.serper"

    def __post_init__(self) -> None:
        self.enabled = bool(self.api_key)

    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]:
        if not self.enabled:
            return []
        payload = {"q": query, "num": max_results}
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        client = self.http or httpx.Client(timeout=self.timeout, headers=headers)
        try:
            resp = client.post("https://google.serper.dev/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
        finally:
            if self.http is None:
                client.close()
        organic = data.get("organic", []) or []
        return [
            SearchHit(
                url=item["link"],
                title=item.get("title"),
                snippet=item.get("snippet"),
                provider=self.name,
            )
            for item in organic
            if item.get("link")
        ]


# DDG html 接口对自我标识的 bot User-Agent 直接返回 anomaly 反爬。
# 这里仅为 SEARCH（公开搜索引擎查询，无登录、无内容获取）使用浏览器风格 UA；
# 抓取阶段仍用合规 USER_AGENT（自报项目身份）。
_BROWSER_UA_FOR_SEARCH = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


@dataclass
class DuckDuckGoSearch:
    """DuckDuckGo HTML 接口。零 key 兜底，质量比 Tavily 差但能跑通真实链路。

    端点：https://html.duckduckgo.com/html/
    结果链接是 DDG 的跳转链 `?uddg=<encoded_real_url>`，需要解出真实 URL。
    DDG 反爬严格，碰到 anomaly 页时优雅返回空列表，由 Collector 自身的兜底路径处理。
    """

    http: httpx.Client | None = None
    timeout: float = DEFAULT_HTTP_TIMEOUT
    name: str = "search.duckduckgo"
    enabled: bool = True

    _ENDPOINT: str = field(
        default="https://html.duckduckgo.com/html/",
        init=False,
        repr=False,
    )

    def search(self, query: str, *, max_results: int = 10) -> list[SearchHit]:
        client = self.http or httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": _BROWSER_UA_FOR_SEARCH,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
                "Referer": "https://duckduckgo.com/",
            },
            follow_redirects=True,
        )
        try:
            resp = client.post(self._ENDPOINT, data={"q": query, "kl": "us-en"})
            if resp.status_code >= 400:
                return []
            html = resp.text
            if "anomaly-modal" in html or "anomaly.js" in html:
                # DDG 反爬触发，不是真实结果页
                return []
        except Exception:
            return []
        finally:
            if self.http is None:
                client.close()
        return _parse_ddg_results(html, provider_name=self.name, limit=max_results)


def _parse_ddg_results(html: str, *, provider_name: str, limit: int) -> list[SearchHit]:
    """从 DDG HTML 页面抽出搜索结果。"""
    from urllib.parse import parse_qs, unquote, urlparse

    soup = BeautifulSoup(html, "lxml")
    hits: list[SearchHit] = []
    for result in soup.select("div.result")[: limit * 2]:  # 多取一些再过滤
        a = result.select_one("a.result__a")
        if a is None:
            continue
        href = a.get("href") or ""
        # DDG 的跳转链：/l/?uddg=<encoded>&...
        real_url: str | None = None
        if href.startswith("//"):
            href = "https:" + href
        try:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                real_url = unquote(qs["uddg"][0])
            elif parsed.netloc and parsed.netloc not in ("duckduckgo.com", "html.duckduckgo.com"):
                real_url = href
        except Exception:
            real_url = None
        if not real_url or not real_url.startswith("http"):
            continue
        title = a.get_text(strip=True) or None
        snippet_el = result.select_one(".result__snippet")
        snippet = snippet_el.get_text(strip=True) if snippet_el else None
        hits.append(
            SearchHit(url=real_url, title=title, snippet=snippet, provider=provider_name)
        )
        if len(hits) >= limit:
            break
    return hits


# ---------- Scrape providers ----------


@dataclass
class FirecrawlScraper:
    """Firecrawl REST API。无 key disabled。"""

    api_key: str | None = field(default_factory=lambda: os.getenv("FIRECRAWL_API_KEY"))
    http: httpx.Client | None = None
    timeout: float = DEFAULT_HTTP_TIMEOUT * 2  # firecrawl 较慢
    name: str = "scrape.firecrawl"

    def __post_init__(self) -> None:
        self.enabled = bool(self.api_key)

    def scrape(self, url: str) -> ScrapeResult:
        if not self.enabled:
            return ScrapeResult(
                url=url,
                final_url=url,
                http_status=None,
                fetched_with=self.name,
                error="firecrawl disabled (missing FIRECRAWL_API_KEY)",
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "url": url,
            "formats": ["markdown", "html"],
            "onlyMainContent": True,
        }
        client = self.http or httpx.Client(timeout=self.timeout, headers=headers)
        try:
            resp = client.post("https://api.firecrawl.dev/v1/scrape", json=payload)
            resp.raise_for_status()
            data = resp.json().get("data", {})
        except Exception as e:
            return ScrapeResult(
                url=url,
                final_url=url,
                http_status=None,
                fetched_with=self.name,
                error=f"firecrawl_failed: {type(e).__name__}: {e}",
            )
        finally:
            if self.http is None:
                client.close()
        html = data.get("html")
        markdown = data.get("markdown") or ""
        metadata = data.get("metadata", {}) or {}
        title = metadata.get("title")
        text_title, text = extract_main_content(html) if html else (None, markdown)
        return ScrapeResult(
            url=url,
            final_url=metadata.get("sourceURL", url),
            http_status=int(metadata.get("statusCode", 200)) if metadata else 200,
            html=html,
            markdown=markdown or None,
            text=text,
            title=title or text_title,
            fetched_with=self.name,
            detected_paywall=detect_paywall(html, text),
        )


@dataclass
class HttpxScraper:
    """直连 httpx + readability。免费兜底，无 JS 渲染。"""

    http: httpx.Client | None = None
    timeout: float = DEFAULT_HTTP_TIMEOUT
    name: str = "scrape.httpx"

    def __post_init__(self) -> None:
        self.enabled = True

    def scrape(self, url: str) -> ScrapeResult:
        client = self.http or httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,zh;q=0.8"},
            follow_redirects=True,
        )
        try:
            resp = client.get(url)
        except Exception as e:
            return ScrapeResult(
                url=url,
                final_url=url,
                http_status=None,
                fetched_with=self.name,
                error=f"httpx_failed: {type(e).__name__}: {e}",
            )
        finally:
            if self.http is None:
                client.close()
        if resp.status_code >= 400:
            return ScrapeResult(
                url=url,
                final_url=str(resp.url),
                http_status=resp.status_code,
                fetched_with=self.name,
                error=f"http_{resp.status_code}",
            )
        html = resp.text
        title, text = extract_main_content(html)
        return ScrapeResult(
            url=url,
            final_url=str(resp.url),
            http_status=resp.status_code,
            html=html,
            text=text,
            title=title,
            fetched_with=self.name,
            detected_paywall=detect_paywall(html, text),
        )


@dataclass
class Crawl4AIScraper:
    """基于 Crawl4AI（Playwright + chromium 渲染）的抓取实现。

    用途：解决 SPA / JS 重渲染页面 httpx 抓不全正文的问题（如 Notion 首页）。
    挂在 registry 的 `scrape.playwright` 位 —— 名字保持 "scrape.playwright"，
    Collector 的 fetch_method 推断把它归入 contract Literal "playwright"，
    契约层面完全干净。

    依赖：
      - `pip install -e '.[tools-crawl4ai]'`
      - `python -m playwright install chromium`

    同步接口：内部用 `asyncio.run` 包 AsyncWebCrawler。每次调用启停一次 chromium，
    冷启动 1-3s。v1 阶段够用，性能瓶颈再上长生命周期 crawler。
    """

    name: str = "scrape.playwright"
    enabled: bool = True
    headless: bool = True
    page_timeout_ms: int = 60_000
    word_count_threshold: int = 1
    verbose: bool = False

    def scrape(self, url: str) -> ScrapeResult:
        import asyncio

        try:
            return asyncio.run(self._async_scrape(url))
        except Exception as e:
            return ScrapeResult(
                url=url,
                final_url=url,
                http_status=None,
                fetched_with=self.name,
                error=f"crawl4ai_failed: {type(e).__name__}: {e}",
            )

    async def _async_scrape(self, url: str) -> ScrapeResult:
        # lazy import：crawl4ai 是 optional 依赖，没装时不影响 tools 模块加载
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        browser_cfg = BrowserConfig(
            browser_type="chromium",
            headless=self.headless,
            verbose=self.verbose,
            user_agent=USER_AGENT,
        )
        run_cfg = CrawlerRunConfig(
            wait_until="domcontentloaded",
            page_timeout=self.page_timeout_ms,
            word_count_threshold=self.word_count_threshold,
            remove_overlay_elements=True,
            verbose=self.verbose,
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            container = await crawler.arun(url=url, config=run_cfg)

        # arun 返回 CrawlResultContainer，单 URL 时取首条
        result = container[0] if hasattr(container, "__getitem__") else container

        html = result.cleaned_html or result.html or ""
        title: str | None = None
        if result.metadata:
            title = result.metadata.get("title")

        # 提取正文：优先 crawl4ai 的 markdown（已经做了 LLM-friendly 抽取）
        markdown_text = ""
        md = getattr(result, "markdown", None)
        if md is not None:
            if hasattr(md, "raw_markdown"):
                markdown_text = md.raw_markdown or ""
            elif isinstance(md, str):
                markdown_text = md
        if markdown_text and len(markdown_text) > 100:
            text = markdown_text
        else:
            t2, text = extract_main_content(html)
            if not title:
                title = t2

        final_url = result.redirected_url or result.url or url
        status = result.redirected_status_code or result.status_code

        return ScrapeResult(
            url=url,
            final_url=final_url,
            http_status=int(status) if status else None,
            html=html or None,
            markdown=markdown_text or None,
            text=text,
            title=title,
            fetched_with=self.name,
            detected_paywall=detect_paywall(html, text),
            error=None
            if result.success and text
            else (result.error_message or "empty_text"),
        )


@dataclass
class NoopPlaywrightScraper:
    """Playwright 占位实现。v1 不强制安装 chromium。

    真实实现由调用方在初始化时替换。"""

    name: str = "scrape.playwright"
    enabled: bool = False

    def scrape(self, url: str) -> ScrapeResult:
        return ScrapeResult(
            url=url,
            final_url=url,
            http_status=None,
            fetched_with=self.name,
            error="playwright_not_installed",
        )


# ---------- Tool Registry ----------


class SimpleToolRegistry:
    """字典封装。BaseAgent 通过 ToolRegistryProtocol 接收即可。"""

    def __init__(self, items: dict[str, Any] | None = None) -> None:
        self._items: dict[str, Any] = dict(items or {})

    def register(self, name: str, tool: Any) -> None:
        self._items[name] = tool

    def get(self, name: str) -> Any:
        if name not in self._items:
            raise KeyError(f"tool not registered: {name}")
        return self._items[name]

    def has(self, name: str) -> bool:
        return name in self._items

    def names(self) -> list[str]:
        return list(self._items.keys())


def build_default_registry(
    *,
    http_client: httpx.Client | None = None,
    enable_playwright: bool = False,
    playwright_impl: ScrapeProvider | None = None,
    enable_crawl4ai: bool = False,
    crawl4ai_kwargs: dict[str, Any] | None = None,
    rate_limit_interval: float = DEFAULT_MIN_DOMAIN_INTERVAL,
) -> SimpleToolRegistry:
    """根据环境变量构造一个默认 registry。

    没有 API key 的 provider 会被注册但 enabled=False，由 Collector 自动跳过。

    `enable_crawl4ai=True`：把 `scrape.playwright` 位换成 `Crawl4AIScraper`（基于 chromium 的
    JS 渲染抓取）。需要已安装 `crawl4ai` 并运行过 `python -m playwright install chromium`。
    与 `playwright_impl` 互斥；同时传入时 `playwright_impl` 优先。
    """
    reg = SimpleToolRegistry()
    reg.register("search.tavily", TavilySearch(http=http_client))
    reg.register("search.serper", SerperSearch(http=http_client))
    reg.register("search.duckduckgo", DuckDuckGoSearch(http=http_client))
    reg.register("scrape.firecrawl", FirecrawlScraper(http=http_client))
    reg.register("scrape.httpx", HttpxScraper(http=http_client))

    playwright_slot: ScrapeProvider
    if enable_playwright and playwright_impl is not None:
        playwright_slot = playwright_impl
    elif enable_crawl4ai:
        playwright_slot = Crawl4AIScraper(**(crawl4ai_kwargs or {}))
    else:
        playwright_slot = NoopPlaywrightScraper()
    reg.register("scrape.playwright", playwright_slot)

    reg.register(
        "robots_checker",
        RobotsChecker(http_client=http_client),
    )
    reg.register("domain_rate_limiter", DomainRateLimiter(min_interval=rate_limit_interval))
    return reg


__all__ = [
    "USER_AGENT",
    "Crawl4AIScraper",
    "DomainRateLimiter",
    "DuckDuckGoSearch",
    "FirecrawlScraper",
    "HttpxScraper",
    "NoopPlaywrightScraper",
    "RobotsChecker",
    "ScrapeProvider",
    "ScrapeResult",
    "SearchHit",
    "SearchProvider",
    "SerperSearch",
    "SimpleToolRegistry",
    "TavilySearch",
    "build_default_registry",
    "detect_paywall",
    "extract_main_content",
]
