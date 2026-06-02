"""AgentRegistry —— 装配 5 个 real Agent 实例。

只支持 real 模式：mock / hybrid 已彻底移除。

构造方式：

- ``AgentRegistry(llm=..., tracer=..., tools=...)``：显式注入依赖。
- ``AgentRegistry.from_env()``：从环境变量自动装配（``DEEPSEEK_API_KEY``
  / ``OPENAI_API_KEY``），tracer 用占位 ``NullTracer``，tools 用 Collector
  默认 registry。最常用于 demo 和 API 层。
"""

from __future__ import annotations

from typing import Any

from backend.agents._base import BaseAgent
from backend.agents.analyst import Analyst
from backend.agents.collector import Collector
from backend.agents.extractor import Extractor
from backend.agents.qa import QA
from backend.agents.reporter import Reporter
from backend.observability import NullTracer, build_tracer_from_env


_AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    "collector": Collector,
    "extractor": Extractor,
    "analyst": Analyst,
    "reporter": Reporter,
    "qa": QA,
}


class AgentRegistry:
    """按 agent 名取 real Agent 实例。

    实例化惰性（首次 ``get(name)`` 时构造），同名 Agent 缓存复用。
    """

    def __init__(
        self,
        *,
        llm: Any,
        tracer: Any,
        tools: Any = None,
        evidence_provider: Any = None,
    ) -> None:
        if llm is None:
            raise ValueError("AgentRegistry requires llm")
        if tracer is None:
            raise ValueError("AgentRegistry requires tracer")
        self._llm = llm
        self._tracer = tracer
        self._tools = tools
        self._evidence_provider = evidence_provider
        self._cache: dict[str, BaseAgent] = {}

    @staticmethod
    def known_agents() -> list[str]:
        return list(_AGENT_CLASSES.keys())

    def get(self, agent_name: str) -> BaseAgent:
        if agent_name in self._cache:
            return self._cache[agent_name]

        cls = _AGENT_CLASSES.get(agent_name)
        if cls is None:
            raise ValueError(
                f"unknown agent: {agent_name!r}; known={self.known_agents()}"
            )

        kwargs: dict[str, Any] = {
            "mock": False,
            "llm": self._llm,
            "tracer": self._tracer,
            "tools": self._tools,
        }

        # Reporter 的 evidence_provider 是构造期注入
        if agent_name == "reporter" and self._evidence_provider is not None:
            kwargs["evidence_provider"] = self._evidence_provider

        agent = cls(**kwargs)
        self._cache[agent_name] = agent
        return agent

    # ----- 运行时 evidence 注入：每次新建，不缓存 -----

    def make_reporter(self, *, evidence_provider: Any) -> BaseAgent:
        """根据当前 outputs 构造一个 Reporter（带运行时 evidence_provider）。

        与 ``get("reporter")`` 不同：每次都新建实例，因为 evidence_provider 跟
        本次 run 收集到的 Extractor outputs 绑定，不能跨 run 复用。
        """
        return Reporter(
            llm=self._llm,
            tools=self._tools,
            tracer=self._tracer,
            evidence_provider=evidence_provider,
            mock=False,
        )

    def make_qa(self, *, evidence_db: dict[str, Any]) -> BaseAgent:
        """根据当前 outputs 构造一个 QA（带运行时 evidence_db）。

        显式传 ``evidence_db`` 是为了阻止 QA 在 ``evidence_db=None`` 时
        悄悄从 fixtures 加载 mock。real 链路严格不依赖 fixture。
        """
        return QA(
            llm=self._llm,
            tools=self._tools,
            tracer=self._tracer,
            evidence_db=evidence_db,
            mock=False,
        )

    # ----- 工厂：从环境变量装配 -----

    @classmethod
    def from_env(
        cls,
        *,
        tools: Any = None,
        evidence_provider: Any = None,
        service_name: str = "competitive-analysis-agent",
    ) -> "AgentRegistry":
        """读 ``DOUBAO/DEEPSEEK/OPENAI_API_KEY`` 装配真实 LLM + OTLP Tracer。

        - 无 LLM key 直接 ``raise RuntimeError``（不静默退化）。
        - ``tools=None`` 自动用 ``backend.agents.collector.build_default_registry()``。
        - ``tracer`` 走 ``backend.observability.build_tracer_from_env``：
          有 ``OTEL_EXPORTER_OTLP_ENDPOINT`` → ``OTLPTracer``；
          没有 → ``NullTracer``（开发 / 单测零配置）。
        """
        from backend.llm import build_llm_from_env

        llm = build_llm_from_env()
        if llm is None:
            raise RuntimeError(
                "AgentRegistry.from_env: no LLM API key found; "
                "set DOUBAO_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY"
            )

        if tools is None:
            from backend.agents.collector import build_default_registry

            tools = build_default_registry()

        tracer = build_tracer_from_env(service_name=service_name)

        return cls(
            llm=llm,
            tracer=tracer,
            tools=tools,
            evidence_provider=evidence_provider,
        )
