# 竞品知识 Schema

> 本文档定义系统所有结构化数据模型。**这是 Agent 之间通信的唯一契约。**所有 Schema 均以 Pydantic 模型为权威定义，本文档展示等价 JSON 形式 + 字段说明。

---

## 1. 总览

| Schema 族 | 包含 | 维护者 |
|---|---|---|
| **业务 Schema** | CompetitorProfile（通用 + 行业扩展）、AnalysisResult、ReportDraft | 架构窗口 |
| **基础设施 Schema** | Evidence、TraceRecord、AgentInput/Output、DAGNode | 架构窗口 |
| **行业扩展** | collaboration_saas / crm_saas / cross_border_ecommerce_saas | 架构窗口 + 各 Agent 窗口提需求 |

**版本号**：当前 v1.0.0，存放于 `backend/schemas/__init__.py`。

**变更原则**：
- 字段删除 / 类型变更 → major bump，必须走 PR
- 新增可选字段 → minor bump
- 注释 / 校验规则微调 → patch

---

## 2. 通用竞品 Schema：CompetitorProfile

每个竞品的完整画像。**通用字段 + 行业扩展字段**。所有业务 Schema 字段都强类型，禁止使用 `dict[str, Any]`。

### 2.1 顶层结构

```python
class CompetitorProfile(BaseModel):
    profile_id:    str
    schema_version: str                # "1.0.0"
    industry:      str                 # "collaboration_saas"

    basic_info:    ProductBasicInfo
    features:      FeatureProfile
    pricing:       PricingProfile
    user_feedback: UserFeedbackProfile
    competitive:   CompetitiveAnalysis

    # 行业扩展（按 industry 切换）
    industry_extension: IndustryExtensionUnion | None = None

    # 元信息
    extracted_at:  datetime
    field_confidence: dict[str, float] # 字段级置信度
    field_status:    dict[str, FieldStatus]
```

```python
class FieldStatus(str, Enum):
    VERIFIED      = "verified"          # 有 evidence 支撑
    UNVERIFIED    = "unverified"        # LLM 抽取但 evidence 匹配失败
    UNKNOWN       = "unknown"           # 原文未提及
    CONFLICTING   = "conflicting"       # 多源冲突
```

### 2.2 基础信息

```python
class ProductBasicInfo(BaseModel):
    name:             str
    company:          str | None
    official_website: HttpUrl | None
    category:         str               # "项目管理 SaaS"
    positioning:      str | None        # 产品定位一句话
    target_users:     list[UserSegment]
    main_scenarios:   list[str]
    founded_year:     int | None
    headquarters:     str | None
    languages_supported: list[str] = []

    # 每个字段的证据
    evidence_refs: dict[str, list[str]] = {}   # field_name → evidence_ids

class UserSegment(BaseModel):
    name:       str                     # "中小企业产品团队"
    size_range: str | None              # "10-200 人"
    industry:   str | None
```

### 2.3 功能画像

```python
class FeatureProfile(BaseModel):
    core_features:           list[Feature]
    feature_modules:         list[FeatureModule]
    differentiated_features: list[Feature]      # 与竞品差异化
    integration_capabilities: list[Integration]
    security_and_permission: SecurityProfile | None
    ai_capabilities:         list[Feature] = []  # AI 能力单列，便于对比

    evidence_refs: dict[str, list[str]] = {}

class Feature(BaseModel):
    name:        str                    # "看板视图"
    description: str | None
    availability: PlanAvailability      # 哪些 plan 提供
    tags:        list[str] = []         # ["view", "visualization"]

class FeatureModule(BaseModel):
    module_name: str                    # "任务管理"
    features:    list[str]              # 该模块下的功能名
    maturity:    Literal["preview", "beta", "ga"] | None

class Integration(BaseModel):
    target:      str                    # "Slack"
    type:        Literal["native", "marketplace", "api", "webhook"]
    notes:       str | None

class SecurityProfile(BaseModel):
    sso_support:        list[str] = []  # ["SAML", "OIDC"]
    audit_log:          bool | None
    data_residency:     list[str] = []  # ["US", "EU", "JP"]
    compliance:         list[str] = []  # ["SOC2", "ISO27001", "GDPR"]
    permission_model:   str | None      # "RBAC" / "ABAC" / "自由分享"

class PlanAvailability(BaseModel):
    free:       bool = False
    paid:       bool = False
    enterprise_only: bool = False
    plan_names: list[str] = []          # ["Free", "Plus", "Business"]
```

