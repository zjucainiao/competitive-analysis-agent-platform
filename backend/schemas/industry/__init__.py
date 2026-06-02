"""行业扩展 Schema 注册表。

新增行业 = 新增一个 *Extension 模型 + 加入 IndustryExtensionUnion。
所有扩展用 `industry_id` 字段作为 discriminator。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ._maturity import MaturityScore
from .collab_saas import CollaborationSaasExtension
from .crm_saas import CrmSaasExtension
from .cross_border import CrossBorderEcommerceSaasExtension
from .edu_saas import EduSaasExtension

IndustryExtensionUnion = Annotated[
    CollaborationSaasExtension
    | CrmSaasExtension
    | CrossBorderEcommerceSaasExtension
    | EduSaasExtension,
    Field(discriminator="industry_id"),
]
"""按 industry_id 区分的行业扩展联合类型。

v1 实际抽取主要落地协作办公（collaboration_saas）；CRM / 跨境电商 / 教育 SaaS
schema 完整 + 模板可加载，证明可扩展性。新行业 = 加一个 *Extension + 模板。
"""


__all__ = [
    "CollaborationSaasExtension",
    "CrmSaasExtension",
    "CrossBorderEcommerceSaasExtension",
    "EduSaasExtension",
    "IndustryExtensionUnion",
    "MaturityScore",
]
