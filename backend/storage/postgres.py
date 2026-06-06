"""Postgres 实现：`PostgresStateStore` + `PostgresCheckpointer`。

驱动：SQLAlchemy 2.0 async + asyncpg。
DSN 示例：`postgresql+asyncpg://app:app@localhost:5432/app`。

设计要点：
- 多态 AgentOutputBase 序列化走 `backend.storage.serde.dump_output / load_output`
- checkpoint 用 pickle 落 bytea（与 langgraph 官方一致）
- 所有写入用 `INSERT ... ON CONFLICT DO UPDATE` 做幂等 upsert
- 连接池由调用方传入（build_storage 工厂统一管理）
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.schemas import (
    AgentOutputBase,
    DAGPlan,
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
from .serde import (
    dump_output,
    load_output,
    pickle_checkpoint,
    pickle_value,
    unpickle_checkpoint,
    unpickle_value,
)


# ---------- StateStore ----------


class PostgresStateStore:
    """实现 `StateStoreProtocol`。

    构造方需自行 `await init_schema(engine)`；本类不主动建表（避免被 import 时副作用）。
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ----- User -----

    async def create_user(self, user: User) -> None:
        sql = text(
            """
            INSERT INTO users (user_id, email, password_hash, display_name, created_at)
            VALUES (:user_id, :email, :password_hash, :display_name, :created_at)
            """
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    sql,
                    {
                        "user_id": user.user_id,
                        "email": user.email.strip().lower(),
                        "password_hash": user.password_hash,
                        "display_name": user.display_name,
                        "created_at": user.created_at,
                    },
                )
        except IntegrityError as e:  # email 唯一索引冲突
            raise ValueError(f"email already registered: {user.email}") from e

    async def get_user_by_email(self, email: str) -> User | None:
        sql = text(
            "SELECT user_id, email, password_hash, display_name, created_at "
            "FROM users WHERE lower(email) = :email"
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(sql, {"email": email.strip().lower()})).first()
        return _row_to_user(row) if row is not None else None

    async def get_user_by_id(self, user_id: str) -> User | None:
        sql = text(
            "SELECT user_id, email, password_hash, display_name, created_at "
            "FROM users WHERE user_id = :uid"
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(sql, {"uid": user_id})).first()
        return _row_to_user(row) if row is not None else None

    # ----- Project -----

    async def save_project(self, project: Project) -> None:
        payload = project.model_dump(mode="json")
        sql = text(
            """
            INSERT INTO projects (project_id, owner, status, created_at, updated_at, payload)
            VALUES (:project_id, :owner, :status, :created_at, now(), CAST(:payload AS jsonb))
            ON CONFLICT (project_id) DO UPDATE
              SET owner = EXCLUDED.owner,
                  status = EXCLUDED.status,
                  payload = EXCLUDED.payload,
                  updated_at = now()
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                sql,
                {
                    "project_id": project.project_id,
                    "owner": project.owner,
                    "status": project.status.value,
                    "created_at": project.created_at,
                    "payload": json.dumps(payload, default=str),
                },
            )

    async def get_project(self, project_id: str) -> Project | None:
        sql = text("SELECT payload FROM projects WHERE project_id = :pid")
        async with self._engine.connect() as conn:
            row = (await conn.execute(sql, {"pid": project_id})).first()
        if row is None:
            return None
        return Project.model_validate(_as_dict(row[0]))

    async def list_projects(
        self,
        *,
        owner: str | None = None,
        status: ProjectStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if owner is not None:
            clauses.append("owner = :owner")
            params["owner"] = owner
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status.value
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = text(
            f"""
            SELECT payload FROM projects
            {where}
            ORDER BY updated_at DESC
            LIMIT :limit OFFSET :offset
            """
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, params)).all()
        return [Project.model_validate(_as_dict(r[0])) for r in rows]

    async def update_project_status(
        self, project_id: str, status: ProjectStatus
    ) -> None:
        sql = text(
            """
            UPDATE projects
               SET status = :status,
                   updated_at = now(),
                   payload = jsonb_set(payload, '{status}', to_jsonb(CAST(:status AS text)))
             WHERE project_id = :pid
            """
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sql, {"pid": project_id, "status": status.value}
            )
            if result.rowcount == 0:
                raise KeyError(f"project not found: {project_id}")

    # ----- DAGPlan -----

    async def save_dag_plan(self, plan: DAGPlan) -> None:
        payload = plan.model_dump(mode="json")
        sql = text(
            """
            INSERT INTO dag_plans (plan_id, project_id, created_at, payload)
            VALUES (:plan_id, :project_id, now(), CAST(:payload AS jsonb))
            ON CONFLICT (plan_id) DO UPDATE
              SET payload = EXCLUDED.payload
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                sql,
                {
                    "plan_id": plan.plan_id,
                    "project_id": plan.project_id,
                    "payload": json.dumps(payload, default=str),
                },
            )

    async def get_dag_plan(self, project_id: str) -> DAGPlan | None:
        sql = text(
            """
            SELECT payload FROM dag_plans
             WHERE project_id = :pid
             ORDER BY created_at DESC
             LIMIT 1
            """
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(sql, {"pid": project_id})).first()
        if row is None:
            return None
        return DAGPlan.model_validate(_as_dict(row[0]))

    async def update_node_status(
        self, project_id: str, node_id: str, status: NodeStatus
    ) -> None:
        # 读 - 改 - 写：单 plan 拿出来改一个节点的 status 字段再 upsert
        plan = await self.get_dag_plan(project_id)
        if plan is None:
            raise KeyError(f"no DAGPlan for project: {project_id}")
        updated = False
        for i, node in enumerate(plan.nodes):
            if node.node_id == node_id:
                plan.nodes[i] = node.model_copy(update={"status": status})
                updated = True
                break
        if not updated:
            raise KeyError(f"node not found: {node_id} in plan {plan.plan_id}")
        await self.save_dag_plan(plan)

    # ----- NodeOutput -----

    async def save_node_output(
        self, project_id: str, node_id: str, output: AgentOutputBase
    ) -> None:
        payload = dump_output(output)
        sql = text(
            """
            INSERT INTO node_outputs (project_id, node_id, agent_name, status, payload, saved_at)
            VALUES (:project_id, :node_id, :agent_name, :status, CAST(:payload AS jsonb), now())
            ON CONFLICT (project_id, node_id) DO UPDATE
              SET agent_name = EXCLUDED.agent_name,
                  status = EXCLUDED.status,
                  payload = EXCLUDED.payload,
                  saved_at = now()
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                sql,
                {
                    "project_id": project_id,
                    "node_id": node_id,
                    "agent_name": output.agent_name,
                    "status": output.status.value,
                    "payload": json.dumps(payload, default=str),
                },
            )

    async def get_node_output(
        self, project_id: str, node_id: str
    ) -> AgentOutputBase | None:
        sql = text(
            "SELECT payload FROM node_outputs "
            "WHERE project_id = :pid AND node_id = :nid"
        )
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(sql, {"pid": project_id, "nid": node_id})
            ).first()
        if row is None:
            return None
        return load_output(_as_dict(row[0]))

    async def list_node_outputs(
        self, project_id: str
    ) -> dict[str, AgentOutputBase]:
        sql = text(
            "SELECT node_id, payload FROM node_outputs WHERE project_id = :pid"
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, {"pid": project_id})).all()
        return {r[0]: load_output(_as_dict(r[1])) for r in rows}

    # ----- QAVerdict -----

    async def save_qa_verdict(
        self, project_id: str, verdict: QAVerdict
    ) -> None:
        payload = verdict.model_dump(mode="json")
        sql = text(
            """
            INSERT INTO qa_verdicts
              (verdict_id, project_id, overall_status, blocking, payload, created_at)
            VALUES
              (:verdict_id, :project_id, :status, :blocking, CAST(:payload AS jsonb), now())
            ON CONFLICT (verdict_id) DO UPDATE
              SET overall_status = EXCLUDED.overall_status,
                  blocking = EXCLUDED.blocking,
                  payload = EXCLUDED.payload
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                sql,
                {
                    "verdict_id": verdict.verdict_id,
                    "project_id": project_id,
                    "status": verdict.overall_status.value,
                    "blocking": verdict.blocking,
                    "payload": json.dumps(payload, default=str),
                },
            )

    async def list_qa_verdicts(self, project_id: str) -> list[QAVerdict]:
        sql = text(
            """
            SELECT payload FROM qa_verdicts
             WHERE project_id = :pid
             ORDER BY created_at DESC
            """
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, {"pid": project_id})).all()
        return [QAVerdict.model_validate(_as_dict(r[0])) for r in rows]

    # ----- LLMCallRecord -----

    async def append_llm_calls(
        self, project_id: str, calls: list[dict]
    ) -> None:
        if not calls:
            return
        sql = text(
            """
            INSERT INTO llm_calls (project_id, node_id, agent_name, ts, payload)
            VALUES
              (:project_id, :node_id, :agent_name, :ts, CAST(:payload AS jsonb))
            """
        )
        async with self._engine.begin() as conn:
            for c in calls:
                await conn.execute(
                    sql,
                    {
                        "project_id": project_id,
                        "node_id": c.get("node_id"),
                        "agent_name": c.get("agent_name"),
                        "ts": float(c.get("timestamp") or 0.0),
                        "payload": json.dumps(c, default=str),
                    },
                )

    async def list_llm_calls(
        self,
        project_id: str,
        *,
        node_id: str | None = None,
        agent_name: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses = ["project_id = :pid"]
        params: dict[str, object] = {"pid": project_id, "limit": limit}
        if node_id is not None:
            clauses.append("node_id = :node_id")
            params["node_id"] = node_id
        if agent_name is not None:
            clauses.append("agent_name = :agent_name")
            params["agent_name"] = agent_name
        sql = text(
            "SELECT payload FROM llm_calls WHERE "
            + " AND ".join(clauses)
            + " ORDER BY ts DESC, seq DESC LIMIT :limit"
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, params)).all()
        return [_as_dict(r[0]) for r in rows]

    # ----- RunSnapshot -----

    async def save_run_snapshot(self, snapshot: RunSnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        sql = text(
            """
            INSERT INTO run_snapshots
              (project_id, run_id, captured_at, final_status, payload)
            VALUES
              (:project_id, :run_id, :captured_at, :final_status, CAST(:payload AS jsonb))
            ON CONFLICT (project_id, run_id) DO UPDATE
              SET captured_at = EXCLUDED.captured_at,
                  final_status = EXCLUDED.final_status,
                  payload = EXCLUDED.payload
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                sql,
                {
                    "project_id": snapshot.project_id,
                    "run_id": snapshot.run_id,
                    "captured_at": snapshot.captured_at,
                    "final_status": snapshot.final_status,
                    "payload": json.dumps(payload, default=str),
                },
            )

    async def get_run_snapshot(
        self, project_id: str, run_id: str
    ) -> RunSnapshot | None:
        sql = text(
            "SELECT payload FROM run_snapshots "
            "WHERE project_id = :pid AND run_id = :rid"
        )
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(sql, {"pid": project_id, "rid": run_id})
            ).first()
        if row is None:
            return None
        return RunSnapshot.model_validate(_as_dict(row[0]))

    async def list_run_snapshots(self, project_id: str) -> list[RunSnapshot]:
        sql = text(
            """
            SELECT payload FROM run_snapshots
             WHERE project_id = :pid
             ORDER BY captured_at DESC
            """
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, {"pid": project_id})).all()
        return [RunSnapshot.model_validate(_as_dict(r[0])) for r in rows]

    async def close(self) -> None:
        # engine 的生命周期由 build_storage 工厂管理
        return None


# ---------- Checkpointer ----------


class PostgresCheckpointer:
    """实现 `CheckpointerProtocol`。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def aget_tuple(self, config: CheckpointConfig) -> CheckpointTuple | None:
        tid = thread_id_of(config)
        ns = checkpoint_ns_of(config)
        cid = checkpoint_id_of(config)
        if cid is None:
            sql = text(
                """
                SELECT checkpoint_id, parent_checkpoint_id, checkpoint, metadata
                  FROM checkpoints
                 WHERE thread_id = :tid AND checkpoint_ns = :ns
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            )
            params: dict[str, Any] = {"tid": tid, "ns": ns}
        else:
            sql = text(
                """
                SELECT checkpoint_id, parent_checkpoint_id, checkpoint, metadata
                  FROM checkpoints
                 WHERE thread_id = :tid AND checkpoint_ns = :ns AND checkpoint_id = :cid
                """
            )
            params = {"tid": tid, "ns": ns, "cid": cid}

        async with self._engine.connect() as conn:
            row = (await conn.execute(sql, params)).first()
            if row is None:
                return None
            cid = row[0]
            parent_cid = row[1]
            checkpoint = unpickle_checkpoint(row[2])
            metadata_raw = row[3]
            metadata: CheckpointMetadata = _as_dict(metadata_raw) or {}  # type: ignore[assignment]

            # 拉 pending writes
            writes_sql = text(
                """
                SELECT task_id, idx, channel, value
                  FROM checkpoint_writes
                 WHERE thread_id = :tid AND checkpoint_ns = :ns AND checkpoint_id = :cid
                 ORDER BY task_id, idx
                """
            )
            writes_rows = (
                await conn.execute(
                    writes_sql, {"tid": tid, "ns": ns, "cid": cid}
                )
            ).all()

        pending = [
            (task_id, channel, unpickle_value(value))
            for (task_id, _idx, channel, value) in writes_rows
        ]
        cfg = make_config(tid, checkpoint_ns=ns, checkpoint_id=cid)
        parent_cfg = (
            make_config(tid, checkpoint_ns=ns, checkpoint_id=parent_cid)
            if parent_cid
            else None
        )
        return CheckpointTuple(
            config=cfg,
            checkpoint=checkpoint,  # type: ignore[arg-type]
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
        import uuid

        tid = thread_id_of(config)
        ns = checkpoint_ns_of(config)
        parent_cid = checkpoint_id_of(config)

        cid = checkpoint.get("id") or f"chk-{uuid.uuid4().hex[:12]}"
        cp = dict(checkpoint)
        cp["id"] = cid
        cp.setdefault("ts", now_ts())

        sql = text(
            """
            INSERT INTO checkpoints
              (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)
            VALUES
              (:tid, :ns, :cid, :parent_cid, :checkpoint, CAST(:metadata AS jsonb))
            ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE
              SET parent_checkpoint_id = EXCLUDED.parent_checkpoint_id,
                  checkpoint = EXCLUDED.checkpoint,
                  metadata = EXCLUDED.metadata
            """
        )
        async with self._engine.begin() as conn:
            await conn.execute(
                sql,
                {
                    "tid": tid,
                    "ns": ns,
                    "cid": cid,
                    "parent_cid": parent_cid,
                    "checkpoint": pickle_checkpoint(cp),
                    "metadata": json.dumps(dict(metadata), default=str),
                },
            )
        return make_config(tid, checkpoint_ns=ns, checkpoint_id=cid)

    async def alist(
        self,
        config: CheckpointConfig | None,
        *,
        before: CheckpointConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if config is not None:
            clauses.append("thread_id = :tid AND checkpoint_ns = :ns")
            params["tid"] = thread_id_of(config)
            params["ns"] = checkpoint_ns_of(config)
        if before is not None:
            bcid = checkpoint_id_of(before)
            if bcid is not None:
                clauses.append(
                    "created_at < (SELECT created_at FROM checkpoints "
                    "WHERE checkpoint_id = :bcid LIMIT 1)"
                )
                params["bcid"] = bcid
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
        sql = text(
            f"""
            SELECT thread_id, checkpoint_ns, checkpoint_id,
                   parent_checkpoint_id, checkpoint, metadata
              FROM checkpoints
              {where}
             ORDER BY created_at DESC
             {limit_clause}
            """
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, params)).all()

        for (tid, ns, cid, parent_cid, cp_bytes, metadata_raw) in rows:
            cfg = make_config(tid, checkpoint_ns=ns, checkpoint_id=cid)
            parent_cfg = (
                make_config(tid, checkpoint_ns=ns, checkpoint_id=parent_cid)
                if parent_cid
                else None
            )
            yield CheckpointTuple(
                config=cfg,
                checkpoint=unpickle_checkpoint(cp_bytes),  # type: ignore[arg-type]
                metadata=_as_dict(metadata_raw) or {},  # type: ignore[arg-type]
                parent_config=parent_cfg,
                pending_writes=[],
            )

    async def aput_writes(
        self,
        config: CheckpointConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
    ) -> None:
        tid = thread_id_of(config)
        ns = checkpoint_ns_of(config)
        cid = checkpoint_id_of(config)
        if cid is None:
            raise ValueError("aput_writes requires checkpoint_id in config")

        # 先查当前 task_id 的 idx 起点
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT COALESCE(MAX(idx), -1) FROM checkpoint_writes "
                        "WHERE thread_id = :tid AND checkpoint_ns = :ns "
                        "AND checkpoint_id = :cid AND task_id = :task_id"
                    ),
                    {"tid": tid, "ns": ns, "cid": cid, "task_id": task_id},
                )
            ).first()
            base_idx = int(row[0]) + 1 if row else 0

        sql = text(
            """
            INSERT INTO checkpoint_writes
              (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, value)
            VALUES (:tid, :ns, :cid, :task_id, :idx, :channel, :value)
            ON CONFLICT DO NOTHING
            """
        )
        async with self._engine.begin() as conn:
            for i, (channel, value) in enumerate(writes):
                await conn.execute(
                    sql,
                    {
                        "tid": tid,
                        "ns": ns,
                        "cid": cid,
                        "task_id": task_id,
                        "idx": base_idx + i,
                        "channel": channel,
                        "value": pickle_value(value),
                    },
                )

    async def close(self) -> None:
        return None


# ---------- helpers ----------


def _as_dict(value: Any) -> dict[str, Any]:
    """jsonb 字段 SQLAlchemy + asyncpg 通常已是 dict；偶尔是 str/bytes 时兜底解析。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _row_to_user(row: Any) -> User:
    """users 表行 → User。列序：user_id, email, password_hash, display_name, created_at。"""
    return User(
        user_id=row[0],
        email=row[1],
        password_hash=row[2],
        display_name=row[3] or "",
        created_at=row[4],
    )


__all__ = [
    "PostgresCheckpointer",
    "PostgresStateStore",
]
