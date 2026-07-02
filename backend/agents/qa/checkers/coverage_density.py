"""coverage_density：报告信息密度 / 章节覆盖。

动机（docs/QA.md § 3.7）：
报告"偏薄"不应该只靠模板的全局字数下限来兜——那样会逼模型注水。真正该被
质检拦住的是：**Analyst 明明产出了某维度的 claim（且有 evidence 支撑），
Reporter 却把该章节写成占位 / 软结论一句话**，导致信息密度过低。这类问题
归属 Reporter（它有料没渲染），与 evidence_completeness 的"整维度 evidence
缺失 → collector"是互补关系，不重叠。

设计原则（与产品讨论一致）：
- **不查总字数**，按"已选维度 + claim 丰富度"动态判定密度。
- 维度 claim 少（甚至 1 条）→ 章节短是诚实的短，不罚。
- 维度有 N 条**带 evidence**的 claim，但报告章节 0 个实质段落 → major → reporter。
- 维度有较多 claim，但实质段落远少于 claim 数（密度过低）→ minor → reporter。
- 若维度的 claim 全都没有 evidence → 跳过（那是上游问题，evidence_completeness
  已按 critical 路由到 collector，不重复责怪 reporter）。

实质段落定义：``not is_soft_conclusion and text.strip()``——占位段
（``*_placeholder`` / ``*_fallback``）和软结论天然被排除。
"""

from __future__ import annotations

from typing import ClassVar

from backend.schemas import AnalysisDimension, QADimension, QAIssue

from ._base import BaseChecker, CheckerContext, CheckerResult
from .evidence_completeness import _section_dimension

# 多 claim 维度判"密度偏低"的阈值
THIN_CLAIM_FLOOR = 3  # claim 达到此数才评估密度（少于此数短属正常）
THIN_DENSITY_RATIO = 0.5  # 实质段落 / 带证据 claim 数 低于此值 → 偏薄
OVERALL_PASS_THRESHOLD = 0.80


class CoverageDensityChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.COVERAGE_DENSITY

    def run(self, ctx: CheckerContext) -> CheckerResult:
        issues: list[QAIssue] = []

        # dimension -> 该维度绑定的报告章节（按 reporter 命名约定反推）
        section_by_dim: dict[AnalysisDimension, tuple[int, object]] = {}
        for sec_idx, section in enumerate(ctx.draft.sections):
            sec_dim = _section_dimension(section.section_id, section.title)
            if sec_dim is not None and sec_dim not in section_by_dim:
                section_by_dim[sec_dim] = (sec_idx, section)

        ratios: list[float] = []
        for dim, dim_obj in ctx.analysis.dimensions.items():
            claims = dim_obj.claims
            if not claims:
                continue
            # 只评估"reporter 本可以渲染"的维度：至少有一条 claim 带 evidence。
            # 全无 evidence 的维度交给 evidence_completeness 路由到上游。
            claims_with_ev = [c for c in claims if c.evidence_ids]
            if not claims_with_ev:
                continue

            located = section_by_dim.get(dim)
            if located is None:
                # 维度有内容但模板里没有对应章节（自适应模板裁掉了）——
                # 不在本 checker 误报，交给模板/规划层。
                continue
            sec_idx, section = located
            substantive = [
                p
                for p in section.paragraphs  # type: ignore[attr-defined]
                if not p.is_soft_conclusion and p.text.strip()
            ]
            n_sub = len(substantive)
            n_claims = len(claims_with_ev)

            if n_sub == 0:
                # 有料没渲染：章节只剩占位/软结论
                ratios.append(0.0)
                issues.append(
                    QAIssue(
                        issue_id=f"iss_cd_empty_{section.section_id}",  # type: ignore[attr-defined]
                        dimension=self.dimension,
                        severity="major",
                        location=f"report.sections[{sec_idx}]",
                        problem=(
                            f"维度 {dim.value!r} 有 {n_claims} 条带证据的分析结论，"
                            f"但报告章节 {section.title!r} 没有任何实质段落"  # type: ignore[attr-defined]
                            "（只有占位 / 软结论），信息密度过低。"
                        ),
                        suggested_fix=(
                            "Reporter 需要把该维度可用的 claim 逐条展开为带 "
                            "evidence_ids 的事实段落（原则上每条 claim 一段），"
                            "不要折叠成单句占位。"
                        ),
                        target_agent="reporter",
                        required_inputs={
                            "section_id": section.section_id,  # type: ignore[attr-defined]
                            "dimension": dim.value,
                            "claims_available": n_claims,
                            "substantive_paragraphs": n_sub,
                        },
                    )
                )
                continue

            ratio = min(1.0, n_sub / n_claims)
            ratios.append(ratio)
            if n_claims >= THIN_CLAIM_FLOOR and ratio < THIN_DENSITY_RATIO:
                issues.append(
                    QAIssue(
                        issue_id=f"iss_cd_thin_{section.section_id}",  # type: ignore[attr-defined]
                        dimension=self.dimension,
                        severity="minor",
                        location=f"report.sections[{sec_idx}]",
                        problem=(
                            f"维度 {dim.value!r} 有 {n_claims} 条带证据结论，"
                            f"报告仅展开 {n_sub} 段实质内容，信息密度偏低。"
                        ),
                        suggested_fix=(
                            "建议每条带 evidence 的 claim 展开为一段（核心发现 + "
                            "证据依据 + 对目标产品的含义），而非合并复述。"
                            "无新证据时不要注水。"
                        ),
                        target_agent="reporter",
                        required_inputs={
                            "section_id": section.section_id,  # type: ignore[attr-defined]
                            "dimension": dim.value,
                            "claims_available": n_claims,
                            "substantive_paragraphs": n_sub,
                        },
                    )
                )

        score = 1.0 if not ratios else round(sum(ratios) / len(ratios), 3)
        pass_ = score >= OVERALL_PASS_THRESHOLD and not any(
            i.severity in ("major", "critical") for i in issues
        )
        notes = (
            "无可评估维度（均无带证据 claim）。"
            if not ratios
            else f"信息密度均值 {score:.0%}（实质段落 / 带证据 claim）。"
        )
        return CheckerResult(
            dimension=self.dimension,
            score=score,
            pass_=pass_,
            notes=notes,
            issues=issues,
        )


__all__ = ["CoverageDensityChecker"]
