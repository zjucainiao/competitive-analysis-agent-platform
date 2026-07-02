# DAG 任务编排

> 本文档定义 Orchestrator 的编排设计：节点状态、图拓扑、QA 回环、超时/降级、checkpoint。
>
> **两套引擎**，由 `ORCH_ENGINE` 选择（默认 `native`，`backend/orchestrator/orchestrator.py:141`）：
> - **NATIVE（默认）**：`backend/orchestrator/graph.py` 装配 `StateGraph(RunState)`，**拓扑固定**，
>   并行扇出经 `Send`、回环经 `Command(goto=...)`（原生）。本文以 native 为准。
> - **LEGACY（`ORCH_ENGINE=legacy`）**：`backend/orchestrator/orchestrator.py` 的 dispatch-loop
>   引擎（`StateGraph(OrchestratorState)`），用 `DAGPlan`/`DAGNode`/`NodeType`/`FeedbackRouter`，保留向后兼容。
>   §3 的 `NodeType` 枚举、§5 自适应 Planner、§9 `Orchestrator` API 描述的是 legacy 体系。

---

## 1. 设计目标

| 目标 | 落地点（native） |
|---|---|
| 任务流可视化、可追溯 | 每个节点执行落一条 `NodeRun` history，前端用 React Flow（`@xyflow/react`）渲染投影 |
| 真正的反馈闭环 | QA verdict 经 `decide_qa_route` 决定回到哪个上游入口节点，图经 `Command(goto=...)` 回环 |
| 容错与降级 | 节点级超时、`build_*_input` fail-soft、降级到 partial 输出，最终 qa 统一收尾 |
| 并行加速 | `collect_dispatch` / `extract_dispatch` 经 `Send` 对每个竞品并行扇出（互不阻塞） |

> 行业适配靠**固定模板选择**（§4.2 `industry_id → 模板`），不是运行时动态生成节点。自适应 LLM
> Planner（§5）非默认路径。native 引擎不改变拓扑，但**消费** `DAGPlan` 的节点元数据：官网种子
> `official_url`、采集维度、每节点超时/重试经 `extract_plan_directives`
> （`backend/orchestrator/plan_directives.py`）折叠进 `RunState.plan_directives`，由各节点消费（§6.3）。

---

## 2. 节点执行状态（agent status → NodeRun.status）

native 引擎**不维护** PENDING/READY/RUNNING/SKIPPED 生命周期状态机——那是 LangGraph 调度器内部的事，
我们不复制。每次节点执行只折叠成一条 `NodeRun` history 记录（`backend/orchestrator/run_state.py:74`），
其 `status` 取值四态，由 Agent 返回的 `AgentStatus` 直接映射（`backend/orchestrator/nodes.py:73`，
`_STATUS_MAP`）：

| `NodeRun.status` | 来源 `AgentStatus` | 含义 |
|---|---|---|
| `success` | `AgentStatus.SUCCESS` | 正常完成 |
| `partial` | `AgentStatus.PARTIAL` | 部分字段缺失，但产出可用（**不**再合并进 success） |
| `needs_rework` | `AgentStatus.NEEDS_REWORK` | Agent 自评需返工 |
| `failed` | `AgentStatus.FAILED` | 失败；或 `build_*_input` 上游缺失 fail-soft（`_build_failed_run`，`nodes.py:104`） |

> 枚举定义见 `backend/schemas/agent_io.py:11`（`AgentStatus`）。`partial` 是独立持久态，并非映射回
> `success`。返工不靠「重置节点为 PENDING」，而是图回环重新执行同一节点函数（见 §7）。

---

## 3. native 图拓扑（固定）

`build_native_graph`（`backend/orchestrator/graph.py`）装配 7 个节点函数（`backend/orchestrator/nodes.py`
的 `make_nodes`），拓扑在所有返工轮中**保持不变**，没有「创建新节点 + 加边」。节点分两类：

- **normal 节点**（经静态边/Command 到达，入参是 `RunState`，可读全局 channel）：
  `collect_dispatch` / `extract_dispatch` / `analyst` / `reporter` / `qa`。
