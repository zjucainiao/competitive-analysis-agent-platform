# 幻觉抑制策略

> 本文档定义平台抑制大模型幻觉的分层策略：引用强制、超长上下文分片、结构化输出约束、错误恢复等。
>
> **本文档严格对齐已落地代码**：每条策略均给出实现位置（file:line）。未实现/规划中的能力（如自一致性采样）明确标注为「未启用 / P2 候选」，不计入当前防线。

---

## 1. 总体思路

幻觉不靠"最后让一个 QA 模型审一遍"来兜底，而是**分层抑制**，越往源头越严格：

```
┌────────────────────────────────────────────┐
│ L4 QA Agent                                │   ← 最后的安全网（兜底）
│    8 维度一致性审查 + 返工路由               │
├────────────────────────────────────────────┤
│ L3 Reporter 引用强制                        │   ← 生成时阻止
│    无 evidence_ids 的事实段落直接拒绝输出    │
├────────────────────────────────────────────┤
│ L2 LLM 输出强约束                           │   ← 输出形式约束
│    response_format / tool_call + JSON 校验 │
├────────────────────────────────────────────┤
│ L1 上下文管理                               │   ← 减少诱因
│    段落优先切片 + 显式禁编造指令             │
└────────────────────────────────────────────┘
```

每一层独立生效，叠加在一起把"模型自己编"的概率压到很低。此外有一条横切的
**Agent 自评（self-critique）**机制（§ 7），低置信输出会被强制标 `needs_rework`。

---

## 2. L1 上下文管理

### 2.1 长文本切片（段落优先）

`backend/agents/extractor/tools.py:39` 的 `TextChunker` 在 Extractor 抽取前把
单源网页正文切片：

```python
# backend/agents/extractor/tools.py:39
class TextChunker:
    """段落优先的极简切片器。先按双换行切，单段超长再按句切；
    每个 chunk 不超过 max_chars(默认 1200 char)，相邻保留 overlap(100)。"""
    def chunk(self, source: RawSourceDoc) -> list[Chunk]: ...
```

- v1 用字符计而非 token，避免引入额外 tokenizer 依赖（`400 char ≈ 100 token`）。
- 每个 `Chunk` 记录 `char_start/char_end` 偏移，供后续 `EvidenceLocation` 溯源填充。

Extractor 当前**不做向量检索**：切片后取前若干片拼接作为单源上下文（上限保护），由 LLM 在该上下文内抽取：

```python
# backend/agents/extractor/agent.py:723-726
chunks = self.chunker.chunk(source)
page_text = "\n\n".join(c.text for c in chunks[:6])  # 上限保护，最多前 6 片
```

> **未实现（不在当前防线内）**：向量库 / RAG 检索。`embed()` 在
> `backend/agents/collector/llm_providers.py:320-324` 直接 `raise NotImplementedError`；
> Analyst README 明确「v1 不接 RAG，`evidence_store_handle` 透传不使用」
> （`backend/agents/analyst/README.md:103`）。事实约束目前靠下游的引用强制（L3）
> 与 QA 审查（L4），而非检索端隔离。

### 2.2 显式禁编造指令

每个 Agent 的 prompt（`backend/agents/*/prompts/*.md`）以「仅依据给定来源、缺失即返回
null/降置信」为基调，要求每个抽取值可回溯到 `source_quote`。这是 prompt 层的软约束，
真正的硬保障在 L2（schema）与 L3（引用 + 数字 + entailment 校验）。

---

## 3. L2 LLM 输出强约束

### 3.1 结构化输出

**禁止**：让 LLM 输出自然语言后正则解析。

**必须**：用结构化输出，传入 Pydantic 模型作为 `response_format`。

```python
# backend/agents/collector/llm_providers.py:237
output = self.llm.chat(
    system=SYSTEM,
    messages=msgs,
    response_format=ExtractorOutput,   # Pydantic 模型
)
```

`OpenAICompatibleLLM.chat()` 在 `response_format` 非空时启用**三层兜底**
（`llm_providers.py:237-316`）：

