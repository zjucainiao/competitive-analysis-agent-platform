"""QA checker 集合。

每个 checker 对应 docs/QA.md § 3 中的一个维度。
QA Agent 主类按固定顺序调度所有 checker，聚合产出 QAVerdict。
"""

from ._base import (
    BaseChecker,
    Checker,
    CheckerContext,
    CheckerResult,
    issue_dedupe_key,
    severity_for_score,
)
from .coverage_density import CoverageDensityChecker
from .evidence_completeness import EvidenceCompletenessChecker
from .expression import ExpressionChecker
from .fact_consistency import FactConsistencyChecker
from .freshness import FreshnessChecker
from .logic_consistency import LogicConsistencyChecker
from .schema_completeness import SchemaCompletenessChecker

# 调度顺序：先静态规则，再 LLM；fact 放最前是因为它最影响 overall_status。
# coverage_density 紧跟 schema_completeness：都属"完整性"族，且不调 LLM。
DEFAULT_CHECKERS: tuple[type[BaseChecker], ...] = (
    FactConsistencyChecker,
    EvidenceCompletenessChecker,
    SchemaCompletenessChecker,
    CoverageDensityChecker,
    LogicConsistencyChecker,
    FreshnessChecker,
    ExpressionChecker,
)


__all__ = [
    "BaseChecker",
    "Checker",
    "CheckerContext",
    "CheckerResult",
    "CoverageDensityChecker",
    "DEFAULT_CHECKERS",
    "EvidenceCompletenessChecker",
    "ExpressionChecker",
    "FactConsistencyChecker",
    "FreshnessChecker",
    "LogicConsistencyChecker",
    "SchemaCompletenessChecker",
    "issue_dedupe_key",
    "severity_for_score",
]
