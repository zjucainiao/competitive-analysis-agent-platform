"""LangGraph-compatible checkpoint 类型的本地镜像。

Storage 层不能 import langgraph（编排器选型不应该污染存储抽象），
但 `CheckpointerProtocol` 又必须能塞进 `langgraph.StateGraph(checkpointer=...)`。
办法：本地定义结构等价的 TypedDict / Pydantic，由 `langgraph_adapter.py` 在
真接 langgraph 时做透明转换。

字段与 langgraph 0.2 同名类一一对应，名字保持一致以便适配层零开销搬运。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict


# ---------- TypedDicts ----------


class _Configurable(TypedDict, total=False):
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str | None


class CheckpointConfig(TypedDict, total=False):
    """langchain_core RunnableConfig 的最小子集。

    Orchestrator 调用 checkpointer 时只需要 `configurable.thread_id`
    +（可选）`checkpoint_ns` + `checkpoint_id`。
    """

    configurable: _Configurable


class CheckpointMetadata(TypedDict, total=False):
    """与 langgraph CheckpointMetadata 字段对齐。"""

    source: Literal["input", "loop", "update", "fork"]
    step: int
    writes: dict[str, Any]
    parents: dict[str, str]


# ChannelVersions：channel -> version（int 或 str），langgraph 内部计数用
ChannelVersions = dict[str, int | str]


class Checkpoint(TypedDict, total=False):
    """完整的 checkpoint 快照。

    `channel_values` 是 StateGraph 当前 state；其余字段是 langgraph 内部簿记。
    存储层不解释这些字段含义，只负责整体落库 / 取回。
    """

    v: int
    id: str
    ts: str
    channel_values: dict[str, Any]
    channel_versions: ChannelVersions
    versions_seen: dict[str, ChannelVersions]
    pending_sends: list[Any]


# ---------- CheckpointTuple ----------
# langgraph 的 CheckpointTuple 是 NamedTuple；我们用 dataclass 等价表达。

from dataclasses import dataclass, field


@dataclass
class CheckpointTuple:
    """一次 checkpoint 读取的完整返回值。

    与 langgraph.checkpoint.base.CheckpointTuple 字段对齐：
      config / checkpoint / metadata / parent_config / pending_writes
    """

    config: CheckpointConfig
    checkpoint: Checkpoint
    metadata: CheckpointMetadata
    parent_config: CheckpointConfig | None = None
    pending_writes: list[tuple[str, str, Any]] = field(default_factory=list)
    """(task_id, channel, value) — 写入但尚未应用的 writes。"""


# ---------- 辅助工具 ----------


def thread_id_of(config: CheckpointConfig) -> str:
    """从 config 提取 thread_id。缺失时抛 ValueError。"""
    cfg = config.get("configurable") or {}
    tid = cfg.get("thread_id")
    if not tid:
        raise ValueError("CheckpointConfig.configurable.thread_id is required")
    return tid


def checkpoint_ns_of(config: CheckpointConfig) -> str:
    """checkpoint_ns 缺省为空串（与 langgraph 一致）。"""
    cfg = config.get("configurable") or {}
    return cfg.get("checkpoint_ns") or ""


def checkpoint_id_of(config: CheckpointConfig) -> str | None:
    cfg = config.get("configurable") or {}
    return cfg.get("checkpoint_id")


def make_config(
    thread_id: str,
    *,
    checkpoint_ns: str = "",
    checkpoint_id: str | None = None,
) -> CheckpointConfig:
    return {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
        }
    }


def now_ts() -> str:
    """ISO-8601 时间戳，与 langgraph 的 ts 字段格式一致。"""
    return datetime.utcnow().isoformat()


__all__ = [
    "ChannelVersions",
    "Checkpoint",
    "CheckpointConfig",
    "CheckpointMetadata",
    "CheckpointTuple",
    "checkpoint_id_of",
    "checkpoint_ns_of",
    "make_config",
    "now_ts",
    "thread_id_of",
]
