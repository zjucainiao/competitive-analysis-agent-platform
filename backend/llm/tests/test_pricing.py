"""LLM pricing 单测。

不 import ``backend.llm`` 子包根（避免触发 collector → httpx 等重依赖），
直接 ``from backend.llm.pricing import ...``，pytest 真环境下 backend/llm/__init__.py
能加载即可。
"""

from __future__ import annotations

import pytest

# pytest 真环境装齐依赖（httpx 等）后，子包根 __init__.py 能正常加载，
# 这里 import 子模块本身是稳的。
from backend.llm.pricing import (
    BUILTIN_PRICE_TABLE_PER_1M,
    _env_key,
    estimate_cost,
    get_price,
    register_price,
)

# ---------- builtin lookup ----------


def test_builtin_lookup_exact():
    assert get_price("gpt-4o-mini") == (0.15, 0.60)
    assert get_price("claude-opus-4-7") == (15.00, 75.00)


def test_unknown_model_returns_none():
    assert get_price("totally-unknown-model-xyz") is None
    assert estimate_cost("totally-unknown-model-xyz", 1000, 1000) == 0.0


# ---------- startswith fallback ----------


def test_startswith_fallback():
    # 真实 OpenAI 把日期挂在模型名后："gpt-4o-2024-08-06"
    assert get_price("gpt-4o-2024-08-06") == (2.50, 10.00)


def test_startswith_fallback_doubao_ep():
    # 豆包 EP ID 类自定义名，应命中 doubao-seed-1-6 前缀
    assert get_price("doubao-seed-1-6-thinking-pro") == (0.0, 0.0)


# ---------- cost calc ----------


def test_cost_basic():
    # gpt-4o-mini @ 1k in / 1k out = 1000*(0.15/1M) + 1000*(0.60/1M) = 0.00075
    assert estimate_cost("gpt-4o-mini", 1000, 1000) == pytest.approx(0.00075)


def test_cost_zero_for_unknown():
    assert estimate_cost("ghost-model", 10000, 10000) == 0.0


def test_cost_zero_tokens():
    assert estimate_cost("gpt-4o-mini", 0, 0) == 0.0


# ---------- env override ----------


def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_PRICING_GPT_4O_MINI", "99.0,100.0")
    assert get_price("gpt-4o-mini") == (99.0, 100.0)
    assert estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(99.0 + 100.0)


def test_env_override_bad_format_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_PRICING_GPT_4O", "not-a-number")
    # 格式不对 → 走 builtin
    assert get_price("gpt-4o") == BUILTIN_PRICE_TABLE_PER_1M["gpt-4o"]


def test_env_override_extra_fields_ignored(monkeypatch):
    """3 个字段（非 2 个）应当被认为格式错误。"""
    monkeypatch.setenv("LLM_PRICING_GPT_4O_MINI", "1.0,2.0,3.0")
    assert get_price("gpt-4o-mini") == BUILTIN_PRICE_TABLE_PER_1M["gpt-4o-mini"]


# ---------- env key formatting ----------


def test_env_key_formatting():
    assert _env_key("gpt-4o-mini") == "LLM_PRICING_GPT_4O_MINI"
    assert _env_key("gpt-4.1-mini") == "LLM_PRICING_GPT_4_1_MINI"
    assert _env_key("claude-opus-4-7") == "LLM_PRICING_CLAUDE_OPUS_4_7"


# ---------- register_price ----------


def test_register_price_runtime():
    register_price("llama-3-70b-test", 0.6, 1.0)
    assert get_price("llama-3-70b-test") == (0.6, 1.0)
    assert estimate_cost("llama-3-70b-test", 1_000_000, 0) == pytest.approx(0.6)


# ---------- collector 调用点零改动验证 ----------


def test_collector_estimate_cost_delegates():
    """``collector.llm_providers._estimate_cost`` 应仍可用且行为一致。"""
    from backend.agents.collector.llm_providers import _estimate_cost as collector_cost

    assert collector_cost("gpt-4o-mini", 1000, 1000) == pytest.approx(
        estimate_cost("gpt-4o-mini", 1000, 1000)
    )
