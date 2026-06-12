# 存储层（backend/storage）

> 本文档是 **I 窗口 → O 窗口** 的硬契约。
> 任何对 `backend/storage/*` Protocol 形状或语义的破坏性变更必须走 PR + 通知 O 窗口。
> 配套 schema：`backend/schemas/orchestrator.py`（`NodeExecutionRequest` / `NodeExecutionResult`）。

---

## 1. 范围与边界

`backend/storage/*` 给 Orchestrator 和 API 层提供四个底座：

| 协议 | 用途 | 实现 |
|---|---|---|
| `CheckpointerProtocol` | LangGraph StateGraph 的 checkpoint 持久化 | InMemory（单测）+ Postgres（生产/演示） |
| `StateStoreProtocol` | Project / DAGPlan / NodeOutput / QAVerdict 的 CRUD | InMemory（单测）+ Postgres（生产/演示） |
| `EventBusProtocol` | `NodeExecutionResult` 跨进程广播（前端 WS 推送、worker 协调） | InMemory（单测）+ Redis pub/sub（生产） |
| dev infra | 本地一键起 PG 16 + Redis 7，提供 pytest fixture | `docker-compose.yml` + `tests/conftest.py` |

边界（**storage 层不做的事**）：
- 不直接持久化 OTLP trace —— 那走 `backend/observability/tracer.py` 的 OTLP exporter（Jaeger / 共用 PG）。
- 不做 LLM 响应级缓存 —— 当前未实现（计划项，无对应代码）。

> 注：本仓**没有**向量索引 / Chroma。`pyproject.toml` 声明了 `chromadb`，但全仓无 `import chroma*`（`grep -rn chromadb --include=*.py backend/` 无命中）。Evidence 不走向量库。

存储层只管 Orchestrator 推进 DAG（及登录用户 / run 快照 / LLM 调用流水）所需的数据底座。

---

## 2. CheckpointerProtocol

### 2.1 设计目标

- 与 LangGraph `BaseCheckpointSaver` **结构等价**，可被 `langgraph.StateGraph(checkpointer=...)` 直接吃下
- 不在 protocol 层 import `langgraph`，避免存储层硬依赖编排器选型
- 一对一的 InMemory + Postgres 实现都通过同一份单元测试套

### 2.2 接口

```python
class CheckpointerProtocol(Protocol):
    async def aget_tuple(self, config: CheckpointConfig) -> CheckpointTuple | None: ...
    async def aput(
        self,
        config: CheckpointConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> CheckpointConfig: ...
    def alist(  # 注意：sync def 返回 AsyncIterator（与 langgraph 一致）
        self,
        config: CheckpointConfig | None,
        *,
        before: CheckpointConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]: ...
    async def aput_writes(
        self,
        config: CheckpointConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
    ) -> None: ...
    async def close(self) -> None: ...
```

> 实现见 `backend/storage/protocols.py:46`（`CheckpointerProtocol`）。`alist` 是普通
> `def` 但返回 `AsyncIterator`（protocols.py:67），与 langgraph `BaseCheckpointSaver.alist` 形状一致。

`CheckpointConfig` 是一个最小 dict 类型 `{"configurable": {"thread_id": str, "checkpoint_ns": str, "checkpoint_id": str | None}}`，
与 langchain_core 的 `RunnableConfig` 的子集等价。

`Checkpoint` / `CheckpointMetadata` / `CheckpointTuple` / `ChannelVersions`
在 `backend/storage/checkpoint_types.py` 定义为本地 Pydantic / TypedDict，
其字段集合与 langgraph 0.2 同名类一致。

### 2.3 LangGraph 适配

`backend/storage/langgraph_adapter.py` 提供 `to_langgraph_saver(impl)`，
把任意满足 `CheckpointerProtocol` 的实现包装成 `langgraph.checkpoint.base.BaseCheckpointSaver` 的子类。
此模块的 `import langgraph` 用 try/except 隔离，未安装时整个 storage 层仍可用。

Orchestrator 真接 LangGraph 时：

```python
from backend.storage import build_storage
from backend.storage.langgraph_adapter import to_langgraph_saver

storage = build_storage(mode="postgres")  # or "memory"
saver = to_langgraph_saver(storage.checkpointer)
graph = builder.compile(checkpointer=saver)
```

