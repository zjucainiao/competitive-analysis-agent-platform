# DAG 任务编排

> 本文档定义 Orchestrator 的 DAG 设计：节点类型、状态机、边语义、默认模板、自适应规划。

---

## 1. 设计目标

| 目标 | 落地点 |
|---|---|
| 任务流可视化、可追溯 | 节点 + 边显式建模，前端用 React Flow 渲染 |
| 真正的反馈闭环 | 反馈边支持把 QA 失败回路由到上游具体节点 |
| 灵活适配不同 query | 自适应 DAG：Orchestrator 根据输入动态生成节点 |
| 容错与降级 | 节点级重试、超时、降级到 partial 输出 |
| 并行加速 | 并行采集 / 并行抽取（不同竞品互不阻塞） |

---

## 2. 节点状态机

每个 `DAGNode` 在六态间流转：

```
        ┌────────┐
        │PENDING │  初始
        └───┬────┘
            │ 依赖满足
            ▼
        ┌────────┐
        │ READY  │  等待调度
        └───┬────┘
            │ Orchestrator 派发
            ▼
        ┌────────┐
        │RUNNING │
        └───┬────┘
            │
   ┌────────┼────────┬─────────────┐
   │        │        │             │
   ▼        ▼        ▼             ▼
┌───────┐┌───────┐┌────────────┐┌────────┐
│SUCCESS││FAILED ││NEEDS_REWORK││SKIPPED │
└───────┘└───┬───┘└─────┬──────┘└────────┘
             │          │
             │重试      │QA 路由回上游
             └──────────┘
             ▼
         回到 PENDING（重试计数 +1）
```

| 状态 | 含义 | 触发 |
|---|---|---|
| `PENDING` | 等待依赖 | 初始 |
| `READY` | 依赖满足，等调度 | 上游全部 SUCCESS |
| `RUNNING` | 执行中 | Orchestrator 派发 |
| `SUCCESS` | 正常完成 | Agent 返回 status=SUCCESS / PARTIAL |
| `FAILED` | 失败且不重试 | 异常超过 max_retries 或 retriable=False |
| `NEEDS_REWORK` | 被 QA 退回 | QA 路由命中本节点 |
| `SKIPPED` | 跳过（条件分支） | 条件不满足 |

`PARTIAL` 状态映射到 `SUCCESS`，但在 trace 中标记 `partial=True`。

---

## 3. 节点类型

```python
class NodeType(str, Enum):
    START         = "start"
    END           = "end"
    AGENT_CALL    = "agent_call"        # 调用某个 Agent
    PARALLEL_FORK = "parallel_fork"     # 分叉
    PARALLEL_JOIN = "parallel_join"     # 汇合
    CONDITIONAL   = "conditional"       # 条件分支
    FEEDBACK      = "feedback"          # 反馈回路（QA → 上游）
```

### 3.1 AGENT_CALL

最常见的节点类型。属性：

- `agent_name`：collector / extractor / analyst / reporter / qa
- `input_payload_ref`：上游节点输出的 ref
- `retry_policy`：`{max_retries: 3, backoff: "exponential"}`
- `timeout_ms`：默认 60s（采集）/ 30s（抽取/分析/报告/质检）

### 3.2 PARALLEL_FORK / JOIN

成对出现。FORK 把一个上游任务拆成 N 个并行节点；JOIN 等所有分支完成后汇合。

例：3 个竞品的采集并行：

```
        Collector(Notion)
       /
FORK ──── Collector(ClickUp) ──── JOIN ──── Extractor(并行 3 个) ── ...
       \
        Collector(Asana)
```

### 3.3 CONDITIONAL

根据上游输出决定走哪条路径。常用于：

- 行业判断：行业 = collab_saas → 用协作办公扩展 Schema
- 数据质量判断：covered_dimensions < 阈值 → 触发补采集

```python
class ConditionalNode(BaseModel):
    condition: ConditionExpression       # 简单 DSL
    branches:  list[Branch]
    default:   str                       # 默认下一节点
```

### 3.4 FEEDBACK

由 QA 输出的 `QARouting` 触发，把控制流回到指定上游节点（并把 `qa_feedback` 注入）。

**反馈边语义**：
- 不是简单"回到老节点重跑"，而是"创建一个新的节点实例，继承老节点的 input，额外注入 qa_feedback"
- 老节点保留在 DAG 中，新节点用 `_v2`, `_v3` 后缀
- 这样可在 UI 时间轴上看到完整迭代历史

