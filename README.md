# AI 驱动的 B 端 SaaS 竞品分析 Agent 协作平台

> 多智能体 DAG 编排 · 结构化竞品知识 Schema · 证据链可溯源 · 质检反馈闭环 · 全链路可观测

面向 B 端 SaaS 竞品分析场景，本平台通过 **5 个专职 Agent**（采集 / 抽取 / 分析 / 报告 / 质检）协同工作，自动完成从公开信息采集、知识结构化、对比分析、报告撰写到质检审查的全流程，输出一份带证据来源、可追踪过程、可复用结构化数据的竞品分析报告。

## 核心特性

| 特性 | 落地点 |
|---|---|
| 5 Agent 专职分工 | 采集 / 抽取 / 分析 / 报告 / 质检 + Orchestrator 编排器 |
| 结构化消息契约 | Agent 间走 Pydantic / JSON Schema，**非自然语言对话** |
| DAG 任务编排 | 节点状态机 + 反馈边，支持并行采集、条件分支、质检回流 |
| 自适应任务拆分 | Orchestrator 根据 query 复杂度动态生成 DAG，不写死 |
| 竞品知识 Schema | 通用 Schema + 行业扩展（协作办公 / CRM / 跨境电商 / 教育 SaaS） |
| 证据链可溯源 | 每个结论绑定 `evidence_id`，UI 一键跳转原文 |
| 幻觉抑制四层 | 结构化输出 + 引用强制 + 自一致性 + QA 反馈 |
| Agent 自评估 | 每个 Agent 输出 confidence + self-critique |
| 决策回放 | 时间轴 UI 可重放任意节点的 prompt / input / output |
| 业务闭环指标 | 准确率 / 覆盖率 / 人工修正率 仪表盘 |
| 合规 | robots.txt 检查 / ToS / 数据脱敏 / 来源声明 |

## 系统架构概览

```
┌─────────────────────────────────────────────────────┐
│  用户交互层  React/Next.js                          │
│  项目配置 · DAG 可视化 · 报告查看 · 证据溯源        │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────┐
│  业务应用层  FastAPI                                │
│  项目管理 · 任务管理 · 报告版本 · 指标仪表盘        │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────┐
│  Agent 协作编排层  LangGraph                        │
│  DAG 调度 · 状态管理 · 反馈闭环 · Trace 记录        │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────┐
│  多智能体执行层                                     │
│  采集 · 抽取 · 分析 · 报告 · 质检                   │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────┐
│  Schema 层      Pydantic 模型 + JSON Schema         │
│  通用 + 行业扩展 + Evidence + Trace                 │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────┐
│  存储层  PostgreSQL · Chroma · Redis                │
│  原始网页 · Evidence · 结构化 KB · 日志 · 报告       │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────┐
│  模型与工具层  LLMProvider 抽象                     │
│  Claude / DeepSeek / Qwen · Tavily · Firecrawl · RAG│
└─────────────────────────────────────────────────────┘
```

## 项目结构

```
.
├── backend/
│   ├── agents/             # 5 个专职 Agent，各窗口实现
│   │   ├── collector/      # 信息采集 Agent
│   │   ├── extractor/      # 结构化抽取 Agent
│   │   ├── analyst/        # 竞品分析 Agent
│   │   ├── reporter/       # 报告撰写 Agent
│   │   └── qa/             # 质检 Agent
│   ├── orchestrator/       # DAG 编排器（LangGraph）
│   ├── schemas/            # Pydantic 模型与 JSON Schema
│   ├── storage/            # PG / Chroma / Redis 适配
│   ├── llm/                # LLMProvider 抽象（Claude/DS/Qwen）
│   ├── tools/              # 搜索 / 爬虫 / RAG 工具
│   ├── observability/      # Trace / Token 计量
│   └── api/                # FastAPI 路由
├── frontend/               # React/Next.js 前端
├── fixtures/
│   └── mock_data/          # Mock 数据，供 Agent 独立开发使用
└── docs/
    ├── ARCHITECTURE.md     # 系统架构
    ├── AGENTS.md           # 5 Agent 接口契约（实现窗口必读）
    ├── SCHEMA.md           # 竞品知识 Schema
    ├── DAG.md              # 任务编排设计
    ├── EVIDENCE.md         # 证据链与溯源
    ├── QA.md               # 质检规则与反馈
    ├── OBSERVABILITY.md    # Trace 与可观测
    ├── HALLUCINATION_CONTROL.md  # 幻觉抑制策略
    ├── METRICS.md          # 业务指标体系
    ├── COMPLIANCE.md       # 合规与数据安全
    ├── INNOVATIONS.md      # 前瞻性技术亮点
    ├── WORKBREAKDOWN.md    # 多窗口分工与里程碑
    └── CONVENTIONS.md      # 编码与协作规范
```

## 5 Agent 速查

| Agent | 输入 | 输出 | 关键工具 |
|---|---|---|---|
| Collector | 产品名 + 维度 + 约束 | RawSourceDoc[] | Tavily / Firecrawl / Playwright |
| Extractor | RawSourceDoc[] + 行业 Schema | CompetitorProfile + Evidence[] | LLM + JSON Schema 校验 |
| Analyst | CompetitorProfile[] + 维度 | AnalysisResult（每条 claim 绑 evidence_id） | LLM + RAG |
| Reporter | AnalysisResult + 模板 | ReportDraft（结构化 markdown） | LLM + 引用强制 |
| QA | ReportDraft + Evidence + Profile | QAVerdict + 路由决策 | LLM + 规则校验 |

