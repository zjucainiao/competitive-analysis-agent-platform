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
search.tavily / search.serper        → URL 候选
   ├ url_ranker（LLM 可选，启发式 fallback）→ top-K
   ├ robots_checker → 合规过滤（ROBOTS_BLOCKED 入 errors）
   ├ domain_rate_limiter → 单域名 ≤ 1 req/s
   ├ scrape.firecrawl → scrape.playwright → mock fixtures
   ├ page_type_classifier（LLM 可选，启发式 fallback）→ 维度复核
   └ build RawSourceDoc
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

### 真实模式

```python
from backend.agents.collector import Collector, build_default_registry

registry = build_default_registry()        # 从 env 读 TAVILY_API_KEY / FIRECRAWL_API_KEY 等
agent = Collector(
    llm=my_llm_provider,                   # 实现 LLMProviderProtocol 的对象
    tracer=my_tracer,                      # 实现 TracerProtocol 的对象
    tools=registry,
    mock=False,
)
out = agent.invoke(my_input, trace_id="...", span_id="...")
```

环境变量（详见 [docs/CONVENTIONS.md § 6](../../../docs/CONVENTIONS.md#61-环境变量)）：

- `TAVILY_API_KEY` — 启用 `search.tavily`
- `SERPER_API_KEY` — 启用 `search.serper`
- `FIRECRAWL_API_KEY` — 启用 `scrape.firecrawl`
- 缺 key 的 provider 注册但 `enabled=False`，Collector 自动跳过；若全链拿不到结果且 `fallback_to_mock=True`，回退到 mock fixtures（带 `FELL_BACK_TO_MOCK` 警告）。

### Playwright（可选）

`scrape.playwright` 默认是 `NoopPlaywrightScraper` 占位；如需启用，传入实现 `ScrapeProvider` 协议的对象：

```python
registry = build_default_registry(
    enable_playwright=True,
    playwright_impl=my_playwright_scraper,
)
```

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

通用错误码见 [docs/AGENTS.md § 2.5](../../../docs/AGENTS.md#25-错误码约定)。Collector 特有：

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
python -m pytest backend/agents/collector/tests -v
```

当前 8 个 case 全绿，覆盖：mock 正常 / mock 缺维度 / 真实模式 robots 阻拦 + fallback / 真实模式全链失败 / firecrawl→playwright 降级 / Schema extra=forbid / paywall 跳过 / rate_limiter 调用。

## 已知限制 / TODO

### v1 范围内
- 语言：仅 `en` / `zh` 自动识别（按 raw_text 前 200 字符 ASCII 比例）
- 不解析 PDF（部分官方文档是 PDF），落到 unsupported
- `_render` 用极简 Jinja2 子集，复杂 prompt 模板需迁移到真正的 Jinja2

### 给架构窗口的 follow-up（建议下次同步时讨论）

1. **`BaseAgent` 在非 mock 模式强制 `llm` 与 `tracer` 不为 None**：但按 [AGENTS.md § 3.5](../../../docs/AGENTS.md#35-prompt-设计要点)，Collector 的 LLM 是"可选优化项"，启发式 fallback 是一等公民。当前测试通过注入 `NullLLM` / `NullTracer` 绕过，建议把这两个改成 `None` 时退化处理，或者由我提供官方的 `Null*` stub 暴露在 `_base.py`。
2. **`RawSourceDoc.fetch_method` Literal 缺少 `"httpx"`**：当前 `HttpxScraper` 已实现但 v1 没接入主链，避免破坏契约。如要把它纳入"无 Firecrawl key 也能跑真实抓取"路径，需要在 `schemas/evidence.py` 加一项。
3. **`robots_checker` / `domain_rate_limiter` / `pii_sanitizer` 当前在 collector 内自给**：等 I 窗口（基础设施）`backend/tools/` 落地后，统一迁过去，Collector 改为 `from backend.tools import RobotsChecker` 即可。
4. **ruff 中文标点告警**：当前 `RUF001 / RUF002 / RUF003` 触发 103 次（中文项目正常），建议在 `pyproject.toml [tool.ruff.lint] ignore` 里加这三项。
5. **`backend/llm/` 还是空目录**：Collector 真实模式跑 LLM 路径暂时用 `NullLLM` 占位，等 I 窗口 `LLMProvider` 落地接入。

## 责任窗口

C 窗口（本窗口）。v1 已交付：mock 闭环、真实模式抓取链 + 合规过滤 + 自评估 + 8 个测试用例全绿。
