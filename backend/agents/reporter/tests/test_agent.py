"""Reporter Agent 单元测试。

覆盖：
1. mock + standard_v1 跑通：5 章节 + 全部段落引用合法 + summary 与 metadata 正确
2. mock + investor_v1：positioning 维度缺失 → 占位 soft 段，整体不 fail
3. mock + pm_v1：differentiation 维度缺失 → 占位 soft 段
4. TEMPLATE_NOT_FOUND：未注册的 template_id → status=FAILED + 错误码
5. MISSING_CITATION：注入空 evidence_ids 的 factual 段落 → _post_validate 抛
6. UNVERIFIED_QUANTITY：is_quantitative=True 段落数字找不到 evidence 原值 → 抛
7. INSUFFICIENT_EVIDENCE：段落引用 pool 外 evidence_id → 抛
8. INSUFFICIENT_EVIDENCE：段落引用 pool 外 claim_id → 抛
9. 禁用词命中 → status=PARTIAL + metadata.banned_term_hits > 0
10. extract_quantities：价格 / 百分比 / 版本号 / 纯数字
11. quantity_supported：±5% 容差 / 找不到时返回 False
12. find_banned_terms：默认词表 + 模板追加
13. ReporterInput Schema extra=forbid
14. 真实模式 LLM 失败 → fallback heuristic（NullLLM）
15. 真实模式 LLM 返回非法 evidence → 校验拒绝 + fallback heuristic
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.reporter import (
    BANNED_TERMS,
    Reporter,
    extract_quantities,
    find_banned_terms,
    quantity_supported,
)
from backend.agents.reporter.fixtures import (
    load_demo_analysis,
    load_demo_input,
)
from backend.agents.reporter.tests.conftest import FakeLLM, NullLLM, NullTracer
from backend.schemas import (
    AgentStatus,
    Evidence,
    EvidenceLocation,
    ReporterInput,
    ReporterOutput,
    ReportParagraph,
    ReportSection,
)
from datetime import UTC, datetime


def _make_evidence(eid: str, content: str) -> Evidence:
    return Evidence(
        evidence_id=eid,
        source_id=f"src_{eid}",
        product_name="X",
        source_url="https://example.com/",
        source_type="pricing_page",
        source_authority=0.9,
        content=content,
        content_hash=f"h_{eid}",
        location=EvidenceLocation(),
        collected_at=datetime.now(tz=UTC),
        extracted_at=datetime.now(tz=UTC),
        confidence=0.9,
        tags=[],
    )


# ---------- 1. mock + standard_v1 ----------


def test_mock_standard_v1_full_pipeline() -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert isinstance(out, ReporterOutput)
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    assert out.draft.template_id == "standard_v1"
    # 5 章节 (overview / features / pricing / swot / source)
    assert len(out.draft.sections) == 5
    titles = [s.title for s in out.draft.sections]
    assert titles[0].startswith("1.")
    assert titles[-1].endswith("数据来源声明")

    # 至少有概览 + 至少一条事实段落 + 来源声明
    total = sum(len(s.paragraphs) for s in out.draft.sections)
    assert total >= 4

    # 来源声明段必须 soft_conclusion 且 evidence_ids 为空
    disclaimer_sec = out.draft.sections[-1]
    assert disclaimer_sec.paragraphs[0].is_soft_conclusion is True
    assert disclaimer_sec.paragraphs[0].evidence_ids == []

    # 所有事实段必须有 evidence_ids，且都属于 analysis 池
    valid_ev = _evidence_pool(inp)
    valid_claims = _claim_pool(inp)
    for section in out.draft.sections:
        for para in section.paragraphs:
            if para.is_soft_conclusion or not para.text.strip():
                continue
            assert para.evidence_ids, f"{para.paragraph_id} 缺 evidence_ids"
            assert all(e in valid_ev for e in para.evidence_ids), (
                f"{para.paragraph_id} 越界 evidence: {para.evidence_ids}"
            )
            assert all(c in valid_claims for c in para.claim_ids), (
                f"{para.paragraph_id} 越界 claim: {para.claim_ids}"
            )

    # metadata 完整
    md = out.draft.metadata
    assert md["paragraph_count"] == total
    assert md["target_audience"] == "产品经理"

    # BaseAgent 注入的元数据
    assert out.agent_name == "reporter"
    assert out.agent_version == "1.0.0"
    assert out.trace_id == "trace-demo"


# ---------- 2. investor_v1（含 positioning 缺失） ----------


def test_mock_investor_v1_handles_missing_dimension() -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="investor_v1", target_audience="投资人")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # positioning 维度在 fixture 中缺失 → 应当出现占位 soft 段，不能 FAILED
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    section_ids = [s.section_id for s in out.draft.sections]
    assert "sec_positioning" in section_ids
    positioning = next(s for s in out.draft.sections if s.section_id == "sec_positioning")
    assert positioning.paragraphs, "positioning 章节至少要有占位段"
    # 占位段必须 soft_conclusion，否则会触发 MISSING_CITATION
    assert positioning.paragraphs[0].is_soft_conclusion is True


# ---------- 3. pm_v1（含 differentiation 缺失） ----------


def test_mock_pm_v1_handles_missing_dimension() -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="pm_v1", target_audience="PM")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    opp = next(s for s in out.draft.sections if s.section_id == "sec_opportunities")
    assert opp.paragraphs
    assert opp.paragraphs[0].is_soft_conclusion is True


# ---------- 4. TEMPLATE_NOT_FOUND ----------


def test_unknown_template_id_returns_failed() -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="totally_made_up_v999")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.FAILED
    assert any(e.code == "TEMPLATE_NOT_FOUND" for e in out.errors)
    assert out.draft.sections == []


# ---------- 5. MISSING_CITATION ----------


def test_missing_citation_triggers_post_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")

    original = agent._build_output

    def poisoned(inp_: ReporterInput, *, allow_llm: bool) -> ReporterOutput:
        out = original(inp_, allow_llm=allow_llm)
        # 找一个非 disclaimer 章节，注入一个没有 evidence 的 factual 段落
        sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
        sec.paragraphs.append(
            ReportParagraph(
                paragraph_id="p_poison_01",
                text="This paragraph is factual but has zero citations.",
                claim_ids=[],
                evidence_ids=[],
                is_quantitative=False,
                is_soft_conclusion=False,  # 关键：非软结论
            )
        )
        return out

    monkeypatch.setattr(agent, "_build_output", poisoned)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status is AgentStatus.NEEDS_REWORK
    assert any(e.code == "MISSING_CITATION" for e in out.errors)


# ---------- 6. UNVERIFIED_QUANTITY ----------


def test_unverified_quantity_triggers_post_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")

    original = agent._build_output

    def poisoned(inp_: ReporterInput, *, allow_llm: bool) -> ReporterOutput:
        out = original(inp_, allow_llm=allow_llm)
        sec = next(s for s in out.draft.sections if s.section_id == "sec_pricing")
        # ev_notion_price_01 内容只含 10 / 15 / Enterprise；我们捏一个 $999
        sec.paragraphs.append(
            ReportParagraph(
                paragraph_id="p_poison_qty",
                text="Notion 实际价格高达 $999/seat/月。",
                claim_ids=["cl_price_001"],
                evidence_ids=["ev_notion_price_01"],
                is_quantitative=True,
                is_soft_conclusion=False,
            )
        )
        return out

    monkeypatch.setattr(agent, "_build_output", poisoned)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status is AgentStatus.NEEDS_REWORK
    assert any(e.code == "UNVERIFIED_QUANTITY" for e in out.errors)


# ---------- 6b. UNVERIFIED_QUANTITY 即使 LLM 漏标 is_quantitative 也要抓 ----------


def test_unverified_quantity_without_flag_still_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA 真实链路抓到的 7 处 hallucination 都来自 LLM 忘记标 is_quantitative。

    回归用例：构造段落 is_quantitative=False、文本含 evidence 里不存在的数字
    （20%、27%、99）→ _post_validate 仍应抛 UNVERIFIED_QUANTITY。
    """
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")

    original = agent._build_output

    def poisoned(inp_: ReporterInput, *, allow_llm: bool) -> ReporterOutput:
        out = original(inp_, allow_llm=allow_llm)
        sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
        # ev_clickup_feature_01 内容 "100+ pre-built automations"，没有 20% / 99
        sec.paragraphs.append(
            ReportParagraph(
                paragraph_id="p_qa_regression",
                text="ClickUp 在协作场景中提升了 20% 的效率，覆盖 99 个团队。",
                claim_ids=["cl_feat_002"],
                evidence_ids=["ev_clickup_feature_01"],
                is_quantitative=False,  # 关键：LLM 漏标，但仍应被扫出来
                is_soft_conclusion=False,
            )
        )
        return out

    monkeypatch.setattr(agent, "_build_output", poisoned)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status is AgentStatus.NEEDS_REWORK
    quantity_errors = [e for e in out.errors if e.code == "UNVERIFIED_QUANTITY"]
    assert quantity_errors, "UNVERIFIED_QUANTITY 必须被抓到，即便 is_quantitative=False"
    assert "possible hallucination" in quantity_errors[0].message


