"""协作办公 / 项目管理 SaaS 行业扩展 Schema。

v1 实际抽取的行业。覆盖 Notion / ClickUp / Asana / Trello / Lark 等类型产品。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._maturity import MaturityScore


class CollaborationSaasExtension(BaseModel):
    """协作办公场景扩展字段。"""

    model_config = ConfigDict(extra="forbid")

    industry_id: Literal["collaboration_saas"] = "collaboration_saas"

    task_management: MaturityScore | None = None
    kanban_view: MaturityScore | None = None
    calendar_view: MaturityScore | None = None
    gantt_view: MaturityScore | None = None
    document_collaboration: MaturityScore | None = None
    workflow_automation: MaturityScore | None = None
    knowledge_base: MaturityScore | None = None
    team_permission: MaturityScore | None = None
    third_party_integration: MaturityScore | None = None
    mobile_support: MaturityScore | None = None
    realtime_editing: MaturityScore | None = None
    ai_assistance: MaturityScore | None = None

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)
