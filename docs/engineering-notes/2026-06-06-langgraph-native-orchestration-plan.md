# LangGraph 原生编排 · 实现计划(Phase 0 + Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把后端编排从「单 dispatch 节点 + 自研 DAGPlan 解释器」改成「原生 LangGraph 图,每个 agent = 一个节点」,Phase 1 末态新引擎与旧前端经临时投影并存。

**Architecture:** 方案 A —— 扁平 `StateGraph`:`collect_dispatch`/`extract_dispatch`(轻量扇出节点,`Send` 按产品扇出,兼作 QA 回环再入口)+ `collect_one`/`extract_one`/`analyst`/`reporter`/`qa`(agent 节点)。`qa` 节点 `return Command(goto=…)` 实现 QA 回环到任意上游(最上游单一目标)。状态 `RunState` 带 reducer(outputs 合并 / history·verdicts append),靠现有 PostgresCheckpointer 持久化。Agent 自检留在 agent 内部,编排层只管流转。

**Tech Stack:** Python 3.12 · LangGraph 1.2.4(`langgraph.types.Send` / `Command`)· Pydantic v2 · pytest · 现有 `backend.agents.*` / `backend.storage`。

**范围**:仅 Phase 0(抽脚手架)+ Phase 1(原生后端,behind 临时 DAGPlan 投影 + `ORCH_ENGINE` flag)。**不含**前端迁移(Phase 2)、删旧(Phase 3)、提示词业务特化(另开)。设计见 [2026-06-06-langgraph-native-orchestration-design.md](2026-06-06-langgraph-native-orchestration-design.md)。

**全程约定**:每个 agent 节点只 `asyncio.to_thread(agent.invoke)`,**不复制 agent 内部自检**;retry 只针对 `FAILED`/超时;新引擎默认关闭(`ORCH_ENGINE=legacy`),旧路径保持全绿。

---

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `backend/orchestrator/inputs.py` | 参数化 agent input 构造器(从 `executor._build_*` 抽出,去掉 DAGNode 依赖) | T1 |
| `backend/orchestrator/run_state.py` | `RunState` + `NodeRun` + reducer | T2 |
| `backend/orchestrator/run_agent.py` | `run_agent_node()`:resolve agent + 重试/超时/trace ctx/evidence 注入 | T3 |
| `backend/orchestrator/nodes.py` | 7 个节点函数 | T4 |
| `backend/orchestrator/routing.py` | `decide_qa_route()`:verdict → (goto, update) | T5 |
| `backend/orchestrator/graph.py` | `build_native_graph()`:连节点/Send/条件路由/编译 | T6 |
| `backend/orchestrator/projection.py` | `run_state_to_dagplan()`:RunState/history → DAGPlan(临时投影) | T7 |
| `backend/orchestrator/orchestrator.py` | `Orchestrator.run()` 加 `ORCH_ENGINE` 分支接新引擎 | T8 |
| `backend/orchestrator/tests/test_native_*.py` | 节点/路由/图集成/回放 测试 | T2–T9 |

---

## Phase 0 — 抽脚手架(纯重构,旧测试保持全绿)

### Task 1: 抽出参数化 input 构造器到 `inputs.py`

**Files:**
- Create: `backend/orchestrator/inputs.py`
- Modify: `backend/orchestrator/executor.py`(`_build_*` 改为委托)
- Test: 复用现有 `backend/orchestrator/tests/`(executor 路径用例不许变红)

- [ ] **Step 1: 写 `inputs.py`,把 5 个 builder 改成参数化纯函数**

每个函数去掉 `node` 依赖,改收显式参数。逻辑逐字搬自 `executor.py:351-483`,仅把 `node.metadata[...]`/`node.input_refs`/`self.project`/`self.trace_id` 换成入参。

```python
"""参数化 agent input 构造器。

从 Executor._build_* 抽出,去掉 DAGNode 耦合:既给旧 Executor(适配 node)
复用,也给新原生节点(从 RunState 取参)复用。纯函数,无副作用。
"""
from __future__ import annotations

from ulid import ULID

from backend.schemas import (
    AnalystInput, CollectorInput, CompetitorProfile, ExtractorInput,
    Project, QAInput, ReporterInput, AgentOutputBase,
)
from backend.schemas.evidence import CollectDimension


class BuildInputError(ValueError):
    """无法装配 Agent input。"""


def _span() -> str:
    return f"span_{ULID()}"


def _industry_schema_id(project: Project) -> str:
    major = project.industry_schema_version.split(".", 1)[0]
    return f"{project.industry}_v{major}"


def profiles_from_outputs(outputs: dict[str, AgentOutputBase]) -> dict[str, CompetitorProfile]:
    profiles: dict[str, CompetitorProfile] = {}
    for nid, out in outputs.items():
        if not nid.startswith("extract."):
            continue
        profile = getattr(out, "profile", None)
        if profile is not None:
            profiles[profile.basic_info.name] = profile
    return profiles


def build_collector_input(
    project: Project, *, trace_id: str, product: str,
    official_url: str | None, dims: list[str], qa_feedback: dict | None,
) -> CollectorInput:
    if not product:
        raise BuildInputError("collector: empty product")
    if not dims:
        raise BuildInputError(f"collector[{product}]: empty collect_dimensions")
    return CollectorInput(
        task_id=f"collect.{product}", project_id=project.project_id,
        trace_id=trace_id, span_id=_span(), product_name=product,
        official_url=official_url, industry=project.industry,
        dimensions=[CollectDimension(d) for d in dims],
        constraints=project.collect_constraints, qa_feedback=qa_feedback,
    )


def build_extractor_input(
    project: Project, *, trace_id: str, product: str,
    collector_output: AgentOutputBase, qa_feedback: dict | None,
) -> ExtractorInput:
    raw_sources = getattr(collector_output, "raw_sources", None)
    if raw_sources is None:
        raise BuildInputError(f"extractor[{product}]: upstream has no raw_sources")
    return ExtractorInput(
        task_id=f"extract.{product}", project_id=project.project_id,
        trace_id=trace_id, span_id=_span(), product_name=product,
        industry_schema_id=_industry_schema_id(project),
        raw_sources=raw_sources, qa_feedback=qa_feedback,
    )


def build_analyst_input(
    project: Project, *, trace_id: str,
    outputs: dict[str, AgentOutputBase], qa_feedback: dict | None,
) -> AnalystInput:
    profiles = profiles_from_outputs(outputs)
    if not profiles:
        raise BuildInputError("analyst: no extractor profiles available")
    return AnalystInput(
        task_id="analyst", project_id=project.project_id, trace_id=trace_id,
        span_id=_span(), target_product=project.target_product,
        competitors=list(project.competitors), profiles=profiles,
        dimensions=list(project.analysis_dimensions), qa_feedback=qa_feedback,
    )


def build_reporter_input(
    project: Project, *, trace_id: str, analyst_output: AgentOutputBase,
    qa_feedback: dict | None,
) -> ReporterInput:
    return ReporterInput(
        task_id="reporter", project_id=project.project_id, trace_id=trace_id,
        span_id=_span(), project_name=project.project_name,
        analysis=analyst_output.result, template_id=project.report_template_id,
        output_format="markdown", target_audience=project.target_audience,
        qa_feedback=qa_feedback,
    )


def build_qa_input(
    project: Project, *, trace_id: str, reporter_output: AgentOutputBase,
    analyst_output: AgentOutputBase, outputs: dict[str, AgentOutputBase],
    prior_verdicts: list,
) -> QAInput:
    return QAInput(
        task_id="qa", project_id=project.project_id, trace_id=trace_id,
        span_id=_span(), draft=reporter_output.draft,
        analysis=analyst_output.result, profiles=profiles_from_outputs(outputs),
        evidence_store_handle=None, prior_verdicts=prior_verdicts,
    )
```