# ---------- 6c. is_quantitative 自动校准（Patch 3） ----------


def test_is_quantitative_auto_calibrated_when_llm_forgets() -> None:
    """LLM 给出含数字段落但 is_quantitative=False → Reporter 应自动校准为 True，
    便于下游 QA / Frontend 复用。同时 metadata 的统计应基于校准后的 flag。"""
    bad_section = ReportSection(
        section_id="sec_features",
        title="2. 核心功能对比",
        order=2,
        paragraphs=[
            ReportParagraph(
                paragraph_id="p_llm_unflagged",
                # 引用的 ev_clickup_feature_01 含 "100+"，与文本 100+ 匹配
                text="ClickUp 自动化覆盖 100+ 预制流。",
                claim_ids=["cl_feat_002"],
                evidence_ids=["ev_clickup_feature_01"],
                is_quantitative=False,  # 关键：故意漏标
                is_soft_conclusion=False,
            )
        ],
    )
    fake = FakeLLM(by_section={"sec_features": bad_section})
    # 注入 evidence_provider 走数字校验路径
    from backend.agents.reporter import FixtureEvidenceProvider

    agent = Reporter(
        llm=fake,
        tracer=NullTracer(),
        evidence_provider=FixtureEvidenceProvider(),
        mock=False,
    )
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
    target = next(p for p in sec.paragraphs if p.paragraph_id == "p_llm_unflagged")
    # Patch 3：is_quantitative 应被自动设为 True
    assert target.is_quantitative is True


