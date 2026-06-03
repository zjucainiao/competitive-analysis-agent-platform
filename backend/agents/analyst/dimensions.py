"""单维度对比分析器。

每个 analyzer 从 `dict[product_name, CompetitorProfile]` 出发，产出符合
契约的 `DimensionAnalysis`：每条 AnalysisClaim 都从输入 profile 的
`evidence_refs` 中挑出 evidence_ids 绑定，杜绝幻觉。

LLM 不在此处介入，便于：
- mock 模式直接复用
- 真实模式下 LLM 失败 / 不可用时作为 fallback
- 单元测试无需 LLM 注入

具体维度规则见 docs/AGENTS.md § 5 与 docs/HALLUCINATION_CONTROL.md § 3.2。
"""

from __future__ import annotations

from collections.abc import Iterable

from backend.schemas import (
    AnalysisClaim,
    AnalysisDimension,
    CompetitorProfile,
    DimensionAnalysis,
    PricingPlan,
)

# 协作 SaaS 行业扩展中频繁用到的成熟度字段
_MATURITY_FIELDS: tuple[str, ...] = (
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

# 成熟度排序：用于比较两个 MaturityScore 谁更强
_MATURITY_RANK: dict[str, int] = {
    "none": 0,
    "basic": 1,
    "standard": 2,
    "advanced": 3,
    "best_in_class": 4,
}

# 维度对应的可读标签（写进 claim 文本）
_FIELD_LABEL_ZH: dict[str, str] = {
    "task_management": "任务管理",
    "kanban_view": "看板视图",
    "calendar_view": "日历视图",
    "gantt_view": "甘特视图",
    "document_collaboration": "文档协作",
    "workflow_automation": "工作流自动化",
    "knowledge_base": "知识库",
    "team_permission": "团队权限",
    "third_party_integration": "三方集成",
    "mobile_support": "移动端支持",
    "realtime_editing": "实时编辑",
    "ai_assistance": "AI 辅助",
}


# ---------- Evidence 池构造 ----------


def collect_profile_evidence_ids(profile: CompetitorProfile) -> set[str]:
    """汇总 profile 内所有 evidence_ids，构成该产品的"合法引用池"。

    Analyst 产出的每条 claim 的 evidence_ids 必须落在所有参与产品的并集池内，
    否则视为幻觉（见 docs/HALLUCINATION_CONTROL.md § 3.2）。
    """
    ids: set[str] = set()
    for vs in profile.basic_info.evidence_refs.values():
        ids.update(vs)
    for vs in profile.features.evidence_refs.values():
        ids.update(vs)
    for vs in profile.pricing.evidence_refs.values():
        ids.update(vs)
    uf = profile.user_feedback
    for vs in uf.evidence_refs.values():
        ids.update(vs)
    for theme in (*uf.positive_themes, *uf.negative_themes):
        ids.update(theme.evidence_ids)
    for pain in uf.user_pain_points:
        ids.update(pain.evidence_ids)
    for review in uf.typical_reviews:
        ids.add(review.evidence_id)
    comp = profile.competitive
    for bucket in (
        comp.strengths,
        comp.weaknesses,
        comp.opportunities,
        comp.threats,
        comp.recommendations,
    ):
        for insight in bucket:
            ids.update(insight.evidence_ids)
    ext = profile.industry_extension
    if ext is not None:
        for field in _MATURITY_FIELDS:
            ms = getattr(ext, field, None)
            if ms is not None:
                ids.update(ms.evidence_ids)
        for vs in getattr(ext, "evidence_refs", {}).values():
            ids.update(vs)
    return ids


# ---------- evidence 选择器 ----------


def _dedup(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _pricing_evidence(profile: CompetitorProfile) -> list[str]:
    refs = profile.pricing.evidence_refs
    return _dedup(refs.get("plans", []) + refs.get("pricing_model", []))


def _feature_evidence(profile: CompetitorProfile) -> list[str]:
    refs = profile.features.evidence_refs
    return _dedup(
        refs.get("core_features", [])
        + refs.get("ai_capabilities", [])
        + refs.get("differentiated_features", [])
        + refs.get("feature_modules", [])
    )


def _positioning_evidence(profile: CompetitorProfile) -> list[str]:
    return _dedup(profile.basic_info.evidence_refs.get("positioning", []))


def _industry_field_evidence(profile: CompetitorProfile, field: str) -> list[str]:
    ext = profile.industry_extension
    if ext is None:
        return []
    ms = getattr(ext, field, None)
    if ms is None:
        return []
    return _dedup(ms.evidence_ids)


def _feedback_theme_evidence(profile: CompetitorProfile) -> list[str]:
    out: list[str] = []
    for theme in profile.user_feedback.positive_themes + profile.user_feedback.negative_themes:
        out.extend(theme.evidence_ids)
    for review in profile.user_feedback.typical_reviews:
        out.append(review.evidence_id)
    return _dedup(out)


def _filter_pool(eids: Iterable[str], pool: set[str]) -> list[str]:
    return [e for e in eids if e in pool]


# ---------- pricing helpers ----------


def _paid_plans(profile: CompetitorProfile) -> list[PricingPlan]:
    return [
        p for p in profile.pricing.plans
        if p.price_per_seat_monthly_usd is not None
        and p.price_per_seat_monthly_usd > 0
    ]


def _entry_paid(profile: CompetitorProfile) -> PricingPlan | None:
    paid = sorted(
        _paid_plans(profile),
        key=lambda p: p.price_per_seat_monthly_usd or float("inf"),
    )
    return paid[0] if paid else None


def _advanced_paid(profile: CompetitorProfile) -> PricingPlan | None:
    """中间档（次便宜的付费档），近似 Business/Advanced 这一层。"""
    paid = sorted(
        _paid_plans(profile),
        key=lambda p: p.price_per_seat_monthly_usd or float("inf"),
    )
    if len(paid) >= 2:
        return paid[1]
    return paid[0] if paid else None


# ---------- 各维度分析器 ----------


def analyze_pricing_comparison(
    *,
    target_product: str,
    competitors: list[str],
    profiles: dict[str, CompetitorProfile],
    valid_pool: set[str],
) -> DimensionAnalysis:
    """对比 entry-paid / advanced-paid 两档价格。"""
    products = [target_product, *competitors]
    matrix_entry: dict[str, float] = {}
    matrix_advanced: dict[str, float] = {}
    entry_meta: dict[str, tuple[float, str, list[str]]] = {}
    advanced_meta: dict[str, tuple[float, str, list[str]]] = {}

    for product in products:
        profile = profiles.get(product)
        if profile is None:
            continue
        ev = _filter_pool(_pricing_evidence(profile), valid_pool)
        entry = _entry_paid(profile)
        if entry is not None and entry.price_per_seat_monthly_usd is not None:
            matrix_entry[product] = entry.price_per_seat_monthly_usd
            entry_meta[product] = (entry.price_per_seat_monthly_usd, entry.name, ev)
        advanced = _advanced_paid(profile)
        if advanced is not None and advanced.price_per_seat_monthly_usd is not None:
            matrix_advanced[product] = advanced.price_per_seat_monthly_usd
            advanced_meta[product] = (advanced.price_per_seat_monthly_usd, advanced.name, ev)

    claims: list[AnalysisClaim] = []

    # 1. entry 档最低价
    if len(entry_meta) >= 2:
        cheap_p, (cheap_price, cheap_plan, _) = min(
            entry_meta.items(), key=lambda x: x[1][0]
        )
        cited = _dedup(eid for _, _, ev in entry_meta.values() for eid in ev)
        if cited:
            claims.append(
                AnalysisClaim(
                    claim_id="cl_price_entry_low",
                    text=(
                        f"{cheap_p} {cheap_plan} 档 ${cheap_price:g}/seat/月，"
                        f"在对比组（{', '.join(entry_meta.keys())}）中入门付费档最低。"
                    ),
                    products_involved=list(entry_meta.keys()),
                    evidence_ids=cited,
                    confidence=0.9,
                )
            )

    # 2. advanced 档溢价
    if len(advanced_meta) >= 2:
        high_p, (high_price, high_plan, _) = max(
            advanced_meta.items(), key=lambda x: x[1][0]
        )
        low_p, (low_price, _, _) = min(
            advanced_meta.items(), key=lambda x: x[1][0]
        )
        if high_price > low_price * 1.2:  # 显著溢价才下结论
            cited = _dedup(eid for _, _, ev in advanced_meta.values() for eid in ev)
            if cited:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_price_advanced_premium",
                        text=(
                            f"{high_p} {high_plan} 档 ${high_price:g}/seat/月，"
                            f"较组内最低的 {low_p}（${low_price:g}）有约 "
                            f"{(high_price / low_price - 1) * 100:.0f}% 溢价。"
                        ),
                        products_involved=list(advanced_meta.keys()),
                        evidence_ids=cited,
                        confidence=0.88,
                    )
                )

    # 3. 定价模式一致性观察
    models = {p: profiles[p].pricing.pricing_model.value for p in products if p in profiles}
    distinct_models = set(models.values())
    if len(distinct_models) == 1 and len(models) >= 2:
        ev_all = _dedup(
            eid for p in models for eid in _filter_pool(_pricing_evidence(profiles[p]), valid_pool)
        )
        if ev_all:
            claims.append(
                AnalysisClaim(
                    claim_id="cl_price_model_aligned",
                    text=(
                        f"对比组 {', '.join(models.keys())} 均采用 "
                        f"{next(iter(distinct_models))} 模式，行业定价范式趋同。"
                    ),
                    products_involved=list(models.keys()),
                    evidence_ids=ev_all,
                    confidence=0.82,
                )
            )

    # ----- 单产品分支：0 竞品时上面 3 条 claim 全空，转向产出"自身定价档位画像"-----
    if not competitors and not claims:
        target_profile = profiles.get(target_product)
        if target_profile is not None:
            ev_target = _filter_pool(_pricing_evidence(target_profile), valid_pool)
            entry = _entry_paid(target_profile)
            advanced = _advanced_paid(target_profile)
            if entry is not None and entry.price_per_seat_monthly_usd is not None and ev_target:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_price_self_entry",
                        text=(
                            f"{target_product} 入门付费档 {entry.name}："
                            f"${entry.price_per_seat_monthly_usd:g}/seat/月。"
                        ),
                        products_involved=[target_product],
                        evidence_ids=ev_target,
                        confidence=0.88,
                    )
                )
            if advanced is not None and advanced.price_per_seat_monthly_usd is not None and ev_target:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_price_self_advanced",
                        text=(
                            f"{target_product} 中档计划 {advanced.name}："
                            f"${advanced.price_per_seat_monthly_usd:g}/seat/月。"
                        ),
                        products_involved=[target_product],
                        evidence_ids=ev_target,
                        confidence=0.86,
                    )
                )
            # pricing_model 单独点出（订阅 / 用量 / 一次性）
            if ev_target:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_price_self_model",
                        text=(
                            f"{target_product} 采用 "
                            f"{target_profile.pricing.pricing_model.value} 定价模式。"
                        ),
                        products_involved=[target_product],
                        evidence_ids=ev_target,
                        confidence=0.82,
                    )
                )

    summary = _build_pricing_summary(matrix_entry, matrix_advanced)
    confidence = 0.9 if claims else 0.5
    matrix: dict[str, dict[str, float]] | None = None
    if matrix_entry or matrix_advanced:
        matrix = {}
        if matrix_entry:
            matrix["entry_paid_usd"] = matrix_entry
        if matrix_advanced:
            matrix["advanced_paid_usd"] = matrix_advanced

    return DimensionAnalysis(
        dimension=AnalysisDimension.PRICING_COMPARISON,
        summary=summary,
        claims=claims,
        comparison_matrix=matrix,
        confidence=confidence,
    )