### 2.4 PostgreSQL 表结构

参考 langgraph 官方 PostgresSaver，简化为两张表（不做多 namespace）。
DDL 源在 `backend/storage/sql.py:12`（`CREATE_CHECKPOINTS` / `CREATE_CHECKPOINT_WRITES`），
全部用 `CREATE TABLE IF NOT EXISTS`（见下文 § 3.4 关于 alembic）：

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id            text NOT NULL,
    checkpoint_ns        text NOT NULL DEFAULT '',
    checkpoint_id        text NOT NULL,
    parent_checkpoint_id text,
    checkpoint           bytea NOT NULL,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),   -- doc 此前漏列
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- 注意排序键是 created_at DESC（不是 checkpoint_id DESC）
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_created
    ON checkpoints (thread_id, checkpoint_ns, created_at DESC);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id     text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id       text NOT NULL,
    idx           integer NOT NULL,
    channel       text NOT NULL,
    value         bytea NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
```

`checkpoint` / `value` 字段序列化为 `pickle` bytes 落 `bytea`（见 `backend/storage/postgres.py`）。

---

## 3. StateStoreProtocol

### 3.1 设计目标

- Orchestrator 需要随时知道：当前 Project 状态、最新 DAGPlan、每个节点的输出、每轮 QAVerdict
- 前端通过 API 拉这些做 DAG 可视化 + 报告查看
- v1 阶段单进程内 Orchestrator + API 共享同一个 store 实例
- 同时承载登录用户（User CRUD）、每次 run 的终态快照（RunSnapshot）、LLM 调用流水（llm_calls），
  这些都挂在同一个 store 协议下（见 `backend/storage/protocols.py:95`）。
- 多次运行同项目时按 `run_id` 隔离产出（P2-RUNSCOPE）：`list_*` 省略 `run_id` 时只返回**最新一次 run**。

### 3.2 接口

实现见 `backend/storage/protocols.py:95`。完整签名：

```python
class StateStoreProtocol(Protocol):
    # User（登录态；email 经 lower+trim 规范化，库级唯一）
    async def create_user(self, user: User) -> None: ...           # email 冲突抛 ValueError
    async def get_user_by_email(self, email: str) -> User | None: ...
    async def get_user_by_id(self, user_id: str) -> User | None: ...

    # Project
    async def save_project(self, project: Project) -> None: ...
    async def get_project(self, project_id: str) -> Project | None: ...
    async def list_projects(
        self, *, owner: str | None = None, status: ProjectStatus | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[Project]: ...
    async def update_project_status(
        self, project_id: str, status: ProjectStatus,
    ) -> None: ...

    # DAGPlan
    async def save_dag_plan(self, plan: DAGPlan) -> None: ...
    async def get_dag_plan(self, project_id: str) -> DAGPlan | None: ...
    async def update_node_status(
        self, project_id: str, node_id: str, status: NodeStatus,
    ) -> None: ...

    # NodeOutput（run_id 省略 → 作用域为「该项目最新一次 run」, P2-RUNSCOPE）
    async def save_node_output(
        self, project_id: str, node_id: str, output: AgentOutputBase,
        *, run_id: str | None = None,
    ) -> None: ...
    async def get_node_output(
        self, project_id: str, node_id: str, *, run_id: str | None = None,
    ) -> AgentOutputBase | None: ...
    async def list_node_outputs(
        self, project_id: str, *, run_id: str | None = None,
    ) -> dict[str, AgentOutputBase]: ...

    # QAVerdict（同样带 run_id kwarg）
    async def save_qa_verdict(
        self, project_id: str, verdict: QAVerdict, *, run_id: str | None = None,
    ) -> None: ...
    async def list_qa_verdicts(
        self, project_id: str, *, run_id: str | None = None,
    ) -> list[QAVerdict]: ...

    # LLM 调用流水（每节点完成后持久化，重启可查）
    async def append_llm_calls(self, project_id: str, calls: list[dict]) -> None: ...
    async def list_llm_calls(
        self, project_id: str, *, node_id: str | None = None,
        agent_name: str | None = None, limit: int = 200,
    ) -> list[dict]: ...

    # RunSnapshot（每次 run 终态时持久化整份 state）
    async def save_run_snapshot(self, snapshot: RunSnapshot) -> None: ...
    async def get_run_snapshot(
        self, project_id: str, run_id: str,
    ) -> RunSnapshot | None: ...
    async def list_run_snapshots(self, project_id: str) -> list[RunSnapshot]: ...

    async def close(self) -> None: ...
```

### 3.3 PostgreSQL 表结构

DDL 全量见 `backend/storage/sql.py`（行号下标）。全部 `CREATE TABLE IF NOT EXISTS`：

```sql
-- sql.py:43 —— 登录用户（doc 此前漏列整张表）
CREATE TABLE IF NOT EXISTS users (
    user_id       text PRIMARY KEY,
    email         text NOT NULL,
    password_hash text NOT NULL,
    display_name  text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now()
);
-- email 大小写不敏感唯一（sql.py:54）
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users (lower(email));

-- sql.py:59
CREATE TABLE IF NOT EXISTS projects (
    project_id text PRIMARY KEY,
    owner      text NOT NULL,
    status     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    payload    jsonb NOT NULL              -- 完整 Project Pydantic dump
);
-- 排序键是 updated_at DESC（不是 created_at）
CREATE INDEX IF NOT EXISTS idx_projects_owner_status
    ON projects (owner, status, updated_at DESC);

-- sql.py:75
CREATE TABLE IF NOT EXISTS dag_plans (
    plan_id    text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    payload    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dag_plans_project ON dag_plans (project_id, created_at DESC);

-- sql.py:89 —— 注意有 run_id 列（doc 此前漏列）
CREATE TABLE IF NOT EXISTS node_outputs (
    project_id text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    node_id    text NOT NULL,
    run_id     text,                       -- P2-RUNSCOPE：产出归属哪次 run
    agent_name text NOT NULL,
    status     text NOT NULL,
    saved_at   timestamptz NOT NULL DEFAULT now(),
    payload    jsonb NOT NULL,             -- AgentOutputBase 多态 dump
    PRIMARY KEY (project_id, node_id)
);

-- sql.py:102 —— 同样有 run_id 列
CREATE TABLE IF NOT EXISTS qa_verdicts (
    verdict_id     text PRIMARY KEY,
    project_id     text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    run_id         text,
    overall_status text NOT NULL,
    blocking       boolean NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    payload        jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_verdicts_project ON qa_verdicts (project_id, created_at DESC);

-- sql.py:129 —— 每次 run 终态整份 state 快照（doc 此前漏列）
CREATE TABLE IF NOT EXISTS run_snapshots (
    project_id   text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    run_id       text NOT NULL,
    captured_at  timestamptz NOT NULL,
    final_status text NOT NULL,
    payload      jsonb NOT NULL,
    PRIMARY KEY (project_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_run_snapshots_project_time
    ON run_snapshots (project_id, captured_at DESC);

-- sql.py:145 —— LLM 调用流水（doc 此前漏列）
CREATE TABLE IF NOT EXISTS llm_calls (
    seq        bigserial PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    node_id    text,
    agent_name text,
    ts         double precision NOT NULL DEFAULT 0,
    payload    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_project_ts ON llm_calls (project_id, ts DESC, seq DESC);
```

> `node_outputs.run_id` / `qa_verdicts.run_id` 对老库另有幂等 `ALTER TABLE ... ADD COLUMN
> IF NOT EXISTS`（sql.py:117 / sql.py:120），老行 `run_id=NULL` 被视作一个历史 run。
>
> `AgentOutputBase` 多态由 payload 内 `agent_name` 字段判别；重建时由
> `backend/storage/serde.py:40` 的 `load_output()` 按注册表路由到具体子类（未知 `agent_name` 抛 ValueError）。

### 3.4 schema 初始化（无 alembic）

v1 **不上 alembic**：`backend/storage/sql.py:183` 的 `init_schema(engine)` 跑一遍
`ALL_STATEMENTS`（全是 `CREATE TABLE / INDEX IF NOT EXISTS` + 幂等 `ALTER`）即可建库，
由 `backend/storage/__init__.py:131` 的 `init_storage(storage)` 在 postgres 模式下触发。
`pyproject.toml` 虽声明了 `alembic>=1.13`，但仓内**无 migrations 目录**，`sql.py:3` 明确写
「v1 阶段不上 alembic」。后续真上多人迁移再切。

---

## 4. EventBusProtocol

### 4.1 设计目标

- Orchestrator 把每个节点的 `NodeExecutionResult` 发到 channel，订阅方（API 的 WS handler）实时拉
- 同进程单测时不依赖 Redis
- v1 接 Redis pub/sub；v2 升级到 Redis Stream（带 consumer group + replay）时不破契约

### 4.2 接口

```python
class EventBusProtocol(Protocol):
    async def publish(self, channel: str, payload: NodeExecutionResult) -> None: ...
    def subscribe(self, channel: str) -> AsyncIterator[NodeExecutionResult]: ...
    async def close(self) -> None: ...
```

订阅语义：
- 调用 `subscribe(channel)` 立即返回一个 `AsyncIterator`，**只看到订阅之后** publish 的消息（pub/sub 语义，不 replay 历史）
- 取消订阅：调用方 `async for` 退出时（含 break / 抛异常）自动释放 channel 资源
- v1 不保证消息持久化或 at-least-once；v2 用 Redis Stream 提升

### 4.3 channel 命名约定

```
project:{project_id}:nodes        # 所有节点执行结果（前端 WS 用）
project:{project_id}:status       # ProjectStatus 变更
project:{project_id}:qa           # QAVerdict 落库通知
```

---

## 5. 工厂 + 配置

`backend/storage/__init__.py` 暴露：

```python
def build_storage(
    mode: Literal["memory", "postgres"] = "memory",
    *,
    pg_dsn: str | None = None,
    redis_url: str | None = None,
) -> Storage: ...
```

返回的 `Storage` 是个轻量 dataclass（`backend/storage/__init__.py:45`）：

```python
@dataclass
class Storage:
    checkpointer: CheckpointerProtocol
    state_store:  StateStoreProtocol
    event_bus:    EventBusProtocol
    mode: Literal["memory", "postgres"] = "memory"   # doc 此前漏列
    async def close(self) -> None: ...               # bus → store → checkpointer 顺序关
```

- `mode="memory"`：三套 InMemory 实现，单进程内可用，pytest 默认走这条
- `mode="postgres"`：Checkpointer + StateStore 走 PG（SQLAlchemy 2.0 async + asyncpg），
  EventBus 走 Redis pub/sub；`pg_dsn` / `redis_url` 缺省读 `POSTGRES_DSN` / `REDIS_URL`，
  任一缺失抛 `ValueError`。postgres 模式实际返回 `_PostgresStorage`（带 engine 句柄，close 时
  `engine.dispose()`）。
- DSN 形如 `postgresql+asyncpg://app:app@localhost:5432/app`。
- 建表用 `init_storage(storage)`（postgres 模式有效，memory 模式 no-op）。

---

## 6. 开发环境

`docker-compose.yml` 起 PG 16 + Redis 7：

```bash
docker compose up -d postgres redis
export POSTGRES_DSN=postgresql+asyncpg://app:app@localhost:5432/app
export REDIS_URL=redis://localhost:6379/0
pytest backend/storage/tests -m e2e
```

测试策略：
- 默认 InMemory，所有 unit test 必跑
- e2e 标签的 PG/Redis 测试在 docker 起来时跑，否则 skip
- 不强求 testcontainers（启动重）；直接连本地 compose 起的服务

---

## 7. 给 O 窗口的接入示例

```python
from backend.storage import build_storage
from backend.storage.langgraph_adapter import to_langgraph_saver

storage = build_storage(mode="memory")           # 或 "postgres"
graph = (
    StateGraph(DAGState)
    .add_node("collector", collector_node)
    # ... 其他节点 ...
    .compile(checkpointer=to_langgraph_saver(storage.checkpointer))
)

async def run_project(project: Project) -> None:
    await storage.state_store.save_project(project)
    plan = await orchestrator.plan(project)
    await storage.state_store.save_dag_plan(plan)
    async for result in orchestrator.run(plan, project):
        await storage.state_store.save_node_output(
            project.project_id, result.node_id, result.output
        )
        await storage.event_bus.publish(
            f"project:{project.project_id}:nodes", result,
        )
```

---

## 8. 版本

- v1.0.0（2026-05-29 由 I 窗口落地）
- 兼容 schemas v1.1.0+（依赖 `NodeExecutionRequest` / `NodeExecutionResult`）
