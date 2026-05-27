# 系统架构

> 本文档定义系统总体架构、模块边界、数据流和技术选型。各 Agent / 编排器 / 前端的详细规约见对应专项文档。

## 1. 系统定位

平台把"竞品分析"这一过去依赖人工经验的工作流，转化为**可编排、可复用、可审查、可量化**的智能化流程。

不做的事：
- 不是单 LLM 一次性生成报告的 wrapper
- 不是固定流程的 RPA 脚本
- 不替代分析师的最终判断，而是提供结构化证据 + 可解释过程

做的事：
- 多 Agent 分工，每个 Agent 职责单一、可独立测试
- 任务流可视化、可观测、可回放
- 报告中每个结论绑定证据
- 质检结果可触发上游 Agent 重做（真实闭环）

## 2. 六层架构

```
┌────────────────────────────────────────────────────────────┐
│ L1 用户交互层  (frontend/)                                  │
│   项目配置 · DAG 可视化 · 报告查看 · 证据溯源 · 指标仪表盘   │
├────────────────────────────────────────────────────────────┤
│ L2 业务应用层  (backend/api/)                               │
│   项目 / 任务 / 报告 / 指标 REST + WebSocket                │
├────────────────────────────────────────────────────────────┤
│ L3 Agent 编排层  (backend/orchestrator/)                    │
│   LangGraph StateGraph · 自适应 DAG · 反馈回流 · Trace      │
├────────────────────────────────────────────────────────────┤
│ L4 Agent 执行层  (backend/agents/)                          │
│   Collector · Extractor · Analyst · Reporter · QA           │
├────────────────────────────────────────────────────────────┤
│ L5 Schema 层  (backend/schemas/)                            │
│   通用 + 行业扩展 + Evidence + Trace + AgentIO              │
├────────────────────────────────────────────────────────────┤
│ L6 存储层  (backend/storage/)                               │
│   PostgreSQL · Chroma · Redis · 对象存储                    │
├────────────────────────────────────────────────────────────┤
│ L7 模型与工具层  (backend/llm/, backend/tools/)             │
│   LLMProvider · 搜索 · 网页抓取 · RAG · 校验                │
└────────────────────────────────────────────────────────────┘
```

> 注：图示是 7 层（含模型/工具底座），业务上仍以"六层"对外表述（L1–L6），L7 作为基础设施。

### L1 用户交互层

承载所有用户可见的能力。**关键页面**：

| 页面 | 用途 | 关键交互 |
|---|---|---|
| 项目配置 | 创建分析任务 | 选择行业模板、配置竞品列表、勾选分析维度 |
| DAG 监控 | 实时观测任务流 | 节点状态颜色、流转动画、点击查看节点详情 |
| Agent 详情 | 单节点回放 | Prompt / Input / Output / Token / Tool calls |
| 报告查看 | 阅读最终报告 | hover 高亮证据、点击跳转原文、人工编辑 |
| 证据库 | 浏览采集证据 | 按产品 / 来源 / 时间筛选 |
| 指标仪表盘 | 业务闭环 | 准确率 / 覆盖率 / 人工修正率 |

### L2 业务应用层

FastAPI 提供 REST API + WebSocket（DAG 实时进度推送）。**关键资源**：

- `/projects` 竞品分析项目
- `/tasks` DAG 任务节点
- `/reports` 报告版本（初稿 / 修订 / 终稿）
- `/evidences` 证据条目
- `/traces` Agent 执行 trace
- `/metrics` 业务指标

### L3 Agent 编排层

基于 **LangGraph** 实现 `StateGraph`。**核心职责**：

- 节点定义：每个节点对应一个 Agent 调用或工具调用
- 边定义：依赖边 + 反馈边（条件分支）
- 状态管理：六态状态机（pending / running / success / failed / needs_rework / skipped）
- 自适应规划：Orchestrator 不是固定 DAG，而是根据 query 复杂度动态生成节点
- Trace 注入：每个节点执行自动生成 `trace_id` + `span_id`

详见 [DAG.md](DAG.md)。

### L4 Agent 执行层

**5 个 Agent，单一职责**：

