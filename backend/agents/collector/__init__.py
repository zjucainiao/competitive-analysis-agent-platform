"""Collector — 公开信息采集 Agent。

入口：`Collector`。工具构造器：`build_default_registry`。

最小可用示例（mock 模式）::

    from backend.agents.collector import Collector
    from backend.schemas import CollectorInput, CollectDimension

    agent = Collector(mock=True)
    out = agent.invoke(
        CollectorInput(
            task_id="t1",
            project_id="p1",
            trace_id="trace-1",
            span_id="span-1",
            product_name="Notion",
            industry="collaboration_saas",
            dimensions=[CollectDimension.HOMEPAGE, CollectDimension.PRICING],
        ),
        trace_id="trace-1",
        span_id="span-1",
    )
"""

from .agent import Collector
from .llm_providers import LLMResponse, OpenAICompatibleLLM
from .tools import (
    Crawl4AIScraper,
    SimpleToolRegistry,
    build_default_registry,
)

__all__ = [
    "Collector",
    "Crawl4AIScraper",
    "LLMResponse",
    "OpenAICompatibleLLM",
    "SimpleToolRegistry",
    "build_default_registry",
]
