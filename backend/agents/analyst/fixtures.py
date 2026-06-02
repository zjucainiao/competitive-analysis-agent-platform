"""Analyst 测试 / demo 用 fixture 装载器。

从 `fixtures/mock_data/competitor_profiles/*.json` 加载 CompetitorProfile，
组装成 AnalystInput；可被单元测试或 Orchestrator 联调脚本复用。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.schemas import (
    AnalysisDimension,
    AnalystInput,
    CompetitorProfile,
)

# 仓库根目录 → fixtures/mock_data/...
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROFILES_DIR = _REPO_ROOT / "fixtures" / "mock_data" / "competitor_profiles"


def _slug(name: str) -> str:
    """产品名 → 文件名（lowercase、去空格）。"""
    return name.lower().replace(" ", "")


def load_competitor_profile(product_name: str) -> CompetitorProfile:
    """按产品名加载 CompetitorProfile。文件不存在时抛 FileNotFoundError。"""
    path = _PROFILES_DIR / f"{_slug(product_name)}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"competitor profile fixture not found for {product_name!r} at {path}"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CompetitorProfile.model_validate(raw)


def load_competitor_profiles(
    product_names: list[str],
) -> dict[str, CompetitorProfile]:
    return {name: load_competitor_profile(name) for name in product_names}


def known_profiles() -> list[str]:
    """fixtures 目录中可用的产品名清单。"""
    if not _PROFILES_DIR.exists():
        return []
    out: list[str] = []
    for path in sorted(_PROFILES_DIR.glob("*.json")):
        out.append(path.stem)
    return out


DEFAULT_DIMENSIONS: list[AnalysisDimension] = [
    AnalysisDimension.FEATURE_COMPARISON,
    AnalysisDimension.PRICING_COMPARISON,
    AnalysisDimension.SWOT,
]


def load_demo_input(
    *,
    target: str = "Notion",
    competitors: list[str] | None = None,
    dimensions: list[AnalysisDimension] | None = None,
    task_id: str = "task-demo",
    project_id: str = "proj-demo",
    trace_id: str = "trace-demo",
    span_id: str = "span-analyst",
) -> AnalystInput:
    """组装 demo 用 AnalystInput（默认 Notion vs ClickUp + Asana，3 维度）。"""
    competitors = competitors if competitors is not None else ["ClickUp", "Asana"]
    profiles = load_competitor_profiles([target, *competitors])
    return AnalystInput(
        task_id=task_id,
        project_id=project_id,
        trace_id=trace_id,
        span_id=span_id,
        target_product=target,
        competitors=competitors,
        profiles=profiles,
        dimensions=dimensions or DEFAULT_DIMENSIONS,
        evidence_store_handle=None,
        qa_feedback=None,
    )


__all__ = [
    "DEFAULT_DIMENSIONS",
    "known_profiles",
    "load_competitor_profile",
    "load_competitor_profiles",
    "load_demo_input",
]