def _build_pricing_summary(
    entry: dict[str, float], advanced: dict[str, float]
) -> str:
    parts: list[str] = []
    if entry:
        items = ", ".join(f"{p} ${v:g}" for p, v in entry.items())
        parts.append(f"入门付费档：{items}")
    if advanced:
        items = ", ".join(f"{p} ${v:g}" for p, v in advanced.items())
        parts.append(f"中档：{items}")
    return "；".join(parts) if parts else "对比组缺少可比较的付费档价格。"


def analyze_feature_comparison(
    *,
    target_product: str,
    competitors: list[str],
    profiles: dict[str, CompetitorProfile],
    valid_pool: set[str],
) -> DimensionAnalysis:
    """对比 industry_extension 各能力的成熟度 + 显著差异点。"""
    products = [target_product, *competitors]
    matrix: dict[str, dict[str, str]] = {}
    claims: list[AnalysisClaim] = []

    for field in _MATURITY_FIELDS:
        row: dict[str, str] = {}
        evidence_by_product: dict[str, list[str]] = {}
        ranks: dict[str, int] = {}
        for product in products:
            profile = profiles.get(product)
            if profile is None or profile.industry_extension is None:
                continue
            ms = getattr(profile.industry_extension, field, None)
            if ms is None or not ms.has_capability:
                continue
            row[product] = ms.maturity_level
            ranks[product] = _MATURITY_RANK.get(ms.maturity_level, 0)
            ev = _filter_pool(_industry_field_evidence(profile, field), valid_pool)
            if not ev:  # fallback：该字段没有专属 evidence，借用 feature 整体 evidence
                ev = _filter_pool(_feature_evidence(profile), valid_pool)
            if ev:
                evidence_by_product[product] = ev
        if row:
            matrix[field] = row
        # 显著差异：最高 vs 最低差 >= 2 档时生成一条 claim
        if len(ranks) >= 2:
            best_p, best_rank = max(ranks.items(), key=lambda x: x[1])
            worst_p, worst_rank = min(ranks.items(), key=lambda x: x[1])
            if best_rank - worst_rank >= 2 and best_p != worst_p:
                cited = _dedup(
                    eid for p in (best_p, worst_p) for eid in evidence_by_product.get(p, [])
                )
                if cited:
                    label = _FIELD_LABEL_ZH.get(field, field)
                    claims.append(
                        AnalysisClaim(
                            claim_id=f"cl_feat_{field}",
                            text=(
                                f"在「{label}」能力上 {best_p}（{row[best_p]}）"
                                f"明显强于 {worst_p}（{row[worst_p]}）。"
                            ),
                            products_involved=[best_p, worst_p],
                            evidence_ids=cited,
                            confidence=0.85,
                        )
                    )

    # 目标产品差异化亮点（AI / 文档/ 等若达 advanced+）
    target = profiles.get(target_product)
    if target is not None and target.industry_extension is not None:
        highlights: list[str] = []
        for field in ("document_collaboration", "ai_assistance", "knowledge_base"):
            ms = getattr(target.industry_extension, field, None)
            if ms is not None and _MATURITY_RANK.get(ms.maturity_level, 0) >= 3:
                highlights.append(_FIELD_LABEL_ZH.get(field, field))
        if highlights:
            target_ev = _filter_pool(_feature_evidence(target), valid_pool)
            if target_ev:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_feat_target_strength",
                        text=(
                            f"{target_product} 在 {', '.join(highlights)} 上达到 "
                            f"advanced 及以上成熟度，是核心能力锚点。"
                        ),
                        products_involved=[target_product],
                        evidence_ids=target_ev,
                        confidence=0.84,
                    )
                )

    # ----- 单产品分支：0 竞品时横向对比 claim 全空，列出 target 自身能力速览 -----
    if not competitors and target is not None and target.industry_extension is not None:
        target_ev = _filter_pool(_feature_evidence(target), valid_pool)
        if target_ev:
            # 按成熟度分组：advanced+ 列为亮点，basic/intermediate 列为常规，none 列为缺口
            advanced_caps: list[str] = []
            basic_caps: list[str] = []
            for field in _MATURITY_FIELDS:
                ms = getattr(target.industry_extension, field, None)
                if ms is None or not ms.has_capability:
                    continue
                rank = _MATURITY_RANK.get(ms.maturity_level, 0)
                label = _FIELD_LABEL_ZH.get(field, field)
                if rank >= 3:
                    advanced_caps.append(label)
                else:
                    basic_caps.append(label)
            # 已有的 cl_feat_target_strength 涵盖了 highlights，这里补"广度"角度的 claim
            existing_ids = {c.claim_id for c in claims}
            if advanced_caps and "cl_feat_self_advanced" not in existing_ids:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_feat_self_advanced",
                        text=(
                            f"{target_product} 在 {', '.join(advanced_caps)} 能力上达到 "
                            f"advanced 及以上成熟度。"
                        ),
                        products_involved=[target_product],
                        evidence_ids=target_ev,
                        confidence=0.82,
                    )
                )
            if basic_caps:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_feat_self_basic",
                        text=(
                            f"{target_product} 同时提供 {', '.join(basic_caps)} 等基础能力，"
                            f"功能覆盖广度较完整。"
                        ),
                        products_involved=[target_product],
                        evidence_ids=target_ev,
                        confidence=0.78,
                    )
                )

    summary = (
        f"覆盖 {len(matrix)} 个能力维度的横向对比，"
        f"其中 {len(claims)} 处存在显著强弱差异。"
        if matrix
        else (
            f"列出 {target_product} 自身 {len(claims)} 项能力点。"
            if not competitors and claims
            else "输入 profile 缺少 industry_extension 字段，无法做能力分析。"
        )
    )
    confidence = 0.85 if claims else (0.55 if matrix else 0.3)
    return DimensionAnalysis(
        dimension=AnalysisDimension.FEATURE_COMPARISON,
        summary=summary,
        claims=claims,
        comparison_matrix=matrix or None,
        confidence=confidence,
    )


