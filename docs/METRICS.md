# 业务闭环指标体系

> 本文档定义平台的业务指标体系。对应评分要点：「设计了清晰的业务闭环（含关键指标如**准确率、覆盖率、人工修正率**），支持后续运营迭代」。

---

## 1. 设计目标

让平台不止能"生成报告"，而是能**量化它生成报告的质量**，并把这些数字回流到运营决策：

- 用户能在仪表盘上一眼看到「这个项目 / 这个 Agent / 这次答辩演示」的质量
- 横向对比：换 LLM / 换 prompt / 换工具 → 指标是涨了还是跌了
- 纵向对比：本周 vs 上周，是否持续改善
- 风险预警：覆盖率突然下降 → 触发告警

---

## 2. 三个核心指标

### 2.1 准确率（Accuracy）

**定义**：报告中事实性段落能被对应 evidence 蕴含的比例。

**计算**：

```
accuracy = entailed_paragraphs_count / total_factual_paragraphs_count
```

- 数据来源：QA Agent 的 `fact_consistency` 维度结果
- 抽样人工标注作为校准：每 100 个项目抽 5 个让分析师审，得出"人工校准准确率"，与系统自评对比

**目标**：≥ 0.92（v1）/ ≥ 0.95（v2）

### 2.2 覆盖率（Coverage）

**定义**：竞品知识 Schema 字段填充率 + 关键章节信息覆盖度。

**计算**：

```
schema_coverage = sum(non_null_fields) / sum(total_fields)        # 各 Profile 平均
source_coverage = sum(unique_dimensions_per_competitor) / sum(target_dimensions)
coverage = (schema_coverage + source_coverage) / 2
```

- 数据来源：Extractor 输出的 field_status + Collector 输出的 dimensions
- 关键字段加权（pricing.plans 比 founded_year 重要）

**目标**：≥ 0.75（v1）/ ≥ 0.85（v2）

### 2.3 人工修正率（Edit Rate）

**定义**：用户在前端对报告进行手工编辑的比例。

**计算**：

```
edit_rate = edited_paragraphs / total_paragraphs
```

- 数据来源：前端打 `is_user_edited=True` 的段落
- 越低越好（< 0.10 表示报告基本可直接用）

**目标**：≤ 0.20（v1）/ ≤ 0.10（v2）

---

## 3. 辅助指标

| 指标 | 计算 | 用途 |
|---|---|---|
| **Evidence 平均引用数 / 段落** | sum(段落 evidence_ids) / 段落数 | 衡量证据丰富度 |
| **Source 权威度均值** | mean(authority_score of all used evidences) | 衡量数据源质量 |
| **QA 通过率** | passes / total_verdicts | 衡量首发质量 |
| **QA 平均重做轮数** | sum(rework_rounds) / projects | 越低越好 |
| **Token 消耗 / 项目** | total_tokens / projects | 成本 |
| **耗时 / 项目** | duration_seconds / projects | 效率 |
| **真实 vs Mock 占比** | real_fetch_count / total_fetch_count | 演示真实性 |
| **Field Confidence 均值** | mean(field_confidence) | 抽取信心 |

---

## 4. 效率提升量化（vs 传统人工）

平台必须**可证明**比人工快、覆盖广、更一致。**对比基线**：

| 维度 | 人工基线 | 平台 | 提升 |
|---|---|---|---|
| **时间** | 1 个分析师 × 3 个竞品 × 5 维度 ≈ 8 小时 | 端到端 < 15 分钟 | ~30× |
| **信息源覆盖** | 平均 3-5 个源 / 竞品 | 8-15 个源 / 竞品 | ~3× |
| **结构化输出** | Word/PPT 自由格式 | JSON Schema 100% 一致 | 1 → ∞ |
| **可溯源** | 偶尔附 URL | 每个 claim 绑 evidence | 完整 |
| **可重复** | 难复现 | 同 query 100% 可复现 | — |

> 这些数据出现在答辩 PPT 和仪表盘。

---

## 5. 指标采集与计算

### 5.1 实时采集

- 每个节点完成 → 同步更新 ProjectMetrics
- QA 完成 → 更新 accuracy
- 用户编辑 → 更新 edit_rate
- 这些操作走异步事件总线（Redis Stream），避免阻塞主流程

### 5.2 持久化

```sql
CREATE TABLE project_metrics (
  project_id    text PRIMARY KEY,
  accuracy      real,
  coverage      real,
  edit_rate     real,
  evidence_count int,
  fields_filled_ratio real,
  total_tokens  bigint,
  total_cost_usd numeric,
  duration_seconds int,
  qa_round_count int,
  updated_at    timestamptz DEFAULT now()
);
```

### 5.3 全局聚合

物化视图 / 定时任务：

```sql
CREATE MATERIALIZED VIEW global_metrics AS
SELECT
  date_trunc('day', updated_at) as day,
  avg(accuracy) as daily_accuracy,
  avg(coverage) as daily_coverage,
  avg(edit_rate) as daily_edit_rate,
  count(*) as project_count,
  sum(total_cost_usd) as daily_cost
FROM project_metrics
GROUP BY 1;
```

---

## 6. 指标仪表盘 UI

```
┌────────────────────────────────────────────────────┐
│ 全局指标 · 近 7 天                                 │
├────────────────────────────────────────────────────┤
│  准确率      0.94 ▲ 0.02                          │
│  覆盖率      0.81 ▲ 0.05                          │
│  人工修正率  0.15 ▼ 0.03                          │
├────────────────────────────────────────────────────┤
│  [趋势图：准确率 / 覆盖率 / 修正率 折线]           │
│                                                    │
│  [Agent 维度：Collector/Extractor/.../QA 通过率]   │
│                                                    │
│  [成本：日均 Token + USD]                          │
│                                                    │
│  [Top 模型：按使用次数与成本]                      │
└────────────────────────────────────────────────────┘
```

项目级仪表盘可下钻到单项目，单项目可下钻到单 Agent / 单 span。

---

## 7. 告警

| 触发 | 动作 |
|---|---|
| 单项目 accuracy < 0.8 | 在项目详情页置红，通知 owner |
| 全局 accuracy 单日下跌 > 5% | 邮件 / IM 告警 |
| 单项目 cost > 上限 | 中止并告警 |
| 真实抓取连续失败 > 30% | 自动切回 mock 并告警 |

v1 阶段告警可以只打日志，v2 接入 IM。

---

## 8. 用于答辩的指标快照

答辩材料中**至少**要展示：

- 演示项目的 accuracy / coverage / edit_rate（带证据）
- vs 人工基线的提升表（§ 4）
- 一个完整的 trace 截图（决策回放）
- 一次真实的 QA 反馈闭环（QA 失败 → routing → 重做 → 通过 → 指标变化）

---

## 9. 实现位置

```
backend/observability/metrics/
├── collector.py        # 监听事件、累计指标
├── calculator.py       # 公式
├── aggregator.py       # 全局聚合
└── alerts.py           # 阈值告警

backend/api/routers/metrics.py     # REST API
frontend/src/pages/Dashboard/      # 仪表盘 UI
```

I 窗口 + F 窗口协作完成，M3 后启动。