- **Send-target 节点**（经 `Send("node", payload)` 到达，入参是裸 `payload` dict，**看不到全局 state**）：
  `collect_one` / `extract_one`。dispatch 节点须把 worker 所需一切打包进 Send payload。

```
START
  │ add_edge(START, collect_dispatch)
  ▼
collect_dispatch ──Command(goto=[Send("collect_one", {product,…}) ×N])──┐  扇出，每竞品一个
                                                                        ▼
                                                              collect_one ×N（并行）
                                                                        │ add_edge(collect_one, extract_dispatch)
                                                                        ▼  barrier：全部完成后只跑一次
extract_dispatch ──Command(goto=[Send("extract_one", {product, collector_output,…}) ×N])──┐
                                                                        ▼
                                                              extract_one ×N（并行）
                                                                        │ add_edge(extract_one, analyst)
                                                                        ▼  barrier：全部完成后只跑一次
                                                                    analyst
                                                                        │ add_edge(analyst, reporter)
                                                                        ▼
                                                                    reporter
                                                                        │ add_edge(reporter, qa)
                                                                        ▼
                                                                      qa ──Command(goto=…)──┐
                                                                        │                   │
                                          goto=END（发布/熔断）          │                   │ goto=上游入口节点
                                                                        ▼                   ▼ （返工，见 §7）
                                                                       END        collect_dispatch / extract_dispatch
                                                                                   / analyst / reporter
```

要点（实现见 `graph.py`、`nodes.py`）：

- `collect_dispatch` / `extract_dispatch` / `qa` **无静态出边**——全靠 `Command(goto=...)` 动态扇出/路由。
- worker（`collect_one` / `extract_one`）**有**静态出边接到下游 barrier（`graph.py:44,46`）；barrier 在所有
  同名 worker 完成后只跑一次，看到 reducer 合并后的全局 state。
- 并发写经 `RunState` 的 reducer 合并：`outputs` 用 `merge_outputs`、`history`/`verdicts` 用 `append_list`
  （`run_state.py:90,97,108-110`）。
- 没有 `CONDITIONAL` 节点：行业适配在图装配前由模板选择完成（§4.2）；QA 回环判断在 `qa` 节点内由
  `decide_qa_route` 完成。

> `NodeType` 枚举（START/END/AGENT_CALL/PARALLEL_FORK/PARALLEL_JOIN/CONDITIONAL/FEEDBACK）属于
> **legacy** 体系（`backend/schemas/dag.py`，被 `planner.py` / `orchestrator.py` 使用）。native 图不用它。

---

## 4. 行业模板与产品扇出

### 4.1 扇出形状（协作办公示例，3 竞品）

native 图对每个竞品产品**并行**采集→抽取，再汇聚到全局 analyst/reporter/qa：

```
                         collect_dispatch
                    ┌──────────┼──────────┐         （Send ×3）
                    ▼          ▼          ▼
            collect_one  collect_one  collect_one
              Notion       ClickUp      Asana
                    └──────────┼──────────┘
                               ▼  barrier
                        extract_dispatch
                    ┌──────────┼──────────┐         （Send ×3）
                    ▼          ▼          ▼
            extract_one  extract_one  extract_one
              Notion       ClickUp      Asana
                    └──────────┼──────────┘
                               ▼  barrier
                            analyst   ← 一次，聚合所有 extract.* profile
                               ▼
                            reporter
                               ▼
                              qa  ──→ END 或回环（§7）
```

> analyst 是**单个全局节点**（聚合全部 profile 做多维对比），不是「每维度一个 analyst 节点」。采集维度
> 优先取 plan 的 `collect_dimensions`（经 `RunState.plan_directives`，由 `collect_dispatch` 打进 Send
> payload——Send-target 看不到全局 state）；plan 缺省时回退 `nodes.py` 的 `_DEFAULT_COLLECT_DIMS`
> （homepage/features/pricing/help_docs/user_reviews）。官网种子 `official_url` 同路径流入 collector
> input（命中即免搜索兜底）。

### 4.2 行业模板（industry_id → 模板）

