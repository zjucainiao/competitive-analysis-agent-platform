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

IndustryExtensionUnion = Annotated[
    CollaborationSaasExtension | CrmSaasExtension | CrossBorderEcommerceSaasExtension,
    Field(discriminator="industry_id"),
]
"""按 industry_id 区分的行业扩展联合类型。

v1 实际抽取仅落地协作办公（collaboration_saas），
其他扩展占接口位，证明可扩展性。
"""


__all__ = [
    "CollaborationSaasExtension",
    "CrmSaasExtension",
    "CrossBorderEcommerceSaasExtension",
    "IndustryExtensionUnion",
    "MaturityScore",
]