def analyze_swot(
    *,
    target_product: str,
    competitors: list[str],
    profiles: dict[str, CompetitorProfile],
    valid_pool: set[str],
) -> DimensionAnalysis:
    """围绕 target_product 视角的 SWOT。

    S/W 来源：target.competitive + 能力维度上的领先 / 落后；
    O/T 来源：竞品弱点 / 强项。
    """
    target = profiles.get(target_product)
    claims: list[AnalysisClaim] = []
    if target is None:
        return DimensionAnalysis(
            dimension=AnalysisDimension.SWOT,
            summary=f"未提供 {target_product} 的 profile，无法做 SWOT。",
            claims=claims,
            comparison_matrix=None,
            confidence=0.2,
        )

    # 1. Strengths：target.competitive.strengths（如果有）
    for idx, ins in enumerate(target.competitive.strengths, start=1):
        ev = _filter_pool(ins.evidence_ids, valid_pool)
        if not ev:
            ev = _filter_pool(_feature_evidence(target) + _positioning_evidence(target), valid_pool)
        if ev:
            claims.append(
                AnalysisClaim(
                    claim_id=f"cl_swot_s_{idx}",
                    text=f"{target_product} 优势：{ins.text}",
                    products_involved=[target_product],
                    evidence_ids=ev,
                    confidence=min(0.9, max(0.6, ins.confidence)),
                    qualifier="strength",
                )
            )

    # 2. Weaknesses：目标在能力维度上落后竞品的项
    if target.industry_extension is not None:
        for field in _MATURITY_FIELDS:
            target_ms = getattr(target.industry_extension, field, None)
            target_rank = _MATURITY_RANK.get(
                target_ms.maturity_level if target_ms and target_ms.has_capability else "none",
                0,
            )
            beaters: list[str] = []
            beater_ev: list[str] = []
            for comp in competitors:
                cprof = profiles.get(comp)
                if cprof is None or cprof.industry_extension is None:
                    continue
                cms = getattr(cprof.industry_extension, field, None)
                if cms is None or not cms.has_capability:
                    continue
                if _MATURITY_RANK.get(cms.maturity_level, 0) - target_rank >= 2:
                    beaters.append(comp)
                    beater_ev.extend(_filter_pool(_industry_field_evidence(cprof, field), valid_pool))
                    if not beater_ev:
                        beater_ev.extend(_filter_pool(_feature_evidence(cprof), valid_pool))
            if beaters and beater_ev:
                label = _FIELD_LABEL_ZH.get(field, field)
                claims.append(
                    AnalysisClaim(
                        claim_id=f"cl_swot_w_{field}",
                        text=(
                            f"{target_product} 在「{label}」上较 {', '.join(beaters)} 落后，"
                            f"可能成为采购决策中的劣势。"
                        ),
                        products_involved=[target_product, *beaters],
                        evidence_ids=_dedup(beater_ev),
                        confidence=0.78,
                        qualifier="weakness",
                    )
                )
                if len([c for c in claims if (c.qualifier or "") == "weakness"]) >= 3:
                    break

    # 2b. 单产品分支：0 竞品时上面拿不出 Weakness claim，转向
    #     target.competitive.weaknesses + user_feedback.pain_points 作为单产品自评弱项
    if not competitors:
        for idx, ins in enumerate(target.competitive.weaknesses, start=1):
            ev = _filter_pool(ins.evidence_ids, valid_pool)
            if not ev:
                ev = _filter_pool(
                    _feature_evidence(target) + _feedback_theme_evidence(target),
                    valid_pool,
                )
            if ev:
                claims.append(
                    AnalysisClaim(
                        claim_id=f"cl_swot_w_self_{idx}",
                        text=f"{target_product} 自评弱项：{ins.text}",
                        products_involved=[target_product],
                        evidence_ids=ev,
                        confidence=min(0.85, max(0.6, ins.confidence)),
                        qualifier="weakness",
                    )
                )
                if len([c for c in claims if (c.qualifier or "") == "weakness"]) >= 3:
                    break
        # 用户痛点也是弱项的真实信号
        if (
            len([c for c in claims if (c.qualifier or "") == "weakness"]) < 3
            and target.user_feedback.user_pain_points
        ):
            for idx, pain in enumerate(target.user_feedback.user_pain_points, start=1):
                if pain.severity == "low":
                    continue
                ev = _filter_pool(pain.evidence_ids, valid_pool)
                if not ev:
                    ev = _filter_pool(_feedback_theme_evidence(target), valid_pool)
                if ev:
                    claims.append(
                        AnalysisClaim(
                            claim_id=f"cl_swot_w_pain_{idx}",
                            text=(
                                f"{target_product} 用户反馈的痛点「{pain.pain}」（severity={pain.severity}）"
                                f"指向能力上的弱项。"
                            ),
                            products_involved=[target_product],
                            evidence_ids=ev,
                            confidence=0.74,
                            qualifier="weakness",
                        )
                    )
                    if len([c for c in claims if (c.qualifier or "") == "weakness"]) >= 3:
                        break

    # 3. Opportunities：竞品共同弱点 → 机会
    if target.industry_extension is not None:
        for field in _MATURITY_FIELDS:
            target_ms = getattr(target.industry_extension, field, None)
            target_rank = _MATURITY_RANK.get(
                target_ms.maturity_level if target_ms and target_ms.has_capability else "none",
                0,
            )
            if target_rank < 2:
                continue
            weak_competitors: list[str] = []
            ev_ids: list[str] = []
            for comp in competitors:
                cprof = profiles.get(comp)
                if cprof is None or cprof.industry_extension is None:
                    continue
                cms = getattr(cprof.industry_extension, field, None)
                rank = _MATURITY_RANK.get(
                    cms.maturity_level if cms and cms.has_capability else "none", 0
                )
                if rank <= 1:
                    weak_competitors.append(comp)
            if len(weak_competitors) == len(competitors) and competitors:
                ev_ids = _filter_pool(_industry_field_evidence(target, field), valid_pool)
                if not ev_ids:
                    ev_ids = _filter_pool(_feature_evidence(target), valid_pool)
                if ev_ids:
                    label = _FIELD_LABEL_ZH.get(field, field)
                    claims.append(
                        AnalysisClaim(
                            claim_id=f"cl_swot_o_{field}",
                            text=(
                                f"{target_product} 在「{label}」上已具备，"
                                f"而对比组（{', '.join(weak_competitors)}）普遍较弱，存在差异化扩张机会。"
                            ),
                            products_involved=[target_product, *weak_competitors],
                            evidence_ids=ev_ids,
                            confidence=0.7,
                            qualifier="opportunity",
                        )
                    )
                    break

    # 4. Threats：竞品在 target 短板上 best_in_class
    if target.industry_extension is not None:
        for field in ("task_management", "workflow_automation"):
            target_ms = getattr(target.industry_extension, field, None)
            target_rank = _MATURITY_RANK.get(
                target_ms.maturity_level if target_ms and target_ms.has_capability else "none",
                0,
            )
            threats: list[str] = []
            ev_ids: list[str] = []
            for comp in competitors:
                cprof = profiles.get(comp)
                if cprof is None or cprof.industry_extension is None:
                    continue
                cms = getattr(cprof.industry_extension, field, None)
                if (
                    cms is not None
                    and cms.has_capability
                    and _MATURITY_RANK.get(cms.maturity_level, 0) >= 4
                    and target_rank < 3
                ):
                    threats.append(comp)
                    ev_ids.extend(_filter_pool(_industry_field_evidence(cprof, field), valid_pool))
                    if not ev_ids:
                        ev_ids.extend(_filter_pool(_feature_evidence(cprof), valid_pool))
            if threats and ev_ids:
                label = _FIELD_LABEL_ZH.get(field, field)
                claims.append(
                    AnalysisClaim(
                        claim_id=f"cl_swot_t_{field}",
                        text=(
                            f"{', '.join(threats)} 在「{label}」上达到 best_in_class，"
                            f"对 {target_product} 在该场景的获客构成威胁。"
                        ),
                        products_involved=[target_product, *threats],
                        evidence_ids=_dedup(ev_ids),
                        confidence=0.76,
                        qualifier="threat",
                    )
                )
                break

    summary = (
        f"围绕 {target_product} 视角，共形成 "
        f"{sum(1 for c in claims if (c.qualifier or '') == 'strength')} S / "
        f"{sum(1 for c in claims if (c.qualifier or '') == 'weakness')} W / "
        f"{sum(1 for c in claims if (c.qualifier or '') == 'opportunity')} O / "
        f"{sum(1 for c in claims if (c.qualifier or '') == 'threat')} T 条结论。"
    )
    confidence = 0.82 if len(claims) >= 3 else (0.65 if claims else 0.4)
    return DimensionAnalysis(
        dimension=AnalysisDimension.SWOT,
        summary=summary,
        claims=claims,
        comparison_matrix=None,
        confidence=confidence,
    )


