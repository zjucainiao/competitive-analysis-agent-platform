# Agent 接口契约

> **这是各 Agent 实现窗口最重要的依据。**所有 Agent 的输入输出、工具、prompt 约定、错误处理都在本文档定义。任何对契约的偏离都会破坏跨窗口协作。

---

## 1. 总览

| Agent | 职责 | 输入 | 输出 | 上游 | 下游 |
|---|---|---|---|---|---|
| Collector | 公开信息采集 | `CollectorInput` | `CollectorOutput`（`RawSourceDoc[]`） | Orchestrator | Extractor |
| Extractor | 非结构化 → 结构化 | `ExtractorInput`（含 RawSourceDoc[]） | `ExtractorOutput`（`CompetitorProfile` + `Evidence[]`） | Collector | Analyst |
| Analyst | 多维度对比分析 | `AnalystInput`（多个 Profile） | `AnalystOutput`（`AnalysisResult`，每个 claim 绑 `evidence_ids`） | Extractor | Reporter |
| Reporter | 报告撰写 | `ReporterInput`（AnalysisResult + 模板） | `ReporterOutput`（`ReportDraft`） | Analyst | QA |
| QA | 质检审查 | `QAInput`（ReportDraft + Evidence） | `QAOutput`（`QAVerdict` + 路由决策） | Reporter | Orchestrator（回流） |

**强约束**：
- Agent 之间**只**通过 Pydantic 模型通信，禁止自然语言对话
- 所有 Agent 继承 `BaseAgent`，通过 `invoke()` 统一入口调用
- 所有 LLM 调用走统一 provider 抽象（当前唯一实现 `OpenAICompatibleLLM`，见 `backend/agents/collector/llm_providers.py`），不直接 import vendor SDK
- 所有 Agent 必须输出 `confidence`（[0, 1]）和 `self_critique`（自评估文本）
- 所有 `*Input` 继承 `AgentInputBase`（`backend/schemas/agent_io.py:30-38`），含必填的 `task_id` / `project_id` / `trace_id` / `span_id`

---

## 2. 通用约定

### 2.1 BaseAgent 基类

所有 Agent 继承自 `BaseAgent`。基类负责：

- Trace 注入（trace_id / span_id）
- Token / 耗时统计
- Schema 输入输出校验
- 异常捕获与降级
- 自评估字段强制要求

```python
# backend/agents/_base.py（架构窗口提供）
from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from pydantic import BaseModel

TInput  = TypeVar("TInput",  bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)

class BaseAgent(Generic[TInput, TOutput], ABC):
    name: str               # "collector" / "extractor" / ...
    version: str            # "1.0.0"
    input_model:  type[TInput]
    output_model: type[TOutput]

    def __init__(self, llm: LLMProvider, tools: ToolRegistry,
                 tracer: Tracer, mock: bool = False):
        ...

    def invoke(self, inp: TInput, *, trace_id: str, span_id: str) -> TOutput:
        """统一入口。自动校验、注入 trace、统计 token、捕获异常。"""
        # 1. 校验输入
        # 2. 开 span
        # 3. 调用 self._run(inp)
        # 4. 校验输出
        # 5. 关 span，写 TraceRecord
        ...

    @abstractmethod
    def _run(self, inp: TInput) -> TOutput:
        """子类实现具体业务逻辑。"""
        ...
```

### 2.2 通用消息字段

所有 `*Input` 必须继承 `AgentInputBase`，所有 `*Output` 必须继承 `AgentOutputBase`
（`backend/schemas/agent_io.py`，均设 `model_config = ConfigDict(extra="forbid")`）：

