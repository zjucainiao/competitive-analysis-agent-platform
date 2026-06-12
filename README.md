# 竞品分析 Agent 协作平台

> 基于 **LangGraph 多 Agent 编排**的竞品情报自动生成平台:输入一个产品，系统自动
> 联网检索 → 结构化抽取 → 竞品分析 → 撰写报告 → 多维质检，产出一份**带证据溯源、
> 过程可回放**的竞品分析报告。

```
采集与结构化  →  分析  →  撰写  →  质检（不合格自动打回返工）
 Collector       Analyst   Reporter   QA
 + Extractor
```

| 阶段 | 内部 Agent | 干什么 |
|---|---|---|
| **1 · 采集与结构化** | Collector + Extractor | 联网检索 + 网页抓取 → LLM 抽取到 Schema + 证据链编织 |
| **2 · 分析** | Analyst | 多竞品对比 / 单产品深度调研，每条结论绑定证据 |
| **3 · 撰写** | Reporter | 结构化报告生成 + 引用强制校验 + 反幻觉自修复 |
| **4 · 质检** | QA | 多维度自动审查，不合格触发反馈环、定向打回上游重做 |

更完整的技术说明（技术栈 / AI 能力 / 工程难点 / 评测体系）见
[docs/PROJECT_SUBMISSION.md](docs/PROJECT_SUBMISSION.md)。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Next.js 16 · React 19 · TypeScript 5 · Tailwind CSS 4 · SWR |
| 后端 | Python 3.12 · FastAPI · **LangGraph**（多 Agent 编排）· Pydantic 2 · JWT + bcrypt |
| 大模型 | DeepSeek（`deepseek-chat`）/ 豆包 Seed（OpenAI 兼容），环境变量切换 |
| 检索/抓取 | 搜索 Tavily / Serper / DuckDuckGo；抓取 Firecrawl → Playwright → httpx 多级降级 |
| 存储 | PostgreSQL 16（SQLAlchemy async + asyncpg）· Redis 7（事件总线）；另有内存模式 |
| 部署 | Docker Compose · Caddy 自动 HTTPS · OpenTelemetry 可观测 |

> 采用**实时联网检索增强**（非向量库 RAG）——竞品情报要的是最新事实，每次现搜现抓、带 URL 溯源。

---

## 项目结构

```
.
├── backend/
│   ├── agents/            # 5 个专职 Agent
│   │   ├── collector/     # 联网检索 + 网页抓取
│   │   ├── extractor/     # 结构化抽取（LLM → Schema）
│   │   ├── analyst/       # 竞品分析
│   │   ├── reporter/      # 报告撰写
│   │   └── qa/            # 多维质检 + 返工路由
│   ├── orchestrator/      # LangGraph 编排状态机（native 引擎）
│   ├── schemas/           # Pydantic 模型 / JSON Schema
│   ├── storage/           # memory / postgres + redis 适配
│   ├── llm/               # LLM provider 抽象（DeepSeek / 豆包）
│   ├── tools/             # 工具与脱敏
│   ├── observability/     # Trace / Token 计量
│   └── api/               # FastAPI 路由（入口 backend.api.app:app）
├── frontend/              # Next.js 前端
├── scripts/localdb.py     # 本地零安装数据层（嵌入式 PG + Redis）
├── docker-compose.yml     # 本地起 Postgres + Redis
├── docker-compose.prod.yml# 生产全栈编排
└── docs/                  # 设计与说明文档
```

---

## 本地部署

### 0. 前置要求

| 工具 | 版本 |
|---|---|
| Python | ≥ 3.12 |
| Node.js | ≥ 20（npm ≥ 10） |

Docker 可选——只有要持久化（Postgres/Redis）时才需要；默认内存模式零依赖即可跑通。

### 1. 克隆 + 装后端

```bash
git clone <repo-url>
cd competitive-analysis-agent-platform

python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

可选 extras：

```bash
pip install -e ".[dev,tools-search]"        # Playwright 抓取兜底
pip install -e ".[dev,tools-crawl4ai]"      # Crawl4AI（SPA 站点）
python -m playwright install chromium       # 装上面两个后执行
pip install -e ".[dev,export-pdf-docx]"     # 启用报告导出 PDF/DOCX
```

### 2. 配置后端 `.env`

```bash
cp .env.example .env
```

至少填**一组 LLM provider**，二选一：

```bash
# 方案 A · DeepSeek（最简单）
DEEPSEEK_API_KEY=sk-xxxx

