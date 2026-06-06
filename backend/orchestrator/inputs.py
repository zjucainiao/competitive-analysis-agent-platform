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
    """从已完成节点 outputs 中收集所有 Extractor profile（key=product 显示名）。"""
    profiles: dict[str, CompetitorProfile] = {}
    for nid, out in outputs.items():
        if not nid.startswith("extract."):
            continue
        profile = getattr(out, "profile", None)
        if profile is not None:
            profiles[profile.basic_info.name] = profile
    return profiles


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
    )


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