def analyze_differentiation(
    *,
    target_product: str,
    competitors: list[str],
    profiles: dict[str, CompetitorProfile],
    valid_pool: set[str],
) -> DimensionAnalysis:
    """差异化机会：竞品共同弱点 + 用户痛点中可被填补的项。"""
    target = profiles.get(target_product)
    claims: list[AnalysisClaim] = []
    if target is None:
        return DimensionAnalysis(
            dimension=AnalysisDimension.DIFFERENTIATION,
            summary=f"未提供 {target_product} 的 profile，无法做差异化分析。",
            claims=claims,
            comparison_matrix=None,
            confidence=0.2,
        )

    # 1. 竞品用户痛点 → 若 target 在对应能力 advanced+，列为差异化机会
    for comp in competitors:
        cprof = profiles.get(comp)
        if cprof is None:
            continue
        for pain in cprof.user_feedback.user_pain_points:
            if pain.severity == "low":
                continue
            ev = _filter_pool(pain.evidence_ids, valid_pool)
            if not ev:
                ev = _filter_pool(_feedback_theme_evidence(cprof), valid_pool)
            if not ev:
                continue
            claims.append(
                AnalysisClaim(
                    claim_id=f"cl_diff_pain_{comp}_{len(claims)}",
                    text=(
                        f"{comp} 的用户痛点「{pain.pain}」可能转化为 {target_product} 的差异化机会。"
                    ),
                    products_involved=[target_product, comp],
                    evidence_ids=ev,
                    confidence=0.7,
                )
            )
            if len(claims) >= 3:
                break
        if len(claims) >= 3:
            break

    # 2. 能力维度上竞品集体弱、target 强 → 差异化锚点
    if target.industry_extension is not None and len(claims) < 4:
        for field in _MATURITY_FIELDS:
            target_ms = getattr(target.industry_extension, field, None)
            if (
                target_ms is None
                or not target_ms.has_capability
                or _MATURITY_RANK.get(target_ms.maturity_level, 0) < 3
            ):
                continue
            weak_comp_ranks = []
            for comp in competitors:
                cprof = profiles.get(comp)
                if cprof is None or cprof.industry_extension is None:
                    continue
                cms = getattr(cprof.industry_extension, field, None)
                rank = _MATURITY_RANK.get(
                    cms.maturity_level if cms and cms.has_capability else "none", 0
                )
                weak_comp_ranks.append(rank)
            if weak_comp_ranks and all(r <= 1 for r in weak_comp_ranks):
                ev = _filter_pool(_industry_field_evidence(target, field), valid_pool)
                if not ev:
                    ev = _filter_pool(_feature_evidence(target), valid_pool)
                if ev:
                    label = _FIELD_LABEL_ZH.get(field, field)
                    claims.append(
                        AnalysisClaim(
                            claim_id=f"cl_diff_cap_{field}",
                            text=(
                                f"{target_product} 的「{label}」能力（{target_ms.maturity_level}）"
                                f"在对比组中独家具备 advanced 以上水平，可重点宣推。"
                            ),
                            products_involved=[target_product, *competitors],
                            evidence_ids=ev,
                            confidence=0.75,
                        )
                    )
            if len(claims) >= 4:
                break

    # ----- 单产品分支：0 竞品时上面两段全空，转向"自身核心差异化锚点"-----
    if not competitors and not claims and target.industry_extension is not None:
        target_ev = _filter_pool(_feature_evidence(target), valid_pool)
        if target_ev:
            anchors: list[str] = []
            for field in _MATURITY_FIELDS:
                ms = getattr(target.industry_extension, field, None)
                if (
                    ms is None
                    or not ms.has_capability
                    or _MATURITY_RANK.get(ms.maturity_level, 0) < 3
                ):
                    continue
                anchors.append(_FIELD_LABEL_ZH.get(field, field))
            if anchors:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_diff_self_anchors",
                        text=(
                            f"{target_product} 的核心差异化锚点："
                            f"{', '.join(anchors)} 均达到 advanced 及以上成熟度，"
                            f"可作为对外定位和宣推的能力支点。"
                        ),
                        products_involved=[target_product],
                        evidence_ids=target_ev,
                        confidence=0.72,
                    )
                )
        # target.competitive.strengths 里如果有标记为 "differentiator" 的 Insight，
        # 当作明示差异化（v1 schema 没有专门字段，复用 strengths 文本）
        for idx, ins in enumerate(target.competitive.strengths, start=1):
            ev = _filter_pool(ins.evidence_ids, valid_pool)
            if not ev:
                ev = _filter_pool(_feature_evidence(target), valid_pool)
            if ev and len(claims) < 3:
                claims.append(
                    AnalysisClaim(
                        claim_id=f"cl_diff_self_strength_{idx}",
                        text=f"{target_product} 自身的差异化卖点：{ins.text}",
                        products_involved=[target_product],
                        evidence_ids=ev,
                        confidence=min(0.78, max(0.55, ins.confidence)),
                    )
                )

    summary = (
        f"识别 {len(claims)} 条 {target_product} 可差异化的方向。"
        if claims
        else (
            f"{target_product} 暂未在 profile 中暴露足够的差异化锚点。"
            if not competitors
            else f"对比组内未发现明显的 {target_product} 差异化机会。"
        )
    )
    confidence = 0.75 if claims else 0.5
    return DimensionAnalysis(
        dimension=AnalysisDimension.DIFFERENTIATION,
        summary=summary,
        claims=claims,
        comparison_matrix=None,
        confidence=confidence,
    )