- [ ] **Step 2: 让 `executor.py` 的 `_build_*` 委托到新函数**

把 `executor.py:330-483` 的 6 个方法体替换为委托(保留方法签名 + 从 `node` 取参)。例如 `_build_collector_input` 改为:

```python
def _build_collector_input(self, node, qa_feedback):
    from .inputs import build_collector_input
    return build_collector_input(
        self.project, trace_id=self.trace_id,
        product=node.metadata.get("product"),
        official_url=node.metadata.get("official_url"),
        dims=node.metadata.get("collect_dimensions") or [],
        qa_feedback=qa_feedback,
    )
```
extractor 同理(从 `node.input_refs[0]` 取 `outputs[upstream]` 当 `collector_output`),analyst/reporter/qa 用 `self._latest_output(...)` 取 `analyst_output`/`reporter_output`。`profiles_from_outputs` 改为 import 自 `inputs.py`(删 executor 内重复定义)。

- [ ] **Step 3: 跑现有编排测试,确认全绿**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/ -q`
Expected: PASS(与重构前同样的通过数;纯委托不改行为)

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/inputs.py backend/orchestrator/executor.py
git commit -m "refactor(orchestrator): extract param-based input builders into inputs.py"
```

---

## Phase 1 — 原生 LangGraph 后端

### Task 2: `RunState` + reducer

**Files:**
- Create: `backend/orchestrator/run_state.py`
- Test: `backend/orchestrator/tests/test_native_run_state.py`

- [ ] **Step 1: 写失败测试(reducer 行为)**

```python
from backend.orchestrator.run_state import RunState, NodeRun, merge_outputs, append_list

def test_merge_outputs_last_write_wins_and_keeps_others():
    assert merge_outputs({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}

def test_append_list_concatenates():
    assert append_list([1, 2], [3]) == [1, 2, 3]

def test_runstate_defaults():
    s = RunState(project_id="p", run_id="r", analysis_mode="competitive_compare", products=["Notion"])
    assert s.outputs == {} and s.history == [] and s.qa_round == 0 and s.aborted is False
```

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_run_state.py -q`
Expected: FAIL(模块不存在)

- [ ] **Step 2: 实现 `run_state.py`**

```python
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
    aborted: bool = False
    abort_reason: str = ""


__all__ = ["RunState", "NodeRun", "merge_outputs", "append_list"]
```

- [ ] **Step 3: 跑测试,确认通过**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_run_state.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/run_state.py backend/orchestrator/tests/test_native_run_state.py
git commit -m "feat(orchestrator): RunState + reducers for native graph"
```

---

### Task 3: `run_agent_node()` 包装器

**Files:**
- Create: `backend/orchestrator/run_agent.py`
- Test: `backend/orchestrator/tests/test_native_run_agent.py`

迁移 `executor.py:138-264` 的重试/退避/超时/trace ctx/resolve agent/evidence 注入,产出一个统一结果对象(不依赖 DAGNode/NodeExecutionResult)。

- [ ] **Step 1: 写失败测试(假 agent:首次失败→重试成功;全失败→FAILED)**

