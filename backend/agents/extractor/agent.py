"""Extractor Agent — 非结构化 → 结构化抽取。

职责：把 ``RawSourceDoc[]`` 转成符合 Schema 的 ``CompetitorProfile``，并把每个支撑
事实切分成 ``Evidence[]`` 入库（v1 阶段直接挂在 output 里，存储层在 I 窗口集成时
统一落库）。

实现链：
    raw_sources
        → TextChunker（保留字符偏移）
        → 每个 source 单独跑 LLM（per-source extraction）→ list[RawClaim]
        → EvidenceLinker：把 source_quote 反向定位回 raw_text
            ├── 命中 → 生成 Evidence + evidence_id
            └── 未命中 → 记 unmatched_quotes，相应字段 field_status=unverified
        → 跨 source 聚合 + 冲突检测
        → industry 扩展 LLM 调用（v1 仅 collaboration_saas）
        → 装配 CompetitorProfile + field_confidence / field_status / evidence_refs
        → 自评估（按 docs/AGENTS.md § 4.6 触发条件）

Mock 模式：直接从 ``fixtures/mock_data/competitor_profiles/<product>.json`` 还原
profile，绕过 LLM；evidences 从 ``evidence_db.jsonl`` 取该产品的全部条目。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agents._base import (
    AgentRunError,
    BaseAgent,
    LLMProviderProtocol,
    ToolRegistryProtocol,
    TracerProtocol,
)
from backend.schemas import (
    SCHEMA_VERSION,
    AgentError,
    AgentStatus,
    CollaborationSaasExtension,
    CompetitorProfile,
    Evidence,
    ExtractorInput,
    ExtractorOutput,
    Feature,
    FeatureProfile,
    FeedbackTheme,
    FieldStatus,
    FreeTrialInfo,
    Integration,
    MaturityScore,
    PlanAvailability,
    PricingModel,
    PricingPlan,
    PricingProfile,
    ProductBasicInfo,
    RawSourceDoc,
    UserFeedbackProfile,
)

from .fixtures import (
    load_mock_evidences,
    load_mock_profile,
)
from .tools import (
    EvidenceLinker,
    LinkResult,
    TextChunker,
    coerce_pydantic,
    content_hash_for,
    evidence_id_for,
    render,
    split_prompt,
)

PROMPT_DIR = Path(__file__).parent / "prompts"


# ---------- 内部 Pydantic：LLM 结构化输出 schema ----------


class _RawClaim(BaseModel):
    """LLM 单条原始抽取。字段路径 + 值 + source_quote + confidence。"""

    model_config = ConfigDict(extra="ignore")

    field_path: str = Field(description="dotted path, e.g. 'basic_info.positioning'")
    value: Any = Field(description="str / int / float / bool / dict / list of primitives")
    source_quote: str = ""
    confidence: float = Field(default=0.8, ge=0, le=1)


class _SourceExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claims: list[_RawClaim] = Field(default_factory=list)


class _MaturityClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimension: str
    has_capability: bool
    maturity_level: str = Field(description="none|basic|standard|advanced|best_in_class")
    notes: str = ""
    source_quote: str = ""


class _CollabSaasMaturityClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claims: list[_MaturityClaim] = Field(default_factory=list)


# ---------- 装配辅助 ----------


_PRICING_MODEL_VALUES = {m.value for m in PricingModel}
_MATURITY_LEVELS = {"none", "basic", "standard", "advanced", "best_in_class"}
_COLLAB_SAAS_DIMENSIONS = {
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
}


def _trim(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_pricing_model(raw: str | None) -> PricingModel:
    if not raw:
        return PricingModel.SUBSCRIPTION
    norm = raw.strip().lower().replace("-", "_")
    if norm in _PRICING_MODEL_VALUES:
        return PricingModel(norm)
    # 容错：常见同义词
    aliases = {"saas": "subscription", "subscriptions": "subscription"}
    return PricingModel(aliases.get(norm, "subscription"))


def _safe_pricing_plan(value: dict[str, Any]) -> PricingPlan | None:
    if not isinstance(value, dict) or not value.get("name"):
        return None
    try:
        return PricingPlan(
            name=str(value["name"]),
            price_per_seat_monthly_usd=_coerce_optional_float(
                value.get("price_per_seat_monthly_usd")
            ),
            price_per_seat_annual_usd=_coerce_optional_float(
                value.get("price_per_seat_annual_usd")
            ),
            min_seats=_coerce_optional_int(value.get("min_seats")),
            max_seats=_coerce_optional_int(value.get("max_seats")),
            target_segment=value.get("target_segment"),
            included_features=_coerce_str_list(value.get("included_features")),
            limits={str(k): str(v) for k, v in (value.get("limits") or {}).items()},
        )
    except ValidationError:
        return None


def _safe_feature(value: Any) -> Feature | None:
    if isinstance(value, str):
        return Feature(name=value)
    if not isinstance(value, dict) or not value.get("name"):
        return None
    availability_raw = value.get("availability") or {}
    if isinstance(availability_raw, dict):
        availability = PlanAvailability(
            free=bool(availability_raw.get("free", False)),
            paid=bool(availability_raw.get("paid", False)),
            enterprise_only=bool(availability_raw.get("enterprise_only", False)),
            plan_names=_coerce_str_list(availability_raw.get("plan_names")),
        )
    else:
        availability = PlanAvailability()
    try:
        return Feature(
            name=str(value["name"]),
            description=value.get("description"),
            availability=availability,
            tags=_coerce_str_list(value.get("tags")),
        )
    except ValidationError:
        return None


def _safe_integration(value: Any) -> Integration | None:
    if not isinstance(value, dict) or not value.get("target"):
        return None
    raw_type = (value.get("type") or "api").lower()
    if raw_type not in {"native", "marketplace", "api", "webhook"}:
        raw_type = "api"
    try:
        return Integration(target=str(value["target"]), type=raw_type, notes=value.get("notes"))  # type: ignore[arg-type]
    except ValidationError:
        return None


def _safe_feedback_theme(
    value: Any, *, default_sentiment: str
) -> FeedbackTheme | None:
    if isinstance(value, str):
        return FeedbackTheme(theme=value, sentiment=default_sentiment)  # type: ignore[arg-type]
    if not isinstance(value, dict) or not value.get("theme"):
        return None
    sentiment = (value.get("sentiment") or default_sentiment).lower()
    if sentiment not in {"positive", "negative", "mixed"}:
        sentiment = default_sentiment
    try:
        return FeedbackTheme(
            theme=str(value["theme"]),
            mention_count=_coerce_optional_int(value.get("mention_count")),
            sentiment=sentiment,  # type: ignore[arg-type]
            sample_quotes=_coerce_str_list(value.get("sample_quotes")),
        )
    except ValidationError:
        return None


def _coerce_optional_float(v: Any) -> float | None:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_optional_int(v: Any) -> int | None:
    if v in (None, "", "null"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _coerce_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


# ---------- Extractor ----------


class Extractor(BaseAgent[ExtractorInput, ExtractorOutput]):
    """抽取 Agent。详见 docs/AGENTS.md § 4。"""

    name: ClassVar[str] = "extractor"
    version: ClassVar[str] = "1.1.0"
    input_model: ClassVar[type[BaseModel]] = ExtractorInput
    output_model: ClassVar[type[BaseModel]] = ExtractorOutput
    required_tools: ClassVar[list[str]] = []  # v1 不依赖外部工具注册表

    # 调参
    DEFAULT_MAX_CLAIMS_PER_SOURCE: ClassVar[int] = 20
    UNVERIFIED_FIELD_THRESHOLD: ClassVar[float] = 0.30  # >30% 字段未匹配 → 低 confidence
    MISSING_FIELD_THRESHOLD: ClassVar[float] = 0.20  # >20% 必填字段缺失 → 低 confidence
    BASE_CONFIDENCE: ClassVar[float] = 0.92
    PENALTY_UNVERIFIED: ClassVar[float] = 0.25
    PENALTY_CONFLICTING: ClassVar[float] = 0.15
    PENALTY_MISSING_REQUIRED: ClassVar[float] = 0.20
    PENALTY_NO_INDUSTRY: ClassVar[float] = 0.05

    # 必填字段（缺失会触发自评估降权）
    REQUIRED_SCALAR_PATHS: ClassVar[tuple[str, ...]] = (
        "basic_info.name",
        "basic_info.positioning",
        "pricing.pricing_model",
    )

    # consolidation 触发字段（与 QA schema_completeness 评分项口径对齐）：
    # 标量缺失或列表为空 → 用全量正文 + 专用 prompt 再补一刀。
    CONSOLIDATION_SCALAR_PATHS: ClassVar[tuple[str, ...]] = (
        "basic_info.positioning",
        "pricing.pricing_model",
    )
    CONSOLIDATION_LIST_PATHS: ClassVar[tuple[str, ...]] = (
        "basic_info.target_users[]",
        "features.core_features[]",
        "pricing.plans[]",
    )
    CONSOLIDATION_MIN_CLAIM_CONFIDENCE: ClassVar[float] = 0.5
    CONSOLIDATION_MAX_CLAIMS: ClassVar[int] = 30
    # consolidation 输入的全文上限，超过会截断（避免 token 爆炸）
    CONSOLIDATION_TEXT_BUDGET_CHARS: ClassVar[int] = 12_000

    # collab_saas 的"无证据"占位 MaturityScore：QA schema_completeness 把 None 视为缺失，
    # 改成显式 has_capability=False + maturity_level=none 才能算"已填"。
    COLLAB_SAAS_NO_EVIDENCE_NOTE: ClassVar[str] = (
        "无明确证据；本次采集来源未涵盖此能力"
    )

    def __init__(
        self,
        *,
        llm: LLMProviderProtocol | None = None,
        tools: ToolRegistryProtocol | None = None,
        tracer: TracerProtocol | None = None,
        mock: bool = False,
        max_claims_per_source: int | None = None,
    ) -> None:
        super().__init__(llm=llm, tools=tools, tracer=tracer, mock=mock)
        self.chunker = TextChunker()
        self.linker = EvidenceLinker()
        self.max_claims_per_source = (
            max_claims_per_source or self.DEFAULT_MAX_CLAIMS_PER_SOURCE
        )

    # ----- Mock -----

    def _run_mock(self, inp: ExtractorInput) -> ExtractorOutput:
        profile = load_mock_profile(inp.product_name)
        evidences = load_mock_evidences(inp.product_name)
        errors: list[AgentError] = []
        if profile is None:
            errors.append(
                AgentError(
                    code="UPSTREAM_MISSING",
                    message=(
                        f"no mock profile fixture for product={inp.product_name!r}; "
                        "expected fixtures/mock_data/competitor_profiles/<slug>.json"
                    ),
                    severity="error",
                    retriable=False,
                )
            )
            return self._failure(inp, errors=errors)

        # Mock fixture 的 schema_version 落后于当前 SCHEMA_VERSION 时，只要 Pydantic
        # 已经反序列化成功（说明字段是向后兼容的），就把 profile + output 重新打到
        # 当前版本，避免 _post_validate 因版本号字符串不一致而把 status 改成 NEEDS_REWORK。
        # 仍 emit 一条 warn，提醒架构窗口刷 fixture。
        if profile.schema_version != SCHEMA_VERSION:
            errors.append(
                AgentError(
                    code="LLM_SCHEMA_INVALID",
                    message=(
                        f"mock profile schema_version={profile.schema_version} != "
                        f"current SCHEMA_VERSION={SCHEMA_VERSION}; auto-upgrading "
                        "for this run, please regenerate fixture"
                    ),
                    severity="warn",
                    retriable=False,
                )
            )
            profile = profile.model_copy(update={"schema_version": SCHEMA_VERSION})

        confidence = max(profile.field_confidence.values(), default=0.9)
        # mock profile 已经预填了 field_confidence；整体 confidence 取均值更稳
        if profile.field_confidence:
            confidence = sum(profile.field_confidence.values()) / len(
                profile.field_confidence
            )

        status = AgentStatus.SUCCESS
        if confidence < self.SELF_CRITIQUE_THRESHOLD:
            status = AgentStatus.NEEDS_REWORK
        critique = (
            f"Mock 数据加载: {inp.product_name} (fixture)。"
            f"field_confidence 均值={confidence:.2f}, evidences={len(evidences)}."
        )
        return ExtractorOutput(
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
            duration_ms=0,  # BaseAgent 回填
            errors=errors,
            profile=profile,
            evidences=evidences,
            field_confidence=profile.field_confidence,
            schema_version=SCHEMA_VERSION,
            unmatched_quotes=[],
        )

    # ----- Real -----

    def _run(self, inp: ExtractorInput) -> ExtractorOutput:
        if self.llm is None:
            raise AgentRunError(
                code="UPSTREAM_MISSING",
                message="extractor requires an LLM provider in non-mock mode",
                retriable=False,
            )
        if not inp.raw_sources:
            raise AgentRunError(
                code="UPSTREAM_MISSING",
                message="raw_sources is empty; collector must produce at least one source",
                retriable=False,
            )

        errors: list[AgentError] = []
        tokens_input = 0
        tokens_output = 0

        # 1. 逐 source 抽取 RawClaim
        all_claims: list[tuple[_RawClaim, RawSourceDoc]] = []
        for source in inp.raw_sources:
            try:
                extraction, t_in, t_out = self._extract_from_source(
                    source=source,
                    inp=inp,
                )
                tokens_input += t_in
                tokens_output += t_out
            except AgentRunError:
                raise
            except Exception as e:  # noqa: BLE001
                errors.append(
                    AgentError(
                        code="LLM_SCHEMA_INVALID",
                        message=(
                            f"extraction failed for source={source.source_id} "
                            f"({type(e).__name__}: {e})"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )
                continue
            for claim in extraction.claims:
                all_claims.append((claim, source))

        # 1.5 Consolidation pass：必填字段（含关键列表）缺失时，再跑一次专用 prompt
        #     用所有 raw_text 拼起来补缺。confidence < 阈值的 claim 直接丢，避免瞎填。
        missing_consolidation = self._missing_for_consolidation(all_claims)
        if missing_consolidation:
            try:
                cons_claims, t_in, t_out = self._consolidation_pass(
                    inp=inp, missing_paths=missing_consolidation
                )
                tokens_input += t_in
                tokens_output += t_out
                if cons_claims:
                    # consolidation 的 source 没有单一归属，挂在 raw_sources[0] 上仅用于
                    # 元组占位（EvidenceLinker 仍按 source_quote 反查命中哪一篇）。
                    placeholder = inp.raw_sources[0]
                    for cc in cons_claims:
                        all_claims.append((cc, placeholder))
            except Exception as e:  # noqa: BLE001
                errors.append(
                    AgentError(
                        code="LLM_SCHEMA_INVALID",
                        message=(
                            f"consolidation pass failed ({type(e).__name__}: {e}); "
                            f"missing paths remain: {missing_consolidation}"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )

        # 2. 证据绑定 + 装配 profile（evidence_refs 已在子模型内部填好）
        (
            profile,
            evidences,
            field_confidence,
            field_status,
            unmatched,
            binding_errors,
        ) = self._bind_and_assemble(
            inp=inp,
            claims=all_claims,
        )
        errors.extend(binding_errors)

        # 3. industry 扩展（v1 仅 collaboration_saas）
        if inp.industry_schema_id.startswith("collaboration_saas"):
            try:
                ext, ext_evs, ext_unmatched, t_in, t_out = self._extract_collab_saas(
                    inp=inp,
                )
                tokens_input += t_in
                tokens_output += t_out
                if ext is not None:
                    profile.industry_extension = ext
                    evidences.extend(ext_evs)
                    unmatched.extend(ext_unmatched)
            except Exception as e:  # noqa: BLE001
                errors.append(
                    AgentError(
                        code="LLM_SCHEMA_INVALID",
                        message=(
                            f"collab_saas extension extraction failed: "
                            f"{type(e).__name__}: {e}"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )
        else:
            errors.append(
                AgentError(
                    code="DIMENSION_NOT_APPLICABLE",
                    message=(
                        f"industry_schema_id={inp.industry_schema_id!r} not supported "
                        "in v1; only collaboration_saas_v1 has an extension extractor"
                    ),
                    severity="warn",
                    retriable=False,
                )
            )

        profile.field_confidence = field_confidence
        profile.field_status = field_status
        # evidence_refs 是 ProductBasicInfo / FeatureProfile / PricingProfile / UserFeedbackProfile
        # 各自的 dict，前面 _bind_and_assemble 已经写入了。

        # 4. 必填字段缺失 / unmatched 比例 → 错误码
        missing_required = [
            p for p in self.REQUIRED_SCALAR_PATHS if p not in field_status
        ]
        for p in missing_required:
            errors.append(
                AgentError(
                    code="SCHEMA_FIELD_MISSING",
                    message=f"required field {p} not extracted from any source",
                    severity="warn",
                    retriable=True,
                )
            )
        if unmatched:
            errors.append(
                AgentError(
                    code="EVIDENCE_UNMATCHED",
                    message=f"{len(unmatched)} source_quote(s) failed to bind to raw_text",
                    severity="warn",
                    retriable=True,
                )
            )
        conflicting = [p for p, s in field_status.items() if s is FieldStatus.CONFLICTING]
        if conflicting:
            errors.append(
                AgentError(
                    code="CONFLICTING_FACTS",
                    message=f"conflicting values across sources for: {', '.join(conflicting)}",
                    severity="warn",
                    retriable=False,
                )
            )

        # 5. 总体 confidence + status
        confidence = self._compute_confidence(
            field_status=field_status,
            unmatched=unmatched,
            missing_required=missing_required,
            has_industry=profile.industry_extension is not None,
            industry_required=inp.industry_schema_id.startswith("collaboration_saas"),
        )
        critique = self._build_self_critique(
            field_status=field_status,
            unmatched=unmatched,
            missing_required=missing_required,
            errors=errors,
        )

        if not all_claims:
            status = AgentStatus.FAILED
        elif missing_required or conflicting or confidence < self.SELF_CRITIQUE_THRESHOLD:
            status = AgentStatus.NEEDS_REWORK
        elif unmatched:
            status = AgentStatus.PARTIAL
        else:
            status = AgentStatus.SUCCESS

        return ExtractorOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=status,
            confidence=confidence,
            self_critique=critique,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_usd=0.0,
            duration_ms=0,
            errors=errors,
            profile=profile,
            evidences=evidences,
            field_confidence=field_confidence,
            schema_version=SCHEMA_VERSION,
            unmatched_quotes=unmatched,
        )

    # ----- 业务级后置校验 -----

    def _post_validate(self, out: ExtractorOutput, inp: ExtractorInput) -> None:
        # 失败兜底路径下 profile=None，此时跳过业务级校验，由 errors 表征失败
        if out.status is AgentStatus.FAILED or out.profile is None:
            return
        # 强约束 1：profile.basic_info.name 必须是输入的 product_name
        if out.profile.basic_info.name != inp.product_name:
            raise AgentRunError(
                code="OUTPUT_TYPE_MISMATCH",
                message=(
                    f"profile.basic_info.name={out.profile.basic_info.name!r} != "
                    f"input product_name={inp.product_name!r}"
                ),
                retriable=False,
            )
        # 强约束 2：schema_version 与当前一致
        if out.schema_version != SCHEMA_VERSION:
            raise AgentRunError(
                code="OUTPUT_TYPE_MISMATCH",
                message=(
                    f"output schema_version={out.schema_version} != "
                    f"current {SCHEMA_VERSION}"
                ),
                retriable=False,
            )
        # 强约束 3：每条 evidence 都属于输入的 raw_sources 之一
        source_ids = {s.source_id for s in inp.raw_sources}
        rogue = [e.evidence_id for e in out.evidences if e.source_id not in source_ids]
        if rogue:
            raise AgentRunError(
                code="OUTPUT_TYPE_MISMATCH",
                message=(
                    f"evidences {rogue[:3]} reference unknown source_ids; "
                    "every evidence must be bound to an input raw_source"
                ),
                retriable=False,
            )

    # ----- LLM 单 source 抽取 -----

    def _extract_from_source(
        self,
        *,
        source: RawSourceDoc,
        inp: ExtractorInput,
    ) -> tuple[_SourceExtraction, int, int]:
        prompt = (PROMPT_DIR / "extract_source.md").read_text(encoding="utf-8")
        system, user_template = split_prompt(prompt)
        # 长文本 chunk → 取拼起来不超过预算的内容（v1 直接合一篇，由 LLM 自己决定）
        chunks = self.chunker.chunk(source)
        if not chunks:
            return _SourceExtraction(claims=[]), 0, 0
        page_text = "\n\n".join(c.text for c in chunks[:6])  # 上限保护
        user = render(
            user_template,
            product_name=inp.product_name,
            industry=inp.industry_schema_id,
            dimension=source.dimension.value,
            source_id=source.source_id,
            source_url=str(source.source_url),
            title=source.title or "",
            page_text=page_text,
            qa_feedback="" if not inp.qa_feedback else str(inp.qa_feedback),
            max_claims=self.max_claims_per_source,
        )
        assert self.llm is not None  # _run 已保证
        resp = self.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_SourceExtraction,
            temperature=0.0,
            max_tokens=2000,
        )
        parsed = coerce_pydantic(resp, _SourceExtraction)
        t_in = getattr(resp, "tokens_input", 0) or 0
        t_out = getattr(resp, "tokens_output", 0) or 0
        return parsed, t_in, t_out

    # ----- LLM consolidation pass -----

    def _missing_for_consolidation(
        self,
        all_claims: list[tuple[_RawClaim, RawSourceDoc]],
    ) -> list[str]:
        """根据 per-source 阶段已经抽出的 claims，挑出仍未覆盖的关键字段。

        判定规则：
        - 标量路径：bucket 里没有对应 field_path 的 claim
        - 列表路径（``foo[]``）：bucket 里该路径的有效 claim 数为 0
        """
        present_paths: set[str] = {c.field_path for c, _ in all_claims}
        missing: list[str] = []
        for p in self.CONSOLIDATION_SCALAR_PATHS:
            if p not in present_paths:
                missing.append(p)
        for p in self.CONSOLIDATION_LIST_PATHS:
            # 列表路径只要出现过一条就算"有"，让后续装配自然合并
            if p not in present_paths:
                missing.append(p)
        return missing

    def _consolidation_pass(
        self,
        *,
        inp: ExtractorInput,
        missing_paths: list[str],
    ) -> tuple[list[_RawClaim], int, int]:
        """把所有 raw_text 合并跑一次 LLM，专门补 missing_paths。

        返回经过 confidence 阈值过滤的 claims；调用方负责合并到 all_claims。
        """
        prompt = (PROMPT_DIR / "extract_consolidation.md").read_text(encoding="utf-8")
        system, user_template = split_prompt(prompt)

        # 拼全文：按 source 加分隔符，整体截到 budget
        parts: list[str] = []
        for src in inp.raw_sources:
            text = (src.raw_text or "").strip()
            if not text:
                continue
            header = (
                f"--- source_id={src.source_id} dimension={src.dimension.value} "
                f"url={src.source_url} ---"
            )
            parts.append(f"{header}\n{text}")
        all_text = "\n\n".join(parts)
        if len(all_text) > self.CONSOLIDATION_TEXT_BUDGET_CHARS:
            all_text = all_text[: self.CONSOLIDATION_TEXT_BUDGET_CHARS] + "\n...[truncated]"

        user = render(
            user_template,
            product_name=inp.product_name,
            industry=inp.industry_schema_id,
            qa_feedback="" if not inp.qa_feedback else str(inp.qa_feedback),
            missing_paths=missing_paths,
            all_text=all_text,
            max_claims=self.CONSOLIDATION_MAX_CLAIMS,
        )
        assert self.llm is not None
        resp = self.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_SourceExtraction,
            temperature=0.0,
            max_tokens=2500,
        )
        try:
            parsed: _SourceExtraction = coerce_pydantic(resp, _SourceExtraction)
        except Exception:
            # ScriptedLLM 没有对应回复 / LLM 返回空 → 视为"没补出来"，不阻塞主流程
            return [], 0, 0
        t_in = getattr(resp, "tokens_input", 0) or 0
        t_out = getattr(resp, "tokens_output", 0) or 0
        # 过滤：confidence < 阈值 / 没有 source_quote → 丢弃
        # 同时只接受落在 missing_paths 范围内的字段，避免 LLM 顺便瞎填别的
        accepted: list[_RawClaim] = []
        missing_set = set(missing_paths)
        for c in parsed.claims:
            if c.confidence < self.CONSOLIDATION_MIN_CLAIM_CONFIDENCE:
                continue
            if not c.source_quote.strip():
                continue
            if c.field_path not in missing_set:
                continue
            accepted.append(c)
        return accepted, t_in, t_out

    # ----- LLM industry 扩展 -----

    def _extract_collab_saas(
        self,
        *,
        inp: ExtractorInput,
    ) -> tuple[CollaborationSaasExtension | None, list[Evidence], list[str], int, int]:
        prompt = (PROMPT_DIR / "extract_industry_collab_saas.md").read_text(
            encoding="utf-8"
        )
        system, user_template = split_prompt(prompt)
        # 拼一组带 source_id 的 chunk（取每个 source 头部）
        bundle: list[dict[str, Any]] = []
        for src in inp.raw_sources:
            chunks = self.chunker.chunk(src)
            head = " ".join(c.text for c in chunks[:2])
            bundle.append(
                {
                    "source_id": src.source_id,
                    "source_url": str(src.source_url),
                    "dimension": src.dimension.value,
                    "text": head[: self.chunker.max_chars],
                }
            )
        user = render(
            user_template,
            product_name=inp.product_name,
            qa_feedback="" if not inp.qa_feedback else str(inp.qa_feedback),
            chunks=bundle,
        )
        assert self.llm is not None
        resp = self.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_CollabSaasMaturityClaims,
            temperature=0.0,
            max_tokens=1500,
        )
        parsed: _CollabSaasMaturityClaims = coerce_pydantic(
            resp, _CollabSaasMaturityClaims
        )
        t_in = getattr(resp, "tokens_input", 0) or 0
        t_out = getattr(resp, "tokens_output", 0) or 0

        ext_kwargs: dict[str, Any] = {}
        evidences: list[Evidence] = []
        unmatched: list[str] = []
        for claim in parsed.claims:
            dim = claim.dimension.strip().lower()
            if dim not in _COLLAB_SAAS_DIMENSIONS:
                continue
            level = (claim.maturity_level or "basic").lower()
            if level not in _MATURITY_LEVELS:
                level = "basic"
            # 绑定 evidence
            link = self.linker.link(claim.source_quote, inp.raw_sources)
            evidence_ids: list[str] = []
            if link.matched and link.source_id is not None:
                ev = self._mint_evidence(
                    inp=inp,
                    link=link,
                    quote=claim.source_quote,
                    tag=f"industry.{dim}",
                )
                evidences.append(ev)
                evidence_ids.append(ev.evidence_id)
            elif claim.source_quote.strip():
                unmatched.append(claim.source_quote.strip())
            # 无证据的占位 claim：LLM 给的是 has_capability=False + level=none + 标准 notes，
            # 这里 evidence_ids=[] 是自然结果；不要把 notes 转成 None，QA 才能区分"没填"与"明确无能力"
            ext_kwargs[dim] = MaturityScore(
                has_capability=claim.has_capability,
                maturity_level=level,  # type: ignore[arg-type]
                notes=claim.notes or None,
                evidence_ids=evidence_ids,
            )
        # 兜底：LLM 漏掉任何一个 capability → 自动塞标准占位 MaturityScore。
        # 这样 12 个字段全部非 None，QA schema_completeness 才能拿满。
        for dim in _COLLAB_SAAS_DIMENSIONS:
            if dim not in ext_kwargs:
                ext_kwargs[dim] = MaturityScore(
                    has_capability=False,
                    maturity_level="none",
                    notes=self.COLLAB_SAAS_NO_EVIDENCE_NOTE,
                    evidence_ids=[],
                )
        ext_kwargs["industry_id"] = "collaboration_saas"
        try:
            return (
                CollaborationSaasExtension(**ext_kwargs),
                evidences,
                unmatched,
                t_in,
                t_out,
            )
        except ValidationError:
            return None, evidences, unmatched, t_in, t_out

    # ----- 证据绑定 + Profile 装配 -----

    def _bind_and_assemble(
        self,
        *,
        inp: ExtractorInput,
        claims: list[tuple[_RawClaim, RawSourceDoc]],
    ) -> tuple[
        CompetitorProfile,
        list[Evidence],
        dict[str, float],
        dict[str, FieldStatus],
        list[str],
        list[AgentError],
    ]:
        errors: list[AgentError] = []
        evidences: list[Evidence] = []
        unmatched_quotes: list[str] = []

        # field_path → [(claim, source, link)]
        bucket: dict[str, list[tuple[_RawClaim, RawSourceDoc, LinkResult]]] = (
            defaultdict(list)
        )
        # 用 content_hash 去重 evidence，命中同一片文本只入一次
        evidence_by_hash: dict[str, Evidence] = {}

        for claim, source in claims:
            link = self.linker.link(claim.source_quote, inp.raw_sources)
            if not link.matched and claim.source_quote.strip():
                unmatched_quotes.append(claim.source_quote.strip())
            bucket[claim.field_path].append((claim, source, link))
            if link.matched and link.source_id is not None:
                content = (
                    link.matched_text
                    if link.matched_text and link.confidence >= 0.9
                    else claim.source_quote
                )
                content = content.strip()
                if not content:
                    continue
                h = content_hash_for(content)
                if h in evidence_by_hash:
                    continue
                ev = self._mint_evidence(
                    inp=inp,
                    link=link,
                    quote=content,
                    tag=claim.field_path,
                )
                evidence_by_hash[h] = ev
                evidences.append(ev)

        # 装配各 section
        basic_info, bi_conf, bi_status, bi_refs = self._assemble_basic_info(
            inp, bucket, evidences
        )
        features, ft_conf, ft_status, ft_refs = self._assemble_features(
            bucket, evidences
        )
        pricing, pr_conf, pr_status, pr_refs = self._assemble_pricing(
            bucket, evidences
        )
        user_feedback, uf_conf, uf_status, uf_refs = self._assemble_user_feedback(
            bucket, evidences
        )

        # 套回 evidence_refs（落在子模型内）
        basic_info = basic_info.model_copy(update={"evidence_refs": bi_refs})
        features = features.model_copy(update={"evidence_refs": ft_refs})
        pricing = pricing.model_copy(update={"evidence_refs": pr_refs})
        user_feedback = user_feedback.model_copy(update={"evidence_refs": uf_refs})

        field_confidence: dict[str, float] = {}
        field_status: dict[str, FieldStatus] = {}
        for d in (bi_conf, ft_conf, pr_conf, uf_conf):
            field_confidence.update(d)
        for d in (bi_status, ft_status, pr_status, uf_status):
            field_status.update(d)

        profile = CompetitorProfile(
            profile_id=f"profile_{inp.product_name.strip().lower().replace(' ', '_')}",
            schema_version=SCHEMA_VERSION,
            industry=inp.industry_schema_id.replace("_v1", "").replace("_v2", ""),
            basic_info=basic_info,
            features=features,
            pricing=pricing,
            user_feedback=user_feedback,
            extracted_at=datetime.now(tz=UTC),
            field_confidence=field_confidence,
            field_status=field_status,
        )
        return (
            profile,
            evidences,
            field_confidence,
            field_status,
            unmatched_quotes,
            errors,
        )

    # ----- 装配：basic_info -----

    def _assemble_basic_info(
        self,
        inp: ExtractorInput,
        bucket: dict[str, list[tuple[_RawClaim, RawSourceDoc, LinkResult]]],
        evidences: list[Evidence],
    ) -> tuple[
        ProductBasicInfo,
        dict[str, float],
        dict[str, FieldStatus],
        dict[str, list[str]],
    ]:
        ev_by_quote = self._evidence_lookup_table(evidences)
        conf: dict[str, float] = {}
        status: dict[str, FieldStatus] = {}
        refs: dict[str, list[str]] = defaultdict(list)

        def _scalar(path: str) -> Any:
            entries = bucket.get(path, [])
            if not entries:
                return None
            best = max(entries, key=lambda x: (x[0].confidence, x[2].confidence))
            self._record_field(
                path, best, conf, status, refs, ev_by_quote
            )
            # 冲突检测：不同 value 出现 → conflicting
            values = {self._stringify(c.value) for c, _, _ in entries if c.value is not None}
            if len(values) > 1:
                status[path] = FieldStatus.CONFLICTING
            return _trim(best[0].value)

        category = _scalar("basic_info.category") or inp.industry_schema_id.replace(
            "_", " "
        )
        positioning = _scalar("basic_info.positioning")
        company = _scalar("basic_info.company")
        founded_year = _coerce_optional_int(_scalar("basic_info.founded_year"))
        headquarters = _scalar("basic_info.headquarters")
        official_website = _scalar("basic_info.official_website")
        if not isinstance(official_website, str):
            official_website = None

        # 列表字段
        target_users_entries = bucket.get("basic_info.target_users[]", [])
        target_users: list[Any] = []
        for c, _src, link in target_users_entries:
            if isinstance(c.value, dict) and c.value.get("name"):
                target_users.append(c.value)
            elif isinstance(c.value, str):
                target_users.append({"name": c.value})
            if link.matched:
                refs["target_users"].extend(self._ev_ids_for(c.source_quote, ev_by_quote))
        if target_users_entries:
            self._record_section_status(
                "basic_info.target_users", target_users_entries, conf, status
            )

        main_scenarios = self._collect_str_list(bucket, "basic_info.main_scenarios[]")
        languages = self._collect_str_list(bucket, "basic_info.languages_supported[]")

        # 注意：basic_info.name 用输入的 product_name（强校验）
        try:
            basic_info = ProductBasicInfo(
                name=inp.product_name,
                company=company if isinstance(company, str) else None,
                official_website=official_website,
                category=str(category) if category else inp.industry_schema_id,
                positioning=positioning if isinstance(positioning, str) else None,
                target_users=[
                    {"name": str(t.get("name", t)) if isinstance(t, dict) else str(t)}
                    if isinstance(t, (str, dict))
                    else {"name": str(t)}
                    for t in target_users
                ],  # type: ignore[misc]
                main_scenarios=main_scenarios,
                founded_year=founded_year,
                headquarters=headquarters if isinstance(headquarters, str) else None,
                languages_supported=languages,
            )
        except ValidationError:
            basic_info = ProductBasicInfo(
                name=inp.product_name,
                category=str(category) if category else inp.industry_schema_id,
            )

        # basic_info.name 由输入注入 → 默认 verified（与 product_name 等同视为已验证）
        status.setdefault("basic_info.name", FieldStatus.VERIFIED)
        conf.setdefault("basic_info.name", 1.0)

        return basic_info, conf, status, dict(refs)

    # ----- 装配：features -----

    def _assemble_features(
        self,
        bucket: dict[str, list[tuple[_RawClaim, RawSourceDoc, LinkResult]]],
        evidences: list[Evidence],
    ) -> tuple[
        FeatureProfile, dict[str, float], dict[str, FieldStatus], dict[str, list[str]]
    ]:
        ev_by_quote = self._evidence_lookup_table(evidences)
        conf: dict[str, float] = {}
        status: dict[str, FieldStatus] = {}
        refs: dict[str, list[str]] = defaultdict(list)

        def _collect_features(path: str, ref_key: str) -> list[Feature]:
            entries = bucket.get(path, [])
            by_name: dict[str, Feature] = {}
            any_matched = False
            sum_conf = 0.0
            count = 0
            for c, _src, link in entries:
                feat = _safe_feature(c.value)
                if feat is None:
                    continue
                if feat.name in by_name:
                    # 合并 tags
                    merged_tags = list(dict.fromkeys(by_name[feat.name].tags + feat.tags))
                    by_name[feat.name] = by_name[feat.name].model_copy(
                        update={"tags": merged_tags}
                    )
                else:
                    by_name[feat.name] = feat
                sum_conf += c.confidence
                count += 1
                if link.matched:
                    any_matched = True
                    refs[ref_key].extend(self._ev_ids_for(c.source_quote, ev_by_quote))
            if entries:
                conf[path.replace("[]", "")] = sum_conf / max(count, 1)
                status[path.replace("[]", "")] = (
                    FieldStatus.VERIFIED if any_matched else FieldStatus.UNVERIFIED
                )
            return list(by_name.values())

        core = _collect_features("features.core_features[]", "core_features")
        diff = _collect_features(
            "features.differentiated_features[]", "differentiated_features"
        )
        integrations_entries = bucket.get("features.integration_capabilities[]", [])
        integrations: list[Integration] = []
        any_int = False
        for c, _src, link in integrations_entries:
            it = _safe_integration(c.value)
            if it is not None:
                integrations.append(it)
                if link.matched:
                    any_int = True
                    refs["integration_capabilities"].extend(
                        self._ev_ids_for(c.source_quote, ev_by_quote)
                    )
        if integrations_entries:
            conf["features.integration_capabilities"] = sum(
                c.confidence for c, _, _ in integrations_entries
            ) / max(len(integrations_entries), 1)
            status["features.integration_capabilities"] = (
                FieldStatus.VERIFIED if any_int else FieldStatus.UNVERIFIED
            )
        ai = _collect_features("features.ai_capabilities[]", "ai_capabilities")

        return (
            FeatureProfile(
                core_features=core,
                differentiated_features=diff,
                integration_capabilities=integrations,
                ai_capabilities=ai,
            ),
            conf,
            status,
            dict(refs),
        )

    # ----- 装配：pricing -----

    def _assemble_pricing(
        self,
        bucket: dict[str, list[tuple[_RawClaim, RawSourceDoc, LinkResult]]],
        evidences: list[Evidence],
    ) -> tuple[
        PricingProfile, dict[str, float], dict[str, FieldStatus], dict[str, list[str]]
    ]:
        ev_by_quote = self._evidence_lookup_table(evidences)
        conf: dict[str, float] = {}
        status: dict[str, FieldStatus] = {}
        refs: dict[str, list[str]] = defaultdict(list)

        # pricing_model
        model_entries = bucket.get("pricing.pricing_model", [])
        best_model = self._resolve_scalar(
            "pricing.pricing_model", model_entries, conf, status, refs, ev_by_quote
        )
        pricing_model = (
            _normalize_pricing_model(str(best_model.value))
            if best_model is not None
            else PricingModel.SUBSCRIPTION
        )

        # plans[]
        plan_entries = bucket.get("pricing.plans[]", [])
        plans_by_name: dict[str, PricingPlan] = {}
        any_matched_plan = False
        for c, _src, link in plan_entries:
            plan = _safe_pricing_plan(c.value if isinstance(c.value, dict) else {})
            if plan is None:
                continue
            existing = plans_by_name.get(plan.name)
            if existing is None:
                plans_by_name[plan.name] = plan
            else:
                # 冲突：同名 plan 不同价 → 标 conflicting，保留高 confidence
                if (
                    existing.price_per_seat_monthly_usd is not None
                    and plan.price_per_seat_monthly_usd is not None
                    and existing.price_per_seat_monthly_usd
                    != plan.price_per_seat_monthly_usd
                ):
                    status["pricing.plans"] = FieldStatus.CONFLICTING
                    if c.confidence > 0.7:
                        plans_by_name[plan.name] = plan
            if link.matched:
                any_matched_plan = True
                refs["plans"].extend(self._ev_ids_for(c.source_quote, ev_by_quote))
        if plan_entries:
            conf.setdefault(
                "pricing.plans",
                sum(c.confidence for c, _, _ in plan_entries)
                / max(len(plan_entries), 1),
            )
            status.setdefault(
                "pricing.plans",
                FieldStatus.VERIFIED if any_matched_plan else FieldStatus.UNVERIFIED,
            )

        plans = list(plans_by_name.values())

        free_trial: FreeTrialInfo | None = None
        ft_entries = bucket.get("pricing.free_trial", [])
        best_ft = self._resolve_scalar(
            "pricing.free_trial", ft_entries, conf, status, refs, ev_by_quote
        )
        if best_ft is not None:
            v = best_ft.value
            if isinstance(v, dict):
                try:
                    free_trial = FreeTrialInfo(
                        available=bool(v.get("available", False)),
                        duration_days=_coerce_optional_int(v.get("duration_days")),
                        requires_credit_card=v.get("requires_credit_card"),
                    )
                except ValidationError:
                    free_trial = None
            elif isinstance(v, bool):
                free_trial = FreeTrialInfo(available=v)

        billing_cycle = self._collect_str_list(bucket, "pricing.billing_cycle[]")
        currencies = self._collect_str_list(bucket, "pricing.currency_supported[]")
        enterprise_contact_entries = bucket.get(
            "pricing.enterprise_contact_required", []
        )
        best_ec = self._resolve_scalar(
            "pricing.enterprise_contact_required",
            enterprise_contact_entries,
            conf,
            status,
            refs,
            ev_by_quote,
        )
        enterprise_contact = bool(best_ec.value) if best_ec is not None else False

        return (
            PricingProfile(
                pricing_model=pricing_model,
                plans=plans,
                free_trial=free_trial,
                billing_cycle=billing_cycle,
                currency_supported=currencies,
                enterprise_contact_required=enterprise_contact,
            ),
            conf,
            status,
            dict(refs),
        )

    # ----- 装配：user_feedback -----

    def _assemble_user_feedback(
        self,
        bucket: dict[str, list[tuple[_RawClaim, RawSourceDoc, LinkResult]]],
        evidences: list[Evidence],
    ) -> tuple[
        UserFeedbackProfile,
        dict[str, float],
        dict[str, FieldStatus],
        dict[str, list[str]],
    ]:
        ev_by_quote = self._evidence_lookup_table(evidences)
        conf: dict[str, float] = {}
        status: dict[str, FieldStatus] = {}
        refs: dict[str, list[str]] = defaultdict(list)

        def _theme_section(path: str, sentiment: str, ref_key: str) -> list[FeedbackTheme]:
            entries = bucket.get(path, [])
            out: list[FeedbackTheme] = []
            any_matched = False
            for c, _src, link in entries:
                theme = _safe_feedback_theme(c.value, default_sentiment=sentiment)
                if theme is None:
                    continue
                ev_ids: list[str] = []
                if link.matched:
                    any_matched = True
                    ev_ids = self._ev_ids_for(c.source_quote, ev_by_quote)
                    refs[ref_key].extend(ev_ids)
                if ev_ids:
                    theme = theme.model_copy(update={"evidence_ids": ev_ids})
                out.append(theme)
            if entries:
                conf[path.replace("[]", "")] = sum(
                    c.confidence for c, _, _ in entries
                ) / max(len(entries), 1)
                status[path.replace("[]", "")] = (
                    FieldStatus.VERIFIED if any_matched else FieldStatus.UNVERIFIED
                )
            return out

        rating_entries = bucket.get("user_feedback.overall_rating", [])
        best_rating = self._resolve_scalar(
            "user_feedback.overall_rating",
            rating_entries,
            conf,
            status,
            refs,
            ev_by_quote,
        )
        overall_rating = (
            _coerce_optional_float(best_rating.value) if best_rating is not None else None
        )
        review_count_entries = bucket.get("user_feedback.review_count", [])
        best_review_count = self._resolve_scalar(
            "user_feedback.review_count",
            review_count_entries,
            conf,
            status,
            refs,
            ev_by_quote,
        )
        review_count = (
            _coerce_optional_int(best_review_count.value)
            if best_review_count is not None
            else None
        )

        review_sources = self._collect_str_list(
            bucket, "user_feedback.review_sources[]"
        )
        positive = _theme_section(
            "user_feedback.positive_themes[]", "positive", "positive_themes"
        )
        negative = _theme_section(
            "user_feedback.negative_themes[]", "negative", "negative_themes"
        )

        return (
            UserFeedbackProfile(
                overall_rating=overall_rating,
                review_count=review_count,
                review_sources=review_sources,
                positive_themes=positive,
                negative_themes=negative,
            ),
            conf,
            status,
            dict(refs),
        )

    # ----- 内部辅助 -----

    def _collect_str_list(
        self,
        bucket: dict[str, list[tuple[_RawClaim, RawSourceDoc, LinkResult]]],
        path: str,
    ) -> list[str]:
        entries = bucket.get(path, [])
        seen: list[str] = []
        seen_set: set[str] = set()
        for c, _src, _link in entries:
            for v in _coerce_str_list(c.value):
                k = v.strip().lower()
                if k and k not in seen_set:
                    seen_set.add(k)
                    seen.append(v.strip())
        return seen

    def _record_field(
        self,
        path: str,
        best: tuple[_RawClaim, RawSourceDoc, LinkResult],
        conf: dict[str, float],
        status: dict[str, FieldStatus],
        refs: dict[str, list[str]],
        ev_by_quote: dict[str, str],
    ) -> None:
        claim, _src, link = best
        conf[path] = float(claim.confidence)
        status[path] = (
            FieldStatus.VERIFIED if link.matched else FieldStatus.UNVERIFIED
        )
        if link.matched:
            ev_ids = self._ev_ids_for(claim.source_quote, ev_by_quote)
            ref_key = path.split(".")[-1]
            refs[ref_key].extend(ev_ids)

    def _resolve_scalar(
        self,
        path: str,
        entries: list[tuple[_RawClaim, RawSourceDoc, LinkResult]],
        conf: dict[str, float],
        status: dict[str, FieldStatus],
        refs: dict[str, list[str]],
        ev_by_quote: dict[str, str],
    ) -> _RawClaim | None:
        """所有 scalar 字段合并的统一入口。

        - 选 (claim.confidence, link.confidence) 最高的一条作为最终值
        - 写入 conf / status / refs
        - 跨源给出不同 value → 将 status 改成 CONFLICTING（值依然保留 best，
          决不丢空，回扣 schema_completeness）
        - 返回 best 的 _RawClaim，调用方负责类型转换 + 写到 PricingProfile 等模型上
        """
        if not entries:
            return None
        best = max(entries, key=lambda x: (x[0].confidence, x[2].confidence))
        self._record_field(path, best, conf, status, refs, ev_by_quote)
        values = {
            self._stringify(c.value) for c, _, _ in entries if c.value is not None
        }
        if len(values) > 1:
            status[path] = FieldStatus.CONFLICTING
        return best[0]

    @staticmethod
    def _record_section_status(
        path: str,
        entries: list[tuple[_RawClaim, RawSourceDoc, LinkResult]],
        conf: dict[str, float],
        status: dict[str, FieldStatus],
    ) -> None:
        avg_conf = sum(c.confidence for c, _, _ in entries) / max(len(entries), 1)
        any_matched = any(link.matched for _, _, link in entries)
        conf[path] = avg_conf
        status[path] = FieldStatus.VERIFIED if any_matched else FieldStatus.UNVERIFIED

    def _ev_ids_for(self, quote: str, ev_by_quote: dict[str, str]) -> list[str]:
        ev_id = ev_by_quote.get(content_hash_for(quote.strip()))
        return [ev_id] if ev_id else []

    @staticmethod
    def _evidence_lookup_table(evidences: list[Evidence]) -> dict[str, str]:
        return {ev.content_hash: ev.evidence_id for ev in evidences}

    @staticmethod
    def _stringify(v: Any) -> str:
        if isinstance(v, (str, int, float, bool)):
            return str(v).strip().lower()
        if v is None:
            return ""
        return repr(v)

    def _mint_evidence(
        self,
        *,
        inp: ExtractorInput,
        link: LinkResult,
        quote: str,
        tag: str,
    ) -> Evidence:
        # 找到原始 source 以填 url / authority / language / collected_at
        src = next(
            (s for s in inp.raw_sources if s.source_id == link.source_id),
            inp.raw_sources[0],
        )
        content = quote.strip()
        return Evidence(
            evidence_id=evidence_id_for(content, src.source_id, salt=tag),
            source_id=src.source_id,
            product_name=inp.product_name,
            source_url=src.source_url,
            source_type=src.dimension.value,
            source_authority=src.source_authority,
            content=content,
            content_hash=content_hash_for(content),
            context_before=None,
            context_after=None,
            location=link.location,
            language=src.language,
            collected_at=src.collected_at,
            extracted_at=datetime.now(tz=UTC),
            confidence=float(link.confidence),
            tags=[tag] if tag else [],
        )

    # ----- 失败兜底 -----

    def _failure(self, inp: ExtractorInput, *, errors: list[AgentError]) -> ExtractorOutput:
        # mock 失败时构造一个最小可序列化输出（避开 profile 必填校验：用 model_construct）
        return ExtractorOutput.model_construct(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=AgentStatus.FAILED,
            confidence=0.0,
            self_critique="; ".join(e.message for e in errors)[:1000],
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
            duration_ms=0,
            errors=errors,
            profile=None,  # type: ignore[arg-type]
            evidences=[],
            field_confidence={},
            schema_version=SCHEMA_VERSION,
            unmatched_quotes=[],
        )

    # ----- confidence / critique -----

    def _compute_confidence(
        self,
        *,
        field_status: dict[str, FieldStatus],
        unmatched: list[str],
        missing_required: list[str],
        has_industry: bool,
        industry_required: bool,
    ) -> float:
        if not field_status:
            return 0.0
        total = len(field_status)
        unverified = sum(
            1 for s in field_status.values() if s is FieldStatus.UNVERIFIED
        )
        conflicting = sum(
            1 for s in field_status.values() if s is FieldStatus.CONFLICTING
        )
        score = self.BASE_CONFIDENCE
        unverified_ratio = unverified / total
        if unverified_ratio > self.UNVERIFIED_FIELD_THRESHOLD:
            score -= self.PENALTY_UNVERIFIED * (
                unverified_ratio / self.UNVERIFIED_FIELD_THRESHOLD
            )
        if conflicting:
            score -= self.PENALTY_CONFLICTING * min(1.0, conflicting / max(total, 1) * 5)
        if missing_required:
            score -= self.PENALTY_MISSING_REQUIRED * (
                len(missing_required) / len(self.REQUIRED_SCALAR_PATHS)
            )
        if industry_required and not has_industry:
            score -= self.PENALTY_NO_INDUSTRY
        if unmatched:
            score -= 0.02 * min(len(unmatched), 5)
        return max(0.0, min(1.0, score))

    def _build_self_critique(
        self,
        *,
        field_status: dict[str, FieldStatus],
        unmatched: list[str],
        missing_required: list[str],
        errors: list[AgentError],
    ) -> str:
        lines: list[str] = []
        if missing_required:
            lines.append(f"必填字段缺失: {', '.join(missing_required)}")
        unverified = [k for k, s in field_status.items() if s is FieldStatus.UNVERIFIED]
        if unverified:
            lines.append(
                f"未匹配 evidence 的字段({len(unverified)}): {', '.join(unverified[:5])}"
                + (" ..." if len(unverified) > 5 else "")
            )
        conflicting = [
            k for k, s in field_status.items() if s is FieldStatus.CONFLICTING
        ]
        if conflicting:
            lines.append(f"多源冲突: {', '.join(conflicting)}")
        if unmatched:
            lines.append(f"未能定位 source_quote: {len(unmatched)} 条")
        warn_codes = sorted({e.code for e in errors if e.severity in ("warn", "error")})
        if warn_codes:
            lines.append(f"过程告警: {', '.join(warn_codes)}")
        if not lines:
            return (
                f"抽取正常完成，共记录 {len(field_status)} 个字段，全部有 evidence 支撑。"
            )
        return " | ".join(lines)


__all__ = ["Extractor"]
