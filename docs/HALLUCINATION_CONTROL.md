# 幻觉抑制策略

> 本文档定义平台抑制大模型幻觉的 4 层策略。对应评分要点：「上下文管理、错误恢复、幻觉抑制有明确策略（如自一致性校验、引用强制、超长上下文分片）」。

---

## 1. 总体思路

幻觉不靠"最后让一个 QA 模型审一遍"来兜底，而是**分层抑制**，越往源头越严格：

```
┌────────────────────────────────────────────┐
│ L4 QA Agent                                │   ← 最后的安全网（兜底）
│    事实/逻辑/时效一致性审查                  │
├────────────────────────────────────────────┤
│ L3 Reporter 引用强制                        │   ← 生成时阻止
│    无 evidence_ids 的事实段落直接拒绝输出    │
├────────────────────────────────────────────┤
│ L2 LLM 输出强约束                           │   ← 输出形式约束
│    response_format / tool_use + JSON 校验  │
├────────────────────────────────────────────┤
│ L1 上下文管理                               │   ← 减少诱因
│    超长分片 + RAG + 显式禁编造指令          │
└────────────────────────────────────────────┘
```

每一层独立生效，叠加在一起把"模型自己编"的概率压到很低。

---

## 2. L1 上下文管理

### 2.1 超长文本分片

```python
# backend/tools/chunker.py
def chunk_text(text: str, max_tokens: int = 400, overlap: int = 50) -> list[Chunk]:
    ...
```

- 单网页正文超过 400 tokens 必须切片
- 切片后入向量库，Agent 用 RAG 取相关片段
- **禁止**一次性把整篇网页塞给 LLM 然后让它"自己理解"

### 2.2 RAG 检索约束

Analyst / QA 在做对比 / 验证时：

```python
# 必须从 Evidence 池里取
top_k_evidences = evidence_store.search(query, product=product, k=10)
prompt = render(template, evidences=top_k_evidences)
```

- 检索结果作为唯一事实来源
- LLM prompt 明确指令："仅基于以下 evidence 回答，未提及的事实必须返回 null"

### 2.3 显式禁编造指令

每个 Agent 的 system prompt 必含：

```
RULES:
- If the source material does not state a fact, return null. Do not infer.
- Every value you produce must trace back to source_quote.
- If you are unsure, lower the confidence and explain in self_critique.
```

### 2.4 上下文窗口预算

每个 Agent 在调用 LLM 前算预算：

```python
budget = MAX_CONTEXT - len(system_prompt) - len(output_reserve)
context_chunks = pack_chunks_within_budget(retrieved_chunks, budget)
```

超出预算 → 截断 retrieved_chunks（按相关性排序），不让 LLM 自己处理 OOC。

---

## 3. L2 LLM 输出强约束

### 3.1 结构化输出

**禁止**：让 LLM 输出自然语言后正则解析。

**必须**：用 `response_format=json_schema` 或 `tool_use`。

```python
# 通过 LLMProvider 抽象
output = self.llm.chat(
    system=SYSTEM,
    messages=msgs,
    response_format=ExtractorOutput,   # Pydantic → JSON Schema
)
# output 自动反序列化为 ExtractorOutput
```

各 Agent 默认走结构化输出：

| Agent | 输出 Schema |
|---|---|
| Collector | LLM 仅用于 URL 排序 / 类型识别 / 摘要，结构化输出 |
| Extractor | `CompetitorProfile`（含 source_quote）|
| Analyst | `DimensionAnalysis`（含 evidence_ids） |
| Reporter | `ReportSection`（含 paragraphs[].evidence_ids） |
| QA | `QADimensionResult` × 6 + `QAIssue[]` |

### 3.2 二次校验

LLM 返回的 JSON 即使过了 schema 校验，也走二次业务校验：

- Extractor：source_quote 必须能在 raw_text 中匹配（substring 或 fuzzy）
- Reporter：evidence_ids 必须都存在于 Evidence 库
- Analyst：claim 的 evidence_ids 必须出现在 input.profiles 的 evidence_refs 中

校验失败 → 重试（最多 2 次，把校验错误注入下一轮 user message）→ 再失败 → 抛错。

### 3.3 温度约束

| 任务类型 | 推荐温度 |
|---|---|
| 抽取 | 0.0–0.1 |
| 分析 | 0.2–0.4 |
| 撰写 | 0.4–0.6 |
| 质检（判断类） | 0.0 |

外露在配置里：`agents/<name>/config.py` 中定义。

---

