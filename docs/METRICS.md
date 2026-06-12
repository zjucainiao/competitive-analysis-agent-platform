# 业务闭环指标体系

> 本文档定义平台的业务指标体系。对应评分要点：「设计了清晰的业务闭环（含关键指标如**准确率、覆盖率、人工修正率**），支持后续运营迭代」。
>
> **文档口径**：本文区分「已实现的 v1 公式」与「设计草案 / 未实现」。真实实现在 `backend/orchestrator/metrics.py`（计算）+ `backend/schemas/project.py`（`ProjectMetrics` 字段）+ `backend/api/routes/meta.py`（跨项目聚合）。指标以 **jsonb 落在 `Project.metrics`**，**没有** 独立 `project_metrics` 表，**没有** 物化视图，**没有** Redis-Stream 事件总线，**没有** 告警表。

---

## 1. 设计目标

让平台不止能"生成报告"，而是能**量化它生成报告的质量**，并把这些数字回流到运营决策：

- 用户能在仪表盘上一眼看到「这个项目 / 这次答辩演示」的质量
- 横向对比：换 LLM / 换 prompt / 换工具 → 指标是涨了还是跌了
- 纵向对比（跨返工轮）：返工后准确率是否真有改善（`per_round_accuracy` / `round_delta`）

---

## 2. 三个核心指标（v1 公式，已实现）

> 实现：`compute_project_metrics`（`backend/orchestrator/metrics.py:40`）。

### 2.1 准确率（Accuracy）

**定义**：最新一份 `QAVerdict` 的**所有维度分数算术平均**。

**计算**（`metrics.py:111-124` `_scores_from_last_verdict`）：

```
accuracy = mean( last_verdict.dimension_results[*].score )
```

- 数据来源：QA Agent 最新 verdict 的全部 `dimension_results`（不只 `fact_consistency`）
- 无 verdict → 0.0

> **口径修正**：早期稿写的「`entailed_paragraphs / total_factual_paragraphs` 蕴含比例」**不是实现**。系统只验**溯源**不逐句验事实蕴含；accuracy 是 QA 各维度评分的算术均值。「抽样人工标注校准」属设计意向，**未实现**。

**目标**：≥ 0.92（v1）/ ≥ 0.95（v2）

### 2.2 覆盖率（Coverage）

**定义**：QA `schema_completeness` 维度分数（等价于 profile 必填字段覆盖度）。

**计算**（`metrics.py:120-121`）：

```
coverage = last_verdict.dimension_results[SCHEMA_COMPLETENESS].score   # 无该维度 → 0.0
```

- 在 `ProjectMetrics` 里 `coverage` 与 `fields_filled_ratio` 取**同一个值**（`metrics.py:67-68`，保留两个独立字段方便前端不同维度展示）

> **口径修正**：早期稿的 `coverage = (schema_coverage + source_coverage) / 2` 双项平均**不是实现**。实际就是单一的 `schema_completeness` 维度分。`source_coverage` / 关键字段加权属设计草案，**未实现**。

**目标**：≥ 0.75（v1）/ ≥ 0.85（v2）

### 2.3 人工修正率（Edit Rate）

**定义**：用户在前端手工编辑（PATCH 段落 / evidence）的累计次数占报告段落数的比例，截顶到 1。

**计算**（`backend/api/routes/evidence.py:120-126`，reports.py PATCH 同口径）：

```
manual_edits += 1                                  # 每次 PATCH 段落 / evidence
edit_rate = min(manual_edits / total_paragraphs, 1.0)
```

- `compute_project_metrics` 一次性算指标时 `edit_rate` 置 **0.0 占位**（`metrics.py:65-67`）；真实值由 PATCH 路径增量维护，`_persist_metrics` 跨重跑保留旧值（不被重算覆盖）
- `total_paragraphs` 取最新报告段落数（`_latest_report_total_paragraphs`）

> **口径修正**：早期稿写「前端打 `is_user_edited=True` 的段落数 / 总段落数」。实际是 **`manual_edits` 计数器**（每次 PATCH +1，disputed 也算人工介入）除以总段落数，再截顶到 1。

**目标**：≤ 0.20（v1）/ ≤ 0.10（v2）

