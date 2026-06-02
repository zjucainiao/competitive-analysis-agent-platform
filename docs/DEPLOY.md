# 部署指南

> 三种部署形态：本地 demo（零依赖）/ docker-compose 全栈（PG + Redis + Jaeger）/ 生产
>
> 默认 demo 用第一种最快上手。

---

## 1. 系统依赖

| 必需 | 用途 |
|---|---|
| Python ≥ 3.12 | 运行后端 |
| Node.js ≥ 20 + pnpm | 运行前端（F 窗口） |
| 至少一个 LLM API key | DOUBAO / DEEPSEEK / OPENAI 任一 |

| 可选 | 用途 |
|---|---|
| Docker + docker-compose | 切到 Postgres / Redis / Jaeger 后端 |
| TAVILY / SERPER API key | 真实搜索（豆包 EP 已自带联网搜索时可不要） |

---

## 2. 本地 demo（最快 1 分钟）

```bash
git clone <repo>
cd competitive-analysis-agent-platform

# Python venv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 配 LLM key（最简一份）
cp .env.example .env
# 编辑 .env，至少填一组：
#   DOUBAO_API_KEY=xxx
#   DOUBAO_MODEL=ep-2026xxxx-xxxxx    （在火山方舟控制台创建带"联网搜索"插件的 EP）

# 跑 API
uvicorn backend.api.app:app --reload --port 8000
```

启动日志看到 `API started (mode=memory, agent_mode=real, schema=1.1.0)` 即成功。

测试 demo：

```bash
curl -X POST http://localhost:8000/api/projects \
  -H 'Content-Type: application/json' \
  -d '{
    "project_name": "demo",
    "owner": "u",
    "target_product": "Notion",
    "competitors": ["Asana"],
    "industry": "collaboration_saas"
  }'

# 拿到 project_id 后启动 run（异步，立即返回）
curl -X POST http://localhost:8000/api/projects/<pid>/run

# 看实时进度
wscat -c ws://localhost:8000/api/projects/<pid>/events

# 等大约 5 分钟后拿完整状态
curl http://localhost:8000/api/projects/<pid>/state | jq
```

**注意**：`mode=memory` 时所有数据存内存，进程重启全丢。生产 / 多次演示要切 PG。

---

## 3. docker-compose 全栈（PG + Redis + Jaeger）

适合现场演示、答辩材料、长跑数据保留。

```bash
# 起 3 个服务
docker compose up -d postgres redis jaeger

# 等 healthcheck 都绿（约 10 秒）
docker compose ps

# 配 .env 加这几行（其他 LLM key 不变）
cat >> .env <<EOF
STORAGE_MODE=postgres
POSTGRES_DSN=postgresql+asyncpg://app:app@localhost:5432/app
REDIS_URL=redis://localhost:6379/0

# OTLP Tracer（让 LLM call / agent span 落 Jaeger UI）
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_SERVICE_NAME=competitive-analysis-agent
EOF

# 启动 API（lifespan 启动时跑 init_storage 自动建表）
uvicorn backend.api.app:app --reload --port 8000
```

观察：

- **数据落 PG**：`docker exec -it cap-postgres psql -U app -d app -c '\dt'`
  看到 `projects / dag_plans / node_outputs / qa_verdicts` 表
- **事件走 Redis**：`docker exec -it cap-redis redis-cli MONITOR` 看到 `PUBLISH project:...:nodes`
- **Trace 落 Jaeger**：跑完一次 e2e 后打开 `http://localhost:16686` →
  Service 选 `competitive-analysis-agent` → 看到完整 span tree（每个 Agent + 内部 LLM call）

---

## 4. 单测前置

