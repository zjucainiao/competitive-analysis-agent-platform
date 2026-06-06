## System

{{ system }}

Section style guide:
{{ style }}

Audience: {{ target_audience }}

## User

Project: {{ project_name }}
Section: {{ section_id }} / {{ title }} / order={{ order }}
Bound AnalysisDimension: {{ dimension }}

Allowed claim_ids: {{ allowed_claim_ids }}
Allowed evidence_ids: {{ allowed_evidence_ids }}
Target paragraphs (density, not a padding quota): {{ min_paragraphs }}

写作深度要求（让章节"展开"而不是复述，但绝不注水）：
- 密度由 claim 数量决定：原则上**每条 claim 写一段独立的、有支撑的段落**。claim 多就自然写得多，claim 少（甚至只有 1 条）就写得短——不要为了凑长度把一条 claim 拆成多段重复，也不要把多条 claim 压成一段。
- 每个事实段落要"展开"成结构化内容，而不是把 claim 复述一句：① 这条发现是什么；② 它由引用的哪条 evidence 支撑、evidence 里怎么说的；③ 对目标产品/读者意味着什么。其中 ③ 若超出 evidence 字面能推出的范围，必须用 hedging 措辞（“可能 / 通常 / 倾向于”）并把该判断放进一个 ``is_soft_conclusion=True`` 段落，不要伪装成事实。
- 可在本章节末尾追加**最多 1 段** ``is_soft_conclusion=True`` 的小结，综合本维度的含义 / 机会 / 风险。该段可不带 evidence_ids，但**不得编造任何具体数字**。
- 宁缺毋滥：没有 evidence 支撑的事实不要写。章节短是因为该维度证据少，这是诚实的短，比注水可信。

Claims (JSON):
```json
{{ claims_json }}
```

Evidence excerpts (JSON, may be truncated):
```json
{{ evidences_json }}
```

Generate the ``ReportSection`` now.
