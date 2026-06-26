"""间接 prompt injection 启发式检测（WI-1）。

定位：对 Collector 抓取的**不可信外部内容**做模式扫描。命中 → 给该来源 / evidence 打
``tainted`` 标记 + 降权 + trace 高亮，让 QA 的 identity/fact 维度据此提权。**不直接拦截**
（避免误杀正常含这些词的评测内容），最终判断交回 QA。

设计要点（对齐 ``sanitizer.py`` 风格）：
- 模式与权重解耦：``InjectionPattern(name, regex, weight)``。
- 模式做得**足够具体**以压低假阳性：仅 "ignore … previous … instructions" 这类**结构化**
  注入串才命中，正常含 "ignore" / "disregard" 的句子（"ignore the minor UI differences"）不命中。
- 纯函数、无 LLM、无网络、可单测。编译后的正则只读，``scan`` 线程安全。

阈值定位：``score >= TAINT_THRESHOLD`` 视为 tainted。单条强模式（指令覆盖 / chat 模板 token /
角色改写）即可越过阈值；多条叠加 score 上限 1.0。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Pattern

# tainted 判定阈值：score ≥ 此值即标记。单条强模式权重 ≥ 0.5，故单击即中。
TAINT_THRESHOLD = 0.5


@dataclass(frozen=True)
class InjectionPattern:
    """单条注入模式。

    - ``name``：统计 / trace 用（"override_instructions_en" 等）
    - ``regex``：编译后的 Pattern
    - ``weight``：命中贡献的分数（强信号高、弱信号低）
    """

    name: str
    regex: Pattern[str]
    weight: float = 0.5


def _c(p: str, flags: int = re.IGNORECASE) -> Pattern[str]:
    return re.compile(p, flags)


# 默认注入模式集。每条都要求**结构化**的注入意图，而非单个敏感词，以压低假阳性。
DEFAULT_INJECTION_PATTERNS: tuple[InjectionPattern, ...] = (
    # 指令覆盖（英文）："ignore/disregard/forget ... (all) previous/above ... instructions/prompt/context"
    InjectionPattern(
        name="override_instructions_en",
        regex=_c(
            r"\b(?:ignore|disregard|forget|override|do\s+not\s+follow)\b"
            r"[^.\n]{0,30}?"
            r"\b(?:previous|prior|above|earlier|preceding|all)\b"
            r"[^.\n]{0,30}?"
            r"\b(?:instruction|instructions|prompt|prompts|context|message|messages|rule|rules|direction|directions)\b"
        ),
        weight=0.6,
    ),
    # 指令覆盖（中文）："忽略/无视/忘记 ... 以上/之前/所有 ... 指令/提示/要求/设定"
    InjectionPattern(
        name="override_instructions_zh",
        regex=_c(
            r"(?:忽略|无视|忘记|不要遵守|不要理会|不用管|清空)"
            r"[^。\n]{0,20}?"
            r"(?:以上|上述|之前|此前|前面|先前|所有|全部)"
            r"[^。\n]{0,20}?"
            r"(?:指令|指示|提示|要求|命令|规则|设定|prompt)"
        ),
        weight=0.6,
    ),
    # 角色改写（英文）："you are now ..." / "act as a(n) ... assistant/ai/model"
    InjectionPattern(
        name="role_override_en",
        regex=_c(
            r"\byou\s+are\s+now\b"
            r"|\bact\s+as\s+(?:an?\s+)?(?:[a-z]+\s+){0,3}?(?:assistant|ai|model|chatbot|system|persona)\b"
        ),
        weight=0.5,
    ),
    # 角色改写（中文）："你现在是" / "从现在起/开始你（是）"
    InjectionPattern(
        name="role_override_zh",
        regex=_c(r"你现在是|从现在(?:起|开始)你"),
        weight=0.5,
    ),
    # 直指 system prompt / 越权
    InjectionPattern(
        name="system_prompt_ref",
        regex=_c(r"\bsystem\s+prompt\b|\boverride\s+(?:the\s+)?system\b|\bjailbreak\b"),
        weight=0.4,
    ),
    # chat 模板控制 token —— 正常网页正文几乎不会出现，强信号
    InjectionPattern(
        name="chat_template_token",
        regex=_c(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>"),
        weight=0.7,
    ),
    InjectionPattern(
        name="inst_token",
        regex=_c(r"\[/?INST\]|<<SYS>>"),
        weight=0.6,
    ),
)


@dataclass(frozen=True)
class InjectionVerdict:
    """``scan`` 的结果。

    - ``tainted``：是否疑似含注入（``score >= TAINT_THRESHOLD``）
    - ``matched_patterns``：命中的模式名（去重、排序）
    - ``score``：加权命中分，上限 1.0
    """

    tainted: bool
    matched_patterns: list[str]
    score: float


class InjectionGuard:
    """可配置的注入扫描器。无可变状态，可全局复用 / 多线程共享。"""

    def __init__(
        self,
        patterns: Iterable[InjectionPattern] = DEFAULT_INJECTION_PATTERNS,
        *,
        threshold: float = TAINT_THRESHOLD,
    ) -> None:
        self._patterns: tuple[InjectionPattern, ...] = tuple(patterns)
        self._threshold = threshold

    def scan(self, text: str) -> InjectionVerdict:
        if not text:
            return InjectionVerdict(tainted=False, matched_patterns=[], score=0.0)
        matched: dict[str, float] = {}
        for p in self._patterns:
            if p.regex.search(text):
                # 同名模式只计一次（多次命中不叠加，避免长文本被刷高）
                matched[p.name] = p.weight
        score = min(1.0, sum(matched.values()))
        return InjectionVerdict(
            tainted=score >= self._threshold,
            matched_patterns=sorted(matched),
            score=round(score, 3),
        )


# ---------- 模块级便捷入口 ----------

_default = InjectionGuard()


def scan(text: str) -> InjectionVerdict:
    """默认 guard 的快捷调用。"""
    return _default.scan(text)


__all__ = [
    "DEFAULT_INJECTION_PATTERNS",
    "InjectionGuard",
    "InjectionPattern",
    "InjectionVerdict",
    "TAINT_THRESHOLD",
    "scan",
]
