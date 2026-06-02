"""跨项目 / 跨 run 的元数据接口。

涵盖：
- 全局指标聚合（``/api/metrics/aggregate``）
- 单项目指标时间序列（``/api/projects/{id}/metrics/timeseries``）
- LLM call 流水（``/api/projects/{id}/llm-calls`` + ``/api/llm-calls``）
- 导出（``/api/projects/{id}/export?format=json|markdown``）
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.deps import get_storage
from backend.observability.llm_call_log import list_calls
from backend.schemas import ProjectStatus, ReporterOutput
from backend.schemas.project import ProjectMetricsSnapshot
from backend.storage import Storage

router = APIRouter(tags=["meta"])


# ============================================================
# 全局指标聚合
# ============================================================


class AggregateMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_count: int
    finished_project_count: int

    avg_accuracy: float
    avg_coverage: float
    avg_edit_rate: float
    total_evidence: int
    total_tokens: int
    total_cost_usd: float
    total_duration_seconds: int
    total_qa_rounds: int
    total_manual_edits: int

    by_status: dict[str, int]
    by_industry: dict[str, int]


@router.get("/metrics/aggregate", response_model=AggregateMetricsResponse)
async def aggregate_metrics(
    storage: Storage = Depends(get_storage),
    since_iso: str | None = Query(default=None, description="ISO8601；只算 created_at ≥ 此值的项目"),
) -> AggregateMetricsResponse:
    """跨项目汇总。"""
    projects = await storage.state_store.list_projects(limit=10000)

    since_dt: datetime | None = None
    if since_iso:
        try:
            since_dt = datetime.fromisoformat(since_iso)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"bad since_iso: {e}") from e

    if since_dt:
        projects = [p for p in projects if p.created_at >= since_dt]

    by_status: dict[str, int] = {}
    by_industry: dict[str, int] = {}
    for p in projects:
        by_status[p.status.value] = by_status.get(p.status.value, 0) + 1
        by_industry[p.industry] = by_industry.get(p.industry, 0) + 1

    with_metrics = [p for p in projects if p.metrics is not None]
    n = len(with_metrics)
    if n == 0:
        return AggregateMetricsResponse(
            project_count=len(projects),
            finished_project_count=0,
            avg_accuracy=0.0,
            avg_coverage=0.0,
            avg_edit_rate=0.0,
            total_evidence=0,
            total_tokens=0,
            total_cost_usd=0.0,
            total_duration_seconds=0,
            total_qa_rounds=0,
            total_manual_edits=0,
            by_status=by_status,
            by_industry=by_industry,
        )

    return AggregateMetricsResponse(
        project_count=len(projects),
        finished_project_count=n,
        avg_accuracy=sum(p.metrics.accuracy for p in with_metrics) / n,
        avg_coverage=sum(p.metrics.coverage for p in with_metrics) / n,
        avg_edit_rate=sum(p.metrics.edit_rate for p in with_metrics) / n,
        total_evidence=sum(p.metrics.evidence_count for p in with_metrics),
        total_tokens=sum(p.metrics.total_tokens for p in with_metrics),
        total_cost_usd=sum(p.metrics.total_cost_usd for p in with_metrics),
        total_duration_seconds=sum(p.metrics.duration_seconds for p in with_metrics),
        total_qa_rounds=sum(p.metrics.qa_round_count for p in with_metrics),
        total_manual_edits=sum(p.metrics.manual_edits for p in with_metrics),
        by_status=by_status,
        by_industry=by_industry,
    )


# ============================================================
# 单项目时间序列
# ============================================================


class MetricsTimeseriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    history: list[ProjectMetricsSnapshot]


@router.get(
    "/projects/{project_id}/metrics/timeseries",
    response_model=MetricsTimeseriesResponse,
)
async def metrics_timeseries(
    project_id: str,
    storage: Storage = Depends(get_storage),
) -> MetricsTimeseriesResponse:
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return MetricsTimeseriesResponse(
        project_id=project_id, history=list(project.metrics_history)
    )


# ============================================================
# LLM call 流水
# ============================================================


class LLMCallsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[dict[str, Any]]
    total: int


@router.get(
    "/projects/{project_id}/llm-calls",
    response_model=LLMCallsResponse,
)
async def project_llm_calls(
    project_id: str,
    node_id: str | None = Query(default=None),
    agent_name: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    storage: Storage = Depends(get_storage),
) -> LLMCallsResponse:
    """单项目的 LLM 调用流水（按节点 / Agent 过滤）。

    数据源是进程内 ring buffer（v1 单进程演示用；多进程 / 重启场景换 OTLP / 存储后端）。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    trace_id = f"trace_{project_id}"
    recs = list_calls(trace_id=trace_id, node_id=node_id, agent_name=agent_name, limit=limit)
    return LLMCallsResponse(calls=[r.to_dict() for r in recs], total=len(recs))


