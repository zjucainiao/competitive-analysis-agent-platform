"""LLM Provider 公共门面层。

v1 阶段：``OpenAICompatibleLLM`` / ``LLMResponse`` 的真实实现仍住在
``backend.agents.collector.llm_providers``（原因见该文件 docstring）；
本模块用 PEP 562 module-level ``__getattr__`` **lazy re-export**，
避免 ``import backend.llm.pricing`` 这种轻量子模块也被迫加载整个 collector
工具链（httpx / bs4 / openai SDK），并切断 ``backend.agents._base`` 调
``estimate_cost`` 时的循环 import 风险。

    from backend.llm import OpenAICompatibleLLM, build_llm_from_env, LLMResponse
    from backend.llm.pricing import estimate_cost          # 不触发 collector

I 窗口未来把通用 LLM 层产出后，只需把 collector.llm_providers 内的实现
搬到 ``backend.llm.provider``，下面 ``__getattr__`` 一次性切 import 路径即可。

``build_llm_from_env()`` 装配优先级：

- ``DOUBAO_*``  → 豆包 Seed（火山方舟，OpenAI 兼容；EP 上开"联网内容插件"
  即自带搜索能力）
- ``DEEPSEEK_*`` → DeepSeek（``deepseek-chat``）
- ``OPENAI_*``  → OpenAI（``gpt-4o-mini``）
- 都没有返回 ``None``
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.agents.collector.llm_providers import (
        LLMResponse,
        OpenAICompatibleLLM,
    )


_DOUBAO_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def __getattr__(name: str) -> Any:
    """PEP 562 lazy 加载 collector 的 LLM 实现。

    第一次访问 ``backend.llm.OpenAICompatibleLLM`` / ``backend.llm.LLMResponse``
    时才 import collector 子树；只 import ``backend.llm.pricing`` 的代码路径
    完全不被牵连。
    """
    if name in ("OpenAICompatibleLLM", "LLMResponse"):
        from backend.agents.collector.llm_providers import (
            LLMResponse as _LLMResponse,
        )
        from backend.agents.collector.llm_providers import (
            OpenAICompatibleLLM as _OpenAICompatibleLLM,
        )

        # 缓存到模块全局，下次直接 getattr，不再走 __getattr__
        globals()["LLMResponse"] = _LLMResponse
        globals()["OpenAICompatibleLLM"] = _OpenAICompatibleLLM
        return {
            "LLMResponse": _LLMResponse,
            "OpenAICompatibleLLM": _OpenAICompatibleLLM,
        }[name]
    raise AttributeError(f"module 'backend.llm' has no attribute {name!r}")


def build_llm_from_env():
    """从环境变量装配 LLM provider。豆包优先，DeepSeek / OpenAI 兜底。

    返回值类型为 ``OpenAICompatibleLLM | None``；运行时按需 lazy import。
    """
    from backend.agents.collector.llm_providers import OpenAICompatibleLLM

    doubao_key = os.getenv("DOUBAO_API_KEY")
    doubao_model = os.getenv("DOUBAO_MODEL")
    if doubao_key and doubao_model:
        return OpenAICompatibleLLM(
            api_key=doubao_key,
            base_url=os.getenv("DOUBAO_BASE_URL", _DOUBAO_DEFAULT_BASE_URL),
            model=doubao_model,
            # 火山方舟当前不接受 OpenAI 的 response_format=json_object 参数；
            # schema 已被注入 system prompt，关闭硬开关即可
            supports_json_mode=False,
        )
    # 没填 EP ID 但填了 key 的情况：警告并跳到下一档 provider
    return OpenAICompatibleLLM.from_env()


__all__ = [
    "LLMResponse",
    "OpenAICompatibleLLM",
    "build_llm_from_env",
]
