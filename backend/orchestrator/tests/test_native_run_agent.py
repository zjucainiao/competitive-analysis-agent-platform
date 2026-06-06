"""run_agent_node 的单元测试。

使用假 Agent 验证重试 / 全失败语义；不调用真实 LLM。
asyncio_mode = "auto" (pyproject.toml)，@pytest.mark.asyncio 保留以明确意图。
"""

from __future__ import annotations

import time

import pytest
from backend.orchestrator.run_agent import run_agent_node, AgentRunResult
from backend.schemas import AgentError, AgentStatus


class _FakeAgent:
    def __init__(self, fail_times: int = 0):
        self.calls = 0
        self.fail_times = fail_times

    def invoke(self, inp, *, trace_id, span_id, node_id):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("transient")

        # Note: `span_id = span_id` is a NameError in Python 3.12 class bodies
        # (class-body scoping treats the LHS as new name, shadowing the enclosing
        # parameter before the RHS is evaluated).  `run_agent_node` only reads
        # `.status` and `.errors`, so omitting the attribute is safe here.
        class _Out:
            status = AgentStatus.SUCCESS
            self_critique = None
            errors = []

        return _Out()


class _Reg:
    def __init__(self, agent):
        self._a = agent

    def get(self, name):
        return self._a


@pytest.mark.asyncio
async def test_retry_then_success():
    agent = _FakeAgent(fail_times=1)
    res = await run_agent_node(
        _Reg(agent), "collector", object(), outputs={}, trace_id="t",
        node_id="collect.x", max_retries=2, timeout_ms=2000, backoff_base=0.0,
    )
    assert res.status == AgentStatus.SUCCESS and agent.calls == 2


@pytest.mark.asyncio
async def test_all_retries_fail_returns_failed():
    agent = _FakeAgent(fail_times=5)
    res = await run_agent_node(
        _Reg(agent), "collector", object(), outputs={}, trace_id="t",
        node_id="collect.x", max_retries=1, timeout_ms=2000, backoff_base=0.0,
    )
    assert res.status == AgentStatus.FAILED and res.error is not None


@pytest.mark.asyncio
async def test_attempts_count_on_failure():
    """max_retries=1 → total attempts must be exactly 2 (no off-by-one)."""
    agent = _FakeAgent(fail_times=5)  # always fails
    res = await run_agent_node(
        _Reg(agent), "collector", object(), outputs={}, trace_id="t",
        node_id="collect.x", max_retries=1, timeout_ms=2000, backoff_base=0.0,
    )
    assert res.status == AgentStatus.FAILED
    assert res.attempts == 2  # max_retries + 1


@pytest.mark.asyncio
async def test_timeout_then_success():
    """First invoke blocks 0.3 s, tripping the 50 ms timeout; retry succeeds."""

    class _TimeoutThenSuccessAgent:
        def __init__(self):
            self.calls = 0

        def invoke(self, inp, *, trace_id, span_id, node_id):
            self.calls += 1
            if self.calls == 1:
                # Block long enough to reliably trip asyncio.wait_for(timeout=0.05)
                time.sleep(0.3)

            class _Out:
                status = AgentStatus.SUCCESS
                self_critique = None
                errors = []

            return _Out()

    agent = _TimeoutThenSuccessAgent()
    res = await run_agent_node(
        _Reg(agent), "collector", object(), outputs={}, trace_id="t",
        node_id="collect.x", max_retries=2, timeout_ms=50, backoff_base=0.0,
    )
    assert res.status == AgentStatus.SUCCESS
    assert agent.calls == 2  # first timed out, second succeeded


@pytest.mark.asyncio
async def test_non_retriable_failed_returns_immediately():
    """A non-retriable FAILED output must abort immediately (no retries)."""

    class _NonRetriableAgent:
        def __init__(self):
            self.calls = 0

        def invoke(self, inp, *, trace_id, span_id, node_id):
            self.calls += 1

            class _Out:
                status = AgentStatus.FAILED
                errors = [
                    AgentError(
                        code="INPUT_INVALID",
                        message="required field missing",
                        severity="fatal",
                        retriable=False,
                    )
                ]

            return _Out()

    agent = _NonRetriableAgent()
    res = await run_agent_node(
        _Reg(agent), "collector", object(), outputs={}, trace_id="t",
        node_id="collect.x", max_retries=3, timeout_ms=2000, backoff_base=0.0,
    )
    assert res.status == AgentStatus.FAILED
    assert res.error is not None
    assert res.error.retriable is False
    assert agent.calls == 1  # non-retriable → exits immediately, no retry


@pytest.mark.asyncio
async def test_partial_returned_as_is():
    """PARTIAL status from agent must flow through unchanged (not retried, not downgraded)."""

    class _PartialAgent:
        def __init__(self):
            self.calls = 0

        def invoke(self, inp, *, trace_id, span_id, node_id):
            self.calls += 1

            class _Out:
                status = AgentStatus.PARTIAL
                self_critique = None
                errors = []

            return _Out()

    agent = _PartialAgent()
    res = await run_agent_node(
        _Reg(agent), "collector", object(), outputs={}, trace_id="t",
        node_id="collect.x", max_retries=3, timeout_ms=2000, backoff_base=0.0,
    )
    assert res.status == AgentStatus.PARTIAL
    assert agent.calls == 1  # PARTIAL is terminal — no retry