### 2.4 定价画像

```python
class PricingProfile(BaseModel):
    pricing_model:  PricingModel        # subscription / usage / freemium / hybrid
    plans:          list[PricingPlan]
    free_trial:     FreeTrialInfo | None
    billing_cycle:  list[str] = []      # ["monthly", "annual"]
    currency_supported: list[str] = []  # ["USD", "CNY"]
    enterprise_contact_required: bool = False

    evidence_refs: dict[str, list[str]] = {}

class PricingModel(str, Enum):
    FREE         = "free"
    FREEMIUM     = "freemium"
    SUBSCRIPTION = "subscription"
    USAGE_BASED  = "usage_based"
    HYBRID       = "hybrid"
    OPEN_SOURCE  = "open_source"

class PricingPlan(BaseModel):
    name:           str                 # "Plus"
    price_per_seat_monthly_usd: float | None
    price_per_seat_annual_usd:  float | None
    min_seats:      int | None
    max_seats:      int | None          # null = 不限
    target_segment: str | None          # "small_team"
    included_features: list[str] = []
    limits:         dict[str, str] = {} # {"storage": "5GB", "api_calls": "1000/mo"}

class FreeTrialInfo(BaseModel):
    available:    bool
    duration_days: int | None
    requires_credit_card: bool | None
```

### 2.5 用户反馈

```python
class UserFeedbackProfile(BaseModel):
    overall_rating:  float | None       # 综合分（如 G2 4.5/5）
    review_count:    int | None
    review_sources:  list[str] = []     # ["G2", "Capterra"]

    positive_themes: list[FeedbackTheme]
    negative_themes: list[FeedbackTheme]
    user_pain_points: list[PainPoint]
    typical_reviews: list[TypicalReview]

    evidence_refs: dict[str, list[str]] = {}

class FeedbackTheme(BaseModel):
    theme:        str                   # "易上手"
    mention_count: int | None
    sentiment:    Literal["positive", "negative", "mixed"]
    sample_quotes: list[str] = []
    evidence_ids: list[str] = []

class PainPoint(BaseModel):
    pain:         str                   # "复杂项目下卡顿"
    affected_segment: str | None
    severity:     Literal["low", "medium", "high"]
    evidence_ids: list[str] = []

class TypicalReview(BaseModel):
    source:       str
    rating:       float | None
    quote:        str
    reviewer_role: str | None
    review_date:  datetime | None
    evidence_id:  str
```

### 2.6 竞争分析（基础部分）

> 注意：这里只是 **profile 自带的 self-assessment**。真正的多产品对比由 Analyst 产出 `AnalysisResult`，见 § 3。

```python
class CompetitiveAnalysis(BaseModel):
    strengths:        list[Insight]
    weaknesses:       list[Insight]
    opportunities:    list[Insight]
    threats:          list[Insight]
    recommendations:  list[Insight] = []

class Insight(BaseModel):
    text:         str
    rationale:    str | None
    evidence_ids: list[str] = []
    confidence:   float = Field(ge=0, le=1)
```

### 2.7 行业扩展

```python
# 协作办公 / 项目管理
class CollaborationSaasExtension(BaseModel):
    industry_id: Literal["collaboration_saas"] = "collaboration_saas"

    task_management:       MaturityScore | None
    kanban_view:           MaturityScore | None
    calendar_view:         MaturityScore | None
    document_collaboration: MaturityScore | None
    workflow_automation:   MaturityScore | None
    knowledge_base:        MaturityScore | None
    team_permission:       MaturityScore | None
    third_party_integration: MaturityScore | None
    mobile_support:        MaturityScore | None
    realtime_editing:      MaturityScore | None

    evidence_refs: dict[str, list[str]] = {}

# CRM
class CrmSaasExtension(BaseModel):
    industry_id: Literal["crm_saas"] = "crm_saas"
    lead_management:        MaturityScore | None
    customer_lifecycle:     MaturityScore | None
    sales_pipeline:         MaturityScore | None
    sales_automation:       MaturityScore | None
    customer_segmentation:  MaturityScore | None
    reporting_dashboard:    MaturityScore | None
    marketing_integration:  MaturityScore | None
    evidence_refs: dict[str, list[str]] = {}

# 跨境电商
class CrossBorderEcommerceSaasExtension(BaseModel):
    industry_id: Literal["cross_border_ecommerce_saas"] = "cross_border_ecommerce_saas"
    store_builder:      MaturityScore | None
    payment_support:    MaturityScore | None
    logistics_support:  MaturityScore | None
    multi_language:     MaturityScore | None
    multi_currency:     MaturityScore | None
    plugin_ecosystem:   MaturityScore | None
    marketing_tools:    MaturityScore | None
    order_fulfillment:  MaturityScore | None
    evidence_refs: dict[str, list[str]] = {}

IndustryExtensionUnion = Annotated[
    CollaborationSaasExtension | CrmSaasExtension | CrossBorderEcommerceSaasExtension,
    Field(discriminator="industry_id")
]

class MaturityScore(BaseModel):
    has_capability:  bool
    maturity_level:  Literal["none", "basic", "standard", "advanced", "best_in_class"]
    notes:           str | None
    evidence_ids:    list[str] = []
```

