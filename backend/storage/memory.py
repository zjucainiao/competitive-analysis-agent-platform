"""三套 InMemory 实现 —— 单元测试 + 无 docker 环境的默认底座。

不依赖 asyncpg / redis / langgraph，可在 pytest 直接跑。
线程安全靠 asyncio.Lock；存储语义与 PG 实现严格对齐，单测套可双跑。
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, AsyncIterator, Sequence

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
    checkpoint_id_of,
    checkpoint_ns_of,
    make_config,
    now_ts,
    thread_id_of,
)


# ---------- Checkpointer ----------


class InMemoryCheckpointer:
    """实现 `CheckpointerProtocol`。"""

    def __init__(self) -> None:
        # key: (thread_id, checkpoint_ns, checkpoint_id)
        # value: (Checkpoint, CheckpointMetadata, parent_checkpoint_id | None, seq)
        self._checkpoints: dict[
            tuple[str, str, str],
            tuple[Checkpoint, CheckpointMetadata, str | None, int],
        ] = {}
        # key: (thread_id, checkpoint_ns, checkpoint_id, task_id)
        # value: list of (idx, channel, value)
        self._writes: dict[
            tuple[str, str, str, str], list[tuple[int, str, Any]]
        ] = defaultdict(list)
        self._seq = 0
        self._lock = asyncio.Lock()

    async def aget_tuple(self, config: CheckpointConfig) -> CheckpointTuple | None:
        async with self._lock:
            tid = thread_id_of(config)
            ns = checkpoint_ns_of(config)
            cid = checkpoint_id_of(config)
            if cid is None:
                # 取该 (tid, ns) 下 seq 最大的
                candidates = [
                    (key, val)
                    for key, val in self._checkpoints.items()
                    if key[0] == tid and key[1] == ns
                ]
                if not candidates:
                    return None
                key, (checkpoint, metadata, parent_cid, _seq) = max(
                    candidates, key=lambda kv: kv[1][3]
                )
                cid = key[2]
            else:
                key = (tid, ns, cid)
                entry = self._checkpoints.get(key)
                if entry is None:
                    return None
                checkpoint, metadata, parent_cid, _seq = entry

            pending = [
                (task_id, ch, val)
                for (t, n, c, task_id), writes in self._writes.items()
                if t == tid and n == ns and c == cid
                for (_idx, ch, val) in sorted(writes)
            ]
            cfg = make_config(tid, checkpoint_ns=ns, checkpoint_id=cid)
            parent_cfg = (
                make_config(tid, checkpoint_ns=ns, checkpoint_id=parent_cid)
                if parent_cid
                else None
            )
            return CheckpointTuple(
                config=cfg,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_cfg,
                pending_writes=pending,
            )

    async def aput(
        self,
        config: CheckpointConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> CheckpointConfig:
        async with self._lock:
            tid = thread_id_of(config)
            ns = checkpoint_ns_of(config)
            # checkpoint 自身可能带 id；否则生成
            cid = checkpoint.get("id") or f"chk-{uuid.uuid4().hex[:12]}"
            checkpoint = dict(checkpoint)
            checkpoint["id"] = cid
            checkpoint.setdefault("ts", now_ts())
            parent_cid = checkpoint_id_of(config)
            self._seq += 1
            self._checkpoints[(tid, ns, cid)] = (
                checkpoint,  # type: ignore[arg-type]
                dict(metadata),  # type: ignore[arg-type]
                parent_cid,
                self._seq,
            )
            return make_config(tid, checkpoint_ns=ns, checkpoint_id=cid)

    async def alist(
        self,
        config: CheckpointConfig | None,
        *,
        before: CheckpointConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        # 注意：alist 是 async generator，不在外层加锁（迭代过程中其他写入应可见即可）。
        # 拿快照后再 yield，避免迭代时字典变更。
        async with self._lock:
            if config is None:
                snapshot = list(self._checkpoints.items())
            else:
                tid = thread_id_of(config)
                ns = checkpoint_ns_of(config)
                snapshot = [
                    (k, v)
                    for k, v in self._checkpoints.items()
                    if k[0] == tid and k[1] == ns
                ]
            before_seq: int | None = None
            if before is not None:
                bcid = checkpoint_id_of(before)
                if bcid is not None:
                    for k, v in snapshot:
                        if k[2] == bcid:
                            before_seq = v[3]
                            break

        snapshot.sort(key=lambda kv: kv[1][3], reverse=True)
        count = 0
        for (tid, ns, cid), (checkpoint, metadata, parent_cid, seq) in snapshot:
            if before_seq is not None and seq >= before_seq:
                continue
            cfg = make_config(tid, checkpoint_ns=ns, checkpoint_id=cid)
            parent_cfg = (
                make_config(tid, checkpoint_ns=ns, checkpoint_id=parent_cid)
                if parent_cid
                else None
            )
            yield CheckpointTuple(
                config=cfg,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_cfg,
                pending_writes=[],
            )
            count += 1
            if limit is not None and count >= limit:
                return

    async def aput_writes(
        self,
        config: CheckpointConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
    ) -> None:
        async with self._lock:
            tid = thread_id_of(config)
            ns = checkpoint_ns_of(config)
            cid = checkpoint_id_of(config)
            if cid is None:
                raise ValueError("aput_writes requires checkpoint_id in config")
            key = (tid, ns, cid, task_id)
            existing = self._writes[key]
            base_idx = len(existing)
            for i, (channel, value) in enumerate(writes):
                existing.append((base_idx + i, channel, value))

    async def close(self) -> None:
        return None


# ---------- StateStore ----------


class InMemoryStateStore:
    """实现 `StateStoreProtocol`。"""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._user_id_by_email: dict[str, str] = {}  # lower(email) -> user_id
        self._projects: dict[str, Project] = {}
        self._project_updated_at: dict[str, datetime] = {}
        self._plans_by_project: dict[str, list[DAGPlan]] = defaultdict(list)
        self._node_outputs: dict[tuple[str, str], AgentOutputBase] = {}
        self._qa_verdicts: dict[str, list[tuple[datetime, QAVerdict]]] = defaultdict(list)
        # project_id -> LLM 调用记录（dict）按追加顺序
        self._llm_calls: dict[str, list[dict]] = defaultdict(list)
        # (project_id, run_id) -> RunSnapshot
        self._run_snapshots: dict[tuple[str, str], RunSnapshot] = {}
        self._lock = asyncio.Lock()

    # ---- User ----

    async def create_user(self, user: User) -> None:
        key = user.email.strip().lower()
        async with self._lock:
            if key in self._user_id_by_email:
                raise ValueError(f"email already registered: {key}")
            self._users[user.user_id] = user
            self._user_id_by_email[key] = user.user_id

    async def get_user_by_email(self, email: str) -> User | None:
        key = email.strip().lower()
        async with self._lock:
            uid = self._user_id_by_email.get(key)
            return self._users.get(uid) if uid else None

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with self._lock:
            return self._users.get(user_id)

    # ---- Project ----

    async def save_project(self, project: Project) -> None:
        async with self._lock:
            self._projects[project.project_id] = project
            self._project_updated_at[project.project_id] = datetime.utcnow()

    async def get_project(self, project_id: str) -> Project | None:
        async with self._lock:
            return self._projects.get(project_id)

    async def list_projects(
        self,
        *,
        owner: str | None = None,
        status: ProjectStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        async with self._lock:
            items = [
                (self._project_updated_at[p.project_id], p)
                for p in self._projects.values()
                if (owner is None or p.owner == owner)
                and (status is None or p.status == status)
            ]
        items.sort(key=lambda kv: kv[0], reverse=True)
        return [p for _ts, p in items[offset : offset + limit]]

    async def update_project_status(
        self, project_id: str, status: ProjectStatus
    ) -> None:
        async with self._lock:
            p = self._projects.get(project_id)
            if p is None:
                raise KeyError(f"project not found: {project_id}")
            updated = p.model_copy(update={"status": status})
            self._projects[project_id] = updated
            self._project_updated_at[project_id] = datetime.utcnow()

    # ---- DAGPlan ----

    async def save_dag_plan(self, plan: DAGPlan) -> None:
        async with self._lock:
            # 同一 plan_id 直接替换；不同 plan_id（自适应重规划）追加
            bucket = self._plans_by_project[plan.project_id]
            for i, existing in enumerate(bucket):
                if existing.plan_id == plan.plan_id:
                    bucket[i] = plan
                    return
            bucket.append(plan)

    async def get_dag_plan(self, project_id: str) -> DAGPlan | None:
        async with self._lock:
            bucket = self._plans_by_project.get(project_id, [])
            return bucket[-1] if bucket else None

    async def update_node_status(
        self, project_id: str, node_id: str, status: NodeStatus
    ) -> None:
        async with self._lock:
            bucket = self._plans_by_project.get(project_id, [])
            if not bucket:
                raise KeyError(f"no DAGPlan for project: {project_id}")
            plan = bucket[-1]
            for i, node in enumerate(plan.nodes):
                if node.node_id == node_id:
                    plan.nodes[i] = node.model_copy(update={"status": status})
                    return
            raise KeyError(f"node not found: {node_id} in plan {plan.plan_id}")

    # ---- NodeOutput ----

    async def save_node_output(
        self, project_id: str, node_id: str, output: AgentOutputBase
    ) -> None:
        async with self._lock:
            self._node_outputs[(project_id, node_id)] = output

    async def get_node_output(
        self, project_id: str, node_id: str
    ) -> AgentOutputBase | None:
        async with self._lock:
            return self._node_outputs.get((project_id, node_id))

    async def list_node_outputs(
        self, project_id: str
    ) -> dict[str, AgentOutputBase]:
        async with self._lock:
            return {
                node_id: out
                for (pid, node_id), out in self._node_outputs.items()
                if pid == project_id
            }

    # ---- QAVerdict ----

    async def save_qa_verdict(
        self, project_id: str, verdict: QAVerdict
    ) -> None:
        async with self._lock:
            self._qa_verdicts[project_id].append((datetime.utcnow(), verdict))

    async def list_qa_verdicts(self, project_id: str) -> list[QAVerdict]:
        async with self._lock:
            items = list(self._qa_verdicts.get(project_id, []))
        items.sort(key=lambda kv: kv[0], reverse=True)
        return [v for _ts, v in items]

    # ---- LLMCallRecord ----

    async def append_llm_calls(
        self, project_id: str, calls: list[dict]
    ) -> None:
        if not calls:
            return
        async with self._lock:
            self._llm_calls[project_id].extend(dict(c) for c in calls)

    async def list_llm_calls(
        self,
        project_id: str,
        *,
        node_id: str | None = None,
        agent_name: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        async with self._lock:
            items = list(self._llm_calls.get(project_id, []))
        # 倒序（最新优先），与 ring buffer list_calls 一致
        items.sort(key=lambda c: c.get("timestamp", 0.0), reverse=True)
        out: list[dict] = []
        for c in items:
            if node_id is not None and c.get("node_id") != node_id:
                continue
            if agent_name is not None and c.get("agent_name") != agent_name:
                continue
            out.append(c)
            if len(out) >= limit:
                break
        return out

    # ---- RunSnapshot ----

    async def save_run_snapshot(self, snapshot: RunSnapshot) -> None:
        async with self._lock:
            self._run_snapshots[(snapshot.project_id, snapshot.run_id)] = snapshot

    async def get_run_snapshot(
        self, project_id: str, run_id: str
    ) -> RunSnapshot | None:
        async with self._lock:
            return self._run_snapshots.get((project_id, run_id))

    async def list_run_snapshots(self, project_id: str) -> list[RunSnapshot]:
        async with self._lock:
            items = [
                s
                for (pid, _rid), s in self._run_snapshots.items()
                if pid == project_id
            ]
        items.sort(key=lambda s: s.captured_at, reverse=True)
        return items

    async def close(self) -> None:
        return None


# ---------- EventBus ----------


class InMemoryEventBus:
    """实现 `EventBusProtocol`。

    pub/sub 语义：每个 subscriber 拿独立的 asyncio.Queue，
    publish 时往所有当前 subscriber 的队列各塞一份。订阅之前的消息丢失。
    """

    def __init__(self, *, queue_maxsize: int = 1024) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[NodeExecutionResult]]] = (
            defaultdict(list)
        )
        self._queue_maxsize = queue_maxsize
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(self, channel: str, payload: NodeExecutionResult) -> None:
        if self._closed:
            raise RuntimeError("event bus closed")
        async with self._lock:
            queues = list(self._subscribers.get(channel, []))
        for q in queues:
            # 不丢消息，但订阅方处理慢会拖累 publisher。v2 切 Redis 后这层 backpressure 改观。
            await q.put(payload)

    async def subscribe(self, channel: str) -> AsyncIterator[NodeExecutionResult]:
        if self._closed:
            raise RuntimeError("event bus closed")
        q: asyncio.Queue[NodeExecutionResult] = asyncio.Queue(
            maxsize=self._queue_maxsize
        )
        async with self._lock:
            self._subscribers[channel].append(q)
        try:
            while True:
                msg = await q.get()
                yield msg
        finally:
            async with self._lock:
                with contextlib.suppress(ValueError):
                    self._subscribers[channel].remove(q)

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            self._subscribers.clear()


__all__ = [
    "InMemoryCheckpointer",
    "InMemoryEventBus",
    "InMemoryStateStore",
]
