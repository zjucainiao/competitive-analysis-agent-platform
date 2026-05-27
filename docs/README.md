# 文档目录

> 本目录是平台所有设计、契约、规范的权威来源。代码层的实现细节服从本目录文档。

---

## 1. 按角色找入口

不同角色推荐的阅读顺序（按先后）：

### 我是项目负责人 / PM
1. [ARCHITECTURE.md](ARCHITECTURE.md) — 一图看懂系统
2. [WORKBREAKDOWN.md](WORKBREAKDOWN.md) — 分工与里程碑
3. [INNOVATIONS.md](INNOVATIONS.md) — 评分加分项
4. [METRICS.md](METRICS.md) — 业务闭环指标

### 我要实现一个 Agent（C/E/A/R/Q 窗口）
1. [ARCHITECTURE.md](ARCHITECTURE.md) — 先建立全局认知
2. [CONVENTIONS.md](CONVENTIONS.md) — 编码规范
3. [AGENTS.md](AGENTS.md) — **重点读你负责的章节 § 3 / § 4 / § 5 / § 6 / § 7**
4. [SCHEMA.md](SCHEMA.md) — 你输入输出的所有 Pydantic 模型
5. [HALLUCINATION_CONTROL.md](HALLUCINATION_CONTROL.md) — 抑制幻觉的具体做法
6. [EVIDENCE.md](EVIDENCE.md) — 如果你产 / 用证据（Extractor / Analyst / Reporter / QA）
7. [QA.md](QA.md) — Q 窗口必读；其他 Agent 也要看，了解你的输出会被如何检查

