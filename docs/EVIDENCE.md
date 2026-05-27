# 证据链与可溯源

> 本文档定义 Evidence 的生命周期、引用规则、溯源 UI 规范。证据链是平台可信度的基石。

---

## 1. 设计目标

报告中**每一个事实性结论**都能被追溯到至少一段原始来源文本。具体来说：

- 用户看到报告中的某句话 → 点击 → 跳转到对应的 Evidence 卡片 → 看到原文 + URL + 抓取时间
- 用户点击"打开原文" → 跳转到 source_url
- 修改 / 删除某条 Evidence → 自动定位到所有引用它的 claim / 段落，并触发重新质检

---

## 2. Evidence 生命周期

```
[Collector]
  采集 RawSourceDoc（HTML / 正文）
        │
        ▼
[Extractor]
  正文切片 (chunker) → 候选 chunks
        │
        ▼
  按 Schema 字段抽取，每个字段记录 source_quote
        │
        ▼
  source_quote 反向匹配回 chunk → 生成 Evidence（含 evidence_id）
        │
        ▼
  Evidence 写入向量库 + 关系库
        │
        ▼
[Analyst]
  reasoning 时，通过 RAG 查询 evidence_id
  每个 claim 的 evidence_ids ⊆ 已入库的 evidence
        │
        ▼
[Reporter]
  组装段落时引用 claim → 展开 → 段落的 evidence_ids
        │
        ▼
[QA]
  对每个段落做 entailment_check(text, evidence_set)
  失败 → 路由回上游
        │
        ▼
[前端]
  用户 hover 段落 → 高亮显示证据
  用户点击 evidence_id → 展开证据卡片
```

---

## 3. Evidence 数据模型

完整定义见 [SCHEMA.md](SCHEMA.md) § 5。关键字段：

| 字段 | 用途 |
|---|---|
| `evidence_id` | 全局唯一，所有引用都用这个 |
| `source_url` | 原始网页 URL |
| `content` | 证据原文片段（核心字段） |
| `context_before / after` | 上下文，便于人类阅读 |
| `location.char_start/end` | 在原文中的字符偏移 |
| `source_authority` | 来源权威度（官方 0.95，UGC 0.6） |
| `collected_at` | 抓取时间，用于时效性判断 |
| `embedding_id` | 向量库主键 |
| `content_hash` | 去重 |

---

## 4. 切片与入库

### 4.1 切片策略

```python
# backend/tools/chunker.py
def chunk_text(text: str, max_tokens: int = 400, overlap: int = 50) -> list[Chunk]:
    """按段落优先，长段落切到 max_tokens，相邻 chunk 有 overlap。"""
```

- 优先按段落 / 列表项切
- 单段超过 `max_tokens` 时按句子切
- 保留 `overlap` 让边界处不丢上下文
- 记录每个 chunk 在原文中的 `char_start / char_end`

### 4.2 去重

- 同一段文本（content_hash 相同）只入库一次
- 多个 source_url 引用同一段文本 → Evidence 的 source_url 改为 list

### 4.3 向量化

- 使用 multi-lingual embedding 模型（中英混合）
- 默认 `bge-m3` 或 `text-embedding-3-small`
- 通过 `LLMProvider.embed()` 统一调用，便于切换

### 4.4 关系库映射

PostgreSQL 表：

```sql
CREATE TABLE evidences (
  evidence_id      text PRIMARY KEY,
  source_id        text REFERENCES raw_sources(source_id),
  product_name     text,
  source_url       text,
  content          text,
  content_hash     text,
  location         jsonb,
  source_authority real,
  language         text,
  collected_at     timestamptz,
  embedding_id     text,
  tags             text[],
  created_at       timestamptz DEFAULT now()
);
CREATE INDEX ON evidences (product_name);
CREATE INDEX ON evidences (content_hash);
```

---

## 5. 引用规则

### 5.1 哪些字段必须有 evidence

| 数据 | 字段 | 强制 |
|---|---|---|
| CompetitorProfile.basic_info | name / official_website / category | ✅ |
| CompetitorProfile.features | core_features 中每个 feature.name | ✅ |
| CompetitorProfile.pricing.plans | 每个 plan.name / price | ✅ |
| AnalysisClaim | `evidence_ids` 列表 | ✅（≥1，否则拒绝） |
| ReportParagraph | `evidence_ids` 列表 | ✅（非软性结论段落） |