@router.get("/llm-calls", response_model=LLMCallsResponse)
async def all_llm_calls(
    limit: int = Query(default=500, ge=1, le=5000),
) -> LLMCallsResponse:
    """全局 LLM 调用流水（用于 /metrics 仪表盘看实时 token 流速）。"""
    recs = list_calls(limit=limit)
    return LLMCallsResponse(calls=[r.to_dict() for r in recs], total=len(recs))


# ============================================================
# 导出
# ============================================================


@router.get("/projects/{project_id}/export")
async def export_project(
    project_id: str,
    fmt: Literal["json", "markdown", "pdf", "docx"] = Query(
        default="json", alias="format"
    ),
    storage: Storage = Depends(get_storage),
) -> Response:
    """导出项目最终报告 + 完整数据。

    - ``format=json``：完整 state（含 plan / outputs / verdicts / metrics），方便给到老板 / 客户做底层数据
    - ``format=markdown``：渲染好的报告 Markdown，含 evidence 引用附录与数据来源声明
    - ``format=pdf``：基于 markdown 渲染成 PDF（需要安装 ``reportlab``）
    - ``format=docx``：基于 markdown 渲染成 Word docx（需要安装 ``python-docx``）

    PDF / DOCX 依赖在可选 extras 里：``pip install '.[export-pdf-docx]'``。
    缺依赖时返回 503 而非 500，方便前端做兜底。
    """
    project = await storage.state_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    plan = await storage.state_store.get_dag_plan(project_id)
    outputs = await storage.state_store.list_node_outputs(project_id)
    verdicts = await storage.state_store.list_qa_verdicts(project_id)

    if fmt == "json":
        payload = {
            "project": project.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan else None,
            "outputs": {
                nid: out.model_dump(mode="json") for nid, out in outputs.items()
            },
            "verdicts": [v.model_dump(mode="json") for v in verdicts],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{project_id}.json"'
            },
        )

    # markdown 渲染（PDF / DOCX 都基于此做格式转换）
    md = _render_markdown(project_id, project, outputs, verdicts)

    if fmt == "markdown":
        return PlainTextResponse(
            content=md,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="{project_id}.md"'
            },
        )

    if fmt == "pdf":
        try:
            body = _render_pdf(project.project_name, md)
        except ImportError as e:
            raise HTTPException(
                status_code=503,
                detail=(
                    "PDF export requires reportlab: "
                    "`pip install '.[export-pdf-docx]'`"
                ),
            ) from e
        return Response(
            content=body,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{project_id}.pdf"'
            },
        )

    # docx
    try:
        body = _render_docx(project.project_name, md)
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "DOCX export requires python-docx: "
                "`pip install '.[export-pdf-docx]'`"
            ),
        ) from e
    return Response(
        content=body,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}.docx"'
        },
    )


# ----- markdown 渲染 -----


