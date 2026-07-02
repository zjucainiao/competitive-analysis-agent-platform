"""Agent 执行层。

每个 Agent 是一个独立子包：
    backend/agents/<name>/
        agent.py     # 继承 BaseAgent 的具体实现
        prompts/     # prompt 外置
        tools.py     # 本 Agent 专属工具
        README.md
        tests/

详细契约见 docs/AGENTS.md。
"""

from ._base import (
    AgentRunError,
    BaseAgent,
    LLMProviderProtocol,
    SelfCritiqueRequiredError,
    ToolRegistryProtocol,
    TracerProtocol,
)

__all__ = [
    "AgentRunError",
    "BaseAgent",
    "LLMProviderProtocol",
    "SelfCritiqueRequiredError",
    "ToolRegistryProtocol",
    "TracerProtocol",
]