### 2.4 跨轮质量追踪（已实现）

> 实现：`_scores_per_round`（`metrics.py:136-160`），字段在 `ProjectMetrics`（`schemas/project.py:88-92`）。用于回答「返工后是否真有改善」并支撑 best-round 择优发布。

| 字段 | 计算 | 说明 |
|---|---|---|
| `per_round_accuracy` | 每轮 verdict 的维度均分序列 | verdict 顺序 == 轮次顺序 |
| `round_delta` | 相邻轮差值 `score[i] - score[i-1]` | 长度 = 轮数 − 1 |
| `best_round` | 维度均分最高的轮（**1-based**，0 = 无 verdict） | **并列取较晚轮**（不改善时退化为最后一轮）；发布择优 `best_round_reporter_key`（`metrics.py:163`）据此挑 reporter / reporter_v{r} |

---

## 3. 辅助指标（`ProjectMetrics` 实际字段）

> 字段定义见 `backend/schemas/project.py:70-99`，计算见 `metrics.py`。

| 字段 | 计算 | 用途 |
|---|---|---|
| `evidence_count` | ∑ `ExtractorOutput.evidences` 长度（按节点最新轮）| 证据丰富度 |
| `fields_filled_ratio` | 同 `coverage`（`schema_completeness` 维度分）| 字段填充覆盖 |
| `total_tokens` | ∑ `AgentOutputBase.(tokens_input + tokens_output)` | 成本（token）|
| `total_cost_usd` | ∑ `AgentOutputBase.cost_usd`（豆包 EP 走方舟控制台 → 0）| 成本（USD）|
| `duration_seconds` | max(node.ended_at) − min(node.started_at) | 端到端耗时 |
| `qa_round_count` | 反馈环跑了几轮（FeedbackRouter 维护）| 返工轮数 |
| `real_fetch_count` | `RawSourceDoc.fetch_method != "mock"` 的条数 | 真实抓取量 |
| `mock_fetch_count` | `RawSourceDoc.fetch_method == "mock"` 的条数 | mock 抓取量 |
| `manual_edits` | PATCH 段落 / evidence 的累计次数 | 人工介入计数（驱动 `edit_rate`）|

> **未实现（设计草案）**：「Source 权威度均值 / QA 通过率 / QA 平均重做轮数（独立指标）/ Field Confidence 均值」未作为持久化指标字段落地。真实 / mock 占比可由上表两个 fetch 计数派生。

---

## 4. 效率提升量化（vs 传统人工）—— 答辩叙事，非系统计算

> 以下是答辩材料中的**对比叙事**，**不是**系统自动计算的指标，请勿当作运行时数据：

| 维度 | 人工基线 | 平台 | 提升 |
|---|---|---|---|
| **时间** | 1 分析师 × 3 竞品 × 5 维度 ≈ 8 小时 | 端到端 < 15 分钟 | ~30× |
| **信息源覆盖** | 平均 3-5 源 / 竞品 | 8-15 源 / 竞品 | ~3× |
| **结构化输出** | Word/PPT 自由格式 | JSON Schema 100% 一致 | 1 → ∞ |
| **可溯源** | 偶尔附 URL | 每个 claim 绑 evidence | 完整 |
| **可重复** | 难复现 | 同 query 可复现 | — |

---

## 5. 指标采集、持久化与聚合（实际）

### 5.1 采集时机

- **批量算一次**：run 进入终态时 `Orchestrator` 调 `compute_project_metrics` 算出全套指标，`_persist_metrics` 写回 `Project.metrics`（`orchestrator.py:442 / 604`）。
- **增量更新**：用户 PATCH 段落 / evidence 时，`manual_edits +1` 并按最新段落数重算 `edit_rate`（`evidence.py:120-126`，reports.py 同），**只动这两个字段，不重算其余**。

> **未实现**：早期稿的「每节点完成同步更新 ProjectMetrics + 走 Redis Stream 异步事件总线」**不存在**。指标是 run 结束时一次性算，加 PATCH 增量，无事件总线。

### 5.2 持久化：jsonb on Project（无独立表）

