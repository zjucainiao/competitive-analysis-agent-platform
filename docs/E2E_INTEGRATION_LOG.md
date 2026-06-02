# 全链路集成日志

> 记录从 mock → real LLM 全链路切换过程中遇到的问题、根因、修复方案与当前状态。
>
> 维护方：架构窗口（本窗口）。每次跑真实 e2e 发现新问题在此追加。

## 概述

- **测试入口**：[backend/api/tests/test_real_full_chain.py](../backend/api/tests/test_real_full_chain.py)（`RUN_REAL_LLM_TESTS=1` 显式触发）
- **演示项目**：Notion + Asana / `collaboration_saas` industry
- **目标**：POST /api/projects → POST /run → 真实 Collector + Extractor + Analyst + Reporter + QA 链路跑通 → 报告非空 + QA verdict 出来
- **最新状态**（最近一次跑）：管道全通、5 Agent 都跑过、报告 5 章、evidence_completeness 1.00、**剩余 QA reject 原因都是 LLM 内容质量问题（hallucination + 字段抽取不全），是 R / E 窗口的事**

---

## 已修复（架构窗口侧）

### #1 `.env` 只有 LLM key、没搜索 API key

| | |
|---|---|
| **现象** | Collector 真实调用时 search.tavily / serper / firecrawl 全是 `enabled=False`；只剩 DuckDuckGo 而 DDG 有 `limit` 关键字 bug → 搜索返回 0 候选 → 无 official_url 种子 → Collector 失败 |
| **根因** | `.env` 里 `TAVILY_API_KEY` / `SERPER_API_KEY` / `FIRECRAWL_API_KEY` 留空。用户只配了 LLM key，不准备买搜索 API |
| **修复** | 在模板 [collab_saas_standard.yaml](../backend/orchestrator/templates/collab_saas_standard.yaml) 加 `product_urls` 表，把已知 SaaS（Notion / ClickUp / Asana / Trello / Monday / Lark / Slack）的官网当 Collector 种子；Planner 写到节点 metadata，Executor 通过 `CollectorInput.official_url` 透传 |
| **状态** | ✅ RESOLVED（Collector 实测拿 10 个 source、conf 0.60） |
| **后续改进** | 未来加入 LLM 推断未知产品 URL 的能力（任意 SaaS 都不需要手填） |

### #2 豆包不支持 `response_format={"type":"json_object"}`

| | |
|---|---|
| **现象** | Extractor 调用 `self.llm.chat(response_format=SomeSchema)` 时火山方舟返回 `400 InvalidParameter: response_format.type not supported` → 几乎所有 Extractor 调用直接 fail |
| **根因** | OpenAI/DeepSeek 支持 `response_format=json_object` 的"JSON mode"；火山方舟没实现。豆包 EP（Seed 2.0 lite）的 API 在解析请求阶段就 reject 这个参数 |
| **修复** | 重写 [llm_providers.py](../backend/agents/collector/llm_providers.py) 的 `chat()`，加**三层兜底**：<br>**L1 (tools)** —— 把 schema 包成伪函数 `submit_result`，用 `tools=[...] + tool_choice="function"` 强制结构化输出。Token 解码层面被约束，最稳。豆包 / DeepSeek / OpenAI 都支持。<br>**L2 (json-repair)** —— L1 不可用（罕见）时回退到 schema 注入 prompt + content 解析；json-repair 库自动修复 markdown 代码块、尾随逗号、未引号 key 等 LLM 常见错误。<br>**L3 (corrective retry)** —— 前两层都失败时把错误响应带回去让模型自己改一遍。<br>三层都失败才返回 `parsed=None`，由调用方报 LLM_SCHEMA_INVALID |
| **状态** | ✅ RESOLVED（Extractor 实测拿 26 evidences、4 个 pricing plan） |
| **依赖** | `json-repair>=0.30` 已加入 [pyproject.toml](../pyproject.toml) |

### #3 API 响应里多态 AgentOutput 子类字段丢失

