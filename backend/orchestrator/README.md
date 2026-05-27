# Orchestrator · DAG 任务编排器

> 详细设计见 [docs/DAG.md](../../docs/DAG.md)。

## 职责

- 接收项目配置，生成 DAG（v1 用固定模板，v2 用自适应 Planner）
- 按拓扑顺序调度节点
- 处理依赖、并行、超时、重试
- 接收 QA 路由，触发上游重做
- 管理六态节点状态机
- 注入 trace_id / span_id
- 向前端推送实时状态（WebSocket）

## 实现位置

```
backend/orchestrator/
├── __init__.py
├── orchestrator.py        # 主类
├── state.py               # LangGraph StateGraph 的 state schema
├── planner.py             # v1: 模板加载；v2: Planner LLM
├── executor.py            # 节点执行 + 重试 + 超时 + 降级
├── feedback_router.py     # QARouting → 创建新节点
├── templates/
│   ├── collab_saas_standard.yaml
│   ├── crm_saas_standard.yaml
│   └── cross_border_standard.yaml
├── README.md
└── tests/
```

## 关键 API

```python
class Orchestrator:
    def plan(self, project: Project) -> DAGPlan: ...
    def run(self, plan: DAGPlan, project: Project) -> AsyncIterator[NodeExecutionResult]: ...
    def handle_qa_routing(self, routing: list[QARouting], state: DAGState) -> DAGState: ...
    def resume(self, project_id: str) -> AsyncIterator[NodeExecutionResult]: ...
```

## 关键约束

- 所有 Agent 调用走 `BaseAgent.invoke()`，统一注入 trace
- 节点状态实时持久化（PG）+ 实时推送（WebSocket）
- 反馈边触发的新节点 = 老节点 input + qa_feedback 注入，节点 id 加 `_v{n}` 后缀
- 防死循环：QA 循环上限 5 次，超出强制发布并告警

## 责任窗口

**O 窗口**。M1 末（所有 Agent 接口冻结）后开始，M2 完成 v1（固定模板），M5 完成自适应 Planner。
