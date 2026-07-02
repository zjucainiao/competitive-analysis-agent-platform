"""Extractor mock 数据加载。

读取 `fixtures/mock_data/competitor_profiles/<product>.json` 与
`fixtures/mock_data/evidences/evidence_db.jsonl`，按产品名分别还原成
``CompetitorProfile`` 与 ``list[Evidence]``。

只在 ``Extractor(mock=True)`` 路径下使用。生产模式不读这些文件。
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.schemas import CompetitorProfile, Evidence

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MOCK_PROFILE_DIR = _REPO_ROOT / "fixtures" / "mock_data" / "competitor_profiles"
_MOCK_EVIDENCE_FILE = (
    _REPO_ROOT / "fixtures" / "mock_data" / "evidences" / "evidence_db.jsonl"
)


def _slug(product_name: str) -> str:
    """notion-ish → 'notion'。和 fixtures 里的文件名约定保持一致。"""
    return product_name.strip().lower().replace(" ", "_")


def load_mock_profile(product_name: str) -> CompetitorProfile | None:
    """读取一个产品的 mock CompetitorProfile。文件缺失返回 None。"""
    fp = _MOCK_PROFILE_DIR / f"{_slug(product_name)}.json"
    if not fp.exists():
        return None
    data = json.loads(fp.read_text(encoding="utf-8"))
    return CompetitorProfile.model_validate(data)


def load_mock_evidences(product_name: str) -> list[Evidence]:
    """从 evidence_db.jsonl 里挑出某产品的全部 Evidence。

    JSONL 中如果 product_name 不区分大小写，这里也容忍一下。
    """
    if not _MOCK_EVIDENCE_FILE.exists():
        return []
    target = product_name.strip().lower()
    out: list[Evidence] = []
    for line in _MOCK_EVIDENCE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("product_name", "").lower() != target:
            continue
        try:
            out.append(Evidence.model_validate(row))
        except Exception:
            # 容忍单条坏数据，不打断整体加载
            continue
    return out


def has_mock_for(product_name: str) -> bool:
    return (_MOCK_PROFILE_DIR / f"{_slug(product_name)}.json").exists()


__all__ = [
    "has_mock_for",
    "load_mock_evidences",
    "load_mock_profile",
]
