"""LLM 价格表 + 成本估算 —— 跨 Provider 共享。

设计要点：
- 默认表内置（``BUILTIN_PRICE_TABLE_PER_1M``），覆盖 OpenAI / DeepSeek / 豆包等
- 运行时可通过环境变量 ``LLM_PRICING_<MODEL>`` 覆盖单个模型；
  格式 ``input_per_1m,output_per_1m``（USD），例如::

      LLM_PRICING_GPT_4O=2.5,10.0
      LLM_PRICING_DEEPSEEK_CHAT=0.27,1.10

  模型名里的字符 ``[a-zA-Z0-9]`` 之外都转成 ``_`` 再加 ``LLM_PRICING_`` 前缀。
- ``estimate_cost(model, in, out)`` 是同步、纯函数，可 hot-path 调用
- 未识别的模型走 ``startswith`` 模糊匹配；都不命中返回 0（保留 token 数）

价格表是估算用，不是计费源，命中失败不抛错。
"""

from __future__ import annotations

import os
import re

# (input_per_1m_usd, output_per_1m_usd)
PriceTuple = tuple[float, float]


# 内置价格表 —— 数据来源：各厂商 2025-Q4 公开 pricing 页面
BUILTIN_PRICE_TABLE_PER_1M: dict[str, PriceTuple] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    # Anthropic Claude
    "claude-opus-4": (15.00, 75.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # 豆包 Seed（EP ID 计费走方舟控制台，这里只占位为 0）
    "doubao-seed-1-6": (0.0, 0.0),
    # Qwen / 通义千问（DashScope 公开价，2025-Q4，按官方 USD 估算）
    "qwen-plus": (0.40, 1.20),
    "qwen-max": (1.60, 6.40),
}


_ENV_PREFIX = "LLM_PRICING_"


def _env_key(model: str) -> str:
    """模型名 → 环境变量名。`gpt-4o-mini` → `LLM_PRICING_GPT_4O_MINI`。"""
    safe = re.sub(r"[^A-Za-z0-9]+", "_", model).strip("_").upper()
    return f"{_ENV_PREFIX}{safe}"


def _parse_env_price(raw: str) -> PriceTuple | None:
    """`"2.5,10.0"` → `(2.5, 10.0)`。格式不对返回 None。"""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def _lookup_with_env(model: str) -> PriceTuple | None:
    """env 优先；未配置走 BUILTIN；都没命中走 startswith 模糊匹配。"""
    env_val = os.getenv(_env_key(model))
    if env_val:
        parsed = _parse_env_price(env_val)
        if parsed is not None:
            return parsed
    if model in BUILTIN_PRICE_TABLE_PER_1M:
        return BUILTIN_PRICE_TABLE_PER_1M[model]
    for key, p in BUILTIN_PRICE_TABLE_PER_1M.items():
        if model.startswith(key):
            return p
    return None


def get_price(model: str) -> PriceTuple | None:
    """暴露查询接口，便于 UI / 日志展示「本次走的什么价格」。"""
    return _lookup_with_env(model)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """按价格表估算单次调用 USD 成本。无法识别模型返回 0。"""
    price = _lookup_with_env(model)
    if price is None:
        return 0.0
    return (tokens_in / 1_000_000) * price[0] + (tokens_out / 1_000_000) * price[1]


def register_price(model: str, input_per_1m: float, output_per_1m: float) -> None:
    """运行时注册新模型价格（v2 接管 admin 接口时用）。"""
    BUILTIN_PRICE_TABLE_PER_1M[model] = (input_per_1m, output_per_1m)


__all__ = [
    "BUILTIN_PRICE_TABLE_PER_1M",
    "PriceTuple",
    "estimate_cost",
    "get_price",
    "register_price",
]
