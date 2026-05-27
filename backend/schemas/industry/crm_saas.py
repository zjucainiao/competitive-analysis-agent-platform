"""CRM SaaS 行业扩展 Schema。

v1 占接口位，证明可扩展性；不在演示中实际抽取。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._maturity import MaturityScore


class CrmSaasExtension(BaseModel):
    """CRM 场景扩展字段。"""

    model_config = ConfigDict(extra="forbid")

    industry_id: Literal["crm_saas"] = "crm_saas"

    lead_management: MaturityScore | None = None
    customer_lifecycle: MaturityScore | None = None
    sales_pipeline: MaturityScore | None = None
    sales_automation: MaturityScore | None = None
    customer_segmentation: MaturityScore | None = None
    reporting_dashboard: MaturityScore | None = None
    marketing_integration: MaturityScore | None = None
    customer_service_integration: MaturityScore | None = None
    mobile_support: MaturityScore | None = None

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)
