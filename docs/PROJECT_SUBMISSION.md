# 竞品分析 Agent 平台 · 项目技术说明

> 一套基于 LangGraph 多 Agent 编排的竞品情报自动生成平台:输入一个产品，
> 系统自动联网检索 → 抽取 → 分析 → 撰写 → 质检，产出带证据溯源的竞品分析报告。

---

## 一、核心技术栈

| 层 | 技术选型 |
|---|---|
| **前端** | Next.js 16.2（App Router）+ React 19.2 + TypeScript 5；Tailwind CSS 4 设计令牌体系；SWR 做数据请求/缓存；ESLint 9。构建时注入 `NEXT_PUBLIC_API_BASE`，产物为 SSR / 静态混合。 |
| **后端** | Python 3.12 + FastAPI（ASGI / Uvicorn）；**LangGraph 0.2 + langchain-core 0.3** 做多 Agent 编排状态机；Pydantic 2.7 全链路 schema 校验；JWT + bcrypt 鉴权；OpenTelemetry 链路追踪；python-ulid 主键。 |
| **数据库** | **PostgreSQL 16**（SQLAlchemy 2.0 async + asyncpg）——持久化运行状态 + LangGraph checkpoint（可中断 / 续跑）；另有内存模式给测试、嵌入式 PG 给本地开发（零安装）。 |
| **中间件** | **Redis 7** 事件总线（`redis.asyncio`）——实时进度流式推送到前端；**Caddy** 反向代理。 |
| **部署环境** | Docker Compose 全栈编排（Caddy + 前端 + 后端 + Postgres + Redis 五容器）；**Caddy 自动 HTTPS**（Let's Encrypt 自动签发 / 续期）；仅 Caddy 对公网暴露 80 / 443，其余服务仅内网互通。 |
| **云资源** | 自有 Linux VM（Ubuntu 24.04）。**全栈自建、无托管云依赖**——数据库、检索、推理均在本机，降低成本与数据合规风险。 |

---

## 二、大模型 / AI 能力使用说明

### 用了哪些模型 / API
- 主力 **DeepSeek**（`deepseek-chat`，推理任务可用 `deepseek-reasoner`），可热切 **豆包 Seed 1.6**（火山方舟，OpenAI 兼容）。
- 统一走 **OpenAI 兼容客户端**，环境变量选 provider（豆包优先，DeepSeek / OpenAI 兜底），内置按 token 计价表估算调用成本。
- 检索 / 抓取 API：搜索 **Tavily / Serper / DuckDuckGo**；网页抓取 **Firecrawl → Crawl4AI / Playwright → httpx** 多级降级。

### Agent 方案（系统核心）
**LangGraph StateGraph 多 Agent 流水线**，5 个专职 Agent：

```
START → collect_dispatch ─(Send 扇出)→ collect_one ─┐
                                                     ↓ (barrier)
                              extract_dispatch ─(扇出)→ extract_one ─┐
                                                                     ↓ (barrier)
                                          analyst → reporter → qa
                                                                  │
                          qa 经 Command(goto=…) 条件路由 ──────────┤
                                                                  ├─→ 返工上游可修节点
                                                                  └─→ END
```

| Agent | 职责 |
|---|---|
| `collector` | 联网检索 + 网页抓取，产出带 URL 的原始证据 |
| `extractor` | 从原始网页结构化抽取字段（定价 / 功能 / 定位…） |
| `analyst` | 竞品维度分析（SWOT、对比、差异化） |
| `reporter` | 组织成结构化报告草稿，逐 section 自我修正 |
| `qa` | 多维度质检，按质量把任务**定向打回**最上游可修节点重跑 |

### RAG / 向量库方案（关键设计取舍）
**不走向量库 RAG，走「实时联网检索增强」（search-augmented generation）。**
竞品情报要的是**最新**事实（定价、功能、动态），而非静态知识库——因此每次现搜现抓、带
URL 溯源，而非预先 embedding 入库。Evidence 模型预留 `embedding_id` 字段，可平滑升级到
向量召回，当前 v1 不启用。

### Prompt 方案
每个 Agent 独立 system prompt + **逐 section 自我修正**（self-correct）；QA 反馈以结构化
`must_address` 回灌，reporter **只定向重写被点名的段落**、其余复用上一稿。

### 模型在系统中的位置
LLM 只在**各 Agent 节点内部**调用（抽取 / 分析 / 撰写 / 质检都是 LLM 推理）；
**编排、路由、校验、持久化、溯源全是确定性代码**，不交给模型——保证流程可控、可复现、可观测。

---

## 三、关键工程难点与解决方案

### 难点 1 · 多 Agent 并发与流水线超时崩溃
- **问题**：一个 Agent 内对 N 个数据源**串行**调 LLM（逐个抽取），极易撞节点超时，表现为
  "upstream output missing"、节点 failed，整条流水线 abort。
- **解决**：用 LangGraph `Send` 把 collector / extractor 按数据源**扇出并行**（dispatch
  节点 → 多 worker 并发 → barrier 汇聚再进下游），并把节点超时与并发度联调到位。改后超时崩溃归零。

### 难点 2 · 质检闭环「诚实 + 真提质」（项目最硬的一块）
- **问题**：QA 原本只数 issue 权重、不看维度分 → 维度不及格却**静默放行**；返工是**无状态
  整篇重生成** → 反馈对质量的边际贡献几乎为零；指标只取末轮，可能越改越差。