def analyze_positioning(
    *,
    target_product: str,
    competitors: list[str],
    profiles: dict[str, CompetitorProfile],
    valid_pool: set[str],
) -> DimensionAnalysis:
    """对比 positioning + target_users 重叠度。"""
    products = [target_product, *competitors]
    claims: list[AnalysisClaim] = []
    matrix: dict[str, dict[str, str]] = {"positioning": {}}

    for product in products:
        profile = profiles.get(product)
        if profile is None or not profile.basic_info.positioning:
            continue
        matrix["positioning"][product] = profile.basic_info.positioning

    # 每个产品的 positioning 单独成 claim
    for product, statement in matrix["positioning"].items():
        profile = profiles[product]
        ev = _filter_pool(_positioning_evidence(profile), valid_pool)
        if not ev:
            continue
        claims.append(
            AnalysisClaim(
                claim_id=f"cl_pos_{product}",
                text=f"{product} 定位：{statement}",
                products_involved=[product],
                evidence_ids=ev,
                confidence=0.82,
            )
        )

    # 目标用户重叠度（简单交集）
    target = profiles.get(target_product)
    if target is not None:
        target_segments = {seg.name for seg in target.basic_info.target_users}
        overlapping: list[str] = []
        for comp in competitors:
            cprof = profiles.get(comp)
            if cprof is None:
                continue
            comp_segments = {seg.name for seg in cprof.basic_info.target_users}
            if target_segments & comp_segments:
                overlapping.append(comp)
        if overlapping:
            ev_all = _dedup(
                eid
                for p in (target_product, *overlapping)
                if profiles.get(p) is not None
                for eid in _filter_pool(_positioning_evidence(profiles[p]), valid_pool)
            )
            if ev_all:
                claims.append(
                    AnalysisClaim(
                        claim_id="cl_pos_audience_overlap",
                        text=(
                            f"{target_product} 与 {', '.join(overlapping)} 在目标用户群体上存在重叠，"
                            f"市场拓展时面临直接竞争。"
                        ),
                        products_involved=[target_product, *overlapping],
                        evidence_ids=ev_all,
                        confidence=0.76,
                    )
                )

    summary = (
        f"对比 {len(matrix['positioning'])} 个产品的定位陈述。"
        if matrix["positioning"]
        else "对比组缺少 positioning 字段。"
    )
    confidence = 0.82 if claims else 0.4
    return DimensionAnalysis(
        dimension=AnalysisDimension.POSITIONING,
        summary=summary,
        claims=claims,
        comparison_matrix=matrix if matrix["positioning"] else None,
        confidence=confidence,
    )


