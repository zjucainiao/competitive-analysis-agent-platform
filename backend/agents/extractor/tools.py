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
from typing import Any, ClassVar

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
    """source_quote 反向定位的结果。

    matched=True 时 ``matched_text`` **必须**是源文档 raw_text 的逐字切片
    （证据原文承诺），且 ``location.char_start/char_end`` 与之精确对应。
    ``match_type`` 区分命中级别：exact（规范化后逐字命中）/ fuzzy（n-gram
    相似窗口命中）/ none（未命中）。
    """

    source_id: str | None
    matched_text: str | None
    location: EvidenceLocation
    confidence: float
    matched: bool
    match_type: str = "none"  # "exact" | "fuzzy" | "none"


_QUOTE_TRANSLATION = {"“": '"', "”": '"', "’": "'"}


def _normalize_with_map(s: str) -> tuple[str, list[int]]:
    """规范化文本并返回「规范化位置 → 原文位置」映射。

    规范化规则与 :func:`_normalize_for_match` 完全一致（单一实现来源）：
    统一引号、连续空白折叠成单空格、去首尾空白。
    ``mapping[i]`` 是规范化文本第 i 个字符在原文中的下标，用于把规范化
    坐标精确映射回原文，替代旧的 12 字符锚点近似（空白不一致时会切错）。
    """
    out_chars: list[str] = []
    mapping: list[int] = []
    prev_space = True  # 视作开头已有空白 → 直接吞掉前导空白（等价 lstrip）
    for i, ch in enumerate(s):
        ch = _QUOTE_TRANSLATION.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            out_chars.append(" ")
            mapping.append(i)
            prev_space = True
        else:
            out_chars.append(ch)
            mapping.append(i)
            prev_space = False
    # 等价 rstrip：去掉折叠后残留的尾部单空格
    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        mapping.pop()
    return "".join(out_chars), mapping


def _normalize_for_match(s: str) -> str:
    """规范化文本：去多余空白、统一引号。仅用于匹配，不用于落库。"""
    return _normalize_with_map(s)[0]


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
    1. **精确 substring**（规范化后比对；用规范化映射把命中区间精确映射回原文）
    2. **Sliding-window n-gram Jaccard**（≥ ``fuzzy_threshold`` 视为命中）
    3. 都不命中 → matched=False，调用方需把 source_quote 加入 unmatched_quotes

    「证据原文逐字」不变量：只要 matched=True，``matched_text`` 一定是原文
    raw_text 的逐字切片，``location.char_start/char_end`` 与之精确对应——
    fuzzy 命中返回的是命中的**原文窗口**（LLM 的转述 quote 绝不透出），
    且 confidence 被 :attr:`FUZZY_CONFIDENCE_CAP` 封顶，诚实低于精确命中的 1.0。
    """

    # fuzzy 命中的置信上限：n-gram 集合相同但语序不同也可能打满 Jaccard，
    # 但它终究不是逐字定位到 quote 本身，置信必须与精确命中（1.0）拉开差距。
    FUZZY_CONFIDENCE_CAP: ClassVar[float] = 0.85

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
        # 每个 source 的规范化文本 + 位置映射只算一次，精确/模糊两阶段共用
        normalized: list[tuple[RawSourceDoc, str, str, list[int]]] = []
        for src in sources:
            text = src.raw_text or ""
            norm_text, mapping = _normalize_with_map(text)
            normalized.append((src, text, norm_text, mapping))

        # 1. 精确 substring：规范化命中后用映射取回原文精确区间
        for src, text, norm_text, mapping in normalized:
            idx = norm_text.lower().find(norm_quote.lower())
            if idx < 0:
                continue
            start, end = self._map_to_original(mapping, idx, idx + len(norm_quote))
            return LinkResult(
                source_id=src.source_id,
                matched_text=text[start:end],
                location=EvidenceLocation(char_start=start, char_end=end),
                confidence=1.0,
                matched=True,
                match_type="exact",
            )

        # 2. fuzzy（n-gram Jaccard sliding window）
        quote_grams = _ngrams(norm_quote)
        best_score = 0.0
        best_hit: tuple[RawSourceDoc, str, str, list[int], int] | None = None
        win_size = max(len(norm_quote), 40)
        for src, text, norm_text, mapping in normalized:
            for i in range(0, max(1, len(norm_text) - win_size + 1), self.window_step):
                window = norm_text[i : i + win_size]
                score = _jaccard(quote_grams, _ngrams(window))
                if score > best_score:
                    best_score = score
                    best_hit = (src, text, norm_text, mapping, i)
        if best_score >= self.fuzzy_threshold and best_hit is not None:
            src, text, norm_text, mapping, win_start = best_hit
            win_end = min(win_start + win_size, len(norm_text))
            # 掐掉窗口两端的空格再映射，保证原文切片首尾都是实字符
            while win_start < win_end and norm_text[win_start] == " ":
                win_start += 1
            while win_end > win_start and norm_text[win_end - 1] == " ":
                win_end -= 1
            start, end = self._map_to_original(mapping, win_start, win_end)
            # 证据内容 = 命中的原文窗口（逐字），绝不是 LLM 的 source_quote
            return LinkResult(
                source_id=src.source_id,
                matched_text=text[start:end],
                location=EvidenceLocation(char_start=start, char_end=end),
                confidence=min(best_score, self.FUZZY_CONFIDENCE_CAP),
                matched=True,
                match_type="fuzzy",
            )
        return LinkResult(
            source_id=None,
            matched_text=None,
            location=EvidenceLocation(),
            confidence=best_score,
            matched=False,
        )

    @staticmethod
    def _map_to_original(mapping: list[int], norm_start: int, norm_end: int) -> tuple[int, int]:
        """把规范化区间 [norm_start, norm_end) 映射回原文区间 [start, end)。

        mapping[i] 是规范化第 i 个字符的原文下标；区间末字符取其原文下标 +1。
        调用方保证区间非空且首尾是非空白字符（规范化 quote 已 strip、
        fuzzy 窗口已掐边），因此切片首尾必为原文实字符。
        """
        start = mapping[norm_start]
        end = mapping[norm_end - 1] + 1
        return start, end


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
    raise ValueError(f"cannot coerce LLM response to {model.__name__}: {type(resp).__name__}")


# ---------- Evidence ID / hash helpers ----------


def evidence_id_for(content: str, source_id: str, salt: str = "") -> str:
    raw = f"{source_id}|{salt}|{content}".encode()
    return "ev_" + hashlib.sha1(raw).hexdigest()[:12]


def content_hash_for(content: str) -> str:
    return "h_" + hashlib.sha1(content.encode()).hexdigest()[:16]


__all__ = [
    "Chunk",
    "EvidenceLinker",
    "LinkResult",
    "TextChunker",
    "coerce_pydantic",
    "content_hash_for",
    "evidence_id_for",
    "render",
    "split_prompt",
]