- **解决**：
  1. 维度 score 低于阈值**强制补发 issue**（`synthesize_threshold_issues`），杜绝低分静默放行；
  2. 核心维度（事实一致性 / 证据完整性 / schema 完整性）失败才升级为**阻塞返工**；
  3. reporter 引入 `prior_draft`，**只定向重写命中段落**，反馈真正落到稿子上；
  4. **best-round 择优发布** + 跨轮 delta 度量，杜绝倒退；
  5. 同一 issue 跨轮重复 ≥3 次自动降级 minor + 非阻塞，**防死循环**。

### 难点 3 · 「抓错产品」的身份漂移
- **问题**：分析「钉钉」却抓回「飞书」内容（同类竞品互相串味），导致整篇报告建立在错误实体上。
- **解决**：**4 阶段身份校验闭环**——collector 混合检测 → Evidence 继承产品身份 →
  QA 设专门的 `identity_consistency` 维度核验 → 用 `exclude_source_urls` 收敛、剔除错源重采。

### 难点 4 · 报告产出洁净度（前后端联调）
- **问题**：报告预览 / 导出会泄露枚举裸值、英文术语、空维度占位、章节跳号——暴露内部实现。
- **解决**：确立「报告洁净不变量」，展示前统一兜底清洗（英文→中文、内部命名脱敏、空维度剔除、
  章节重新连号），前后端枚举 / schema 通过适配层对齐。

### 难点 5 · 部署与单用户安全
- Docker Compose 全栈 + Caddy 自动签发 / 续期 HTTPS，仅边缘暴露公网；
- `AUTH_ALLOWED_EMAILS` 在**注册 + 登录双闸**实现单用户锁定；
- 踩平前端 `NEXT_PUBLIC_API_BASE` 必须 **build 时**注入这一典型坑。

---

## 四、评测体系（质量如何度量与保证）

系统的「评测」由 **QA Agent + 跨轮度量** 两部分构成：既给每份报告打分判级，又度量「多轮返工
是否真的越改越好」。

### 4.1 多维度评分
QA 对每份草稿沿 **8 个维度**逐项打分（每维度 `score ∈ [0,1]` + `pass_` 布尔）：

| 维度 | 含义 |
|---|---|
| `fact_consistency` | 事实一致性（与证据是否冲突、数字是否对得上） |
| `evidence_completeness` | 证据完整性（论断是否有溯源支撑） |
| `schema_completeness` | 字段完整性（结构化字段是否齐全） |
| `coverage_density` | 覆盖度 / 信息密度 |
| `logic_consistency` | 逻辑一致性（前后章节是否自相矛盾） |
| `freshness` | 时效性（信息是否够新） |
| `expression` | 表达质量（语言、可读性） |
| `identity_consistency` | 产品身份一致性（防「抓错产品」） |

### 4.2 加权判级（`aggregate_verdict`）
按 issue 严重度加权汇总，映射到置信度与是否阻塞返工：

| 严重度 | 权重 |
|---|---|
| minor | 1 |
| major | 5 |
| critical | 20 |

| 总权重 / 条件 | 判级 status | blocking | confidence |
|---|---|---|---|
| 权重 = 0 | PASS | 否 | 0.90 |
| 0 < 权重 ≤ 10 | NEEDS_REVISION | 否（浮出但不强返工） | 0.75 |
| 10 < 权重 ≤ 25 | NEEDS_REVISION | **是** | 0.60 |
| 权重 > 25 或 ≥ 2 个 critical | REJECT | **是** | 0.40 |
| 任一**核心维度**失败 | 至少 NEEDS_REVISION | **是** | ≤ 0.60 |
| 触达轮次上限强制放行 | 反映客观（PASS / NEEDS_REVISION） | 否 | 0.55 / 0.70 |

> 核心维度 = fact_consistency / evidence_completeness / schema_completeness（「数据层可真修」）。
> 这三个不及格才**强制阻塞**返工；其余维度只「浮出」为低权重 issue，不强行卡发布。

### 4.3 不静默放行
若某维度 `score` 低于阈值却没有对应 issue，`synthesize_threshold_issues` 会**自动补发**
一条 issue（核心维度→major，其余→minor）——杜绝「分数不及格但悄悄发布」。

### 4.4 跨轮度量与择优发布
多轮返工时，`_scores_per_round` 计算：

- **`per_round_accuracy`**：每轮 verdict 的维度均分（轮次顺序）；
- **`round_delta`**：相邻轮差值，量化「这一轮返工带来了多少改善」；
- **`best_round`**：维度均分最高的轮次。

**择优发布**：触达轮次上限时，**发布历史最高分轮**（best-round）而非最后一轮，避免「越改越差还
发了末轮」。同时若某轮相比上一轮**无实质改善**（delta 低于阈值），提前止损、不再空转返工。

### 4.5 防死循环
同一 `dimension|location` 的 issue 跨轮重复 ≥ 3 次（确实改不动的维度）自动降级为 minor + 非阻塞，
最终降级放行并由 best-round 兜底，避免无限返工。

---

*本文档基于代码实测整理（版本、库、模型、链路、评测规则均可在仓库中对应到具体实现）。*
