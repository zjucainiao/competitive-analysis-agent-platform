# Collector Agent · 信息采集

> 契约源：[docs/AGENTS.md § 3](../../../docs/AGENTS.md#3-collector采集-agent) ·
> Schema：[docs/SCHEMA.md § 6](../../../docs/SCHEMA.md#6-原始来源rawsourcedoc) ·
> 合规：[docs/COMPLIANCE.md](../../../docs/COMPLIANCE.md)

## 职责

从公开渠道采集竞品相关网页，输出结构化的 `RawSourceDoc[]`。**不做语义抽取**——那是 Extractor 的事。

## 输入 / 输出

- Input：`CollectorInput`（产品名、维度、约束、可选 QA 反馈）
- Output：`CollectorOutput`（含 `raw_sources: list[RawSourceDoc]`、`coverage_by_dimension`、`confidence`、`self_critique`）

## 实现链

```
通用维度（HOMEPAGE / FEATURES / PRICING / HELP_DOCS / ...）：
   search.tavily / search.serper / search.duckduckgo  → URL 候选
   + official_url seed（按维度拼路径，无搜索 key 也能跑）
   + 去重（原 URL）
   ├ url_ranker（LLM 可选，启发式 fallback）→ top-K
   ├ robots_checker → 合规过滤（ROBOTS_BLOCKED 入 errors）
   ├ domain_rate_limiter → 单域名 ≤ 1 req/s
   ├ scrape.firecrawl → scrape.playwright → scrape.httpx → mock fixtures
   ├ final_url 二次去重（应对 301/302）
   ├ page_type_classifier（LLM 可选，启发式 fallback）→ 维度复核
   └ build RawSourceDoc

REVIEWS 维度（独立路径，依赖 LLM 内置联网搜索）：
   ├ L1：LLM 联网搜索（豆包 Seed EP / OpenAI web_search / Claude web_search）
   │     → _ReviewsFinding（overall_rating + themes + sample_quotes + sources）
   │     → 每个 source 一条 fetch_method='llm_synthesis' 的 RawSourceDoc
   │       （合成文本：identity=ambiguous、authority=0.4；无引用 URL 时
   │        用 .invalid 标记 URI，不伪造 G2 等真实站点 URL）
   └ L2 兜底：_seed_review_hosts（G2/Capterra/TrustRadius slug 拼接）→ 走通用 scrape 链
```

## 目录结构

```
backend/agents/collector/
├── agent.py          # Collector(BaseAgent) 主实现
├── tools.py          # Search/Scrape/Robots/RateLimiter + SimpleToolRegistry
├── fixtures.py       # Notion / ClickUp / Asana × 4 维度 mock 数据
├── prompts/
│   ├── url_ranker.md
│   ├── page_type_classifier.md
│   └── summary.md
├── tests/
│   ├── conftest.py   # FakeSearch / FakeScrape / FakeRobots / FakeLimiter / Null{LLM,Tracer}
│   └── test_agent.py # 8 个 case
└── README.md
```

## 运行方式

### Mock 模式（推荐 demo + 单元测试）

```python
from backend.agents.collector import Collector
from backend.schemas import CollectDimension, CollectorInput

agent = Collector(mock=True)
out = agent.invoke(
    CollectorInput(
        task_id="task-1",
        project_id="proj-1",
        trace_id="trace-1",
        span_id="span-1",
        product_name="Notion",
        industry="collaboration_saas",
        dimensions=[
            CollectDimension.HOMEPAGE,
            CollectDimension.FEATURES,
            CollectDimension.PRICING,
            CollectDimension.HELP_DOCS,
        ],
    ),
    trace_id="trace-1",
    span_id="span-1",
)
print(out.status, out.confidence, len(out.raw_sources))
```

Mock 数据覆盖：**Notion / ClickUp / Asana × HOMEPAGE / FEATURES / PRICING / HELP_DOCS** = 12 个 RawSourceDoc。其他产品 / 维度走 mock 时会返回空 + `NO_RELEVANT_RESULTS` 警告 + 进入 PARTIAL 状态。

### 真实模式（零 key 也能跑）

```python
from dotenv import load_dotenv
from backend.agents.collector import (
    Collector,
    OpenAICompatibleLLM,
    build_default_registry,
)

load_dotenv(".env")

llm = OpenAICompatibleLLM.from_env()        # DOUBAO > DEEPSEEK > OPENAI 优先级；都没就返 None
registry = build_default_registry()          # DDG + httpx 默认启用，Tavily/Firecrawl 缺 key 自动跳过
agent = Collector(
    llm=llm,
    tracer=my_tracer,                        # 实现 TracerProtocol 的对象
    tools=registry,
    mock=False,
)
out = agent.invoke(my_input, trace_id="...", span_id="...")
```

环境变量（完整清单见仓库根目录 [.env.example](../../../.env.example)）：

| key | 用途 | 必需性 |
|---|---|---|
| `DOUBAO_API_KEY` (`DOUBAO_BASE_URL`, `DOUBAO_MODEL`) | 火山方舟豆包 EP，**带内置联网搜索** —— REVIEWS 维度依赖它 | 推荐 |
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` | LLM 路径（URL ranker / page type classifier） | 可选（无则启发式） |
| `TAVILY_API_KEY` | 启用 `search.tavily` | 可选（无则用 DDG） |
| `SERPER_API_KEY` | 启用 `search.serper` | 可选 |
| `FIRECRAWL_API_KEY` | 启用 `scrape.firecrawl`（带 markdown + onlyMainContent，质量最好） | 可选（无则 httpx 兜底） |

**完全零 key 路径**：DuckDuckGo 公开搜索（HTML 接口）+ official_url seed + httpx+readability 抓取 + 启发式 rank/classify。整条真实链能跑，但 DDG 反爬挑战概率较高、httpx 不渲染 JS（SPA 首页可能正文很短）。Demo 推荐至少配 `FIRECRAWL_API_KEY` 提升抓取质量。

### Crawl4AI（JS 渲染 / SPA 抓取，推荐）

`scrape.playwright` 默认是 `NoopPlaywrightScraper`。装好 Crawl4AI 后开启 `enable_crawl4ai=True`，该位会换成 `Crawl4AIScraper`（基于 chromium 的 JS 渲染抓取），契约上仍归 `fetch_method="playwright"`。

```bash
# 一次性安装
pip install -e '.[tools-crawl4ai]'
python -m playwright install chromium
```

```python
registry = build_default_registry(enable_crawl4ai=True)
# 或自定义参数：
registry = build_default_registry(
    enable_crawl4ai=True,
    crawl4ai_kwargs={"headless": True, "page_timeout_ms": 90_000},
)
```

**SPA 抓取效果对比**（同一 URL `https://www.notion.com/`）：

| Scraper | text_len | 备注 |
|---|---|---|
| `HttpxScraper` | **38 字** | 拿到的是空壳 HTML，readability 抠不出 |
| `Crawl4AIScraper` | **9770 字** | 完整渲染后正文（约 **257× 提升**） |

启用 Crawl4AI 后，Collector 主链跑 Notion HOMEPAGE 的真采集：status=SUCCESS, confidence=0.90, fetch_method='playwright', 单页正文 9120 字。

### 自定义 Playwright（不用 Crawl4AI）

如果你已有 Playwright 封装，可以直接注入：

```python
registry = build_default_registry(
    enable_playwright=True,
    playwright_impl=my_playwright_scraper,
)
```

`playwright_impl` 优先于 `enable_crawl4ai`，两个同时传时前者生效。

## 关键约束

- 必须遵守 `robots.txt`（除非 `respect_robots_txt=False`）
- 单域名抓取频率 ≤ 1 req/s（COMPLIANCE § 3.2）
- 抓取链 fallback 顺序：`firecrawl → playwright → mock`（hybrid 模式下）
- 每个 dimension 至少返回 1 个有效页面，否则 `self_critique` 报告
- User-Agent 固定为：`CompetitiveAnalysisBot/1.0 (+...)`

## 自评估触发条件

confidence 起点 `0.95`，按下列规则扣减并 clamp 到 [0, 1]：

| 触发 | 扣减 |
|---|---|
| 每个空维度（zero pages） | -0.15 |
| 每个 paywall 页面 | -0.05 |
| 每个 raw_text < 200 字符 | -0.05 |
| robots 阻挡比例 > 30% | -0.10 |
| 完全没采到任何源 | 强制 0.0 |

confidence < 0.6 → BaseAgent 强制 `self_critique` 非空（已在 `_build_self_critique` 中生成具体文本）。

## 错误码

通用错误码见 [docs/AGENTS.md § 2.5](../../../docs/AGENTS.md#25-错误码约定)。REVIEWS 维度遵循同一套错误码（LLM 失败 → `TOOL_FAILED`，无源 → `NO_RELEVANT_RESULTS`，schema 不匹配 → `LLM_SCHEMA_INVALID`）。Collector 特有：

| Code | 含义 | 何时触发 |
|---|---|---|
| `ROBOTS_BLOCKED` | robots.txt 禁止 | `respect_robots_txt=True` 且命中 Disallow |
| `PAYWALL_DETECTED` | 付费墙阻挡 | `detect_paywall` 命中且 `allow_paid_content=False` |
| `NO_RELEVANT_RESULTS` | 搜索零结果或维度不符 | 搜索全空 / classifier 判定为其他维度 |
| `FELL_BACK_TO_MOCK` | 真实链失败回退 | `fallback_to_mock=True` 且 raw 链拿到 0 个源 |
| `TOOL_FAILED` | 工具异常 | scrape 全链失败 |

## 测试

```bash
. .venv/bin/activate

# 单元测试（无外部依赖，秒级）
python -m pytest backend/agents/collector/tests --ignore=backend/agents/collector/tests/test_e2e_real.py -v

# 端到端真采（需要联网；DOUBAO_API_KEY 推荐，REVIEWS 维度必需）
python -m pytest backend/agents/collector/tests/test_e2e_real.py -v -s
```

**单元测试 11 个全绿**：mock 正常 / mock 缺维度 / 真实模式 robots 阻拦 + fallback / 真实模式无源失败 / firecrawl→playwright 降级 / Schema extra=forbid / paywall 跳过 / rate_limiter 调用 / **REVIEWS LLM 路径 × 3**（emits per source / empty finding 兜底 / LLM 抛异常不阻塞）。

**e2e 测试 4 个全绿**：
- `test_e2e_real.py` × 2：通用维度真采（HOMEPAGE+PRICING）+ **REVIEWS 维度真采（豆包联网搜 G2/Capterra）**
- `test_e2e_crawl4ai.py` × 2：crawl4ai vs httpx SPA 抓取对比 + Collector 主链启用 crawl4ai

**REVIEWS 真采输出（豆包 Seed EP `ep-20260514111325-xjmj7` 联网搜索）**：

```
status   = SUCCESS  confidence = 0.95  duration_ms = 7728
got 2 review docs:
  - [search] https://www.g2.com/products/notion/reviews        text_len=816
    G2评分4.7/5，超3000条评论，用户普遍认可其灵活性与协作能力...
  - [search] https://www.capterra.com/p/235776/Notion/reviews  text_len=822
    Capterra评分4.5/5，超2000条评论，用户称赞自定义能力强...
  Overall rating: 4.6/5, review_count=5000
  Positive themes: 界面简洁美观; 高度自定义; 集成丰富; 跨多端同步
  Negative themes: 大页面性能问题; 高级版定价偏高
```

Extractor 抽 `user_feedback.overall_rating = 4.6` 直接命中（QA `schema_completeness` critical 解除）。

**真采输出（DeepSeek + 无 Tavily/Firecrawl key，纯 httpx）**：

```
status   = SUCCESS  confidence = 0.80  duration_ms = 34586
coverage = {'homepage': 2, 'pricing': 2}
  - [homepage] manual 200 https://www.notion.com/                    text_len=38   (SPA, 受 httpx 限制)
  - [homepage] manual 200 https://notion.notion.site/                text_len=29
  - [pricing]  manual 200 https://www.notion.com/pricing             text_len=994  ✓
  - [pricing]  manual 200 https://www.notion.com/product/projects... text_len=159
  ! warn TOOL_FAILED: scrape failed for https://www.notion.so/index.html (404, seed 推测路径)
  self_critique = 正文过短(<200 字符): 3 个页面，可能抓取失败 | 过程告警: TOOL_FAILED
```

**启用 Crawl4AI 后的同一页面对比**：

```
status   = SUCCESS  confidence = 0.90  duration_ms = 21095
  - [homepage] playwright 200 https://www.notion.com/        text_len=9120  ✓ (SPA 渲染后)
  - [homepage] playwright 200 https://www.notion.so/index.html text_len=28  (404 跳转，正常短)
```

httpx 38 字 → crawl4ai 9120 字，约 **240× 提升**。这是接入 Crawl4AI 的核心价值。

## 已知限制 / TODO

- 语言：仅 `en` / `zh` 自动识别（按 raw_text 前 200 字符 ASCII 比例）
- 不解析 PDF（部分官方文档是 PDF），落到 unsupported
- `_render` 用极简 Jinja2 子集，复杂 prompt 模板需迁移到真正的 Jinja2
- `robots_checker` / `domain_rate_limiter` / `pii_sanitizer` 当前在 collector 内自给，后续可统一迁到共享的 `backend/tools/`