```python
# backend/schemas/agent_io.py
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class AgentStatus(str, Enum):
    SUCCESS         = "success"           # 正常完成
    PARTIAL         = "partial"           # 部分完成（如部分源采集失败）
    NEEDS_REWORK    = "needs_rework"      # 主动声明需要重做（自评估不通过）
    FAILED          = "failed"            # 失败

class AgentInputBase(BaseModel):
    """所有 Agent 输入的基类字段。各 *Input 继承后再加业务字段。"""
    model_config = ConfigDict(extra="forbid")
    task_id:    str
    project_id: str
    trace_id:   str
    span_id:    str

class AgentOutputBase(BaseModel):
    """所有 Agent 输出的基类字段。"""
    model_config = ConfigDict(extra="forbid")
    agent_name:    str
    agent_version: str
    task_id:       str
    trace_id:      str
    span_id:       str

    status:        AgentStatus
    confidence:    float = Field(ge=0, le=1, description="本次输出整体置信度")
    self_critique: str  = Field(description="自评估文本，confidence<0.6 时必填具体原因")

    # 度量
    tokens_input:  int = 0
    tokens_output: int = 0
    cost_usd:      float = 0.0
    duration_ms:   int   = 0

    # 错误累积
    errors:        list["AgentError"] = []
    # 输入快照（可观测）：本次 invoke 收到输入的紧凑摘要（计数 + 关键名，已脱敏），
    # 由 BaseAgent.invoke 出口统一填充，随 output 流到前端做「输入」区可观测。
    input_snapshot: dict = {}

class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code:    str       # e.g. "LLM_TIMEOUT", "LLM_SCHEMA_INVALID"，见 § 2.5
    message: str
    severity:  Literal["warn", "error", "fatal"] = "error"
    retriable: bool = True
    details:   dict = {}    # 结构化补充信息
```

### 2.3 LLM 调用约定

```python
# 统一走 provider 抽象（当前唯一实现 OpenAICompatibleLLM），禁止业务代码直接 import
# 任何 vendor SDK（如 anthropic.messages.create()）——便于统计 / Mock / 切换 provider。
result = self.llm.chat(
    system=SYSTEM_PROMPT,
    messages=[...],
    response_format=MyOutputSchema,   # 强制 JSON Schema 输出（三层兜底，见 llm_providers.py）
    tools=[tool_a, tool_b],
    max_tokens=4096,
    temperature=0.2,
)
```

> 实际 provider 由 `OpenAICompatibleLLM.from_env()` 按 `DOUBAO_API_KEY` > `DEEPSEEK_API_KEY`
> > `OPENAI_API_KEY`（默认 `gpt-4o-mini`）优先级选取（`backend/agents/collector/llm_providers.py`）。
> 项目内**没有** Anthropic / Claude / Qwen 的运行时 provider（仅 `backend/llm/pricing.py` 里有
> 若干未被选用的价目表条目）。

LLM 调用规则：
1. **结构化输出强制**：能用 `response_format` / `tool_use` 的场景必须用，禁止"用自然语言拼 JSON 然后正则解析"
2. **Prompt 外置**：所有 prompt 放在 `agents/<name>/prompts/` 目录下的 `.md` 或 `.j2` 文件，不硬编码在代码里
3. **温度约束**：抽取类任务 `temperature=0.1`，分析类 `0.3`，撰写类 `0.5`
4. **重试策略**：JSON 校验失败重试最多 2 次，每次把校验错误注入到下一轮的 user message

### 2.4 工具调用约定

每个 Agent 在 `__init__` 时声明依赖的工具：

```python
# 实际声明见 backend/agents/collector/agent.py:437
class Collector(BaseAgent):
    required_tools = [
        "search.tavily", "search.duckduckgo",
        "scrape.firecrawl", "scrape.httpx", "scrape.playwright",
        "robots_checker", "domain_rate_limiter",
    ]
```

工具调用全部走 `ToolRegistry`，便于统计 / Mock / 降级。

### 2.5 错误码约定

通用错误码（所有 Agent 共用，均在代码中真实抛出）：

| Code | 含义 | retriable | 出处 |
|---|---|---|---|
| `LLM_TIMEOUT` | LLM 超时 | yes | `orchestrator/run_agent.py`、`executor.py` |
| `LLM_SCHEMA_INVALID` | LLM 输出三层兜底后仍不符合 Schema | yes（带修复重试） | 各 agent `_run` |
| `TOOL_FAILED` | 工具调用失败 | yes | `collector/agent.py` |
| `INPUT_INVALID` | 输入校验失败 | no | `agents/_base.py:365` |
| `UPSTREAM_MISSING` | 上游必要数据缺失 | no | `collector/extractor agent.py` |

> 各 Agent 特有错误码在对应章节定义（均经代码核对）。除上表与各章节外，代码中还有少量
> 未在文档登记的内部码（如 reporter 的 `UNVERIFIED_INFERENCE`）——以源码为准。

