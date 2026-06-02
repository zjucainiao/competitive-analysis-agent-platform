# `backend/api` · Orchestrator HTTP / WebSocket 入口

FastAPI 应用工厂 + 路由 + 依赖注入。生产实例：`backend.api.app:app`。

## 启动

```bash
# dev：内存 storage + mock agents（不需要 docker）
uvicorn backend.api.app:app --reload

# 生产：PG + Redis + hybrid 模式
export STORAGE_MODE=postgres
export AGENT_MODE=hybrid
export POSTGRES_DSN=postgresql+asyncpg://app:app@localhost:5432/app
export REDIS_URL=redis://localhost:6379/0
uvicorn backend.api.app:app --workers 1
```

或 Python 内：

```python
from backend.api import create_app
app = create_app(mode="memory", agent_mode="mock")
```

## 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 + schema / mode 元信息 |
| POST | `/api/projects` | 创建项目（系统补 project_id / created_at） |
| GET | `/api/projects` | 列出项目（支持 `?owner=` / `?project_status=`） |
| GET | `/api/projects/{id}` | 获取单个项目 |
| POST | `/api/projects/{id}/run` | 启动 DAG 运行（后台异步） |
| GET | `/api/projects/{id}/state` | 拉取 plan + outputs + verdicts 聚合状态 |
| WS | `/api/projects/{id}/events` | 订阅 `NodeExecutionResult` 推送 |

## 后台任务

`POST /api/projects/{id}/run` 创建 `asyncio.create_task` 跑 `Orchestrator.run`：
- 项目状态置 `RUNNING`
- 跑完 → `DONE`；异常 → `FAILED`
- 同一项目并发拒绝（409）

任务句柄保存在 `app.state.running_tasks`，lifespan 结束时全部 cancel。

## WebSocket 协议

订阅一个 channel：`project:{id}:nodes`。每条消息是 `NodeExecutionResult` 的 JSON（`model_dump(mode="json")` 输出，含 `node_id` / `status` / `output` / `duration_ms` / ...）。客户端主动断开即停止推送。

## 依赖注入

`backend.api.deps` 提供三个 `Depends`：

```python
storage: Storage          = Depends(get_storage)
orch:    Orchestrator     = Depends(get_orchestrator)
registry: AgentRegistry   = Depends(get_agent_registry)
```

它们都从 `app.state` 取实例，由 `lifespan` 在启动时装配一次。

## 测试

```bash
pytest backend/api/tests/
```

8 个测试覆盖 health / CRUD / run + 反馈闭环 / 409 / 404 / WebSocket 推送。