def analyze_user_feedback(
    *,
    target_product: str,
    competitors: list[str],
    profiles: dict[str, CompetitorProfile],
    valid_pool: set[str],
) -> DimensionAnalysis:
    """用户反馈摘要：正/负向 themes + 痛点。"""
    products = [target_product, *competitors]
    claims: list[AnalysisClaim] = []
    matrix: dict[str, dict[str, float]] = {"overall_rating": {}}

    for product in products:
        profile = profiles.get(product)
        if profile is None:
            continue
        rating = profile.user_feedback.overall_rating
        if rating is not None:
            matrix["overall_rating"][product] = rating

    # 1. positive themes — 每个产品取最显著的 1 条
    for product in products:
        profile = profiles.get(product)
        if profile is None or not profile.user_feedback.positive_themes:
            continue
        theme = profile.user_feedback.positive_themes[0]
        ev = _filter_pool(theme.evidence_ids, valid_pool)
        if not ev:
            ev = _filter_pool(_feedback_theme_evidence(profile), valid_pool)
        if not ev:
            continue
        claims.append(
            AnalysisClaim(
                claim_id=f"cl_uf_pos_{product}",
                text=f"{product} 的用户反馈聚焦在「{theme.theme}」上。",
                products_involved=[product],
                evidence_ids=ev,
                confidence=0.78,
            )
        )

    # 2. 负向痛点 — 取首个
    for product in products:
        profile = profiles.get(product)
        if profile is None:
            continue
        if not profile.user_feedback.user_pain_points:
            continue
        pain = profile.user_feedback.user_pain_points[0]
        ev = _filter_pool(pain.evidence_ids, valid_pool)
        if not ev:
            ev = _filter_pool(_feedback_theme_evidence(profile), valid_pool)
        if not ev:
            continue
        claims.append(
            AnalysisClaim(
                claim_id=f"cl_uf_pain_{product}",
                text=f"{product} 用户反馈的痛点：「{pain.pain}」（severity={pain.severity}）。",
                products_involved=[product],
                evidence_ids=ev,
                confidence=0.74,
            )
        )

    summary = (
        f"覆盖 {len(matrix['overall_rating'])} 个产品的反馈聚合，"
        f"形成 {len(claims)} 条带证据的 theme。"
        if claims
        else "对比组缺少用户反馈类 evidence。"
    )
    confidence = 0.78 if claims else 0.45
    return DimensionAnalysis(
        dimension=AnalysisDimension.USER_FEEDBACK,
        summary=summary,
        claims=claims,
        comparison_matrix=matrix if matrix["overall_rating"] else None,
        confidence=confidence,
    )