**扩展约定**：
- 新行业 = 新增一个 `*Extension` 模型 + 加入 `IndustryExtensionUnion`
- 不允许在通用 Schema 中加行业特有字段
- 每个扩展用 `industry_id` 作为 discriminator

---

## 3. 分析结果：AnalysisResult

由 Analyst 产出，详见 [AGENTS.md](AGENTS.md) § 5。关键约束：

- 每个 `AnalysisClaim` 必须有 `evidence_ids`（≥1）
- 支持 `counter_evidence_ids`（反例，体现严谨）
- `confidence` 强制 [0,1]

---

## 4. 报告：ReportDraft

由 Reporter 产出，详见 [AGENTS.md](AGENTS.md) § 6。关键约束：

- 报告每个事实性段落必须有 `evidence_ids`
- 数字 / 价格 / 百分比段落额外标记 `is_quantitative=True`，QA 会做更严格检查
- 版本号递增，旧版本保留供回放

---

## 5. 证据：Evidence

```python
class Evidence(BaseModel):
    evidence_id:   str                  # "ev_<uuid8>"
    source_id:     str                  # 关联到 RawSourceDoc
    product_name:  str
    source_url:    HttpUrl
    source_type:   str                  # "pricing_page" / "review" / "blog"
    source_authority: float             # 0.6 - 0.95

    content:       str                  # 证据原文片段
    content_hash:  str                  # 去重用
    context_before: str | None          # 前文（便于人类阅读）
    context_after:  str | None
    location:      EvidenceLocation     # 在原文中的位置

    language:      str                  # "en" / "zh"
    collected_at:  datetime
    extracted_at:  datetime
    confidence:    float                # 抽取置信度

    tags:          list[str] = []       # ["pricing", "feature"]
    embedding_id:  str | None           # 向量库主键

class EvidenceLocation(BaseModel):
    char_start: int | None
    char_end:   int | None
    selector:   str | None              # CSS selector / xpath
    page_section: str | None            # "<h2>Pricing</h2>" 这一节
```

详细使用规则见 [EVIDENCE.md](EVIDENCE.md)。

---

## 6. 原始来源：RawSourceDoc

详见 [AGENTS.md](AGENTS.md) § 3.3。

---

## 7. Trace 记录

```python
class TraceRecord(BaseModel):
    trace_id:    str                    # 一个项目 / 任务的根 trace
    span_id:     str                    # 单次 Agent 调用
    parent_span_id: str | None

    agent_name:  str
    agent_version: str
    node_id:     str | None             # DAG 节点 id

    started_at:  datetime
    ended_at:    datetime | None
    status:      AgentStatus

    # 完整 LLM 调用流水
    llm_calls:   list[LLMCallRecord]

    # 工具调用流水
    tool_calls:  list[ToolCallRecord]

    # 输入输出快照（敏感字段已脱敏）
    input_snapshot:  dict
    output_snapshot: dict

    # 度量
    tokens_input:  int
    tokens_output: int
    cost_usd:      float
    duration_ms:   int

    self_critique: str
    confidence:    float

class LLMCallRecord(BaseModel):
    call_id:     str
    model:       str
    system_prompt: str
    messages:    list[dict]             # 完整 messages
    response:    dict                   # 完整响应
    tokens_input: int
    tokens_output: int
    finish_reason: str
    duration_ms: int

class ToolCallRecord(BaseModel):
    call_id:     str
    tool_name:   str
    arguments:   dict
    result:      dict
    duration_ms: int
    error:       str | None
```