# 方案 B · 豆包 Seed（火山方舟，OpenAI 兼容；填了优先用豆包）
DOUBAO_API_KEY=xxxx
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
DOUBAO_MODEL=ep-xxxx        # 推理接入点 ID
```

可选（不填有默认行为）：

| 变量 | 用途 |
|---|---|
| `TAVILY_API_KEY` / `SERPER_API_KEY` | 外部 Web 搜索，提升采集质量 |
| `FIRECRAWL_API_KEY` | SPA 站点抓取；不填走 httpx + Playwright |
| `STORAGE_MODE` | `memory`（默认）/ `postgres` |
| `QA_MAX_ROUNDS` | QA 返工上限，默认 `3` |
| `AUTH_ALLOWED_EMAILS` | 注册/登录白名单；不设=开放注册 |

`.env` 已被 `.gitignore` 屏蔽，不会进版本库。

### 3. 装前端 + 配地址

```bash
cd frontend
npm install
cp .env.local.example .env.local      # 默认 NEXT_PUBLIC_API_BASE=http://localhost:8000
cd ..
```

### 4. 启动（开两个终端）

```bash
# 终端 1 · 后端
source .venv/bin/activate
uvicorn backend.api.app:app --reload --port 8000
```

```bash
# 终端 2 · 前端
cd frontend
npm run dev
```

打开 **http://localhost:3000** 即可使用。后端健康检查：`curl http://localhost:8000/health`。

### 5.（可选）持久化存储

默认 `STORAGE_MODE=memory`，重启即清空。要持久化把 `.env` 改成 `STORAGE_MODE=postgres`，
再起 PG + Redis，二选一：

```bash
# 方式一 · 有 Docker（推荐）
docker compose up -d postgres redis

# 方式二 · 无 Docker，零安装嵌入式数据层
python scripts/localdb.py up         # 起 pg+redis 并打印 DSN / REDIS_URL
# 把打印出的 POSTGRES_DSN / REDIS_URL 填回 .env
# 停止：python scripts/localdb.py down
```

`.env` 默认连接串已对齐 Docker 方式（`postgresql+asyncpg://app:app@localhost:5432/app`）。

---

## 生产部署

Docker Compose 全栈（Caddy 自动 HTTPS + 前端 + 后端 + Postgres + Redis）。
完整步骤见 [docs/DEPLOY_PROD.md](docs/DEPLOY_PROD.md)：

```bash
cp .env.prod.example .env.prod        # 填域名 / 邮箱 / DB 密码 / API key
# 注意必须带 --env-file：compose 用 ${DOMAIN} 等替换，默认不读 .env.prod
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

---

## 测试

```bash
# 不发 LLM 请求的单元 + 集成测试（秒过）
pytest backend/orchestrator/tests backend/api/tests \
       backend/storage/tests/test_memory.py backend/storage/tests/test_serde.py -q

# 真实 LLM 全链路（需 API key，约 5-10 分钟）
RUN_REAL_LLM_TESTS=1 pytest backend/api/tests/test_real_full_chain.py -v -s
```

---

## 文档导航

所有文档均严格对照代码维护，可与对应源码 docstring 互相引用。

| 主题 | 文档 |
|---|---|
| 技术总览 | [docs/PROJECT_SUBMISSION.md](docs/PROJECT_SUBMISSION.md) |
| 系统架构 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Agent 接口契约 | [docs/AGENTS.md](docs/AGENTS.md) |
| 竞品知识 Schema | [docs/SCHEMA.md](docs/SCHEMA.md) |
| 编排（DAG / 状态机） | [docs/DAG.md](docs/DAG.md) |
| 质检与返工闭环 | [docs/QA.md](docs/QA.md) |
| 证据链与溯源 | [docs/EVIDENCE.md](docs/EVIDENCE.md) |
| 幻觉抑制策略 | [docs/HALLUCINATION_CONTROL.md](docs/HALLUCINATION_CONTROL.md) |
| 可观测性（Trace / 回放） | [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) |
| 业务指标 | [docs/METRICS.md](docs/METRICS.md) |
| 存储层契约 | [docs/STORAGE.md](docs/STORAGE.md) |
| 合规与数据安全 | [docs/COMPLIANCE.md](docs/COMPLIANCE.md) |
| 技术亮点 | [docs/INNOVATIONS.md](docs/INNOVATIONS.md) |
| 生产部署 Runbook | [docs/DEPLOY_PROD.md](docs/DEPLOY_PROD.md) |
| 前端设计系统 / 产品语境 | [DESIGN.md](DESIGN.md) · [PRODUCT.md](PRODUCT.md) |

---

## 贡献

欢迎 PR。开发环境、自检命令（lint / 类型 / 测试）与代码约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

[MIT](LICENSE) © competitive-analysis-agent-platform contributors
