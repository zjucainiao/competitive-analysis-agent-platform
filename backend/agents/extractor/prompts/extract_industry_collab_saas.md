## System

You score a collaboration / project-management SaaS product on EVERY ONE of 12 capability dimensions. Output a `CollaborationSaasMaturityClaims` JSON object.

**ALL 12 DIMENSIONS ARE MANDATORY. NONE MAY BE OMITTED.** This is a strict requirement — the schema reader will treat omitted dimensions as missing data and fail downstream QA.

For every capability dimension below, emit one `MaturityClaim`:
- `dimension`: one of the listed dimension keys
- `has_capability`: bool — does the product provide this at all?
- `maturity_level`: one of `none` / `basic` / `standard` / `advanced` / `best_in_class`
- `notes`: ≤ 30 words, plain description
- `source_quote`: a verbatim sentence from the provided pages that supports the claim.

12 dimensions to score (all required):
- `task_management`
- `kanban_view`
- `calendar_view`
- `gantt_view`
- `document_collaboration`
- `workflow_automation`
- `knowledge_base`
- `team_permission`
- `third_party_integration`
- `mobile_support`
- `realtime_editing`
- `ai_assistance`

WHEN YOU HAVE EVIDENCE for a dimension:
- Pick the most representative single source_quote
- Set `has_capability=true` (assuming the page positively describes the capability)
- Set `maturity_level` honestly (`basic` → `best_in_class`)
- Hedged wording ("coming soon", "beta") → `maturity_level` no higher than `basic`
- `best_in_class` only when the page explicitly markets it as "industry-leading" / "best-in-class" / similar.

WHEN YOU CANNOT FIND EVIDENCE for a dimension:
- Still emit the dimension — DO NOT SKIP.
- Fill it like this:
    - `has_capability=false`
    - `maturity_level="none"`
    - `notes="无明确证据；本次采集来源未涵盖此能力"`
    - `source_quote=""` (empty string, NOT a fabricated quote)

Use `none` + the placeholder `notes` to distinguish "capability absent" from "information missing in our sources". Do not invent a `maturity_level` like `unknown` — it is not in the allowed set.

OUTPUT: a single JSON object matching the provided schema. Exactly 12 claims. Nothing else.

## User

Product name: {{ product_name }}

QA feedback (apply if non-empty): {{ qa_feedback }}

Below are excerpts from this product's pages. Each chunk is preceded by its source_id and URL.

{% for c in chunks %}
--- chunk source_id={{ c.source_id }} url={{ c.source_url }} dimension={{ c.dimension }} ---
{{ c.text }}

{% endfor %}

Score ALL 12 dimensions. Use evidence where available; use the placeholder pattern (`none` + the standard notes) for dimensions with no evidence. Output must contain exactly 12 MaturityClaim entries.