### 5.2 软性结论例外

模糊性结论（含"可能"、"通常"、"或许"等词）允许 `evidence_ids` 为空，但：
- 必须在 `self_critique` 中说明
- QA 会用更宽松的 entailment 阈值

### 5.3 反例证据

`AnalysisClaim.counter_evidence_ids` 是体现严谨的关键字段：

> 例：claim = "ClickUp 对中小团队更友好"  
> evidence_ids = ["ev_001", "ev_002"]（正面评价）  
> counter_evidence_ids = ["ev_055"]（一篇说复杂的反例）

QA 在审查时会特别加分。

---

## 6. 引用展开规则

报告段落引用 claim，claim 又引用 evidence。**展开方向**：

```
ReportParagraph.evidence_ids = ∪ AnalysisClaim.evidence_ids (for claim in claim_ids)
                             ∪ ReportParagraph 自身额外补充的 evidence
```

Reporter 在生成段落时：
1. 先选要引用哪些 claim
2. 自动展开为 evidence_ids 集合
3. 段落 text 中不强制要求脚标 [1][2]，但前端 hover 时自动高亮

---

## 7. 溯源 UI 规范

### 7.1 报告查看页

```
┌──────────────────────────────────────────────────┐
│ 报告 · v3 final · 2026-05-27                    │
├──────────────────────────────────────────────────┤
│ ## 3. 定价策略对比                                │
│                                                  │
│ Notion 提供 Free / Plus / Business / Enterprise │
│ 四档定价，Plus 起售价 $10/seat/月。 [3 evidence]│  ← hover 高亮，点击展开
│                                                  │
│ ClickUp 与 Notion 相似但价格更低，Unlimited 档  │
│ $7/seat/月，覆盖大部分常用功能。 [4 evidence]   │
└──────────────────────────────────────────────────┘
```

- **hover 段落** → 段落底色变浅黄，显示 `[N evidence]` 计数
- **点击 [N evidence]** → 右侧抽屉滑出 evidence 卡片列表
- **每张卡片** 显示：来源 URL、抓取时间、authority 评分、原文（含 context_before/after）、跳转原文按钮
- **多产品对比** 时，可按产品切换 evidence 视图

### 7.2 Evidence 卡片

```
┌──────────────────────────────────────────────────┐
│ ev_a8f2  · Notion · pricing_page · authority 0.95│
├──────────────────────────────────────────────────┤
│ ... Plus is $10/seat/month, billed annually.    │
│ It includes unlimited blocks for teams,         │
│ unlimited file uploads, and 30 day page history.│
│                                                  │
│ [打开原文 →] [复制] [标记不准确]                │
│                                                  │
│ 抓取于 2026-05-26 14:32                          │
└──────────────────────────────────────────────────┘
```

### 7.3 反向追溯

- 在 Evidence 库页面，每条 Evidence 显示"被引用 X 次"
- 点击 → 列出所有引用此 Evidence 的报告段落

---

## 8. 人工介入

- **标记不准确**：用户标记 Evidence 不准确 → Evidence 状态置 `disputed` → 触发 QA 重审引用该 Evidence 的所有段落
- **手动添加**：用户可以手动添加 Evidence（用于补充自有资料）
- **手动编辑段落**：用户编辑后，段落 `is_user_edited=True`，并计入"人工修正率"指标

---

## 9. 时效性

每条 Evidence 带 `collected_at`。Profile 字段绑定 Evidence 时同步存最早的 `collected_at`。

- 超过 6 个月：标记 `freshness=stale`，QA 会提示
- 超过 1 年：标记 `freshness=expired`，建议重新采集
- 定价 / 版本号 / 功能等高变动字段，阈值更严（3 个月 stale，6 个月 expired）

---

## 10. 实现位置

```
backend/
├── schemas/evidence.py         # Evidence / EvidenceLocation
├── storage/evidence_store.py   # PG + Chroma 双写 + RAG retrieve
├── tools/chunker.py
├── tools/evidence_linker.py    # source_quote → Evidence 匹配
└── api/routers/evidences.py    # REST: GET /evidences/{id}, search, etc.
```
