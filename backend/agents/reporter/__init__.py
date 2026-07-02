"""Reporter — 模板驱动的竞品报告渲染 Agent。

入口：``Reporter``。完整契约见 docs/AGENTS.md § 6。

最小可用示例（mock 模式）::

    from backend.agents.reporter import Reporter
    from backend.agents.reporter.fixtures import load_demo_input

    agent = Reporter(mock=True)
    inp = load_demo_input(template_id="standard_v1")
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    for section in out.draft.sections:
        print(section.title, len(section.paragraphs))
"""

from .agent import Reporter
from .templates import TEMPLATES, ReportTemplate, get_template, list_templates
from .tools import (
    BANNED_TERMS,
    EvidenceProvider,
    FixtureEvidenceProvider,
    StaticEvidenceProvider,
    extract_quantities,
    find_banned_terms,
    quantity_supported,
)

__all__ = [
    "BANNED_TERMS",
    "TEMPLATES",
    "EvidenceProvider",
    "FixtureEvidenceProvider",
    "ReportTemplate",
    "Reporter",
    "StaticEvidenceProvider",
    "extract_quantities",
    "find_banned_terms",
    "get_template",
    "list_templates",
    "quantity_supported",
]
