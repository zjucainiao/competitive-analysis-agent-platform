"""Storage 四件 Protocol —— I 窗口对 O 窗口的硬契约。

设计原则：
- 所有方法 async（v1 单进程也用 async，避免 v2 切换 worker 模式时改签名）
- Protocol 标 runtime_checkable，方便 isinstance 检查（pytest fixture 用）
- 不在 Protocol 层 import langgraph / redis / asyncpg，确保 storage 抽象干净

详细契约见 docs/STORAGE.md。
"""

from __future__ import annotations

from typing import (
    Any,
    AsyncIterator,
    Protocol,
    Sequence,
    runtime_checkable,
)

from backend.schemas import (
    AgentOutputBase,
    DAGPlan,
    NodeExecutionResult,
    NodeStatus,
    Project,
    ProjectStatus,
    QAVerdict,
    RunSnapshot,
    User,
)

from .checkpoint_types import (
    ChannelVersions,
    Checkpoint,
    CheckpointConfig,
    CheckpointMetadata,
    CheckpointTuple,
)


# ---------- Checkpointer ----------


@runtime_checkable
class CheckpointerProtocol(Protocol):
    """与 LangGraph `BaseCheckpointSaver` 结构等价（async 子集）。

    通过 `backend.storage.langgraph_adapter.to_langgraph_saver()` 包装后
    可直接传给 `StateGraph.compile(checkpointer=...)`。
    """

    async def aget_tuple(self, config: CheckpointConfig) -> CheckpointTuple | None:
        """按 config 取 checkpoint（无 checkpoint_id 时取该 thread 最新一条）。"""
        ...

    async def aput(
        self,
        config: CheckpointConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> CheckpointConfig:
        """落一个 checkpoint，返回带 checkpoint_id 的 config。"""
        ...

    def alist(
        self,
        config: CheckpointConfig | None,
        *,
        before: CheckpointConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """按时间倒序遍历 checkpoint。`config=None` 表示全表（罕用）。"""
        ...

    async def aput_writes(
        self,
        config: CheckpointConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
    ) -> None:
        """单 task 的 pending writes。`writes` 是 (channel, value) 列表。"""
        ...

    async def close(self) -> None:
        """释放连接池等资源。InMemory 实现可空实现。"""
        ...


# ---------- StateStore ----------


@runtime_checkable
class StateStoreProtocol(Protocol):
    """User / Project / DAGPlan / NodeOutput / QAVerdict 的 CRUD。"""

    # ----- User -----

    async def create_user(self, user: User) -> None:
        """插入新用户。email 已唯一冲突时抛 ValueError（上层转 409）。"""
        ...

    async def get_user_by_email(self, email: str) -> User | None:
        """按规范化（lower+trim）email 查；登录用。"""
        ...

    async def get_user_by_id(self, user_id: str) -> User | None:
        """按 user_id 查；get_current_user 解析 JWT 后用。"""
        ...

    # ----- Project -----

    async def save_project(self, project: Project) -> None:
        """upsert：按 project_id 主键。"""
        ...

    async def get_project(self, project_id: str) -> Project | None: ...

    async def list_projects(
        self,
        *,
        owner: str | None = None,
        status: ProjectStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]: ...

    async def update_project_status(
        self, project_id: str, status: ProjectStatus
    ) -> None: ...

    # ----- DAGPlan -----

    async def save_dag_plan(self, plan: DAGPlan) -> None:
        """upsert：按 plan_id 主键。"""
        ...

    async def get_dag_plan(self, project_id: str) -> DAGPlan | None:
        """取该 project 最新一份 plan。"""
        ...

    async def update_node_status(
        self, project_id: str, node_id: str, status: NodeStatus
    ) -> None:
        """直接更新 DAGPlan.nodes[node_id].status，无需重写整份 plan。"""
        ...

    # ----- NodeOutput（AgentOutputBase 多态） -----

    async def save_node_output(
        self, project_id: str, node_id: str, output: AgentOutputBase
    ) -> None:
        """upsert：(project_id, node_id) 复合主键。"""
        ...

    async def get_node_output(
        self, project_id: str, node_id: str
    ) -> AgentOutputBase | None: ...

    async def list_node_outputs(
        self, project_id: str
    ) -> dict[str, AgentOutputBase]:
        """key=node_id。"""
        ...

    # ----- QAVerdict -----

    async def save_qa_verdict(
        self, project_id: str, verdict: QAVerdict
    ) -> None: ...

    async def list_qa_verdicts(self, project_id: str) -> list[QAVerdict]:
        """按创建时间倒序。"""
        ...

    # ----- LLMCallRecord（每节点完成后持久化其 LLM 调用流水，重启可查） -----

    async def append_llm_calls(
        self, project_id: str, calls: list[dict]
    ) -> None:
        """追加一批 LLM 调用记录（dict 形态，见 observability.LLMCallRecord）。"""
        ...

    async def list_llm_calls(
        self,
        project_id: str,
        *,
        node_id: str | None = None,
        agent_name: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """按 timestamp 倒序返回；node_id / agent_name 精确过滤。"""
        ...

    # ----- RunSnapshot（每次 run 终态时持久化整份 state） -----

    async def save_run_snapshot(self, snapshot: RunSnapshot) -> None:
        """upsert：(project_id, run_id) 复合主键。"""
        ...

    async def get_run_snapshot(
        self, project_id: str, run_id: str
    ) -> RunSnapshot | None: ...

    async def list_run_snapshots(self, project_id: str) -> list[RunSnapshot]:
        """按 captured_at 倒序。"""
        ...

    async def close(self) -> None: ...


# ---------- EventBus ----------


@runtime_checkable
class EventBusProtocol(Protocol):
    """`NodeExecutionResult` 跨进程广播。

    channel 命名约定见 docs/STORAGE.md § 4.3：
        project:{project_id}:nodes    # 节点执行结果
        project:{project_id}:status   # ProjectStatus 变更
        project:{project_id}:qa       # QAVerdict 落库通知

    语义：pub/sub，订阅之后才能收到的消息，无 replay；
    v2 升级 Redis Stream 时不破契约（增量加 replay 方法）。
    """

    async def publish(self, channel: str, payload: NodeExecutionResult) -> None: ...

    def subscribe(self, channel: str) -> AsyncIterator[NodeExecutionResult]:
        """返回 AsyncIterator；调用方 `async for` 退出时自动释放。"""
        ...

    async def close(self) -> None: ...


__all__ = [
    "CheckpointerProtocol",
    "EventBusProtocol",
    "StateStoreProtocol",
]