1. **L1 tool_call**：把 `model_json_schema()` 注入为 function tool，用
   `tool_choice="function"` 强制模型按 schema 产出 JSON（`llm_providers.py:340`）。
2. **L2 json_mode + prompt 注入**：provider 不支持 tool_call 时落到此层，把 schema
   塞进 system，开 `{"type":"json_object"}`（若支持），再 `json-repair`。
3. **L3 修复重试**：带着上一次的坏输出 + 报错回灌，要求模型只吐合规 JSON。

三层都失败 → `resp.parsed` 为 None，调用方据此报 `LLM_SCHEMA_INVALID`。

各 Agent 默认走结构化输出：

| Agent | 输出 Schema |
|---|---|
| Collector | LLM 仅用于 URL 排序 / 类型识别 / 摘要，结构化输出 |
| Extractor | `CompetitorProfile`（含 source_quote）|
| Analyst | `AnalysisResult`（claim 含 evidence_ids） |
| Reporter | `ReportDraft`（含 paragraphs[].evidence_ids） |
| QA | `QAVerdict`：`QADimensionResult` × 8 + `QAIssue[]` |

### 3.2 二次校验

> **此锚点被代码引用**（`backend/agents/analyst/dimensions.py` 注释引
> `docs/HALLUCINATION_CONTROL.md § 3.2`）。请勿改动本节编号。

LLM 返回的 JSON 即使过了 schema 校验，也走 Agent 内联的二次业务校验：

- **Extractor**：未匹配字段比例超阈值自动降置信。
  `UNVERIFIED_FIELD_THRESHOLD=0.30`（>30% 字段未匹配 → 低 confidence），并对冲突字段
  施 `PENALTY_CONFLICTING=0.15`（`backend/agents/extractor/agent.py:304-309`）。
- **Reporter**：`_post_validate` 强制 `claim_ids` / `evidence_ids ⊆ 分析池`、事实段落必须有引用、数字必须被引用证据支撑、可选 entailment 校验（详见 § 4）。

校验逻辑**内联在各 Agent**（`reporter/agent.py:224`、`extractor/agent.py`），不存在独立的
`backend/llm/validators.py`。

### 3.3 温度约束

各任务温度**硬编码在对应 Agent 的 `agent.py` 调用处**，不存在 `agents/<name>/config.py`：

| 任务类型 | 温度 | 实现位置 |
|---|---|---|
| 抽取（Extractor） | `0.0` | `extractor/agent.py:744,817,876` |
| 分析（Analyst） | `0.3` | `analyst/agent.py:318` |
| 撰写（Reporter 写作） | `0.3` | `reporter/agent.py:720` |
| 判断类（Reporter entailment judge / QA） | `0.0` | `reporter/agent.py:816,1074` |

抽取与判断类用 `0.0` 求确定性；分析 / 撰写用 `0.3` 给一点表达自由度但仍偏保守。

---

## 4. L3 Reporter 引用强制

核心实现：`Reporter._post_validate`（`backend/agents/reporter/agent.py:224-314`）。
逐 section / paragraph 跑五道校验，任一硬约束失败即 `raise AgentRunError(retriable=False)`，
被 BaseAgent 转成 `NEEDS_REWORK` 并带错误码。

### 4.1 引用池约束 + 段落级强制

```python
# reporter/agent.py:237-272
# 1+2. claim_ids / evidence_ids 必须 ⊆ 分析池
if bad_claims or bad_ev:
    raise AgentRunError(code="INSUFFICIENT_EVIDENCE", retriable=False)
# 3. 非软结论的事实段落必须有 evidence_ids
if not para.is_soft_conclusion and not para.evidence_ids and para.text.strip():
    raise AgentRunError(code="MISSING_CITATION", retriable=False)
```

- 引用了分析池外的 claim/evidence → `INSUFFICIENT_EVIDENCE`。
- 事实段落无引用 → `MISSING_CITATION`（软结论 `is_soft_conclusion=True` 允许空）。

### 4.2 数字段落强校验

