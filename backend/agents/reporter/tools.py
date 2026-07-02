"""Reporter 工具：禁用词、数字提取、Evidence 校验、Evidence 提供者。

引用强制（核心抑制幻觉）：
- ``BANNED_TERMS`` + ``find_banned_terms``：检测段落中的绝对化表述
- ``extract_quantities`` + ``quantity_supported``：把数字从段落里抽出来，
  并到 evidence 文本里做 ±5% 容差匹配
- ``EvidenceProvider`` Protocol：抽象 Evidence 查询接口，避免 Reporter
  直接耦合 Evidence 存储；mock 模式默认 ``FixtureEvidenceProvider``
  从 fixtures/mock_data/evidences/evidence_db.jsonl 加载
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from backend.schemas import Evidence

# ---------- 禁用词 ----------
#
# 报告中常见的绝对化/夸张表述。这些词触发后 Reporter 不会直接抛错（避免误伤
# evidence 原文里出现的中性词），而是：
# - 在 self_critique 中累计提醒
# - 段落 confidence 降低
# - 命中数 > 0 计入 metadata.banned_term_hits
#
# 模板可通过 ``banned_terms_extra`` 追加自定义禁用词。

BANNED_TERMS: tuple[str, ...] = (
    "行业唯一",
    "行业第一",
    "业内唯一",
    "绝对领先",
    "完美",
    "100% 领先",
    "最佳产品",
    "无可替代",
    "全网最佳",
    "毫无对手",
    "稳赢",
)


# 绝对化宣称模式：数字 / 「全网/所有」+ 强力修饰动词。
# QA 真实反例："98% 福布斯云100强企业信赖"。命中后计入 banned_term_hits。
#
# 这些模式独立于固定词表，因为单独看 "信赖" / "采用" / "100强" 都不算绝对化，
# 只有跟比例 / 总量 / 强力修饰组合在一起才有问题。
_SUPERLATIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 「98% xxx 信赖/选择/采用/认可」
    re.compile(r"\d{1,3}\s*%[^，。；！？\n]{0,25}(?:信赖|采用|选择|认可|青睐|选用)"),
    # 「100强企业 / 100强公司」
    re.compile(r"\d{2,}\s*强[^，。；！？\n]{0,15}(?:企业|公司|品牌|客户|机构)"),
    # 「全网 / 全行业 / 所有 + 都/均/皆 + 在用/选/信赖」
    re.compile(
        r"(?:全网|全行业|业界|所有)[^，。；！？\n]{0,15}(?:都|均|皆)[^，。；！？\n]{0,8}"
        r"(?:在用|选择|采用|信赖|青睐)"
    ),
    # 「X 家以上 + 公司/企业 + 信赖/采用」
    re.compile(
        r"\d{2,}\s*家(?:以上)?[^，。；！？\n]{0,8}(?:公司|企业|客户|品牌)"
        r"[^，。；！？\n]{0,8}(?:信赖|采用|选择)"
    ),
)


def find_superlative_claims(text: str) -> list[str]:
    """返回命中的绝对化宣称片段（按出现顺序，去重）。"""
    hits: list[str] = []
    for pat in _SUPERLATIVE_PATTERNS:
        for m in pat.finditer(text):
            phrase = m.group(0)
            if phrase not in hits:
                hits.append(phrase)
    return hits


def find_banned_terms(text: str, extra: Iterable[str] = ()) -> list[str]:
    """返回段落中命中的禁用词 + 绝对化宣称片段（去重，保持出现顺序）。

    包含三类：
    - 固定禁用词 ``BANNED_TERMS``
    - 模板追加禁用词 ``extra``
    - 绝对化宣称模式（数字 + 强力修饰动词）
    """
    seen: list[str] = []
    pool = list(BANNED_TERMS) + list(extra)
    for term in pool:
        if term in text and term not in seen:
            seen.append(term)
    for phrase in find_superlative_claims(text):
        if phrase not in seen:
            seen.append(phrase)
    return seen


# ---------- 数字提取 ----------
#
# 抽取段落中可能受 evidence 约束的数值表达：
# - 价格 ``$10`` / ``$24.99``
# - 百分比 ``35%`` / ``12.5%``
# - 版本号 ``v2.3``
# - 纯数字 ``100+`` / ``7``（>=2 位，避免误抓 "1 段"）
#
# 数字校验时统一转 float（百分比保留 % 语义、版本号保留 v 语义）作为 key。

_PRICE_RE = re.compile(r"\$\s*(\d{1,4}(?:\.\d{1,2})?)")
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,2})?)\s*%")
_VERSION_RE = re.compile(r"\bv(\d+(?:\.\d+){1,3})\b", re.IGNORECASE)
# 注意：lookahead/lookbehind 只阻断 ASCII 字母数字下划线点和 %。
# 不要用 \w —— Python 3 默认开 Unicode，中文字符也算 \w，
# 会导致 "17 个集成" 中的 "17" 被「个」阻断 → hallucination 漏网。
_PLAIN_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(\d{2,}(?:\.\d{1,2})?)\+?(?![A-Za-z0-9_.%])"
)


def extract_quantities(text: str) -> list[tuple[str, float]]:
    """返回 [(kind, value), ...]。kind ∈ {price, percent, version, number}。

    版本号的 value 是 "1.2" → 1.2；"1.2.3" → 1.2（取前两段做近似匹配）。
    去重：相同 (kind, value) 只保留一次。
    """

    out: list[tuple[str, float]] = []
    seen: set[tuple[str, float]] = set()

    def push(kind: str, val: float) -> None:
        key = (kind, round(val, 3))
        if key in seen:
            return
        seen.add(key)
        out.append((kind, val))

    for m in _PRICE_RE.finditer(text):
        try:
            push("price", float(m.group(1)))
        except ValueError:
            pass
    for m in _PERCENT_RE.finditer(text):
        try:
            push("percent", float(m.group(1)))
        except ValueError:
            pass
    for m in _VERSION_RE.finditer(text):
        head = m.group(1).split(".")
        try:
            push("version", float(".".join(head[:2])))
        except ValueError:
            pass
    # 纯数字最后扫，避免和价格/百分比/版本号 token 重复
    masked = _PRICE_RE.sub(" ", text)
    masked = _PERCENT_RE.sub(" ", masked)
    masked = _VERSION_RE.sub(" ", masked)
    for m in _PLAIN_NUMBER_RE.finditer(masked):
        try:
            push("number", float(m.group(1)))
        except ValueError:
            pass
    return out


def quantity_supported(
    kind: str, value: float, evidences: Iterable[Evidence], *, tolerance: float = 0.05
) -> bool:
    """检查 (kind, value) 是否在任一 evidence.content 内出现（±tolerance 容差）。

    匹配策略：
    - price: evidence 文本里抽出所有 $X / X dollar / 纯数字（>=1），比值差距 < tolerance
    - percent: 抽 X% 或 X percent
    - version: 抽 vX.Y / X.Y（前两位）
    - number: 抽所有 >=2 位数字
    """

    contents = [ev.content for ev in evidences]
    if not contents:
        return False
    haystack = " ".join(contents)

    if kind == "price":
        # evidence 中常出现 "$10" / "10 per seat" 等，对所有候选数做容差匹配
        candidates = _collect_numbers(haystack, prefer_price=True)
    elif kind == "percent":
        candidates = [
            float(m.group(1))
            for m in _PERCENT_RE.finditer(haystack)
            if _safe_float(m.group(1)) is not None
        ]
    elif kind == "version":
        candidates = []
        for m in _VERSION_RE.finditer(haystack):
            head = m.group(1).split(".")
            v = _safe_float(".".join(head[:2]))
            if v is not None:
                candidates.append(v)
        # 也允许裸 "2.3" 形式
        for m in re.finditer(r"\b(\d+\.\d+)\b", haystack):
            v = _safe_float(m.group(1))
            if v is not None:
                candidates.append(v)
    else:  # number
        candidates = _collect_numbers(haystack, prefer_price=False)

    if not candidates:
        return False
    for cand in candidates:
        if _within_tolerance(value, cand, tolerance):
            return True
    return False


def _safe_float(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _collect_numbers(haystack: str, *, prefer_price: bool) -> list[float]:
    """抽取所有候选数字。价格场景额外吃 "$X" 形式。"""
    out: list[float] = []
    if prefer_price:
        for m in _PRICE_RE.finditer(haystack):
            v = _safe_float(m.group(1))
            if v is not None:
                out.append(v)
    for m in re.finditer(
        r"(?<![A-Za-z0-9_.])(\d{1,4}(?:\.\d{1,2})?)(?![A-Za-z0-9_.%])", haystack
    ):
        v = _safe_float(m.group(1))
        if v is not None:
            out.append(v)
    return out


def _within_tolerance(a: float, b: float, tolerance: float) -> bool:
    if a == b:
        return True
    if a == 0 or b == 0:
        return abs(a - b) <= tolerance
    rel = abs(a - b) / max(abs(a), abs(b))
    return rel <= tolerance


# ---------- Evidence 提供者 ----------


@runtime_checkable
class EvidenceProvider(Protocol):
    """Evidence 查询的最小接口。

    Reporter 用它把 evidence_id 解析为 Evidence 对象（拿到 .content 才能做
    数字校验）。生产环境由 I 窗口的 EvidenceStore 实现；mock / 测试场景由
    ``FixtureEvidenceProvider`` 直接从 jsonl 加载。
    """

    def get_many(self, evidence_ids: Iterable[str]) -> dict[str, Evidence]:
        ...


class FixtureEvidenceProvider:
    """从 fixtures/mock_data/evidences/evidence_db.jsonl 加载 Evidence。

    单例式缓存：第一次 get_many 时读盘并建立 evidence_id -> Evidence 映射。
    """

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            repo_root = Path(__file__).resolve().parents[3]
            db_path = repo_root / "fixtures" / "mock_data" / "evidences" / "evidence_db.jsonl"
        self._path = db_path
        self._cache: dict[str, Evidence] | None = None

    def _load(self) -> dict[str, Evidence]:
        if self._cache is not None:
            return self._cache
        out: dict[str, Evidence] = {}
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    ev = Evidence.model_validate(obj)
                    out[ev.evidence_id] = ev
                except Exception:
                    # fixture 损坏不应阻塞 Reporter；测试会单独覆盖
                    continue
        self._cache = out
        return out

    def get_many(self, evidence_ids: Iterable[str]) -> dict[str, Evidence]:
        db = self._load()
        return {eid: db[eid] for eid in evidence_ids if eid in db}


class StaticEvidenceProvider:
    """显式注入 Evidence 字典的 Provider（测试用）。"""

    def __init__(self, evidences: dict[str, Evidence]):
        self._db = dict(evidences)

    def get_many(self, evidence_ids: Iterable[str]) -> dict[str, Evidence]:
        return {eid: self._db[eid] for eid in evidence_ids if eid in self._db}


__all__ = [
    "BANNED_TERMS",
    "EvidenceProvider",
    "FixtureEvidenceProvider",
    "StaticEvidenceProvider",
    "extract_quantities",
    "find_banned_terms",
    "quantity_supported",
]