```
Collector  → 公开信息采集（HTML + 摘要 + URL + 抓取时间）
Extractor  → 非结构化 → CompetitorProfile + Evidence[]
Analyst    → CompetitorProfile[] → AnalysisResult（每 claim 绑 evidence_id）
Reporter   → AnalysisResult → ReportDraft（结构化 markdown）
QA         → ReportDraft → QAVerdict + 路由决策
```

**强约束**：Agent 之间**只**通过 Pydantic 模型通信，禁止自然语言对话。任意两个 Agent 的接口冻结后，可独立替换实现而不影响其他 Agent。

详见 [AGENTS.md](AGENTS.md)。

### L5 Schema 层

所有跨模块数据都走 Pydantic 模型 + JSON Schema 校验。**关键 Schema 族**：

- **业务 Schema**：CompetitorProfile（通用 + 行业扩展）、AnalysisResult、ReportDraft
- **基础设施 Schema**：Evidence、TraceRecord、AgentInput/Output、DAGNode
- **校验**：LLM 输出走 `response_format=json_schema`（或 tool_use）+ 二次 Pydantic 校验

详见 [SCHEMA.md](SCHEMA.md)。

### L6 存储层

| 存储 | 数据 | 选型理由 |
|---|---|---|
| PostgreSQL | 项目 / 任务 / 报告 / Evidence 元数据 / Trace | 关系完整、事务、JSONB 灵活 |
| Chroma | Evidence chunk 向量索引 | 轻量、Python 友好、本地可跑 |
| Redis | DAG 状态、任务队列、缓存 | 实时、低延迟 |
| 本地文件 / S3 | 原始 HTML、PDF、截图 | 体积大、对象存储 |

### L7 模型与工具层

**LLMProvider 抽象**：所有 Agent 通过统一接口调用，默认 Claude，可切换 DeepSeek / Qwen / OpenAI。统一管理 token 计量、重试、降级、cache。

**工具集**（每个 Agent 按需声明依赖）：
- 搜索：Tavily / Serper
- 抓取：Firecrawl / Playwright（fallback）
- RAG：Chroma + 自建 retriever
- 校验：JSON Schema validator、引用解析器

## 3. 端到端数据流（举例）

> 例：用户分析"Notion vs ClickUp vs Asana，关注协作办公场景"

```
[用户] 提交项目配置
   │  POST /projects
   ▼
[API] 持久化 Project，触发 Orchestrator
   │  enqueue(project_id)
   ▼
[Orchestrator] 根据 query 复杂度生成 DAG
   │  生成节点：3 个 Collector × 4 个维度，3 个 Extractor，5 个 Analyst，1 个 Reporter，1 个 QA
   ▼
[Collector × N] 并行采集
   │  调用 Tavily 搜索 → Firecrawl 抓取 → 存 raw HTML 到对象存储
   │  输出 RawSourceDoc[]
   ▼
[Extractor × N] 抽取结构化竞品知识
   │  对每个产品分别抽取 → CompetitorProfile（按行业 Schema）
   │  同时生成 Evidence[] 并写入向量库
   ▼
[Analyst × N] 多维度对比分析
   │  feature_comparison / pricing_comparison / swot / opportunities / pain_points
   │  每个 claim 绑定 evidence_ids[]
   ▼
[Reporter] 生成 ReportDraft
   │  按模板组装 markdown，引用强制（无 evidence_id 的 claim 直接拒绝）
   ▼
[QA] 6 维度审查
   │  ├─ pass     → 写入 final_report，通知前端
   │  └─ revise   → 路由回 Collector / Extractor / Analyst / Reporter
   ▼
[Orchestrator] 根据 QA 路由决策，触发上游 Agent 重做（带 QA 反馈消息）
   │  循环直到 pass 或重试次数耗尽
   ▼
[用户] 前端实时看到 DAG 节点状态变化、报告版本演进、证据库增长
```

所有节点执行同时写入 `TraceRecord`，用户可在任意时刻打开"决策回放"查看任意节点的完整 prompt / input / output / token / 工具调用。

## 4. 关键模块边界

