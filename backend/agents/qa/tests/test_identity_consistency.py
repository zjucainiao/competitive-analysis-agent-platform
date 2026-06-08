"""identity_consistency checker + QA 整体判级（抓错产品 → 回 collector 返工）。

- 干净/旧数据（identity_status=unvalidated）→ 满分、无 issue（不误报）。
- 被引用证据 mismatch → major issue、回 collector、带 mismatch_source_urls，维度不及格。
- 纯 ambiguous → minor issue，但维度仍 pass（避免对比页空转）。
- QA 整体：identity 是 core 维度，mismatch → blocking=True，routing 选最上游 collector。
"""
from __future__ import annotations

from backend.agents.qa import QA
from backend.agents.qa.checkers import CheckerContext, IdentityConsistencyChecker
from backend.agents.qa.checkers.identity_consistency import _cited_evidence_ids
from backend.agents.qa.fixtures import load_demo_input, load_evidence_db
from backend.agents.qa.tests.conftest import NullLLM, NullTracer
from backend.schemas import Evidence, QADimension


def _ctx_with_db(evidence_db: dict[str, Evidence]) -> CheckerContext:
    inp = load_demo_input()
    return CheckerContext(
        draft=inp.draft,
        analysis=inp.analysis,
        profiles=inp.profiles,
        evidence_db=evidence_db,
    )


def _one_cited_id(evidence_db: dict[str, Evidence]) -> str:
    inp = load_demo_input()
    ctx = CheckerContext(
        draft=inp.draft, analysis=inp.analysis, profiles=inp.profiles, evidence_db=evidence_db
    )
    cited = _cited_evidence_ids(ctx) & set(evidence_db)
    assert cited, "demo draft 应至少引用一条 evidence_db 里的证据"
    return sorted(cited)[0]


def test_identity_clean_legacy_data_passes() -> None:
    """旧数据全是 unvalidated → 不误报。"""
    db = load_evidence_db()
    result = IdentityConsistencyChecker().run(_ctx_with_db(db))
    assert result.pass_ is True
    assert result.issues == []
    assert result.score == 1.0


def test_identity_mismatch_flags_collector_with_exclude_urls() -> None:
    db = load_evidence_db()
    eid = _one_cited_id(db)
    bad = db[eid].model_copy(
        update={"identity_status": "mismatch", "detected_product_name": "OtherProduct"}
    )
    db[eid] = bad

    result = IdentityConsistencyChecker().run(_ctx_with_db(db))
    assert result.pass_ is False
    majors = [i for i in result.issues if i.severity == "major"]
    assert majors, "mismatch 应产 major issue"
    iss = majors[0]
    assert iss.target_agent == "collector"
    assert iss.dimension is QADimension.IDENTITY_CONSISTENCY
    # 带上跑题 URL，供 collector 重采时排除（P4）
    assert str(bad.source_url) in iss.required_inputs["mismatch_source_urls"]
    assert iss.required_inputs["product"] == bad.product_name


def test_identity_ambiguous_is_minor_and_does_not_fail() -> None:
    db = load_evidence_db()
    eid = _one_cited_id(db)
    db[eid] = db[eid].model_copy(update={"identity_status": "ambiguous"})

    result = IdentityConsistencyChecker().run(_ctx_with_db(db))
    # 纯 ambiguous：维度不因它失败（pass_ 只看 mismatch）
    assert result.pass_ is True
    minors = [i for i in result.issues if i.severity == "minor"]
    assert minors and all(i.target_agent == "collector" for i in minors)


def test_qa_mismatch_blocks_and_routes_to_collector() -> None:
    """端到端 QA：抓错产品 → identity(core) 不及格 → blocking → 最上游 collector。"""
    db = load_evidence_db()
    eid = _one_cited_id(db)
    db[eid] = db[eid].model_copy(
        update={"identity_status": "mismatch", "detected_product_name": "OtherProduct"}
    )
    agent = QA(llm=NullLLM(), tracer=NullTracer(), evidence_db=db)
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    verdict = out.verdict
    idr = verdict.dimension_results[QADimension.IDENTITY_CONSISTENCY]
    assert idr.pass_ is False
    assert verdict.blocking is True
    assert any(r.target_agent == "collector" for r in verdict.routing)
    assert any(
        i.dimension is QADimension.IDENTITY_CONSISTENCY for i in verdict.issues
    )
