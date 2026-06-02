# `backend/tools` · I 窗口产出

Agent 间共享的工具能力。详细落地清单见 docs/COMPLIANCE.md § 4 / § 9。

## 已落地

### `sanitizer` · PII 脱敏

trace / report / evidence 写入前的统一过滤。覆盖：

- 邮箱、中美电话、中国身份证 18 位、信用卡（13-19 位带分隔符）、美国 SSN
- OpenAI / Anthropic 风格 API key、`Authorization: Bearer ...`

```python
from backend.tools import sanitize, sanitize_with_stats, Sanitizer

clean = sanitize("contact me at alice@example.com")
# "contact me at [REDACTED]"

clean, stats = sanitize_with_stats("a@b.com 13812345678")
# stats.hits_by_name == {"email": 1, "phone_cn": 1}

s = Sanitizer(redact_label=True)
s.sanitize("a@b.com")     # "[REDACTED:EMAIL]"
```

线程安全：`Sanitizer` 实例无可变状态，可全局复用。

调用点：
- `backend/observability/tracer.py`：OTLPSpan 写 attribute 前过 sanitize
- 推荐 Reporter / API 用户上传链路写库前调用

## 计划落地（其他窗口/迁移）

按 docs/COMPLIANCE.md § 9：

| 模块 | 当前位置 | 计划 |
|---|---|---|
| `robots_checker` | `backend/agents/collector/tools.py` | 迁到 `backend/tools/compliance.py` |
| `rate_limiter` | `backend/agents/collector/tools.py` | 同上 |
| `JSONSchemaValidator` | — | 落 `backend/tools/validator.py` |
| `CitationParser` | — | 落 `backend/tools/citation.py` |
| `EvidenceRetriever` | — | 落 `backend/tools/retriever.py`（Chroma） |
