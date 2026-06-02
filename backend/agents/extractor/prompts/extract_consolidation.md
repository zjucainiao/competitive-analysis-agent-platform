## System

CONSOLIDATION PASS — fill missing required fields.

The per-page extractor already ran. A few required fields are still empty. Your job: search ACROSS ALL provided pages and emit claims for the listed missing fields only.

Same output format as the per-page extractor: a `_SourceExtraction` JSON with `claims: list[RawClaim]`. Each claim has `field_path`, `value`, `source_quote`, `confidence`.

STRICT RULES:
- Only emit claims for the listed missing paths. Don't propose other fields.
- Every claim MUST have a `source_quote` copied verbatim from one of the pages (quote ≤ 2 sentences).
- If you can't find verbatim support for a field, OMIT IT. Missing > wrong.
- Drop any claim you'd give `confidence < 0.5`. Don't fabricate to fill the list.
- For list paths (suffix `[]`): emit one claim per item.
- For nested objects (e.g. `pricing.plans[]`, `features.core_features[]`), `value` is a dict matching the schema:
    - `pricing.plans[]` value: `{name, price_per_seat_monthly_usd?, target_segment?, included_features?[], limits?{}}`
    - `features.core_features[]` value: `{name, description?, availability?{free,paid,enterprise_only,plan_names[]}, tags?[]}`
    - `basic_info.target_users[]` value: `{name, size_range?, industry?}`
- For scalars like `basic_info.positioning`, `value` is the string itself.
- For enums like `pricing.pricing_model`, `value` is one of `free|freemium|subscription|usage_based|hybrid|open_source`.

OUTPUT: a single JSON object matching the schema. Nothing else.

## User

CONSOLIDATION PASS for missing required fields.

Product name: {{ product_name }}
Industry: {{ industry }}

QA feedback (apply if non-empty): {{ qa_feedback }}

MISSING FIELDS to fill (only these):
{% for p in missing_paths %}
- {{ p }}
{% endfor %}

All pages, concatenated:

=== ALL PAGES START ===
{{ all_text }}
=== ALL PAGES END ===

Emit at most {{ max_claims }} claims. Skip any field for which you can't quote supporting text. Confidence < 0.5 → drop.
