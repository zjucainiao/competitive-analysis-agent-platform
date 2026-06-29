# Extractor Agent · 结构化抽取

> 详细契约见 [docs/AGENTS.md § 4](../../../docs/AGENTS.md#4-extractor抽取-agent)。

## 职责

把 `RawSourceDoc[]` 转换为符合 Schema 的 `CompetitorProfile`，同时把支撑性事实切分为 `Evidence[]` 与字段一一绑定。**不做对比分析**——那是 Analyst 的事。

## 输入 / 输出

- Input：`ExtractorInput`（含 `raw_sources: list[RawSourceDoc]` + `industry_schema_id`）
- Output：`ExtractorOutput`（含 `profile: CompetitorProfile` + `evidences: list[Evidence]` + `field_confidence` + `field_status` + `unmatched_quotes`）

## 实现链（v1.1）

```
raw_sources
  → TextChunker（段落优先，保留 char 偏移）
  → 每个 source 单独跑一次 LLM
       prompts/extract_source.md → _SourceExtraction { claims: list[RawClaim] }
       RawClaim = { field_path, value, source_quote, confidence }
  → 【v1.1 新增】Consolidation pass：
       检查 basic_info.positioning / basic_info.target_users / features.core_features /
       pricing.pricing_model / pricing.plans 是否仍空，缺哪些就把所有 raw_text 拼起来
       跑一次 prompts/extract_consolidation.md 专门补；confidence < 0.5 的 claim 直接丢
  → EvidenceLinker：source_quote 反向定位回 raw_text
       命中（substring / 模糊 Jaccard）→ 生成 Evidence + evidence_id
       未命中 → 加入 unmatched_quotes，相关字段 field_status=unverified
  → 跨 source 聚合 + 同字段冲突检测（field_status=conflicting，best 值仍保留）
  → industry 扩展（v1 仅 collaboration_saas）
       prompts/extract_industry_collab_saas.md：强制要求 12 个 capability 全部输出
       【v1.1 新增】LLM 漏掉任何一维 → 装配阶段自动塞 has_capability=False + maturity_level=none
       + notes='无明确证据；本次采集来源未涵盖此能力' 占位 MaturityScore
  → 装配 CompetitorProfile + field_confidence + field_status + evidence_refs
  → 自评估
```

Mock 模式：跳过 LLM，直接从 `fixtures/mock_data/competitor_profiles/<product>.json` 还原 profile，从 `evidences/evidence_db.jsonl` 还原 evidences。

## 目录

```
backend/agents/extractor/
├── __init__.py
├── agent.py             # Extractor 主类
├── tools.py             # TextChunker / EvidenceLinker / prompt helpers
├── fixtures.py          # Mock 数据加载
├── prompts/
│   ├── extract_source.md
│   ├── extract_consolidation.md   # v1.1：必填字段缺失时的补刀 prompt
│   └── extract_industry_collab_saas.md
├── tests/
│   ├── conftest.py      # ScriptedLLM / NullTracer / 输入工厂
│   └── test_agent.py    # 17 个 case：mock / real / 错误 / linker / consolidation / 12 维 / scalar conflict
└── README.md
```

## 抽制幻觉的关键约束

- 原文未提及的字段 **填 None**，绝不编造。LLM 输出的每个 claim 必须带 `source_quote`，未给出 source_quote 的字段在装配后会被丢弃。
- 每个非 None 字段经过 `EvidenceLinker` 反向验证。命中 → `field_status=verified` 并 mint `Evidence`；未命中 → `field_status=unverified` + 降低 confidence + 进 `unmatched_quotes`。
- 同字段多源冲突 → `field_status=conflicting` + 触发 `CONFLICTING_FACTS` 错误码，由 QA 决定路由。
- LLM 调用走 `response_format=<pydantic>` 强结构化输出，温度 0.0；prompts 全部外置在 `prompts/*.md`。

## 错误码（AGENTS.md § 4.7）

| Code | 触发条件 |
|---|---|
| `EVIDENCE_UNMATCHED` | 有 source_quote 无法在任何 raw_source 中匹配 |
| `SCHEMA_FIELD_MISSING` | 必填字段（`basic_info.name` / `basic_info.positioning` / `pricing.pricing_model`）任一缺失 |
| `CONFLICTING_FACTS` | 跨源对同一字段给出不同值 |
| `DIMENSION_NOT_APPLICABLE` | industry_schema_id 非 collaboration_saas（v1 限制） |
| `UPSTREAM_MISSING` | 非 mock 模式缺 LLM / raw_sources 为空 / mock 模式找不到 fixture |
| `LLM_SCHEMA_INVALID` | 单个 source 的 LLM 输出解析失败（不阻塞，跳过该 source） |

## 自评估触发条件（AGENTS.md § 4.6）

- > 30% 字段 evidence 匹配失败 → 降 confidence
- > 20% 必填字段缺失 → 降 confidence
- 出现 `conflicting` 字段 → 降 confidence
- industry 扩展未抽到 → 降 confidence
- `confidence < 0.6` 时 BaseAgent 强制 `self_critique` 非空

## 运行方式

```python
from backend.agents.extractor import Extractor
from backend.schemas import ExtractorInput, RawSourceDoc

# Mock 模式（无需 LLM）
agent = Extractor(mock=True)
out = agent.invoke(extractor_input, trace_id="...", span_id="...")

# 真实模式
agent = Extractor(llm=my_llm_provider, tracer=my_tracer)
out = agent.invoke(extractor_input, trace_id="...", span_id="...")
```

`llm` 必须满足 `LLMProviderProtocol`（见 `backend/agents/_base.py`），duck-typing 即可，不强制继承。

## 测试

```bash
pytest backend/agents/extractor/tests/ -q
```

17 个 case 覆盖：mock 正常 / mock 未知产品 / 输入 Schema 严格性 / 真实模式缺 LLM / 真实模式 scripted LLM 装配 / 未匹配 source_quote / 跨源冲突（pricing_model + overall_rating）/ 必填字段缺失 / post_validate 强一致性 / EvidenceLinker substring + fuzzy + miss / TextChunker 偏移保留 / **consolidation pass 补必填 + 阈值过滤** / **consolidation 在字段已齐时跳过** / **industry 扩展 12 维兜底**。

## 已知限制 / TODO

- v1：仅 `collaboration_saas_v1` 行业扩展真抽，其他 industry 返回 warn 错误码
- v1：长文本 chunk 后取前 6 段塞进 LLM，没接真正的 RAG。token 预算紧时可裁剪
- v1：Evidence 仅挂在 output 上返回，没写向量库 / 关系库
- v1：跨源冲突仅按 `field_path == field_path` 简单比较，复杂结构（plans 同名不同价）已专门处理，其他列表字段未做细粒度对齐
- v1：`qa_feedback` 仅作为字符串透传进 prompt，未做结构化解析
- v1.1：consolidation pass 全文上限 12k char，超过会截断。Notion+Asana 双产品当前没触发，更长来源（changelog/help）可能需调

## Changelog

- **v1.1.0（2026-05-29）**：
  - 新增 consolidation pass：per-source 之后做一刀全文兜底，补 positioning / target_users / core_features / pricing_model / pricing.plans
  - industry 扩展强制 12 维全量填充：LLM 漏掉的字段在装配阶段塞标准占位 MaturityScore（has_capability=False / level=none / notes 标注"无明确证据"）
  - 标量合并路径统一收敛到 `_resolve_scalar`：所有 scalar 字段在跨源给出不同值时一律标 `field_status=conflicting`，但 best-confidence 的值始终保留（绝不丢空）
  - 触发器：QA `schema_completeness=0.47`（阈值 0.80）的回归
- **v1.0.0（2026-05-29）**：首版，per-source 抽取 + evidence binding + collab_saas 扩展
