# Reporter Agent · 报告撰写

> 详细契约见 [docs/AGENTS.md § 6](../../../docs/AGENTS.md#6-reporter报告撰写-agent)。

## 职责

把 `AnalysisResult` 渲染为正式竞品分析报告（markdown 结构化）。**严格禁止引入未在 evidence / analysis 中出现的事实**。

## 输入 / 输出

- Input：`ReporterInput`（含 analysis + template_id + target_audience）
- Output：`ReporterOutput`（含 `draft: ReportDraft`）

## 支持的模板

- `standard_v1` 标准版（产品 PM 用）
- `investor_v1` 投资分析版
- `pm_v1` 产品规划版（突出差异化机会）
- 用户可自定义模板 → 走 `template_id` 注册

## 实现位置

```
backend/agents/reporter/
├── agent.py
├── prompts/
│   ├── system.md
│   ├── section_overview.md
│   ├── section_features.md
│   ├── section_pricing.md
│   ├── section_swot.md
│   ├── section_opportunities.md
│   └── source_disclaimer.md     # 数据来源声明（合规）
├── templates/
│   ├── standard_v1.yaml
│   ├── investor_v1.yaml
│   └── pm_v1.yaml
├── tools.py
├── README.md
└── tests/
```

## 关键约束（核心：引用强制）

- 每个事实性 `ReportParagraph.evidence_ids` 非空 → 否则抛 `MISSING_CITATION`
- 数字 / 价格 / 百分比 / 版本号必须能在 evidence 文本中找到（容差 ±5%）→ 否则抛 `UNVERIFIED_QUANTITY`
- 软结论（"可能"、"通常"）允许空但 self_critique 必填
- 禁用词列表：行业唯一 / 绝对领先 / 完美 / 100% / 最佳产品 / 无可替代 …
- 分章节生成 LLM，避免 context 过长
- 报告末尾自动追加"数据来源声明"

## 已知限制 / TODO

- v1：仅支持 markdown 输出，不支持 docx / PDF
- v1：图表（如对比矩阵图片）需前端渲染，本 Agent 仅出结构化数据
- v2：多模板风格学习

## 责任窗口

**R 窗口**。M0 后开始，M1 完成 v1。
