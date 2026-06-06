# Phase 2 · native 转生产可用 + RunStateView + 前端迁移

> 2026-06-07 · 自主执行(用户授权"全做完不打扰")。设计依据:已批准的原生化设计 + Phase 1 plan 末尾的 8 缺口清单 + 一轮并行理解(前端 25 文件 / 后端 API 面 / 8 缺口具体修法 / 存储面)。

## 自主锁定的决策(供醒来 review,可回退)

- **D1 native 转默认**:补完缺口 + 真实 LLM 跑通验证后,把 `ORCH_ENGINE` 默认从 legacy 改 native。**legacy 代码不删**(Phase 3 才删),`ORCH_ENGINE=legacy` 始终可切回。
- **D2 RunStateView 契约**:`{status, products, stages[{stage,agent,instances[]|revisions[]}], history[NodeRun], verdicts, qa_round, metrics}`。新增 `GET /projects/{id}/run-state`(在跑)+ `GET /runs/{run_id}/view`(历史);**保留** `/state`(经 DAGPlan 投影)作前端迁移期的桥。
- **D3 QA 反馈注入(gap 4)**:做。RunState 加 `qa_feedback_by_node`,routing 填充,节点透传 → 返工 agent 看得到 QA 反对什么(返工从"盲重跑"变"针对性修正")。
- **D4 投影桥**:前端迁移期保留 projection;不删 legacy。
- **D5 前端迁移走 adapter 层**:组件吃稳定内部模型(DagNodeRecord/TraceSpan/MockReport),只重写 adapter(`apiStateToDagData`/`apiStateToSpans`)+ types/client/hooks + 少数直读 plan.nodes 的(workspace-details-rail/execution-log-card)。tsc+build+Playwright 验证;若无法干净跑通则保持投影路径、不留破 app。

## 执行阶段(每阶段验证通过即 commit)

- **Stage A — 后端补缺口**(flag-gated, 全测试):gap 1 FAILED 广播、2 metrics 落库、3 LLM-call 落库、4 QA 反馈注入、5 resume 认引擎、6 占位 plan(改用空 plan 起步)、7 reporter/qa 失败软着陆。+ 把 `final_state.history` 落进 RunSnapshot。
- **Stage B — RunStateView 契约**:schema(schemas/project.py)+ assembler(RunState/history → view)+ 端点(runs.py)+ WS adapter。additive,测试。
- **Stage C — 翻默认 + 真实冒烟**:`ORCH_ENGINE` 默认 native;真实 LLM 端到端跑一次(经 projection,前端此时仍用 /state),验证 outputs/metrics/trace/report 全populate。
- **Stage D — 前端迁移**:adapter 层重写吃 RunStateView;tsc+build+Playwright 走查真实 run。
- **Stage E — 终审 + 文档收尾**。

## 8 缺口具体修法(理解阶段产出,带行号)

1. FAILED 广播(M):`_run_native` 跟踪 history 里 status=='failed' 的新 NodeRun → yield NodeExecutionResult(FAILED)。
2. metrics(M):astream 结束后 `compute_project_metrics(plan=proj_plan, outputs, verdicts, qa_round_count=final_state['qa_round'])` → save_project + metrics_history(复用 legacy `_persist_metrics` 419-441)。
3. LLM-call(S):每个非空 output 后 `_persist_node_llm_calls(project_id, NodeExecutionResult(node_id=ref, output=out))`(复用 legacy 375-397),try/except 不阻塞。
4. QA 反馈(L):RunState 加 `qa_feedback_by_node`;routing `decide_qa_route` 回第三元素填充(按 target_agent/product 聚 issues.required_inputs);dispatch 把 qa_feedback 进 Send payload、worker 取出;analyst/reporter/qa 从 state 取;透传给 build_*_input。
5. resume(M):`resume()` 顶部认 `ORCH_ENGINE`,native 走新 `_resume_native`(astream(None) 续 checkpoint)。
6. 占位 plan(M):`_run_native` 起步**不存 legacy 占位 plan**(改空 plan 或跳过),终态投影覆盖 → 消除 collect.notion vs collect.Notion 不匹配。
7. 失败软着陆(S):reporter/qa 节点 `state.outputs.get('analyst')`,缺则早退记 failed NodeRun。
8. 常量/DRY(S,cleanup,本轮可不做):`_MAX_QA_ROUNDS` 与 feedback_router 同步;executor 的 `_collect_evidences`/`_resolve_agent` Phase 3 收敛。

## 安全护栏
legacy 不删 · flag 可切回 · 每阶段验证通过才 commit · native 转默认前真实跑通 · 前端迁移不留破 app。