详细可观测性设计见 [OBSERVABILITY.md](OBSERVABILITY.md)。

---

## 8. DAG 节点

```python
class DAGNode(BaseModel):
    node_id:     str
    project_id:  str
    agent_name:  str | None             # None = 控制节点（start/end/merge）
    node_type:   NodeType
    status:      NodeStatus

    input_refs:  list[str]              # 上游 node_id
    output_ref:  str | None             # 输出落到哪

    retry_count: int = 0
    max_retries: int = 3

    started_at:  datetime | None
    ended_at:    datetime | None

class NodeType(str, Enum):
    START        = "start"
    END          = "end"
    AGENT_CALL   = "agent_call"
    PARALLEL_FORK = "parallel_fork"
    PARALLEL_JOIN = "parallel_join"
    CONDITIONAL  = "conditional"
    FEEDBACK     = "feedback"

class NodeStatus(str, Enum):
    PENDING       = "pending"
    READY         = "ready"            # 依赖满足，等待调度
    RUNNING       = "running"
    SUCCESS       = "success"
    FAILED        = "failed"
    NEEDS_REWORK  = "needs_rework"     # QA 退回
    SKIPPED       = "skipped"
```

详细编排设计见 [DAG.md](DAG.md)。

---

## 9. 项目配置

```python
class Project(BaseModel):
    project_id:    str
    project_name:  str
    owner:         str
    created_at:    datetime

    target_product: str
    competitors:    list[str]
    industry:       str                 # "collaboration_saas"
    industry_schema_version: str

    analysis_dimensions: list[AnalysisDimension]
    report_template_id:  str            # "standard_v1"
    target_audience:     str | None

    mode:           Literal["mock", "hybrid", "real"] = "hybrid"
    collect_constraints: CollectConstraints

    # 状态
    status:         ProjectStatus
    current_report_id: str | None
    metrics:        ProjectMetrics | None

class ProjectStatus(str, Enum):
    DRAFT      = "draft"
    PLANNING   = "planning"
    RUNNING    = "running"
    REVIEWING  = "reviewing"
    DONE       = "done"
    FAILED     = "failed"
```

---

## 10. 业务指标

```python
class ProjectMetrics(BaseModel):
    accuracy:       float    # 见 METRICS.md
    coverage:       float
    edit_rate:      float    # 人工修正率
    evidence_count: int
    fields_filled_ratio: float
    total_tokens:   int
    total_cost_usd: float
    duration_seconds: int
    qa_round_count: int      # QA 循环次数
```

详见 [METRICS.md](METRICS.md)。

---

## 11. 实现位置约定

```
backend/schemas/
├── __init__.py            # SCHEMA_VERSION = "1.0.0"
├── agent_io.py            # AgentInputBase / AgentOutputBase / AgentError / AgentStatus
├── collector.py           # CollectorInput/Output, RawSourceDoc
├── extractor.py           # ExtractorInput/Output
├── analyst.py             # AnalystInput/Output, AnalysisResult, AnalysisClaim
├── reporter.py            # ReporterInput/Output, ReportDraft, ReportSection
├── qa.py                  # QAInput/Output, QAVerdict, QAIssue, QARouting
├── competitor.py          # CompetitorProfile, FeatureProfile, PricingProfile, ...
├── industry/
│   ├── __init__.py        # IndustryExtensionUnion + registry
│   ├── collab_saas.py
│   ├── crm_saas.py
│   └── cross_border.py
├── evidence.py            # Evidence, EvidenceLocation, RawSourceDoc
├── trace.py               # TraceRecord, LLMCallRecord, ToolCallRecord
├── dag.py                 # DAGNode, NodeType, NodeStatus
└── project.py             # Project, ProjectMetrics, CollectConstraints
```

**架构窗口承诺**：M0 时点这些文件全部成型，其他窗口直接 import 即可。

---

## 12. JSON Schema 导出

为了支持非 Python 端（前端 TS 类型、答辩材料）：

```bash
# 全量导出
python -m backend.schemas.export --out schemas/json/
# 输出：schemas/json/CompetitorProfile.schema.json 等
```

前端通过 `openapi-typescript` 直接从 FastAPI 拿到类型，无需手维护。