# ---------- 7. evidence_ids 越界 ----------


def test_evidence_id_outside_pool_triggers_post_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")

    original = agent._build_output

    def poisoned(inp_: ReporterInput, *, allow_llm: bool) -> ReporterOutput:
        out = original(inp_, allow_llm=allow_llm)
        sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
        sec.paragraphs.append(
            ReportParagraph(
                paragraph_id="p_poison_ev",
                text="ClickUp 引用了一个根本不存在的证据。",
                claim_ids=[],
                evidence_ids=["ev_hallucinated_zzz"],
                is_quantitative=False,
                is_soft_conclusion=False,
            )
        )
        return out

    monkeypatch.setattr(agent, "_build_output", poisoned)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status is AgentStatus.NEEDS_REWORK
    assert any(e.code == "INSUFFICIENT_EVIDENCE" for e in out.errors)


# ---------- 8. claim_ids 越界 ----------


def test_claim_id_outside_pool_triggers_post_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")

    original = agent._build_output

    def poisoned(inp_: ReporterInput, *, allow_llm: bool) -> ReporterOutput:
        out = original(inp_, allow_llm=allow_llm)
        sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
        sec.paragraphs.append(
            ReportParagraph(
                paragraph_id="p_poison_claim",
                text="A factual claim that cites a non-existent claim id.",
                claim_ids=["cl_does_not_exist"],
                evidence_ids=["ev_notion_home_01"],
                is_quantitative=False,
                is_soft_conclusion=False,
            )
        )
        return out

    monkeypatch.setattr(agent, "_build_output", poisoned)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status is AgentStatus.NEEDS_REWORK
    assert any(e.code == "INSUFFICIENT_EVIDENCE" for e in out.errors)


# ---------- 9. 禁用词命中 → PARTIAL + metadata ----------


def test_banned_term_hit_yields_partial_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")

    # monkeypatch 启发式合成函数，让 sec_features 段落带「绝对领先」
    from backend.agents.reporter import agent as agent_module

    original = agent_module.Reporter._heuristic_section

    def tainted_heuristic(tpl, analysis):  # type: ignore[no-untyped-def]
        section = original(tpl, analysis)
        if tpl.section_id == "sec_features" and section.paragraphs:
            first = section.paragraphs[0]
            section.paragraphs[0] = first.model_copy(
                update={"text": first.text + " Notion 绝对领先于所有对手。"}
            )
        return section

    monkeypatch.setattr(
        agent_module.Reporter, "_heuristic_section", staticmethod(tainted_heuristic)
    )

    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status is AgentStatus.PARTIAL
    assert out.draft.metadata["banned_term_hits"] >= 1
    assert "禁用词" in out.self_critique


# ---------- 10. extract_quantities ----------


def test_extract_quantities_covers_price_percent_version_number() -> None:
    text = "ClickUp $7/seat 起，自动化覆盖率 35%，最新版本 v2.3，包含 100+ 预制流。"
    qs = dict(extract_quantities(text))
    # 价格 7
    assert qs.get("price") == 7.0
    # 百分比 35
    assert qs.get("percent") == 35.0
    # 版本号 2.3
    assert qs.get("version") == pytest.approx(2.3)
    # 纯数字 100
    # 抽取后可能含多个；只断言 100 在其中
    nums = [v for k, v in extract_quantities(text) if k == "number"]
    assert 100.0 in nums


# ---------- 10b. R4 回归：纯数字在中文上下文中也要被抓到 ----------


def test_extract_quantities_captures_chinese_context_counts() -> None:
    """QA 真实链路漏过的 '17 个集成 / 99 家企业' case。

    Python 3 默认 \\w 含 Unicode，所以中文「个 / 家」在旧正则里算 word，
    会把前面的「17 / 99」阻断成不可见数字。修复后必须能抓出来。
    """
    nums = {v for k, v in extract_quantities("ClickUp 提供 17 个集成。") if k == "number"}
    assert 17.0 in nums, "17 必须被抓到（中文「个」不应阻断 plain number 匹配）"

    nums2 = {v for k, v in extract_quantities("已被 99 家企业采用。") if k == "number"}
    assert 99.0 in nums2

    # 中英文混排同样要抓
    nums3 = {v for k, v in extract_quantities("Notion 已被 200 万用户选用。") if k == "number"}
    assert 200.0 in nums3


