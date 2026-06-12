# 质检规则与反馈闭环

> 本文档定义 QA Agent 的 8 维度检查规则、阈值、路由策略。QA 是平台可信度的最后一道闸。

---

## 1. 设计目标

| 目标 | 落地点 |
|---|---|
| 真实闭环 | QA 输出 routing 后 Orchestrator 必须真触发上游重做（不只 mock 一下） |
| 可证伪 | 每个 issue 都指向具体段落 + 具体问题 + 具体修复建议 |
| 防死循环 | 重做次数到达上限自动降级 |
| 多维度 | 不止"事实对不对"，还要看"完整性、逻辑、时效、规范性" |

---

## 2. 8 个检查维度

| Dimension | 含义 | 主要工具 |
|---|---|---|
| `fact_consistency` | 报告结论 vs Evidence 是否一致 | NLI / LLM entailment |
| `evidence_completeness` | 关键结论是否都有 evidence 支撑 | 字段扫描 |
| `schema_completeness` | Profile 必填字段是否齐 | Schema 校验 |
| `coverage_density` | 已选维度是否被报告实质展开（信息密度）| 规则（段落/claim 比） |
| `identity_consistency` | 引用证据是否真属于其标注的产品（防抓错产品）| 规则（读 `identity_status`）|
| `logic_consistency` | 报告内部前后是否自洽 | LLM contradiction check |
| `freshness` | 引用证据是否过期 | 时间戳对比 |
| `expression` | 表达是否规范、无绝对化 | 规则 + LLM |

---

## 3. 维度详细规则

### 3.1 fact_consistency（事实一致性）

**核心检查**：对每个 `ReportParagraph`，让 LLM 判断 `text` 是否被 `evidence` 集合所蕴含。

```python
def check_entailment(text: str, evidences: list[Evidence]) -> EntailmentResult:
    # 调用 LLM：give me labels {entailed, contradicted, neutral}
    ...
```

**评分**：
- entailed 段落数 / 总段落数 ≥ 0.95 → pass
- 0.80–0.95 → minor issues
- < 0.80 → major issue，路由回 Reporter（或 Analyst，如果是 claim 本身有问题）

**特别检查**：
- 段落中的**数字 / 价格 / 百分比 / 版本号** 必须能在 evidence 文本中找到字面匹配（容差 ±5%）
- 未通过 → `UNVERIFIED_QUANTITY` issue

### 3.2 evidence_completeness（证据完整性）

**检查**：

- 每个事实性段落 `evidence_ids` 非空 → 否则 issue
- 每个 AnalysisClaim 的 `evidence_ids` ≥ 1 → 否则 issue
- 关键章节（功能对比 / 定价对比 / SWOT）的 evidence 覆盖率 ≥ 0.90

**routing**：
- 段落缺引用 → 回 Reporter
- claim 缺引用 → 回 Analyst
- 多个产品某维度 evidence 完全缺失 → 回 Collector（补采集）

### 3.3 schema_completeness（Schema 完整性）

**检查**：

- 每个 CompetitorProfile 的必填字段填充率 ≥ 0.80
- 行业扩展字段填充率 ≥ 0.60
- 字段 `field_status` 不能有 > 20% 是 `unverified`

**routing**：
- 必填字段缺失 → 回 Extractor（带 `must_address` 字段名列表）
- 原始数据中确实没有 → 回 Collector

### 3.4 logic_consistency（逻辑一致性）

**检查**：

- 同一产品在不同段落出现的事实是否冲突
  - 例：第 3 节说 ClickUp 有 AI 写作，第 7 节又说没有
- SWOT 中 S 和 W 是否同义反复
- 定价对比中各 plan 是否名称一致

**实现**：让 LLM 把报告中所有事实性陈述抽出来，做两两对比；或用 contradiction NLI 模型。

**评分**：按 severity 累加惩罚（critical=0.20 / major=0.10 / minor=0.04），阈值 0.85。
设计意图：1 处软冲突（minor）只是提示，不卡 pass；2 处硬冲突（major）才挂掉。

**routing**：
- 矛盾点定位明确 → 回 Reporter（让它重写涉及段落）
- 矛盾源头在 Analyst → 回 Analyst

### 3.5 freshness（时效性）

**检查**：

- 优先使用 Evidence.`source_published_at`（源文档发布/最后修改时间）
- 评分**只针对带日期的证据**：`source_published_at` 为 None 的证据**不计入评分**
  （既不加分也不扣分，见 `checkers/freshness.py:133-138`），避免把刚抓的旧文档误判为新鲜
- 若全部引用证据都无日期 → `score=1.0` 默认通过（freshness 不再 gating，见
  `checkers/freshness.py:135-136`）；只有**确实带日期且过期**的证据才报警