`ProjectMetrics` 作为 **jsonb 序列化在 `Project.metrics`**（`schemas/project.py:70-99`），随项目一起存。**没有** 独立的 `project_metrics` 关系表。

> **口径修正**：早期稿的 `CREATE TABLE project_metrics (...)` SQL **不是实现**，已删除。

### 5.3 全局聚合：应用层求和（无物化视图）

`GET /api/metrics/aggregate`（`backend/api/routes/meta.py:60`）在**应用层遍历当前用户的项目**，对 `p.metrics` 求平均 / 求和，返回 `AggregateMetricsResponse`（`avg_accuracy` / `avg_coverage` / `avg_edit_rate` / `total_evidence` / `total_tokens` / `total_cost_usd` / `total_duration_seconds` / `total_qa_rounds` / `total_manual_edits` / `by_status` / `by_industry`）。支持 `since_iso` 过滤起始时间。

> **口径修正**：早期稿的 `CREATE MATERIALIZED VIEW global_metrics` **不是实现**，已删除。聚合是 in-app 计算，非物化视图 / 定时任务。

---

## 6. 指标仪表盘 UI

仪表盘消费 `/api/metrics/aggregate`（全局）与 `Project.metrics`（单项目）渲染准确率 / 覆盖率 / 修正率、成本、状态 / 行业分布，单项目可下钻到决策回放（见 [OBSERVABILITY.md](OBSERVABILITY.md) § 8）。

```
┌────────────────────────────────────────────────────┐
│ 全局指标                                           │
├────────────────────────────────────────────────────┤
│  准确率      avg_accuracy                          │
│  覆盖率      avg_coverage                          │
│  人工修正率  avg_edit_rate                         │
├────────────────────────────────────────────────────┤
│  [成本：total_tokens + total_cost_usd]             │
│  [分布：by_status / by_industry]                   │
└────────────────────────────────────────────────────┘
```

> **未实现（设计草案）**：「近 7 天趋势折线 / 环比 ▲▼ / Agent 维度 QA 通过率 / Top 模型」依赖时间序列与按模型分组聚合，当前 `/api/metrics/aggregate` 不产出（仅单项目 timeseries 接口 `/api/projects/{id}/metrics/timeseries` 提供时间序列）。

---

## 7. 告警 —— 未实现（设计草案）

> 早期稿设想的阈值告警（accuracy < 0.8 置红 / 单日下跌 > 5% 邮件 IM / cost 超限中止 / 真实抓取失败率 > 30% 切 mock）**均未实现**。**没有** 告警表、**没有** 告警链路。保留此节作为后续设计参考。

| 触发（设计草案） | 动作（设计草案） |
|---|---|
| 单项目 accuracy < 0.8 | 项目详情页置红，通知 owner |
| 全局 accuracy 单日下跌 > 5% | 邮件 / IM 告警 |
| 单项目 cost > 上限 | 中止并告警 |
| 真实抓取连续失败 > 30% | 自动切回 mock 并告警 |

---

## 8. 用于答辩的指标快照

答辩材料中至少展示：

- 演示项目的 `accuracy` / `coverage` / `edit_rate`（带证据）
- vs 人工基线的提升叙事（§ 4）
- 一个完整的决策回放截图（[OBSERVABILITY.md](OBSERVABILITY.md) § 8）
- 一次真实的 QA 反馈闭环（QA 失败 → routing → 重做 → 通过 → `per_round_accuracy` / `round_delta` 变化）

---

## 9. 实现位置（真实路径）

```
backend/orchestrator/metrics.py     # compute_project_metrics（v1 公式）+ best_round_reporter_key（择优）
backend/schemas/project.py:70-99    # ProjectMetrics 字段定义（jsonb on Project）
backend/api/routes/meta.py          # /api/metrics/aggregate（in-app 聚合）+ timeseries
backend/api/routes/evidence.py      # PATCH 路径维护 manual_edits / edit_rate（reports.py 同口径）
frontend/src/...                    # 仪表盘 UI
```

> **不存在以下路径**（早期稿曾列出，已删除）：`backend/observability/metrics/{collector,calculator,aggregator,alerts}.py`、`backend/api/routers/metrics.py`、独立 `project_metrics` 表、物化视图 `global_metrics`。