`industry_id → 模板文件名` 映射定义在 `_INDUSTRY_TEMPLATE_MAP`（`backend/orchestrator/planner.py:33`，
该处注释显式锁定「docs/DAG.md § 4.2」）。未命中时回退到 `{industry}_standard`（`planner.py:114`）：

| `project.industry` | 模板文件 |
|---|---|
| `collaboration_saas` | `collab_saas_standard.yaml` |
| `crm_saas` | `crm_saas_standard.yaml` |
| `cross_border_ecommerce_saas` | `cross_border_standard.yaml` |
| `edu_saas` | `edu_saas_standard.yaml` |

```
backend/orchestrator/templates/
├── collab_saas_standard.yaml
├── crm_saas_standard.yaml
├── cross_border_standard.yaml
└── edu_saas_standard.yaml
```

> native 引擎按选定 industry 装配固定拓扑；YAML 模板（含 `for_each`/`depends_on` 展开、`NodeType`
> 节点）由 `Planner._expand`（`planner.py:135`）消费成 `DAGPlan`。native 引擎不用 `DAGPlan` 定拓扑，
> 但会经 `extract_plan_directives` 消费其节点元数据（模板 `product_urls` 官网种子 /
> `collect_dimensions` / 超时重试）。两者共享 industry→模板映射。

---

## 5. 自适应 Planner（非默认）

> **不是默认路径**（默认 `mode="template"`）。native 引擎不直接调用 Planner，但两种模式产出的
> `DAGPlan` 元数据都会被 native 经 `extract_plan_directives` 消费（§4.2、§6.3）。

`Planner` 支持 `mode="adaptive"`（`backend/orchestrator/planner.py:74`，`AdaptivePlanner`，需在
`__init__` 传 `llm`），用 LLM 推断 URL + 维度，输出仍是 `DAGPlan`。`mode="auto"` 先试 adaptive 失败回退
template；默认 `mode="template"`。

---

## 6. 调度策略

### 6.1 并行度

- 每个竞品产品一个 `Send`，由 LangGraph 调度器并行执行 `collect_one` / `extract_one`（`nodes.py:158,231`）。
- 并发上限由 LangGraph 运行时与各 Agent 内部约束（如 collector 的 `constraints.timeout_seconds` 与抓取
  上限）决定，编排层不再单设固定并发数。

### 6.2 失败处理

- 重试发生在 `run_agent_node` 内部（指数退避）：普通失败按 `max_retries` 重试——优先取 plan 的
  `max_retries`（经 `plan_directives`），plan 缺省时为 3；**超时不重试**（`TimeoutError` 直接
  FAILED，`code=LLM_TIMEOUT`——同步 invoke 无协作式取消，重试会与僵尸线程并发烧配额）。
  图层面无节点级重试循环。
- `build_*_input` 因上游缺失抛 `BuildInputError` 时 fail-soft：
  返回一条 `status="failed"` 的 `NodeRun`（`_build_failed_run`，`nodes.py:104`），不崩图。
- reporter/qa 见上游 `None` 时早退，最终 `qa` 节点统一标 `aborted`（`nodes.py:408`）优雅收尾。

### 6.3 超时

节点级超时**优先取 plan**：planner 精调的 `timeout_ms` 经 `extract_plan_directives` 进
`RunState.plan_directives`（Send worker 由 dispatch 打进 payload），喂给 `run_agent_node`。
plan 缺省时回退下限表 `NODE_TIMEOUT_FLOOR_MS`（`backend/orchestrator/plan_directives.py`）；
低于下限的 plan 值会在提取时被钳到下限（该表是「节点超时连锁失败」事故后精调的下限）：

| 节点 | 超时下限 |
|---|---|
| collector | 300s |
| extractor | 300s |
| analyst | 240s |
| reporter | 240s |
| qa | 180s |

> 真实采集一个产品可触发 100+ 次 LLM 调用（search + 多页抓取 + page_type 分类 + 身份校验），
> 故 collector/extractor 下限 300s；过短会撞超时→节点 failed→下游连锁「upstream output missing」。
> 高于下限的 plan 值保留（如 adaptive planner 给 reporter 的 600s）。

