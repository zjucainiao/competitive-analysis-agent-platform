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
Minimum paragraphs: {{ min_paragraphs }}

Claims (JSON):
```json
{{ claims_json }}
```

Evidence excerpts (JSON, may be truncated):
```json
{{ evidences_json }}
```

Generate the ``ReportSection`` now.
