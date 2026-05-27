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
    rate_limit_interval: float = DEFAULT_MIN_DOMAIN_INTERVAL,
) -> SimpleToolRegistry:
    """根据环境变量构造一个默认 registry。

    没有 API key 的 provider 会被注册但 enabled=False，由 Collector 自动跳过。
    """
    reg = SimpleToolRegistry()
    reg.register("search.tavily", TavilySearch(http=http_client))
    reg.register("search.serper", SerperSearch(http=http_client))
    reg.register("scrape.firecrawl", FirecrawlScraper(http=http_client))
    reg.register("scrape.httpx", HttpxScraper(http=http_client))
    if enable_playwright and playwright_impl is not None:
        reg.register("scrape.playwright", playwright_impl)
    else:
        reg.register("scrape.playwright", NoopPlaywrightScraper())
    reg.register(
        "robots_checker",
        RobotsChecker(http_client=http_client),
    )
    reg.register("domain_rate_limiter", DomainRateLimiter(min_interval=rate_limit_interval))
    return reg


__all__ = [
    "USER_AGENT",
    "DomainRateLimiter",
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