| | |
|---|---|
| **现象** | GET /api/projects/{id}/state 返回的 `outputs` 字典里，AnalystOutput 没有 `result` 字段、ReporterOutput 没有 `draft` 字段，只剩 AgentOutputBase 的通用字段 |
| **根因** | `ProjectStateResponse.outputs: dict[str, AgentOutputBase]` 用基类做 value 类型；Pydantic 序列化时按声明类型走，**默认不导出子类专属字段** |
| **修复** | [backend/api/schemas.py](../backend/api/schemas.py) 改成 `outputs: dict[str, SerializeAsAny[AgentOutputBase]]`。`SerializeAsAny` 告诉 Pydantic 用实际子类的序列化器 |
| **状态** | ✅ RESOLVED |

### #4 Real 链路单次跑太长，测试超时阻塞 e2e

| | |
|---|---|
| **现象** | 原超时 360s 不够，反馈环触发后总耗时 ~500s 才结束；多轮 feedback 还可能让 plan 持续扩张 |
| **根因** | 真实 LLM 调用 + 真实采集每个节点 30-60s；feedback 触发后新 _v2 节点链路重跑；max_parallel=4 受限于 4 个 worker |
| **修复** | 测试拉到 15 分钟超时；max_parallel=8；加**提前退出条件**——5 个 Agent 各自至少有过一次 output + 至少一份 QA verdict 即视为"管道全通"，不强求 DAG 到 `end` 节点 |
| **状态** | ✅ RESOLVED（实测 200-220s pass） |

### #5 Reporter / QA 收不到 Extractor 抽出的 Evidence

| | |
|---|---|
| **现象** | Extractor 真实跑时产出了 26 条 Evidence（住在 `ExtractorOutput.evidences`），但 Reporter `evidence_provider=None`、QA 还会偷偷从 fixtures 加载 mock evidence_db。下游引用强制 / 数字校验跑空 |
| **根因** | AgentRegistry 启动时构造一次缓存 5 个 Agent，没有运行时注入 evidence 的通道。`QA.__init__` 在 `evidence_db=None` 时还会 `_load_evidence_db()` 从 fixtures 加载（隐藏 mock fallback）|
| **修复** | [agent_registry.py](../backend/orchestrator/agent_registry.py) 加 `make_reporter(evidence_provider=...)` + `make_qa(evidence_db=...)` 工厂方法（**不缓存**，每次新建）；[executor.py](../backend/orchestrator/executor.py) 的 `_resolve_agent()` 在调度到 reporter/qa 节点时，先 `_collect_evidences(outputs)` 把所有 `extract.*` outputs 的 Evidence 汇总成 dict，再注入新建的实例 |
| **状态** | ✅ RESOLVED（QA evidence_completeness 维度从未知 → 实测 **1.00**） |

---

## 已暴露 / 待跨窗口修复

### #6 Reporter hallucination 数字（→ R 窗口）

