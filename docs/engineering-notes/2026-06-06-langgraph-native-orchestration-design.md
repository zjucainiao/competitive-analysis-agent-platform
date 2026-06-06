# 设计:编排改成完全原生 LangGraph(每节点 = 一个 Agent)

> 2026-06-06 · 设计已逐段确认,待 review → 转实现计划。

**一句话**:现在 LangGraph 只是个"壳"(1 个 `dispatch` 节点 + 自循环,真 DAG 在自研 `DAGPlan`/`Executor` 解释器里)。改成**每个 agent 就是一个 StateGraph 节点**,用原生 `Send`/`Command`/条件回环/checkpoint 表达,并**连前端一起改**、丢掉 `DAGPlan`。环境:LangGraph 1.2.4(`Send`/`Command`/`interrupt`/子图/checkpoint 都可用)。

## 1. 目标图(方案 A · 扁平图 + Send + Command)

```
START
  → collect_dispatch ──Send×产品──→ collect_one ─┐
  → extract_dispatch ──Send×产品──→ extract_one ─┴─(fan-in)→ analyst → reporter → qa
                                                                                  │
                                          qa 节点 return Command(goto=…, update=…)│
        ┌──────────────────────────────────────────────────────────────────────┘
        ├─ goto="collect_dispatch" / "extract_dispatch" / "analyst" / "reporter"  (按 verdict 回环)
        └─ goto=END                                            (PASS 或 qa_round 触顶)
```

- 节点=agent;`collect_dispatch`/`extract_dispatch` 是轻量扇出节点,兼作 **QA 回环再入口**。
- 扇出:dispatch 读 `state.rework_products`(空=全部)→ 每产品 `Send`。首轮全量,返工轮只 Send 子集。
- **单产品 = 扇出数 1**(barrier 平凡直通),零特判 → 现有 `_prune_trivial_barriers` / 产品列表退化逻辑全删。
- 修订身份(reporter v1↔v2)从"`_v{n}` 节点"换成 `RunState.history` 里 append 的 `NodeRun`。

## 2. 决策清单(拍过的板,防反复)

1. 方向:**全原生 LangGraph + 连前端一起改**(丢 DAGPlan)。
2. 图结构:**方案 A**(扁平图 + Send + Command)。
3. QA 回环:**保持全能力**(任意上游,含按产品重采);一轮多 routing 取**最上游单一目标**(等价简化)。
4. ① **Agent 自检留在 agent**(Pydantic 校验 / self-critique / `_post_validate` / 禁用词 / evidence);LangGraph 只管流转。retry 只对 `FAILED`/超时,`PARTIAL`/`NEEDS_REWORK` 照常向下游流。
5. ② **回放真相源 = `history` + RunSnapshot**,不是 WS 事件。前端 **Pull 权威(`RunStateView`)+ Push 加速(WS 增量,可丢、重拉即对齐)**。
6. ③ **barrier 先跑通**;按产品流水线(链式 Send)作为后续优化,本次不做。
7. 编排**模式无关**:`analysis_mode` + 产品列表作为契约透传给 agent。
8. **单/多产品提示词业务特化 = 另开一轮**(对比 vs 调研口径、analyst 维度提示词拆两套),本次只留透传接口、不动提示词。

## 3. 迁移地图(删 / 留 / 移 / 新增)

| 现有 | 处置 |
|---|---|
| `orchestrator.py` `_dispatch_step`/`_find_ready_nodes`/`_apply_*` | **删**(自研解释器循环) |
| `planner.py` `_expand`/`for_each`/通配依赖/`_prune_trivial_barriers`/`target_plus_competitors` | **删**(图进代码) |
| `feedback_router.py` `_spawn_rework_node`/`_next_versioned_id`/`_bfs_downstream` | **删**(回环=Command goto) |
| `feedback_router.py` 产品收窄(`_wanted_products_from_issues` 等) | **移**→ `routing.py` |
| `executor.py` `_build_input` | **移**→ `inputs.py` |
| `executor.py` 重试/退避/超时/trace ctx/reporter·qa evidence 注入 | **移**→ `run_agent_node()` 包装器 |
| `DAGPlan`/`DAGNode`/`DAGEdge`、`OrchestratorState` | **删/换**→ `RunState`(迁移期临时留) |
| YAML 模板(nodes/edges) | **瘦身**→ 仅每行业配置(种子 URL、collect_dimensions) |
| `agent_registry.py`/`metrics.py`/`_persist_node_llm_calls`/storage(checkpointer/RunSnapshot) | **留** |
| 新增 | `graph.py`/`nodes.py`/`run_state.py`/`routing.py`/`inputs.py` + `RunStateView` 投影&读 API |

`RunState` 关键字段:`outputs`(reducer 合并)、`history`(reducer append)、`verdicts`(append)、`qa_round`(封顶 3)、`rework_products`/`rework_target`、`aborted`/`abort_reason`、`analysis_mode`/`products`。

`RunStateView`(前端契约,替代 DAGPlan):`{status, products, stages[{stage,agent,instances[]|revisions[]}], history[NodeRun], verdicts, qa_round}`。

## 4. 分阶段上线(每阶段可独立交付)

```
Phase 0  抽脚手架:_build_input + 重试包装器从 executor 抽到 inputs.py / run_agent_node(纯重构,老编排照跑)
Phase 1  原生后端:graph/nodes/routing + RunState;新引擎同时吐【临时 DAGPlan 投影】→ 老前端不动也能跑 → 行为对齐验证
Phase 2  前端迁移:RunStateView API + WS adapter;DAG/Trace/各 tab 切新契约
Phase 3  删旧:dispatch 解释器 / planner 展开 / feedback 加节点 / DAGPlan / 临时投影 → 全原生终态
```

**验证锚点**:① 图集成测试(单产品 Send×1 / 多产品 Send×N / QA 回环 history 出 reporter v2 + 轮次封顶 abort / 失败软着陆);② **仅凭 snapshot 重建 RunStateView**,断言时间线齐全;③ 同输入与现系统报告金标准对齐;④ 现有 agent/QA/api 测试全绿。

---

**范围外**:单/多产品提示词特化(另开一轮)、按产品流水线优化、LangGraph 生态(LangSmith/interrupt/子图,原生化后天然可接)。
