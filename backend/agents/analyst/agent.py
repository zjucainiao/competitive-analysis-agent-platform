"""Analyst Agent — 多 profile 对比分析。

职责：对多份 CompetitorProfile 做多维度对比，输出 AnalysisResult；
每条 AnalysisClaim 必须绑定至少 1 条来自输入 profile 池的 evidence_id，
不允许引入输入之外的 evidence（幻觉抑制 L2 / L3）。

模式：
- mock：直接走 heuristic（dimensions.py 中的纯函数）
- real：每个维度优先 LLM 结构化分析；失败 / 不可用 → fallback heuristic

LLM 路径仍要求 response_format=DimensionAnalysis，并复用 _post_validate
做 evidence_ids ⊆ pool 的硬门禁。

详细契约见 docs/AGENTS.md § 5；幻觉抑制策略见 docs/HALLUCINATION_CONTROL.md。
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from backend.agents._base import (
    AgentRunError,
    BaseAgent,
    LLMProviderProtocol,
    ToolRegistryProtocol,
    TracerProtocol,
)
from backend.schemas import (
    AgentError,
    AgentStatus,
    AnalysisClaim,
    AnalysisDimension,
    AnalysisResult,
    AnalystInput,
    AnalystOutput,
    CompetitorProfile,
    DimensionAnalysis,
)

from .dimensions import analyze_dimension, collect_profile_evidence_ids

PROMPT_DIR = Path(__file__).parent / "prompts"


class Analyst(BaseAgent[AnalystInput, AnalystOutput]):
    """分析 Agent。详见 docs/AGENTS.md § 5。"""

    name: ClassVar[str] = "analyst"
    version: ClassVar[str] = "1.0.0"
    input_model: ClassVar[type[BaseModel]] = AnalystInput
    output_model: ClassVar[type[BaseModel]] = AnalystOutput
    required_tools: ClassVar[list[str]] = []  # evidence retrieval 可选，v1 不强制

    # 各维度 LLM 分析相互独立 → 并行执行，墙钟从 Σ(各维度) 降到 ~最慢单维度。
    # 关键：串行 6 个 Doubao 结构化输出（含 freeform 兜底 ~55s/次）会顶破节点
    # 120s 超时 → 重试从头跑 → 永远完不成。并行后 ~最慢维度即可收敛。
    MAX_DIMENSION_WORKERS: ClassVar[int] = 6

    # confidence 调参
    BASE_CONFIDENCE: ClassVar[float] = 0.9
    PENALTY_DROPPED_CLAIM: ClassVar[float] = 0.02
    PENALTY_EMPTY_DIM: ClassVar[float] = 0.10
    PENALTY_LOW_COVERAGE: ClassVar[float] = 0.15
    LOW_COVERAGE_THRESHOLD: ClassVar[float] = 0.5

    def __init__(
        self,
        *,
        llm: LLMProviderProtocol | None = None,
        tools: ToolRegistryProtocol | None = None,
        tracer: TracerProtocol | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__(llm=llm, tools=tools, tracer=tracer, mock=mock)

    # ----- Mock -----

    def _run_mock(self, inp: AnalystInput) -> AnalystOutput:
        return self._build_output(inp, allow_llm=False)

    # ----- Real -----

    def _run(self, inp: AnalystInput) -> AnalystOutput:
        return self._build_output(inp, allow_llm=True)

    # ----- 业务级后置校验 -----

    def _post_validate(self, out: AnalystOutput, inp: AnalystInput) -> None:
        # 1. 请求的维度必须全部在结果里（即便没有 claim 也要有占位 DimensionAnalysis）
        for d in inp.dimensions:
            if d not in out.result.dimensions:
                raise AgentRunError(
                    code="DIMENSION_NOT_APPLICABLE",
                    message=f"requested dimension {d.value} missing from result",
                    retriable=False,
                )
        # 2. target_product 一致
        if out.result.target_product != inp.target_product:
            raise AgentRunError(
                code="OUTPUT_TYPE_MISMATCH",
                message=(
                    f"target_product mismatch: input={inp.target_product!r}, "
                    f"output={out.result.target_product!r}"
                ),
                retriable=False,
            )
        # 3. 任何 claim 引用的 evidence_id 必须出现在输入 profile 池内
        valid_pool = self._aggregate_pool(inp.profiles)
        for analysis in out.result.dimensions.values():
            for c in analysis.claims:
                bad = [e for e in c.evidence_ids if e not in valid_pool]
                if bad:
                    raise AgentRunError(
                        code="INSUFFICIENT_EVIDENCE",
                        message=(
                            f"claim {c.claim_id} cites evidence_ids outside input "
                            f"profile pool: {bad}"
                        ),
                        retriable=False,
                    )

    # ----- 内部：核心组装流程 -----

    def _build_output(self, inp: AnalystInput, *, allow_llm: bool) -> AnalystOutput:
        valid_pool = self._aggregate_pool(inp.profiles)
        coverage = self._profile_coverage(inp.profiles)
        errors: list[AgentError] = []
        per_dim: dict[AnalysisDimension, DimensionAnalysis] = {}
        total_dropped = 0
        missing_profiles = sorted(
            {inp.target_product, *inp.competitors} - set(inp.profiles.keys())
        )
        if missing_profiles:
            errors.append(
                AgentError(
                    code="PROFILE_INCOMPLETE",
                    message=(
                        f"missing profile for product(s): {missing_profiles}; "
                        "comparisons involving these are skipped"
                    ),
                    severity="warn",
                    retriable=False,
                )
            )

        # 单维度工作单元：用本地 errors 列表（线程安全），返回 (维度, 结果, dropped, 本地errors)
        def _run_one(
            dimension: AnalysisDimension,
        ) -> tuple[AnalysisDimension, DimensionAnalysis, int, list[AgentError]]:
            local_errors: list[AgentError] = []
            analysis = self._analyze_one(
                dimension=dimension,
                inp=inp,
                valid_pool=valid_pool,
                allow_llm=allow_llm,
                errors=local_errors,
            )
            cleaned, dropped = self._scrub_claims(analysis, valid_pool)
            if dropped:
                local_errors.append(
                    AgentError(
                        code="INSUFFICIENT_EVIDENCE",
                        message=(
                            f"dropped {dropped} claim(s) from {dimension.value} "
                            "due to missing/invalid evidence references"
                        ),
                        severity="warn",
                        retriable=False,
                    )
                )
            return dimension, cleaned, dropped, local_errors

        dims = list(inp.dimensions)
        if not allow_llm or len(dims) <= 1:
            # mock / 单维度：串行（无并发开销，且确定性）
            dim_results = [_run_one(d) for d in dims]
        else:
            # 每维度 copy_context()：把 LLM trace contextvar（node_id/trace_id）带进
            # worker 线程，否则并发产生的 LLM call 会丢失 node 归属（同 collector）。
            contexts = [contextvars.copy_context() for _ in dims]
            with ThreadPoolExecutor(
                max_workers=min(len(dims), self.MAX_DIMENSION_WORKERS)
            ) as pool:
                futures = [
                    pool.submit(ctx.run, _run_one, dim)
                    for dim, ctx in zip(dims, contexts)
                ]
                # 按提交顺序取结果 → per_dim / errors 顺序与串行一致（确定性）
                dim_results = [f.result() for f in futures]

        for dimension, cleaned, dropped, local_errors in dim_results:
            per_dim[dimension] = cleaned
            total_dropped += dropped
            errors.extend(local_errors)

        result = AnalysisResult(
            target_product=inp.target_product,
            competitors=inp.competitors,
            dimensions=per_dim,
        )

        confidence = self._overall_confidence(per_dim, total_dropped, coverage)
        critique = self._build_critique(per_dim, total_dropped, errors, coverage)
        status = self._derive_status(per_dim, confidence, missing_profiles)

        return AnalystOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=status,
            confidence=confidence,
            self_critique=critique,
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
            duration_ms=0,  # BaseAgent.invoke 回填
            errors=errors,
            result=result,
        )

    # ----- 单维度：LLM-first，启发式 fallback -----

    def _analyze_one(
        self,
        *,
        dimension: AnalysisDimension,
        inp: AnalystInput,
        valid_pool: set[str],
        allow_llm: bool,
        errors: list[AgentError],
    ) -> DimensionAnalysis:
        if allow_llm and self.llm is not None:
            try:
                llm_analysis = self._llm_analyze(dimension, inp, valid_pool)
            except Exception as e:
                llm_analysis = None
                errors.append(
                    AgentError(
                        code="LLM_SCHEMA_INVALID",
                        message=(
                            f"LLM analysis for {dimension.value} failed: "
                            f"{type(e).__name__}: {e}"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )
            if llm_analysis is not None:
                return llm_analysis
        return analyze_dimension(
            dimension,
            target_product=inp.target_product,
            competitors=inp.competitors,
            profiles=inp.profiles,
            valid_pool=valid_pool,
        )

    def _llm_analyze(
        self,
        dimension: AnalysisDimension,
        inp: AnalystInput,
        valid_pool: set[str],
    ) -> DimensionAnalysis | None:
        if self.llm is None:
            return None
        prompt_path = PROMPT_DIR / f"{dimension.value}.md"
        if not prompt_path.exists():
            return None
        system, user_template = _split_prompt(prompt_path.read_text(encoding="utf-8"))

        # QA 反馈块：当且仅当本节点是 ``analyst_v{n+1}``（FeedbackRouter 派生）时
        # inp.qa_feedback 非空。把上一轮 verdict 里 target_agent=="analyst" 的 issue
        # 渲染成强标记块进 prompt，让 LLM 在重做时针对性改 claim / evidence 选择，
        # 而不是只是版本号 bump 后照旧重出。
        from backend.agents._qa_feedback import render_qa_feedback_block

        qa_block = render_qa_feedback_block(
            inp.qa_feedback,
            closing_instruction=(
                f"Apply the fixes above when re-emitting this dimension "
                f"({dimension.value}). Only address issues whose ``location`` "
                f"references this dimension or its claims; other dimensions are "
                f"regenerated separately. Do NOT re-introduce dropped evidence_ids "
                f"or claims flagged by QA."
            ),
        )

        user = _render(
            user_template,
            target=inp.target_product,
            competitors=", ".join(inp.competitors) if inp.competitors else "(none — single-product research)",
            dimension=dimension.value,
            valid_evidence_ids=", ".join(sorted(valid_pool)),
            profiles_json=_compact_profiles(inp.profiles),
            qa_feedback_block=qa_block,
        )

        # 单产品调研模式：在 user prompt 顶部加一行强提示，让 LLM 不要幻觉对比
        if not inp.competitors:
            user = (
                "## NOTE: Single-product research mode\n"
                "There are NO competitors in this run. Do NOT invent comparison claims. "
                "Describe ONLY the target product based on its profile + cited evidence. "
                "Skip any 'X is better/worse than Y' framing.\n\n"
                + user
            )
        resp = self.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=DimensionAnalysis,
            temperature=0.3,
            max_tokens=2500,
        )
        try:
            parsed = _coerce_pydantic(resp, DimensionAnalysis)
        except (ValueError, ValidationError):
            return None
        # 强制对齐 dimension 字段（防 LLM 输错枚举值）
        if parsed.dimension is not dimension:
            try:
                parsed = DimensionAnalysis(
                    dimension=dimension,
                    summary=parsed.summary,
                    claims=parsed.claims,
                    comparison_matrix=parsed.comparison_matrix,
                    confidence=parsed.confidence,
                )
            except ValidationError:
                return None
        return parsed

    # ----- evidence 过滤 -----

    @staticmethod
    def _aggregate_pool(profiles: dict[str, CompetitorProfile]) -> set[str]:
        pool: set[str] = set()
        for profile in profiles.values():
            pool.update(collect_profile_evidence_ids(profile))
        return pool

    def _scrub_claims(
        self, analysis: DimensionAnalysis, valid_pool: set[str]
    ) -> tuple[DimensionAnalysis, int]:
        """剔除 evidence_id 不在 pool 内的 claim；保留部分有效引用的版本。

        每条 claim 至少需要 1 条有效 evidence；否则整条丢弃。
        counter_evidence_ids 同样按 pool 过滤。
        """
        kept: list[AnalysisClaim] = []
        dropped = 0
        for claim in analysis.claims:
            valid_ev = [e for e in claim.evidence_ids if e in valid_pool]
            if not valid_ev:
                dropped += 1
                continue
            invalid = [e for e in claim.evidence_ids if e not in valid_pool]
            new_confidence = claim.confidence
            if invalid:
                # 有部分非法引用 → 降置信
                new_confidence = max(0.0, claim.confidence - 0.1)
            kept.append(
                AnalysisClaim(
                    claim_id=claim.claim_id,
                    text=claim.text,
                    products_involved=claim.products_involved,
                    evidence_ids=valid_ev,
                    confidence=new_confidence,
                    counter_evidence_ids=[
                        e for e in claim.counter_evidence_ids if e in valid_pool
                    ],
                    qualifier=claim.qualifier,
                )
            )
        return (
            DimensionAnalysis(
                dimension=analysis.dimension,
                summary=analysis.summary,
                claims=kept,
                comparison_matrix=analysis.comparison_matrix,
                confidence=analysis.confidence,
            ),
            dropped,
        )

    # ----- 置信度 / 状态 / critique -----

    @staticmethod
    def _profile_coverage(profiles: dict[str, CompetitorProfile]) -> float:
        """关键字段填充率的均值。

        关键字段：positioning / features / pricing.plans / user_feedback / industry_extension。
        """
        if not profiles:
            return 0.0
        scores: list[float] = []
        for p in profiles.values():
            filled = 0
            total = 5
            if p.basic_info.positioning:
                filled += 1
            if p.features.core_features or p.features.ai_capabilities:
                filled += 1
            if p.pricing.plans:
                filled += 1
            uf = p.user_feedback
            if uf.overall_rating is not None or uf.positive_themes or uf.user_pain_points:
                filled += 1
            if p.industry_extension is not None:
                filled += 1
            scores.append(filled / total)
        return sum(scores) / len(scores)

    def _overall_confidence(
        self,
        per_dim: dict[AnalysisDimension, DimensionAnalysis],
        dropped: int,
        coverage: float,
    ) -> float:
        if per_dim:
            base = sum(d.confidence for d in per_dim.values()) / len(per_dim)
        else:
            base = 0.0
        score = base
        score -= self.PENALTY_DROPPED_CLAIM * dropped
        if coverage < self.LOW_COVERAGE_THRESHOLD:
            score -= self.PENALTY_LOW_COVERAGE
        empty_dims = sum(1 for d in per_dim.values() if not d.claims)
        score -= self.PENALTY_EMPTY_DIM * empty_dims
        if not per_dim:
            score = 0.0
        return max(0.0, min(1.0, score))

    @staticmethod
    def _derive_status(
        per_dim: dict[AnalysisDimension, DimensionAnalysis],
        confidence: float,
        missing_profiles: list[str],
    ) -> AgentStatus:
        if not per_dim:
            return AgentStatus.FAILED
        empty = sum(1 for d in per_dim.values() if not d.claims)
        if empty == len(per_dim):
            return AgentStatus.FAILED
        if confidence < 0.6:
            return AgentStatus.NEEDS_REWORK
        if empty > 0 or missing_profiles:
            return AgentStatus.PARTIAL
        return AgentStatus.SUCCESS

    @staticmethod
    def _build_critique(
        per_dim: dict[AnalysisDimension, DimensionAnalysis],
        dropped: int,
        errors: list[AgentError],
        coverage: float,
    ) -> str:
        lines: list[str] = []
        if dropped:
            lines.append(f"过滤了 {dropped} 条 evidence 校验未通过的 claim（疑似幻觉）")
        if coverage < 0.5:
            lines.append(f"输入 profile 字段填充率 {coverage:.0%}，对比信号较弱")
        empty = [d.dimension.value for d in per_dim.values() if not d.claims]
        if empty:
            lines.append(f"未能产出带 evidence 的 claim 的维度：{', '.join(empty)}")
        codes = sorted({e.code for e in errors if e.severity in ("warn", "error")})
        if codes:
            lines.append(f"过程告警：{', '.join(codes)}")
        if not lines:
            total_claims = sum(len(d.claims) for d in per_dim.values())
            return (
                f"覆盖 {len(per_dim)} 个维度，共产出 {total_claims} 条有 evidence 支撑的 claim。"
            )
        return " | ".join(lines)


# ---------- prompt 解析辅助（与 Collector 同款） ----------


def _split_prompt(prompt: str) -> tuple[str, str]:
    sys_marker = "## System"
    usr_marker = "## User"
    si = prompt.find(sys_marker)
    ui = prompt.find(usr_marker)
    if si < 0 or ui < 0 or ui < si:
        return prompt.strip(), ""
    system = prompt[si + len(sys_marker) : ui].strip()
    user = prompt[ui + len(usr_marker) :].strip()
    return system, user


def _render(template: str, **vars: Any) -> str:
    import re

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        value = vars.get(expr)
        return "" if value is None else str(value)

    return re.sub(r"{{\s*(.+?)\s*}}", repl, template)


def _coerce_pydantic(resp: Any, model: type[BaseModel]) -> Any:
    if isinstance(resp, model):
        return resp
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, model):
        return parsed
    if isinstance(parsed, dict):
        return model.model_validate(parsed)
    if isinstance(resp, dict):
        return model.model_validate(resp)
    if hasattr(resp, "model_dump"):
        return model.model_validate(resp.model_dump())
    raise ValueError(
        f"cannot coerce LLM response to {model.__name__}: {type(resp).__name__}"
    )


def _compact_profiles(profiles: dict[str, CompetitorProfile]) -> str:
    """生成 prompt 用的压缩 profile JSON 字符串。

    去掉大字段，只保留对比分析需要的核心字段 + 所有 evidence_ids。
    """
    import json

    compact: dict[str, Any] = {}
    for name, p in profiles.items():
        compact[name] = {
            "positioning": p.basic_info.positioning,
            "target_users": [seg.name for seg in p.basic_info.target_users],
            "pricing_model": p.pricing.pricing_model.value,
            "pricing_plans": [
                {
                    "name": plan.name,
                    "price_per_seat_monthly_usd": plan.price_per_seat_monthly_usd,
                    "target_segment": plan.target_segment,
                }
                for plan in p.pricing.plans
            ],
            "core_features": [f.name for f in p.features.core_features],
            "ai_capabilities": [f.name for f in p.features.ai_capabilities],
            "industry_extension": (
                {
                    field: {
                        "has_capability": ms.has_capability,
                        "maturity_level": ms.maturity_level,
                    }
                    for field in (
                        "task_management",
                        "kanban_view",
                        "calendar_view",
                        "gantt_view",
                        "document_collaboration",
                        "workflow_automation",
                        "knowledge_base",
                        "team_permission",
                        "third_party_integration",
                        "mobile_support",
                        "realtime_editing",
                        "ai_assistance",
                    )
                    if (ms := getattr(p.industry_extension, field, None)) is not None
                }
                if p.industry_extension is not None
                else None
            ),
            "evidence_ids": sorted(collect_profile_evidence_ids(p)),
        }
    return json.dumps(compact, ensure_ascii=False, indent=2)


__all__ = ["Analyst"]