- 定价 / 版本号 / 功能等敏感字段引用的 Evidence 不能超过 `SENSITIVE_MAX_DAYS=90`
- 一般字段不能超过 `GENERAL_MAX_DAYS=365`

**routing**：
- 关键字段引用 stale Evidence → 回 Collector（重新采集该 dimension）
- 一般字段 stale → 仅在报告中标注"数据采集于 YYYY-MM"
- 无 `source_published_at` 本身**不开 issue、不参与评分**（无日期 ≠ 过期）

### 3.6 expression（表达规范性）

**规则检查**：禁用词列表
- "行业唯一"、"绝对领先"、"完美"、"100%"、"最佳"...

**LLM 检查**：
- 是否存在过度推断
- 是否使用"我们"等第一人称
- 是否结构清晰（每章节有 topic sentence）

**routing**：
- 表达问题 → 回 Reporter（minor，通常一次就能改好）

---

### 3.7 coverage_density（信息密度 / 章节覆盖）

**动机**：报告"偏薄"不靠全局字数下限来兜（那会逼模型注水）。真正该拦的是：
Analyst 产出了某维度的 claim（且有 evidence 支撑），Reporter 却把该章节写成
占位 / 软结论一句话，导致信息密度过低。

**规则检查**（不调 LLM）：
- 仅评估"Reporter 本可以渲染"的维度：该维度至少有一条 claim 带 evidence
  （全无 evidence 的维度交给 `evidence_completeness` 路由到上游，不重复责怪 Reporter）。
- 实质段落定义：`not is_soft_conclusion and text.strip()`（占位 / 软结论天然排除）。
- 维度有 N 条带证据 claim，但报告章节 0 个实质段落 → **major**。
- 维度 claim 数 ≥ 3，但实质段落 / 带证据 claim < 0.5（密度过低）→ **minor**。
- 维度 claim 少（甚至 1 条）→ 章节短是诚实的短，不罚。

**score**：各维度 `min(1, 实质段落/带证据claim)` 的均值；pass 阈值 0.80。

**routing**：
- 章节空 / 偏薄 → 回 Reporter（让它把可用 claim 逐条展开，不要折叠成占位）。

> 与 Reporter 侧改动配套：section prompt 要求"每条 claim 一段 + 至多 1 段软结论
> 小结"，段落目标按 claim 数动态缩放（见 `agents/reporter`）。两端共同保证
> "厚度由证据丰富度决定"，而非字数配额。

---

### 3.8 identity_consistency（产品身份一致性）

**动机**：搜索/排序可能选错源——分析「钉钉」却抓到「飞书 / Slack」的评价或功能页。
这类内容若进了报告，是事实性硬伤，但 `fact_consistency` 只查「结论 vs 引用证据」是否
自洽，**预设证据本身是对产品的**，发现不了「证据根本是别的产品的」。

**身份信号来源**（非 LLM；读已落库字段）：Collector 抓取后用「启发式 gate + 模糊时
LLM 裁定」的混合策略判定每个源的 `identity_status`（confirmed / mismatch / ambiguous），
Extractor 继承到每条 Evidence（见 `collector/agent.py::_assess_identity`）。

**规则检查**（不调 LLM）：
- 只看**被报告段落 / 分析 claim 引用到**的证据（未被引用的脏数据不影响成稿，不强行返工）。
- `identity_status == "mismatch"`（确属别的产品）→ **major**，按产品聚合，路由回 Collector，
  并在 `required_inputs.mismatch_source_urls` 带上跑题来源 URL。
- `identity_status == "ambiguous"`（提到目标产品但无法确证，如对比页）→ **minor**，仅浮出。

**score**：`1 - (mismatch + 0.4·ambiguous)/被引用证据数`；**pass 只由 mismatch 决定**
（纯 ambiguous 不致失败，避免在对比页上空转）。

**routing / 收敛**：mismatch → Collector（抓错产品的根因在采集层选错了源）。本维度为
**core**：触发一轮 blocking 返工；返工时 `build_collector_input` 把 `mismatch_source_urls`
解成 `exclude_source_urls`，Collector 重采时跳过这些页面 → 一轮即收敛；改不动则由
core 复发护栏 + best-round 兜底，不空转。

---

## 4. QA 输出结构

完整 Pydantic 模型见 [AGENTS.md](AGENTS.md) § 7.3。关键字段：

```python
class QAVerdict:
    overall_status:    "pass" | "needs_revision" | "reject"
    dimension_results: {QADimension: {score, pass_, notes}}
    issues:            [QAIssue]
    routing:           [QARouting]
    blocking:          bool      # True=必须重做才能发布
```

---

