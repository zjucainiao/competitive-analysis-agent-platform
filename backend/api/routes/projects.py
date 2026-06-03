"""项目 CRUD 路由。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from ulid import ULID

from backend.api.deps import get_storage
from backend.api.schemas import ProjectCreateRequest, ProjectListResponse
from backend.schemas import AnalysisMode, Project, ProjectStatus
from backend.storage import Storage

router = APIRouter(tags=["projects"])


@router.post("/projects", status_code=status.HTTP_201_CREATED, response_model=Project)
async def create_project(
    req: ProjectCreateRequest,
    storage: Storage = Depends(get_storage),
) -> Project:
    # ----- mode 校验 / 自动派生 -----
    # 关键设计：single_research 模式不再硬剔对比类维度。功能 / 定价 / SWOT /
    # 差异化对单产品本身仍然有意义（自身能力速览 / 定价档位 / 自我 SW 评估 /
    # 差异化定位）。Analyst 内部对这些维度有 competitors=[] 单产品分支；
    # Reporter ``single_research_v1`` 模板用调研基调标题。
    competitors = list(req.competitors)
    analysis_dimensions = list(req.analysis_dimensions)
    report_template_id = req.report_template_id

    if req.analysis_mode == AnalysisMode.COMPETITIVE_COMPARE:
        if not competitors:
            raise HTTPException(
                status_code=400,
                detail=(
                    "analysis_mode=competitive_compare requires ≥1 competitor; "
                    "use 'single_research' for solo product研究 or 'auto_discover' "
                    "to let the system fill competitors first."
                ),
            )
    elif req.analysis_mode == AnalysisMode.SINGLE_RESEARCH:
        # 单产品调研：忽略用户传的 competitors（即使非空也清掉）；
        # dimensions 由用户自由选；默认模板换 single_research_v1（调研基调）。
        competitors = []
        if report_template_id == "standard_v1":
            report_template_id = "single_research_v1"
    elif req.analysis_mode == AnalysisMode.AUTO_DISCOVER:
        # 由前端在 POST /api/discover-competitors 后把候选填进 competitors 再创建；
        # 这里仍允许 competitors=[] 但 plan 阶段会因为 0 产品而失败 —— 给出友好提示
        if not competitors:
            raise HTTPException(
                status_code=400,
                detail=(
                    "analysis_mode=auto_discover but competitors=[]; please call "
                    "POST /api/discover-competitors first and submit the result."
                ),
            )

    project = Project(
        project_id=f"proj_{ULID()}",
        project_name=req.project_name,
        owner=req.owner,
        created_at=datetime.now(timezone.utc),
        target_product=req.target_product,
        competitors=competitors,
        analysis_mode=req.analysis_mode,
        industry=req.industry,
        industry_schema_version=req.industry_schema_version,
        analysis_dimensions=analysis_dimensions,
        report_template_id=report_template_id,
        target_audience=req.target_audience,
        mode=req.mode,
        collect_constraints=req.collect_constraints,
        status=ProjectStatus.DRAFT,
    )
    await storage.state_store.save_project(project)
    return project


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(
    storage: Storage = Depends(get_storage),
    owner: str | None = None,
    project_status: ProjectStatus | None = None,
    include_archived: bool = False,
    include_deleted: bool = False,
) -> ProjectListResponse:
    """列项目。

    默认隐藏 ARCHIVED + DELETED；要看回收站传 ``include_archived=true``，
    要看彻底删除（30 天保留期）传 ``include_deleted=true``。
    """
    items = await storage.state_store.list_projects(
        owner=owner, status=project_status
    )
    if not include_archived:
        items = [p for p in items if p.status != ProjectStatus.ARCHIVED]
    if not include_deleted:
        items = [p for p in items if p.status != ProjectStatus.DELETED]
    return ProjectListResponse(projects=items)


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(
    project_id: str,
    storage: Storage = Depends(get_storage),
) -> Project:
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id!r} not found",
        )
    return project


@router.post("/projects/{project_id}/archive", response_model=Project)
async def archive_project(
    project_id: str,
    storage: Storage = Depends(get_storage),
) -> Project:
    """归档：从列表默认视图隐藏，但保留所有数据。"""
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    updated = project.model_copy(
        update={
            "status": ProjectStatus.ARCHIVED,
            "archived_at": datetime.now(timezone.utc),
        }
    )
    await storage.state_store.save_project(updated)
    return updated


@router.post("/projects/{project_id}/restore", response_model=Project)
async def restore_project(
    project_id: str,
    storage: Storage = Depends(get_storage),
) -> Project:
    """从归档 / 回收站恢复。状态置回 DONE 或 DRAFT（根据是否有 metrics 推断）。"""
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    if project.status not in (ProjectStatus.ARCHIVED, ProjectStatus.DELETED):
        raise HTTPException(
            status_code=400,
            detail=f"project {project_id!r} not archived/deleted (status={project.status.value})",
        )
    new_status = ProjectStatus.DONE if project.metrics is not None else ProjectStatus.DRAFT
    updated = project.model_copy(
        update={"status": new_status, "archived_at": None, "deleted_at": None}
    )
    await storage.state_store.save_project(updated)
    return updated


@router.delete("/projects/{project_id}", response_model=Project)
async def delete_project(
    project_id: str,
    storage: Storage = Depends(get_storage),
) -> Project:
    """软删：进回收站。30 天保留期由外部 cron 真删（v1 不实施 cron）。"""
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    updated = project.model_copy(
        update={
            "status": ProjectStatus.DELETED,
            "deleted_at": datetime.now(timezone.utc),
        }
    )
    await storage.state_store.save_project(updated)
    return updated
