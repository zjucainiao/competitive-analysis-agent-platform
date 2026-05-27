# Extractor Agent · 结构化抽取

> 详细契约见 [docs/AGENTS.md § 4](../../../docs/AGENTS.md#4-extractor抽取-agent)。

## 职责

把 `RawSourceDoc[]` 转换为符合 Schema 的 `CompetitorProfile`，同时把支撑性事实切分为 `Evidence[]` 入库。**不做对比分析**。

## 输入 / 输出

- Input：`ExtractorInput`（含 raw_sources + industry_schema_id）
- Output：`ExtractorOutput`（含 `profile: CompetitorProfile` + `evidences: list[Evidence]`）

## 关键工具

- LLM 结构化抽取（`response_format=CompetitorProfile`）
- `text.chunker`：长文本切片
- `vector.upsert`：Evidence 入向量库
- `evidence_linker`：source_quote → Evidence ID 反向匹配

## 实现位置

```
backend/agents/extractor/
├── agent.py
├── prompts/
│   ├── extract_basic_info.md
│   ├── extract_features.md
│   ├── extract_pricing.md
│   ├── extract_user_feedback.md
│   └── extract_industry_ext.md
├── tools.py
├── README.md
└── tests/
```

## 抽取流程（两步法）

1. **粗抽取**：LLM 按 Schema 输出 JSON，附带 `source_quote`
2. **证据绑定**：source_quote → 反向匹配 raw_source → 生成 Evidence + evidence_id

字段无 evidence 匹配 → `field_status=unverified` + 降低 confidence。

## 关键约束

- 原文未提及的字段必须填 `null`，**禁止编造**
- 每个非空字段必须 ≥ 1 个 evidence_id
- 多源冲突字段标 `field_status=conflicting`
- 长文本必须 chunk 后走 RAG，不一次塞 LLM

## 已知限制 / TODO

- v1：仅支持 collab_saas / crm_saas / cross_border_ecommerce_saas 三个行业扩展
- v1：定价表如果是图片形式无法抽取
- v2：增加 OCR / table extraction

## 责任窗口

**E 窗口**。M0 后开始，M1 完成 v1。
