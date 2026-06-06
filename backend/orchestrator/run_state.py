"""原生 LangGraph 编排 state。RunState 是 StateGraph 的 schema,也是 checkpoint 载荷。"""
from __future__ import annotations

from typing import Annotated, Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class NodeRun(BaseModel):
    """history 里一条节点执行记录(回放真相源的最小单元)。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    node: str                  # 逻辑节点: collect/extract/analyst/reporter/qa
    agent: str
    product: Optional[str] = None
    round: int = 1             # QA 返工轮次(1=首跑)
    status: str                # success/partial/needs_rework/failed
    span_id: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    prompt_preview: Optional[str] = None
    response_preview: Optional[str] = None
    output_ref: Optional[str] = None   # outputs 里的 key


def merge_outputs(current: dict, update: dict) -> dict:
    """并行 Send 分支各写一个 key;合并 dict,后写覆盖同 key。"""
    merged = dict(current)
    merged.update(update)
    return merged


def append_list(current: list, update: list) -> list:
    """并行分支各 append;拼接。"""
    return list(current) + list(update)


class RunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    project_id: str
    run_id: str
    analysis_mode: str                 # 透传给 agent,编排不解释
    products: list[str]
    outputs: Annotated[dict[str, Any], merge_outputs] = Field(default_factory=dict)
    history: Annotated[list[NodeRun], append_list] = Field(default_factory=list)
    verdicts: Annotated[list[Any], append_list] = Field(default_factory=list)
    qa_round: int = 0
    rework_products: list[str] = Field(default_factory=list)
    rework_target: Optional[str] = None
    # QA 返工反馈,按"消费该反馈的入口节点 ID"作键(last-write-wins,无 reducer):
    # per-product Agent → ``collect.{product}`` / ``extract.{product}``;
    # 全局 Agent → ``analyst`` / ``reporter``。由 qa 节点每轮经 decide_qa_route
    # 整体覆盖写入,rework 节点从这里取对应 payload 注入 build_*_input。
    qa_feedback_by_node: dict[str, dict] = Field(default_factory=dict)
    aborted: bool = False
    abort_reason: str = ""


__all__ = ["RunState", "NodeRun", "merge_outputs", "append_list"]
