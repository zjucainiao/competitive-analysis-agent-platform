"""evidence_completeness：证据完整性。

规则（docs/QA.md § 3.2）：
- 每个事实性 ReportParagraph 的 ``evidence_ids`` 必须非空（soft_conclusion 除外）
- 每个 AnalysisClaim 的 ``evidence_ids`` 至少 1 条
- 关键章节（功能对比 / 定价对比 / SWOT）的段落 evidence 覆盖率 ≥ 0.90

- 量化（高风险）段落若其引用证据**全部低权威**（source_authority < 阈值）→ 非阻塞提示

routing：
- 段落缺引用 → reporter
- claim 缺引用 → analyst
- 整个产品×维度的 evidence 全空 → collector
- 量化结论仅由低权威来源支撑 → collector（非阻塞，补高权威来源）
"""

from __future__ import annotations

from typing import ClassVar

from backend.agents._authority import ANALYSIS_TO_COLLECT, authority_for
from backend.schemas import AnalysisDimension, QADimension, QAIssue

from ._base import BaseChecker, CheckerContext, CheckerResult

KEY_DIMENSIONS = {
    AnalysisDimension.FEATURE_COMPARISON,
    AnalysisDimension.PRICING_COMPARISON,
    AnalysisDimension.SWOT,
}

# 「关键量化维度」：定价/功能的量化结论缺高权威源 → major（权重判级）；其余量化 → minor。
KEY_QUANT_DIMENSIONS = {
    AnalysisDimension.FEATURE_COMPARISON,
    AnalysisDimension.PRICING_COMPARISON,
}


class EvidenceCompletenessChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.EVIDENCE_COMPLETENESS

    KEY_COVERAGE_THRESHOLD = 0.90
    OVERALL_PASS_THRESHOLD = 0.90
    # 低权威阈值（消费 source_authority）：< 此值视为弱来源。相对语义下官方页查定价
    # =0.95、评论站查口碑=0.92 都不算弱；只有「其他第三方(0.5-0.6) / 评论站越界查定价
    # (0.6)」这类才落入弱来源。
    LOW_AUTHORITY_THRESHOLD = 0.7

    def run(self, ctx: CheckerContext) -> CheckerResult:
        issues: list[QAIssue] = []
        total_factual = 0
        cited_factual = 0

        # 1. 报告段落级
        for sec_idx, section in enumerate(ctx.draft.sections):
            for para_idx, para in enumerate(section.paragraphs):
                if para.is_soft_conclusion or not para.text.strip():
                    continue
                total_factual += 1
                location = f"report.sections[{sec_idx}].paragraphs[{para_idx}]"
                if not para.evidence_ids:
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_ec_para_{para.paragraph_id}",
                            dimension=self.dimension,
                            severity="major",
                            location=location,
                            problem=(
                                f"事实性段落 {para.paragraph_id!r} 的 evidence_ids 为空。"
                            ),
                            suggested_fix=(
                                "补充该段落引用的 evidence_ids，或将段落标记为 "
                                "is_soft_conclusion=True。"
                            ),
                            target_agent="reporter",
                            required_inputs={
                                "paragraph_id": para.paragraph_id,
                                "section_id": section.section_id,
                            },
                        )
                    )
                else:
                    cited_factual += 1

        # 2. AnalysisClaim 级
        for dim, dim_obj in ctx.analysis.dimensions.items():
            for c_idx, claim in enumerate(dim_obj.claims):
                if not claim.evidence_ids:
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_ec_claim_{claim.claim_id}",
                            dimension=self.dimension,
                            severity="major",
                            location=(
                                f"analysis.dimensions[{dim.value}].claims[{c_idx}]"
                            ),
                            problem=(
                                f"Analysis claim {claim.claim_id!r} 无 evidence_ids。"
                            ),
                            suggested_fix=(
                                "Analyst 需要为该 claim 补充至少 1 条 evidence_id，"
                                "若上游 profile 不含相关支撑则回到 Collector 重采。"
                            ),
                            target_agent="analyst",
                            required_inputs={
                                "claim_id": claim.claim_id,
                                "dimension": dim.value,
                            },
                        )
                    )

        # 3. 关键章节覆盖率
        for sec_idx, section in enumerate(ctx.draft.sections):
            section_dim = _section_dimension(section.section_id, section.title)
            if section_dim is None or section_dim not in KEY_DIMENSIONS:
                continue
            paras = [p for p in section.paragraphs if not p.is_soft_conclusion]
            if not paras:
                continue
            cited = sum(1 for p in paras if p.evidence_ids)
            coverage = cited / len(paras)
            if coverage < self.KEY_COVERAGE_THRESHOLD:
                issues.append(
                    QAIssue(
                        issue_id=f"iss_ec_coverage_{section.section_id}",
                        dimension=self.dimension,
                        severity="major",
                        location=f"report.sections[{sec_idx}]",
                        problem=(
                            f"关键章节 {section.title!r} 的段落 evidence 覆盖率 "
                            f"{coverage:.0%} 低于阈值 "
                            f"{self.KEY_COVERAGE_THRESHOLD:.0%}。"
                        ),
                        suggested_fix=(
                            "为该章节缺引用的段落补充 evidence_ids；"
                            "若多产品该维度 evidence 缺失，回到 Collector 重采。"
                        ),
                        target_agent="reporter",
                        required_inputs={
                            "section_id": section.section_id,
                            "current_coverage": round(coverage, 3),
                        },
                    )
                )

        # 4. 整维度 evidence 完全缺失 → collector
        for dim, dim_obj in ctx.analysis.dimensions.items():
            if not dim_obj.claims:
                continue
            all_evs = {e for c in dim_obj.claims for e in c.evidence_ids}
            if not all_evs:
                issues.append(
                    QAIssue(
                        issue_id=f"iss_ec_dim_empty_{dim.value}",
                        dimension=self.dimension,
                        severity="critical",
                        location=f"analysis.dimensions[{dim.value}]",
                        problem=(
                            f"维度 {dim.value!r} 的所有 claim 都没有 evidence_ids，"
                            "数据来源缺失。"
                        ),
                        suggested_fix=(
                            "Collector 重新采集与该维度相关的来源文档，"
                            "Extractor 重新抽取以补充 evidence。"
                        ),
                        target_agent="collector",
                        required_inputs={
                            "dimension": dim.value,
                            "competitors_involved": list(
                                {
                                    p
                                    for c in dim_obj.claims
                                    for p in c.products_involved
                                }
                            ),
                        },
                    )
                )

        # 5. 高风险量化结论的来源权威度（**消费** source_authority，相对语义 + 跨维度校正）。
        # 对每个量化段落按其**主题维度**重算引用证据的权威度（评论证据用到 pricing 段落不再
        # 按采集时的 0.92，而按 authority_for("review", PRICING)=0.6）；source_class/维度任一
        # 未知则**保守回退**到证据存的 source_authority（不校正）。多来源类型互证则豁免。
        # 仅做**检测**，issue 在评分后再 append——不让权威信号翻转本维度（引用覆盖率）的 pass_。
        weak_key, weak_other = self._weak_authority_quant(ctx)

        # ---- 评分（仅基于引用覆盖率 issue；权威 issue 不参与本维度判级）----
        if total_factual == 0:
            paragraph_score = 1.0
        else:
            paragraph_score = cited_factual / total_factual

        total_claims = sum(len(d.claims) for d in ctx.analysis.dimensions.values())
        claims_with_ev = sum(
            1
            for d in ctx.analysis.dimensions.values()
            for c in d.claims
            if c.evidence_ids
        )
        claim_score = 1.0 if total_claims == 0 else claims_with_ev / total_claims

        score = 0.5 * paragraph_score + 0.5 * claim_score
        # critical issue 把 score 拉到不及格
        if any(i.severity == "critical" for i in issues):
            score = min(score, 0.55)

        pass_ = score >= self.OVERALL_PASS_THRESHOLD and not any(
            i.severity in ("major", "critical") for i in issues
        )

        # 权威 issue 在 pass_ 之后 append：进全局判级（major 计权重、可与其它 issue 累计触发
        # 阻塞）但**不**翻转本维度 pass_、不经 core 路径强制阻塞。对抗评审一致结论：硬门槛在
        # 真实数据验证触发率前过激，故此处以**权重判级**落地（关键章节弱源=major，其余=minor），
        # 暂不升 hard_block（升级只需把 major 的 required_inputs 加 hard_block=True，见 routing）。
        if weak_key:
            issues.append(
                QAIssue(
                    issue_id="iss_ec_low_authority_key",
                    dimension=self.dimension,
                    severity="major",
                    location="report.dimension[evidence_completeness]",
                    problem=(
                        f"{len(weak_key)} 处定价/功能的量化结论仅由弱来源支撑"
                        f"（按主题维度重算权威度 < {self.LOW_AUTHORITY_THRESHOLD}），"
                        "关键数字缺高权威佐证。"
                    ),
                    suggested_fix="Collector 为这些定价/功能量化结论补采官方页等高权威来源。",
                    target_agent="collector",
                    required_inputs={
                        "paragraph_ids": weak_key,
                        "authority_threshold": self.LOW_AUTHORITY_THRESHOLD,
                    },
                )
            )
        if weak_other:
            issues.append(
                QAIssue(
                    issue_id="iss_ec_low_authority",
                    dimension=self.dimension,
                    severity="minor",
                    location="report.dimension[evidence_completeness]",
                    problem=(
                        f"{len(weak_other)} 处量化结论仅由低权威来源支撑，"
                        "关键数字可信度不足。"
                    ),
                    suggested_fix="Collector 为这些量化结论补采更高权威来源。",
                    target_agent="collector",
                    required_inputs={
                        "paragraph_ids": weak_other,
                        "authority_threshold": self.LOW_AUTHORITY_THRESHOLD,
                    },
                )
            )

        notes = (
            f"事实段落 {cited_factual}/{total_factual} 有引用；"
            f"claim {claims_with_ev}/{total_claims} 有 evidence。"
        )
        return CheckerResult(
            dimension=self.dimension,
            score=round(score, 3),
            pass_=pass_,
            notes=notes,
            issues=issues,
        )

    def _weak_authority_quant(
        self, ctx: CheckerContext
    ) -> tuple[list[str], list[str]]:
        """检测「量化结论仅由弱来源支撑」的段落（相对语义 + 跨维度校正）。

        返回 ``(weak_key, weak_other)``：weak_key=定价/功能关键量化段，weak_other=其余量化段。
        - 按段落主题维度（``_section_dimension`` → ``ANALYSIS_TO_COLLECT``）重算每条引用证据的
          corrected-authority = ``authority_for(source_class, collect_dim)``；``source_class`` /
          维度任一未知 → **保守回退**到 ``evidence.source_authority``（不校正）。
        - 多来源类型互证（≥2 个不同 ``source_class``）→ 豁免（可信度足够）。
        - 段落**所有**证据 corrected-authority < 阈值 → 弱源。
        """
        weak_key: list[str] = []
        weak_other: list[str] = []
        for sec in ctx.draft.sections:
            topic = _section_dimension(sec.section_id, sec.title)
            collect_dim = ANALYSIS_TO_COLLECT.get(topic) if topic else None
            for para in sec.paragraphs:
                if (
                    not para.is_quantitative
                    or para.is_soft_conclusion
                    or not para.evidence_ids
                ):
                    continue
                evs = [
                    ctx.evidence_db[e]
                    for e in para.evidence_ids
                    if e in ctx.evidence_db
                ]
                if not evs:
                    continue
                classes = {
                    e.source_class for e in evs if e.source_class is not None
                }
                if len(classes) >= 2:
                    continue  # 多来源类型互证 → 豁免
                corrected = [
                    authority_for(e.source_class, collect_dim)
                    if (e.source_class is not None and collect_dim is not None)
                    else e.source_authority
                    for e in evs
                ]
                if max(corrected) >= self.LOW_AUTHORITY_THRESHOLD:
                    continue  # 至少一条够权威
                if topic in KEY_QUANT_DIMENSIONS and collect_dim is not None:
                    weak_key.append(para.paragraph_id)
                else:
                    weak_other.append(para.paragraph_id)
        return weak_key, weak_other


def _section_dimension(section_id: str, title: str) -> AnalysisDimension | None:
    """从 section_id / title 推断对应的 AnalysisDimension。

    与 Reporter 的命名约定保持一致：section_id 形如 'sec_features' / 'sec_pricing'
    / 'sec_swot' / 'sec_diff' / 'sec_positioning' / 'sec_user_feedback'。
    标题里也常出现关键词，做次级匹配。
    """
    sid = section_id.lower()
    tt = title.lower()
    rules: list[tuple[tuple[str, ...], AnalysisDimension]] = [
        (("features", "feature", "功能"), AnalysisDimension.FEATURE_COMPARISON),
        (("pricing", "price", "定价", "价格"), AnalysisDimension.PRICING_COMPARISON),
        (("swot",), AnalysisDimension.SWOT),
        (("diff", "差异化", "机会"), AnalysisDimension.DIFFERENTIATION),
        (("position", "定位"), AnalysisDimension.POSITIONING),
        (("feedback", "review", "口碑", "用户"), AnalysisDimension.USER_FEEDBACK),
    ]
    for keys, dim in rules:
        if any(k in sid for k in keys) or any(k in tt for k in keys):
            return dim
    return None


__all__ = ["EvidenceCompletenessChecker"]
