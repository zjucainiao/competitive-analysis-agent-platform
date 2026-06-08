"""identity_consistency：产品身份一致性。

拦截「抓错产品」——报告/分析引用的证据，其内容是否真的属于它标注的
``product_name``。证据的 ``identity_status`` 由 Collector 在抓取后判定、Extractor
继承到 Evidence（详见 docs/QA.md § 3.8 与 collector/agent.py 的 ``_assess_identity``）。

规则：
- 只看**被报告/分析引用到**的证据（未被引用的脏数据不影响成稿，不强行返工）。
- ``identity_status == "mismatch"``（确属别的产品）→ major issue，路由回 collector，
  并在 ``required_inputs.mismatch_source_urls`` 带上跑题来源 URL，供重采时排除（P4）。
- ``identity_status == "ambiguous"``（提到目标产品但无法确证）→ minor issue，仅浮出，
  不让该维度失败（``pass_`` 只由 mismatch 决定），避免在「对比页/第三方站」上空转返工。

routing：mismatch / ambiguous 一律回 collector（抓错产品的根因在采集层选错了源，
extractor 只是忠实抽取）。按 product 聚合，每个产品一条 issue，便于一次性排除该
产品名下所有跑题 URL。
"""
from __future__ import annotations

from typing import ClassVar

from backend.schemas import Evidence, QADimension, QAIssue

from ._base import BaseChecker, CheckerContext, CheckerResult


class IdentityConsistencyChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.IDENTITY_CONSISTENCY

    OVERALL_PASS_THRESHOLD = 0.90
    AMBIGUOUS_WEIGHT = 0.4  # ambiguous 对 score 的折算权重（mismatch 记 1.0）

    def run(self, ctx: CheckerContext) -> CheckerResult:
        cited_ids = _cited_evidence_ids(ctx)
        cited = [ctx.evidence_db[e] for e in cited_ids if e in ctx.evidence_db]
        total = len(cited)

        mismatch = [e for e in cited if e.identity_status == "mismatch"]
        ambiguous = [e for e in cited if e.identity_status == "ambiguous"]

        issues: list[QAIssue] = []
        # mismatch：按产品聚合 → major，回 collector，带 exclude URLs
        for product, evs in _group_by_product(mismatch).items():
            urls = sorted({str(e.source_url) for e in evs})
            detected = sorted({e.detected_product_name for e in evs if e.detected_product_name})
            issues.append(
                QAIssue(
                    issue_id=f"iss_identity_mismatch_{_slug(product)}",
                    dimension=self.dimension,
                    severity="major",
                    location=f"evidence[product={product}]",
                    problem=(
                        f"产品 {product!r} 引用了 {len(evs)} 条**别的产品**的证据"
                        + (f"（疑似 {', '.join(detected)}）" if detected else "")
                        + "，属抓错产品。"
                    ),
                    suggested_fix=(
                        "Collector 重新采集该产品的来源，排除下列跑题 URL；"
                        "确认页面主讲对象就是目标产品。"
                    ),
                    target_agent="collector",
                    required_inputs={
                        "product": product,
                        "mismatch_source_urls": urls,
                        "evidence_ids": sorted(e.evidence_id for e in evs),
                    },
                )
            )
        # ambiguous（且无 mismatch 覆盖该产品）：minor，仅浮出，不致失败
        mismatch_products = {e.product_name for e in mismatch}
        amb_by_product = {
            p: evs
            for p, evs in _group_by_product(ambiguous).items()
            if p not in mismatch_products
        }
        for product, evs in amb_by_product.items():
            urls = sorted({str(e.source_url) for e in evs})
            issues.append(
                QAIssue(
                    issue_id=f"iss_identity_ambiguous_{_slug(product)}",
                    dimension=self.dimension,
                    severity="minor",
                    location=f"evidence[product={product}]",
                    problem=(
                        f"产品 {product!r} 有 {len(evs)} 条证据无法确证属于该产品"
                        "（可能来自对比页/第三方站），建议人工复核。"
                    ),
                    suggested_fix=(
                        "Collector 优先采集目标产品官方/直接来源，减少身份存疑的引用。"
                    ),
                    target_agent="collector",
                    required_inputs={
                        "product": product,
                        "ambiguous_source_urls": urls,
                    },
                )
            )

        # ---- 评分 ----
        if total == 0:
            score = 1.0
        else:
            penalty = (len(mismatch) + self.AMBIGUOUS_WEIGHT * len(ambiguous)) / total
            score = max(0.0, 1.0 - penalty)
        # 只要有 mismatch（抓错产品）就判不及格 → 触发一轮 blocking 返工；
        # 纯 ambiguous 不致失败（避免在对比页上空转）。
        pass_ = len(mismatch) == 0 and score >= self.OVERALL_PASS_THRESHOLD

        notes = (
            f"被引用证据 {total} 条；mismatch {len(mismatch)}、ambiguous {len(ambiguous)}。"
        )
        return CheckerResult(
            dimension=self.dimension,
            score=round(score, 3),
            pass_=pass_,
            notes=notes,
            issues=issues,
        )


def _cited_evidence_ids(ctx: CheckerContext) -> set[str]:
    """报告段落 + 分析 claim 引用到的所有 evidence_id。"""
    out: set[str] = set()
    for section in ctx.draft.sections:
        for para in section.paragraphs:
            out.update(para.evidence_ids or [])
    for dim_obj in ctx.analysis.dimensions.values():
        for claim in dim_obj.claims:
            out.update(claim.evidence_ids or [])
    return out


def _group_by_product(evs: list[Evidence]) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = {}
    for e in evs:
        out.setdefault(e.product_name, []).append(e)
    return out


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s.strip().lower())[:40] or "x"


__all__ = ["IdentityConsistencyChecker"]
