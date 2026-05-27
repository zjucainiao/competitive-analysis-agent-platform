# 多窗口分工与里程碑

> 本文档定义各窗口（人/Claude 会话）的分工、接口冻结时间点、Mock 数据协议和集成节奏。**所有 Agent 实现者动手前必读**。

## 1. 角色与窗口分工

| 窗口 | 负责 | 主要产出 | 依赖 |
|---|---|---|---|
| **架构窗口**（本窗口） | 架构、文档、Schema、契约、审查 | docs/* + backend/schemas/* + 集成评审 | — |
| **C 窗口** | Collector（采集 Agent） | backend/agents/collector/* | 架构窗口的 Schema 与契约 |
| **E 窗口** | Extractor（抽取 Agent） | backend/agents/extractor/* | 架构窗口 + Mock 采集数据 |
| **A 窗口** | Analyst（分析 Agent） | backend/agents/analyst/* | 架构窗口 + Mock Profile 数据 |
| **R 窗口** | Reporter（报告 Agent） | backend/agents/reporter/* | 架构窗口 + Mock Analysis 数据 |
| **Q 窗口** | QA（质检 Agent） | backend/agents/qa/* | 架构窗口 + Mock 全链路数据 |
| **O 窗口** | Orchestrator（编排器） | backend/orchestrator/* + backend/api/* | 所有 Agent 接口冻结后 |
| **F 窗口** | Frontend（前端） | frontend/* | API Schema + Mock 接口 |
| **I 窗口** | Infra（存储/LLM/工具） | backend/llm/* + backend/storage/* + backend/tools/* | 架构窗口的抽象接口 |

> 实际窗口可合并，但角色边界保持清晰。最少需要 4-5 个窗口：架构 + 至少 2 个 Agent + Orchestrator + Frontend。

## 2. 实现优先级（关键路径）

```
P0 (必须，影响所有人):
  架构窗口 → docs/SCHEMA.md  +  backend/schemas/*.py 全套 Pydantic 模型
  架构窗口 → fixtures/mock_data/* 各阶段 Mock 数据
  架构窗口 → backend/agents/_base.py BaseAgent 基类
  I 窗口   → backend/llm/provider.py LLMProvider 抽象

P1 (核心 Agent，决定 demo 能不能跑):
  C 窗口 → Collector v1（真实采集 + Mock 兜底）
  E 窗口 → Extractor v1
  A 窗口 → Analyst v1
  R 窗口 → Reporter v1
  Q 窗口 → QA v1
  O 窗口 → Orchestrator v1（固定 DAG，先跑通）

P2 (体现工程深度 + 评分加分):
  O 窗口 → 自适应 DAG
  各 Agent → 自评估 (self_critique)
  F 窗口 → 决策回放 UI
  架构窗口 → OBSERVABILITY 接入

P3 (打磨):
  F 窗口 → 指标仪表盘
  架构窗口 → 合规模块、PII 脱敏
  全员 → 答辩材料、演示视频、README 打磨
```

## 3. 关键路径与里程碑

| 里程碑 | 内容 | Owner | 阻塞谁 |
|---|---|---|---|
| **M0 契约冻结** | Schema + AgentIO + DAGNode 全部 Pydantic 模型 + Mock 数据集 | 架构窗口 | 所有 Agent 窗口 |
| **M1 单 Agent 跑通** | 每个 Agent 用 Mock 输入跑出符合 Schema 的输出 | 各 Agent 窗口 | Orchestrator |
| **M2 链路串通** | Orchestrator 用固定 DAG 串起 5 Agent，端到端跑完一个项目 | O 窗口 | Frontend |
| **M3 闭环触发** | QA 真实失败 → 路由回上游 → 重做 → 改善后通过 | Q + O 窗口 | 评分项「反馈闭环真实可触发」 |
| **M4 前端联调** | DAG 可视化、报告查看、证据溯源 | F 窗口 | 演示 |
| **M5 工程亮点** | 自适应 DAG、自评估、决策回放、合规、指标仪表盘 | 全员 | 评分加分项 |
| **M6 答辩准备** | 演示视频、演讲稿、问答预演 | PM | 提交 |

> M0 是所有人的硬阻塞。架构窗口必须先完成。

## 4. Mock 数据协议（核心：让窗口可独立开发）

为了让各 Agent 窗口**不必等彼此**就能开始开发，架构窗口提供完整的 Mock 数据集。每个 Agent 的输入都有对应的 Mock 文件。

### 4.1 目录结构

```
fixtures/mock_data/
├── projects/
│   └── collab_saas_demo.json          # 协作办公演示项目配置
├── raw_sources/                       # Collector 的输出 / Extractor 的输入
│   ├── notion/
│   │   ├── homepage.json              # RawSourceDoc
│   │   ├── pricing.json
│   │   └── help_docs.json
│   ├── clickup/
│   └── asana/
├── competitor_profiles/               # Extractor 的输出 / Analyst 的输入
│   ├── notion.json                    # CompetitorProfile
│   ├── clickup.json
│   └── asana.json
├── analysis_results/                  # Analyst 的输出 / Reporter 的输入
│   ├── feature_comparison.json
│   ├── pricing_comparison.json
│   ├── swot.json
│   └── opportunities.json
├── report_drafts/                     # Reporter 的输出 / QA 的输入
│   └── draft_v1.json
├── qa_verdicts/                       # QA 的输出
│   ├── pass.json
│   └── needs_revision.json
└── evidences/
    └── evidence_db.jsonl              # 整个 Evidence 库示例
```

### 4.2 Mock 使用约定

- **每个 Agent 必须支持 Mock 模式**：通过 `BaseAgent.__init__(mock=True)` 跳过真实 LLM 调用，直接返回 fixture
- **每个 Agent 必须提供单元测试**：用 Mock 输入跑出预期 Mock 输出
- **Agent 开发顺序无关**：拿到上游 Mock 就能开始
- **架构窗口承诺**：M0 时点提供完整 Mock 数据集，覆盖至少 3 个竞品 × 4 个维度

### 4.3 真实数据 vs Mock 切换

```python
# 启动方式
app_mode = "mock"     # 全链路 Mock（开发用）
app_mode = "hybrid"   # 真实采集 + Mock 兜底（演示用，推荐）
app_mode = "real"     # 全真（生产）
```

## 5. 接口冻结与变更流程

### 5.1 冻结时间点

- **Schema 与 AgentIO 在 M0 冻结**
- 冻结后任何变更须走 PR + 架构窗口审查 + 通知所有受影响窗口

### 5.2 变更协议

破坏性变更必须包含：
- 变更动机（为什么必须改）
- 影响面（哪些窗口需要改）
- 迁移路径（旧字段如何映射到新字段）
- Schema 版本号 bump（major / minor）

### 5.3 Schema 版本号

```
v{major}.{minor}.{patch}
- major: 破坏性（字段删除 / 类型变更）
- minor: 增量（新增可选字段）
- patch: 注释/校验规则微调
```

v1 阶段：架构窗口维护 `backend/schemas/__init__.py` 里的 `SCHEMA_VERSION = "1.0.0"`。

## 6. 集成 Checklist

每个 Agent 窗口的 Agent 在合入主分支前必须满足：

- [ ] Pydantic Input/Output 严格符合 docs/AGENTS.md
- [ ] 通过 Mock 输入的单元测试（至少 3 个 case：正常 / 边界 / 异常）
- [ ] 输出包含完整 `AgentOutput`：data + confidence + self_critique + tokens + duration
- [ ] LLM 调用走 `LLMProvider` 抽象，不直接 import vendor SDK
- [ ] 所有 prompt 抽到 `prompts/` 目录，不硬编码在代码里
- [ ] 提供 `README.md`：本 Agent 做什么、输入输出、运行方式
- [ ] 异常处理：超时 / 限流 / Schema 校验失败都有对应错误码
- [ ] 引用强制（Reporter）：claim 无 evidence_id → 抛 `MissingEvidenceError`
- [ ] 自评估：confidence < 0.6 → 在 self_critique 中说明原因
- [ ] Trace：每次调用 LLM 都通过 BaseAgent 自动 trace

## 7. 审查机制

- 每个 Agent 窗口完成后，由**架构窗口**对照本文档 § 6 Checklist 审查
- 审查通过后合入主分支
- 集成出问题时，架构窗口负责定位是哪一个 Agent / 哪一段契约的问题
- 重大决策（Schema 变更、新增 Agent、技术选型变化）由架构窗口最终拍板

## 8. 沟通约定

- **接口问题**：直接对照 docs/AGENTS.md 和 docs/SCHEMA.md
- **歧义点**：架构窗口在 docs/ 里补充澄清，避免私下口头约定
- **跨窗口同步**：每个里程碑点架构窗口给出对齐总结

## 9. 提交节奏建议

| 阶段 | 提交内容 | 频率 |
|---|---|---|
| 实现阶段 | 单 Agent feat、test、prompt | 每完成一个子能力 |
| 联调阶段 | Orchestrator 集成、fixture 调整 | 每次跑通一条新路径 |
| 打磨阶段 | UI、文档、性能、合规 | 每天一次小提交 |

Git 规范见 [CONVENTIONS.md](CONVENTIONS.md)。
