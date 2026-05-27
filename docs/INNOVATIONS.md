# 前瞻性技术亮点

> 本文档定义平台的差异化亮点，对应评分要点：「技术方案有独特或前瞻性思考（如**自适应任务拆分、Agent 自评估、动态 Schema 演化**）」。
>
> 经与用户对齐，v1 落地 **3 个亮点**：自适应 DAG + Agent 自评估 + 决策回放。

---

## 1. 自适应 DAG（Adaptive Planning）

### 1.1 问题

传统做法：DAG 写死。问题：
- 用户只想"快速对比定价" → 还跑全套 5 维度，浪费
- 用户提供了一个"边缘竞品"（小众 SaaS）→ 默认采集源覆盖不到，Collector 失败
- 用户加了第 6 个竞品 → 模板没法应对

### 1.2 方案

Orchestrator 用一个 **Planner LLM 调用**根据 query 复杂度生成定制 DAG。Planner 自身受 Schema 约束：

```python
class DAGPlan(BaseModel):
    nodes: list[DAGNode]
    edges: list[DAGEdge]
    rationale: str               # 为什么生成这个 DAG
    confidence: float
    complexity_score: float      # 衡量任务复杂度
```

### 1.3 Planner 决策点

| 决策 | 依据 |
|---|---|
| 采集 dimensions 选哪些 | 用户选择的分析维度 + 竞品类型 |
| 是否扩展更多竞品 | 用户已列竞品的市场覆盖度 |
| 是否追加专项分析 | query 是否含特定关键词（"AI 能力"、"出海"） |
| 是否使用 self-consistency | 关键结论 stake 高低 |
| 并行度 | LLM 限流 + 工具配额 |

### 1.4 实现

```python
# backend/orchestrator/planner.py
class AdaptivePlanner:
    def plan(self, project: Project) -> DAGPlan:
        if project.mode == "fixed":
            return self._load_template(project.industry)

        # LLM-driven planning
        plan = self.llm.chat(
            system=PLANNER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": render_project(project)}],
            response_format=DAGPlan,
            tools=[get_available_dimensions, get_available_agents],
        )
        # 校验
        self._validate(plan)
        return plan
```

### 1.5 安全网

Planner 是 LLM 输出 → 必须严格 schema 校验 + 拓扑校验（无环、所有依赖可解析、所有 agent_name 在注册表中）。校验失败 → fallback 到默认模板。

### 1.6 评分价值

- "技术方案有独特或前瞻性思考（自适应任务拆分）" ← 直接命中

---

## 2. Agent 自评估（Self-Critique）

### 2.1 问题

传统做法：Agent 出结果就完事，质量好坏由 QA 兜底。问题：
- QA 才发现问题 → 一个完整循环代价高
- Agent 自己其实"知道"自己不确定，但没机制表达
- 无法在 Agent 之间传递"信心程度"

### 2.2 方案

每个 Agent 输出必含：

```python
class AgentOutputBase:
    confidence:    float       # [0, 1]
    self_critique: str         # 自评估文本
    ...
```

**强约束**：

| 条件 | 要求 |
|---|---|
| confidence < 0.6 | self_critique 必填具体原因 |
| 必填字段 null > 30% | confidence 自动 ≤ 0.6 |
| 多源冲突 | confidence 自动 ≤ 0.7 |
| 主动 needs_rework | status 标位，注释 reason |

### 2.3 Prompt 设计

每个 Agent 的 system prompt 包含 self-critique 指令：

```
After producing your structured output, evaluate it:
1. Are all required fields populated with verifiable data?
2. Did you use null for any field where source did not provide info?
3. Are there conflicting facts that you had to choose between?
4. Are there fields you are less than 80% confident about?

Output your evaluation in `self_critique` as 1-3 sentences,
and a numeric `confidence` ∈ [0, 1].
```

### 2.4 下游使用

Orchestrator 根据上游 confidence 决策：

- confidence < 0.5 → 不进入下游，直接触发 QA 提前介入
- 0.5 ≤ confidence < 0.7 → 走完流程但报告中标"低置信度"
- ≥ 0.7 → 正常