### 6.4 降级

- Collector 失败 → 可落 Mock 数据（hybrid 模式）。
- Extractor 部分字段缺失 → 返回 `partial`。
- 上游全失败 → `build_*_input` fail-soft 记 failed，下游 `None` 接力早退，`qa` 标 `aborted`。
- QA 无输出（verdict 为 None）→ `Command(goto=END)` 直接收尾（`nodes.py:460`）。

### 6.5 checkpoint

LangGraph 的 checkpointer 在每步后落 `RunState`；崩溃可由 `resume` 从最近 checkpoint 继续。
thread 作用域为 `{project_id}::{run_id}`（`native_thread_config`，`orchestrator.py:72`）。
checkpointer 实现见 §8。

---

## 7. 反馈闭环：QA 回环（native Command(goto)）

native 引擎**不创建新节点、不加边**。`qa` 节点跑完 QA 后，把本轮 `verdict` 交给纯函数
`decide_qa_route`（`backend/orchestrator/routing.py:61`），它返回 `(goto, state_update)`，`qa` 节点据此
返回 `Command(goto=goto, update={...})`（`nodes.py:464`）让图原生回环到某个**已存在的上游入口节点**。

`decide_qa_route` 规则（按优先级，`routing.py:93`+）：

1. `qa_round+1 >= max_rounds`（=3）→ `goto=END`，`aborted=True`（强制发布，off-by-one 已修）。
2. routing 为空或 `verdict.blocking is False` → `goto=END`（正常结束）。
3. **无提升即停**：本轮是返工轮且维度均分相比上一轮 `Δ < 0.01`（`_MIN_ROUND_IMPROVEMENT`）→ `goto=END`，
   `aborted=True`，发布最优轮（API 层用 `best_round_reporter_key` 择优，绝不发更差版本）。
4. 否则取 routing 中**最上游** `target_agent`（`_AGENT_ORDER`），映射到图入口节点（`_AGENT_TO_ENTRY`）：

   | `target_agent` | goto 入口节点 |
   |---|---|
   | collector | `collect_dispatch` |
   | extractor | `extract_dispatch` |
   | analyst | `analyst` |
   | reporter | `reporter` |

   - per-product Agent（collector/extractor）：从 `issues.required_inputs` 收窄 `rework_products`；收窄为空
     则回退全量 products（绝不丢返工）。
   - `state_update` 含 `qa_round+1` / `rework_target` / `rework_products` / `qa_feedback_by_node`
     （键约定 `collect.{product}` / `extract.{product}` / `analyst` / `reporter`，dispatch 节点据键取
     payload 注入 `build_*_input`）。

> **同一拓扑重跑**：回环不产生新图节点。再次执行的同名节点用 `versioned_ref`（`run_state.py:15`）写
> `{base}_v{round}`（如 `reporter_v2`、`collect.飞书_v2`）作 outputs key——`_v{n}` 只是**版本化 output 键
> 后缀**，不是新图节点。`latest_output` 取最新轮产物（`run_state.py:32`）。
>
> **轮次上限**：最多 3 轮 QA + 无提升即停。详见 [QA.md](QA.md) § 7。`RunState` 里**没有** routing_queue。

---

## 8. 与 LangGraph 的映射 + checkpointer

native 概念在 LangGraph 中的对应：

| 我们 | LangGraph（native） |
|---|---|
| 一次 run | `graph.ainvoke/astream`，input 是 `RunState`（`run_state.py:102`） |
| 节点函数 | `StateGraph.add_node(name, fn)`（`graph.py:39`） |
| barrier 边 | `add_edge("collect_one","extract_dispatch")` 等（`graph.py:42-48`） |
| 并行扇出 | dispatch 节点返回 `Command(goto=[Send("collect_one", payload) ×N])` |
| QA 回环 | `qa` 节点返回 `Command(goto=上游入口节点 / END, update=...)` |
| 并发写合并 | channel reducer：`merge_outputs`（outputs）/ `append_list`（history、verdicts） |
| 节点状态 | 维护在 `RunState`，由 checkpointer 持久化 |

