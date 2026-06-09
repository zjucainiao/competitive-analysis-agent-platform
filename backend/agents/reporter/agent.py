"""Reporter Agent — 模板驱动的竞品报告渲染。

职责：把 ``AnalysisResult`` 渲染为带 evidence 引用的结构化报告。

模式：
- mock：完全启发式（不调用 LLM），按模板章节遍历 AnalysisResult 维度，每条
  AnalysisClaim 直接转成一个 ReportParagraph
- real：每章节单独走 LLM，``response_format=ReportSection``；任一章节失败
  或被 _post_validate 拒绝时 fallback 到该章节的启发式版本

引用强制（核心抑制幻觉）：
- 段落 ``evidence_ids`` 必须落在 AnalysisResult 的 evidence 池内
- 段落 ``claim_ids`` 必须落在 AnalysisResult 的 claim 池内
- 非 soft_conclusion 段落 ``evidence_ids`` 不能为空 → MISSING_CITATION
- **所有数字**（不依赖 LLM 标的 ``is_quantitative`` flag，LLM 经常漏标）必须
  在引用 evidence.content 中可寻（±5% 容差，evidence 不可得时记 warn，
  不阻塞） → UNVERIFIED_QUANTITY；Reporter 同时会在段落入 ReportDraft 前
  自动校准 ``is_quantitative`` flag，方便下游 QA / Frontend 复用
- 禁用词命中 → 段落 confidence 降级 + warn（不直接拒绝）

详细契约见 docs/AGENTS.md § 6；幻觉抑制策略见 docs/HALLUCINATION_CONTROL.md。
"""

from __future__ import annotations

import contextvars
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


def _parallel_map(fn, items: list, *, max_workers: int) -> list:
    """对独立任务并行求值，保持输入顺序。

    每个任务提交前 ``copy_context()``，把 LLM trace 的 node_id/trace_id 带进
    worker 线程（否则并发产生的 LLM call 会丢节点归属）。``max_workers<=1`` 或
    单元素时退化为串行。
    """
    if len(items) <= 1 or max_workers <= 1:
        return [fn(it) for it in items]
    contexts = [contextvars.copy_context() for _ in items]
    with ThreadPoolExecutor(max_workers=min(len(items), max_workers)) as pool:
        futures = [
            pool.submit(ctx.run, fn, it) for it, ctx in zip(items, contexts)
        ]
        return [f.result() for f in futures]

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    AnalysisResult,
    DimensionAnalysis,
    Evidence,
    ReportDraft,
    ReporterInput,
    ReporterOutput,
    ReportParagraph,
    ReportSection,
)
from backend.schemas.labels import dimension_label

from .templates import (
    ReportSectionTemplate,
    ReportTemplate,
    get_template,
)
from .tools import (
    BANNED_TERMS,
    EvidenceProvider,
    FixtureEvidenceProvider,
    extract_quantities,
    find_banned_terms,
    quantity_supported,
)

PROMPT_DIR = Path(__file__).parent / "prompts"

# B1 定向改稿：从 QA issue.location 解析 section 归属。
# 形如 ``report.sections[2].paragraphs[0]`` / ``report.paragraphs[p_sw_01]``。
_SECTION_IDX_RE = re.compile(r"report\.sections\[(\d+)\]")
_PARAGRAPH_ID_RE = re.compile(r"report\.paragraphs\[([^\]]+)\]")


def _paragraph_ids_in(required_inputs: dict) -> set[str]:
    """从 ``issue.required_inputs`` 里通用地挖出所有段落 id（字符串值或字符串列表值）。

    logic_consistency 把冲突的两段记在 ``paragraph_a`` / ``paragraph_b`` /
    ``paragraph_ids`` / ``strength_paragraph`` / ``weakness_paragraph`` 等键里。
    这里不硬编码键名，收集所有字符串候选；调用方再用 ``pid_to_sec`` 过滤出真段落 id，
    故顺带收进来的非段落字符串（如 product/plan 名）会被安全忽略。
    """
    out: set[str] = set()
    for v in required_inputs.values():
        if isinstance(v, str) and v.strip():
            out.add(v.strip())
        elif isinstance(v, list):
            out.update(x.strip() for x in v if isinstance(x, str) and x.strip())
    return out


class EntailmentVerdict(BaseModel):
    """LLM-as-judge 对单段事实陈述的语义校验结果。

    Reporter 内部使用，不进 backend/schemas（不属于跨模块契约）。
    """

    model_config = ConfigDict(extra="forbid")

    entailed: bool = Field(description="段落事实陈述是否能从引用 evidence 直接推出")
    reason: str = Field(description="一句话理由（中文）")