---

## 4. 默认 DAG 模板

v1 阶段每个项目使用固定模板，覆盖最常见的"3 竞品 + 5 维度"场景。

### 4.1 协作办公场景（示例）

```
                        ┌─────────────┐
                        │   START     │
                        └──────┬──────┘
                               │
                        ┌──────▼──────┐
                        │ PLAN(static)│  v1 用模板，v2 用 Adaptive Planner
                        └──────┬──────┘
                               │
                  ┌────────────┼────────────┐
                  ▼            ▼            ▼
            ┌──────────┐ ┌──────────┐ ┌──────────┐
            │Collector │ │Collector │ │Collector │
            │ Notion   │ │ ClickUp  │ │  Asana   │
            │(homepage,│ │(homepage,│ │(homepage,│
            │ pricing, │ │ pricing, │ │ pricing, │
            │ docs,    │ │ docs,    │ │ docs,    │
            │ reviews) │ │ reviews) │ │ reviews) │
            └────┬─────┘ └────┬─────┘ └────┬─────┘
                 │            │            │
                 ▼            ▼            ▼
            ┌──────────┐ ┌──────────┐ ┌──────────┐
            │Extractor │ │Extractor │ │Extractor │
            │ Notion   │ │ ClickUp  │ │  Asana   │
            └────┬─────┘ └────┬─────┘ └────┬─────┘
                 └────────────┼────────────┘
                              ▼
                       ┌──────────────┐
                       │ JOIN (3 P)   │
                       └──────┬───────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
         ┌──────────┐   ┌──────────┐    ┌──────────┐
         │ Analyst  │   │ Analyst  │    │ Analyst  │
         │ feature  │   │ pricing  │    │ feedback │
         └────┬─────┘   └────┬─────┘    └────┬─────┘
              ▼               ▼               ▼
         ┌──────────┐   ┌──────────────────────┐
         │ Analyst  │   │ Analyst              │
         │  swot    │   │ differentiation      │
         └────┬─────┘   └──────────┬───────────┘
              └──────────┬─────────┘
                         ▼
                  ┌──────────────┐
                  │  Reporter    │
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐
                  │     QA       │
                  └──┬─────────┬─┘
                     │ pass    │ revise
                     ▼         ▼
                ┌─────────┐  ┌────────────────┐
                │   END   │  │ FEEDBACK ROUTE │
                └─────────┘  │ (回 C/E/A/R)   │
                             └────────────────┘
```

### 4.2 模板存放位置

```
backend/orchestrator/templates/
├── collab_saas_standard.yaml
├── crm_saas_standard.yaml
└── cross_border_standard.yaml
```

模板 YAML 示例：

```yaml
template_id: collab_saas_standard_v1
name: 协作办公标准 DAG
nodes:
  - id: start
    type: start
  - id: collect.{product}
    type: agent_call
    agent: collector
    for_each: project.competitors
    input:
      product_name: "{product}"
      industry: "collaboration_saas"
      dimensions: [homepage, pricing, help_docs, reviews]
  - id: join_collect
    type: parallel_join
    depends_on: ["collect.*"]
  ...
```

---

## 5. 自适应 DAG（INNOVATIONS.md 详述）

v2 阶段：Orchestrator 不直接套模板，而是用一个 **Planner LLM 调用**根据用户输入决定：

- 需要采集哪些 dimension
- 是否需要扩展到更多竞品
- 是否需要追加专项分析（如 AI 能力对比）
- DAG 复杂度匹配 query 复杂度

输出仍然是 DAG，但节点是 LLM 生成的。

Planner 自身也是一个特殊的"Orchestrator Agent"，受 Schema 约束：

```python
class DAGPlan(BaseModel):
    nodes: list[DAGNode]
    edges: list[DAGEdge]
    rationale: str                       # 为什么生成这个 DAG
    confidence: float
```

详见 [INNOVATIONS.md](INNOVATIONS.md) § 1。

---

## 6. 调度策略

### 6.1 并行度

- 同类型节点（如多个 Collector）默认并行，并发上限 4
- LLM 调用受 LLMProvider 限流约束
- 工具调用受 ToolRegistry 限流约束

### 6.2 重试

- 节点失败 → 检查 `retriable`，若 yes 则按指数退避重试（1s / 4s / 16s）
- `max_retries` 默认 3，可在节点定义中覆盖
- 重试达到上限 → 节点 FAILED，触发降级路径

