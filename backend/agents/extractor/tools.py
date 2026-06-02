"""Extractor 工具层。

仅放纯函数 / 轻量类，便于 agent.py 复用与单元测试。
不依赖 LLMProvider；LLM 相关参数 / 调用在 agent.py 里处理。

主要包含：
- TextChunker：段落优先、按 token 上限切分的极简切片器
- EvidenceLinker：把 LLM 给出的 source_quote 反向定位到 raw_source 的字符区间
- 简易 Jinja2 子集（与 collector 同款）：渲染 prompts/*.md 模板
- _coerce_pydantic：把 LLM provider 返回值拉齐成 pydantic 实例
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from backend.schemas import EvidenceLocation, RawSourceDoc


# ---------- TextChunker ----------


@dataclass
class Chunk:
    """单段切片。char_start/end 指向 raw_text 中的字符偏移。"""

    text: str
    char_start: int
    char_end: int
    source_id: str
    section_hint: str | None = None


class TextChunker:
    """段落优先的极简切片器。

    切分规则：
    1. 先按双换行（段落）切，单段超长再按句号 / 中文句号切
    2. 每个 chunk 不超过 ``max_chars``；相邻 chunk 保留 ``overlap`` 字符
    3. 记录每段在原文中的字符偏移，方便后续 EvidenceLocation 填充

    v1 用 char 计而非 token，避免引入额外 tokenizer 依赖。400 char ≈ 100 token。
    """

    def __init__(self, max_chars: int = 1200, overlap: int = 100) -> None:
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk(self, source: RawSourceDoc) -> list[Chunk]:
        text = source.raw_text or ""
        if not text.strip():
            return []
        chunks: list[Chunk] = []
        # 段落 split：保留偏移
        paragraphs = self._split_paragraphs(text)
        for raw_para, start in paragraphs:
            if len(raw_para) <= self.max_chars:
                chunks.append(
                    Chunk(
                        text=raw_para,
                        char_start=start,
                        char_end=start + len(raw_para),
                        source_id=source.source_id,
                    )
                )
                continue
            # 长段落 → 按句切再合并
            for piece, sub_start in self._split_long(raw_para, start):
                chunks.append(
                    Chunk(
                        text=piece,
                        char_start=sub_start,
                        char_end=sub_start + len(piece),
                        source_id=source.source_id,
                    )
                )
        return chunks

    @staticmethod
    def _split_paragraphs(text: str) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        pos = 0
        for raw in re.split(r"\n\s*\n", text):
            # 找到 raw 在原文中真实位置（保留前导空白带来的偏移）
            idx = text.find(raw, pos)
            if idx < 0:
                idx = pos
            stripped = raw.strip()
            if stripped:
                # 把 idx 校正到 stripped 开头
                offset = raw.find(stripped)
                out.append((stripped, idx + max(offset, 0)))
            pos = idx + len(raw)
        return out

    def _split_long(self, paragraph: str, base_offset: int) -> list[tuple[str, int]]:
        sentences = re.split(r"(?<=[。.!?！？])\s+", paragraph)
        out: list[tuple[str, int]] = []
        buf = ""
        buf_start = base_offset
        cursor = base_offset
        for s in sentences:
            if not s:
                continue
            if not buf:
                buf = s
                buf_start = cursor
            elif len(buf) + 1 + len(s) <= self.max_chars:
                buf = buf + " " + s
            else:
                out.append((buf, buf_start))
                # overlap：把 buf 尾巴接到下一个 buf 头
                tail = buf[-self.overlap :] if self.overlap and len(buf) > self.overlap else ""
                buf = (tail + " " + s).strip() if tail else s
                buf_start = cursor - len(tail) if tail else cursor
            cursor += len(s) + 1
        if buf:
            out.append((buf, buf_start))
        return out


# ---------- EvidenceLinker ----------


@dataclass
class LinkResult:
    """source_quote 反向定位的结果。"""

    source_id: str | None
    matched_text: str | None
    location: EvidenceLocation
    confidence: float
    matched: bool


def _normalize_for_match(s: str) -> str:
    """规范化文本：去多余空白、统一引号。仅用于匹配，不用于落库。"""
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


def _ngrams(s: str, n: int = 3) -> set[str]:
    s = _normalize_for_match(s).lower()
    if len(s) <= n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class EvidenceLinker:
    """把 LLM 给出的 source_quote 反向定位到 raw_source 文本中。

    匹配链路（按优先级）：
    1. **精确 substring**（norm 后比对，对应原始 raw_text 的位置仍按 lower 命中）
    2. **Sliding-window n-gram Jaccard**（≥ ``fuzzy_threshold`` 视为命中）
    3. 都不命中 → matched=False，调用方需把 source_quote 加入 unmatched_quotes

    返回 ``LinkResult.location`` 总是带原文 char_start / char_end（精确命中才有）。
    fuzzy 命中时 location 标 page_section 但不带精确偏移，避免给出错误位置。
    """

    def __init__(
        self,
        *,
        fuzzy_threshold: float = 0.55,
        min_quote_len: int = 8,
        window_step: int = 20,
    ) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self.min_quote_len = min_quote_len
        self.window_step = window_step

    def link(self, quote: str, sources: list[RawSourceDoc]) -> LinkResult:
        if not quote or len(quote.strip()) < self.min_quote_len:
            return LinkResult(
                source_id=None,
                matched_text=None,
                location=EvidenceLocation(),
                confidence=0.0,
                matched=False,
            )
        norm_quote = _normalize_for_match(quote)
        # 1. 精确 substring
        for src in sources:
            text = src.raw_text or ""
            norm_text = _normalize_for_match(text)
            idx = norm_text.lower().find(norm_quote.lower())
            if idx >= 0:
                # 回到原文找 substring：用 norm_quote 在 lower 上找一次
                raw_idx = self._original_index(text, norm_quote)
                if raw_idx is not None:
                    return LinkResult(
                        source_id=src.source_id,
                        matched_text=text[raw_idx : raw_idx + len(norm_quote)],
                        location=EvidenceLocation(
                            char_start=raw_idx,
                            char_end=raw_idx + len(norm_quote),
                        ),
                        confidence=1.0,
                        matched=True,
                    )
        # 2. fuzzy（n-gram Jaccard sliding window）
        quote_grams = _ngrams(norm_quote)
        best: tuple[float, RawSourceDoc | None, str | None] = (0.0, None, None)
        win_size = max(len(norm_quote), 40)
        for src in sources:
            text = src.raw_text or ""
            norm_text = _normalize_for_match(text)
            for i in range(0, max(1, len(norm_text) - win_size + 1), self.window_step):
                window = norm_text[i : i + win_size]
                score = _jaccard(quote_grams, _ngrams(window))
                if score > best[0]:
                    best = (score, src, window)
        if best[0] >= self.fuzzy_threshold and best[1] is not None:
            return LinkResult(
                source_id=best[1].source_id,
                matched_text=best[2],
                location=EvidenceLocation(page_section=None),
                confidence=best[0],
                matched=True,
            )
        return LinkResult(
            source_id=None,
            matched_text=None,
            location=EvidenceLocation(),
            confidence=best[0],
            matched=False,
        )

    @staticmethod
    def _original_index(text: str, norm_needle: str) -> int | None:
        """在原文中找 norm_needle 第一次出现的位置。

        norm_needle 已经按 _normalize_for_match 处理；原文可能有多余空白 / 大小写差异。
        策略：先按 needle 中第一个非空 token 在 text 中检索锚点，
        然后用 normalized 比较窗口。简单可靠优于完美。
        """
        if not norm_needle:
            return None
        # 锚点：needle 前 12 个非空白 char 在 text 中的位置
        anchor = norm_needle[: min(len(norm_needle), 12)]
        # 在原文中按 lower 模糊匹配
        text_lower = text.lower()
        idx = text_lower.find(anchor.lower())
        if idx >= 0:
            return idx
        # 再试 needle 中间的 12 char（避开开头被换行打断）
        mid = len(norm_needle) // 2
        anchor2 = norm_needle[mid : mid + 12]
        idx = text_lower.find(anchor2.lower())
        if idx >= 0:
            return max(0, idx - mid)
        return None


# ---------- Prompt 渲染（极简 Jinja2 子集，与 collector 同款，但本地化） ----------


def split_prompt(prompt: str) -> tuple[str, str]:
    sys_marker = "## System"
    usr_marker = "## User"
    si = prompt.find(sys_marker)
    ui = prompt.find(usr_marker)
    if si < 0 or ui < 0 or ui < si:
        return prompt.strip(), ""
    system = prompt[si + len(sys_marker) : ui].strip()
    user = prompt[ui + len(usr_marker) :].strip()
    return system, user


def render(template: str, **vars: Any) -> str:
    for_block = re.compile(
        r"{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%}(.*?){%\s*endfor\s*%}",
        re.DOTALL,
    )

    def expand_for(match: re.Match[str]) -> str:
        var_name, iter_name, body = match.group(1), match.group(2), match.group(3)
        items = vars.get(iter_name) or []
        chunks: list[str] = []
        for item in items:
            chunks.append(_render_simple(body, {var_name: item, **vars}))
        return "".join(chunks)

    rendered = for_block.sub(expand_for, template)
    return _render_simple(rendered, vars)


def _render_simple(template: str, vars: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        value = _resolve(expr, vars)
        return "" if value is None else str(value)

    return re.sub(r"{{\s*(.+?)\s*}}", repl, template)


def _resolve(expr: str, vars: dict[str, Any]) -> Any:
    parts = expr.split(".")
    head = vars.get(parts[0])
    if head is None:
        return None
    cur: Any = head
    for p in parts[1:]:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            cur = getattr(cur, p, None)
        if cur is None:
            return None
    return cur


def coerce_pydantic(resp: Any, model: type[BaseModel]) -> Any:
    """把 LLM provider 返回值尽量转成 model 实例。兼容多 provider 形态。"""
    if isinstance(resp, model):
        return resp
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, model):
        return parsed
    if isinstance(parsed, dict):
        return model.model_validate(parsed)
    if isinstance(resp, dict):
        return model.model_validate(resp)
    if hasattr(resp, "model_dump"):
        return model.model_validate(resp.model_dump())
    raise ValueError(
        f"cannot coerce LLM response to {model.__name__}: {type(resp).__name__}"
    )


# ---------- Evidence ID / hash helpers ----------


def evidence_id_for(content: str, source_id: str, salt: str = "") -> str:
    raw = f"{source_id}|{salt}|{content}".encode()
    return "ev_" + hashlib.sha1(raw).hexdigest()[:12]


def content_hash_for(content: str) -> str:
    return "h_" + hashlib.sha1(content.encode()).hexdigest()[:16]


__all__ = [
    "Chunk",
    "TextChunker",
    "EvidenceLinker",
    "LinkResult",
    "split_prompt",
    "render",
    "coerce_pydantic",
    "evidence_id_for",
    "content_hash_for",
]
