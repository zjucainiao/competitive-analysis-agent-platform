"""freshness：引用 evidence 的时效性。

规则（docs/QA.md § 3.5）：
- **age 来源优先级**：``source_published_at``（源文档发布时间）→ ``collected_at``
  （仅采集时间）。后者只反映"什么时候抓的"，不反映"内容多老"，所以仅当
  fallback 且降权为中性分。
- 敏感字段（定价 / 版本号 / 功能等）引用的 evidence 不能超过
  ``SENSITIVE_MAX_DAYS``
- 普通字段超过 ``GENERAL_MAX_DAYS`` 提示一下

每条引用的"新鲜度贡献"分档：
- 有 source_published_at 且在窗口内 → 1.0
- 有 source_published_at 但 stale → 0.0（并开 issue）
- 无 source_published_at（仅有采集时间）→ ``NEUTRAL_NO_DATE_SCORE``
  （默认 0.7，含义："不知道老不老，无法满分"）

routing：
- 敏感字段 stale → collector（重新采集该 dimension）
- 普通字段 stale → reporter（在报告中加日期标注，minor 级）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from backend.schemas import Evidence, QADimension, QAIssue, ReportParagraph

from ._base import BaseChecker, CheckerContext, CheckerResult

SENSITIVE_SECTION_KEYWORDS = ("pricing", "price", "定价", "价格", "version", "changelog")


class FreshnessChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.FRESHNESS

    SENSITIVE_MAX_DAYS = 90
    GENERAL_MAX_DAYS = 365
    OVERALL_PASS_THRESHOLD = 0.85
    # 无 source_published_at 时单条 evidence 的"中性"分；
    # 让全无日期的报告维持在 0.7 左右，而不是误报 1.0。
    NEUTRAL_NO_DATE_SCORE = 0.7

    def run(self, ctx: CheckerContext) -> CheckerResult:
        issues: list[QAIssue] = []
        now = ctx.now
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        contributions: list[float] = []
        dated_count = 0
        undated_count = 0
        max_age_days = 0
        sensitive_violations = 0

        for sec_idx, section in enumerate(ctx.draft.sections):
            sensitive = _section_is_sensitive(section.section_id, section.title)
            for para_idx, para in enumerate(section.paragraphs):
                if not para.evidence_ids:
                    continue
                stale_ids: list[str] = []
                undated_ids: list[str] = []
                for eid in para.evidence_ids:
                    ev = ctx.evidence_db.get(eid)
                    if ev is None:
                        # evidence 缺失由 evidence_completeness/fact 处理
                        continue
                    published = _published_at(ev)
                    if published is None:
                        undated_count += 1
                        undated_ids.append(eid)
                        contributions.append(self.NEUTRAL_NO_DATE_SCORE)
                        continue
                    age_days = _days_between(published, now)
                    dated_count += 1
                    max_age_days = max(max_age_days, age_days)
                    limit = (
                        self.SENSITIVE_MAX_DAYS
                        if sensitive or para.is_quantitative
                        else self.GENERAL_MAX_DAYS
                    )
                    if age_days <= limit:
                        contributions.append(1.0)
                    else:
                        contributions.append(0.0)
                        stale_ids.append(eid)

                if stale_ids:
                    is_sensitive = sensitive or para.is_quantitative
                    if is_sensitive:
                        sensitive_violations += 1
                        severity = "major"
                        target = "collector"
                        fix = (
                            f"敏感字段 evidence 超过 {self.SENSITIVE_MAX_DAYS} 天，"
                            "Collector 重新采集该维度的最新来源。"
                        )
                    else:
                        severity = "minor"
                        target = "reporter"
                        fix = (
                            f"该段落引用 evidence 超过 {self.GENERAL_MAX_DAYS} 天，"
                            "在段落末尾标注 '数据采集于 YYYY-MM'。"
                        )
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_fr_{para.paragraph_id}",
                            dimension=self.dimension,
                            severity=severity,  # type: ignore[arg-type]
                            location=(
                                f"report.sections[{sec_idx}].paragraphs[{para_idx}]"
                            ),
                            problem=(
                                f"段落引用 {len(stale_ids)} 条过期 evidence："
                                f"{stale_ids[:3]}"
                                + ("…" if len(stale_ids) > 3 else "")
                            ),
                            suggested_fix=fix,
                            target_agent=target,  # type: ignore[arg-type]
                            required_inputs={
                                "paragraph_id": para.paragraph_id,
                                "stale_evidence_ids": stale_ids,
                                "max_age_days": (
                                    self.SENSITIVE_MAX_DAYS
                                    if is_sensitive
                                    else self.GENERAL_MAX_DAYS
                                ),
                            },
                        )
                    )

        total_refs = dated_count + undated_count
        if total_refs == 0:
            score = 1.0
        else:
            score = sum(contributions) / total_refs
        if sensitive_violations >= 3:
            score = min(score, 0.6)
        pass_ = (
            score >= self.OVERALL_PASS_THRESHOLD
            and not any(i.severity in ("major", "critical") for i in issues)
        )
        notes = _build_notes(
            dated_count=dated_count,
            undated_count=undated_count,
            max_age_days=max_age_days,
            stale_issues=len([i for i in issues if "过期" in i.problem]),
        )
        return CheckerResult(
            dimension=self.dimension,
            score=round(score, 3),
            pass_=pass_,
            notes=notes,
            issues=issues,
        )


def _section_is_sensitive(section_id: str, title: str) -> bool:
    sid = section_id.lower()
    tt = title.lower()
    return any(k in sid or k in tt for k in SENSITIVE_SECTION_KEYWORDS)


def _published_at(ev: Evidence) -> datetime | None:
    """优先用 source_published_at；没有就返回 None（让 checker 走中性兜底）。

    刻意不退化到 collected_at——后者只是抓取时间戳，可能把 10 年前的
    旧博客判成"今天发布"。Collector 接入 publish_date 抽取前，
    这条路径就该给"无可靠日期"的中性信号。
    """
    published = ev.source_published_at
    if published is None:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published


def _days_between(past: datetime, now: datetime) -> int:
    return max(0, (now - past).days)


def _build_notes(
    *,
    dated_count: int,
    undated_count: int,
    max_age_days: int,
    stale_issues: int,
) -> str:
    total = dated_count + undated_count
    if total == 0:
        return "无可校验的 evidence 引用。"
    bits = [
        f"带日期 {dated_count}/{total}，无日期 {undated_count}/{total}",
    ]
    if dated_count:
        bits.append(f"最大年龄 {max_age_days} 天")
    if undated_count:
        bits.append(
            "无日期项按中性 0.7 计分（待 Collector 落 source_published_at 后回升）"
        )
    if stale_issues:
        bits.append(f"过期 {stale_issues} 处")
    return "；".join(bits) + "。"


# 段落定义引用，避免未使用警告
_ = ReportParagraph

__all__ = ["FreshnessChecker"]
