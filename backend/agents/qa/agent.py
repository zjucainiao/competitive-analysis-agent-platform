"""QA Agent — 报告多维度质检 + 路由决策。

职责：
- 对 ReportDraft 跑全部 checker（fact / evidence / schema / coverage / identity /
  logic / freshness / expression）
- 聚合 issues → routing → overall_status / blocking
- 防死循环：prior_verdicts 中反复出现的 issue 自动降级，超阈值强制放行
- 不修改 draft，只产 QAVerdict

模式：
- mock：按 ``inp.draft.version`` 切换 fixture（version==1 → needs_revision，
  version>=2 → pass），用于 Orchestrator M3 闭环演示
- real：跑全部 6 checker；LLM 不可用时降级为规则检查
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from backend.agents._base import (
    BaseAgent,
    LLMProviderProtocol,
    ToolRegistryProtocol,
    TracerProtocol,
)
from backend.schemas import (
    AgentError,
    AgentStatus,
    Evidence,
    QADimension,
    QADimensionResult,
    QAInput,
    QAIssue,
    QAOutput,
    QAStatus,
    QAVerdict,
)

from .checkers import (
    DEFAULT_CHECKERS,
    BaseChecker,
    CheckerContext,
    CheckerResult,
)
from .routing import (
    SEVERITY_WEIGHTS,
    aggregate_verdict,
    build_routing,
    count_prior_issue_occurrences,
    downgrade_repeated_issues,
    escalate_by_self_status,
    max_retry_error,
    synthesize_threshold_issues,
)

PROMPT_DIR = Path(__file__).parent / "prompts"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_DB_PATH = (
    _REPO_ROOT / "fixtures" / "mock_data" / "evidences" / "evidence_db.jsonl"
)
_QA_FIXTURE_DIR = _REPO_ROOT / "fixtures" / "mock_data" / "qa_verdicts"


class QA(BaseAgent[QAInput, QAOutput]):
    """质检 Agent。详细规约见 docs/QA.md。"""

    name: ClassVar[str] = "qa"
    version: ClassVar[str] = "1.0.0"
    input_model: ClassVar[type[BaseModel]] = QAInput
    output_model: ClassVar[type[BaseModel]] = QAOutput
    required_tools: ClassVar[list[str]] = []

    CONFIDENCE_PASS: ClassVar[float] = 0.9
    CONFIDENCE_SOFT_FAIL: ClassVar[float] = 0.75
    CONFIDENCE_HARD_FAIL: ClassVar[float] = 0.6
    CONFIDENCE_REJECT: ClassVar[float] = 0.4

    def __init__(
        self,
        *,
        llm: LLMProviderProtocol | None = None,
        tools: ToolRegistryProtocol | None = None,
        tracer: TracerProtocol | None = None,
        evidence_db: dict[str, Evidence] | None = None,
        checkers: tuple[type[BaseChecker], ...] | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(llm=llm, tools=tools, tracer=tracer, mock=mock)
        # evidence_db 缺省时从 fixtures 加载；缺失不致命，只是某些数字/freshness 校验跳过
        if evidence_db is None:
            evidence_db = _load_evidence_db()
        self.evidence_db = evidence_db
        self.checkers: tuple[type[BaseChecker], ...] = checkers or DEFAULT_CHECKERS

    # ---------- Mock ----------

    def _run_mock(self, inp: QAInput) -> QAOutput:
        """按 draft.version 切 fixture，演示 QA → 重做 → 通过 闭环。"""
        version = inp.draft.version
        verdict = _load_verdict_fixture(version)
        confidence = (
            self.CONFIDENCE_PASS
            if verdict.overall_status is QAStatus.PASS
            else self.CONFIDENCE_HARD_FAIL
        )
        critique = (
            "[mock] 按 draft.version="
            f"{version} 返回 {verdict.overall_status.value} 固定 verdict。"
        )
        return QAOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=AgentStatus.SUCCESS,
            confidence=confidence,
            self_critique=critique,
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
            duration_ms=0,
            errors=[],
            verdict=verdict,
        )

    # ---------- Real ----------

    def _run(self, inp: QAInput) -> QAOutput:
        ctx = CheckerContext(
            draft=inp.draft,
            analysis=inp.analysis,
            profiles=inp.profiles,
            evidence_db=self.evidence_db,
            evidence_store_handle=inp.evidence_store_handle,
            prior_verdicts=list(inp.prior_verdicts),
            llm=self.llm,
            prompt_dir=str(PROMPT_DIR),
        )

        errors: list[AgentError] = []
        dimension_results: dict[QADimension, QADimensionResult] = {}
        all_issues: list[QAIssue] = []

        for checker_cls in self.checkers:
            checker = checker_cls()
            try:
                result: CheckerResult = checker.run(ctx)
            except Exception as e:  # noqa: BLE001
                errors.append(
                    AgentError(
                        code="CHECKER_FAILED",
                        message=(
                            f"{checker_cls.__name__} crashed: "
                            f"{type(e).__name__}: {e}"
                        ),
                        severity="error",
                        retriable=True,
                    )
                )
                dimension_results[checker_cls.dimension] = QADimensionResult(
                    dimension=checker_cls.dimension,
                    score=0.0,
                    pass_=False,  # type: ignore[call-arg]
                    notes="checker crashed",
                )
                continue
            dimension_results[result.dimension] = QADimensionResult(
                dimension=result.dimension,
                score=result.score,
                pass_=result.pass_,  # type: ignore[call-arg]
                notes=result.notes,
            )
            all_issues.extend(result.issues)
            errors.extend(result.errors)

        # ---- A1：杀静默放行 —— 对低分但无 issue 的维度补发 ----
        all_issues.extend(
            synthesize_threshold_issues(dimension_results, all_issues)
        )

        # ---- ⑤ 把上游 agent 自评(needs_rework)接入判级：加权其名下已有 issue ----
        all_issues = escalate_by_self_status(all_issues, inp.upstream_statuses)

        # ---- 防死循环：降级反复出现的 issue ----
        prior_counts = count_prior_issue_occurrences(inp.prior_verdicts)
        adjusted_issues, downgraded = downgrade_repeated_issues(
            all_issues, prior_counts
        )

        # ---- 整体判定（A1：核心维度不及格 → 强制 blocking 返工）----
        # 传 prior_verdicts 启用复发护栏:已失败过的核心维度不再强制阻塞,避免空转。
        overall = aggregate_verdict(
            adjusted_issues,
            len(inp.prior_verdicts),
            dimension_results,
            prior_verdicts=list(inp.prior_verdicts),
        )
        if overall.max_retry_reached:
            errors.append(max_retry_error(len(inp.prior_verdicts)))

        # ---- routing 装配 ----
        routing = build_routing(
            issues=adjusted_issues,
            blocking=overall.blocking,
        )

        verdict = QAVerdict(
            verdict_id=f"qa_{inp.task_id}_v{inp.draft.version}",
            overall_status=overall.status,
            dimension_results=dimension_results,
            issues=adjusted_issues,
            routing=routing,
            blocking=overall.blocking,
        )

        critique = _build_critique(
            verdict=verdict,
            dimension_results=dimension_results,
            downgraded=downgraded,
            errors=errors,
            prior_count=len(inp.prior_verdicts),
            max_retry_reached=overall.max_retry_reached,
            upstream_statuses=inp.upstream_statuses,
        )

        return QAOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=_derive_status(overall.status, errors),
            confidence=overall.confidence,
            self_critique=critique,
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
            duration_ms=0,
            errors=errors,
            verdict=verdict,
        )

    # ---------- 后置校验 ----------

    def _post_validate(self, out: QAOutput, inp: QAInput) -> None:
        verdict = out.verdict

        # 1. dimension_results 必须覆盖所有维度（QADimension 全集）
        missing = [
            d
            for d in QADimension
            if d not in verdict.dimension_results
        ]
        if missing:
            from backend.agents._base import AgentRunError

            raise AgentRunError(
                code="OUTPUT_TYPE_MISMATCH",
                message=(
                    f"QA verdict missing dimensions: "
                    f"{[d.value for d in missing]}"
                ),
                retriable=False,
            )

        # 2. 每个 issue 的 location 必须能被人类回溯（非空）
        for issue in verdict.issues:
            if not issue.location.strip():
                from backend.agents._base import AgentRunError

                raise AgentRunError(
                    code="OUTPUT_TYPE_MISMATCH",
                    message=f"QA issue {issue.issue_id!r} has empty location",
                    retriable=False,
                )

        # 3. routing 的 target_agent 必须出现在 issues 中
        targets_in_issues = {i.target_agent for i in verdict.issues}
        for r in verdict.routing:
            if r.target_agent not in targets_in_issues:
                from backend.agents._base import AgentRunError

                raise AgentRunError(
                    code="OUTPUT_TYPE_MISMATCH",
                    message=(
                        f"routing target {r.target_agent!r} has no backing issues"
                    ),
                    retriable=False,
                )

        # 4. blocking 与 overall_status 自洽：reject 必须 blocking
        if verdict.overall_status is QAStatus.REJECT and not verdict.blocking:
            from backend.agents._base import AgentRunError

            raise AgentRunError(
                code="OUTPUT_TYPE_MISMATCH",
                message="reject verdict must be blocking",
                retriable=False,
            )


# ---------- helpers ----------


def _derive_status(qa_status: QAStatus, errors: list[AgentError]) -> AgentStatus:
    fatal = any(e.severity == "fatal" for e in errors)
    if fatal:
        return AgentStatus.FAILED
    if qa_status is QAStatus.PASS:
        return AgentStatus.SUCCESS
    # NEEDS_REVISION / REJECT：QA 本身成功跑完了，只是 verdict 不通过
    # 用 PARTIAL 标识 "Agent 成功，但产物有问题"
    return AgentStatus.PARTIAL


def _build_critique(
    *,
    verdict: QAVerdict,
    dimension_results: dict[QADimension, QADimensionResult],
    downgraded: list[QAIssue],
    errors: list[AgentError],
    prior_count: int,
    max_retry_reached: bool,
    upstream_statuses: dict[str, str] | None = None,
) -> str:
    bits: list[str] = []
    failing = [
        d for d, r in dimension_results.items() if not r.pass_
    ]
    if failing:
        bits.append(
            "未通过维度："
            + ", ".join(d.value for d in failing)
        )
    flagged = sorted(
        a for a, s in (upstream_statuses or {}).items() if s == "needs_rework"
    )
    if flagged:
        bits.append(f"上游自评 needs_rework：{', '.join(flagged)}（已加权其名下 issue）")
    if verdict.issues:
        weight = sum(SEVERITY_WEIGHTS[i.severity] for i in verdict.issues)
        bits.append(
            f"共 {len(verdict.issues)} 条 issue，"
            f"严重度权重 {weight}"
        )
    if downgraded:
        bits.append(
            f"自动降级 {len(downgraded)} 条反复出现的 issue"
        )
    if max_retry_reached:
        bits.append(f"已达重试上限（prior={prior_count}），强制放行")
    err_codes = sorted({e.code for e in errors if e.severity in ("warn", "error")})
    if err_codes:
        bits.append(f"过程告警：{', '.join(err_codes)}")
    if not bits:
        return (
            f"6 维度全部通过；blocking={verdict.blocking}。"
        )
    return " | ".join(bits)


def _load_evidence_db() -> dict[str, Evidence]:
    if not _EVIDENCE_DB_PATH.exists():
        return {}
    out: dict[str, Evidence] = {}
    for line in _EVIDENCE_DB_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data: dict[str, Any] = json.loads(line)
            ev = Evidence.model_validate(data)
            out[ev.evidence_id] = ev
        except Exception:  # noqa: BLE001
            continue
    return out


def _load_verdict_fixture(version: int) -> QAVerdict:
    """version==1 → needs_revision，version>=2 → pass。"""
    name = "pass.json" if version >= 2 else "needs_revision.json"
    path = _QA_FIXTURE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"QA mock fixture not found at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return QAVerdict.model_validate(data)


__all__ = ["QA"]