## 5. 整体判定

```
所有 dimension pass     → overall = pass, blocking = false
有 minor issues         → overall = needs_revision, blocking = false（建议改但可发）
有 major issues         → overall = needs_revision, blocking = true
有 critical issues      → overall = reject, blocking = true
```

```python
severity_weights = {"minor": 1, "major": 5, "critical": 20}
total_weight = sum(w[i.severity] for i in issues)
if total_weight == 0: status = pass
elif total_weight <= 10: status = needs_revision (non-blocking)
else: status = needs_revision (blocking) or reject
```

---

## 6. 路由策略

```python
# 简化路由表
ISSUE_TYPE → TARGET_AGENT
"missing_citation"        → reporter
"unverified_quantity"     → reporter
"contradictory_paragraph" → reporter
"insufficient_evidence"   → analyst（若上游有支撑信息）/ collector（若上游也没有）
"missing_schema_field"    → extractor
"stale_evidence"          → collector
"expression_issue"        → reporter
"profile_incomplete"      → extractor
```

QA 在生成 routing 时附 `payload`：

```python
QARouting(
    target_agent="reporter",
    reason="3 个段落缺引用",
    payload={
        "must_address": ["paragraph_id_017", "paragraph_id_023", "paragraph_id_031"],
        "instructions": "为这 3 个段落补充 evidence_ids，或重写为软性结论"
    }
)
```

---

## 7. 防死循环

`QAInput.prior_verdicts` 累积历史质检结果。Orchestrator 维护：

- 同一 issue（按 `dimension + location` 去重）出现次数计数
- 同一 issue 出现 ≥ 3 次（`SAME_ISSUE_MAX_OCCURRENCES`，`qa/routing.py:29`）：
  - 该 issue 在新 verdict 中标记 `severity=minor`、`blocking=False`
  - 报告中对应段落自动追加注释 "[未完全验证]"

**循环上限（两条独立护栏）**：

- **native 引擎（默认）**：QA 轮次上限 `max_rounds`（默认 3，`orchestrator/feedback_router.py:47`
  `DEFAULT_MAX_ROUNDS = 3`）。判定见 `orchestrator/routing.py`：
  - `qa_round+1 >= max_rounds` → 强制发布（best-round 兜底，`routing.py:98`）
  - **无提升早停**：本轮 QA 分相比上一轮 `Δ < 0.01`（`_MIN_ROUND_IMPROVEMENT`，`routing.py:20,121`）
    → 不再返工，强制发布
- **legacy 引擎**：`MAX_RETRY_VERDICTS = 5`（`qa/routing.py:30`）是 `prior_verdicts` 累计次数
  上限，超出 → 强制放行。

---

## 8. QA Agent Prompt 设计

每个维度独立 prompt，避免一次塞太多。模板（伪代码）：

```
SYSTEM: 你是一名严格的竞品分析报告质检员。你只输出 JSON。
USER:
  请检查以下报告段落的事实一致性。
  段落:
    {paragraph.text}
  引用的证据:
    {evidences_json}
  请判断:
    - 段落每句话是否被证据蕴含
    - 是否存在数字 / 价格 / 百分比 / 版本号无法在证据中找到
  输出 JSON Schema: {EntailmentResult}
```

所有 prompt 外置在 `backend/agents/qa/prompts/`。

---

## 9. 与 Trace 的关联

- 每次 QA 调用生成一个 span，挂在 Reporter span 的同级
- Verdict 完整持久化到 `qa_verdicts` 表，关联 trace_id
- 前端"决策回放"可看到每次 QA 输出 + 后续 routing 触发了哪些重做

---

## 10. 业务指标贡献

QA 直接产出以下 [METRICS.md](METRICS.md) 指标：

- **准确率** = `fact_consistency.score`
- **覆盖率** = `evidence_completeness.score + schema_completeness.score / 2`
- **质检通过率** = `pass / total_verdicts`（单项目层面）

---

## 11. 实现位置

```
backend/agents/qa/
├── agent.py
├── checkers/                      # 8 个维度 checker（与 §2 表一一对应）
│   ├── _base.py
│   ├── fact_consistency.py
│   ├── evidence_completeness.py
│   ├── schema_completeness.py
│   ├── logic_consistency.py
│   ├── freshness.py
│   ├── expression.py
│   ├── coverage_density.py
│   └── identity_consistency.py
├── routing.py            # SEVERITY_WEIGHTS + 维度策略 + 判级 + 装配 payload
├── prompts/
│   ├── entailment.md
│   ├── contradiction.md
│   └── expression.md
└── tests/
```

Q 窗口实现，M0 后开始，M2 时点完成 v1（至少 3 个维度），M3 完成全部 8 维度。