详细契约见 [docs/AGENTS.md](docs/AGENTS.md)。

## 文档导航

> 完整文档清单与按角色推荐阅读顺序见 [docs/README.md](docs/README.md)。下表是快速跳转。

| 看什么 | 看哪 |
|---|---|
| 全部文档目录 | [docs/README.md](docs/README.md) |
| 我要总体了解 | [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 我要实现一个 Agent | [AGENTS.md](docs/AGENTS.md) + [SCHEMA.md](docs/SCHEMA.md) |
| 我要做编排器 | [DAG.md](docs/DAG.md) + [QA.md](docs/QA.md) |
| 我要做前端 | [OBSERVABILITY.md](docs/OBSERVABILITY.md) + [METRICS.md](docs/METRICS.md) |
| 我是 PM 想看分工 | [WORKBREAKDOWN.md](docs/WORKBREAKDOWN.md) |
| 我想看亮点 | [INNOVATIONS.md](docs/INNOVATIONS.md) |
| 我关心合规 | [COMPLIANCE.md](docs/COMPLIANCE.md) |
| 我要部署 / 现场演示 | [DEPLOY.md](docs/DEPLOY.md) |
| 我想看真实链路怎么调通的 | [E2E_INTEGRATION_LOG.md](docs/E2E_INTEGRATION_LOG.md) |

## 评分标准映射

| 评分维度 | 落地文档 |
|---|---|
| 多 Agent 协作与输出可信度 | AGENTS / SCHEMA / EVIDENCE / QA |
| 技术深度与工程完整度 | ARCHITECTURE / OBSERVABILITY / HALLUCINATION_CONTROL / DAG |
| 业务价值与产品体验 | METRICS / 前端 UI 设计 |
| 代码质量与文档 | CONVENTIONS / 全套 docs |
| 合规、材料与答辩 | COMPLIANCE / 答辩材料（待补） |

## Prerequisites

| 工具 | 版本 |
|---|---|
| Python | ≥ 3.12 |
| Node.js | ≥ 20 |
| npm | ≥ 10 |

可选：Docker + Docker Compose（仅在使用 Postgres / Redis 持久化时需要；默认走内存模式）。

## Installation

```bash
git clone <repo-url>
cd competitive-analysis-agent-platform
```

### Backend

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

可选 extras：

```bash
pip install -e ".[dev,tools-search]"          # Playwright 抓取兜底
pip install -e ".[dev,tools-crawl4ai]"        # Crawl4AI（SPA 站点），装完跑：
python -m playwright install chromium

pip install -e ".[dev,export-pdf-docx]"       # 让 GET /api/projects/{id}/export?format=pdf|docx 生效
```

### Frontend

```bash
cd frontend
npm install
cd ..
```

## Configuration

### 后端 `.env`

```bash
cp .env.example .env
```

必填（至少一组 LLM provider，默认走 Doubao）：

| 变量 | 说明 |
|---|---|
| `DOUBAO_API_KEY` | 火山方舟 API key |
| `DOUBAO_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` |
| `DOUBAO_MODEL` | 推理接入点 ID（`ep-...`），推荐带「联网搜索」插件 |

可选：

| 变量 | 用途 |
|---|---|
| `TAVILY_API_KEY` / `SERPER_API_KEY` | 外部 Web 搜索；不填走 Doubao 自带 |
| `FIRECRAWL_API_KEY` | SPA 抓取；不填走 httpx + Playwright |
| `STORAGE_MODE` | `memory`（默认）/ `postgres` |
| `POSTGRES_DSN` / `REDIS_URL` | 仅 `STORAGE_MODE=postgres` 时需要 |
| `QA_MAX_ROUNDS` | QA 反馈循环上限，默认 `3` |

`.env` 已被 `.gitignore` 屏蔽，不会进版本控制。

### 前端 `frontend/.env.local`

```bash
cd frontend
cp .env.local.example .env.local
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | 后端 FastAPI 地址 |
| `NEXT_PUBLIC_WS_BASE` | 从 API base 派生 | 跨域 / TLS 场景手动覆盖 |

## Running

需要两个终端：

```bash
# Terminal 1 — backend
source .venv/bin/activate
uvicorn backend.api.app:app --reload --port 8000
```

```bash
# Terminal 2 — frontend
cd frontend
npm run dev
```

访问 [http://localhost:3000](http://localhost:3000)。

> 持久化模式：`docker compose up -d postgres redis`，详见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 测试

```bash
# 不发 LLM 请求的单元 + 集成测试（72 项，秒过）
pytest backend/orchestrator/tests backend/api/tests backend/storage/tests/test_memory.py backend/storage/tests/test_serde.py -q

# 真实 LLM 全链路（需 API key + ~5-10 分钟）
RUN_REAL_LLM_TESTS=1 pytest backend/api/tests/test_real_full_chain.py -v -s
```

## 协作方式

多窗口分工开发：

- **本仓库的架构、文档、Schema、接口契约**由架构窗口统一维护
- **每个 Agent 实现**由独立窗口负责，遵循 [docs/AGENTS.md](docs/AGENTS.md) 的契约
- 集成节奏与里程碑见 [docs/WORKBREAKDOWN.md](docs/WORKBREAKDOWN.md)
- 编码与提交规范见 [docs/CONVENTIONS.md](docs/CONVENTIONS.md)