# ---------- 11. quantity_supported ----------


def test_quantity_supported_respects_tolerance() -> None:
    evs = [_make_evidence("ev1", "Plan Plus at $10 per seat/month.")]
    # 精确命中
    assert quantity_supported("price", 10.0, evs)
    # ±5% 容差内（10.4 vs 10）
    assert quantity_supported("price", 10.4, evs)
    # 容差外
    assert not quantity_supported("price", 99.0, evs)
    # 空 evidence
    assert not quantity_supported("price", 10.0, [])


def test_quantity_supported_for_percent_and_version() -> None:
    evs = [
        _make_evidence("ev_pct", "Coverage reaches 35% across regions."),
        _make_evidence("ev_ver", "Latest release is v2.3 from Q1."),
    ]
    assert quantity_supported("percent", 35.0, evs)
    assert quantity_supported("percent", 36.0, evs)  # ±5% 内
    assert not quantity_supported("percent", 70.0, evs)
    assert quantity_supported("version", 2.3, evs)


# ---------- 12. find_banned_terms ----------


def test_find_banned_terms_picks_up_defaults_and_extras() -> None:
    text = "Notion 绝对领先，是行业唯一的选择。"
    hits = find_banned_terms(text)
    assert "绝对领先" in hits
    assert "行业唯一" in hits

    # 模板自定义追加
    extra = ["稳赢"]
    hits2 = find_banned_terms("这笔投资稳赢不赔。", extra)
    assert "稳赢" in hits2

    # BANNED_TERMS 公开常量是 tuple
    assert isinstance(BANNED_TERMS, tuple)


# ---------- 12b. R4 回归：绝对化宣称（数字 + 强力修饰） ----------


def test_find_banned_terms_catches_superlative_quantitative_claims() -> None:
    """QA 真实抓到的 '98% 福布斯云100强企业信赖' 模式。

    单独看 '信赖' / '100强' 都不是禁用词，组合在一起才是。
    """
    # 「98% xxx 信赖」
    hits = find_banned_terms("98% 福布斯云100强企业信赖 Notion。")
    assert any("信赖" in h for h in hits), f"未命中 '98%…信赖' 模式: {hits}"
    # 「100强企业」
    hits2 = find_banned_terms("已为 500 强企业服务多年。")
    assert any("强企业" in h or "强 企业" in h for h in hits2), f"未命中 '500强企业': {hits2}"
    # 「全网都在用」
    hits3 = find_banned_terms("全网都在用 Notion 协同。")
    assert hits3, f"未命中 '全网都在用': {hits3}"
    # 反向：不该误报
    hits_neg = find_banned_terms("Notion 在文档编辑场景的灵活度领先。")
    assert hits_neg == [], f"误报：{hits_neg}"


# ---------- 13. Schema 严格性 ----------


def test_reporter_input_rejects_unknown_field() -> None:
    analysis = load_demo_analysis()
    with pytest.raises(ValidationError):
        ReporterInput(  # type: ignore[call-arg]
            task_id="t",
            project_id="p",
            trace_id="tr",
            span_id="sp",
            project_name="demo",
            analysis=analysis,
            template_id="standard_v1",
            unknown="oops",
        )


# ---------- 14. 真实模式 LLM 全失败 → fallback heuristic ----------


def test_real_mode_with_failing_llm_falls_back_to_heuristic() -> None:
    agent = Reporter(llm=NullLLM(), tracer=NullTracer(), mock=False)
    # 真实模式没自动 evidence_provider，数字校验 fallback 走 unverified=warn
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 启发式应能产出 5 章节、各章节有段落
    assert out.draft.sections, "LLM 失败仍应有启发式产出"
    assert len(out.draft.sections) == 5
    # 每次 LLM 失败应有一条 LLM_SCHEMA_INVALID（overview / disclaimer 章节不调 LLM）
    assert any(e.code == "LLM_SCHEMA_INVALID" for e in out.errors)


# ---------- 15. 真实模式 LLM 返回非法 evidence → fallback ----------