## 4. L3 Reporter 引用强制

### 4.1 段落级强制

```python
class ReportParagraph(BaseModel):
    text: str
    claim_ids: list[str] = []
    evidence_ids: list[str] = []
    is_quantitative: bool = False
    is_soft_conclusion: bool = False     # "可能"、"通常"等

@validator("evidence_ids")
def must_have_evidence(cls, v, values):
    if values.get("is_soft_conclusion"):
        return v   # 软结论允许空
    if not v:
        raise MissingCitationError(...)
    return v
```

引用空 → BaseAgent 自动捕获 → 输出 `status=needs_rework` + 错误码 `MISSING_CITATION`。

### 4.2 数字段落强校验

```python
if paragraph.is_quantitative:
    for num in extract_numbers(paragraph.text):
        if not any(num in ev.content for ev in get_evidences(paragraph.evidence_ids)):
            raise UnverifiedQuantityError(...)
```

价格 / 百分比 / 版本号必须能在 evidence 字面找到（±5% 容差）。

### 4.3 禁用语规则

```python
BANNED_PHRASES = ["行业唯一", "绝对领先", "完美", "100%", "最佳产品", "无可替代"]

if any(p in paragraph.text for p in BANNED_PHRASES):
    raise ExpressionViolation(...)
```

由 Reporter 自检 + QA 复检，双保险。

---

## 5. L4 QA Agent 兜底

详见 [QA.md](QA.md)。核心是 6 维度审查：

- fact_consistency：entailment check
- evidence_completeness：引用全覆盖
- schema_completeness：必填字段
- logic_consistency：内部矛盾
- freshness：时效性
- expression：表达规范

失败 → 路由回上游 → 重做。

---

## 6. 自一致性（Self-Consistency）

对于**高 stakes 结论**（如核心 SWOT、关键差异化机会），使用 self-consistency：

```python
# 同一 prompt sample N 次，取多数
results = [llm.chat(...) for _ in range(N)]
final = majority_vote(results)  # 或 LLM 自评
```

- N 默认 3，关键结论 5
- 仅对 Analyst 关键维度启用（按需，成本敏感）
- 各 sample 结果都入 trace，便于审查

---

## 7. Agent 自评估（Self-Critique）

每个 Agent 输出 `confidence` + `self_critique`：

```
confidence < 0.6 触发情况：
- 上游数据不足
- 多源冲突
- 模型自身不确定（输出包含"不太确定"等表达）
- Schema 必填字段为 null
- 引用证据 < 阈值

self_critique 文本必填：
- 简要说明为何 confidence 低
- 哪些字段最不确定
- 建议下游怎么用（"该结论建议仅作参考"）
```

低 confidence → BaseAgent 自动标 `status=needs_rework` → Orchestrator 决定是否触发 QA 提前介入。

这是**前瞻性亮点之二**，详见 [INNOVATIONS.md](INNOVATIONS.md) § 2。

---

## 8. 错误恢复策略

| 错误类型 | 恢复策略 |
|---|---|
| LLM 输出 JSON 不合法 | 重试 2 次，注入校验错误 |
| LLM 超时 | 指数退避重试（1s/4s/16s），最多 3 次 |
| LLM 限流 | 退避 + 切换备用模型 |
| Tool 失败 | 切换备用工具（Firecrawl → Playwright） |
| Source 抓取失败 | 跳过该源，标记 partial |
| Evidence 找不到 | 标 unverified，降低 confidence |
| 反复 QA 失败 | 第 3 轮后转 minor，允许发布带标注 |

---

## 9. 实现位置

```
backend/agents/_base.py          # BaseAgent 自评估 + 引用强制基础
backend/llm/provider.py          # 结构化输出 + 重试
backend/llm/validators.py        # JSON schema + 业务二次校验
backend/agents/reporter/         # 引用强制实现
backend/agents/qa/               # 6 维度审查
backend/observability/           # 失败链路 trace
```

---

## 10. 测试要求

每个 Agent 必须有"幻觉测试"用例：

- 给模型一段 evidence 里**不存在**的事实诱导问题
- 期望：Agent 返回 null + low confidence + self_critique 说明
- 这些 case 进 fixture，CI 跑

---

## 11. 评分映射

| 评分要点 | 本文档落地 |
|---|---|
| 引用强制 | § 4 Reporter |
| 自一致性 | § 6 |
| 超长上下文分片 | § 2.1 |
| 结构化输出 | § 3 |
| 错误恢复 | § 8 |
| 自评估 | § 7 |