**checkpointer**（`backend/storage/__init__.py`）：

- `InMemoryCheckpointer`（`mode="memory"`）/ `PostgresCheckpointer`（`mode="postgres"`，`storage/__init__.py:103`）。
- 经 `to_langgraph_saver`（`storage/langgraph_adapter.py`）适配为 LangGraph saver（`orchestrator.py:195`）。
- **不是** `MemorySaver` / `RedisSaver`。**Redis 是事件总线**（节点进度广播），不是 checkpoint 存储。

> `RunState` 里**没有** `routing_queue`，也**没有** `nodes: dict[str, DAGNode]`——回环靠 `Command(goto=...)`，
> 节点版本靠 outputs key 的 `_v{n}` 后缀（§7）。

---

## 9. Orchestrator 关键 API

入口仍是 `Orchestrator`（`backend/orchestrator/orchestrator.py`），但 `run()` 内部按 `ORCH_ENGINE` 分流
（`orchestrator.py:141`）：默认走 `_run_native`（装配 `build_native_graph` 并 astream），`legacy` 走
dispatch-loop。

```python
class Orchestrator:
    def plan(self, project, *, template_id=None) -> DAGPlan:
        """加载 YAML 模板 → DAGPlan（legacy 形状；native 投影另算，
        但其元数据经 extract_plan_directives 被 native 消费）。"""

    async def run(self, plan, project, *, run_id=None, seed_state=None) -> AsyncIterator[NodeExecutionResult]:
        """ORCH_ENGINE=native（默认）→ _run_native；=legacy → dispatch-loop。"""

    async def resume(self, project_id, ...) -> AsyncIterator[NodeExecutionResult]:
        """从 checkpoint 续跑（native：_resume_native，复算同一 thread）。"""
```

> native 回环决策不在 `Orchestrator` 上，而在纯函数 `decide_qa_route`（`routing.py`）+ `qa` 节点内
> 的 `Command(goto=...)`。`seed_state` 让从头跑的 run 携带 `prompt_override_by_node` /
> `rework_target` / `qa_feedback_by_node` 等定向意图（`orchestrator.py:120`）。

---

## 10. 实现位置

**native 引擎（默认）**：

```
backend/orchestrator/
├── graph.py            # build_native_graph：装配 StateGraph(RunState)
├── nodes.py            # make_nodes：7 个节点函数（消费 plan_directives）
├── plan_directives.py  # extract_plan_directives / resolve_node_limits / NODE_TIMEOUT_FLOOR_MS
├── routing.py          # decide_qa_route：QA verdict → (goto, state_update)
├── run_state.py        # RunState / NodeRun / reducer / versioned_ref / latest_output
├── inputs.py           # build_*_input：组装各 Agent 输入（fail-soft）
├── run_agent.py        # run_agent_node：跑单个 Agent + 重试/超时
├── projection.py       # outputs/history → 前端 DAGPlan 投影
├── orchestrator.py     # Orchestrator.run 分流 + _run_native / _resume_native
└── templates/          # 行业 YAML 模板（industry→模板，§4.2）
```

**legacy 引擎（`ORCH_ENGINE=legacy`）**：`orchestrator.py`（dispatch-loop）、`state.py`
（`OrchestratorState`）、`executor.py`（节点执行+重试+超时）、`feedback_router.py`（QARouting→新节点）、
`planner.py`（模板/自适应）。

---

## 11. 前端可视化

前端用 **React Flow**（`@xyflow/react`）渲染编排图：

- 节点：颜色对应 `NodeRun.status`（success / partial / needs_rework / failed）。
- 实时更新：经事件总线（Redis）广播节点进度，后端经 WebSocket 推送到前端。
- 点击节点：右侧抽屉显示 Trace 详情（prompt / input / output / token / 工具调用）。
- 迭代历史：返工轮的 `_v{n}` 版本经 `parent_node_id`（`projection.py:99`）串联，可看完整迭代链。

UI 细节见 [OBSERVABILITY.md](OBSERVABILITY.md)。
