"""跨境电商 SaaS 行业扩展 Schema。

v1 占接口位，证明可扩展性；不在演示中实际抽取。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._maturity import MaturityScore


class CrossBorderEcommerceSaasExtension(BaseModel):
    """跨境电商场景扩展字段。"""

    model_config = ConfigDict(extra="forbid")

    industry_id: Literal["cross_border_ecommerce_saas"] = "cross_border_ecommerce_saas"

    store_builder: MaturityScore | None = None
    payment_support: MaturityScore | None = None
    logistics_support: MaturityScore | None = None
    multi_language: MaturityScore | None = None
    multi_currency: MaturityScore | None = None
    plugin_ecosystem: MaturityScore | None = None
    marketing_tools: MaturityScore | None = None
    order_fulfillment: MaturityScore | None = None
    tax_compliance: MaturityScore | None = None

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)
