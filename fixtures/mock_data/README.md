# Mock Data Fixtures

> 协作办公场景 demo 数据集。**不要手改这里的 JSON**——它们从 `fixtures/build_mock_data.py` 自动生成，过 Pydantic 校验。

## 重建

```bash
python -m fixtures.build_mock_data
```

Schema 变更时重跑即可。

## 用途

让各 Agent **不必等彼此**就能开始开发：每个 Agent 都能直接 `json.load()` 自己的上游 mock 输入。

## 目录

```
mock_data/
├── projects/
│   └── collab_saas_demo.json       # Project (Notion vs ClickUp vs Asana)
│
├── raw_sources/                    # Collector 输出 / Extractor 输入
│   ├── notion/{homepage,pricing}.json
│   ├── clickup/{homepage,pricing}.json
│   └── asana/{homepage,pricing,user_reviews}.json
│
├── competitor_profiles/            # Extractor 输出 / Analyst 输入
│   ├── notion.json                 # CompetitorProfile（含 CollaborationSaasExtension）
│   ├── clickup.json
│   └── asana.json
│
├── analysis_results/               # Analyst 输出 / Reporter 输入
│   ├── analysis_full.json          # 完整 AnalysisResult
│   ├── feature_comparison.json     # 单维度 DimensionAnalysis
│   ├── pricing_comparison.json
│   └── swot.json
│
├── report_drafts/
│   └── draft_v1.json               # Reporter 输出 / QA 输入
│
├── qa_verdicts/
│   ├── pass.json                   # 全通过示例
│   └── needs_revision.json         # 含 issues + routing 的示例
│
├── evidences/
│   └── evidence_db.jsonl           # 10 条 Evidence
│
└── agent_outputs/                  # 完整 Agent Output 示例
    ├── collector__notion.json      # 含 confidence/self_critique/tokens/...
    ├── extractor__notion.json
    ├── analyst__full.json
    ├── reporter__v1.json
    ├── qa__pass.json
    └── qa__needs_revision.json
```

## 使用示例

### Extractor

```python
import json
from pathlib import Path
from backend.schemas import RawSourceDoc

raw = json.loads(Path("fixtures/mock_data/raw_sources/notion/pricing.json").read_text())
doc = RawSourceDoc.model_validate(raw)

# 用 doc.raw_text 作为 LLM 输入进行抽取测试
```

### Analyst

```python
from backend.schemas import CompetitorProfile

profiles = {
    name: CompetitorProfile.model_validate_json(
        Path(f"fixtures/mock_data/competitor_profiles/{name.lower()}.json").read_text()
    )
    for name in ["Notion", "ClickUp", "Asana"]
}
```

### Reporter / QA

类似上面，加载 `analysis_results/analysis_full.json` 或 `report_drafts/draft_v1.json`。

## 演示场景

- **Project**：「协作办公 SaaS 竞品分析 · Demo」
- **Target**：Notion
- **Competitors**：ClickUp、Asana
- **Industry**：`collaboration_saas`（使用 `CollaborationSaasExtension`）
- **Dimensions**：feature_comparison、pricing_comparison、swot、differentiation
- **真实抓取来源**：notion.so / clickup.com / asana.com / g2.com
- **QA 反馈闭环**：`qa_verdicts/needs_revision.json` 展示了一次真实的 routing → Reporter 流程

## 数据一致性保证

- 所有 fixture 通过 Pydantic `model_validate` 双向校验
- `agent_outputs/*.json` 内的 `evidence_ids` 与 `evidences/evidence_db.jsonl` 严格对齐
- `CompetitorProfile.industry_extension` 的 `industry_id` discriminator 正确解析
- 报告中每个事实性段落 `evidence_ids` 非空（演示引用强制）

Schema 字段变化时，重跑 `python -m fixtures.build_mock_data` 即可。