Agent 特有错误码在各 Agent 章节定义。

---

## 3. Collector（采集 Agent）

### 3.1 职责

从公开渠道采集竞品相关网页，输出结构化的原始来源文档 `RawSourceDoc[]`。**不做语义抽取**——那是 Extractor 的事。

### 3.2 输入

```python
class CollectorInput(AgentInputBase):   # 继承 task_id/project_id/trace_id/span_id
    product_name: str                  # "Notion"
    official_url: str | None = None    # 可选，已知官网
    industry: str                      # "collaboration_saas"
    dimensions: list[CollectDimension] # ["homepage", "pricing", "docs", "reviews"]
    constraints: CollectConstraints = CollectConstraints()

    # 重做时由 QA 反馈注入（QAFeedback 的序列化；用 dict 避免循环依赖）
    qa_feedback: dict | None = None
    # 返工收敛：上一轮被 QA 判为身份不符的源 URL，重采时直接跳过
    exclude_source_urls: list[str] = []

class CollectDimension(str, Enum):
    HOMEPAGE     = "homepage"
    FEATURES     = "features"
    PRICING      = "pricing"
    HELP_DOCS    = "help_docs"
    CHANGELOG    = "changelog"
    CASES        = "customer_cases"
    BLOG         = "blog"
    REVIEWS      = "user_reviews"      # G2 / Capterra / etc.
    APP_MARKET   = "app_market"

class CollectConstraints(BaseModel):
    max_pages_per_dimension: int = 5
    timeout_seconds:         int = 60
    respect_robots_txt:      bool = True
    allow_paid_content:      bool = False
    fallback_to_mock:        bool = True    # demo 用
```

### 3.3 输出

```python
class CollectorOutput(AgentOutputBase):
    raw_sources: list[RawSourceDoc] = []
    coverage_by_dimension: dict[CollectDimension, int] = {}  # 每维度成功采集页面数

# 定义在 backend/schemas/evidence.py
class RawSourceDoc(BaseModel):
    source_id:    str                  # uuid
    product_name: str
    dimension:    CollectDimension
    source_url:   HttpUrl              # 校验过的 URL，非裸 str
    source_type:  str                  # "html" / "pdf" / "json"
    title:        str | None = None
    raw_html:     str | None = None    # 完整 HTML（可选；v1 无对象存储，通常存摘要或 None）
    raw_text:     str                  # 抽正文后的纯文本
    summary:      str | None = None    # 短摘要（可选，便于上游预览）
    language:     str = "en"           # "en" / "zh"

    collected_at:        datetime
    fetch_method:        Literal["search", "firecrawl", "playwright", "mock", "manual"]
    http_status:         int | None = None
    robots_allowed:      bool = True
    source_authority:    float = Field(ge=0, le=1, default=0.7)  # 官方页 0.95，UGC 0.6
    detected_paywall:    bool = False
    detected_outdated:   bool = False  # 页面 last-modified 早于 1 年

    # 产品身份校验（Collector 抓取后填，Extractor 继承到 Evidence；详见 § 7 / docs/QA.md）
    detected_product_name: str | None = None        # 页面内容检测出的产品名
    identity_confidence:   float | None = None      # 确属 product_name 的置信度 0-1
    identity_status:       Literal["unvalidated", "confirmed", "mismatch", "ambiguous"] = "unvalidated"
    source_class:          Literal["official", "review", "other"] | None = None
```

### 3.4 关键工具

- `search.tavily`：关键词 → 候选 URL 列表
- `scrape.firecrawl`：URL → 结构化 markdown / HTML
- `scrape.playwright`：需要 JS 渲染或登录的 fallback
- `parse.readability`：HTML → 正文

### 3.5 Prompt 设计要点

Collector 主要靠工具，LLM 仅在以下场景介入：
1. 候选 URL 排序（给 N 个候选，让 LLM 按相关性打分）
2. 网页类型识别（这是定价页还是博客？）
3. 异常页面摘要（采到一个奇怪的页面时让 LLM 概括）

### 3.6 自评估

