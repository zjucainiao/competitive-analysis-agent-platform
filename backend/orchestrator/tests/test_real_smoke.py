"""真实 LLM smoke 测试 —— 验证 ``AgentRegistry.from_env()`` 装配
出的 Agent 可以发起并完成一次真实 LLM 调用。

显式 opt-in（双重门控）::

    RUN_REAL_LLM_TESTS=1 pytest backend/orchestrator/tests/test_real_smoke.py -m e2e -v -s

必须 ``RUN_REAL_LLM_TESTS=1``（避免 CI 烧 token）且 ``DEEPSEEK_API_KEY`` /
``OPENAI_API_KEY`` 至少有一个非空，否则 skip；默认 ``pytest`` 反选 e2e。
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from backend.agents.analyst.fixtures import load_demo_input
from backend.orchestrator import AgentRegistry
from backend.schemas import AgentStatus

# ---------- gating ----------

# 显式开启真实 e2e 时才读 .env 补 LLM key；模块级无条件 load_dotenv 会在收集
# 阶段泄漏开发者 .env 的 POSTGRES_DSN / REDIS_URL，破坏 storage 测试的自动 skip
if os.getenv("RUN_REAL_LLM_TESTS") == "1":
    load_dotenv(override=False)


def _has_any_llm_key() -> bool:
    return any(os.getenv(k) for k in ("DOUBAO_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"))


def _real_llm_disabled() -> bool:
    if os.getenv("RUN_REAL_LLM_TESTS") != "1":
        return True
    return not _has_any_llm_key()


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        _real_llm_disabled(),
        reason=(
            "set RUN_REAL_LLM_TESTS=1 + DOUBAO_API_KEY (or DEEPSEEK/OPENAI) to run real LLM smoke"
        ),
    ),
]


# ---------- smoke ----------


@pytest.mark.slow
def test_real_analyst_invocation_succeeds() -> None:
    """最便宜的端到端验证：用 fixture profile 喂给真实 Analyst，要 LLM 返回合规 JSON。"""
    registry = AgentRegistry.from_env()
    analyst = registry.get("analyst")
    assert analyst.mock is False, "real mode should not return mock agent"
    assert analyst.llm is not None

    inp = load_demo_input()
    out = analyst.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)

    # 容忍 PARTIAL/NEEDS_REWORK，但绝不能是 FAILED
    assert out.status != AgentStatus.FAILED, f"analyst FAILED with errors: {out.errors}"
    # 结果必须有维度且每个维度都有 LLM 生成的 claim
    assert out.result.dimensions, "real analyst returned empty dimensions"
    total_claims = sum(len(d.claims) for d in out.result.dimensions.values())
    assert total_claims > 0, "real analyst returned 0 claims"
    # 跑了多于 1 秒说明真的发了请求（mock 是 ~0ms）
    assert out.duration_ms > 1000, f"suspicious duration {out.duration_ms}ms"


@pytest.mark.slow
def test_real_qa_invocation_succeeds() -> None:
    """QA real 模式：跑一遍 checker pipeline（含 LLM 调用）。"""
    from backend.agents.qa.fixtures import load_demo_input as load_qa_input

    registry = AgentRegistry.from_env()
    qa = registry.get("qa")
    assert qa.mock is False

    inp = load_qa_input()
    out = qa.invoke(inp, trace_id=inp.trace_id, span_id=inp.span_id)
    assert out.status != AgentStatus.FAILED, f"qa FAILED with errors: {out.errors}"
    assert out.verdict.dimension_results, "qa verdict had no dimensions"
