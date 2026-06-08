"""参数化 agent input 构造器。

从 Executor._build_* 抽出,去掉 DAGNode 耦合:既给旧 Executor(适配 node)
复用,也给新原生节点(从 RunState 取参)复用。纯函数,无副作用。
"""
from __future__ import annotations

from ulid import ULID

from backend.schemas import (
    AnalystInput,
    CollectorInput,
    CompetitorProfile,
    ExtractorInput,
    Project,
    QAInput,
    QAVerdict,
    ReporterInput,
    AgentOutputBase,
)
from backend.schemas.evidence import CollectDimension


class BuildInputError(ValueError):
    """无法装配 Agent input。"""


def new_span_id() -> str:
    return f"span_{ULID()}"


def _industry_schema_id(project: Project) -> str:
    major = project.industry_schema_version.split(".", 1)[0]
    return f"{project.industry}_v{major}"


def profiles_from_outputs(outputs: dict[str, AgentOutputBase]) -> dict[str, CompetitorProfile]:
    """从已完成节点 outputs 中收集所有 Extractor profile（key=product 显示名）。

    版本化 key 下同一产品可能同时存在 ``extract.X``(v1) 与 ``extract.X_v2``(返工)，
    先经 ``latest_outputs`` 收敛到每产品最新轮，避免把 v1/v2 同产品重复计入或被
    非确定的遍历顺序覆盖成旧版。
    """
    from backend.orchestrator.run_state import latest_outputs

    profiles: dict[str, CompetitorProfile] = {}
    for nid, out in latest_outputs(outputs).items():
        if not nid.startswith("extract."):
            continue
        profile = getattr(out, "profile", None)
        if profile is not None:
            profiles[profile.basic_info.name] = profile
    return profiles


def _exclude_urls_from_feedback(qa_feedback: dict | None) -> list[str]:
    """从 qa_feedback 的 identity issue 提取需排除的跑题来源 URL（P4 收敛）。

    QAFeedback.issues[*].required_inputs.mismatch_source_urls 由
    IdentityConsistencyChecker 写入；重采时把这些页面直接排除，避免又抓回来。
    """
    if not qa_feedback:
        return []
    urls: set[str] = set()
    for issue in qa_feedback.get("issues", []) or []:
        ri = (issue or {}).get("required_inputs") or {}
        for u in ri.get("mismatch_source_urls", []) or []:
            if isinstance(u, str) and u.strip():
                urls.add(u)
    return sorted(urls)


def build_collector_input(
    project: Project,
    *,
    trace_id: str,
    product: str,
    official_url: str | None,
    dims: list[str],
    qa_feedback: dict | None,
) -> CollectorInput:
    if not product:
        raise BuildInputError("collector: empty product")
    if not dims:
        raise BuildInputError(f"collector[{product}]: empty collect_dimensions")
    return CollectorInput(
        task_id=f"collect.{product}",
        project_id=project.project_id,
        trace_id=trace_id,
        span_id=new_span_id(),
        product_name=product,
        official_url=official_url,
        industry=project.industry,
        dimensions=[CollectDimension(d) for d in dims],
        constraints=project.collect_constraints,
        qa_feedback=qa_feedback,
        exclude_source_urls=_exclude_urls_from_feedback(qa_feedback),
    )


def build_extractor_input(
    project: Project,
    *,
    trace_id: str,
    product: str,
    collector_output: AgentOutputBase,
    qa_feedback: dict | None,
) -> ExtractorInput:
    raw_sources = getattr(collector_output, "raw_sources", None)
    if raw_sources is None:
        raise BuildInputError(f"extractor[{product}]: upstream has no raw_sources")
    return ExtractorInput(
        task_id=f"extract.{product}",
        project_id=project.project_id,
        trace_id=trace_id,
        span_id=new_span_id(),
        product_name=product,
        industry_schema_id=_industry_schema_id(project),
        raw_sources=raw_sources,
        qa_feedback=qa_feedback,
    )


def build_analyst_input(
    project: Project,
    *,
    trace_id: str,
    outputs: dict[str, AgentOutputBase],
    qa_feedback: dict | None,
) -> AnalystInput:
    profiles = profiles_from_outputs(outputs)
    if not profiles:
        raise BuildInputError("analyst: no extractor profiles available")
    return AnalystInput(
        task_id="analyst",
        project_id=project.project_id,
        trace_id=trace_id,
        span_id=new_span_id(),
        target_product=project.target_product,
        competitors=list(project.competitors),
        profiles=profiles,
        dimensions=list(project.analysis_dimensions),
        qa_feedback=qa_feedback,
    )


def build_reporter_input(
    project: Project,
    *,
    trace_id: str,
    analyst_output: AgentOutputBase,
    qa_feedback: dict | None,
    prior_draft: "ReportDraft | None" = None,
) -> ReporterInput:
    return ReporterInput(
        task_id="reporter",
        project_id=project.project_id,
        trace_id=trace_id,
        span_id=new_span_id(),
        project_name=project.project_name,
        analysis=analyst_output.result,  # type: ignore[attr-defined]
        template_id=project.report_template_id,
        output_format="markdown",
        target_audience=project.target_audience,
        qa_feedback=qa_feedback,
        prior_draft=prior_draft,
    )


def _upstream_statuses(outputs: dict[str, AgentOutputBase]) -> dict[str, str]:
    """从 collect.* / extract.* outputs 汇总各 Agent 的「最差」自评状态。

    供 QA 把上游自评(needs_rework)接入判级（escalate_by_self_status）。按
    failed>needs_rework>partial>success 的劣度取最差，只要有一个产品的 collector
    自评 needs_rework，collector 整体即记为 needs_rework。
    """
    from backend.orchestrator.run_state import latest_outputs

    rank = {"failed": 3, "needs_rework": 2, "partial": 1, "success": 0}
    agg: dict[str, str] = {}
    for nid, out in latest_outputs(outputs).items():
        if nid.startswith("collect."):
            agent = "collector"
        elif nid.startswith("extract."):
            agent = "extractor"
        else:
            continue
        status = getattr(getattr(out, "status", None), "value", None)
        if not status:
            continue
        if agent not in agg or rank.get(status, 0) > rank.get(agg[agent], 0):
            agg[agent] = status
    return agg


def build_qa_input(
    project: Project,
    *,
    trace_id: str,
    reporter_output: AgentOutputBase,
    analyst_output: AgentOutputBase,
    outputs: dict[str, AgentOutputBase],
    prior_verdicts: list[QAVerdict],
) -> QAInput:
    return QAInput(
        task_id="qa",
        project_id=project.project_id,
        trace_id=trace_id,
        span_id=new_span_id(),
        draft=reporter_output.draft,  # type: ignore[attr-defined]
        analysis=analyst_output.result,  # type: ignore[attr-defined]
        profiles=profiles_from_outputs(outputs),
        evidence_store_handle=None,
        prior_verdicts=prior_verdicts,
        upstream_statuses=_upstream_statuses(outputs),
    )


__all__ = [
    "BuildInputError",
    "new_span_id",
    "build_collector_input",
    "build_extractor_input",
    "build_analyst_input",
    "build_reporter_input",
    "build_qa_input",
    "profiles_from_outputs",
]
