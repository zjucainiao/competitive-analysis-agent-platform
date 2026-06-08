"""原生 LangGraph 编排 state。RunState 是 StateGraph 的 schema,也是 checkpoint 载荷。"""
from __future__ import annotations

import re
from typing import Annotated, Any, Optional
from pydantic import BaseModel, ConfigDict, Field

# 版本化 output key:与 ``projection._node_id`` 的 ``_v{n}`` 后缀语义严格一致。
# round<=1 用裸 base(``reporter`` / ``collect.飞书``),round>1 加 ``_v{round}``
# (``reporter_v2`` / ``collect.飞书_v2``)。这样每一返工轮的产物各占一个独立 key,
# reducer 不再后写覆盖,v1/v2 历史与「发布择优」可如实保存(修 P1-a/P1-b)。
_VERSION_SUFFIX = re.compile(r"^(?P<base>.+)_v(?P<n>\d+)$")


def versioned_ref(base: str, round_: int) -> str:
    """逻辑节点 base + 轮次 → 版本化 output key。round<=1 不加后缀。"""
    return base if round_ <= 1 else f"{base}_v{round_}"


def split_versioned(key: str) -> tuple[str, int]:
    """版本化 key → (base, version)。无后缀视作 v1。

    注意:按 ``_v{digits}`` 后缀反解,故 base 自身含 ``_v数字`` 的产品名是病态输入
    (极罕见的展示名),此处不特别处理。
    """
    m = _VERSION_SUFFIX.match(key)
    if m:
        return m.group("base"), int(m.group("n"))
    return key, 1


def latest_output(outputs: dict[str, Any], base: str) -> Any:
    """取某 base 的「最新轮」产物(版本号最大者);无则 None。

    下游数据流读取(qa 读 reporter、analyst 读 extract.*)需要「最新一轮」而非裸 key,
    因为返工后裸 key 可能仍是 v1。

    抗后缀冲突(P2-VERSIONCONFLICT):**优先精确匹配** ``key == base``(视作 v1)——
    这样即便产品名本身以 ``_v数字`` 结尾(如 ``collect.Acme_v2``),它的 round1 裸 key
    也能被自身查询命中,而不会因 ``split_versioned`` 把它误解析成别的 base 而丢失。
    """
    best_v = -1
    best_val = None
    for key, val in outputs.items():
        if key == base:
            v = 1  # 精确命中 base 本身：恒为 v1，绕开后缀歧义
        else:
            b, n = split_versioned(key)
            if b != base:
                continue
            v = n
        if v > best_v:
            best_v, best_val = v, val
    return best_val


def latest_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    """把含多轮版本的 outputs 收敛为 ``{base: 最新轮产物}``。

    供「每节点只取终态」的聚合(metrics 计数 / profiles 收集)复用,避免把 v1+v2
    同一产品重复计入。注意:**不要**用它喂「发布择优」——那里要保留 ``reporter_v{n}``
    才能挑非最新的最优轮。
    """
    best_ver: dict[str, int] = {}
    best_val: dict[str, Any] = {}
    for key, val in outputs.items():
        b, v = split_versioned(key)
        if v >= best_ver.get(b, -1):
            best_ver[b] = v
            best_val[b] = val
    return best_val


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
    # 节点级用户提示词覆盖(P1-INTERVENE)。键同 qa_feedback_by_node 约定：per-product
    # Agent → ``collect.{product}`` / ``extract.{product}``；全局 Agent → ``analyst`` /
    # ``reporter`` / ``qa``。值为用户改写的 prompt 文本，native 节点取出后作为
    # ``user_prompt_override`` 传给 run_agent_node（其会注入 ContextVar→system prompt）。
    prompt_override_by_node: dict[str, str] = Field(default_factory=dict)
    aborted: bool = False
    abort_reason: str = ""


__all__ = [
    "RunState",
    "NodeRun",
    "merge_outputs",
    "append_list",
    "versioned_ref",
    "split_versioned",
    "latest_output",
    "latest_outputs",
]
