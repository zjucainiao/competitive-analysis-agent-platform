# `backend/tools`

Agent 间共享的工具能力。详细落地清单见 docs/COMPLIANCE.md § 4 / § 8。

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