Analyst / Reporter 在引用上游数据时也会读 `field_confidence`，对低置信字段：
- 软化表述（"根据初步资料……"）
- 标记给 QA 复查

### 2.5 评分价值

- "技术方案有独特或前瞻性思考（Agent 自评估）" ← 直接命中
- "幻觉抑制策略" ← 加分

---

## 3. 决策回放（Time-Travel Debugging）

### 3.1 问题

传统做法：报告出来了，但用户无法"还原"Agent 是怎么得出结论的。问题：
- 当用户怀疑某结论时无法验证推理过程
- 优化 prompt 时只能盲调
- 故障排查只能看 stack trace

### 3.2 方案

每个节点的完整执行细节都可"回放"：

- 时间轴 UI 按时间顺序展示所有节点（含重做 v2 / v3）
- 点击任一节点 → 看到完整：
  - System prompt 文件 + 渲染后实际值
  - Input messages（完整）
  - LLM Response（完整 raw + 解析后）
  - 工具调用（参数 + 返回）
  - 输出（完整 JSON）
  - Token / 耗时 / cost
  - confidence + self_critique
- 支持 **v1 vs v2 diff 视图**（QA 重做时特别有用）
- 支持 **复制 prompt 作为 fixture**（开发调试）

### 3.3 实现

依赖完整的 Trace 体系（详见 [OBSERVABILITY.md](OBSERVABILITY.md)）。

前端核心组件：
- `<TraceTimeline />`：时间轴 + 状态 + 耗时叠加
- `<SpanDetail />`：节点详情抽屉
- `<LLMCallDetail />`：单次 LLM 调用详情
- `<SpanDiff v1={...} v2={...} />`：版本对比

### 3.4 进阶能力

- **从证据反查 Agent**：在 Evidence 卡片上点击"哪个 Agent 用了我" → 跳转到对应 span
- **从指标反查 Agent**：仪表盘上指标恶化 → 一键定位到拖累指标的 Agent
- **What-if 重跑**：在某个 span 上修改 input → 单独重跑该节点 → 对比结果（v2 实现）

### 3.5 评分价值

- "可观测性达标：每个 Agent 的 Prompt、输入、输出、决策过程、Token 消耗均有日志 / Trace 可查" ← 直接命中
- "交互设计流畅：Agent 决策回放等核心动作易用直观" ← 直接命中

---

## 4. 可选增量亮点（v2 视情况落地）

### 4.1 动态 Schema 演化

- 多个项目运行后，收集"用户经常补 / 删的字段"
- 自动建议 industry_extension 新增 / 废弃字段
- 用户确认后 schema 版本号 bump

### 4.2 人工介入（Human-in-the-Loop）

- 在 DAG 任一节点暂停，让用户编辑 Agent 输出，再继续
- 编辑事件入 trace + 计入 edit_rate
- 重要节点（QA 失败次数 ≥ 2）自动请求人工裁决

### 4.3 跨项目知识沉淀

- 同一产品在多个项目中的画像融合
- 用户可"复用"已有竞品画像，跳过重新采集
- 画像有版本号，定期 refresh

这三个是 v2 候选，v1 不做但留接口（field_status / 编辑事件 / profile_id）。

---

## 5. 评分映射汇总

| 亮点 | 评分要点 | 命中度 |
|---|---|---|
| 自适应 DAG | 自适应任务拆分 | 直接命中 |
| Agent 自评估 | Agent 自评估 + 幻觉抑制 | 直接命中 |
| 决策回放 | 可观测性 + 交互设计 | 双命中 |
| 动态 Schema（候选） | 动态 Schema 演化 | 候选 |
| 人工介入（候选） | 人工介入修正 + 业务闭环 | 候选 |

---

## 6. 实现里程碑

| 亮点 | 里程碑 | 责任窗口 |
|---|---|---|
| 自适应 DAG | M2 v1 用模板，M5 上 Planner | O 窗口 |
| Agent 自评估 | M1 起所有 Agent 必含 confidence + self_critique | 各 Agent 窗口 |
| 决策回放 | M3 后端 trace 完备，M4 前端 UI | I + F 窗口 |
| 动态 Schema | v2 候选 | 架构窗口 |
| 人工介入 | v2 候选 | F 窗口 |
