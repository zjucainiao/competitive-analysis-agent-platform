"""Checker 基础设施。

每个维度的 checker 实现 ``Checker`` 协议，接受同一个 ``CheckerContext``，
返回 ``CheckerResult``（含 score / pass_ / issues / notes / errors）。

QA Agent 主类负责：
- 把各 checker 的 CheckerResult 聚合到 QAVerdict
- 调用 routing 模块把 issues 装配成 QARouting
- 套用防死循环策略
- 计算整体 overall_status / blocking / confidence

Checker 自身只关心"这个维度有没有问题、严重度多少、定位在哪"。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Protocol, runtime_checkable

from backend.agents._base import LLMProviderProtocol
from backend.schemas import (
    AgentError,
    AnalysisResult,
    CompetitorProfile,
    Evidence,
    QADimension,
    QAIssue,
    QAVerdict,
    ReportDraft,
)


@dataclass(frozen=True)
class CheckerContext:
    """跨 checker 的只读上下文。"""

    draft: ReportDraft
    analysis: AnalysisResult
    profiles: dict[str, CompetitorProfile]
    evidence_db: dict[str, Evidence]
    evidence_store_handle: str | None = None
    prior_verdicts: list[QAVerdict] = field(default_factory=list)
    llm: LLMProviderProtocol | None = None
    prompt_dir: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CheckerResult:
    """单维度 checker 的输出。"""

    dimension: QADimension
    score: float
    pass_: bool
    notes: str = ""
    issues: list[QAIssue] = field(default_factory=list)
    errors: list[AgentError] = field(default_factory=list)


@runtime_checkable
class Checker(Protocol):
    """所有 checker 实现的协议。"""

    dimension: ClassVar[QADimension]

    def run(self, ctx: CheckerContext) -> CheckerResult:
        ...


class BaseChecker(ABC):
    """方便子类继承的抽象基类（同时满足 Checker Protocol）。"""

    dimension: ClassVar[QADimension]

    @abstractmethod
    def run(self, ctx: CheckerContext) -> CheckerResult:
        raise NotImplementedError


# ---------- 通用工具 ----------


def severity_for_score(score: float) -> str | None:
    """根据 score 推导该维度问题的整体严重度。

    None 表示该维度无须开 issue。
    """
    if score >= 0.95:
        return None
    if score >= 0.80:
        return "minor"
    if score >= 0.60:
        return "major"
    return "critical"


def issue_dedupe_key(dimension: QADimension, location: str) -> str:
    """同一 issue 跨 verdict 去重的 key（用于防死循环计数）。"""
    return f"{dimension.value}|{location}"


__all__ = [
    "BaseChecker",
    "Checker",
    "CheckerContext",
    "CheckerResult",
    "issue_dedupe_key",
    "severity_for_score",
]
