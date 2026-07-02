"""Orchestrator —— DAG 任务编排器。

公开入口：

    from backend.orchestrator import Orchestrator, AgentRegistry, Planner

详细设计见 docs/DAG.md。本模块对外承诺：

- 输入 ``Project`` → ``Planner.plan()`` 生成 ``DAGPlan``
- ``Orchestrator.run(plan, project)`` 按拓扑序异步执行，每个节点 yield
  ``NodeExecutionResult``
- ``Orchestrator.handle_qa_routing()`` 处理 QA 路由，派生 _v{n+1} 节点
- 所有持久化通过 ``backend.storage`` 的 Protocol，编排器自身不直连 PG/Redis
"""

from __future__ import annotations

from backend.observability import NullSpan, NullTracer  # 等价于以前 .tracing 的占位

from .agent_registry import AgentRegistry
from .executor import BuildInputError, Executor
from .feedback_router import DEFAULT_MAX_ROUNDS, FeedbackOutcome, FeedbackRouter
from .orchestrator import Orchestrator
from .planner import Planner, TemplateNotFoundError
from .state import OrchestratorState

__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "AgentRegistry",
    "BuildInputError",
    "Executor",
    "FeedbackOutcome",
    "FeedbackRouter",
    "NullSpan",
    "NullTracer",
    "Orchestrator",
    "OrchestratorState",
    "Planner",
    "TemplateNotFoundError",
]