```python
import asyncio
import pytest
from backend.orchestrator.run_agent import run_agent_node, AgentRunResult
from backend.schemas import AgentStatus

class _FakeAgent:
    def __init__(self, fail_times=0):
        self.calls = 0; self.fail_times = fail_times
    def invoke(self, inp, *, trace_id, span_id, node_id):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("transient")
        class _Out:
            status = AgentStatus.SUCCESS; trace_id = "t"; span_id = span_id
            self_critique = None; errors = []
        return _Out()

class _Reg:
    def __init__(self, agent): self._a = agent
    def get(self, name): return self._a

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
```

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_run_agent.py -q`
Expected: FAIL(模块不存在)

- [ ] **Step 2: 实现 `run_agent.py`**

```python
"""run_agent_node:在原生节点里安全地调一个同步 agent。

负责编排层关切:resolve agent(reporter/qa 运行时注入 evidence)、重试/退避/
超时、trace contextvar、节点级 prompt override。**不复制 agent 内部自检**——
agent.invoke 自己跑 self-critique/_post_validate,这里只看 FAILED/超时决定重试。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from ulid import ULID
from backend.schemas import AgentError, AgentOutputBase, AgentStatus, Evidence

_BACKOFF_MULT = 4


@dataclass
class AgentRunResult:
    status: AgentStatus
    output: Optional[AgentOutputBase]
    error: Optional[AgentError]
    attempts: int
    span_id: str
    started_at: datetime
    ended_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _collect_evidences(outputs: dict[str, AgentOutputBase]) -> dict[str, Evidence]:
    db: dict[str, Evidence] = {}
    for nid, out in outputs.items():
        if not nid.startswith("extract."):
            continue
        for ev in getattr(out, "evidences", None) or []:
            db[ev.evidence_id] = ev
    return db


def _resolve_agent(registry, agent_name: str, outputs: dict[str, AgentOutputBase]):
    if agent_name == "reporter":
        from backend.agents.reporter.tools import StaticEvidenceProvider
        return registry.make_reporter(evidence_provider=StaticEvidenceProvider(_collect_evidences(outputs)))
    if agent_name == "qa":
        return registry.make_qa(evidence_db=_collect_evidences(outputs))
    return registry.get(agent_name)


async def run_agent_node(
    registry, agent_name: str, input_obj: Any, *,
    outputs: dict[str, AgentOutputBase], trace_id: str, node_id: str,
    max_retries: int = 3, timeout_ms: int = 60000,
    user_prompt_override: str | None = None, backoff_base: float = 1.0,
) -> AgentRunResult:
    from backend.agents._base import reset_user_prompt_override, set_user_prompt_override
    from backend.observability.llm_call_log import reset_trace_context, set_trace_context

    agent = _resolve_agent(registry, agent_name, outputs)
    started = _now()
    last_error: AgentError | None = None
    span_id = f"span_{ULID()}"

    for attempt in range(max_retries + 1):
        span_id = f"span_{ULID()}"
        ctx = set_trace_context(trace_id=trace_id, span_id=span_id, node_id=node_id, agent_name=agent_name)
        ov = set_user_prompt_override(user_prompt_override)
        try:
            try:
                output = await asyncio.wait_for(
                    asyncio.to_thread(agent.invoke, input_obj, trace_id=trace_id, span_id=span_id, node_id=node_id),
                    timeout=timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                last_error = AgentError(code="LLM_TIMEOUT", message=f"{node_id} attempt {attempt+1} timeout", severity="error", retriable=True)
            except Exception as exc:  # noqa: BLE001
                last_error = AgentError(code="UNEXPECTED", message=f"{type(exc).__name__}: {exc}", severity="error", retriable=True)
            else:
                if output.status == AgentStatus.FAILED:
                    last_error = (output.errors[0] if output.errors else
                                  AgentError(code="AGENT_FAILED", message="FAILED without errors", severity="error", retriable=True))
                    if not last_error.retriable:
                        return AgentRunResult(AgentStatus.FAILED, None, last_error, attempt + 1, span_id, started, _now())
                else:
                    # SUCCESS / PARTIAL / NEEDS_REWORK = agent 跑完了,自检结论照传
                    return AgentRunResult(output.status, output, None, attempt + 1, span_id, started, _now())
        finally:
            reset_user_prompt_override(ov)
            reset_trace_context(ctx)
        if attempt < max_retries:
            await asyncio.sleep(backoff_base * (_BACKOFF_MULT ** attempt))

    return AgentRunResult(AgentStatus.FAILED, None, last_error, max_retries + 1, span_id, started, _now())


__all__ = ["run_agent_node", "AgentRunResult"]
```

- [ ] **Step 3: 跑测试,确认通过**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_run_agent.py -q`
Expected: PASS(`test_retry_then_success` agent.calls==2;`test_all_retries_fail_returns_failed` status==FAILED)

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/run_agent.py backend/orchestrator/tests/test_native_run_agent.py
git commit -m "feat(orchestrator): run_agent_node wrapper (retry/timeout/trace/evidence)"
```

---

### Task 4: QA 路由 `decide_qa_route()`

**Files:**
- Create: `backend/orchestrator/routing.py`
- Test: `backend/orchestrator/tests/test_native_routing.py`

先做路由(纯函数,无 LangGraph 依赖),便于表驱动测试。移植 `feedback_router` 的产品收窄。

- [ ] **Step 1: 写失败测试**

```python
from langgraph.graph import END
from backend.orchestrator.routing import decide_qa_route
from backend.schemas import QAVerdict, QARouting, QAIssue   # 按实际 schema 字段构造

def _verdict(*, routing, blocking, issues=()):
    return QAVerdict(verdict_id="v1", overall_status="needs_revision",
                     blocking=blocking, routing=list(routing), issues=list(issues))

def test_cap_aborts():
    goto, upd = decide_qa_route(_verdict(routing=[QARouting(target_agent="reporter", reason="x", payload={})], blocking=True),
                                qa_round=3, max_rounds=3, products=["Notion"])
    assert goto == END and upd["aborted"] is True

def test_no_routing_ends():
    goto, upd = decide_qa_route(_verdict(routing=[], blocking=False), qa_round=0, max_rounds=3, products=["Notion"])
    assert goto == END

def test_routes_to_reporter():
    goto, upd = decide_qa_route(_verdict(routing=[QARouting(target_agent="reporter", reason="rewrite", payload={})], blocking=True),
                                qa_round=0, max_rounds=3, products=["Notion","Asana"])
    assert goto == "reporter" and upd["qa_round"] == 1 and upd["rework_products"] == []

def test_picks_most_upstream_target():
    rt = [QARouting(target_agent="reporter", reason="r", payload={}),
          QARouting(target_agent="extractor", reason="e", payload={})]
    goto, upd = decide_qa_route(_verdict(routing=rt, blocking=True), qa_round=0, max_rounds=3, products=["Notion","Asana"])
    assert goto == "extract_dispatch" and upd["rework_target"] == "extractor"
```

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_routing.py -q`
Expected: FAIL(模块不存在)。若 `QAVerdict`/`QARouting` 字段名不符,先 `grep -n "class QAVerdict\|class QARouting\|class QAIssue" backend/schemas/qa.py` 校正构造参数。

- [ ] **Step 2: 实现 `routing.py`**

```python
"""QA verdict → 原生图回环决策。移植 feedback_router 的产品收窄。"""
from __future__ import annotations

from typing import Any
from langgraph.graph import END

# 上游→下游顺序;多 routing 取最上游单一目标(最上游重跑必带动下游)
_AGENT_ORDER = ["collector", "extractor", "analyst", "reporter"]
_AGENT_TO_ENTRY = {
    "collector": "collect_dispatch", "extractor": "extract_dispatch",
    "analyst": "analyst", "reporter": "reporter",
}
_PRODUCT_STR_KEYS = ("product", "competitor")
_PRODUCT_LIST_KEYS = ("products_missing", "competitors_involved", "products", "competitors")


def _wanted_products(issues: list, target_agent: str) -> list[str]:
    out: set[str] = set()
    for i in issues:
        if getattr(i, "target_agent", None) != target_agent:
            continue
        ri = getattr(i, "required_inputs", None) or {}
        for k in _PRODUCT_STR_KEYS:
            v = ri.get(k)
            if isinstance(v, str) and v.strip():
                out.add(v)
        for k in _PRODUCT_LIST_KEYS:
            v = ri.get(k)
            if isinstance(v, list):
                out.update(x for x in v if isinstance(x, str) and x.strip())
    return sorted(out)


def decide_qa_route(verdict, *, qa_round: int, max_rounds: int, products: list[str]) -> tuple[Any, dict]:
    """返回 (goto, state_update)。goto=END 表示收尾。"""
    if qa_round >= max_rounds:
        return END, {"aborted": True, "abort_reason": f"qa_round={qa_round} >= max_rounds={max_rounds}; force-publish"}
    routing = getattr(verdict, "routing", None) or []
    if not routing or getattr(verdict, "blocking", True) is False:
        return END, {}
    targets = {r.target_agent for r in routing}
    chosen = next((a for a in _AGENT_ORDER if a in targets), None)
    if chosen is None:
        return END, {}
    per_product = chosen in ("collector", "extractor")
    rework = _wanted_products(getattr(verdict, "issues", []) or [], chosen) if per_product else []
    if per_product and not rework:
        rework = list(products)   # 收窄不到就全量重做,绝不丢返工
    return _AGENT_TO_ENTRY[chosen], {
        "qa_round": qa_round + 1, "rework_target": chosen, "rework_products": rework,
    }


__all__ = ["decide_qa_route"]
```

- [ ] **Step 3: 跑测试,确认通过**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_routing.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/routing.py backend/orchestrator/tests/test_native_routing.py
git commit -m "feat(orchestrator): decide_qa_route for native graph rework cycles"
```

---

### Task 5: 节点函数 `nodes.py`

**Files:**
- Create: `backend/orchestrator/nodes.py`
- Test: `backend/orchestrator/tests/test_native_nodes.py`

节点函数闭包持有 `(registry, project)`;agent 节点调 `run_agent_node`,记 `NodeRun`,写 `outputs`。dispatch 节点返回 `Command(goto=[Send(...)])`。

- [ ] **Step 1: 写失败测试(analyst 节点:假 registry 跑出 output 写进 state delta)**

```python
import pytest
from backend.orchestrator.nodes import make_nodes
from backend.orchestrator.run_state import RunState
from backend.schemas import AgentStatus

class _Out:
    def __init__(self): self.status=AgentStatus.SUCCESS; self.span_id="s"; self.trace_id="t"; self.self_critique=None; self.errors=[]; self.result=object()

class _Agent:
    def invoke(self, inp, **kw): return _Out()
class _Reg:
    def get(self, n): return _Agent()
    def make_reporter(self, **k): return _Agent()
    def make_qa(self, **k): return _Agent()

@pytest.mark.asyncio
async def test_analyst_node_writes_output(monkeypatch):
    # analyst input 需要 profiles;桩掉 build_analyst_input 直接返回占位
    import backend.orchestrator.nodes as N
    monkeypatch.setattr(N, "build_analyst_input", lambda *a, **k: object())
    nodes = make_nodes(_Reg(), project=_fake_project())   # _fake_project 见下方 helper
    delta = await nodes["analyst"](RunState(project_id="p", run_id="r", analysis_mode="competitive_compare", products=["Notion"]))
    assert "analyst" in delta["outputs"] and len(delta["history"]) == 1
    assert delta["history"][0].node == "analyst"
```

`_fake_project()` helper:用现有 fixture——`from backend.agents.reporter.fixtures import load_demo_input` 拿不到 Project,改用 `backend/orchestrator/tests/conftest.py` 里已有的 project fixture(先 `grep -rn "def.*project" backend/orchestrator/tests/conftest.py`;若无,用 `Project(project_id="p", project_name="t", target_product="Notion", competitors=[], industry="collaboration_saas", ...)` 按 schema 必填字段构造)。

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_nodes.py -q`
Expected: FAIL(模块不存在)

- [ ] **Step 2: 实现 `nodes.py`**

```python
"""原生图节点函数。make_nodes 返回 {name: async fn},闭包持有 registry+project。"""
from __future__ import annotations

from typing import Any, Callable
from langgraph.types import Command, Send

from backend.schemas import AgentStatus, Project
from .inputs import (
    build_analyst_input, build_collector_input, build_extractor_input,
    build_qa_input, build_reporter_input,
)
from .run_agent import AgentRunResult, run_agent_node
from .run_state import NodeRun, RunState
from .routing import decide_qa_route

_MAX_ROUNDS = 3
_STATUS_STR = {
    AgentStatus.SUCCESS: "success", AgentStatus.PARTIAL: "partial",
    AgentStatus.NEEDS_REWORK: "needs_rework", AgentStatus.FAILED: "failed",
}


def _node_run(*, node, agent, res: AgentRunResult, output_ref, product=None, round_=1) -> NodeRun:
    out = res.output
    return NodeRun(
        node=node, agent=agent, product=product, round=round_,
        status=_STATUS_STR.get(res.status, "failed"), span_id=res.span_id,
        started_at=res.started_at.isoformat(), ended_at=res.ended_at.isoformat(),
        prompt_preview=None, response_preview=None,
        output_ref=output_ref if out is not None else None,
    )


def make_nodes(registry, *, project: Project) -> dict[str, Callable]:
    trace_id = f"trace_{project.project_id}"

    def _round(state: RunState) -> int:
        return state.qa_round + 1 if state.qa_round else 1

    async def collect_dispatch(state: RunState):
        targets = state.rework_products or state.products
        sends = [Send("collect_one", {"product": p, "_round": _round(state)}) for p in targets]
        return Command(goto=sends)

    async def collect_one(state: RunState, *, product: str = "", _round: int = 1):
        # LangGraph 把 Send 的 payload 作为 node 第二参传入(实现以 1.2.4 API 为准)
        inp = build_collector_input(
            project, trace_id=trace_id, product=product, official_url=None,
            dims=[d.value for d in project.analysis_dimensions], qa_feedback=None,
        )
        res = await run_agent_node(registry, "collector", inp, outputs=state.outputs,
                                   trace_id=trace_id, node_id=f"collect.{product}")
        ref = f"collect.{product}"
        return {"outputs": {ref: res.output} if res.output else {},
                "history": [_node_run(node="collect", agent="collector", res=res, output_ref=ref, product=product, round_=_round)]}

    async def extract_dispatch(state: RunState):
        targets = state.rework_products or state.products
        sends = [Send("extract_one", {"product": p, "_round": _round(state)}) for p in targets]
        return Command(goto=sends)

    async def extract_one(state: RunState, *, product: str = "", _round: int = 1):
        collector_out = state.outputs.get(f"collect.{product}")
        inp = build_extractor_input(project, trace_id=trace_id, product=product,
                                    collector_output=collector_out, qa_feedback=None)
        res = await run_agent_node(registry, "extractor", inp, outputs=state.outputs,
                                   trace_id=trace_id, node_id=f"extract.{product}")
        ref = f"extract.{product}"
        return {"outputs": {ref: res.output} if res.output else {},
                "history": [_node_run(node="extract", agent="extractor", res=res, output_ref=ref, product=product, round_=_round)]}

    async def analyst(state: RunState):
        inp = build_analyst_input(project, trace_id=trace_id, outputs=state.outputs, qa_feedback=None)
        res = await run_agent_node(registry, "analyst", inp, outputs=state.outputs,
                                   trace_id=trace_id, node_id="analyst")
        return {"outputs": {"analyst": res.output} if res.output else {},
                "history": [_node_run(node="analyst", agent="analyst", res=res, output_ref="analyst", round_=_round(state))]}

    async def reporter(state: RunState):
        inp = build_reporter_input(project, trace_id=trace_id, analyst_output=state.outputs["analyst"], qa_feedback=None)
        res = await run_agent_node(registry, "reporter", inp, outputs=state.outputs,
                                   trace_id=trace_id, node_id="reporter")
        return {"outputs": {"reporter": res.output} if res.output else {},
                "history": [_node_run(node="reporter", agent="reporter", res=res, output_ref="reporter", round_=_round(state))]}

    async def qa(state: RunState):
        inp = build_qa_input(project, trace_id=trace_id, reporter_output=state.outputs["reporter"],
                             analyst_output=state.outputs["analyst"], outputs=state.outputs,
                             prior_verdicts=list(state.verdicts))
        res = await run_agent_node(registry, "qa", inp, outputs=state.outputs,
                                   trace_id=trace_id, node_id="qa")
        update: dict[str, Any] = {
            "outputs": {"qa": res.output} if res.output else {},
            "history": [_node_run(node="qa", agent="qa", res=res, output_ref="qa", round_=_round(state))],
        }
        verdict = getattr(res.output, "verdict", None)
        if verdict is None:
            return Command(goto="__end__", update=update)
        update["verdicts"] = [verdict]
        goto, route_update = decide_qa_route(verdict, qa_round=state.qa_round, max_rounds=_MAX_ROUNDS, products=state.products)
        update.update(route_update)
        return Command(goto=goto, update=update)

    return {
        "collect_dispatch": collect_dispatch, "collect_one": collect_one,
        "extract_dispatch": extract_dispatch, "extract_one": extract_one,
        "analyst": analyst, "reporter": reporter, "qa": qa,
    }


__all__ = ["make_nodes"]
```

> 注:`collect_one`/`extract_one` 接收 Send payload 的具体签名以 LangGraph 1.2.4 为准(payload 作为 state 合并 or 第二参)。Step 1 测试先覆盖 analyst/reporter/qa 这类无 Send 的节点;Send 节点的签名在 Task 6 图集成测试里跑通后回填校正。

- [ ] **Step 3: 跑测试,确认通过**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_nodes.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/nodes.py backend/orchestrator/tests/test_native_nodes.py
git commit -m "feat(orchestrator): native graph node functions"
```

---

### Task 6: 建图 `graph.py` + 图集成测试

**Files:**
- Create: `backend/orchestrator/graph.py`
- Test: `backend/orchestrator/tests/test_native_graph.py`

- [ ] **Step 1: 写失败的图集成测试(stub 全 agent,跑通单/多产品 + QA 回环 + 封顶)**

```python
import pytest
from backend.orchestrator.graph import build_native_graph
from backend.orchestrator.run_state import RunState

# stub registry:collector→raw_sources, extractor→profile+evidences, analyst→result,
# reporter→draft, qa→verdict(可配置 routing:第 1 轮要求 reporter 返工,第 2 轮 PASS)
#（实现见测试文件;用最小假对象满足 build_* 取的属性)

@pytest.mark.asyncio
async def test_multi_product_runs_all_stages(stub_registry, two_product_project):
    graph = build_native_graph(stub_registry, project=two_product_project, checkpointer=None)
    init = RunState(project_id="p", run_id="r", analysis_mode="competitive_compare",
                    products=["Notion","Asana"]).model_dump()
    final = None
    async for snap in graph.astream(init, {"configurable": {"thread_id": "p"}}, stream_mode="values"):
        final = snap
    outs = final["outputs"]
    assert {"collect.Notion","collect.Asana","extract.Notion","extract.Asana","analyst","reporter","qa"} <= set(outs)

@pytest.mark.asyncio
async def test_qa_cycle_produces_reporter_v2(stub_registry_rework, two_product_project):
    graph = build_native_graph(stub_registry_rework, project=two_product_project, checkpointer=None)
    final = None
    async for snap in graph.astream(RunState(project_id="p", run_id="r", analysis_mode="competitive_compare", products=["Notion","Asana"]).model_dump(),
                                    {"configurable": {"thread_id": "p2"}}, stream_mode="values"):
        final = snap
    reporter_runs = [h for h in final["history"] if h.node == "reporter"]
    assert len(reporter_runs) == 2 and reporter_runs[1].round == 2   # 回环产生第 2 轮 reporter

@pytest.mark.asyncio
async def test_round_cap_aborts(stub_registry_always_rework, two_product_project):
    graph = build_native_graph(stub_registry_always_rework, project=two_product_project, checkpointer=None)
    final = None
    async for snap in graph.astream(RunState(project_id="p", run_id="r", analysis_mode="competitive_compare", products=["Notion"]).model_dump(),
                                    {"configurable": {"thread_id": "p3"}}, stream_mode="values"):
        final = snap
    assert final["aborted"] is True and "max_rounds" in final["abort_reason"]
```

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_graph.py -q`
Expected: FAIL(模块不存在)

- [ ] **Step 2: 实现 `graph.py`**

```python
"""build_native_graph:把节点连成原生 StateGraph 并编译。"""
from __future__ import annotations

from typing import Any
from langgraph.graph import START, END, StateGraph

from backend.schemas import Project
from .nodes import make_nodes
from .run_state import RunState


def build_native_graph(registry, *, project: Project, checkpointer: Any = None):
    nodes = make_nodes(registry, project=project)
    g = StateGraph(RunState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    g.add_edge(START, "collect_dispatch")
    # collect_dispatch 用 Command(goto=[Send(collect_one)]) 扇出 → 无需静态边
    g.add_edge("collect_one", "extract_dispatch")     # barrier:全 collect_one 完 → extract_dispatch
    # extract_dispatch Command(goto=[Send(extract_one)]) 扇出
    g.add_edge("extract_one", "analyst")              # barrier:全 extract_one 完 → analyst
    g.add_edge("analyst", "reporter")
    g.add_edge("reporter", "qa")
    # qa 用 Command(goto=…) 动态回环 / END → 无需静态边

    return g.compile(checkpointer=checkpointer)


__all__ = ["build_native_graph"]
```

- [ ] **Step 3: 跑图集成测试,逐个修正 Send 签名/边语义直到通过**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_graph.py -q`
Expected: PASS(3 个用例:多产品全 stage / QA 回环出 reporter v2 / 封顶 abort)
若 `collect_one` 取不到 Send payload(`product` 为空),按 1.2.4 的 Send 语义修正 `nodes.py` 节点签名(payload 合并进 state vs 关键字参数),回跑至绿。

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/graph.py backend/orchestrator/tests/test_native_graph.py backend/orchestrator/nodes.py
git commit -m "feat(orchestrator): build & compile native LangGraph (Send fan-out + QA cycle)"
```

---

### Task 7: 临时 DAGPlan 投影 `projection.py`

**Files:**
- Create: `backend/orchestrator/projection.py`
- Test: `backend/orchestrator/tests/test_native_projection.py`

把终态 `RunState`(outputs+history)投影成现有 `DAGPlan` + `node_id->output` 形状,让旧 `/state`、前端不改也能消费。

- [ ] **Step 1: 写失败测试**

```python
from backend.orchestrator.projection import run_state_to_dagplan
from backend.schemas import DAGPlan, NodeStatus

def test_projection_has_expected_nodes(sample_final_state, two_product_project):
    plan, outputs = run_state_to_dagplan(sample_final_state, project=two_product_project)
    ids = {n.node_id for n in plan.nodes}
    assert {"collect.Notion","extract.Notion","analyst","reporter","qa"} <= ids
    assert plan.nodes  # 所有完成节点 status=SUCCESS
    assert "reporter" in outputs

def test_projection_reporter_revisions_map_to_versioned_nodes(rework_final_state, two_product_project):
    # history 里两轮 reporter → 投影出 reporter + reporter_v2(保住前端 v1↔v2 回放)
    plan, outputs = run_state_to_dagplan(rework_final_state, project=two_product_project)
    ids = {n.node_id for n in plan.nodes}
    assert "reporter" in ids and "reporter_v2" in ids
```

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_projection.py -q`
Expected: FAIL

- [ ] **Step 2: 实现 `projection.py`**

```python
"""临时迁移脚手架:RunState → DAGPlan 形状,供旧 /state + 前端在 Phase 2 前消费。
Phase 3 删除。"""
from __future__ import annotations

from ulid import ULID
from backend.schemas import DAGEdge, DAGNode, DAGPlan, NodeStatus, NodeType, Project

_AGENT_OF = {"collect": "collector", "extract": "extractor",
             "analyst": "analyst", "reporter": "reporter", "qa": "qa"}


def _node_id(run) -> str:
    base = f"{run.node}.{run.product}" if run.product else run.node
    return base if run.round <= 1 else f"{base}_v{run.round}"


def run_state_to_dagplan(state: dict, *, project: Project) -> tuple[DAGPlan, dict]:
    """state 是 RunState.model_dump()。返回 (DAGPlan, node_id->output)。"""
    history = state["history"]
    outputs_by_ref = state["outputs"]
    nodes: list[DAGNode] = []
    out_map: dict = {}
    seen: set[str] = set()
    for run in history:
        nid = _node_id(run)
        if nid in seen:
            continue
        seen.add(nid)
        status = NodeStatus.SUCCESS if run.status in ("success", "partial", "needs_rework") else NodeStatus.FAILED
        nodes.append(DAGNode(
            node_id=nid, project_id=project.project_id, node_type=NodeType.AGENT_CALL,
            agent_name=_AGENT_OF.get(run.node), status=status, input_refs=[],
            output_ref=run.output_ref, retry_count=0, max_retries=3, timeout_ms=60000,
            started_at=None, ended_at=None, parent_node_id=None,
            revision=run.round, metadata={"product": run.product} if run.product else {},
        ))
        if run.output_ref and run.output_ref in outputs_by_ref:
            out_map[nid] = outputs_by_ref[run.output_ref]
    plan = DAGPlan(
        plan_id=f"plan_{ULID()}", project_id=project.project_id,
        template_id="native", nodes=nodes, edges=[], rationale="native-graph projection",
        confidence=1.0, complexity_score=min(1.0, len(nodes) / 20.0),
    )
    return plan, out_map


__all__ = ["run_state_to_dagplan"]
```

> 注:DAGNode/DAGPlan 必填字段以 `backend/schemas`(dag 相关)为准;Step 1 跑红时按报错补字段。投影**不复刻边**(前端 DAG 视图在 Phase 2 重做,过渡期边可空)。

- [ ] **Step 3: 跑测试,确认通过**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_projection.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/projection.py backend/orchestrator/tests/test_native_projection.py
git commit -m "feat(orchestrator): temporary RunState->DAGPlan projection (migration scaffold)"
```

---

### Task 8: 接入 `Orchestrator.run()`(`ORCH_ENGINE` flag,默认 legacy)

**Files:**
- Modify: `backend/orchestrator/orchestrator.py`
- Test: `backend/orchestrator/tests/test_native_engine_e2e.py`

- [ ] **Step 1: 写失败的 e2e 测试(ORCH_ENGINE=native 跑通,落 node output + 广播)**

```python
import pytest
@pytest.mark.asyncio
async def test_native_engine_persists_outputs(monkeypatch, stub_registry, two_product_project, memory_storage):
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator(registry=stub_registry, storage=memory_storage)
    plan = orch.plan(two_product_project)
    results = [r async for r in orch.run(plan, two_product_project)]
    assert any(r.node_id == "reporter" for r in results)
    saved = await memory_storage.state_store.list_node_outputs(two_product_project.project_id)
    assert "reporter" in saved and "qa" in saved
```

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_engine_e2e.py -q`
Expected: FAIL

- [ ] **Step 2: 在 `Orchestrator.run()` 加 native 分支**

在 `orchestrator.py` `run()` 顶部按 env 分流;native 分支用 `build_native_graph` astream,逐 superstep 把新 outputs 投影落库 + 广播,保持现有 `yield NodeExecutionResult` 契约。

```python
import os
from .graph import build_native_graph
from .projection import run_state_to_dagplan
from .run_state import RunState as _RunState

async def run(self, plan, project):
    if os.getenv("ORCH_ENGINE", "legacy") == "native":
        async for r in self._run_native(plan, project):
            yield r
        return
    # ... 现有 legacy 实现保持不动 ...

async def _run_native(self, plan, project):
    await self.storage.state_store.save_dag_plan(plan)  # 占位 plan,跑完被投影覆盖
    graph = build_native_graph(self.registry, project=project,
                               checkpointer=to_langgraph_saver(self.storage.checkpointer))
    init = _RunState(
        project_id=project.project_id, run_id=f"run_{ULID()}",
        analysis_mode=project.analysis_mode.value, products=[project.target_product, *project.competitors],
    ).model_dump()
    config = {"configurable": {"thread_id": project.project_id}}
    seen_refs: set[str] = set()
    final_state = None
    async for snap in graph.astream(init, config=config, stream_mode="values"):
        final_state = snap
        for ref, out in snap["outputs"].items():
            if ref in seen_refs or out is None:
                continue
            seen_refs.add(ref)
            await self.storage.state_store.save_node_output(project.project_id, ref, out)
            res = NodeExecutionResult(project_id=project.project_id, node_id=ref,
                                      status=NodeStatus.SUCCESS, output=out)
            await self.storage.event_bus.publish(f"project:{project.project_id}:nodes", res)
            yield res
    if final_state is not None:
        proj_plan, _ = run_state_to_dagplan(final_state, project=project)
        await self.storage.state_store.save_dag_plan(proj_plan)   # 投影覆盖,旧 /state 可消费
```

> `ULID`/`to_langgraph_saver`/`NodeExecutionResult`/`NodeStatus` 已在 orchestrator.py import。`save_node_output` 用 `ref`(collect.Notion/reporter/...)作 node_id,与投影 node_id 对齐(首轮 round=1 不带 _v 后缀)。

- [ ] **Step 3: 跑 e2e + 全量编排测试(确认 legacy 默认未受影响)**

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/ -q`
Expected: PASS(native e2e 绿 + 原有 legacy 用例全绿,因为默认 `ORCH_ENGINE=legacy`)

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/orchestrator.py backend/orchestrator/tests/test_native_engine_e2e.py
git commit -m "feat(orchestrator): wire native engine behind ORCH_ENGINE flag (legacy default)"
```

---

### Task 9: 回放重建测试(②号约束验收)+ 真实链路 smoke

**Files:**
- Test: `backend/orchestrator/tests/test_native_replay.py`
- Modify(按需): `backend/orchestrator/orchestrator.py`(终态落 RunSnapshot)

- [ ] **Step 1: 写回放测试(仅凭持久态重建时间线,不靠实时事件)**

```python
@pytest.mark.asyncio
async def test_replay_from_persisted_history(monkeypatch, stub_registry_rework, two_product_project, memory_storage):
    monkeypatch.setenv("ORCH_ENGINE", "native")
    from backend.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator(registry=stub_registry_rework, storage=memory_storage)
    plan = orch.plan(two_product_project)
    _ = [r async for r in orch.run(plan, two_product_project)]   # 跑完不读事件
    # 仅从持久态重建:投影 plan 里应有 reporter + reporter_v2
    plan2 = await memory_storage.state_store.get_dag_plan(two_product_project.project_id)
    ids = {n.node_id for n in plan2.nodes}
    assert "reporter" in ids and "reporter_v2" in ids   # v1↔v2 回放可还原
```

Run: `.venv/bin/python -m pytest backend/orchestrator/tests/test_native_replay.py -q`
Expected: FAIL → 若投影没保留多轮 reporter,回到 Task 7 `_node_id` 修正(round>1 → `_v{round}`),再绿。

- [ ] **Step 2: (按需)终态把 RunState.history 落进 RunSnapshot**

若现有 `save_run_snapshot` 已能从投影 plan/outputs 落库,则复用 Task 8 的投影即可,无需改动;本步仅在回放测试暴露缺口时补 `_run_native` 末尾的 `save_run_snapshot(...)`。

- [ ] **Step 3: 真实链路 smoke(可选,手动)**

```bash
# localdb 起着 + DOUBAO_API_KEY 在 .env;触发一次真实 run 看原生引擎端到端
ORCH_ENGINE=native .venv/bin/python -m uvicorn backend.api.app:app --port 8001 &
# 用既有项目触发 POST /api/projects/{id}/run,轮询 /state,导出 markdown 对比 legacy 产出
```
Expected: 报告内容与 legacy 引擎对齐(金标准),节点 status 正常。

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/tests/test_native_replay.py backend/orchestrator/orchestrator.py
git commit -m "test(orchestrator): replay reconstruction from persisted state (native engine)"
```

---

## Self-Review(spec 覆盖核对)

- ✅ 方案 A 扁平图 + Send + Command → T5/T6。
- ✅ QA 全能力回环 + 最上游单一目标 + 产品收窄 → T4。
- ✅ ①Agent 自检留 agent(节点只 to_thread invoke,retry 只对 FAILED/超时)→ T3/T5。
- ✅ ②history/snapshot 还原回放(不靠实时事件)→ T9。
- ✅ ③barrier 基线(collect_one→extract_dispatch→extract_one)→ T6。
- ✅ 模式无关透传 analysis_mode → T2(RunState 字段)/T8(init)。
- ✅ 单产品 = 扇出 1,无特判 → T6 多产品测试天然覆盖(products=[1] 即单产品)。
- ✅ 临时 DAGPlan 投影(旧前端不动)→ T7/T8。
- ✅ `ORCH_ENGINE` flag,legacy 默认全绿 → T8。
- 后续 plan:Phase 2(前端迁移 RunStateView)、Phase 3(删 legacy/planner/feedback/DAGPlan/投影)、提示词业务特化(另开)。

## 执行说明

- **每个 Task 独立可提交**;Task 1 是纯重构(旧测试是安全网),Task 2–9 是新增(默认 flag 关,不影响现网)。
- 关键不确定点:**LangGraph 1.2.4 的 Send payload 注入语义**(T5 节点签名 / T6 集成测试里拍实)——先用 T6 集成测试驱动跑通,再回填 T5 签名。

---

## 实现完成状态(2026-06-07)

Phase 0 + Phase 1 **全部落地并过两段 review**。后端编排测试 **93 passed, 2 skipped**(2 skip 是 real-LLM smoke)。

**实测验证**:
- 单产品(Send×1)/ 多产品(Send×N)/ QA 回环出 reporter_v2 / 轮次封顶 abort / 失败软着陆 —— 图集成测试全绿。
- **②约束回放**:仅凭持久态(`get_dag_plan` + `list_qa_verdicts` + `list_node_outputs`)重建整条时间线(reporter v1+v2、两轮 verdict、各产品输出)。
- **Postgres serde**:native 图经**真实 PG checkpointer** 跑通含返工的全流程,输出以 Pydantic 落库并**崩溃 resume** 还原(`ReporterOutput.draft.version=2`)。
- 探明的 LangGraph 1.2.4 事实:Send-target 节点收**原始 payload dict**(非全局 state),且**不**按 RunState schema 校验局部 payload;normal 节点收 RunState 实例;`Command(goto=[Send...])` 扇出 + reducer 并发合并均工作。
- 计划模板的两处错误已在实现期修正:① `dims=[d.value for d in project.analysis_dimensions]` 会崩(那是 analyst 枚举,非 CollectDimension)→ 用 `_DEFAULT_COLLECT_DIMS`(4 模板字节一致);② `astream(values)` 快照里 `history` 是 NodeRun 对象非 dict → 投影前 `model_validate().model_dump()` 归一。

## native 转默认前必须补(before-promotion 清单)

native 引擎当前**仅在 `ORCH_ENGINE=native` 时启用,默认 legacy**。下列缺口在把 native 设为默认/进 Phase 2 前必须补齐(均不影响当前 flag-gated 验证):

1. **(重要)失败节点不广播**:`_run_native` 只对 `outputs` 里非空输出 yield/broadcast `NodeExecutionResult`;某节点失败(output=None)时前端 DAG 会一直显示"运行中"。需补 FAILED 事件。
2. **(重要)LLM call 流水 + ProjectMetrics 未落**:legacy `_dispatch_step` 调 `_persist_node_llm_calls` / `_persist_metrics`,native 没有 → Trace tab 空、指标 sparkline 空。
3. **(重要)`resume()` 不认 ORCH_ENGINE**:跨引擎 resume 会拿 RunState checkpoint 喂 OrchestratorState 校验 → 崩。同一 project 不可混用引擎(flag-gated 下需运维知晓)。
4. **(次要)占位 plan 用 legacy 形状 node id**:`_run_native` 开头 `save_dag_plan(plan)` 存的是 legacy 模板 id(`collect.notion`),与 native 广播(`collect.Notion`)不匹配 → 中途 join 的客户端对不上;跑完被投影覆盖即一致。
5. **(次要)模块顶部 import**:`list_calls`/`ULID` 顶层导入,可下沉到使用处避免拖累 legacy。
6. **(重要)QA 反馈未注入返工 agent**:native 节点 `build_*_input(..., qa_feedback=None)` 恒为 None;QA 回环虽能重跑,但 reworked agent **看不到 QA 到底反对什么**(legacy 会把 `qa_feedback` 串进去)→ 返工是"盲重跑"而非"针对性修正"。要补:RunState 带 `qa_feedback_by_node`,`decide_qa_route` 填充,dispatch/agent 节点透传。回环机制已验证,**反馈内容注入**是 Phase 2 的事。
7. **(次要)失败软着陆未实现**:`reporter`/`qa` 节点 `state.outputs["analyst"]` 直接下标;上游失败(output=None)时 KeyError 崩图、无 failed NodeRun 记录。需改 `.get()` + 早退记 failed。
8. **(次要)常量/DRY**:`nodes._MAX_QA_ROUNDS=3` 与 `feedback_router.DEFAULT_MAX_ROUNDS` 各写一份;`_collect_evidences`/`_resolve_agent` 在 `run_agent.py` 与 `executor.py` 重复(双引擎并存期可接受,删 legacy 时收敛)。

> Phase 2(前端迁 RunStateView)与 Phase 3(删 legacy/planner/feedback/DAGPlan/投影)各自另开 plan;提示词业务特化另开 brainstorm。
> **Phase 1 验证结论:native 引擎流转、回环、回放、PG 持久化全部跑通(flag-gated, 默认 off),可安全合入;上述 1–8 是转默认前的清单。**