# ---------- 调度入口 ----------

DIMENSION_DISPATCH = {
    AnalysisDimension.FEATURE_COMPARISON: analyze_feature_comparison,
    AnalysisDimension.PRICING_COMPARISON: analyze_pricing_comparison,
    AnalysisDimension.USER_FEEDBACK: analyze_user_feedback,
    AnalysisDimension.SWOT: analyze_swot,
    AnalysisDimension.DIFFERENTIATION: analyze_differentiation,
    AnalysisDimension.POSITIONING: analyze_positioning,
}


def analyze_dimension(
    dimension: AnalysisDimension,
    *,
    target_product: str,
    competitors: list[str],
    profiles: dict[str, CompetitorProfile],
    valid_pool: set[str],
) -> DimensionAnalysis:
    """单一调度入口，便于 agent.py 单点替换为 LLM 分析。"""
    fn = DIMENSION_DISPATCH[dimension]
    return fn(
        target_product=target_product,
        competitors=competitors,
        profiles=profiles,
        valid_pool=valid_pool,
    )


__all__ = [
    "analyze_differentiation",
    "analyze_dimension",
    "analyze_feature_comparison",
    "analyze_positioning",
    "analyze_pricing_comparison",
    "analyze_swot",
    "analyze_user_feedback",
    "collect_profile_evidence_ids",
]