```python
# reporter/agent.py:273-289
detected_quantities = list(extract_quantities(para.text))   # 不信任 LLM 的 is_quantitative 标记
if detected_quantities and para.evidence_ids:
    for kind, value in detected_quantities:
        if not quantity_supported(kind, value, evs):
            raise AgentRunError(code="UNVERIFIED_QUANTITY", retriable=False)
```

- **不**依赖 LLM 自标的 `is_quantitative`（它常漏标 → 漏网），而是对**段落里出现的所有数字**
  （价格 / 百分比 / 版本号 / 多位纯数字）逐个校验是否被引用证据支撑。
- 未被支撑 → `UNVERIFIED_QUANTITY`（疑似 hallucination）。

### 4.3 语义层 entailment 校验

```python
# reporter/agent.py:290-314
if self.entailment_check and not self.self_correct and ...:
    verdict = self._judge_entailment(para, evs)        # LLM-as-judge, temperature=0.0
    if verdict is not None and not verdict.entailed:
        raise AgentRunError(code="UNVERIFIED_INFERENCE", retriable=False)
```

非软结论段落经 LLM 裁判判断是否被引用证据**蕴含**；不蕴含 → `UNVERIFIED_INFERENCE`。
（开启 `self_correct` 时已在 `_run_self_correct` 内修复，此处作兜底。）

### 4.4 禁用语 / 绝对化表述（软惩罚，非拒绝）

禁用语**不**抛异常，而是降置信 + 计入元数据。词表与正则在
`backend/agents/reporter/tools.py:32-67`：

```python
# tools.py:32  固定禁用词
BANNED_TERMS = ("行业唯一", "行业第一", "绝对领先", "完美", "最佳产品", "无可替代", ...)
# tools.py:52  绝对化宣称正则（如「98% xxx 信赖」「100强企业」）
```

命中后（`reporter/agent.py`）：

- 累计 `banned_hits`，按 `PENALTY_PER_BANNED_HIT=0.05`（`reporter/agent.py:171`）derate
  段落 / 整体 confidence（`_overall_confidence`，`reporter/agent.py:501`）。
- 命中数计入 `metadata.banned_term_hits`（`reporter/agent.py:492`）。

即：禁用语是**软信号**（降置信 + warn + 进 metadata），不直接拒绝输出，也不存在
`ExpressionViolation` 异常。QA 的 `expression` 维度（§ 5）再复检一遍。

---

## 5. L4 QA Agent 兜底

详见 [QA.md](QA.md)。核心是 **8 维度审查**（`QADimension`，`backend/schemas/qa.py:25-35`）：

- `fact_consistency`：事实一致性
- `evidence_completeness`：引用全覆盖
- `schema_completeness`：必填字段
- `logic_consistency`：内部矛盾
- `freshness`：时效性
- `expression`：表达规范
- `coverage_density`：覆盖密度
- `identity_consistency`：产品身份一致性（拦截「分析钉钉却引用了飞书/Slack 的内容」）

失败 → 经 `decide_qa_route`（`backend/orchestrator/routing.py:61`）路由回上游 →
重做，最多 `DEFAULT_MAX_ROUNDS=3` 轮（`backend/orchestrator/feedback_router.py:47`）。
`qa_round+1 >= max_rounds` 时强制发布并标 `aborted=True`（`routing.py:98`）。

---

## 6. 自一致性（Self-Consistency）—— 未启用 / P2 候选

> **当前未实现，不计入现有防线。** Analyst README 明确：「自一致性（self-consistency）
> 目前未启用，关键维度（SWOT / DIFFERENTIATION）可在 P2 加入 N=3 采样」
> （`backend/agents/analyst/README.md:105`）。代码中**没有** `majority_vote` /
> 多次采样逻辑。

规划方向（P2，尚未落地）：对高 stakes 结论（核心 SWOT、关键差异化机会）同一 prompt
采样 N 次取多数。当前事实约束完全依赖 L1–L4 与自评，不依赖自一致性。

---

## 7. Agent 自评估（Self-Critique）

每个 Agent 输出 `confidence` + `self_critique`。`BaseAgent` 在
`backend/agents/_base.py` 强制二者一致：