### 我要实现编排器（O 窗口）
1. [ARCHITECTURE.md](ARCHITECTURE.md)
2. [DAG.md](DAG.md) — **核心**
3. [AGENTS.md § 8](AGENTS.md#8-orchestrator编排器) — Orchestrator 接口
4. [QA.md § 6-7](QA.md) — 路由策略与防死循环
5. [OBSERVABILITY.md § 5](OBSERVABILITY.md) — Trace 注入

### 我要做前端（F 窗口）
1. [ARCHITECTURE.md § 2.1](ARCHITECTURE.md) — 关键页面清单
2. [OBSERVABILITY.md § 8](OBSERVABILITY.md) — 决策回放 UI
3. [EVIDENCE.md § 7](EVIDENCE.md) — 溯源 UI
4. [METRICS.md § 6](METRICS.md) — 仪表盘
5. [SCHEMA.md](SCHEMA.md) — TS 类型从 OpenAPI 自动生成，但要理解业务模型

### 我要做基础设施（I 窗口：LLM/Tools/Storage）
1. [ARCHITECTURE.md](ARCHITECTURE.md)
2. [CONVENTIONS.md](CONVENTIONS.md)
3. [HALLUCINATION_CONTROL.md](HALLUCINATION_CONTROL.md) — LLMProvider 要支持哪些约束
4. [OBSERVABILITY.md](OBSERVABILITY.md) — Trace / Token 计量
5. [COMPLIANCE.md](COMPLIANCE.md) — 抓取合规、PII 脱敏

### 我是评审 / 答辩观众
1. [ARCHITECTURE.md](ARCHITECTURE.md) — 1 张图理解平台
2. [INNOVATIONS.md](INNOVATIONS.md) — 看亮点
3. [METRICS.md § 4](METRICS.md) — vs 人工的提升数据
4. [QA.md](QA.md) — 看看反馈闭环怎么真实跑起来的

---

## 2. 每份文档详解

### [ARCHITECTURE.md](ARCHITECTURE.md) · 257 行
**系统总体架构。** 六层架构、模块边界、端到端数据流、技术选型、部署拓扑、设计原则。任何人加入项目第一份要读的文档。
- 读完知道：系统是什么、由哪些层组成、数据是怎么流转的、为什么选这些技术栈

### [AGENTS.md](AGENTS.md) · 679 行
**5 个专职 Agent 的接口契约权威。** 通用 BaseAgent、AgentOutputBase、LLM 调用约定、工具调用约定、错误码；然后每个 Agent 的输入 / 输出 Pydantic 模型、关键工具、prompt 设计要点、自评估规则、特有错误码。
- 读完知道：你负责的 Agent 输入输出是什么、上下游怎么对接、必须实现哪些约束
- **所有 Agent 实现窗口最重要的文档**

### [SCHEMA.md](SCHEMA.md) · 539 行
**竞品知识 Schema 的权威定义。** CompetitorProfile（通用 + 行业扩展）、AnalysisResult、ReportDraft、Evidence、TraceRecord、DAGNode、Project 全套 Pydantic 模型。包含字段级说明 + 行业扩展机制 + 版本管理规则。
- 读完知道：所有跨模块数据的精确结构

### [DAG.md](DAG.md) · 375 行
**任务编排引擎设计。** 节点六态状态机、节点类型（START/END/AGENT_CALL/PARALLEL_FORK/JOIN/CONDITIONAL/FEEDBACK）、边语义（依赖边 + 反馈边）、默认模板、自适应规划、调度策略（并行/重试/超时/降级）、反馈闭环实现机制、LangGraph 映射。
- 读完知道：Orchestrator 怎么调度、QA 失败后怎么真实路由回上游

### [EVIDENCE.md](EVIDENCE.md) · 247 行
**证据链与可溯源。** Evidence 生命周期、切片策略、入库流程、引用规则（哪些字段必须有证据、软结论例外、反例证据）、引用展开规则、溯源 UI 规范、人工介入、时效性。
- 读完知道：证据是怎么从网页流到报告的、每个 claim 怎么追溯到原文

### [QA.md](QA.md) · 254 行
**质检 Agent 的 6 维度规则。** fact_consistency / evidence_completeness / schema_completeness / logic_consistency / freshness / expression 每个维度的检查方法、阈值、路由策略；整体判定逻辑；防死循环；prompt 设计要点。
- 读完知道：报告是怎么被质检的、什么情况下会触发上游重做

### [OBSERVABILITY.md](OBSERVABILITY.md) · 351 行
**全链路可观测设计。** Trace / Span / Call 三层模型、必须记录的字段、存储表结构、Token 计量、决策回放 UI 规范、安全脱敏、性能优化。
- 读完知道：怎么记录每一次 LLM 调用、用户怎么在前端"时间旅行"看任意节点的执行细节

### [HALLUCINATION_CONTROL.md](HALLUCINATION_CONTROL.md) · 281 行
**幻觉抑制四层策略。** L1 上下文管理（分片 + RAG + 显式指令）、L2 输出强约束（结构化输出 + 二次校验）、L3 引用强制（Reporter 段落级）、L4 QA 兜底；自一致性、Agent 自评估、错误恢复。
- 读完知道：为什么我们的系统不会"模型乱编"

### [METRICS.md](METRICS.md) · 207 行
**业务闭环指标体系。** 准确率 / 覆盖率 / 人工修正率三个核心指标的定义、计算公式、目标值；辅助指标；vs 人工基线的对比；采集与持久化；仪表盘 UI；告警；答辩快照要求。
- 读完知道：怎么用数字证明平台比人工好

### [COMPLIANCE.md](COMPLIANCE.md) · 210 行
**合规与数据安全。** robots.txt 合规、ToS（User-Agent / 频率 / 来源声明 / 不二次发布）、PII 脱敏、模型与工具合规、数据保留与删除、安全实践、答辩材料合规清单。
- 读完知道：抓取怎么做合规、用户数据怎么处理、答辩前要 check 哪些

### [INNOVATIONS.md](INNOVATIONS.md) · 227 行
**前瞻性技术亮点。** v1 落地 3 个：自适应 DAG（Planner LLM）、Agent 自评估（confidence + self_critique）、决策回放（time-travel UI）；v2 候选：动态 Schema 演化、人工介入、跨项目知识沉淀。每个亮点的问题、方案、实现、评分价值。
- 读完知道：答辩时讲什么能加分

### [CONVENTIONS.md](CONVENTIONS.md) · 359 行
**编码与协作规范。** 仓库结构、Python 规范、TS 规范、测试要求、Git 工作流（分支 / commit / PR）、配置管理、Schema 变更流程、文档要求、性能成本、安全清单、AI 工具痕迹、新窗口加入第一步。
- 读完知道：写代码怎么写、提交怎么提、Schema 变更走什么流程

### [WORKBREAKDOWN.md](WORKBREAKDOWN.md) · 177 行
**多窗口分工与里程碑。** 角色与窗口分工表、实现优先级、M0-M6 里程碑、Mock 数据协议（让窗口可并行不互相阻塞）、接口冻结与变更流程、集成 Checklist、审查机制、提交节奏。
- 读完知道：谁干啥、什么时候交付、卡谁的脖子

---

## 3. 文档间依赖关系

```
              ARCHITECTURE.md
              （所有人入口）
                    │
        ┌───────────┼────────────┐
        ▼           ▼            ▼
   AGENTS.md    DAG.md      OBSERVABILITY.md
   （契约权威）  （编排）        （可观测）
        │           │
        ▼           │
   SCHEMA.md ←──────┘
   （模型权威）
        │
        ├──→ EVIDENCE.md   （证据链使用 SCHEMA）
        │
        ├──→ QA.md         （QA 规则基于 SCHEMA 字段）
        │
        ├──→ HALLUCINATION_CONTROL.md
        │    （引用强制依赖 SCHEMA 模型）
        │
        └──→ METRICS.md    （指标定义引用 SCHEMA）

  独立维度：
   COMPLIANCE.md       （合规策略，横切所有 Agent）
   INNOVATIONS.md      （亮点说明，答辩用）
   CONVENTIONS.md      （编码规范，所有窗口）
   WORKBREAKDOWN.md    （PM 视角，跨所有文档）
```

---

## 4. 文档版本与维护

- 所有文档当前对应 **SCHEMA_VERSION 1.0.0**
- 文档变更走 PR + 架构窗口 review
- Schema 字段变更必须同步更新 AGENTS.md / SCHEMA.md
- 评分要点 / 赛题要求变化时，更新 INNOVATIONS.md / METRICS.md / COMPLIANCE.md
- 各 Agent 实现完成后，对应 `backend/agents/<name>/README.md` 反向链接回 AGENTS.md 章节

---

## 5. 还没有的文档（未来补）

| 文档 | 时机 |
|---|---|
| `DEPLOYMENT.md` | v1 完成、有 docker-compose 时 |
| `LICENSES.md` | 依赖清单稳定后 |
| `SECURITY.md` | 答辩前 |
| `CHANGELOG.md` | 第一次发版时 |
| `ANSWERING_QUESTIONS.md` | 答辩准备阶段 |

---

## 6. 阅读时间估算

| 角色 | 必读文档行数 | 预计阅读时间 |
|---|---|---|
| 任一 Agent 实现窗口 | ~2000 行（重点章节） | 1.5–2 小时 |
| Orchestrator 窗口 | ~1500 行 | 1.5 小时 |
| Frontend 窗口 | ~1200 行 | 1 小时 |
| 评审 / 答辩观众 | ~500 行 | 30 分钟 |
| 项目负责人 | 全部 4163 行 | 3–4 小时（建议分多次） |
