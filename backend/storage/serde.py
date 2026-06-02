"""序列化 / 反序列化工具。

两件事：
1. `dump_output` / `load_output`：把多态 `AgentOutputBase` 子类来回转 JSON。
   PG 落 jsonb 时 schema 不直接知道具体子类，靠 `agent_name` 字段做路由。
2. `pickle_checkpoint` / `unpickle_checkpoint`：langgraph 的 checkpoint 内含
   任意 Python 对象（含 BaseModel 实例），用 pickle 直存 bytea 是 langgraph
   官方 PostgresSaver 的做法，我们保持一致。
"""

from __future__ import annotations

import pickle
from typing import Any

from backend.schemas.agent_io import AgentOutputBase
from backend.schemas.collector import CollectorOutput
from backend.schemas.extractor import ExtractorOutput
from backend.schemas.analyst import AnalystOutput
from backend.schemas.reporter import ReporterOutput
from backend.schemas.qa import QAOutput


_OUTPUT_REGISTRY: dict[str, type[AgentOutputBase]] = {
    "collector": CollectorOutput,
    "extractor": ExtractorOutput,
    "analyst": AnalystOutput,
    "reporter": ReporterOutput,
    "qa": QAOutput,
}


def dump_output(output: AgentOutputBase) -> dict[str, Any]:
    """`AgentOutputBase` → dict（含 agent_name，方便后续反序列化）。"""
    payload = output.model_dump(mode="json")
    payload.setdefault("agent_name", output.agent_name)
    return payload


def load_output(payload: dict[str, Any]) -> AgentOutputBase:
    """dict → 具体 `AgentOutputBase` 子类。

    用 `agent_name` 路由到注册表中的子类；未知 agent 抛 ValueError。
    """
    agent_name = payload.get("agent_name")
    if not agent_name:
        raise ValueError("payload missing 'agent_name'; cannot infer AgentOutput subtype")
    model = _OUTPUT_REGISTRY.get(agent_name)
    if model is None:
        raise ValueError(
            f"unknown agent_name={agent_name!r}; "
            f"known: {sorted(_OUTPUT_REGISTRY)}"
        )
    return model.model_validate(payload)


def register_output_type(agent_name: str, model: type[AgentOutputBase]) -> None:
    """供未来扩展（如新增第 6 个 Agent）注册子类。"""
    _OUTPUT_REGISTRY[agent_name] = model


# ---------- Pickle helpers for checkpoint payloads ----------


def pickle_checkpoint(checkpoint: dict[str, Any]) -> bytes:
    """langgraph 的 checkpoint dict 含任意 Python 对象，pickle 是标准做法。"""
    return pickle.dumps(checkpoint, protocol=pickle.HIGHEST_PROTOCOL)


def unpickle_checkpoint(data: bytes) -> dict[str, Any]:
    return pickle.loads(data)


def pickle_value(value: Any) -> bytes:
    """单个 channel value 的序列化（用于 checkpoint_writes 表）。"""
    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)


def unpickle_value(data: bytes) -> Any:
    return pickle.loads(data)


__all__ = [
    "dump_output",
    "load_output",
    "pickle_checkpoint",
    "pickle_value",
    "register_output_type",
    "unpickle_checkpoint",
    "unpickle_value",
]