```python
# _base.py:310
SELF_CRITIQUE_THRESHOLD: ClassVar[float] = 0.6

# _base.py:543-548  低置信但无 self_critique → 抛错
def _enforce_self_critique(self, out):
    if out.confidence < self.SELF_CRITIQUE_THRESHOLD and not out.self_critique.strip():
        raise AgentRunError(code="SELF_CRITIQUE_REQUIRED", ...)
```

- `confidence < 0.6` 且 `self_critique` 为空 → `SELF_CRITIQUE_REQUIRED`
  （`_base.py:466`），并把 `out.status` 置为 `NEEDS_REWORK`（`_base.py:473,488`）。
- **Extractor 自动降置信**：未匹配字段比例 > `UNVERIFIED_FIELD_THRESHOLD=0.30` 或必填字段
  缺失 > `MISSING_FIELD_THRESHOLD=0.20` → 降 confidence；冲突字段施
  `PENALTY_CONFLICTING=0.15`（`backend/agents/extractor/agent.py:304-309`）。

低 confidence → BaseAgent 标 `status=needs_rework`，作为 QA 判级的加权信号
（QA 据 `upstream_statuses` 把自评不达标 Agent 名下的 minor 升 major，见
`backend/schemas/qa.py:149`）。

---

## 8. 错误恢复策略

| 错误类型 | 恢复策略 | 实现位置 |
|---|---|---|
| LLM 输出无法解析为合规 JSON | 三层兜底（tool_call → json_mode → 修复重试）；仍失败报 `LLM_SCHEMA_INVALID` | `llm_providers.py:237-316` |
| Tool 抓取失败 | 按 `firecrawl → playwright → httpx` 顺序回退 | `collector/agent.py:562` |
| 真实抓取链全失败 | 按 `fallback_to_mock` 兜底到 mock fixtures | `collector/agent.py:607-608` |
| 抽取字段未匹配 / 必填缺失 | 自动降 confidence + self_critique | `extractor/agent.py:304-309` |
| 反复 QA 失败 | `qa_round+1 >= max_rounds(=3)` 强制发布并标 `aborted=True`；API 层择优发布最优轮 | `routing.py:98`、`feedback_router.py:47` |

---

## 9. 实现位置

```
backend/agents/_base.py                       # BaseAgent 自评估强制(SELF_CRITIQUE_THRESHOLD)
backend/agents/collector/llm_providers.py     # 结构化输出三层兜底 + 重试(chat:237)
backend/agents/extractor/tools.py             # TextChunker 段落切片(:39)
backend/agents/extractor/agent.py             # 抽取自评降置信(:304-309) + 切片取用(:723)
backend/agents/reporter/agent.py              # 引用/数字/entailment 强校验(_post_validate:224)
backend/agents/reporter/tools.py              # 禁用词 + 绝对化正则(:32-67)
backend/schemas/qa.py                         # QA 8 维度定义(:25-35)
backend/orchestrator/routing.py               # QA 路由 decide_qa_route(:61)
backend/orchestrator/feedback_router.py       # 返工轮次上限 DEFAULT_MAX_ROUNDS=3(:47)
```

> 注意：不存在 `backend/llm/provider.py` / `backend/llm/validators.py` /
> `backend/tools/chunker.py`。LLM provider 实为
> `backend/agents/collector/llm_providers.py`；二次校验**内联**在各 Agent 内。

---

## 10. 测试约定

幻觉抑制相关测试目标（mock LLM 下可断言）：

- Reporter 引用 / 数字 / entailment 校验：构造越界引用、未被证据支撑的数字，断言抛
  对应错误码（`INSUFFICIENT_EVIDENCE` / `UNVERIFIED_QUANTITY` / `UNVERIFIED_INFERENCE`）。
- 自评强制：低 confidence + 空 self_critique 断言抛 `SELF_CRITIQUE_REQUIRED`。
- 结构化输出三层兜底：模拟 provider 不支持 tool_call，断言落到 json_mode / 修复重试。
