"""报告人工修改接口。

提供 PATCH 单段落能力，配合前端"人工编辑"按钮 / 评分点 § 14（交互流畅）
与 § 15（人工修正率指标）。

设计：
- 段落定位：``(project_id, report_id, paragraph_id)``；report_id 是 ReporterOutput
  对应节点 id（``reporter`` / ``reporter_v2`` / ...）。
- 修改仅落到该节点 output 的 ReportDraft.paragraphs，不重跑 Agent。
- 每次 PATCH 累加 ``Project.metrics.manual_edits``，重算 ``edit_rate``：
  ``edit_rate = manual_edits / total_paragraphs``，total_paragraphs 取被改报告
  的当前段落总数。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.deps import get_owned_project, get_storage
from backend.schemas import (
    Project,
    ProjectMetrics,
    ReporterOutput,
    ReportParagraph,
)
from backend.storage import Storage

router = APIRouter(tags=["reports"])


class ParagraphPatchRequest(BaseModel):
    """PATCH body：只允许改文本和软结论标记，evidence_ids / claim_ids 不动。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    is_soft_conclusion: bool | None = None
    is_quantitative: bool | None = None


class ParagraphPatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph: ReportParagraph
    report_node_id: str
    manual_edits: int
    edit_rate: float
    status: Literal["ok"] = "ok"


@router.patch(
    "/projects/{project_id}/reports/{report_node_id}/paragraphs/{paragraph_id}",
    response_model=ParagraphPatchResponse,
)
async def patch_paragraph(
    project_id: str,
    report_node_id: str,
    paragraph_id: str,
    req: ParagraphPatchRequest,
    storage: Storage = Depends(get_storage),
    project: Project = Depends(get_owned_project),
) -> ParagraphPatchResponse:
    if not report_node_id.startswith("reporter"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{report_node_id!r} is not a reporter node id "
                "(expected 'reporter' / 'reporter_v2' / ...)"
            ),
        )

    output = await storage.state_store.get_node_output(project_id, report_node_id)
    if output is None or not isinstance(output, ReporterOutput):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"reporter output {report_node_id!r} not found",
        )

    new_output, updated_para, total_paragraphs = _apply_patch(output, paragraph_id, req)
    if updated_para is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"paragraph {paragraph_id!r} not found in {report_node_id!r}",
        )

    await storage.state_store.save_node_output(project_id, report_node_id, new_output)

    # 维护 Project.metrics.manual_edits + edit_rate
    metrics = project.metrics or ProjectMetrics()
    manual_edits = metrics.manual_edits + 1
    edit_rate = manual_edits / total_paragraphs if total_paragraphs > 0 else 0.0
    # ProjectMetrics.edit_rate 是 [0,1] 约束，截顶
    edit_rate = min(edit_rate, 1.0)
    new_metrics = metrics.model_copy(
        update={"manual_edits": manual_edits, "edit_rate": edit_rate}
    )
    updated_project = project.model_copy(update={"metrics": new_metrics})
    await storage.state_store.save_project(updated_project)

    return ParagraphPatchResponse(
        paragraph=updated_para,
        report_node_id=report_node_id,
        manual_edits=manual_edits,
        edit_rate=edit_rate,
    )


# ---------- 内部 ----------


def _apply_patch(
    output: ReporterOutput,
    paragraph_id: str,
    req: ParagraphPatchRequest,
) -> tuple[ReporterOutput, ReportParagraph | None, int]:
    """返回 (新 output, 修改后的段落 or None, 报告段落总数)。"""
    draft = output.draft
    found_paragraph: ReportParagraph | None = None
    new_sections = []
    total_paragraphs = 0

    for section in draft.sections:
        new_paragraphs = []
        for para in section.paragraphs:
            total_paragraphs += 1
            if para.paragraph_id == paragraph_id:
                updates = {"text": req.text}
                if req.is_soft_conclusion is not None:
                    updates["is_soft_conclusion"] = req.is_soft_conclusion
                if req.is_quantitative is not None:
                    updates["is_quantitative"] = req.is_quantitative
                new_para = para.model_copy(update=updates)
                found_paragraph = new_para
                new_paragraphs.append(new_para)
            else:
                new_paragraphs.append(para)
        new_sections.append(section.model_copy(update={"paragraphs": new_paragraphs}))

    if found_paragraph is None:
        return output, None, total_paragraphs

    # ReportDraft.version 是 ≥1 整数；只有 Reporter 自身重做时 bump，人工编辑
    # 不动它（保持版本号语义清晰：版本号 = Agent 生产次数）。
    new_draft = draft.model_copy(update={"sections": new_sections})
    new_output = output.model_copy(update={"draft": new_draft})
    return new_output, found_paragraph, total_paragraphs