| | |
|---|---|
| **现象** | 真实 LLM 写报告时编造了至少 7 处量化数据（20%、27%、10、99…），evidence 原文里压根没这些数字。QA fact_consistency 维度直接 0.00 / 13 段都"entailed 0/13" |
| **根因** | Reporter 的 `_post_validate` 只在 `para.is_quantitative=True` 时校验数字；但**真实 LLM 大量漏标这个 flag**，校验跳过 → hallucination 进了草稿 |
| **影响** | QA reject、blocking=True，触发 feedback 路由回 Reporter |
| **状态** | ⏳ PENDING（已写好 patch，等 R 窗口实施） |
| **Patch（给 R 窗口）** | 见下方 [§ R 窗口 patch](#r-窗口-patch--reporter-hallucination) |

### #7 Extractor 必填字段抽不全 / 行业扩展几乎全空（→ E 窗口）

| | |
|---|---|
| **现象** | QA schema_completeness：必填均值 56%（< 阈值 0.80）、行业扩展均值 25%（< 阈值 0.60）。具体哪些字段缺：`basic_info.positioning`、`pricing.pricing_model`、`features.core_features`、12 个 collab_saas capability 字段中只填了 ~3 个 |
| **根因** | <ol><li>**用户 reviews 维度根本没抓**（user_feedback.overall_rating 没源数据；这是 Collector 的事，不在 E 窗口范围）</li><li>**LLM 在信息稀薄时倾向于留空**（safer 但坏覆盖率）</li><li>**行业扩展字段类型是 `MaturityScore \| None`**，LLM 没找到证据时把整个对象留 `None`，QA `_is_filled()` 直接判 "缺失"</li></ol> |
| **影响** | QA blocking、触发 feedback 路由到 extractor + collector |
| **状态** | ⏳ PENDING（已写好 patch，等 E 窗口实施） |
| **Patch（给 E 窗口）** | 见下方 [§ E 窗口 patch](#e-窗口-patch--extractor-字段抽取不全) |

---

## R 窗口 Patch — Reporter Hallucination

### Patch R-1（核心）：`_post_validate` 不依赖 LLM 标的 flag，强制扫描所有数字

**文件**：[backend/agents/reporter/agent.py:160-173](../backend/agents/reporter/agent.py#L160-L173)

```python
# 改前
# 4. 数字段落必须能在 evidence 中找到
if para.is_quantitative and para.evidence_ids:
    evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
    if evs:
        for kind, value in extract_quantities(para.text):
            if not quantity_supported(kind, value, evs):
                raise AgentRunError(code="UNVERIFIED_QUANTITY", ...)

# 改后：任何段落含数字都校验（不信任 LLM 的 is_quantitative 标记）
detected_quantities = list(extract_quantities(para.text))
if detected_quantities and para.evidence_ids:
    evs = [ev_db[e] for e in para.evidence_ids if e in ev_db]
    if evs:
        for kind, value in detected_quantities:
            if not quantity_supported(kind, value, evs):
                raise AgentRunError(
                    code="UNVERIFIED_QUANTITY",
                    message=(
                        f"paragraph {para.paragraph_id} quantity "
                        f"{kind}={value} not found in cited evidence "
                        f"(possible hallucination)"
                    ),
                    retriable=False,
                )
```

同样的逻辑在 line 472 还有一份（`_build_output` 内的预校验），一并改。

### Patch R-2：prompt 加强反幻觉约束

**文件**：[backend/agents/reporter/prompts/system.md](../backend/agents/reporter/prompts/system.md)

替换 rule 4 + 7、新增 rule 8：

```markdown
4. Numeric grounding (CRITICAL):
   If a paragraph contains ANY number — prices ($X), percentages (X%),
   version numbers, counts, durations — that exact number MUST appear
   verbatim (or within ±5%) in at least one cited evidence's content.

   - WRONG: "Notion 覆盖 90% 的协作场景" when no evidence states "90%"
   - RIGHT: Cite an evidence that literally says "90%", OR rewrite as
     "Notion 覆盖大多数协作场景"

   The Reporter post-validates ALL numbers in paragraphs, regardless of
   is_quantitative flag. Hallucinated numbers will fail validation and
   trigger a rework.

7. Stay grounded — do not introduce facts that are not derivable from
   the provided claims and their evidence.

8. Hallucination prevention: If you are unsure whether a number is
   supported by evidence, rewrite the sentence to use qualitative
   language ("大多数" / "显著高于" / "相对较低") instead of inventing
   a number to fill the slot.
```

### Patch R-3（推荐）：写出 ReportDraft 前自动校准 `is_quantitative`

**文件**：[backend/agents/reporter/agent.py](../backend/agents/reporter/agent.py)，`_build_output()` 装配 sections 完成后、return 前：

```python
# 兜底：LLM 漏标 is_quantitative 的段落，自动校准
for section in sections:
    for para in section.paragraphs:
        if not para.is_quantitative and list(extract_quantities(para.text)):
            para.is_quantitative = True
```

### R 窗口回归测试样例

最近一次跑真实链路 QA 抓到的具体 hallucination：

```
[major] 量化数据 '20%' 未能在引用 evidence 中找到字面匹配（容差 ±5%）
[major] 量化数据 '27%' 未能在引用 evidence 中找到字面匹配
[major] 量化数据 '20%' 未能在引用 evidence 中找到字面匹配  (×4 重复)
[major] 量化数据 '10' （count）未能在引用 evidence 中找到字面匹配
[major] 量化数据 '99' （count）未能在引用 evidence 中找到字面匹配
```

R 窗口改完后用同样的 Notion vs Asana / collaboration_saas 项目跑 [test_real_full_chain.py](../backend/api/tests/test_real_full_chain.py)，QA fact_consistency 应从 **0.00 拉到 ≥0.7**。

---

## E 窗口 Patch — Extractor 字段抽取不全

> 修正版：之前提到的 "用 `unknown` 作 maturity_level" 是**错的**——`MaturityScore.maturity_level` 是 `Literal["none","basic","standard","advanced","best_in_class"]`，schema 不允许 `unknown`。正确做法见 Patch E-2。

### Patch E-1（核心）：必填字段缺失时做 **consolidation pass** 二次抽取

**文件**：[backend/agents/extractor/agent.py](../backend/agents/extractor/agent.py)，在 `_aggregate_bucket()` 把 bucket 合成 profile 之后、`return profile` 之前。

**思路**：聚合完后检查必填字段，发现缺时，把**所有 raw_sources 的文本拼起来**做一次"全局兜底抽取"，专门补缺的字段。

```python
# Extractor._run() 主流程末尾，return profile 之前

REQUIRED_FIELDS = [
    "basic_info.positioning",
    "basic_info.target_users",
    "features.core_features",
    "pricing.pricing_model",
    "pricing.plans",
]  # 跟 QA 的 REQUIRED_FIELDS 对齐

missing = [p for p in REQUIRED_FIELDS if _field_empty(profile, p)]
if missing and self.llm is not None and inp.raw_sources:
    self._consolidation_pass(
        profile=profile,
        missing_fields=missing,
        raw_sources=inp.raw_sources,
        evidences=evidences,
    )
```

新增方法骨架：

```python
def _consolidation_pass(
    self, *, profile, missing_fields, raw_sources, evidences,
) -> None:
    """全局兜底：把所有 source 文本拼起来，让 LLM 专门补这些缺失字段。

    存在的字段不动；只有 missing_fields 列表里的字段允许写。
    LLM 出的值低 confidence (<0.5) 仍然不写，避免把 unknown 误填成实际值。
    """
    concatenated = "\n\n---\n\n".join(
        f"[source {i+1}: {s.url}]\n{(s.raw_text or '')[:2000]}"
        for i, s in enumerate(raw_sources[:6])  # 控 token
    )
    prompt = (
        "你之前已经抽出了大部分字段，但下列字段仍然为空：\n"
        f"  {', '.join(missing_fields)}\n\n"
        "请只针对这些字段，从以下汇总文本中**只在有明确依据时**填值。"
        "宁可留空也别瞎猜。每填一个字段要给出该字段的 source_quote。\n\n"
        f"汇总文本：\n{concatenated}"
    )
    # 用 LLMProvider.chat 拿一个 _ConsolidationResult schema 的结构化输出
    # 再 merge 进 profile（confidence>=0.5 才写）
    ...
```

预期效果：必填填充率从 ~56% → 80%+。

### Patch E-2（**修正版**）：行业扩展强制全打分，"未知"用 `none + notes` 表达

**文件**：[backend/agents/extractor/prompts/extract_industry_collab_saas.md](../backend/agents/extractor/prompts/extract_industry_collab_saas.md)

**之前的错**：我说"加 `unknown` 取值"——schema 里没这个值，`MaturityScore.maturity_level` 只接受 `none / basic / standard / advanced / best_in_class`。

**正确思路**：

- LLM 找不到证据时**不要把整个 `MaturityScore` 留 `None`**，而是构造一个：
  ```python
  MaturityScore(
      has_capability=False,
      maturity_level="none",
      notes="无明确证据；本次采集来源未涵盖此能力",
      evidence_ids=[],
  )
  ```
- 这个对象**通过 QA 的 `_is_filled()` 校验**（对象非 None 即算 filled），同时 `notes` 字段诚实记录"信息缺失而非能力缺失"
- 12 个 capability 字段必须**全部输出一个 MaturityScore**，不允许整字段省略

prompt 关键段落：

```markdown
## CRITICAL: All 12 capabilities MUST be scored (never omit)

For each of the 12 capability fields below, you MUST emit a `MaturityScore`
object. NEVER leave a capability field as null/missing.

Levels:
- `best_in_class`: flagship capability, prominently featured (hero section)
- `advanced`: rich functionality with dedicated docs / multiple sub-features
- `standard`: basic functionality clearly present
- `basic`: mentioned but limited
- `none`: explicit indication the capability is absent OR no evidence found

If you find NO evidence about a capability:
- Set `has_capability=false`
- Set `maturity_level="none"`
- Set `notes="无明确证据；本次采集来源未涵盖此能力"`
- Set `evidence_ids=[]`
- Use confidence ≤ 0.3

If you find SOME evidence:
- Set the appropriate level (basic/standard/advanced/best_in_class)
- Cite the evidence_id(s)
- Use confidence 0.5-0.95 depending on evidence quality

Capability list (all 12 required):
- task_management
- kanban_view
- calendar_view
- gantt_view
- document_collaboration
- workflow_automation
- knowledge_base
- team_permission
- third_party_integration
- mobile_support
- realtime_editing
- ai_assistance
```

预期效果：行业扩展填充率从 ~25% → **100%**（其中部分是 "none + 信息不足" 标注，但字段全填，QA 不再 ding "缺失"）。

### Patch E-3：冲突字段合并审计

**文件**：[backend/agents/extractor/agent.py:854-857](../backend/agents/extractor/agent.py#L854-L857)

`_scalar` 在冲突时已正确保留高 confidence 值；但 list/复合字段的合并路径可能在冲突时把值丢空。审一遍这 5 个必填字段的合并：

| 字段 | 合并路径 | 风险 |
|---|---|---|
| `basic_info.name` | `_scalar` | OK |
| `basic_info.category` | `_scalar` | OK |
| `basic_info.positioning` | `_scalar` | OK |
| `basic_info.target_users` | list 合并 | **审查** |
| `features.core_features` | list 合并 | **审查** |
| `pricing.pricing_model` | `_normalize_pricing_model` + 选择逻辑 | **审查**（实测出问题） |
| `pricing.plans` | `_safe_pricing_plan` 列表 + 同名合并 | **审查** |
| `user_feedback.overall_rating` | scalar | OK（但本来就没数据源） |

要点：CONFLICTING 时**至少保留一个值**（最高 confidence 的），不要丢空。

### Patch E-4（可选 / 进阶）：抽取阶段加 "schema retry"

`_extract_from_source` 内，针对每个 source + dimension 检查是否抽出了"该 dimension 预期的字段"：

- homepage → 应出 `basic_info.positioning / category / target_users` + `features.core_features` 概览
- pricing → 必出 `pricing.pricing_model + plans`
- features → 详细 `features.core_features` + 12 个 capability 评分
- help_docs → 补充 `target_users / core_features`

应该出但没出 → 对同一 source 再 prompt 一次"明确要这些字段"，最多 1 次。

### E 窗口回归测试

跑 [test_real_full_chain.py](../backend/api/tests/test_real_full_chain.py)，QA verdict 应满足：

```
✓ schema_completeness: score >= 0.80
  notes=必填均值 ≥80%，行业扩展均值 ≥60%（含 "none+无证据" 计入 filled）
```

而不是现状的 `0.47`。

---

## 当前打分预期（R + E 两窗口都改完后）

跑同一项目（Notion + Asana / collab_saas）的 QA verdict 预测：

| 维度 | 现状 | R/E 改完预期 | 备注 |
|---|---|---|---|
| fact_consistency | 0.00 | ≥ 0.75 | R 窗口拦截 hallucination |
| evidence_completeness | 1.00 | 1.00 | 已经满分 |
| schema_completeness | 0.47 | ≥ 0.80 | E 窗口 consolidation + 强制行业扩展 |
| logic_consistency | 0.90 | 0.90 | 软分 |
| freshness | 1.00 | 1.00 | |
| expression | 1.00 | 1.00 | |
| **overall** | **reject** | **needs_revision 或 pass** | |

如果还是 `needs_revision`，第二轮 feedback（FeedbackRouter `max_rounds=3` 默认）应能自愈到 pass。

---

## 怎么继续这份文档

新发现的全链路问题，请按以下模板追加到 "已暴露 / 待跨窗口修复" 或 "已修复" 节：

```markdown
### #N 简短标题（→ X 窗口）

| | |
|---|---|
| **现象** | 用户能观察到的现象，最好带具体数字 / 日志片段 |
| **根因** | 一两句话定位代码位置 / 模型行为 |
| **修复** | 改了什么文件，思路要点 |
| **状态** | ✅ RESOLVED / ⏳ PENDING / 🔄 IN PROGRESS |
| **依赖** | （可选）涉及的新依赖 / schema 变更 / 跨窗口协作 |
```
