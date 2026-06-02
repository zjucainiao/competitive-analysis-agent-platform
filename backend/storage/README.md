# `backend/storage` · I 窗口产出

Storage 层给 Orchestrator 和 API 提供三件底座：

| Protocol | 用途 | 实现 |
|---|---|---|
| `CheckpointerProtocol` | LangGraph StateGraph checkpoint 持久化 | `InMemoryCheckpointer` / `PostgresCheckpointer` |
| `StateStoreProtocol` | Project / DAGPlan / NodeOutput / QAVerdict 的 CRUD | `InMemoryStateStore` / `PostgresStateStore` |
| `EventBusProtocol` | `NodeExecutionResult` 跨进程广播 | `InMemoryEventBus` / `RedisEventBus` |

完整契约见 [docs/STORAGE.md](../../docs/STORAGE.md)。

## 最小用法

```python
from backend.storage import build_storage

# 单测 / 无 docker：纯 InMemory
storage = build_storage(mode="memory")

await storage.state_store.save_project(project)
await storage.event_bus.publish("project:p1:nodes", node_result)

# 接 LangGraph
from backend.storage.langgraph_adapter import to_langgraph_saver
saver = to_langgraph_saver(storage.checkpointer)
graph = builder.compile(checkpointer=saver)
```

## 生产底座

```python
from backend.storage import build_storage, init_storage

storage = build_storage(
    mode="postgres",
    pg_dsn="postgresql+asyncpg://app:app@localhost:5432/app",
    redis_url="redis://localhost:6379/0",
)
await init_storage(storage)  # CREATE TABLE IF NOT EXISTS
```

## 开发环境

```bash
# 起 PG + Redis
docker compose up -d postgres redis

# 配环境变量
export POSTGRES_DSN=postgresql+asyncpg://app:app@localhost:5432/app
export REDIS_URL=redis://localhost:6379/0

# 跑测试
pytest backend/storage/tests/                      # 单测（必跑）
pytest backend/storage/tests/ -m postgres          # PG e2e（需 POSTGRES_DSN）
pytest backend/storage/tests/ -m redis             # Redis e2e（需 REDIS_URL）
```

未设环境变量时，e2e 测试由 [conftest.py](tests/conftest.py) 自动 skip。
单测一律走 InMemory，不需要 docker。

## 模块布局

```
backend/storage/
├── __init__.py             # build_storage / Storage facade
├── protocols.py            # 三件 Protocol（Checkpointer / StateStore / EventBus）
├── checkpoint_types.py     # LangGraph-compatible Checkpoint/CheckpointTuple/...
├── serde.py                # 多态 AgentOutput 序列化 + pickle helpers
├── sql.py                  # DDL：所有 CREATE TABLE 语句 + init_schema()
├── memory.py               # 三件 InMemory 实现
├── postgres.py             # PostgresStateStore + PostgresCheckpointer
├── redis_bus.py            # RedisEventBus
├── langgraph_adapter.py    # to_langgraph_saver()：CheckpointerProtocol → BaseCheckpointSaver
└── tests/
    ├── conftest.py
    ├── test_memory.py
    ├── test_serde.py
    ├── test_postgres_e2e.py
    └── test_redis_e2e.py
```

## 给 O 窗口的接入清单

1. 调 `build_storage(mode=...)` 拿到 `Storage`，三件实例都是注入式
2. `to_langgraph_saver(storage.checkpointer)` 喂给 `StateGraph.compile()`
3. 每个节点执行后 `await storage.state_store.save_node_output(project_id, node_id, output)`
4. 每个节点广播 `await storage.event_bus.publish(f"project:{pid}:nodes", node_result)`
5. API/WS handler 订阅同一 channel：`async for msg in storage.event_bus.subscribe(channel)`
6. 进程退出 `await storage.close()`

任何 Protocol 形状变更必须走 PR + 通知 O 窗口（docs/STORAGE.md § 8 是版本号承载点）。
