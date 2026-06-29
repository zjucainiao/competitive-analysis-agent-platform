# Orchestrator · DAG 任务编排器

> v1 已落地。详细设计见 [docs/DAG.md](../../docs/DAG.md)，存储/事件契约见 [docs/STORAGE.md](../../docs/STORAGE.md)。

## 职责

- 接收 `Project`，按 industry 加载 YAML 模板 → 生成 `DAGPlan`
- 按拓扑顺序异步调度，单层并发上限 `max_parallel`（默认 4）
- 节点级重试（指数退避 1s/4s/16s）+ 超时（asyncio.wait_for）+ 降级（Collector hybrid → mock）
- QA 反馈路由：把 `QARouting` 翻译成 `_v{n+1}` 版本节点 + 下游 PENDING
- 通过 `backend.storage` 持久化 DAGPlan / NodeOutput / QAVerdict，广播 `NodeExecutionResult` 到 EventBus
- 六态节点状态机（PENDING / READY / RUNNING / SUCCESS / FAILED / NEEDS_REWORK / SKIPPED）

## 模块结构

```
backend/orchestrator/
├── __init__.py
├── orchestrator.py       # 主类：plan / run / resume
├── state.py              # OrchestratorState（LangGraph StateGraph schema）
├── planner.py            # YAML 模板 → DAGPlan（for_each 展开 + 通配 depends_on）
├── executor.py           # 单节点执行：input 解包 / 重试 / 超时 / 降级
├── feedback_router.py    # QARouting → _v{n+1} 节点 + 下游 reset
├── agent_registry.py     # name → BaseAgent 实例（mock / hybrid / real）
├── templates/
│   └── collab_saas_standard.yaml
└── tests/
    ├── test_planner.py
    ├── test_executor.py
    ├── test_feedback_router.py
    └── test_e2e_mock.py
```

## 关键 API

```python
from backend.orchestrator import Orchestrator, AgentRegistry, Planner
from backend.storage import build_storage

storage = build_storage(mode="memory")  # 或 mode="postgres"
orch = Orchestrator(
    registry=AgentRegistry(mode="mock"),
    storage=storage,
)

plan = orch.plan(project)                      # → DAGPlan
async for result in orch.run(plan, project):   # → NodeExecutionResult 流
    print(result.node_id, result.status)

# 崩溃恢复：从最近 checkpoint 续跑
async for result in orch.resume(project_id, project):
    ...
```

## DAG 模板（v1）

仅落地 `collab_saas_standard.yaml`（协作办公 SaaS 场景）。结构：

```
start
  ↓
collect.{notion,clickup,asana}      # 并行采集（4 维度：homepage/features/pricing/help_docs）
  ↓                                  # 1:1 依赖
extract.{notion,clickup,asana}      # 并行抽取
  ↓
join_extract (parallel_join)
  ↓
analyst (多维度 + 多产品 一次 LLM 调用)
  ↓
reporter (template-driven 渲染 + 引用强制)
  ↓
qa (6 维度 checker)
  ↓
end
```

QA 反馈：

```
qa.verdict.routing = [{target_agent: reporter, ...}]
  ↓
FeedbackRouter.apply()
  ↓ 新节点：reporter_v2 (revision=2, parent_node_id=reporter, qa_feedback 注入)
  ↓ 重设：qa.input_refs=[reporter_v2], qa.status=PENDING, end.status=PENDING
  ↓
继续调度 → reporter_v2 → qa → end
```

`qa_round_count` 上限默认 3，超过强制 END，详见 `FeedbackRouter`。

## 调度细节

- **READY 判定**：`node.status == PENDING` 且所有 `input_refs` 都已 SUCCESS / SKIPPED
- **执行**：`asyncio.to_thread(agent.invoke, ...)` 包到 `asyncio.wait_for` 控超时
- **Agent 状态映射**：SUCCESS / PARTIAL / NEEDS_REWORK 三种都映射为 `NodeStatus.SUCCESS`（output 可被下游消费）；只有 `AgentStatus.FAILED` 触发重试
- **降级**：Collector + `project.mode=hybrid` + `constraints.fallback_to_mock=True` + 重试用尽 → 切 `Collector(mock=True)` 重跑

## LangGraph 集成

LangGraph `StateGraph` 单节点循环：

```
START → dispatch ←┐
         ↓        │ should_continue: 还有 PENDING/RUNNING → loop
         └────────┘
         ↓        should_continue: 全部终态 / aborted → END
        END
```

`dispatch` 节点一轮做 4 件事：
1. `_find_ready_nodes` → 并发执行 Agent
2. 把结果写回 `plan.nodes[i].status` + `outputs`
3. 持久化（`state_store.save_node_output`、`update_node_status`）+ 广播（`event_bus.publish`）
4. 处理 QA routing → 调 `FeedbackRouter` → 应用到 plan

LangGraph 的 checkpointer 通过 `backend.storage.langgraph_adapter.to_langgraph_saver()` 接到 `CheckpointerProtocol`，每轮 dispatch 后自动落 checkpoint。

## 运行模式

| mode | Collector | Extractor | Analyst | Reporter | QA | 何时用 |
|---|---|---|---|---|---|---|
| `mock` | mock | mock | mock | mock | mock | 单测 / 演示链路 |
| `hybrid` | 真实采集 | mock | mock | mock | mock | demo（已有 Collector v1 真采） |
| `real` | 真实 | 真实 | 真实 | 真实 | 真实 | LLMProvider 落地后开启 |

mode 由 `AgentRegistry(mode=...)` 决定；`Project.mode` 仅用于 Collector 降级开关。

## 测试

```bash
pytest backend/orchestrator/tests/
```

51 个测试覆盖：
- Planner（15）：YAML 展开 / 通配 / 模板未找到 / metadata
- Executor（15）：控制节点 / Agent 调用 / input 解包 / 超时重试 / hybrid 降级
- FeedbackRouter（17）：单轮 / 多轮版本递增 / collector per-product / max_rounds 上限 / qa_feedback payload
- E2E mock（4）：完整链路 + QA 反馈闭环 + storage 落盘 + WS 事件流

## 后续计划

- `AdaptiveLLMPlanner`（取代 YAML 模板加载）
- 真实 LLMProvider / Tracer 接入（real 模式）
- `CONDITIONAL` 节点支持