```bash
# 单元 + API 测试（无外部依赖）
pytest backend/orchestrator/tests backend/api/tests backend/storage/tests/test_memory.py backend/storage/tests/test_serde.py -q

# PG / Redis e2e（需 docker compose up）
export POSTGRES_DSN=postgresql+asyncpg://app:app@localhost:5432/app
export REDIS_URL=redis://localhost:6379/0
pytest backend/storage/tests/test_postgres_e2e.py backend/storage/tests/test_redis_e2e.py -q

# 真实 LLM 全链路（耗 token）
export RUN_REAL_LLM_TESTS=1
pytest backend/api/tests/test_real_full_chain.py -v -s
```

---

## 5. 生产部署要点

### 5.1 数据库

- Postgres：建议托管（RDS / Cloud SQL）；启用连接池（asyncpg 默认池够用）
- Redis：托管即可（ElastiCache / Memorystore）；只用于 pub/sub，不存关键数据

### 5.2 LLM Provider

- 推荐豆包 Seed EP（自带联网搜索，省 Tavily/Serper 费用）
- 备用 DeepSeek（最便宜，无搜索）
- key 走 secrets manager，不要直接 .env 文件挂上去

### 5.3 横向扩展

- API 层无状态（state 全在 PG/Redis），可以多副本 + LB 直接堆
- 后台 run 任务靠 LangGraph checkpoint 跨进程恢复
- 同一 project 同时只允许一个 run（API 已有 409 防御）

### 5.4 监控

- Jaeger 看 trace（开发用够；生产可换 Tempo / Datadog）
- structlog JSON 输出可直接进 Loki / Datadog / CloudWatch
- 关键告警：QA reject 率 / 反馈环 max_rounds 命中率 / 单次 run token 数

### 5.5 合规

- 详见 [COMPLIANCE.md](COMPLIANCE.md)：robots.txt 默认开启，PII sanitizer 在
  `backend/tools/sanitizer.py`
- 用户上传访谈数据时**必须**走 sanitizer + 同意书

---

## 6. 切换前端

前端项目在 `frontend/`（F 窗口）。`.env.local` 写：

```
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

```bash
cd frontend
pnpm install
pnpm dev   # http://localhost:3000
```

前端会订阅 `WS /api/projects/{id}/events` 实时刷 DAG 视图、报告、指标仪表盘。

---

## 7. 故障排查

| 现象 | 原因 | 修法 |
|---|---|---|
| 启动报 `no LLM API key found` | .env 里 DOUBAO/DEEPSEEK/OPENAI 都没填 | 配一个 |
| `backend/storage/tests/test_postgres_e2e.py` skipped | env 没设 `POSTGRES_DSN` | export 完跑 docker compose up |
| Collector 5 维度调用慢 | 模板默认 timeout 180s 是 4 维场景的；豆包联网搜索 5 维一次约 80-120s | 已调到 180s，仍超时则拉到 300s |
| Extractor failed | consolidation pass + 12 capability 抽取需要 5-10 min，模板已配 600s | 仍超时 → 拉到 900s 或拆 source 数量 |
| QA verdict 持续 reject + reporter_v2/v3/v4 | 反馈环正常工作但 LLM 内容质量未收敛 | 见 [E2E_INTEGRATION_LOG.md](E2E_INTEGRATION_LOG.md) "R 窗口 patch" |
| Jaeger UI 看不到 trace | env 没设 `OTEL_EXPORTER_OTLP_ENDPOINT` 或 `OTEL_SDK_DISABLED=true` | 启动日志看 `tracer:` 行确认 |

---

## 8. 一键起 demo（脚本备忘）

```bash
#!/usr/bin/env bash
set -e

docker compose up -d postgres redis jaeger
sleep 5

source .venv/bin/activate
export STORAGE_MODE=postgres
export POSTGRES_DSN=postgresql+asyncpg://app:app@localhost:5432/app
export REDIS_URL=redis://localhost:6379/0
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

uvicorn backend.api.app:app --port 8000 &
API_PID=$!

cd frontend
pnpm dev &
FE_PID=$!

trap "kill $API_PID $FE_PID; docker compose down" EXIT
wait
```