| 模块 | 不允许做的事 | 必须做的事 |
|---|---|---|
| Agent | 不直接读数据库（除 Evidence RAG） | 通过 `AgentInput` 接收所有上下文 |
| Agent | 不自己写 trace 日志 | 通过 `BaseAgent.invoke()` 自动注入 trace |
| Agent | 不互相直接调用 | 只通过 Orchestrator 路由 |
| Orchestrator | 不做业务推理 | 只负责调度、状态、路由 |
| Reporter | 不引入未在 Evidence 库的事实 | 每个 claim 必须有 `evidence_ids` |
| QA | 不修改报告内容 | 只输出 verdict + issue + 路由决策 |
| 前端 | 不直接读 LLM | 一切通过 API + WebSocket |

## 5. 技术选型

| 层 | 选型 | 备注 |
|---|---|---|
| 后端框架 | FastAPI | 异步、Pydantic 原生支持 |
| 包管理 | uv | 速度快、锁文件稳定 |
| Agent 编排 | LangGraph | StateGraph、条件分支、checkpoint |
| LLM 默认 | Claude Sonnet 4.6（开发）/ Claude Opus 4.7（关键路径） | 工具调用稳定、JSON 输出可靠 |
| LLM 备选 | DeepSeek / Qwen / GPT-4.x | 通过 LLMProvider 抽象切换 |
| 数据库 | PostgreSQL 16 | JSONB 用于 Schema 字段 |
| 向量库 | Chroma | 本地可跑、零运维 |
| 缓存 / 队列 | Redis 7 | DAG 状态 + Stream |
| 搜索 | Tavily（主） / Serper（备） | 学术友好、配额合理 |
| 抓取 | Firecrawl（主） / Playwright（备） | 复杂站点用 Playwright |
| 前端 | Next.js 14 + React 18 + TypeScript | 服务端渲染、文件路由 |
| UI 库 | shadcn/ui + Tailwind | 轻、定制度高 |
| 可视化 | React Flow（DAG） + ECharts（指标） | 主流方案 |
| 实时 | WebSocket（FastAPI 原生） | DAG 状态推送 |
| 可观测 | LangSmith（可选） + 自建 Trace 表 | 不强依赖外部 |
| 容器化 | Docker Compose | 本地一键启动 |

## 6. 部署架构（简化）

```
                ┌───────────────┐
                │   Browser     │
                └───────┬───────┘
                        │ HTTPS / WSS
                        ▼
                ┌───────────────┐
                │  Next.js Web  │  (frontend)
                └───────┬───────┘
                        │
                        ▼
                ┌───────────────┐
                │   FastAPI     │  (backend/api)
                └───┬───────┬───┘
                    │       │
        ┌───────────┘       └──────────┐
        ▼                              ▼
  ┌──────────┐                  ┌────────────┐
  │ Postgres │                  │ Redis      │
  └──────────┘                  └─────┬──────┘
                                      │
                                      │ task queue
                                      ▼
                        ┌──────────────────────┐
                        │  Orchestrator Worker │  (LangGraph)
                        └─────────┬────────────┘
                                  │ invoke
                                  ▼
                        ┌──────────────────────┐
                        │ Agents (in-process)  │
                        └─────────┬────────────┘
                                  │
                  ┌───────────────┼────────────────┐
                  ▼               ▼                ▼
            ┌─────────┐    ┌──────────┐    ┌──────────────┐
            │ Chroma  │    │ LLM API  │    │ Tools API    │
            └─────────┘    │ Claude…  │    │ Tavily/      │
                           └──────────┘    │ Firecrawl    │
                                           └──────────────┘
```

v1 阶段 Orchestrator + Agents 可以同进程跑，后期分进程通过 Redis Stream 解耦。

## 7. 设计原则

1. **单一职责**：每个 Agent 只做一件事，跨职责的需求 → 拆 Agent，不 → 扩 prompt
2. **结构化优先**：Agent 间永远走 Pydantic 模型，自然语言只出现在 LLM prompt 内部
3. **证据先行**：任何分析结论都必须可追溯到 Evidence；引用强制在生成阶段，不靠 QA 兜底
4. **可观测优于可解释**：每一步都留痕（prompt / input / output / token），用户随时可回放
5. **Schema 即契约**：Schema 变更必须走 PR + 版本号，不允许各窗口私下扩字段
6. **降级优于失败**：关键节点失败时优先返回部分结果（标注 partial），而非整个流程崩溃
7. **真实数据 + Mock 兜底**：Demo 演示时真实采集，网络异常自动 fallback 到预置数据