def test_real_mode_with_hallucinating_llm_rejects_and_falls_back() -> None:
    bad_section = ReportSection(
        section_id="sec_features",
        title="2. 核心功能对比",
        order=2,
        paragraphs=[
            ReportParagraph(
                paragraph_id="p_llm_bad",
                text="Notion 引用了一个虚构的 evidence 来撒谎。",
                claim_ids=[],
                evidence_ids=["ev_hallucinated_xyz"],  # pool 外
                is_quantitative=False,
                is_soft_conclusion=False,
            )
        ],
    )
    fake = FakeLLM(by_section={"sec_features": bad_section})
    agent = Reporter(llm=fake, tracer=NullTracer(), mock=False)
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # _llm_section_valid 把这段拒掉 → fallback heuristic
    # 启发式产出后通过 _post_validate
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
    assert all(
        all(e != "ev_hallucinated_xyz" for e in p.evidence_ids) for p in sec.paragraphs
    )
    # 应该记录 LLM citation fallback
    assert any(e.code in {"MISSING_CITATION", "LLM_SCHEMA_INVALID"} for e in out.errors)


# ---------- 16. R4 回归：entailment LLM-as-judge 拦截过度推断 ----------


def test_entailment_judge_triggers_legacy_raise_when_self_correct_disabled() -> None:
    """R-4 行为（self_correct=False）：entailed=False → 直接抛 UNVERIFIED_INFERENCE → NEEDS_REWORK。

    现在默认 self_correct=True 会走 self-correct loop（见下一个测试），
    所以本用例显式关闭 self_correct 来锁住 R-4 兜底行为。
    """
    from backend.agents.reporter import FixtureEvidenceProvider
    from backend.agents.reporter.agent import EntailmentVerdict

    bad_section = ReportSection(
        section_id="sec_swot",
        title="4. SWOT（以目标产品为视角）",
        order=4,
        paragraphs=[
            ReportParagraph(
                paragraph_id="p_swot_over",
                text="Notion 内置 AI 助手，Asana 在 AI 能力上完全缺失这类能力。",
                claim_ids=["cl_swot_001"],
                evidence_ids=["ev_notion_feature_01"],
                is_quantitative=False,
                is_soft_conclusion=False,
            )
        ],
    )
    fake = FakeLLM(
        by_section={"sec_swot": bad_section},
        entailment_default=EntailmentVerdict(
            entailed=False,
            reason="未支撑：引用的 evidence 只描述 Notion，未提及 Asana 的 AI 能力缺失",
        ),
    )
    agent = Reporter(
        llm=fake,
        tracer=NullTracer(),
        evidence_provider=FixtureEvidenceProvider(),
        mock=False,
        self_correct=False,  # 锁住 R-4 raise 行为
    )
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.NEEDS_REWORK
    inference_errors = [e for e in out.errors if e.code == "UNVERIFIED_INFERENCE"]
    assert inference_errors, "entailed=False 应触发 UNVERIFIED_INFERENCE"
    assert "未支撑" in inference_errors[0].message


def test_entailment_judge_self_correct_drops_unsupported_paragraph() -> None:
    """R-5 默认行为：self_correct=True 时 entailment 失败 + LLM repair 失败
    → 最多 3 轮后强制丢段 + 留 SELF_CORRECT_FALLBACK warn，
    QA 拿到的 draft 不再含 hallucinated claim，整体状态 PARTIAL（不再 NEEDS_REWORK）。
    """
    from backend.agents.reporter import FixtureEvidenceProvider
    from backend.agents.reporter.agent import EntailmentVerdict

    bad_section = ReportSection(
        section_id="sec_swot",
        title="4. SWOT（以目标产品为视角）",
        order=4,
        paragraphs=[
            ReportParagraph(
                paragraph_id="p_swot_over",
                text="Notion 内置 AI 助手，Asana 在 AI 能力上完全缺失这类能力。",
                claim_ids=["cl_swot_001"],
                evidence_ids=["ev_notion_feature_01"],
                is_quantitative=False,
                is_soft_conclusion=False,
            )
        ],
    )
    # FakeLLM 没配 repair_default → 3 轮 LLM repair 全失败 → 走强制兜底。
    # 只让目标段落 entailed=False，避免误伤合规段（否则全 section 都被丢）
    fake = FakeLLM(
        by_section={"sec_swot": bad_section},
        entailment_by_phrase={
            "完全缺失这类能力": EntailmentVerdict(
                entailed=False,
                reason="未支撑：引用的 evidence 只描述 Notion，未提及 Asana",
            ),
        },
        entailment_default=EntailmentVerdict(entailed=True, reason="ok"),
    )
    agent = Reporter(
        llm=fake,
        tracer=NullTracer(),
        evidence_provider=FixtureEvidenceProvider(),
        mock=False,
        # self_correct 默认 True
    )
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 不再 NEEDS_REWORK；self-correct 兜底后状态降到 PARTIAL
    assert out.status is AgentStatus.PARTIAL
    # _post_validate 不再抛 UNVERIFIED_INFERENCE（self-correct 已处理）
    assert not any(e.code == "UNVERIFIED_INFERENCE" for e in out.errors)
    # 应有 SELF_CORRECT_FALLBACK warn
    fallback_errors = [e for e in out.errors if e.code == "SELF_CORRECT_FALLBACK"]
    assert fallback_errors, "应有 SELF_CORRECT_FALLBACK 记录脏段被处理"
    # 原始 hallucination 段已被丢弃；sec_swot 现在只剩 fallback 占位段
    sec = next(s for s in out.draft.sections if s.section_id == "sec_swot")
    assert all("完全缺失" not in p.text for p in sec.paragraphs), (
        "原 hallucination 文本不应再出现在 draft"
    )
    # metadata 显示 self-correct 跑过
    assert out.draft.metadata["repair_attempts"] >= 1
    assert out.draft.metadata["forced_fallbacks"] >= 1


