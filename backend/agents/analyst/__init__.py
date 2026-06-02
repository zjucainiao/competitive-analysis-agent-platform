"""Analyst — 多维度竞品对比分析 Agent。

入口：`Analyst`。完整契约见 docs/AGENTS.md § 5。

最小可用示例（mock 模式）::

    from backend.agents.analyst import Analyst
    from backend.agents.analyst.fixtures import load_demo_input

    agent = Analyst(mock=True)
    inp = load_demo_input()
    out = agent.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    for dim, analysis in out.result.dimensions.items():
        print(dim.value, "→", len(analysis.claims), "claims")
"""

from .agent import Analyst
from .dimensions import (
    analyze_dimension,
    collect_profile_evidence_ids,
)

__all__ = [
    "Analyst",
    "analyze_dimension",
    "collect_profile_evidence_ids",
]
