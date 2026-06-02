"""Tools 层 —— Agent 间共享的工具能力。

v1 公共能力：
- ``sanitizer``：PII 脱敏（写 trace / report / evidence 前必过）

Collector 自用工具（search / scrape / robots / rate_limiter）暂留在
``backend.agents.collector.tools``；后续迁出 robots + rate_limiter 时
落在 ``backend.tools.compliance``。
"""

from __future__ import annotations

from .sanitizer import (
    DEFAULT_PII_PATTERNS,
    PIIPattern,
    SanitizationStats,
    Sanitizer,
    sanitize,
    sanitize_with_stats,
)

__all__ = [
    "DEFAULT_PII_PATTERNS",
    "PIIPattern",
    "SanitizationStats",
    "Sanitizer",
    "sanitize",
    "sanitize_with_stats",
]