def test_entailment_judge_passes_supported_paragraph() -> None:
    """entailed=True 的段落应放行，不影响正常流程。"""
    from backend.agents.reporter import FixtureEvidenceProvider
    from backend.agents.reporter.agent import EntailmentVerdict

    fake = FakeLLM(
        # 让 LLM 都失败 → fallback heuristic，再统一判 entailed=True
        by_section={},
        entailment_default=EntailmentVerdict(entailed=True, reason="已支撑：fixture demo"),
    )
    agent = Reporter(
        llm=fake,
        tracer=NullTracer(),
        evidence_provider=FixtureEvidenceProvider(),
        mock=False,
    )
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    # 不应有 UNVERIFIED_INFERENCE
    assert not any(e.code == "UNVERIFIED_INFERENCE" for e in out.errors)
    # FakeLLM 应被调到 entailment 路径
    assert any(c.startswith("entailment:") for c in fake.call_log)


def test_entailment_check_skipped_in_mock_mode() -> None:
    """mock 模式 / 无 llm 时 entailment 自动跳过，避免破坏既有 mock 测试。"""
    agent = Reporter(mock=True)  # 默认 entailment_check=True，但 self.llm 为 None
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    assert not any(e.code == "UNVERIFIED_INFERENCE" for e in out.errors)


def test_entailment_check_can_be_disabled() -> None:
    """entailment_check=False 时即使 LLM 判 entailed=False 也不阻塞（用于节约成本）。"""
    from backend.agents.reporter import FixtureEvidenceProvider
    from backend.agents.reporter.agent import EntailmentVerdict

    fake = FakeLLM(
        entailment_default=EntailmentVerdict(entailed=False, reason="挡不住"),
    )
    agent = Reporter(
        llm=fake,
        tracer=NullTracer(),
        evidence_provider=FixtureEvidenceProvider(),
        mock=False,
        entailment_check=False,  # 关闭
    )
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert not any(e.code == "UNVERIFIED_INFERENCE" for e in out.errors)
    # FakeLLM 不应被调到 entailment 路径
    assert not any(c.startswith("entailment:") for c in fake.call_log)


# ---------- 17. R-5 self-correct loop：LLM repair 成功 → 干净 draft ----------


def test_self_correct_llm_repair_cleans_hallucinated_numbers() -> None:
    """LLM 给出含 hallucinated 数字的章节，但 self-correct prompt 成功
    让 LLM 改写成定性表述 → 最终 draft 不再含坏数字、不进入兜底剥号。"""
    from backend.agents.reporter import FixtureEvidenceProvider
    from backend.agents.reporter.agent import EntailmentVerdict, RepairedParagraph

    bad_section = ReportSection(
        section_id="sec_features",
        title="2. 核心功能对比",
        order=2,
        paragraphs=[
            ReportParagraph(
                paragraph_id="p_feat_bad",
                # ev_clickup_feature_01 含 "100+"，但 27% 是编的
                text="ClickUp 自动化覆盖率提升 27%，仍优于 Notion 的内置工作流。",
                claim_ids=["cl_feat_002"],
                evidence_ids=["ev_clickup_feature_01"],
                is_quantitative=True,
                is_soft_conclusion=False,
            )
        ],
    )
    repaired = RepairedParagraph(
        text="ClickUp 自动化覆盖范围较广，相对 Notion 的内置工作流更适合复杂跨任务编排。"
    )
    fake = FakeLLM(
        by_section={"sec_features": bad_section},
        entailment_default=EntailmentVerdict(entailed=True, reason="已支撑"),
        repair_default=repaired,
    )
    agent = Reporter(
        llm=fake,
        tracer=NullTracer(),
        evidence_provider=FixtureEvidenceProvider(),
        mock=False,
    )
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
    target = next((p for p in sec.paragraphs if p.paragraph_id == "p_feat_bad"), None)
    assert target is not None
    # LLM repair 改写成功 → 段落保留但文字换了，27% 消失
    assert "27%" not in target.text
    assert "覆盖范围较广" in target.text
    # 状态干净（PARTIAL 因为 repair_attempts > 0 不算干扰）
    assert out.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)
    # 无 UNVERIFIED_QUANTITY raise
    assert not any(e.code == "UNVERIFIED_QUANTITY" for e in out.errors)
    # 至少 1 轮 repair
    assert out.draft.metadata["repair_attempts"] >= 1
    # repair LLM 被调过
    assert any(c.startswith("repair:") for c in fake.call_log)


