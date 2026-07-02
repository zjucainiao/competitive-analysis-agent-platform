"""PII 脱敏 —— trace / report / evidence 写入前的统一过滤。

对应 docs/COMPLIANCE.md § 4.1（示例代码已外延为可配置的 Sanitizer 类）。

设计要点：
- 模式与替换 token 解耦：``PIIPattern(name, regex, replacement)``，方便
  审计阶段用 ``sanitize_with_stats()`` 看命中分布
- 默认替换 token 是 ``[REDACTED]``，可按字段类型差异化（如 ``[REDACTED:EMAIL]``）
- 不修改原文长度时尽量保留原始上下文（行号 / 段落不变）—— 用单一 token，
  长度差异由调用方接受
- 路由模式：``redact_label=True`` 时 token 带类型标签，便于人工 review

线程安全：编译后的正则是只读的；Sanitizer 实例可被多线程共享。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from re import Pattern

# ---------- 模式定义 ----------


@dataclass(frozen=True)
class PIIPattern:
    """单条 PII 规则。

    - ``name``：用于统计 + 日志（"email" / "phone_cn" / ...）
    - ``regex``：编译后的 Pattern；构造时用 ``compile(...)`` 包装
    - ``replacement``：命中时替换为这个字符串；默认 ``[REDACTED]``
    """

    name: str
    regex: Pattern[str]
    replacement: str = "[REDACTED]"


def _c(p: str, flags: int = 0) -> Pattern[str]:
    return re.compile(p, flags)


# 默认 PII 模式 ——
# 顺序敏感：先匹配更具特征的 ID 类（信用卡 / 身份证 / SSN），
# 再匹配电话 / 邮箱，避免电话正则误吞 SSN 等。
DEFAULT_PII_PATTERNS: tuple[PIIPattern, ...] = (
    PIIPattern(
        name="email",
        regex=_c(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
    # 信用卡：13-19 位数字，允许空格 / 短横线分组（Luhn 不在这一层做，
    # 这里宽松匹配，宁可多脱敏）
    PIIPattern(
        name="credit_card",
        regex=_c(r"\b(?:\d[ \-]?){12,18}\d\b"),
    ),
    # 美国 SSN：3-2-4 格式
    PIIPattern(
        name="ssn",
        regex=_c(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    # 中国身份证 18 位（末位可能是 X）
    PIIPattern(
        name="id_card_cn",
        regex=_c(r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"),
    ),
    # 中国手机号：1 开头 11 位，第二位 3-9
    PIIPattern(
        name="phone_cn",
        regex=_c(r"(?<!\d)(?:\+?86[\s\-]?)?1[3-9]\d{9}(?!\d)"),
    ),
    # 北美电话：(xxx) xxx-xxxx / xxx-xxx-xxxx / +1 xxx xxx xxxx
    PIIPattern(
        name="phone_us",
        regex=_c(
            r"(?<!\d)(?:\+?1[\s\-\.]?)?"
            r"(?:\(\d{3}\)|\d{3})"
            r"[\s\-\.]\d{3}[\s\-\.]\d{4}(?!\d)"
        ),
    ),
    # OpenAI 风格的 API key：sk-... 至少 20 个字符
    PIIPattern(
        name="api_key_openai",
        regex=_c(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    ),
    # 通用 Bearer / Authorization header value
    PIIPattern(
        name="bearer_token",
        regex=_c(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b", re.IGNORECASE),
    ),
)


# ---------- Sanitizer ----------


@dataclass
class SanitizationStats:
    """``sanitize_with_stats`` 的命中统计。"""

    hits_by_name: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def record(self, name: str, count: int = 1) -> None:
        self.hits_by_name[name] = self.hits_by_name.get(name, 0) + count
        self.total += count


class Sanitizer:
    """可配置的 PII 脱敏器。

    线程安全：实例无可变状态，可全局复用。

    用法::

        sanitizer = Sanitizer()
        clean = sanitizer.sanitize("contact me at alice@x.com")
        # "contact me at [REDACTED]"

        clean, stats = sanitizer.sanitize_with_stats("...")
        # stats.hits_by_name == {"email": 1}
    """

    def __init__(
        self,
        patterns: Iterable[PIIPattern] = DEFAULT_PII_PATTERNS,
        *,
        redact_label: bool = False,
    ) -> None:
        self._patterns: tuple[PIIPattern, ...] = tuple(patterns)
        self._redact_label = redact_label

    def sanitize(self, text: str) -> str:
        """脱敏后返回文本。``text`` 为空 / None 安全返回。"""
        if not text:
            return text
        for p in self._patterns:
            text = p.regex.sub(self._replacement_for(p), text)
        return text

    def sanitize_with_stats(self, text: str) -> tuple[str, SanitizationStats]:
        """返回 ``(脱敏文本, 命中统计)``。审计 / 日志用。"""
        stats = SanitizationStats()
        if not text:
            return text, stats
        for p in self._patterns:
            # 与 _name 同理：默认参数在定义时求值，把本轮的 replacement 一并固定，
            # 避免闭包晚绑定拿到后续迭代的值（B023）
            def _sub(
                _m: re.Match[str],
                _name: str = p.name,
                _repl: str = self._replacement_for(p),
            ) -> str:
                stats.record(_name)
                return _repl

            text = p.regex.sub(_sub, text)
        return text, stats

    def _replacement_for(self, p: PIIPattern) -> str:
        if self._redact_label:
            return f"[REDACTED:{p.name.upper()}]"
        return p.replacement


# ---------- 模块级便捷入口 ----------

_default = Sanitizer()


def sanitize(text: str) -> str:
    """默认 sanitizer 的快捷调用。"""
    return _default.sanitize(text)


def sanitize_with_stats(text: str) -> tuple[str, SanitizationStats]:
    return _default.sanitize_with_stats(text)


__all__ = [
    "DEFAULT_PII_PATTERNS",
    "PIIPattern",
    "SanitizationStats",
    "Sanitizer",
    "sanitize",
    "sanitize_with_stats",
]
