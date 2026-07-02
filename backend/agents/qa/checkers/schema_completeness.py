"""schema_completeness：Profile 必填字段填充率。

规则（docs/QA.md § 3.3）：
- 每个 CompetitorProfile 必填字段填充率 ≥ 0.80
- 行业扩展字段填充率 ≥ 0.60
- ``field_status`` 中 ``unverified`` 占比 > 20% 触发 issue

routing：
- 必填字段缺失 → extractor（带 must_address 字段名）
- 多产品同字段都缺 → collector（提示该维度可能根本没采）
"""

from __future__ import annotations

from typing import ClassVar

from backend.schemas import CompetitorProfile, FieldStatus, QADimension, QAIssue

from ._base import BaseChecker, CheckerContext, CheckerResult

# 通用必填字段（点路径），不依赖具体行业
REQUIRED_FIELDS = (
    "basic_info.name",
    "basic_info.category",
    "basic_info.positioning",
    "basic_info.target_users",
    "features.core_features",
    "pricing.pricing_model",
    "pricing.plans",
    "user_feedback.overall_rating",
)

# 行业扩展（IndustryExtensionUnion）的能力字段：MaturityScore 子项
INDUSTRY_EXT_CAPABILITY_FIELDS = (
    "task_management",
    "kanban_view",
    "calendar_view",
    "gantt_view",
    "document_collaboration",
    "workflow_automation",
    "knowledge_base",
    "team_permission",
    "third_party_integration",
    "mobile_support",
    "realtime_editing",
    "ai_assistance",
)


class SchemaCompletenessChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.SCHEMA_COMPLETENESS

    REQUIRED_FILL_THRESHOLD = 0.80
    EXTENSION_FILL_THRESHOLD = 0.60
    UNVERIFIED_RATIO_THRESHOLD = 0.20
    OVERALL_PASS_THRESHOLD = 0.80

    def run(self, ctx: CheckerContext) -> CheckerResult:
        issues: list[QAIssue] = []
        required_scores: list[float] = []
        extension_scores: list[float] = []

        # 多产品同字段缺失统计：用于触发 collector routing
        missing_by_field: dict[str, set[str]] = {}

        for product_name, profile in ctx.profiles.items():
            req_fill, req_missing = _required_fill(profile)
            required_scores.append(req_fill)
            for f in req_missing:
                missing_by_field.setdefault(f, set()).add(product_name)

            if req_fill < self.REQUIRED_FILL_THRESHOLD:
                issues.append(
                    QAIssue(
                        issue_id=f"iss_sc_required_{_slug(product_name)}",
                        dimension=self.dimension,
                        severity="major" if req_fill < 0.6 else "minor",
                        location=f"profiles[{product_name!r}]",
                        problem=(
                            f"{product_name} 的必填字段填充率 {req_fill:.0%} "
                            f"低于阈值 {self.REQUIRED_FILL_THRESHOLD:.0%}；"
                            f"缺失：{sorted(req_missing)}"
                        ),
                        suggested_fix=(
                            "Extractor 重新抽取以补齐这些必填字段；"
                            "如果原始 source 中本就没有，回到 Collector 补采。"
                        ),
                        target_agent="extractor",
                        required_inputs={
                            "product": product_name,
                            "must_address": sorted(req_missing),
                        },
                    )
                )

            ext_fill = _extension_fill(profile)
            if ext_fill is not None:
                extension_scores.append(ext_fill)
                if ext_fill < self.EXTENSION_FILL_THRESHOLD:
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_sc_ext_{_slug(product_name)}",
                            dimension=self.dimension,
                            severity="minor",
                            location=(f"profiles[{product_name!r}].industry_extension"),
                            problem=(
                                f"{product_name} 的行业扩展字段填充率 "
                                f"{ext_fill:.0%} 低于阈值 "
                                f"{self.EXTENSION_FILL_THRESHOLD:.0%}。"
                            ),
                            suggested_fix=(
                                "Extractor 补齐行业扩展中缺失的 MaturityScore；"
                                "原始 source 不足以判断时回到 Collector 补采该维度。"
                            ),
                            target_agent="extractor",
                            required_inputs={
                                "product": product_name,
                            },
                        )
                    )

            # field_status 中 unverified 占比
            statuses = list(profile.field_status.values())
            if statuses:
                unverified_ratio = sum(1 for s in statuses if s is FieldStatus.UNVERIFIED) / len(
                    statuses
                )
                if unverified_ratio > self.UNVERIFIED_RATIO_THRESHOLD:
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_sc_unverified_{_slug(product_name)}",
                            dimension=self.dimension,
                            severity="major",
                            location=(f"profiles[{product_name!r}].field_status"),
                            problem=(
                                f"{product_name} 字段状态中 unverified 占比 "
                                f"{unverified_ratio:.0%} 超过阈值 "
                                f"{self.UNVERIFIED_RATIO_THRESHOLD:.0%}。"
                            ),
                            suggested_fix=(
                                "Extractor 复查这些 unverified 字段，能 verify 的补齐"
                                "原文 evidence_ids；不能 verify 的回 Collector 补采。"
                            ),
                            target_agent="extractor",
                            required_inputs={
                                "product": product_name,
                                "unverified_ratio": round(unverified_ratio, 3),
                            },
                        )
                    )

        # 多产品同字段缺失 → collector routing 用 critical issue 暴露
        if ctx.profiles:
            for field_, products_missing in sorted(missing_by_field.items()):
                if len(products_missing) >= max(2, len(ctx.profiles) // 2 + 1):
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_sc_field_gap_{_slug(field_)}",
                            dimension=self.dimension,
                            severity="critical",
                            location=f"profiles.*[{field_}]",
                            problem=(
                                f"字段 {field_!r} 在 "
                                f"{len(products_missing)}/{len(ctx.profiles)} 个产品"
                                "中均缺失，怀疑原始数据未采集。"
                            ),
                            suggested_fix=(
                                "Collector 针对相关产品/维度补采来源，"
                                "Extractor 二次抽取后再交给 Analyst。"
                            ),
                            target_agent="collector",
                            required_inputs={
                                "field": field_,
                                "products_missing": sorted(products_missing),
                            },
                        )
                    )

        # 评分
        if required_scores:
            req_avg = sum(required_scores) / len(required_scores)
        else:
            req_avg = 1.0
        if extension_scores:
            ext_avg = sum(extension_scores) / len(extension_scores)
        else:
            ext_avg = 1.0
        # 必填权重更高
        score = 0.7 * req_avg + 0.3 * ext_avg
        if any(i.severity == "critical" for i in issues):
            score = min(score, 0.55)

        pass_ = (
            req_avg >= self.REQUIRED_FILL_THRESHOLD
            and ext_avg >= self.EXTENSION_FILL_THRESHOLD
            and not any(i.severity in ("major", "critical") for i in issues)
        )
        notes = (
            f"必填均值 {req_avg:.0%}，行业扩展均值 {ext_avg:.0%}，覆盖 {len(ctx.profiles)} 个产品。"
        )
        return CheckerResult(
            dimension=self.dimension,
            score=round(score, 3),
            pass_=pass_,
            notes=notes,
            issues=issues,
        )


def _required_fill(profile: CompetitorProfile) -> tuple[float, list[str]]:
    """返回 (填充率, 缺失字段路径列表)。"""
    filled = 0
    missing: list[str] = []
    for path in REQUIRED_FIELDS:
        value = _dig(profile, path)
        if _is_filled(value):
            filled += 1
        else:
            missing.append(path)
    return filled / len(REQUIRED_FIELDS), missing


def _extension_fill(profile: CompetitorProfile) -> float | None:
    """行业扩展字段填充率，无 industry_extension 时返回 None。"""
    ext = profile.industry_extension
    if ext is None:
        return None
    filled = 0
    total = 0
    for f in INDUSTRY_EXT_CAPABILITY_FIELDS:
        if not hasattr(ext, f):
            continue
        total += 1
        if getattr(ext, f) is not None:
            filled += 1
    if total == 0:
        return None
    return filled / total


def _dig(obj: object, path: str) -> object:
    cur: object = obj
    for part in path.split("."):
        if cur is None:
            return None
        cur = getattr(cur, part, None)
    return cur


def _is_filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    return True


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in text.strip().lower())
    return out.strip("_") or "x"


__all__ = ["SchemaCompletenessChecker"]