# ---------- 18. R-5：LLM repair 全失败 → 兜底剥数字，draft 仍干净 ----------


def test_self_correct_force_strips_numbers_when_llm_repair_fails() -> None:
    """LLM repair 3 轮都失败时，self-correct 兜底用正则剥掉坏数字，
    段落标 is_soft_conclusion=True，draft 仍发得出去（不会 NEEDS_REWORK）。"""
    from backend.agents.reporter import FixtureEvidenceProvider
    from backend.agents.reporter.agent import EntailmentVerdict

    bad_section = ReportSection(
        section_id="sec_features",
        title="2. 核心功能对比",
        order=2,
        paragraphs=[
            ReportParagraph(
                paragraph_id="p_feat_bad",
                # 27% / 50 都不在 ev_clickup_feature_01（"100+ pre-built automations"）
                # 的 ±5% 容差内，会被判 unverified
                text="ClickUp 自动化覆盖率提升 27%，被 50 家企业采用。",
                claim_ids=["cl_feat_002"],
                evidence_ids=["ev_clickup_feature_01"],
                is_quantitative=True,
                is_soft_conclusion=False,
            )
        ],
    )
    fake = FakeLLM(
        by_section={"sec_features": bad_section},
        entailment_default=EntailmentVerdict(entailed=True, reason="已支撑"),
        # 无 repair_default → 3 轮 repair 全失败
    )
    agent = Reporter(
        llm=fake,
        tracer=NullTracer(),
        evidence_provider=FixtureEvidenceProvider(),
        mock=False,
    )
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    sec = next(s for s in out.draft.sections if s.section_id == "sec_features")
    target = next((p for p in sec.paragraphs if p.paragraph_id == "p_feat_bad"), None)
    assert target is not None
    # 兜底剥号：27% / 50 都消失
    assert "27%" not in target.text
    assert "50" not in target.text
    # 段落降级为 soft_conclusion
    assert target.is_soft_conclusion is True
    assert target.is_quantitative is False
    # 状态 PARTIAL（不再 NEEDS_REWORK）
    assert out.status is AgentStatus.PARTIAL
    assert not any(e.code == "UNVERIFIED_QUANTITY" for e in out.errors)
    fallback_errors = [e for e in out.errors if e.code == "SELF_CORRECT_FALLBACK"]
    assert fallback_errors
    assert out.draft.metadata["forced_fallbacks"] >= 1


# ---------- 19. R-5：self_correct=False 时退化为 R-4 raise 行为 ----------