class RepairedParagraph(BaseModel):
    """Reporter self-correct loop 中 LLM 返回的单段重写产物。

    只携带 text；其他字段（claim_ids / evidence_ids / 标记位）由 Reporter
    在 in-place 应用时维护，避免 LLM 改坏关联引用。
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="重写后段落文本（简体中文）")


@dataclass
class _RepairIssue:
    """单段需要 self-correct 的具体问题。"""

    unverified_quantities: list[tuple[str, float]] = field(default_factory=list)
    entailment_reason: str | None = None

    @property
    def is_dirty(self) -> bool:
        return bool(self.unverified_quantities) or self.entailment_reason is not None

    def describe(self) -> str:
        parts: list[str] = []
        if self.unverified_quantities:
            qts = ", ".join(f"{k}={v:g}" for k, v in self.unverified_quantities)
            parts.append(f"未在 evidence 中字面找到的数字：{qts}")
        if self.entailment_reason:
            parts.append(f"未支撑的推断：{self.entailment_reason}")
        return "; ".join(parts)


class Reporter(BaseAgent[ReporterInput, ReporterOutput]):
    """报告 Agent。详见 docs/AGENTS.md § 6。"""

    name: ClassVar[str] = "reporter"
    version: ClassVar[str] = "1.0.0"
    input_model: ClassVar[type[BaseModel]] = ReporterInput
    output_model: ClassVar[type[BaseModel]] = ReporterOutput
    required_tools: ClassVar[list[str]] = []  # evidence_provider 用构造参数注入

    # confidence 调参
    BASE_CONFIDENCE: ClassVar[float] = 0.9
    PENALTY_PER_BANNED_HIT: ClassVar[float] = 0.05
    PENALTY_PER_UNVERIFIED: ClassVar[float] = 0.1
    PENALTY_PER_FORCED_FALLBACK: ClassVar[float] = 0.08
    PENALTY_THIN_REPORT: ClassVar[float] = 0.15
    # self-correct loop
    MAX_REPAIR_ATTEMPTS: ClassVar[int] = 3
    MAX_ENTAILMENT_CHECKS: ClassVar[int] = 32
    # 并行度：章节生成 / entailment 判定 / repair 重写都是独立 LLM 调用，
    # 并行执行把 reporter 总壁钟从"调用数×串行"降到接近"最慢单调用"。
    MAX_LLM_WORKERS: ClassVar[int] = 8

    def __init__(
        self,
        *,
        llm: LLMProviderProtocol | None = None,
        tools: ToolRegistryProtocol | None = None,
        tracer: TracerProtocol | None = None,
        evidence_provider: EvidenceProvider | None = None,
        mock: bool = False,
        entailment_check: bool = True,
        self_correct: bool = True,
    ) -> None:
        """``entailment_check``：开启后会对每个事实性段落跑一次 LLM-as-judge
        语义校验，拦截"过度推断 / 跨产品幻觉"等不能从 evidence 字面推出的段落。

        ``self_correct``：开启后 Reporter 在 ReportDraft 落定前自检数字 /
        entailment hallucination，对脏段落跑最多 ``MAX_REPAIR_ATTEMPTS`` 轮
        LLM 重写；仍修不掉的段落会被强制改成 qualitative（剥掉数字 / 丢段）
        以保证发到 QA 的 draft 是干净的。关闭后退化为 R-4 行为（只 raise
        UNVERIFIED_QUANTITY/INFERENCE，由外层反馈环重做）。

        Mock 模式或 ``self.llm is None`` 时 LLM 路径自动跳过。
        """
        super().__init__(llm=llm, tools=tools, tracer=tracer, mock=mock)
        # mock 模式默认走 fixture；真实模式可不传（数字校验会退化为 warn）
        if evidence_provider is None and mock:
            evidence_provider = FixtureEvidenceProvider()
        self.evidence_provider: EvidenceProvider | None = evidence_provider
        self.entailment_check = entailment_check
        self.self_correct = self_correct

    # ----- Mock -----

    def _run_mock(self, inp: ReporterInput) -> ReporterOutput:
        return self._build_output(inp, allow_llm=False)

    # ----- Real -----

    def _run(self, inp: ReporterInput) -> ReporterOutput:
        return self._build_output(inp, allow_llm=True)

    # ----- 业务级后置校验 -----

    def _post_validate(self, out: ReporterOutput, inp: ReporterInput) -> None:
        template = get_template(inp.template_id)
        if template is None:
            # _build_output 已经把 fail output 写好（含 TEMPLATE_NOT_FOUND）。
            # 这里再抛会被 BaseAgent 转成 NEEDS_REWORK，反而盖掉 FAILED。
            return None

        valid_claims = self._claim_pool(inp.analysis)
        valid_evidence = self._evidence_pool(inp.analysis)
        ev_db = self._fetch_evidence(valid_evidence)

        for section in out.draft.sections:
            for para in section.paragraphs:
                # 1. claim_ids ⊆ pool
                bad_claims = [c for c in para.claim_ids if c not in valid_claims]
                if bad_claims:
                    raise AgentRunError(
                        code="INSUFFICIENT_EVIDENCE",
                        message=(
                            f"paragraph {para.paragraph_id} cites claim_ids outside "
                            f"analysis pool: {bad_claims}"
                        ),
                        retriable=False,
                    )
                # 2. evidence_ids ⊆ pool
                bad_ev = [e for e in para.evidence_ids if e not in valid_evidence]
                if bad_ev:
                    raise AgentRunError(
                        code="INSUFFICIENT_EVIDENCE",
                        message=(
                            f"paragraph {para.paragraph_id} cites evidence_ids outside "
                            f"analysis pool: {bad_ev}"
                        ),
                        retriable=False,
                    )
                # 3. 事实性段落必须有 evidence
                if (
                    not para.is_soft_conclusion
                    and not para.evidence_ids
                    and para.text.strip()
                ):
                    raise AgentRunError(
                        code="MISSING_CITATION",
                        message=(
                            f"paragraph {para.paragraph_id} is factual but has no "
                            f"evidence_ids"
                        ),
                        retriable=False,
                    )
                # 4. 任何段落里出现的数字都校验（不信任 LLM 的 is_quantitative 标记，
                #    它经常忘标 → hallucination 漏网）
                detected_quantities = list(extract_quantities(para.text))
                if detected_quantities and para.evidence_ids:
                    evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
                    if evs:
                        for kind, value in detected_quantities:
                            if not quantity_supported(kind, value, evs):
                                raise AgentRunError(
                                    code="UNVERIFIED_QUANTITY",
                                    message=(
                                        f"paragraph {para.paragraph_id} quantity "
                                        f"{kind}={value} not found in cited evidence "
                                        f"(possible hallucination)"
                                    ),
                                    retriable=False,
                                )
                # 5. 语义层 entailment 校验（LLM-as-judge）
                #    self_correct=True 时 entailment 已在 _run_self_correct 里
                #    处理过（修不掉的段落要么被改成 qualitative，要么被丢弃），
                #    这里不再重复调 LLM；关闭 self_correct 时作为兜底走 raise。
                if (
                    self.entailment_check
                    and not self.self_correct
                    and not self.mock
                    and self.llm is not None
                    and not para.is_soft_conclusion
                    and para.text.strip()
                    and para.evidence_ids
                ):
                    evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
                    if evs:
                        verdict = self._judge_entailment(para, evs)
                        if verdict is not None and not verdict.entailed:
                            raise AgentRunError(
                                code="UNVERIFIED_INFERENCE",
                                message=(
                                    f"paragraph {para.paragraph_id} fails entailment "
                                    f"check: {verdict.reason}"
                                ),
                                retriable=False,
                            )

    # ----- 内部：核心组装 -----

    @staticmethod
    def _affected_section_ids(
        qa_feedback: dict, prior_draft: ReportDraft
    ) -> set[str] | None:
        """从 QA 反馈定位需要重写的 section_id 集合（B1 定向改稿）。

        只认结构化 location：``report.sections[i]...`` / ``report.paragraphs[pid]``
        （issue.dimension 是 QA 维度，与 section 的分析维度是两套体系，不能据此映射）。
        不可定位的 issue（维度级补发 issue / 异常 location）**跳过**，只按可定位的
        issue 收窄重写范围；一个都定位不到 → 返回 ``None`` 退化为全篇重生成。

        注意：A1 给低分维度补发的 issue 用维度级 location(``report.dimension[...]``)，
        且 fact 等会路由到 reporter；若不跳过它们，定向改稿会被它们拖成全篇重生成。
        """
        issues = qa_feedback.get("issues") or []
        must = set(qa_feedback.get("must_address") or [])
        flagged = [i for i in issues if not must or i.get("issue_id") in must]
        if not flagged:
            return None
        pid_to_sec = {
            p.paragraph_id: s.section_id
            for s in prior_draft.sections
            for p in s.paragraphs
        }
        affected: set[str] = set()
        for iss in flagged:
            # 1) 主 location 定位（通常指向冲突的第一段 / 问题段）
            loc = iss.get("location") or ""
            m = _SECTION_IDX_RE.search(loc)
            if m:
                idx = int(m.group(1))
                if 0 <= idx < len(prior_draft.sections):
                    affected.add(prior_draft.sections[idx].section_id)
            else:
                m = _PARAGRAPH_ID_RE.search(loc)
                if m and m.group(1) in pid_to_sec:
                    affected.add(pid_to_sec[m.group(1)])
            # 2) 成对重写：logic 矛盾的 location 只指向一段（paragraph_a），但
            #    required_inputs 里带着另一段（paragraph_b / paragraph_ids /
            #    strength_paragraph / weakness_paragraph）。把相关段所在 section 一并纳入，
            #    让矛盾的【两段】被一起重写、保证内部一致——否则只改一边，矛盾仍在。
            for pid in _paragraph_ids_in(iss.get("required_inputs") or {}):
                if pid in pid_to_sec:
                    affected.add(pid_to_sec[pid])
            # 都不可定位 → 跳过（不据此扩大/放弃定向）
        # 没有任何可定位 issue → 无定向信号 → 退化全篇重生成
        return affected or None

    def _build_output(self, inp: ReporterInput, *, allow_llm: bool) -> ReporterOutput:
        template = get_template(inp.template_id)
        errors: list[AgentError] = []
        if template is None:
            errors.append(
                AgentError(
                    code="TEMPLATE_NOT_FOUND",
                    message=(
                        f"template_id {inp.template_id!r} not registered; "
                        f"available: {sorted(list(_known_template_ids()))}"
                    ),
                    severity="fatal",
                    retriable=False,
                )
            )
            return self._fail(inp, errors)

        valid_claims = self._claim_pool(inp.analysis)
        valid_evidence = self._evidence_pool(inp.analysis)
        ev_db = self._fetch_evidence(valid_evidence)

        sections: list[ReportSection] = []
        banned_hits = 0
        unverified_hits = 0

        # 各章节相互独立（每节自己的 LLM 调用 + 校验），并行生成；结果按 order
        # 收集，顺序与串行一致。token 累加器已加锁，trace context 由 _parallel_map
        # 逐任务 copy_context 传播。
        #
        # 只渲染本次实际分析过的维度章节：固定模板含 pricing/SWOT 等全维度，
        # 但用户可能只选了 feature_comparison。跳过未分析维度，避免交付物里出现
        # 「## 定价策略对比 暂无…」这类空占位。overview / disclaimer（dimension=None）始终保留。
        analysed_dims = set(inp.analysis.dimensions.keys())
        ordered_tpls = [
            t
            for t in sorted(template.sections, key=lambda s: s.order)
            if t.dimension is None or t.dimension in analysed_dims
        ]

        # B1 定向改稿：返工(有 prior_draft + qa_feedback)时只重写被 QA 命中
        # location 的 section，其余原样复用上一版 → 反馈真正有抓手 + 省 LLM 调用。
        # affected_ids=None → 全篇重生成（首轮 / 无 prior / 有不可定位 issue 的兜底）。
        affected_ids: set[str] | None = None
        prior_by_id: dict[str, ReportSection] = {}
        if inp.prior_draft is not None and inp.qa_feedback:
            affected_ids = self._affected_section_ids(
                inp.qa_feedback, inp.prior_draft
            )
            if affected_ids is not None:
                prior_by_id = {
                    s.section_id: s for s in inp.prior_draft.sections
                }

        def _build_one(sec_tpl: ReportSectionTemplate):
            if (
                affected_ids is not None
                and sec_tpl.section_id not in affected_ids
                and sec_tpl.section_id in prior_by_id
            ):
                # 未命中 section：原样复用上一版（不重新调用 LLM）
                return prior_by_id[sec_tpl.section_id], [], 0, 0
            return self._build_section(
                tpl=sec_tpl,
                template=template,
                inp=inp,
                valid_claims=valid_claims,
                valid_evidence=valid_evidence,
                ev_db=ev_db,
                allow_llm=allow_llm,
            )

        section_results = _parallel_map(
            _build_one, ordered_tpls, max_workers=self.MAX_LLM_WORKERS
        )
        for section, sec_errors, sec_banned, sec_unverified in section_results:
            sections.append(section)
            errors.extend(sec_errors)
            banned_hits += sec_banned
            unverified_hits += sec_unverified

        # Self-correct loop（R-5）：在 ReportDraft 定稿前内部修脏段，
        # 避免发到 QA 的 draft 还带 hallucinated 数字 / 过度推断。
        repair_stats = self._run_self_correct(sections, inp, ev_db, errors)
        repair_attempts = repair_stats["repair_attempts"]
        forced_fallbacks = repair_stats["forced_fallbacks"]
        entailment_checks = repair_stats["entailment_checks"]

        # self-correct 改写过段落 → 重新统计 banned_term / unverified_quantity
        # 让 metadata 与 confidence 反映修复后的真实状态
        if repair_attempts:
            banned_hits = 0
            unverified_hits = 0
            for section in sections:
                sec_banned, sec_unverified = self._postprocess_section(
                    section=section, template=template, ev_db=ev_db
                )
                banned_hits += sec_banned
                unverified_hits += sec_unverified

        # 过滤掉未分析维度后，标题里写死的「N. 」序号会跳号（如 1/2/5）→ 连续重排。
        sections = _renumber_sections(sections)

        total_paragraphs = sum(len(s.paragraphs) for s in sections)
        word_count = sum(
            len(p.text) for s in sections for p in s.paragraphs
        )
        claim_count = len(
            {cid for s in sections for p in s.paragraphs for cid in p.claim_ids}
        )
        evidence_count = len(
            {eid for s in sections for p in s.paragraphs for eid in p.evidence_ids}
        )

        draft = ReportDraft(
            report_id=f"rep_{inp.task_id}",
            version=1 + (inp.qa_feedback or {}).get("revision", 0)
            if inp.qa_feedback
            else 1,
            template_id=template.template_id,
            sections=sections,
            summary=self._build_summary(inp.analysis, template),
            metadata={
                "word_count": word_count,
                "claim_count": claim_count,
                "evidence_count": evidence_count,
                "paragraph_count": total_paragraphs,
                "banned_term_hits": banned_hits,
                "unverified_quantity_hits": unverified_hits,
                "repair_attempts": repair_attempts,
                "forced_fallbacks": forced_fallbacks,
                "entailment_checks": entailment_checks,
                "target_audience": template.target_audience,
            },
        )

        confidence = self._overall_confidence(
            total_paragraphs=total_paragraphs,
            banned_hits=banned_hits,
            unverified_hits=unverified_hits,
            forced_fallbacks=forced_fallbacks,
            template=template,
        )
        status = self._derive_status(
            total_paragraphs=total_paragraphs,
            banned_hits=banned_hits,
            unverified_hits=unverified_hits,
            forced_fallbacks=forced_fallbacks,
            template=template,
            confidence=confidence,
        )
        critique = self._build_critique(
            total_paragraphs=total_paragraphs,
            banned_hits=banned_hits,
            unverified_hits=unverified_hits,
            repair_attempts=repair_attempts,
            forced_fallbacks=forced_fallbacks,
            entailment_checks=entailment_checks,
            template=template,
            errors=errors,
        )

        return ReporterOutput(
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
            draft=draft,
        )

    # ----- 单章节：LLM-first，启发式 fallback -----

    def _build_section(
        self,
        *,
        tpl: ReportSectionTemplate,
        template: ReportTemplate,
        inp: ReporterInput,
        valid_claims: set[str],
        valid_evidence: set[str],
        ev_db: dict[str, Evidence],
        allow_llm: bool,
    ) -> tuple[ReportSection, list[AgentError], int, int]:
        errors: list[AgentError] = []
        if tpl.is_overview:
            section = self._heuristic_overview(tpl, inp)
        elif tpl.is_disclaimer:
            section = self._disclaimer_section(tpl, template)
        else:
            section = None
            has_dimension_claims = (
                tpl.dimension is not None
                and inp.analysis.dimensions.get(tpl.dimension) is not None
            )
            if allow_llm and self.llm is not None and has_dimension_claims:
                try:
                    section = self._llm_section(
                        tpl=tpl,
                        template=template,
                        inp=inp,
                        valid_evidence=valid_evidence,
                        ev_db=ev_db,
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(
                        AgentError(
                            code="LLM_SCHEMA_INVALID",
                            message=(
                                f"LLM section {tpl.section_id} failed: "
                                f"{type(e).__name__}: {e}"
                            ),
                            severity="warn",
                            retriable=True,
                        )
                    )
                    section = None
                else:
                    # LLM 产物先做一次校验，过不了就 fallback
                    if section is not None and not self._llm_section_valid(
                        section, valid_claims, valid_evidence, ev_db
                    ):
                        errors.append(
                            AgentError(
                                code="MISSING_CITATION",
                                message=(
                                    f"LLM section {tpl.section_id} failed citation/"
                                    "quantity gate; falling back to heuristic"
                                ),
                                severity="warn",
                                retriable=True,
                            )
                        )
                        section = None
            if section is None:
                section = self._heuristic_section(tpl, inp.analysis)

        # 兜底：LLM 经常漏标 is_quantitative，自动校准；
        # 让 _postprocess_section 的统计与 _post_validate 命中范围
        # 与下游 QA 看到的 flag 保持一致
        self._calibrate_quantitative_flags(section)

        banned_hits, unverified_hits = self._postprocess_section(
            section=section, template=template, ev_db=ev_db
        )
        return section, errors, banned_hits, unverified_hits

    @staticmethod
    def _calibrate_quantitative_flags(section: ReportSection) -> None:
        """LLM 漏标 is_quantitative 时自动补上。

        段落里只要 extract_quantities 命中任意数字，就强制 is_quantitative=True。
        这是 Patch 3 的兜底：让下游 QA 看到正确 flag、Reporter 自己的引用强制
        校验范围也准确，避免 LLM 漏标导致 hallucinated 数字漏网。
        """
        for para in section.paragraphs:
            if not para.is_quantitative and extract_quantities(para.text):
                para.is_quantitative = True

    # ---- LLM 路径 ----

    def _llm_section(
        self,
        *,
        tpl: ReportSectionTemplate,
        template: ReportTemplate,
        inp: ReporterInput,
        valid_evidence: set[str],
        ev_db: dict[str, Evidence],
    ) -> ReportSection | None:
        assert self.llm is not None  # for type checker
        prompt_path = PROMPT_DIR / "section.md"
        system_path = PROMPT_DIR / "system.md"
        if not prompt_path.exists() or not system_path.exists():
            return None
        system_block, user_template = _split_prompt(prompt_path.read_text(encoding="utf-8"))
        system_base = system_path.read_text(encoding="utf-8").strip()
        dim_obj: DimensionAnalysis | None = None
        if tpl.dimension is not None:
            dim_obj = inp.analysis.dimensions.get(tpl.dimension)
        local_claims = dim_obj.claims if dim_obj else []
        allowed_claim_ids = sorted(c.claim_id for c in local_claims)
        # 段落密度按 claim 数动态决定（不设全局字数下限）：claim 越多写越多，
        # claim 少则诚实地短。下限仍是模板 min_paragraphs（通常 1），上限留给
        # LLM 判断——prompt 已要求"每条 claim 一段 + 至多 1 段小结"。
        target_paragraphs = max(tpl.min_paragraphs, len(local_claims))
        # evidence 仅暴露与本章节绑定 dimension 相关的子集（避免 prompt 膨胀）
        allowed_ev = sorted(
            {e for c in local_claims for e in c.evidence_ids if e in valid_evidence}
        )
        ev_excerpts = [
            {
                "evidence_id": ev_db[eid].evidence_id,
                "product": ev_db[eid].product_name,
                "content": ev_db[eid].content,
                "source_url": str(ev_db[eid].source_url),
            }
            for eid in allowed_ev
            if eid in ev_db
        ]
        sys_rendered = _render(
            system_block,
            system=_render(
                system_base,
                allowed_evidence_ids=", ".join(allowed_ev),
                allowed_claim_ids=", ".join(allowed_claim_ids),
            ),
            style=tpl.style,
            target_audience=template.target_audience,
        )
        # QA 返工反馈是最高优先级指令，且要在 trace 的 prompt_preview（前若干字符）里
        # 可见 —— 所以 prepend 到 system 顶部，而不是埋在 user message 末尾被截断。
        # 这让"决策回放"在真实 API 运行里也能一眼看到 reporter_v2 注入了 QA FEEDBACK。
        qa_block = _render_qa_feedback_block(inp.qa_feedback)
        if qa_block.strip():
            sys_rendered = f"{qa_block}\n\n{sys_rendered}"
        usr_rendered = _render(
            user_template,
            project_name=inp.project_name,
            section_id=tpl.section_id,
            title=tpl.title,
            order=str(tpl.order),
            dimension=tpl.dimension.value if tpl.dimension else "(none)",
            allowed_claim_ids=", ".join(allowed_claim_ids),
            allowed_evidence_ids=", ".join(allowed_ev),
            min_paragraphs=str(target_paragraphs),
            claims_json=json.dumps(
                [
                    {
                        "claim_id": c.claim_id,
                        "text": c.text,
                        "products_involved": c.products_involved,
                        "evidence_ids": c.evidence_ids,
                        "qualifier": c.qualifier,
                    }
                    for c in local_claims
                ],
                ensure_ascii=False,
                indent=2,
            ),
            evidences_json=json.dumps(ev_excerpts, ensure_ascii=False, indent=2),
        )

        resp = self.llm.chat(
            system=sys_rendered,
            messages=[{"role": "user", "content": usr_rendered}],
            response_format=ReportSection,
            temperature=0.3,
            # 输出预算随段落密度缩放（厚章节不被截断），有上限防失控
            max_tokens=min(4000, max(2000, 600 * target_paragraphs)),
        )
        try:
            section = _coerce_pydantic(resp, ReportSection)
        except (ValueError, ValidationError):
            return None
        # 强制对齐 section 元数据（防 LLM 输错）
        if (
            section.section_id != tpl.section_id
            or section.title != tpl.title
            or section.order != tpl.order
        ):
            try:
                section = ReportSection(
                    section_id=tpl.section_id,
                    title=tpl.title,
                    order=tpl.order,
                    paragraphs=section.paragraphs,
                )
            except ValidationError:
                return None
        return section

    def _llm_section_valid(
        self,
        section: ReportSection,
        valid_claims: set[str],
        valid_evidence: set[str],
        ev_db: dict[str, Evidence],
    ) -> bool:
        for para in section.paragraphs:
            if any(c not in valid_claims for c in para.claim_ids):
                return False
            if any(e not in valid_evidence for e in para.evidence_ids):
                return False
            if (
                not para.is_soft_conclusion
                and not para.evidence_ids
                and para.text.strip()
            ):
                return False
            # 数字校验：self_correct=True 时放行 → 由后置 _run_self_correct 处理
            # （含定向 LLM repair + 兜底剥号），避免一遇 hallucination 就 fallback
            # heuristic 把段落直接换掉、丢掉修复机会。关闭 self_correct 时保留
            # R-4 行为：在这里直接拒，让 heuristic 接管。
            if self.self_correct:
                continue
            detected = list(extract_quantities(para.text))
            if detected and para.evidence_ids:
                evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
                if evs:
                    for kind, value in detected:
                        if not quantity_supported(kind, value, evs):
                            return False
        return True

    # ---- Entailment LLM-as-judge ----

    def _judge_entailment(
        self, para: ReportParagraph, evidences: list[Evidence]
    ) -> EntailmentVerdict | None:
        """对单段事实陈述跑一次便宜的 LLM-as-judge。

        - 失败 / schema 不对 → 返回 None（调用方按"不阻塞"处理）
        - 成功 → 返回 EntailmentVerdict（entailed + reason）

        Prompt 关键约束见 ``prompts/entailment.md``。
        """
        if self.llm is None:
            return None
        prompt_path = PROMPT_DIR / "entailment.md"
        if not prompt_path.exists():
            return None
        system_block, user_template = _split_prompt(
            prompt_path.read_text(encoding="utf-8")
        )
        excerpts = [
            {
                "evidence_id": ev.evidence_id,
                "product": ev.product_name,
                "content": ev.content,
            }
            for ev in evidences
        ]
        user = _render(
            user_template,
            paragraph_text=para.text,
            evidence_excerpts_json=json.dumps(excerpts, ensure_ascii=False, indent=2),
        )
        try:
            resp = self.llm.chat(
                system=system_block,
                messages=[{"role": "user", "content": user}],
                response_format=EntailmentVerdict,
                temperature=0.0,
                max_tokens=300,
            )
            return _coerce_pydantic(resp, EntailmentVerdict)
        except (ValueError, ValidationError, NotImplementedError):
            return None
        except Exception:  # noqa: BLE001
            # LLM 自身异常不应阻塞 Reporter；后置校验在 entailed=None 时直接放行
            return None

    # ---- Self-correct loop ----

    def _run_self_correct(
        self,
        sections: list[ReportSection],
        inp: ReporterInput,
        ev_db: dict[str, Evidence],
        errors: list[AgentError],
    ) -> dict[str, int]:
        """LLM 写完 draft → 内部自检 → 修不掉再强制降级 qualitative。

        流程：
        1. ``_compute_issue`` 扫描每段：数字 ⊄ evidence、或 entailment=false
        2. 有脏段 → 单段 ``_llm_repair_paragraph``，prompt 里点名要 drop 的数字
           / 推断
        3. 重试至多 ``MAX_REPAIR_ATTEMPTS`` 次；每次 detect 都重跑（修一段可能
           破坏另一段）
        4. 最后一轮仍脏的段：
           - 仅数字问题 → ``_strip_number_token`` 把每个不存在的数字替换成
             "若干 / 相当比例" 等定性词，标 ``is_soft_conclusion=True``
           - 含 entailment 失败 → 整段从 section 移除；section 空了补 1 段
             "本章节涉及结论待人工复核"的占位

        返回 ``{"repair_attempts": int, "forced_fallbacks": int,
        "entailment_checks": int}``，喂给 metadata / confidence / status /
        critique。

        关闭条件（直接返回 0/0）：
        - ``self.self_correct=False``
        - ``ev_db`` 为空（没 evidence 无法判定，避免误伤）
        """
        if not self.self_correct or not ev_db:
            return {
                "repair_attempts": 0,
                "forced_fallbacks": 0,
                "entailment_checks": 0,
            }

        verdict_cache: dict[tuple[str, str], EntailmentVerdict | None] = {}
        entail_enabled = (
            self.entailment_check and not self.mock and self.llm is not None
        )
        # entailment 判定 / repair 重写并行化：entailment 的 LLM 调用放在锁外
        # 才能真正并发；budget 计数 + verdict_cache 读写在锁内保证线程安全。
        cache_lock = threading.Lock()
        entail_state = {"checks": 0, "exhausted": False}

        def compute_issue(para: ReportParagraph) -> _RepairIssue | None:
            if para.is_soft_conclusion or not para.evidence_ids:
                return None
            evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
            if not evs:
                return None
            issue = _RepairIssue()
            for kind, value in extract_quantities(para.text):
                if not quantity_supported(kind, value, evs):
                    issue.unverified_quantities.append((kind, value))
            if issue.unverified_quantities:
                return issue
            if entail_enabled:
                key = (para.paragraph_id, para.text)
                need_call = False
                verdict: EntailmentVerdict | None = None
                with cache_lock:
                    if key in verdict_cache:
                        verdict = verdict_cache[key]
                    elif entail_state["checks"] >= self.MAX_ENTAILMENT_CHECKS:
                        entail_state["exhausted"] = True
                        return issue if issue.is_dirty else None
                    else:
                        entail_state["checks"] += 1
                        need_call = True
                if need_call:
                    # LLM 调用在锁外 → 多段 entailment 真正并发
                    verdict = self._judge_entailment(para, evs)
                    with cache_lock:
                        verdict_cache[key] = verdict
                if verdict is not None and not verdict.entailed:
                    issue.entailment_reason = verdict.reason
            return issue if issue.is_dirty else None

        def _detect_dirty() -> list[tuple[ReportParagraph, _RepairIssue]]:
            paras = [p for s in sections for p in s.paragraphs]
            issues = _parallel_map(
                compute_issue, paras, max_workers=self.MAX_LLM_WORKERS
            )
            return [
                (p, iss) for p, iss in zip(paras, issues) if iss is not None
            ]

        repair_attempts = 0
        for _attempt in range(self.MAX_REPAIR_ATTEMPTS):
            dirty = _detect_dirty()
            if not dirty:
                break
            repair_attempts += 1
            if self.llm is None or self.mock:
                break  # 没 LLM 时直接进 force fallback

            def _repair(item: tuple[ReportParagraph, _RepairIssue]):
                para, issue = item
                evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
                return para, self._llm_repair_paragraph(para, issue, evs)

            for para, repaired in _parallel_map(
                _repair, dirty, max_workers=self.MAX_LLM_WORKERS
            ):
                if repaired is None or not repaired.text.strip():
                    continue
                para.text = repaired.text
                # 文本变了 → 重新校准 is_quantitative（新文本 = 新 cache key）
                para.is_quantitative = bool(extract_quantities(para.text))

        # 强制降级兜底：仍脏的段落 → 剥数字 / 丢段。
        # 修复后文本变了需重判 → 先并行重新检测一遍，再串行做 keep/strip/drop。
        _final_paras = [p for s in sections for p in s.paragraphs]
        _issue_by_para = {
            id(p): iss
            for p, iss in zip(
                _final_paras,
                _parallel_map(
                    compute_issue, _final_paras, max_workers=self.MAX_LLM_WORKERS
                ),
            )
        }
        forced = 0
        for section in sections:
            kept: list[ReportParagraph] = []
            for para in section.paragraphs:
                issue = _issue_by_para.get(id(para))
                if issue is None:
                    kept.append(para)
                    continue
                forced += 1
                if issue.unverified_quantities and not issue.entailment_reason:
                    stripped = para.text
                    for kind, value in issue.unverified_quantities:
                        stripped = _strip_number_token(stripped, kind, value)
                    para.text = stripped
                    para.is_quantitative = False
                    para.is_soft_conclusion = True
                    kept.append(para)
                    errors.append(
                        AgentError(
                            code="SELF_CORRECT_FALLBACK",
                            message=(
                                f"paragraph {para.paragraph_id} numeric tokens "
                                f"stripped after {self.MAX_REPAIR_ATTEMPTS} "
                                f"repair attempts ({issue.describe()})"
                            ),
                            severity="warn",
                            retriable=False,
                        )
                    )
                else:
                    # entailment 失败（不论是否带数字）→ 整段丢
                    errors.append(
                        AgentError(
                            code="SELF_CORRECT_FALLBACK",
                            message=(
                                f"paragraph {para.paragraph_id} dropped after "
                                f"{self.MAX_REPAIR_ATTEMPTS} unsuccessful repair "
                                f"attempts ({issue.describe()})"
                            ),
                            severity="warn",
                            retriable=False,
                        )
                    )
            if not kept and section.paragraphs:
                kept.append(
                    ReportParagraph(
                        paragraph_id=f"p_{section.section_id}_fallback",
                        text=(
                            "本章节涉及的结论暂无可直接引用的 evidence 支撑，"
                            "已隐去待人工复核。"
                        ),
                        claim_ids=[],
                        evidence_ids=[],
                        is_quantitative=False,
                        is_soft_conclusion=True,
                    )
                )
            section.paragraphs = kept

        if entail_state["exhausted"]:
            errors.append(
                AgentError(
                    code="ENTAILMENT_CHECK_BUDGET_EXHAUSTED",
                    message=(
                        "reporter reached internal entailment check budget "
                        f"({self.MAX_ENTAILMENT_CHECKS}); remaining paragraphs "
                        "will be left for QA review"
                    ),
                    severity="warn",
                    retriable=True,
                )
            )

        return {
            "repair_attempts": repair_attempts,
            "forced_fallbacks": forced,
            "entailment_checks": entail_state["checks"],
        }

    def _llm_repair_paragraph(
        self,
        para: ReportParagraph,
        issue: _RepairIssue,
        evidences: list[Evidence],
    ) -> RepairedParagraph | None:
        """对单段调一次 LLM 重写。失败 → 返回 None。"""
        if self.llm is None:
            return None
        prompt_path = PROMPT_DIR / "self_correct.md"
        if not prompt_path.exists():
            return None
        system_block, user_template = _split_prompt(
            prompt_path.read_text(encoding="utf-8")
        )
        excerpts = [
            {
                "evidence_id": ev.evidence_id,
                "product": ev.product_name,
                "content": ev.content,
            }
            for ev in evidences
        ]
        issues_lines: list[str] = []
        if issue.unverified_quantities:
            qts = ", ".join(f"{k}={v:g}" for k, v in issue.unverified_quantities)
            issues_lines.append(
                f"- 以下数字未在 evidence 中字面找到（必须 drop，不要换别的数字）：{qts}"
            )
        if issue.entailment_reason:
            issues_lines.append(
                f"- entailment judge 判定段落过度推断（必须移除该论断）：{issue.entailment_reason}"
            )
        user = _render(
            user_template,
            original_text=para.text,
            issues_list="\n".join(issues_lines) or "- (no specific issues)",
            evidence_excerpts_json=json.dumps(excerpts, ensure_ascii=False, indent=2),
        )
        try:
            resp = self.llm.chat(
                system=system_block,
                messages=[{"role": "user", "content": user}],
                response_format=RepairedParagraph,
                temperature=0.0,
                max_tokens=500,
            )
            return _coerce_pydantic(resp, RepairedParagraph)
        except (ValueError, ValidationError, NotImplementedError):
            return None
        except Exception:  # noqa: BLE001
            return None

    # ---- 启发式路径 ----

    @staticmethod
    def _heuristic_overview(
        tpl: ReportSectionTemplate, inp: ReporterInput
    ) -> ReportSection:
        target = inp.analysis.target_product
        competitors = "、".join(inp.analysis.competitors) or "（暂无）"
        dims = (
            "、".join(dimension_label(d.value) for d in inp.analysis.dimensions.keys())
            or "（暂无）"
        )
        para = ReportParagraph(
            paragraph_id=f"p_{tpl.section_id}_01",
            text=(
                f"本次报告以 {target} 为目标产品，与 {competitors} 进行对比，"
                f"覆盖维度：{dims}。项目：{inp.project_name}。"
            ),
            claim_ids=[],
            evidence_ids=[],
            is_quantitative=False,
            is_soft_conclusion=True,
        )
        return ReportSection(
            section_id=tpl.section_id,
            title=tpl.title,
            order=tpl.order,
            paragraphs=[para],
        )

    @staticmethod
    def _disclaimer_section(
        tpl: ReportSectionTemplate, template: ReportTemplate
    ) -> ReportSection:
        para = ReportParagraph(
            paragraph_id=f"p_{tpl.section_id}_01",
            text=template.disclaimer,
            claim_ids=[],
            evidence_ids=[],
            is_quantitative=False,
            is_soft_conclusion=True,
        )
        return ReportSection(
            section_id=tpl.section_id,
            title=tpl.title,
            order=tpl.order,
            paragraphs=[para],
        )

    @staticmethod
    def _heuristic_section(
        tpl: ReportSectionTemplate,
        analysis: AnalysisResult,
    ) -> ReportSection:
        paragraphs: list[ReportParagraph] = []
        if tpl.dimension is not None:
            dim_obj = analysis.dimensions.get(tpl.dimension)
            if dim_obj is not None:
                # summary 段：soft_conclusion，evidence 选取所有 claim 的并集前两条
                summary_ev = []
                for c in dim_obj.claims[:2]:
                    summary_ev.extend(c.evidence_ids)
                summary_ev = list(dict.fromkeys(summary_ev))[:3]
                if dim_obj.summary.strip():
                    paragraphs.append(
                        ReportParagraph(
                            paragraph_id=f"p_{tpl.section_id}_summary",
                            text=dim_obj.summary.strip(),
                            claim_ids=[],
                            evidence_ids=summary_ev,
                            is_quantitative=_looks_quantitative(dim_obj.summary),
                            is_soft_conclusion=not summary_ev,
                        )
                    )
                for idx, claim in enumerate(dim_obj.claims, start=1):
                    paragraphs.append(
                        ReportParagraph(
                            paragraph_id=f"p_{tpl.section_id}_{idx:02d}",
                            text=_format_claim_paragraph(claim),
                            claim_ids=[claim.claim_id],
                            evidence_ids=list(claim.evidence_ids),
                            is_quantitative=_looks_quantitative(claim.text),
                            is_soft_conclusion=False,
                        )
                    )
        if not paragraphs:
            # 维度缺失时占位（soft_conclusion，避免 MISSING_CITATION）
            paragraphs.append(
                ReportParagraph(
                    paragraph_id=f"p_{tpl.section_id}_placeholder",
                    text=f"暂无与 {tpl.dimension.value if tpl.dimension else tpl.title} 相关的分析结论。",
                    claim_ids=[],
                    evidence_ids=[],
                    is_quantitative=False,
                    is_soft_conclusion=True,
                )
            )
        return ReportSection(
            section_id=tpl.section_id,
            title=tpl.title,
            order=tpl.order,
            paragraphs=paragraphs,
        )

    # ----- 后处理：禁用词 / 数字校验软指标 -----

    def _postprocess_section(
        self,
        section: ReportSection,
        template: ReportTemplate,
        ev_db: dict[str, Evidence],
    ) -> tuple[int, int]:
        banned = 0
        unverified = 0
        # evidence_provider 未注入时，数字校验在本次报告中无法执行。
        # 既不算 unverified（不污染置信），也不算 verified，纯粹跳过。
        skip_quantity = self.evidence_provider is None
        for para in section.paragraphs:
            hits = find_banned_terms(para.text, template.banned_terms_extra)
            banned += len(hits)
            if skip_quantity:
                continue
            if para.is_quantitative and para.evidence_ids:
                evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
                if not evs:
                    # evidence_provider 在但查不到 → 真正的可疑情况
                    unverified += 1
                else:
                    for kind, value in extract_quantities(para.text):
                        if not quantity_supported(kind, value, evs):
                            unverified += 1
        return banned, unverified

    # ----- 摘要 -----

    @staticmethod
    def _build_summary(analysis: AnalysisResult, template: ReportTemplate) -> str:
        parts: list[str] = []
        target = analysis.target_product
        competitors = "、".join(analysis.competitors)
        parts.append(f"以 {target} 为目标的竞品对比（vs {competitors}）。")
        # 各维度摘要拼成读者向正文；不带 [feature_comparison] 这类内部枚举标签
        for dim_obj in analysis.dimensions.values():
            if dim_obj.summary.strip():
                parts.append(dim_obj.summary.strip())
        parts.append(f"目标读者：{template.target_audience}。")
        return " ".join(parts)

    # ----- 置信 / 状态 / 自评估 -----

    def _overall_confidence(
        self,
        *,
        total_paragraphs: int,
        banned_hits: int,
        unverified_hits: int,
        forced_fallbacks: int,
        template: ReportTemplate,
    ) -> float:
        score = self.BASE_CONFIDENCE
        score -= self.PENALTY_PER_BANNED_HIT * banned_hits
        score -= self.PENALTY_PER_UNVERIFIED * unverified_hits
        score -= self.PENALTY_PER_FORCED_FALLBACK * forced_fallbacks
        if total_paragraphs < template.min_total_paragraphs:
            score -= self.PENALTY_THIN_REPORT
        return max(0.0, min(1.0, score))

    @staticmethod
    def _derive_status(
        *,
        total_paragraphs: int,
        banned_hits: int,
        unverified_hits: int,
        forced_fallbacks: int,
        template: ReportTemplate,
        confidence: float,
    ) -> AgentStatus:
        if total_paragraphs == 0:
            return AgentStatus.FAILED
        if confidence < 0.6:
            return AgentStatus.NEEDS_REWORK
        thin = total_paragraphs < template.min_total_paragraphs
        if (
            thin
            or banned_hits > 0
            or unverified_hits > 0
            or forced_fallbacks > 0
        ):
            return AgentStatus.PARTIAL
        return AgentStatus.SUCCESS

    @staticmethod
    def _build_critique(
        *,
        total_paragraphs: int,
        banned_hits: int,
        unverified_hits: int,
        repair_attempts: int,
        forced_fallbacks: int,
        entailment_checks: int,
        template: ReportTemplate,
        errors: list[AgentError],
    ) -> str:
        lines: list[str] = []
        if total_paragraphs < template.min_total_paragraphs:
            lines.append(
                f"段落数 {total_paragraphs} 低于模板下限 "
                f"{template.min_total_paragraphs}"
            )
        if banned_hits:
            lines.append(
                f"命中 {banned_hits} 处禁用词（绝对化表述），建议换措辞"
            )
        if unverified_hits:
            lines.append(
                f"有 {unverified_hits} 处数字未能在 evidence 中字面核对，"
                "若 evidence_provider 未注入请优先补齐"
            )
        if repair_attempts:
            lines.append(
                f"self-correct 跑了 {repair_attempts} 轮 LLM 重写来清理 hallucination"
            )
        if entailment_checks:
            lines.append(f"entailment judge 执行 {entailment_checks} 次")
        if forced_fallbacks:
            lines.append(
                f"self-correct 后仍有 {forced_fallbacks} 段被强制改成 qualitative "
                "（数字剥掉 / 段落丢弃），可能是 evidence 覆盖不足，建议人工复核"
            )
        codes = sorted({e.code for e in errors if e.severity in ("warn", "error")})
        if codes:
            lines.append(f"过程告警：{', '.join(codes)}")
        if not lines:
            return f"模板 {template.template_id} 报告生成正常，引用与数字校验通过。"
        return " | ".join(lines)

    # ----- 内部工具 -----

    def _fetch_evidence(self, evidence_ids: set[str]) -> dict[str, Evidence]:
        if self.evidence_provider is None:
            return {}
        try:
            return self.evidence_provider.get_many(sorted(evidence_ids))
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _claim_pool(analysis: AnalysisResult) -> set[str]:
        pool: set[str] = set()
        for dim in analysis.dimensions.values():
            for c in dim.claims:
                pool.add(c.claim_id)
        return pool

    @staticmethod
    def _evidence_pool(analysis: AnalysisResult) -> set[str]:
        pool: set[str] = set()
        for dim in analysis.dimensions.values():
            for c in dim.claims:
                pool.update(c.evidence_ids)
                pool.update(c.counter_evidence_ids)
        return pool

    def _fail(
        self, inp: ReporterInput, errors: list[AgentError]
    ) -> ReporterOutput:
        draft = ReportDraft(
            report_id=f"rep_{inp.task_id}",
            version=1,
            template_id=inp.template_id,
            sections=[],
            summary="",
            metadata={
                "word_count": 0,
                "claim_count": 0,
                "evidence_count": 0,
                "paragraph_count": 0,
                "banned_term_hits": 0,
                "unverified_quantity_hits": 0,
            },
        )
        return ReporterOutput(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=inp.trace_id,
            span_id=inp.span_id,
            status=AgentStatus.FAILED,
            confidence=0.0,
            self_critique="; ".join(e.message for e in errors)[:1000]
            or "Reporter failed before any section was produced.",
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
            duration_ms=0,
            errors=errors,
            draft=draft,
        )


# ---------- prompt 解析辅助（与 Analyst 同款） ----------


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
    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        value = vars.get(expr)
        return "" if value is None else str(value)

    return re.sub(r"{{\s*(.+?)\s*}}", repl, template)


_REPORTER_CLOSING = (
    "Apply the fixes above. Only this section's relevant issues (matching "
    "``location``) need to be addressed here; other sections will be "
    "regenerated separately. Do NOT re-introduce dropped evidence_ids or "
    "hallucinated numbers."
)


def _render_qa_feedback_block(qa_feedback: dict | None) -> str:
    """Reporter 专用 thin wrapper（保留原名让外部测试用同样路径调）。"""
    from backend.agents._qa_feedback import render_qa_feedback_block

    return render_qa_feedback_block(
        qa_feedback, closing_instruction=_REPORTER_CLOSING
    )


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


# ---------- 启发式辅助 ----------

_QUANT_HINTS = ("$", "%", "/seat", "/user", "per ", "档", "version", "v.")


def _looks_quantitative(text: str) -> bool:
    if any(hint in text for hint in _QUANT_HINTS):
        return True
    # 含 2 位以上数字
    return bool(re.search(r"\d{2,}", text))


def _format_claim_paragraph(claim: AnalysisClaim) -> str:
    out = claim.text.strip()
    if claim.qualifier:
        out = f"{out}（{claim.qualifier}）"
    return out


# 标题里写死的「3. 」「4、」前缀；过滤章节后用来剥离再连续重排
_SECTION_NUM_PREFIX = re.compile(r"^\s*\d+\s*[\.、]\s*")


def _renumber_sections(sections: list[ReportSection]) -> list[ReportSection]:
    """把章节标题的前导序号按当前顺序连续重排（1,2,3…），消除过滤后的跳号。

    标题不含前导数字（如「摘要」）则原样保留，不强加序号。
    """
    out: list[ReportSection] = []
    for i, sec in enumerate(sections, start=1):
        base = _SECTION_NUM_PREFIX.sub("", sec.title)
        new_title = f"{i}. {base}" if base != sec.title else sec.title
        out.append(sec.model_copy(update={"title": new_title}) if new_title != sec.title else sec)
    return out


def _known_template_ids() -> set[str]:
    from .templates import TEMPLATES

    return set(TEMPLATES.keys())


# ---------- self-correct 兜底：剥掉特定数字 token ----------


def _strip_number_token(text: str, kind: str, value: float) -> str:
    """把段落里某个具体数字 token 替换成定性词。

    用于 self-correct 3 轮 LLM 重试仍失败的最终兜底。模式按 kind 分支匹配；
    匹配不上就原样返回（最坏情况是数字残留，外层会把段落标 soft_conclusion，
    QA 至少不会再因这条 claim "被严肃陈述" 触发 fact_consistency=0）。
    """
    vstr = f"{value:g}"
    escaped = re.escape(vstr)
    if kind == "price":
        return re.sub(rf"\$\s*{escaped}(?:\.\d{{1,2}})?", "相应价格", text)
    if kind == "percent":
        return re.sub(rf"{escaped}(?:\.\d{{1,2}})?\s*%", "相当比例", text)
    if kind == "version":
        return re.sub(
            rf"\bv\s*{escaped}(?:\.\d+){{0,2}}",
            "近期版本",
            text,
            flags=re.IGNORECASE,
        )
    # plain number：与 _PLAIN_NUMBER_RE 保持 ASCII-only 断言，避免中文阻断
    return re.sub(
        rf"(?<![A-Za-z0-9_.]){escaped}\+?(?![A-Za-z0-9_.%])",
        "若干",
        text,
    )


__all__ = ["Reporter", "BANNED_TERMS"]