```
低 confidence 触发条件：
- 某个 dimension 一个有效页面都没采到
- 多个页面 detected_paywall = True
- raw_text 长度 < 200 字符（怀疑抓取失败）
- robots_allowed = False 的页面占比 > 30%
```

### 3.7 特有错误码

| Code | 含义 |
|---|---|
| `ROBOTS_BLOCKED` | robots.txt 禁止抓取 |
| `PAYWALL_DETECTED` | 内容被付费墙阻挡 |
| `NO_RELEVANT_RESULTS` | 搜索零结果 |

### 3.8 合规

- 必须读取并尊重 `robots.txt`（除非用户配置 `respect_robots_txt=False` 且有合法理由）
- User-Agent 包含项目标识
- 单站点抓取频率不超过 1 req/s
- 详见 [COMPLIANCE.md](COMPLIANCE.md)

---

## 4. Extractor（抽取 Agent）

### 4.1 职责

把 `RawSourceDoc[]` 转换为符合 Schema 的结构化 `CompetitorProfile`，**同时**把支撑性事实切分为 `Evidence[]`（随 `ExtractorOutput.evidences` 返回，靠 `content_hash` 关联原文，**不入向量库**）。**不做对比分析**——那是 Analyst 的事。

### 4.2 输入

```python
class ExtractorInput(AgentInputBase):   # 继承 task_id/project_id/trace_id/span_id
    product_name: str
    industry_schema_id: str            # "collaboration_saas_v1"
    raw_sources: list[RawSourceDoc]
    schema_fields: list[str] | None = None  # 指定要抽哪些字段，None=全部
    qa_feedback: dict | None = None    # 重做时 QA 反馈（QAFeedback 序列化，dict 避循环依赖）
```

### 4.3 输出

```python
class ExtractorOutput(AgentOutputBase):
    profile:   CompetitorProfile       # 见 SCHEMA.md
    evidences: list[Evidence] = []     # 抽取过程产生的所有证据
    field_confidence: dict[str, float] = {}  # 字段级置信度，e.g. {"pricing.plans": 0.92}
    schema_version: str                # 对应 schemas.SCHEMA_VERSION（当前 "1.2.0"）
    unmatched_quotes: list[str] = []   # LLM 给的 source_quote 中匹配不上 raw_text 的部分（供自评估）
```

详细 `CompetitorProfile` / `Evidence` 结构见 [SCHEMA.md](SCHEMA.md)。

### 4.4 关键工具

- LLM 结构化抽取（`response_format=CompetitorProfile`）
- `TextChunker`（`backend/agents/extractor/tools.py`）：段落优先、按 token 上限切分的切片器，
  保留字符偏移，用于把长 `raw_text` 切片后逐块喂 LLM。**不是 ToolRegistry 工具，也不写向量库。**

> 注意：本项目**没有向量库 / RAG / embedding**。`Evidence.embedding_id` 始终为 `None`，
> `OpenAICompatibleLLM.embed()` 直接 `raise NotImplementedError`。证据与原文的关联完全靠
> `content_hash`（见 `extractor/agent.py` 的 `_mint_evidence`），不经任何向量检索。

### 4.5 Prompt 设计要点

**抽取分两步**（推荐拆成两次 LLM 调用，更稳）：

1. **粗抽取**：让 LLM 按 Schema 输出 JSON，附带每个字段的来源句子（"source_quote": "..."）
2. **证据绑定**：对每个 source_quote，按文本匹配回 raw_source，生成 Evidence 并赋 evidence_id；
   匹配不上的 quote 进 `unmatched_quotes`，对应字段降低 confidence

**关键原则**：
- 每个非空字段必须有 1 个以上 evidence_id 支撑
- 如果原文没说，必须填 `null`，**禁止编造**（这是 Extractor 抑制幻觉的核心）
- 长文本先用 `TextChunker` 切片再逐块抽取，不一次性塞 LLM

### 4.6 自评估

```
低 confidence 触发条件：
- > 20% 必填字段为 null
- > 30% 字段的 source_quote 匹配失败（进 unmatched_quotes）
- 字段值与多个来源出现矛盾
```

### 4.7 特有错误码

| Code | 含义 |
|---|---|
| `EVIDENCE_UNMATCHED` | LLM 产出的事实在 raw_sources 中找不到原文 |
| `SCHEMA_FIELD_MISSING` | 必填字段缺失 |
| `CONFLICTING_FACTS` | 不同来源对同一字段给出冲突值 |