def _render_markdown(project_id, project, outputs, verdicts) -> str:
    """把最终 reporter 输出 + evidence 附录拼成 Markdown 报告。"""
    final_key = "reporter"
    versioned = sorted(
        (k for k in outputs if k.startswith("reporter_v")), reverse=True
    )
    if versioned:
        final_key = versioned[0]
    reporter_out = outputs.get(final_key)

    lines: list[str] = []
    lines.append(f"# {project.project_name}")
    lines.append("")
    lines.append(f"> 竞品分析报告 · 自动生成于 {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 项目 ID | `{project_id}` |")
    lines.append(f"| 目标产品 | {project.target_product} |")
    lines.append(f"| 竞品 | {', '.join(project.competitors)} |")
    lines.append(f"| 行业 | {project.industry} |")
    lines.append(f"| 状态 | {project.status.value} |")
    if project.metrics:
        lines.append(f"| QA accuracy | {project.metrics.accuracy:.2f} |")
        lines.append(f"| Schema 覆盖率 | {project.metrics.coverage:.2f} |")
        lines.append(f"| Evidence 数 | {project.metrics.evidence_count} |")
        lines.append(f"| Token 总量 | {project.metrics.total_tokens} |")
    lines.append("")

    if isinstance(reporter_out, ReporterOutput) and reporter_out.draft:
        draft = reporter_out.draft
        lines.append(f"## 摘要")
        lines.append("")
        lines.append(draft.summary or "_（无摘要）_")
        lines.append("")
        for section in draft.sections:
            lines.append(f"## {section.title}")
            lines.append("")
            for para in section.paragraphs:
                lines.append(para.text)
                if para.evidence_ids:
                    cites = ", ".join(f"^[{eid}]" for eid in para.evidence_ids)
                    lines.append("")
                    lines.append(f"> 引用：{cites}")
                lines.append("")
    else:
        lines.append("_（尚未产出报告草稿）_")
        lines.append("")

    # Evidence 附录
    lines.append("---")
    lines.append("")
    lines.append("## 附录：Evidence 索引")
    lines.append("")
    seen: set[str] = set()
    for nid, out in outputs.items():
        if not nid.startswith("extract."):
            continue
        evs = getattr(out, "evidences", None) or []
        for ev in evs:
            if ev.evidence_id in seen:
                continue
            seen.add(ev.evidence_id)
            disputed = " ⚠️ disputed" if getattr(ev, "disputed", False) else ""
            lines.append(
                f"- `{ev.evidence_id}`{disputed} · {ev.product_name} · "
                f"[{ev.source_type}]({ev.source_url})"
            )
            preview = (ev.content or "").strip()[:200]
            if preview:
                lines.append(f"  > {preview}{'…' if len(ev.content) > 200 else ''}")
    lines.append("")

    # QA verdict 终态
    if verdicts:
        last = verdicts[-1]
        lines.append("## 附录：QA Verdict")
        lines.append("")
        lines.append(f"- overall_status: **{last.overall_status.value}**")
        lines.append(f"- blocking: {last.blocking}")
        for dim, res in (last.dimension_results or {}).items():
            mark = "✓" if res.pass_ else "✗"
            lines.append(f"- {mark} {dim.value}: {res.score:.2f} — {res.notes or ''}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 数据来源声明：本报告基于公开 SaaS 产品官网 / 帮助文档 / 公开评论站采集，"
        "已遵守 robots.txt 与 ToS（详见 [COMPLIANCE.md](docs/COMPLIANCE.md)）；"
        "未含个人隐私信息或非公开内容。"
    )
    return "\n".join(lines)


# ============================================================
# PDF / DOCX 渲染（懒加载依赖；缺依赖时让上层捕 ImportError 返 503）
# ============================================================

import re as _re  # noqa: E402


