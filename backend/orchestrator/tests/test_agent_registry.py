"""AgentRegistry 单测：装配 real Agent + from_env 工厂。

真实 LLM 调用在 ``test_real_smoke.py`` 和 API 层 ``test_real_full_chain.py``；
本文件只验证装配路径（不发请求）。
"""

from __future__ import annotations

import pytest

from backend.orchestrator import AgentRegistry, NullTracer


class _FakeLLM:
    """构造期注入用的占位 LLM，本文件不调用 .chat。"""

    def chat(self, **kwargs):  # pragma: no cover
        raise AssertionError("test should not hit LLM")


# ---------- 显式构造 ----------


def test_real_construction_requires_llm() -> None:
    with pytest.raises(ValueError, match="requires llm"):
        AgentRegistry(llm=None, tracer=NullTracer())  # type: ignore[arg-type]


def test_real_construction_requires_tracer() -> None:
    with pytest.raises(ValueError, match="requires tracer"):
        AgentRegistry(llm=_FakeLLM(), tracer=None)  # type: ignore[arg-type]


def test_all_agents_are_real() -> None:
    from backend.agents.collector import build_default_registry

    r = AgentRegistry(
        llm=_FakeLLM(),
        tracer=NullTracer(),
        tools=build_default_registry(),
    )
    for name in r.known_agents():
        agent = r.get(name)
        assert agent.mock is False


def test_registry_caches_agent_instances() -> None:
    from backend.agents.collector import build_default_registry

    r = AgentRegistry(
        llm=_FakeLLM(),
        tracer=NullTracer(),
        tools=build_default_registry(),
    )
    a1 = r.get("collector")
    a2 = r.get("collector")
    assert a1 is a2


def test_unknown_agent_raises() -> None:
    from backend.agents.collector import build_default_registry

    r = AgentRegistry(
        llm=_FakeLLM(),
        tracer=NullTracer(),
        tools=build_default_registry(),
    )
    with pytest.raises(ValueError, match="unknown agent"):
        r.get("nonexistent")


# ---------- from_env 工厂 ----------


def test_from_env_without_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOUBAO_API_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no LLM API key"):
        AgentRegistry.from_env()


def test_from_env_attaches_real_llm_and_null_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """env 有 key 时所有 Agent 都拿到真 LLM + NullTracer。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test_key_xxx")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = AgentRegistry.from_env()
    for name in r.known_agents():
        agent = r.get(name)
        assert agent.mock is False
        assert agent.llm is not None
        assert isinstance(agent.tracer, NullTracer)


def test_null_tracer_span_is_context_manager() -> None:
    tracer = NullTracer()
    with tracer.span(
        trace_id="t1",
        span_id="s1",
        parent_span_id=None,
        agent_name="collector",
        agent_version="1.0.0",
    ) as span:
        span.set_output({"k": "v"})
        span.set_error(RuntimeError("ignored"))
