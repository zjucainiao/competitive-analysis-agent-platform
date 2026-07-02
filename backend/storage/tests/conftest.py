"""storage 测试共用 fixture。

提供：
- 一个 minimal `Project` / `DAGPlan` / `CollectorOutput` / `QAVerdict` 工厂，方便单测构造数据
- e2e marker：POSTGRES_DSN / REDIS_URL 未设时自动 skip 对应测试
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from backend.schemas import (
    AgentStatus,
    AnalysisDimension,
    CollectConstraints,
    CollectDimension,
    CollectorOutput,
    DAGEdge,
    DAGNode,
    DAGPlan,
    NodeStatus,
    NodeType,
    Project,
    ProjectStatus,
    QADimension,
    QAStatus,
    QAVerdict,
)


@pytest.fixture
def make_project():
    def _factory(
        *,
        project_id: str | None = None,
        owner: str = "user-x",
        status: ProjectStatus = ProjectStatus.DRAFT,
    ) -> Project:
        return Project(
            project_id=project_id or f"prj-{uuid.uuid4().hex[:8]}",
            project_name="Demo project",
            owner=owner,
            created_at=datetime.now(tz=UTC),
            target_product="Demo SaaS",
            competitors=["Notion", "ClickUp"],
            industry="collaboration_saas",
            analysis_dimensions=[
                AnalysisDimension.FEATURE_COMPARISON,
                AnalysisDimension.PRICING_COMPARISON,
            ],
            collect_constraints=CollectConstraints(),
            status=status,
        )

    return _factory


@pytest.fixture
def make_dag_plan():
    def _factory(project_id: str, *, plan_id: str | None = None) -> DAGPlan:
        nodes = [
            DAGNode(
                node_id="n1",
                project_id=project_id,
                node_type=NodeType.AGENT_CALL,
                agent_name="collector",
            ),
            DAGNode(
                node_id="n2",
                project_id=project_id,
                node_type=NodeType.AGENT_CALL,
                agent_name="extractor",
                input_refs=["n1"],
            ),
        ]
        edges = [
            DAGEdge(edge_id="e1", from_node="n1", to_node="n2"),
        ]
        return DAGPlan(
            plan_id=plan_id or f"plan-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            nodes=nodes,
            edges=edges,
        )

    return _factory


@pytest.fixture
def make_collector_output():
    def _factory(*, task_id: str = "task-1", confidence: float = 0.9) -> CollectorOutput:
        return CollectorOutput(
            agent_name="collector",
            agent_version="1.0.0",
            task_id=task_id,
            trace_id="trace-1",
            span_id="span-1",
            status=AgentStatus.SUCCESS,
            confidence=confidence,
            self_critique="",
            raw_sources=[],
            coverage_by_dimension={d: 0 for d in CollectDimension},
        )

    return _factory


@pytest.fixture
def make_qa_verdict():
    def _factory(
        *,
        verdict_id: str | None = None,
        overall_status: QAStatus = QAStatus.PASS,
        blocking: bool = False,
    ) -> QAVerdict:
        return QAVerdict(
            verdict_id=verdict_id or f"v-{uuid.uuid4().hex[:8]}",
            overall_status=overall_status,
            dimension_results={},
            issues=[],
            routing=[],
            blocking=blocking,
        )

    return _factory


# ----- e2e markers -----

# 在 conftest 加载期（pytest 启动即加载初始 conftest，早于任何测试模块 import）
# 快照 PG/Redis 环境变量：api 测试模块 import backend.api 会触发其模块级
# load_dotenv()，把开发者 .env 的 POSTGRES_DSN / REDIS_URL 侧效应注入
# os.environ；若等到 modifyitems 时才读，会误判「环境已配好」而不 skip。
# 只认 shell 显式导出的变量，与单独跑 backend/storage/tests 时的行为一致。
_PG_DSN_AT_LOAD = os.getenv("POSTGRES_DSN")
_REDIS_URL_AT_LOAD = os.getenv("REDIS_URL")


def pytest_collection_modifyitems(config, items):
    """e2e tests 在缺 POSTGRES_DSN / REDIS_URL 时自动 skip。"""
    skip_pg = not _PG_DSN_AT_LOAD
    skip_redis = not _REDIS_URL_AT_LOAD
    pg_marker = pytest.mark.skip(reason="POSTGRES_DSN not set; skipping PG e2e tests")
    redis_marker = pytest.mark.skip(reason="REDIS_URL not set; skipping Redis e2e tests")
    for item in items:
        if "postgres" in item.keywords and skip_pg:
            item.add_marker(pg_marker)
        if "redis" in item.keywords and skip_redis:
            item.add_marker(redis_marker)
