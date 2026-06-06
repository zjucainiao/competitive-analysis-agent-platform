"""Evidence 操作接口。

支持两种模式：
- 默认（``auto_rework=false``）：仅标 disputed，前端可显式调
  ``POST /api/projects/{id}/nodes/reporter/retry`` 重跑
- ``auto_rework=true``：标 disputed 后服务端自动：
  1. 在最新 reporter draft 里定位引用该 evidence 的段落
  2. 合成一份 ``QAVerdict``（routing → reporter）落 storage
  3. 调 ``FeedbackRouter`` 派生 ``reporter_v{n+1}`` 节点并把下游重置为 PENDING
  4. 若项目当前未在跑，立刻起后台 run 任务

服务端做法（标 disputed 部分始终执行）：
1. 遍历 project 所有 ``extract.*`` outputs 的 evidences
2. 找到 ``evidence_id`` 匹配的那条
3. 标 disputed=True + reason，回写 ExtractorOutput
4. metrics.manual_edits +1（disputed 也算人工介入）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from backend.api.deps import get_orchestrator, get_owned_project, get_storage
from backend.orchestrator import Orchestrator
from backend.orchestrator.orchestrator import _apply_feedback_outcome
from backend.schemas import (
    Evidence,
    ExtractorOutput,
    Project,
    ProjectMetrics,
    ProjectStatus,
    QADimension,
    QADimensionResult,
    QAIssue,
    QARouting,
    QAStatus,
    QAVerdict,
    ReporterOutput,
)
from backend.storage import Storage

router = APIRouter(tags=["evidence"])

_log = logging.getLogger(__name__)


class EvidenceDisputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disputed: bool = True
    reason: str | None = Field(default=None, max_length=500)


class EvidenceDisputeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    disputed: bool
    located_in_node: str
    manual_edits: int
    auto_rework_triggered: bool = False
    rework_verdict_id: str | None = None
    rework_new_node_ids: list[str] = Field(default_factory=list)
    affected_paragraph_ids: list[str] = Field(default_factory=list)


@router.patch(
    "/projects/{project_id}/evidence/{evidence_id}",
    response_model=EvidenceDisputeResponse,
)
async def patch_evidence(
    project_id: str,
    evidence_id: str,
    req: EvidenceDisputeRequest,
    request: Request,
    storage: Storage = Depends(get_storage),
    orch: Orchestrator = Depends(get_orchestrator),
    auto_rework: bool = Query(
        default=False,
        description="True 时自动派生 reporter_v{n+1} 节点 + 起后台 run",
    ),
    project: Project = Depends(get_owned_project),
) -> EvidenceDisputeResponse:

    outputs = await storage.state_store.list_node_outputs(project_id)

    target_node_id: str | None = None
    updated_output: ExtractorOutput | None = None
    for nid, out in outputs.items():
        if not isinstance(out, ExtractorOutput):
            continue
        new_evidences: list[Evidence] = []
        hit = False
        for ev in out.evidences or []:
            if ev.evidence_id == evidence_id:
                hit = True
                new_evidences.append(ev.model_copy(update={"disputed": req.disputed}))
            else:
                new_evidences.append(ev)
        if hit:
            target_node_id = nid
            updated_output = out.model_copy(update={"evidences": new_evidences})
            break

    if target_node_id is None or updated_output is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"evidence {evidence_id!r} not found in project {project_id!r}",
        )

    await storage.state_store.save_node_output(project_id, target_node_id, updated_output)

    # manual_edits +1，并按最新报告段落数重算 edit_rate（与 reports.py PATCH 路径
    # 口径一致：edit_rate = manual_edits / total_paragraphs，[0,1] 截顶）。
    metrics = project.metrics or ProjectMetrics()
    manual_edits = metrics.manual_edits + 1
    total_paragraphs = _latest_report_total_paragraphs(outputs)
    edit_rate = (
        min(manual_edits / total_paragraphs, 1.0)
        if total_paragraphs > 0
        else metrics.edit_rate
    )
    new_metrics = metrics.model_copy(
        update={"manual_edits": manual_edits, "edit_rate": edit_rate}
    )
    await storage.state_store.save_project(project.model_copy(update={"metrics": new_metrics}))

    # ----- 自动联动重审 -----
    if not (auto_rework and req.disputed):
        return EvidenceDisputeResponse(
            evidence_id=evidence_id,
            disputed=req.disputed,
            located_in_node=target_node_id,
            manual_edits=new_metrics.manual_edits,
        )

    # 1. 定位 reporter 最新输出里引用此 evidence 的段落
    affected_paragraph_ids = _find_paragraphs_citing(outputs, evidence_id)

    # 2. 合成 QAVerdict（routing → reporter）
    verdict = _synthesize_disputed_verdict(
        evidence_id=evidence_id,
        reason=req.reason or "evidence marked disputed by user",
        affected_paragraph_ids=affected_paragraph_ids,
    )
    await storage.state_store.save_qa_verdict(project_id, verdict)

    # 3. 调 FeedbackRouter 派生 reporter_v{n+1}
    plan = await storage.state_store.get_dag_plan(project_id)
    if plan is None:
        raise HTTPException(
            status_code=409,
            detail="auto_rework requested but project has no DAGPlan",
        )

    outcome = orch.feedback_router.apply(verdict=verdict, plan=plan, qa_round_count=0)
    if outcome.aborted:
        # 触顶 / 无匹配 target —— 直接放弃自动重审，标准 disputed 路径已经走完
        return EvidenceDisputeResponse(
            evidence_id=evidence_id,
            disputed=req.disputed,
            located_in_node=target_node_id,
            manual_edits=new_metrics.manual_edits,
            auto_rework_triggered=False,
            rework_verdict_id=verdict.verdict_id,
            affected_paragraph_ids=affected_paragraph_ids,
        )

    new_plan = _apply_feedback_outcome(plan, outcome)
    await storage.state_store.save_dag_plan(new_plan)

    # 4. 自动起 run 任务（若当前没在跑）
    running_tasks: dict[str, asyncio.Task] = request.app.state.running_tasks
    triggered = False
    existing = running_tasks.get(project_id)
    if existing is None or existing.done():
        latest_project = await storage.state_store.get_project(project_id)
        run_project = latest_project or project

        async def _resume_in_bg() -> None:
            try:
                async for _ in orch.run(new_plan, run_project):
                    pass
                await storage.state_store.update_project_status(
                    project_id, ProjectStatus.DONE
                )
            except Exception:  # noqa: BLE001
                _log.exception(
                    "evidence-auto-rework run failed for project=%s", project_id
                )
                await storage.state_store.update_project_status(
                    project_id, ProjectStatus.FAILED
                )

        await storage.state_store.update_project_status(
            project_id, ProjectStatus.RUNNING
        )
        task = asyncio.create_task(
            _resume_in_bg(), name=f"evidence-rework-{project_id}-{ULID()}"
        )
        running_tasks[project_id] = task
        triggered = True

    return EvidenceDisputeResponse(
        evidence_id=evidence_id,
        disputed=req.disputed,
        located_in_node=target_node_id,
        manual_edits=new_metrics.manual_edits,
        auto_rework_triggered=triggered,
        rework_verdict_id=verdict.verdict_id,
        rework_new_node_ids=[n.node_id for n in outcome.new_nodes],
        affected_paragraph_ids=affected_paragraph_ids,
    )


# ============================================================
# 辅助
# ============================================================


def _latest_report_total_paragraphs(outputs: dict) -> int:
    """最新 reporter draft 的段落总数（把 manual_edits 换算成 edit_rate 用）。"""
    versioned = sorted(
        (k for k in outputs if k.startswith("reporter_v")), reverse=True
    )
    final_key = versioned[0] if versioned else "reporter"
    reporter_out = outputs.get(final_key)
    if not isinstance(reporter_out, ReporterOutput) or reporter_out.draft is None:
        return 0
    return sum(len(s.paragraphs) for s in reporter_out.draft.sections)


def _find_paragraphs_citing(
    outputs: dict, evidence_id: str
) -> list[str]:
    """从 outputs 里最新 reporter draft 找引用 evidence_id 的段落 id 列表。"""
    versioned = sorted(
        (k for k in outputs if k.startswith("reporter_v")), reverse=True
    )
    final_key = versioned[0] if versioned else "reporter"
    reporter_out = outputs.get(final_key)
    if not isinstance(reporter_out, ReporterOutput) or reporter_out.draft is None:
        return []
    hits: list[str] = []
    for section in reporter_out.draft.sections:
        for para in section.paragraphs:
            if evidence_id in (para.evidence_ids or []):
                hits.append(para.paragraph_id)
    return hits


def _synthesize_disputed_verdict(
    *,
    evidence_id: str,
    reason: str,
    affected_paragraph_ids: list[str],
) -> QAVerdict:
    """合成一份「用户标 disputed 触发」的 QAVerdict，路由到 reporter。

    必须保留 dimension_results / issues 字段语义，让 FeedbackRouter 能正常处理。
    """
    verdict_id = f"v_disputed_{ULID()}"

    # 每个受影响段落生成一条 issue（target_agent=reporter）
    issues: list[QAIssue] = []
    must_address: list[str] = []
    if affected_paragraph_ids:
        for pid in affected_paragraph_ids:
            iid = f"iss_disputed_{pid}"
            issues.append(
                QAIssue(
                    issue_id=iid,
                    dimension=QADimension.EVIDENCE_COMPLETENESS,
                    severity="major",
                    location=f"report.paragraphs[{pid}]",
                    problem=(
                        f"User disputed evidence {evidence_id!r}; paragraph cites it. "
                        f"Reason: {reason}"
                    ),
                    suggested_fix=(
                        f"Re-draft this paragraph without relying on evidence "
                        f"{evidence_id!r}. Find alternative evidence or weaken "
                        f"the claim."
                    ),
                    target_agent="reporter",
                    required_inputs={"avoid_evidence_ids": [evidence_id]},
                )
            )
            must_address.append(iid)
    else:
        # 没有具体段落引用：也仍然要求 reporter 整体复审，避免后续段落引入
        iid = f"iss_disputed_global_{evidence_id}"
        issues.append(
            QAIssue(
                issue_id=iid,
                dimension=QADimension.EVIDENCE_COMPLETENESS,
                severity="minor",
                location="report.global",
                problem=(
                    f"User disputed evidence {evidence_id!r}; no current paragraph "
                    f"cites it but future revisions should avoid it. Reason: {reason}"
                ),
                suggested_fix=f"Treat {evidence_id!r} as unreliable in any rewrite.",
                target_agent="reporter",
                required_inputs={"avoid_evidence_ids": [evidence_id]},
            )
        )
        must_address.append(iid)

    return QAVerdict(
        verdict_id=verdict_id,
        overall_status=QAStatus.NEEDS_REVISION,
        dimension_results={
            QADimension.EVIDENCE_COMPLETENESS: QADimensionResult(
                dimension=QADimension.EVIDENCE_COMPLETENESS,
                score=0.5,
                pass_=False,  # populate_by_name=True，直接用字段名
                notes=f"user-disputed evidence {evidence_id!r}",
            ),
        },
        issues=issues,
        routing=[
            QARouting(
                target_agent="reporter",
                reason=(
                    f"User marked evidence {evidence_id!r} as disputed; rerun "
                    f"reporter to avoid citing it."
                ),
                payload={
                    "must_address": must_address,
                    "avoid_evidence_ids": [evidence_id],
                },
            ),
        ],
        blocking=True,
    )