---

## 5. Analyst（分析 Agent）

### 5.1 职责

对多个 `CompetitorProfile` 进行**对比分析**，输出 `AnalysisResult`。每个 claim 必须绑定 `evidence_ids`。

### 5.2 输入

```python
class AnalystInput(AgentInputBase):             # 继承 task_id/project_id/trace_id/span_id
    target_product:  str                       # "Notion"
    competitors:     list[str]                 # ["ClickUp", "Asana", "Trello"]
    profiles:        dict[str, CompetitorProfile]   # {product_name: profile}
    dimensions:      list[AnalysisDimension]
    evidence_store_handle: str | None = None   # 句柄字符串，可按 evidence_id 取详情
    qa_feedback:     dict | None = None        # QAFeedback 序列化（dict 避循环依赖）

class AnalysisDimension(str, Enum):
    FEATURE_COMPARISON   = "feature_comparison"
    PRICING_COMPARISON   = "pricing_comparison"
    USER_FEEDBACK        = "user_feedback"
    SWOT                 = "swot"
    DIFFERENTIATION      = "differentiation_opportunities"
    POSITIONING          = "positioning"
```

### 5.3 输出

```python
class AnalystOutput(AgentOutputBase):
    result: AnalysisResult

class AnalysisResult(BaseModel):
    target_product: str
    competitors:    list[str]
    dimensions:     dict[AnalysisDimension, DimensionAnalysis]

class DimensionAnalysis(BaseModel):
    dimension:   AnalysisDimension
    summary:     str                          # 维度总览
    claims:      list[AnalysisClaim]          # 具体结论
    comparison_matrix: dict | None = None     # 对比矩阵（feature/pricing 维度用）
    confidence:  float

class AnalysisClaim(BaseModel):
    claim_id:    str
    text:        str                          # "ClickUp 在自动化能力上强于 Notion"
    products_involved: list[str]
    evidence_ids: list[str]                   # ≥1，否则拒绝
    confidence:  float
    counter_evidence_ids: list[str] = []      # 反例证据，体现严谨
    qualifier:   str | None                   # "针对中型团队场景"
```

### 5.4 关键工具

- LLM 推理：evidence 池随输入直接传入（无向量检索 / RAG），LLM 只能从该池选 `evidence_ids`

### 5.5 Prompt 设计要点

- 每个维度独立 prompt，避免一次塞太长
- 强制要求 LLM 输出 `evidence_ids`，且**仅能**从输入随 profiles 提供的 evidence 池中选
- 鼓励输出 `counter_evidence_ids`：体现严谨，给质检 Agent 一个验证锚点
- 维度间禁止串结论（避免互相污染）

### 5.6 自评估

```
低 confidence 触发条件：
- 某 claim 的 evidence_ids 为空
- 输入 profile 的字段填充率 < 50%
- 多个竞品在同一维度上 profile 字段不对齐（无法对比）
```

### 5.7 特有错误码

| Code | 含义 |
|---|---|
| `INSUFFICIENT_EVIDENCE` | 某 claim 缺少支撑证据 |
| `PROFILE_INCOMPLETE` | 输入 profile 字段缺失影响对比 |
| `DIMENSION_NOT_APPLICABLE` | 行业 Schema 不支持该维度 |

---

## 6. Reporter（报告撰写 Agent）

### 6.1 职责

把 `AnalysisResult` 渲染为正式竞品分析报告（markdown 结构化）。**严格禁止引入未在 evidence/analysis 中出现的事实**。

### 6.2 输入

```python
class ReporterInput(AgentInputBase):   # 继承 task_id/project_id/trace_id/span_id
    project_name: str
    analysis: AnalysisResult
    template_id: str                   # "standard_v1" / "investor_v1" / "pm_v1"
    output_format: Literal["markdown", "html"] = "markdown"
    target_audience: str | None = None # "产品经理" / "投资人"
    qa_feedback: dict | None = None    # QAFeedback 序列化（dict 避循环依赖）
    # 返工时上一版草稿：配合 qa_feedback.must_address 做定向改稿（只重写命中 location 的 section），
    # None → 全篇生成（首轮 / 兜底）
    prior_draft: ReportDraft | None = None
```

