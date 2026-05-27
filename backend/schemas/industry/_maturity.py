"""MaturityScore：行业扩展中频繁使用的单项能力成熟度评分。

提到独立模块以避免与各 *Extension 之间的循环 / 前向引用问题。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MaturityScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_capability: bool
    maturity_level: Literal["none", "basic", "standard", "advanced", "best_in_class"]
    notes: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