### 6.3 超时

- 节点级超时：默认 60s（采集） / 30s（其他）
- 超时 → 节点 FAILED + 错误码 `LLM_TIMEOUT` / `TOOL_TIMEOUT`

### 6.4 降级

- Collector 失败 → 落 Mock 数据（hybrid 模式）
- Extractor 部分字段失败 → 返回 PARTIAL，未抽取字段 status=`unknown`
- Analyst 某维度失败 → 报告中标注"该维度数据不足"
- Reporter 失败 → 不允许降级，必须重试或人工介入
- QA 失败 → 默认通过但标注"未完成质检"

### 6.5 节点级 checkpoint

利用 LangGraph 的 checkpoint 机制，每个节点完成后状态可恢复。崩溃后能从最近 checkpoint 继续。

---

## 7. 反馈闭环：从 QA Routing 到节点创建

QA 输出 `QARouting`：

```python
class QARouting(BaseModel):
    target_agent: Literal["collector", "extractor", "analyst", "reporter"]
    reason:       str
    payload:      dict        # qa_feedback
```

Orchestrator 处理流程：

1. 解析 `target_agent`
2. 查找最近一个该 agent 的 SUCCESS 节点
3. 创建新节点（继承老节点 input + 注入 `qa_feedback`）
4. 新节点 id = `老节点_v{n}`
5. DAG 中加边：`新节点 → 下游节点们`
6. 下游节点状态置回 PENDING

**重做次数限制**：同一个 issue 反复出现 3 次仍未解决 → QA 标 `blocking=False`，允许发布。详见 [QA.md](QA.md) § 7。

---

## 8. 与 LangGraph 的映射

我们的概念在 LangGraph 中的对应：

| 我们 | LangGraph |
|---|---|
| `Project` | 一次 `graph.invoke()` 的 input state |
| `DAGNode` | `StateGraph.add_node()` |
| 依赖边 | `StateGraph.add_edge()` |
| `CONDITIONAL` 节点 | `StateGraph.add_conditional_edges()` |
| `FEEDBACK` 节点 | LangGraph 不原生支持，需要在 state 里维护 routing 队列 |
| 节点状态 | 我们自己维护在 state 里 + 持久化到 PG |
| Checkpoint | LangGraph `MemorySaver` / `RedisSaver` |

> **关键**：LangGraph 的 state 是 immutable update，我们在 state 上维护 `nodes: dict[str, DAGNode]` 和 `routing_queue: list[QARouting]`。

---

## 9. Orchestrator 关键 API

```python
class Orchestrator:
    def plan(self, project: Project) -> DAGPlan:
        """生成 DAG（v1 套模板，v2 用 Planner LLM）。"""
        ...

    def run(self, plan: DAGPlan, project: Project) -> AsyncIterator[NodeExecutionResult]:
        """异步执行 DAG，每个节点完成时 yield 结果，前端订阅。"""
        ...

    def handle_qa_routing(self, routing: list[QARouting], state: DAGState) -> DAGState:
        """处理 QA 反馈，更新 DAG 状态。"""
        ...

    def resume(self, project_id: str) -> AsyncIterator[NodeExecutionResult]:
        """从 checkpoint 恢复执行。"""
        ...
```

---

## 10. 实现位置

```
backend/orchestrator/
├── __init__.py
├── orchestrator.py        # 主类
├── state.py               # DAGState（LangGraph state schema）
├── planner.py             # v1: 模板加载；v2: Planner LLM
├── executor.py            # 节点执行 + 重试 + 超时
├── feedback_router.py     # QARouting → 创建新节点
├── templates/             # YAML 模板
└── tests/
```

O 窗口在 M0 后开始实现，M2 时点完成串联。

---

## 11. 前端可视化

前端用 **React Flow** 渲染 DAG：

- 节点：颜色对应状态（灰=pending，蓝=running，绿=success，红=failed，橙=needs_rework）
- 边：实线=依赖边，虚线=反馈边
- 实时更新：WebSocket 推送 `NodeExecutionResult`
- 点击节点：右侧抽屉显示 Trace 详情（prompt / input / output / token / 工具调用）
- 时间轴模式：按时间顺序展示节点执行（含 v1 / v2 迭代历史）

UI 细节见 [OBSERVABILITY.md](OBSERVABILITY.md)。