### 6.3 输出

```python
class ReporterOutput(AgentOutputBase):
    draft: ReportDraft

class ReportDraft(BaseModel):
    report_id:    str
    version:      int = Field(ge=1)    # 1, 2, 3 ...（QA 退回时递增）
    template_id:  str
    sections:     list[ReportSection] = []
    summary:      str                  # 摘要
    metadata:     dict = {}            # 字数 / claim 数 / evidence 数

class ReportSection(BaseModel):
    section_id:  str
    title:       str                   # "定价策略对比"
    order:       int
    paragraphs:  list[ReportParagraph] = []

class ReportParagraph(BaseModel):
    paragraph_id: str
    text:         str
    claim_ids:    list[str] = []       # 引用了哪些 AnalysisClaim
    evidence_ids: list[str] = []       # 引用了哪些 Evidence（展开后）
    is_quantitative:   bool = False    # 含数字 / 价格 / 占比的段落要更严格校验
    is_soft_conclusion: bool = False   # True 时允许 evidence_ids 为空（"可能"、"通常" 等模糊表述）
```

> `evidence_ids` 在 schema 层**有意允许为空**（便于分步构建草稿）；"非软结论段落必须非空"
> 这一条件约束不在模型里，而由 `Reporter._post_validate` 在输出校验阶段拒绝（见 `reporter/agent.py`）。

### 6.4 引用强制规则

**这是 Reporter 抑制幻觉的核心**：

1. 报告中每个**事实性陈述**段落（`is_soft_conclusion=False`）必须有非空 `evidence_ids`
2. 缺 evidence_ids 的事实性段落由 `Reporter._post_validate` 在输出校验阶段标 `MISSING_CITATION`
3. 段落中的数字、价格、百分比、版本号必须可在 evidence 文本中找到，`is_quantitative=True` 时
   字面校验，找不到记 `UNVERIFIED_QUANTITY`（见 `reporter/agent.py:282`）
4. 软性结论（`is_soft_conclusion=True`，如"可能"、"通常"）允许 evidence_ids 为空

### 6.5 Prompt 设计要点

- 模板驱动：每个 template_id 对应一个固定结构（章节列表 + 每章节的写作指引）
- 分章节生成：每章节单独调用 LLM，避免 context 过长
- 输出强约束：`response_format=ReportSection`
- 风格规范：避免"行业标杆"、"绝对领先"等绝对化表述

### 6.6 自评估

```
低 confidence 触发条件：
- 引用强制校验失败的段落数 > 0（会直接 needs_rework）
- 报告总字数 < 模板下限
- 关键章节（如 SWOT）缺失
```

### 6.7 特有错误码

| Code | 含义 |
|---|---|
| `MISSING_CITATION` | 段落缺少 evidence_ids |
| `UNVERIFIED_QUANTITY` | 数字/价格在 evidence 中找不到原文 |
| `TEMPLATE_NOT_FOUND` | template_id 无效 |

---

## 7. QA（质检 Agent）

### 7.1 职责

对 `ReportDraft` 进行 **8 维度**审查，输出 `QAVerdict` + 路由决策。**不修改报告**，只负责诊断和路由。

### 7.2 输入

```python
class QAInput(AgentInputBase):             # 继承 task_id/project_id/trace_id/span_id
    draft: ReportDraft
    analysis: AnalysisResult
    profiles: dict[str, CompetitorProfile]
    evidence_store_handle: str | None = None
    prior_verdicts: list[QAVerdict] = []   # 历史质检结果，避免无限循环
    # 上游各 Agent 的自评状态（如 {'collector':'needs_rework'}），QA 据此给已有 issue 加权
    upstream_statuses: dict[str, str] = {}
```

### 7.3 输出

