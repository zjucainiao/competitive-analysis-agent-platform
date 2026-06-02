"""QA — 报告质检 Agent。

入口：``QA``。完整契约见 docs/AGENTS.md § 7，规则细节见 docs/QA.md。

最小可用示例（mock 模式）::

    from backend.agents.qa import QA
    from backend.agents.qa.fixtures import load_demo_input

    agent = QA(mock=True)
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    print(out.verdict.overall_status, out.verdict.blocking)
"""

from .agent import QA
from .checkers import (
    DEFAULT_CHECKERS,
    CheckerContext,
    CheckerResult,
    EvidenceCompletenessChecker,
    ExpressionChecker,
    FactConsistencyChecker,
    FreshnessChecker,
    LogicConsistencyChecker,
    SchemaCompletenessChecker,
)
from .routing import (
    MAX_RETRY_VERDICTS,
    SAME_ISSUE_MAX_OCCURRENCES,
    SEVERITY_WEIGHTS,
    aggregate_verdict,
    build_routing,
)

__all__ = [
    "DEFAULT_CHECKERS",
    "MAX_RETRY_VERDICTS",
    "QA",
    "SAME_ISSUE_MAX_OCCURRENCES",
    "SEVERITY_WEIGHTS",
    "CheckerContext",
    "CheckerResult",
    "EvidenceCompletenessChecker",
    "ExpressionChecker",
    "FactConsistencyChecker",
    "FreshnessChecker",
    "LogicConsistencyChecker",
    "SchemaCompletenessChecker",
    "aggregate_verdict",
    "build_routing",
]