def test_self_correct_disabled_falls_back_to_post_validate_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_correct=False 时不跑 self-correct loop，UNVERIFIED_QUANTITY 在
    _post_validate 阶段 raise → NEEDS_REWORK（R-4 兜底行为）。"""
    from backend.agents.reporter import FixtureEvidenceProvider

    agent = Reporter(
        mock=True,
        evidence_provider=FixtureEvidenceProvider(),
        self_correct=False,
    )
    inp = load_demo_input(template_id="standard_v1")

    original = agent._build_output

    def poisoned(inp_: ReporterInput, *, allow_llm: bool) -> ReporterOutput:
        out = original(inp_, allow_llm=allow_llm)
        sec = next(s for s in out.draft.sections if s.section_id == "sec_pricing")
        sec.paragraphs.append(
            ReportParagraph(
                paragraph_id="p_poison_27",
                text="ClickUp 自动化覆盖率提升 27%（evidence 里没有这个数字）。",
                claim_ids=["cl_price_001"],
                evidence_ids=["ev_clickup_price_01"],
                is_quantitative=True,
                is_soft_conclusion=False,
            )
        )
        return out

    monkeypatch.setattr(agent, "_build_output", poisoned)
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert out.status is AgentStatus.NEEDS_REWORK
    assert any(e.code == "UNVERIFIED_QUANTITY" for e in out.errors)
    assert out.draft.metadata["repair_attempts"] == 0
    assert out.draft.metadata["forced_fallbacks"] == 0


# ---------- QA feedback → prompt 接通测试 ----------


def test_render_qa_feedback_block_empty_returns_empty_string() -> None:
    """无 feedback / None / 空 dict → 渲染空串（section.md 占位符消失）。"""
    from backend.agents.reporter.agent import _render_qa_feedback_block

    assert _render_qa_feedback_block(None) == ""
    assert _render_qa_feedback_block({}) == ""
    # 字段全空也应跳过，不产生噪音 prompt
    assert (
        _render_qa_feedback_block(
            {"from_verdict_id": "v_1", "issues": [], "must_address": [], "instructions": ""}
        )
        == ""
    )


def test_render_qa_feedback_block_contains_instructions_and_issues() -> None:
    """有 routing reason + must_address + issues 时，全部进 prompt 块。"""
    from backend.agents.reporter.agent import _render_qa_feedback_block

    payload = {
        "from_verdict_id": "v_42",
        "revision": 2,
        "instructions": "重写 pricing 章节，剔除被用户标 disputed 的证据",
        "must_address": ["iss_p1", "iss_p2"],
        "issues": [
            {
                "issue_id": "iss_p1",
                "dimension": "evidence_completeness",
                "severity": "major",
                "location": "report.paragraphs[para_007]",
                "problem": "段落引用 ev_n_pricing_001 已被用户标 disputed",
                "suggested_fix": "改写为定性表述或换证据",
                "target_agent": "reporter",
                "required_inputs": {"avoid_evidence_ids": ["ev_n_pricing_001"]},
            },
            {
                "issue_id": "iss_p2",
                "dimension": "fact_consistency",
                "severity": "critical",
                "location": "report.sections[3].paragraphs[2]",
                "problem": "数字 90% 在引用证据中找不到原值",
                "suggested_fix": "去掉具体数字，改用「显著高于」之类定性词",
                "target_agent": "reporter",
                "required_inputs": {},
            },
        ],
    }
    block = _render_qa_feedback_block(payload)

    # 顶部强标记 + revision
    assert "QA Feedback" in block
    assert "revision 2" in block
    # routing reason
    assert "重写 pricing 章节" in block
    # must_address 全部出现
    assert "iss_p1" in block
    assert "iss_p2" in block
    # issue 详情
    assert "report.paragraphs[para_007]" in block
    assert "report.sections[3].paragraphs[2]" in block
    assert "已被用户标 disputed" in block
    assert "改用「显著高于」" in block
    # required_inputs 落到 Constraints 行
    assert "avoid_evidence_ids" in block
    assert "ev_n_pricing_001" in block
    # 严重程度 + 维度标签
    assert "major" in block
    assert "critical" in block
    assert "evidence_completeness" in block


def test_qa_feedback_reaches_real_llm_section_prompt() -> None:
    """端到端：把 qa_feedback 塞进 ReporterInput，跑真实 LLM 路径，
    断言 FakeLLM 捕获的 user_content 里能看到 issue 文本。

    这是 QA→Reporter 反馈环最关键的接通点：之前 Reporter 只读 ``revision`` 编号
    bump 版本，不把反馈文本送进 LLM；本测保证不再回退。
    """
    from backend.agents.reporter.fixtures import load_demo_input

    captured: list[str] = []

    class CaptureLLM:
        """记录每次 chat 收到的 user_content，再走 FakeLLM 的兜底逻辑。"""

        def chat(self, *, system: str, messages: list[dict], **kwargs: Any) -> Any:
            uc = next((m["content"] for m in messages if m["role"] == "user"), "")
            captured.append(uc)
            # 让 Reporter 失败 fallback heuristic（我们只关心 prompt 是否含反馈）
            raise RuntimeError("capture done")

        def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return [[0.0] * 8 for _ in texts]

    qa_feedback = {
        "from_verdict_id": "v_test",
        "revision": 1,
        "instructions": "用户标了 ev_n_pricing_xyz 为 disputed，重写相关段落",
        "must_address": ["iss_disputed_para_777"],
        "issues": [
            {
                "issue_id": "iss_disputed_para_777",
                "dimension": "evidence_completeness",
                "severity": "major",
                "location": "report.paragraphs[para_777]",
                "problem": "用户 PATCH evidence ev_n_pricing_xyz 标 disputed，本段引用它",
                "suggested_fix": "改写不依赖该 evidence；找替代证据或弱化结论",
                "target_agent": "reporter",
                "required_inputs": {"avoid_evidence_ids": ["ev_n_pricing_xyz"]},
            }
        ],
    }

    inp_base = load_demo_input(template_id="standard_v1")
    inp = inp_base.model_copy(update={"qa_feedback": qa_feedback})

    agent = Reporter(llm=CaptureLLM(), tracer=NullTracer(), self_correct=False)
    agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    assert captured, "Reporter 没调到 LLM，wiring 路径未触达"
    joined = "\n".join(captured)
    # 关键断言：QA 反馈文本必须真的出现在 user prompt 里
    assert "QA Feedback" in joined
    assert "用户标了 ev_n_pricing_xyz" in joined
    assert "iss_disputed_para_777" in joined
    assert "report.paragraphs[para_777]" in joined
    assert "avoid_evidence_ids" in joined
    assert "ev_n_pricing_xyz" in joined


# ---------- 辅助函数 ----------


def _evidence_pool(inp: ReporterInput) -> set[str]:
    pool: set[str] = set()
    for dim in inp.analysis.dimensions.values():
        for c in dim.claims:
            pool.update(c.evidence_ids)
            pool.update(c.counter_evidence_ids)
    return pool


def _claim_pool(inp: ReporterInput) -> set[str]:
    pool: set[str] = set()
    for dim in inp.analysis.dimensions.values():
        for c in dim.claims:
            pool.add(c.claim_id)
    return pool
