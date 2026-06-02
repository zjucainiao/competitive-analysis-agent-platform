"""Reporter 测试 / demo 用 fixture 装载器。

提供：
- ``load_demo_analysis()``：从 fixtures/mock_data/analysis_results/analysis_full.json
  加载 ``AnalysisResult``
- ``load_demo_input()``：组装一个完整的 ``ReporterInput``
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.schemas import (
    AnalysisResult,
    ReporterInput,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ANALYSIS_DIR = _REPO_ROOT / "fixtures" / "mock_data" / "analysis_results"


def load_demo_analysis(file_name: str = "analysis_full.json") -> AnalysisResult:
    path = _ANALYSIS_DIR / file_name
    if not path.exists():
        raise FileNotFoundError(f"analysis fixture not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AnalysisResult.model_validate(raw)


def load_demo_input(
    *,
    project_name: str = "协作办公 SaaS 对比",
    template_id: str = "standard_v1",
    target_audience: str | None = "产品经理",
    task_id: str = "task-demo",
    project_id: str = "proj-demo",
    trace_id: str = "trace-demo",
    span_id: str = "span-reporter",
) -> ReporterInput:
    return ReporterInput(
        task_id=task_id,
        project_id=project_id,
        trace_id=trace_id,
        span_id=span_id,
        project_name=project_name,
        analysis=load_demo_analysis(),
        template_id=template_id,
        output_format="markdown",
        target_audience=target_audience,
        qa_feedback=None,
    )


__all__ = [
    "load_demo_analysis",
    "load_demo_input",
]