def _render_pdf(title: str, md_text: str) -> bytes:
    """用 reportlab 把 markdown 渲染成 PDF bytes。

    实现策略：不引重型 markdown→HTML→PDF 流水线（避免 pango/cairo 系统依赖），
    而是直接按行解析 markdown 的最常见结构（#/##/段落/列表/引用块），用 reportlab
    flowables 拼。中文字体使用系统 STHeiti / PingFang（macOS）/ DejaVu（Linux），
    任一可用即可；都不可用时退化到 Helvetica（中文显示为方框）。
    """
    from io import BytesIO

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    font_name = _register_cjk_font(pdfmetrics, TTFont)

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=10.5,
        leading=15,
    )
    h1_style = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName=font_name,
        fontSize=20,
        leading=26,
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=15,
        leading=20,
        spaceBefore=10,
        spaceAfter=6,
    )
    quote_style = ParagraphStyle(
        "Quote",
        parent=body_style,
        leftIndent=12,
        textColor="#666666",
        fontSize=9.5,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        title=title,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    story = []
    for raw_line in md_text.split("\n"):
        line = raw_line.rstrip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        # 分隔线 ---
        if _re.fullmatch(r"-{3,}", line):
            story.append(Spacer(1, 6))
            continue
        # 表格行 | xx | yy | —— 简化处理：当作普通段落
        if line.startswith("# "):
            story.append(Paragraph(_md_inline_to_html(line[2:].strip()), h1_style))
        elif line.startswith("## "):
            story.append(Paragraph(_md_inline_to_html(line[3:].strip()), h2_style))
        elif line.startswith("> "):
            story.append(Paragraph(_md_inline_to_html(line[2:].strip()), quote_style))
        elif line.startswith("- "):
            story.append(
                Paragraph("• " + _md_inline_to_html(line[2:].strip()), body_style)
            )
        else:
            story.append(Paragraph(_md_inline_to_html(line), body_style))

    doc.build(story)
    return buf.getvalue()


def _register_cjk_font(pdfmetrics, TTFont) -> str:
    """注册一个支持中文的字体并返回 fontName。任一系统候选可用即注册成功。"""
    candidates = [
        # macOS
        ("STHeiti", "/System/Library/Fonts/STHeiti Light.ttc"),
        ("PingFang", "/System/Library/Fonts/PingFang.ttc"),
        # Linux 常见
        ("NotoSansCJK", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    import os

    for name, path in candidates:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            return name
        except Exception:  # noqa: BLE001
            continue
    return "Helvetica"  # 兜底（中文会变方框，但不报错）


def _md_inline_to_html(text: str) -> str:
    """reportlab Paragraph 支持的 mini HTML 子集：**bold** → <b>；`code` → <font>。

    顺便把 < 和 & 转义掉，避免 reportlab 把它当成 XML 出错。
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = _re.sub(
        r"`([^`]+?)`", r'<font name="Courier" color="#a40000">\1</font>', text
    )
    # 简单链接 [text](url) → text（reportlab 链接处理不稳，直接降级）
    text = _re.sub(r"\[(.+?)\]\(([^)]+?)\)", r"\1", text)
    return text


def _render_docx(title: str, md_text: str) -> bytes:
    """用 python-docx 把 markdown 渲染成 docx bytes。"""
    from io import BytesIO

    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)

    for raw_line in md_text.split("\n"):
        line = raw_line.rstrip()
        if not line:
            doc.add_paragraph("")
            continue
        if _re.fullmatch(r"-{3,}", line):
            doc.add_paragraph("———————————————")
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("> "):
            p = doc.add_paragraph(line[2:].strip())
            p.style = doc.styles["Quote"] if "Quote" in doc.styles else style
        elif line.startswith("- "):
            doc.add_paragraph(line[2:].strip(), style="List Bullet")
        else:
            doc.add_paragraph(_strip_md_inline(line))

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _strip_md_inline(text: str) -> str:
    """docx 渲染不解析行内 markdown，直接把 **、` 等去掉只保留可读文本。"""
    text = _re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = _re.sub(r"`([^`]+?)`", r"\1", text)
    text = _re.sub(r"\[(.+?)\]\(([^)]+?)\)", r"\1", text)
    return text