```python
class QAOutput(AgentOutputBase):
    verdict: QAVerdict

class QAVerdict(BaseModel):
    verdict_id: str
    overall_status: QAStatus
    dimension_results: dict[QADimension, QADimensionResult]
    issues: list[QAIssue]
    routing: list[QARouting]        # 路由决策
    blocking: bool                  # True = 必须重做，False = 可发布但有改进建议

class QAStatus(str, Enum):
    PASS              = "pass"
    NEEDS_REVISION    = "needs_revision"
    REJECT            = "reject"               # 严重不合格

class QADimension(str, Enum):           # 共 8 维度（backend/schemas/qa.py:25-35）
    FACT_CONSISTENCY      = "fact_consistency"       # 事实一致性
    EVIDENCE_COMPLETENESS = "evidence_completeness"  # 证据完整性
    SCHEMA_COMPLETENESS   = "schema_completeness"    # Schema 完整性
    LOGIC_CONSISTENCY     = "logic_consistency"      # 逻辑一致性
    FRESHNESS             = "freshness"              # 时效性
    EXPRESSION            = "expression"             # 表达规范性
    COVERAGE_DENSITY      = "coverage_density"       # 覆盖密度
    IDENTITY_CONSISTENCY  = "identity_consistency"   # 产品身份一致性（拦截"抓错产品"）

class QADimensionResult(BaseModel):
    dimension: QADimension
    score:     float = Field(ge=0, le=1)
    pass_:     bool       # 字段名为 pass_（alias="pass"），避免关键字冲突
    notes:     str = ""

class QAIssue(BaseModel):
    issue_id:    str
    dimension:   QADimension
    severity:    Literal["minor", "major", "critical"]
    location:    str                  # "report.sections[3].paragraphs[2]"
    problem:     str                  # "声明 ClickUp 有 AI 写作，但 evidence 中未提及"
    suggested_fix: str
    target_agent: Literal["collector", "extractor", "analyst", "reporter"]
    required_inputs: dict             # 给目标 Agent 的补充指令

class QARouting(BaseModel):
    target_agent: Literal["collector", "extractor", "analyst", "reporter"]
    reason:       str
    payload:      dict                # 作为 qa_feedback 传给目标 Agent
```

### 7.4 8 维度规则详见

[QA.md](QA.md) 给出每个维度的具体规则、阈值和示例。

### 7.5 校验手段

QA 没有专门的 NLP / 检索工具，蕴含与矛盾判定都以 **LLM-as-judge** 内联实现：

- 蕴含判定：`fact_consistency` checker 对每个事实性段落调 LLM，输出 entailed / contradicted /
  neutral 标签（`backend/agents/qa/checkers/fact_consistency.py`），蕴含失败记 `ENTAILMENT_FAILED`
- 矛盾判定：`logic_consistency` checker 内联用 LLM 检查段落间是否自相矛盾
- 量化字面校验：`is_quantitative` 段落做数字字面匹配（±5% 容差）
- 证据原文随 `QAInput`（draft + analysis + profiles 中的 evidence）直接传入，无独立 lookup 工具

### 7.6 Prompt 设计要点

- 各维度独立检查（每个 checker 独立 prompt）
- 结构化输出：每个 issue 明确指向具体段落/句子
- 给 routing 决策时附 payload：告诉上游 Agent 具体要补什么

### 7.7 防无限循环

`prior_verdicts` 长度超过阈值（默认 3）时：
- 如果同一类 issue 反复出现 → 标 `blocking=False`，允许发布但在报告中标注"未完全验证"
- 触发 `MAX_RETRY_REACHED` 错误码，记录 trace

### 7.8 特有错误码

| Code | 含义 | 出处 |
|---|---|---|
| `ENTAILMENT_FAILED` | claim 与 evidence 蕴含校验失败 | `qa/checkers/fact_consistency.py:169` |
| `MAX_RETRY_REACHED` | 反复重做仍未通过 | `qa/routing.py:496` |

---

## 8. Orchestrator（编排器）

> 不是业务 Agent，但作为协作枢纽列在此处供接口对齐。详细设计见 [DAG.md](DAG.md)。

### 8.1 职责

- 接收项目配置，生成 DAG（v1 用固定模板，v2 用自适应规划）
- 按拓扑顺序调度节点，处理依赖
- 接收 QA 路由，触发上游重做
- 管理节点状态（六态）
- 注入 trace_id / span_id
- 向前端推送实时状态（WebSocket）

### 8.2 与 Agent 的接口

