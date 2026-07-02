"""PostgreSQL DDL —— storage 层的全部建表语句。

v1 阶段不上 alembic：`init_schema(engine)` 跑一次 `CREATE TABLE IF NOT EXISTS`
即可。后续真上多人开发 / 线上迁移再切 alembic。

表结构对应 docs/STORAGE.md § 2.4 + § 3.3。
"""

from __future__ import annotations

CREATE_CHECKPOINTS = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id            text NOT NULL,
    checkpoint_ns        text NOT NULL DEFAULT '',
    checkpoint_id        text NOT NULL,
    parent_checkpoint_id text,
    checkpoint           bytea NOT NULL,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
"""

CREATE_CHECKPOINTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_created
    ON checkpoints (thread_id, checkpoint_ns, created_at DESC);
"""

CREATE_CHECKPOINT_WRITES = """
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
"""

CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id       text PRIMARY KEY,
    email         text NOT NULL,
    password_hash text NOT NULL,
    display_name  text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now()
);
"""

# email 大小写不敏感唯一：注册/登录前已 lower() 规范化，这里再加库级唯一兜底
CREATE_USERS_EMAIL_IDX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
    ON users (lower(email));
"""

CREATE_PROJECTS = """
CREATE TABLE IF NOT EXISTS projects (
    project_id text PRIMARY KEY,
    owner      text NOT NULL,
    status     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    payload    jsonb NOT NULL
);
"""

CREATE_PROJECTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_projects_owner_status
    ON projects (owner, status, updated_at DESC);
"""

CREATE_DAG_PLANS = """
CREATE TABLE IF NOT EXISTS dag_plans (
    plan_id    text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    payload    jsonb NOT NULL
);
"""

CREATE_DAG_PLANS_IDX = """
CREATE INDEX IF NOT EXISTS idx_dag_plans_project
    ON dag_plans (project_id, created_at DESC);
"""

CREATE_NODE_OUTPUTS = """
CREATE TABLE IF NOT EXISTS node_outputs (
    project_id text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    node_id    text NOT NULL,
    run_id     text,
    agent_name text NOT NULL,
    status     text NOT NULL,
    saved_at   timestamptz NOT NULL DEFAULT now(),
    payload    jsonb NOT NULL,
    PRIMARY KEY (project_id, node_id)
);
"""

CREATE_QA_VERDICTS = """
CREATE TABLE IF NOT EXISTS qa_verdicts (
    verdict_id     text PRIMARY KEY,
    project_id     text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    run_id         text,
    overall_status text NOT NULL,
    blocking       boolean NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    payload        jsonb NOT NULL
);
"""

# P2-RUNSCOPE 迁移：已存在的库补 run_id 列（幂等）。新库由上面的 CREATE 直接带列；
# 这两条对老库 ADD COLUMN，老行 run_id=NULL（被 list_* 的「最新 run」作用域视作一个
# 历史 run，不影响新 run 隔离）。
ALTER_NODE_OUTPUTS_RUN_ID = "ALTER TABLE node_outputs ADD COLUMN IF NOT EXISTS run_id text;"
ALTER_QA_VERDICTS_RUN_ID = "ALTER TABLE qa_verdicts ADD COLUMN IF NOT EXISTS run_id text;"

CREATE_QA_VERDICTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_qa_verdicts_project
    ON qa_verdicts (project_id, created_at DESC);
"""

CREATE_RUN_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS run_snapshots (
    project_id   text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    run_id       text NOT NULL,
    captured_at  timestamptz NOT NULL,
    final_status text NOT NULL,
    payload      jsonb NOT NULL,
    PRIMARY KEY (project_id, run_id)
);
"""

CREATE_RUN_SNAPSHOTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_run_snapshots_project_time
    ON run_snapshots (project_id, captured_at DESC);
"""

CREATE_LLM_CALLS = """
CREATE TABLE IF NOT EXISTS llm_calls (
    seq        bigserial PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    node_id    text,
    agent_name text,
    ts         double precision NOT NULL DEFAULT 0,
    payload    jsonb NOT NULL
);
"""

CREATE_LLM_CALLS_IDX = """
CREATE INDEX IF NOT EXISTS idx_llm_calls_project_ts
    ON llm_calls (project_id, ts DESC, seq DESC);
"""

ALL_STATEMENTS = [
    CREATE_USERS,
    CREATE_USERS_EMAIL_IDX,
    CREATE_PROJECTS,
    CREATE_PROJECTS_IDX,
    CREATE_DAG_PLANS,
    CREATE_DAG_PLANS_IDX,
    CREATE_NODE_OUTPUTS,
    ALTER_NODE_OUTPUTS_RUN_ID,
    CREATE_QA_VERDICTS,
    ALTER_QA_VERDICTS_RUN_ID,
    CREATE_QA_VERDICTS_IDX,
    CREATE_RUN_SNAPSHOTS,
    CREATE_RUN_SNAPSHOTS_IDX,
    CREATE_LLM_CALLS,
    CREATE_LLM_CALLS_IDX,
    CREATE_CHECKPOINTS,
    CREATE_CHECKPOINTS_IDX,
    CREATE_CHECKPOINT_WRITES,
]


async def init_schema(engine_or_conn) -> None:  # type: ignore[no-untyped-def]
    """跑全部建表语句。可接受 SQLAlchemy AsyncEngine 或 asyncpg 连接。

    幂等：所有语句都是 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`。
    """
    # SQLAlchemy AsyncEngine
    if hasattr(engine_or_conn, "begin"):
        from sqlalchemy import text as _text

        async with engine_or_conn.begin() as conn:
            for stmt in ALL_STATEMENTS:
                await conn.execute(_text(stmt))
        return
    # asyncpg 连接（rare path）
    for stmt in ALL_STATEMENTS:
        await engine_or_conn.execute(stmt)


__all__ = ["ALL_STATEMENTS", "init_schema"]
