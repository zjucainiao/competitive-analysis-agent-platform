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
  source_quote 的 content_hash 反向匹配回 chunk → 生成 Evidence（含 evidence_id）
  （见 extractor/agent.py::_mint_evidence；无向量库，纯 content_hash 关联）
        │
        ▼
  Evidence 随 ExtractorOutput 落 state_store（关系库 / localdb）
        │
        ▼
[Analyst]
  reasoning 时直接消费上游传入的 evidence（非 RAG / 非向量检索）
  每个 claim 的 evidence_ids ⊆ 已抽取的 evidence
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
| `content_hash` | 去重 + Evidence 与 chunk 的关联键（见 §4） |
| `embedding_id` | 预留字段：声明但 v1 未使用（无向量库），始终为 None（`evidence.py:87`） |
| `detected_product_name` | 内容检测出的产品名；供 `identity_consistency` 拦截「抓错产品」（`evidence.py:95`） |
| `identity_confidence` | 该证据确属 `product_name` 的置信度 0-1（`evidence.py:99`） |
| `identity_status` | 身份校验结论 `unvalidated/confirmed/mismatch/ambiguous`（`evidence.py:105`） |
| `source_class` | 来源粗分类 `official/review/other`，从 RawSourceDoc 继承（`evidence.py:109`） |

---

## 4. 切片与入库

### 4.1 切片策略

```python
# backend/agents/extractor/tools.py
class TextChunker:
    def __init__(self, max_chars: int = 1200, overlap: int = 100) -> None: ...
    def chunk(self, source: RawSourceDoc) -> list[Chunk]:
        """按段落优先，长段落按句切；用 char（非 token）计长。"""
```

- 优先按段落 / 列表项切
- 单段超过 `max_chars` 时按句子切
- 保留 `overlap` 让边界处不丢上下文
- 记录每个 chunk 在原文中的 `char_start / char_end`
- v1 用 char 计长（400 char ≈ 100 token），不引入额外 tokenizer 依赖

### 4.2 去重

- 同一段文本（content_hash 相同）只入库一次
- 多个 source_url 引用同一段文本 → Evidence 的 source_url 改为 list

### 4.3 向量化（v1 未实现）

v1 **不做向量化、不建向量库、不走 RAG**。Evidence 与原文 chunk 的关联完全靠
`content_hash` 反向匹配（见 §4.2 与 `extractor/agent.py::_mint_evidence`）。

- `LLMProvider.embed()` 是抽象基类上的占位方法，默认实现 `raise NotImplementedError`
  （`backend/agents/_base.py:527`、`collector/llm_providers.py:322`），生产路径不调用。
- `embedding_id` 字段声明但未消费，`chromadb` 在 `pyproject.toml` 中声明但全仓库无 `import`。
- 留作后续扩展位，不影响当前证据链。

### 4.4 持久化

v1 **没有专门的 `evidences` 表**。Evidence 作为 `ExtractorOutput.evidences` 的一部分，
随节点输出整体序列化为 JSON，落在 `node_outputs` 表的 `payload` 列
（DDL 见 `backend/storage/sql.py`，`CREATE TABLE node_outputs`）：

```sql
CREATE TABLE IF NOT EXISTS node_outputs (
  project_id  text,
  node_id     text,        -- e.g. 'extract.notion'
  run_id      text,
  agent_name  text,
  status      text,
  payload     jsonb,       -- 序列化后的 ExtractorOutput（内含 evidences[]）
  saved_at    timestamptz
);
```

- 读取：`PostgresStateStore.list_node_outputs(project_id)` 反序列化回 Pydantic
  （`backend/api/routes/evidence.py:92` 即按此遍历找 evidence）。
- 内存 / 本地模式（`MemoryStateStore`，`backend/storage/memory.py`）同样存整份 output，
  接口一致。

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

QA 的 `freshness` 维度按 Evidence 的 `source_published_at`（源文档发布时间，**非**
`collected_at` 抓取时间）判时效（见 docs/QA.md §3.5、`checkers/freshness.py`）：

- 定价 / 版本号 / 功能等敏感字段引用的证据：超过 `SENSITIVE_MAX_DAYS=90` 天 → 过期，
  开 issue 路由回 Collector 补采
- 一般字段：超过 `GENERAL_MAX_DAYS=365` 天 → 提示加日期标注（minor）
- `source_published_at` 为 None 的证据**不参与评分**（无日期 ≠ 过期）；全无日期则默认通过

> 没有持久化的 `freshness=stale/expired` 枚举字段，时效性是 QA 运行时按上述阈值计算的。

---

## 10. 实现位置

```
backend/
├── schemas/evidence.py              # Evidence / EvidenceLocation / RawSourceDoc
├── agents/extractor/
│   ├── tools.py                     # TextChunker.chunk() 切片 +
│   │                                #   EvidenceLinker.link() 把 source_quote 匹配回原文
│   └── agent.py                     # _mint_evidence() 生成 Evidence（content_hash 关联）
├── storage/{postgres,memory,sql}.py # Evidence 随 ExtractorOutput 落 node_outputs（无独立表）
└── api/routes/evidence.py           # PATCH /projects/{id}/evidence/{evidence_id}（标 disputed）
```

> 不存在 `storage/evidence_store.py`、`tools/chunker.py`、`tools/evidence_linker.py`：
> 切片 / 链接逻辑都在 `agents/extractor/`，无向量库、无 RAG retrieve。
> API 仅有上面这一个 `PATCH` 端点（dispute / auto_rework），没有 `GET /evidences/{id}` 或 search。