```python
# 完整定义见 backend/schemas/orchestrator.py（此处略去可观测/计量字段）
class NodeExecutionRequest(BaseModel):
    project_id: str
    task_id:    str
    node_id:    str
    agent_name: str
    input:      AgentInputBase         # 多态：CollectorInput / ExtractorInput / ...
    trace_id:   str
    span_id:    str
    parent_span_id: str | None = None
    enqueued_at:    datetime | None = None

class NodeExecutionResult(BaseModel):
    project_id: str
    node_id:    str
    status:     NodeStatus             # six-state
    output:     AgentOutputBase | None = None
    error:      AgentError | None = None
    next_nodes: list[str] = []         # Orchestrator 用于推进 DAG
    # 另含透传的 trace/span、started_at/ended_at、tokens、cost_usd、metadata 等计量字段
```

---

## 9. 各 Agent 实现 Checklist

每个 Agent 实现窗口完成后必须满足（架构窗口逐项审查）：

- [ ] 目录结构：`backend/agents/<name>/{__init__.py, agent.py, prompts/, tools.py, README.md}`
- [ ] 继承 `BaseAgent`，正确声明 `input_model` / `output_model`
- [ ] Pydantic 模型与本文档完全一致（不私自扩字段）
- [ ] LLM 调用通过 `LLMProvider`，prompt 在 `prompts/` 外置
- [ ] 至少 3 个单元测试：正常 case / 边界 case / 异常 case
- [ ] Mock 模式可用：`Agent(mock=True).invoke(mock_input)` 返回预期输出
- [ ] 完整 `AgentOutputBase` 字段（status / confidence / self_critique / tokens / errors）
- [ ] 引用强制（Reporter）/ 证据匹配（Extractor）等关键约束已实现
- [ ] `README.md` 说明：本 Agent 做什么、输入输出、运行方式、已知限制
- [ ] 提供示例 fixture 用于联调

---

## 10. 跨 Agent 的反馈消息

QA → 上游 Agent 的反馈通过统一的 `QAFeedback` 字段传递：

```python
class QAFeedback(BaseModel):
    from_verdict_id: str
    issues: list[QAIssue] = []
    instructions: str = ""             # 具体改进指令
    must_address: list[str] = []       # 必须解决的 issue_id
    revision: int = 0                  # 当前返工轮次，供 Reporter bump ReportDraft.version

# 注入时序列化为 dict（各 Agent 输入的 qa_feedback 字段为 dict | None，避免 schemas 循环依赖）；
# 消费侧用 validate_qa_feedback(payload) 在边界做 fail-soft 容错校验。
```

每个 Agent 在接收 `qa_feedback` 时：
1. 解析 `must_address` 列表
2. 针对每个 issue 调整 prompt / 重新采集 / 重新分析
3. 在新输出的 `self_critique` 中说明如何回应了反馈

---

## 11. 版本管理

- 本文档与 `backend/schemas/__init__.py` 中的 `SCHEMA_VERSION` 同步
- 任何字段变更：major（删/改类型）、minor（新增可选）、patch（注释/校验）
- 变更须走 PR + 架构窗口审查 + 通知所有 Agent 窗口

当前版本：**v1.2.0**（与 `backend/schemas/__init__.py:7` 的 `SCHEMA_VERSION` 一致）

> v1.2.0：`Evidence` / `RawSourceDoc` 新增 `trust_level`（`trusted|untrusted`，默认
> `untrusted`）、`tainted`（默认 `False`）、`taint_reasons`（默认 `[]`）——间接 prompt
> injection 防御（WI-1）。Collector 抓取后用 `backend/tools/injection_guard.py` 扫描标记，
> Extractor 铸 Evidence 时继承，QA `identity_consistency` 据 `tainted` 提权回 collector。
> 向后兼容：旧 JSON 缺这三字段时按默认值反序列化，现有 Agent IO 不变（Minor bump）。
>
> v1.1.0：`Evidence` 新增可选 `source_published_at`（向后兼容，旧 JSON 默认 `None`）。
> 见 `backend/schemas/__init__.py`。Minor bump：纯增量，现有 Agent 输入输出 schema 不变。
>
> `NodeExecutionRequest` / `NodeExecutionResult`（§ 8.2）也已在 `backend/schemas/orchestrator.py`
> 落地，为 storage 层 `EventBusProtocol` 提供消息载荷类型。
