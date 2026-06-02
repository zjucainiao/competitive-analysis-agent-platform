"""报告模板定义。

每个 ReportTemplate 描述一种报告版本（standard_v1 / investor_v1 / pm_v1），
包含章节顺序、每章节绑定的 AnalysisDimension、写作风格指引、最小段落数、
追加的禁用词、数据来源声明。

为避免引入额外依赖（PyYAML），模板以 Python 字面量保存，类型由
ReportTemplate Pydantic 模型严格校验。新增模板时复制其中一份并改
template_id 即可。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import AnalysisDimension


class ReportSectionTemplate(BaseModel):
    """单章节模板。"""

    model_config = ConfigDict(extra="forbid")

    section_id: str
    title: str
    order: int
    dimension: AnalysisDimension | None = Field(
        default=None,
        description="None 表示与 AnalysisDimension 无关（概览/结论/来源声明等）",
    )
    style: str = Field(description="写作风格指引，喂给 LLM 的 instruction")
    min_paragraphs: int = Field(default=1, ge=0)
    is_overview: bool = Field(
        default=False,
        description="True 时由 Reporter 直接生成概览段（不绑 claim）",
    )
    is_disclaimer: bool = Field(
        default=False,
        description="True 时该章节用 disclaimer 文本生成单段（is_soft_conclusion=True）",
    )


class ReportTemplate(BaseModel):
    """报告模板。"""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    target_audience: str
    summary_style: str
    sections: list[ReportSectionTemplate]
    banned_terms_extra: list[str] = Field(default_factory=list)
    disclaimer: str = Field(
        description="数据来源声明，自动追加为最后一节的段落",
    )
    min_total_paragraphs: int = Field(
        default=4,
        description="低于此值会被打入 self_critique 并降置信",
    )


# ---------- 共用禁用词与声明 ----------

_DEFAULT_DISCLAIMER = (
    "本报告基于公开渠道（产品官网、定价页、用户评论等）的可追溯证据生成，"
    "所有事实性结论均带 evidence_id 引用。数据快照采集于报告生成当日，"
    "如供应商后续调整定价或功能，结论可能滞后；建议复核关键数字与时间戳。"
)

_DEFAULT_BANNED_EXTRA: list[str] = []  # 通用禁用词在 tools.BANNED_TERMS


# ---------- standard_v1：通用产品 PM 视角 ----------

STANDARD_V1 = ReportTemplate(
    template_id="standard_v1",
    target_audience="产品经理",
    summary_style="客观、简洁、突出三家差异，避免褒贬绝对化表述",
    sections=[
        ReportSectionTemplate(
            section_id="sec_overview",
            title="1. 竞品概览",
            order=1,
            dimension=None,
            style="用 1-2 段介绍本次对比的目标产品、竞品、覆盖维度与数据时点",
            min_paragraphs=1,
            is_overview=True,
        ),
        ReportSectionTemplate(
            section_id="sec_features",
            title="2. 核心功能对比",
            order=2,
            dimension=AnalysisDimension.FEATURE_COMPARISON,
            style=(
                "对每条 AnalysisClaim 转一段，强调差异点。避免使用‘完美’、‘绝对领先’等"
                "绝对化表述。引用必须来自 evidence_ids 池。"
            ),
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_pricing",
            title="3. 定价策略对比",
            order=3,
            dimension=AnalysisDimension.PRICING_COMPARISON,
            style=(
                "段落含数字时必须 is_quantitative=True；价格、百分比、版本号需可在"
                "evidence 文本中找到（容差 ±5%）。"
            ),
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_swot",
            title="4. SWOT（以目标产品为视角）",
            order=4,
            dimension=AnalysisDimension.SWOT,
            style="按优势 / 劣势 / 机会 / 威胁分段；qualifier 字段标注象限。",
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_source",
            title="5. 数据来源声明",
            order=5,
            dimension=None,
            style="逐字使用 template.disclaimer",
            min_paragraphs=1,
            is_disclaimer=True,
        ),
    ],
    banned_terms_extra=_DEFAULT_BANNED_EXTRA,
    disclaimer=_DEFAULT_DISCLAIMER,
    min_total_paragraphs=4,
)


# ---------- investor_v1：投资分析视角 ----------

INVESTOR_V1 = ReportTemplate(
    template_id="investor_v1",
    target_audience="投资人",
    summary_style=(
        "中性、数据驱动、突出定价差异与潜在市场风险；避免推荐意见"
    ),
    sections=[
        ReportSectionTemplate(
            section_id="sec_overview",
            title="1. 行业格局与对比对象",
            order=1,
            dimension=None,
            style="说明对比对象在所在赛道的位置（依据 positioning 维度的 summary）",
            min_paragraphs=1,
            is_overview=True,
        ),
        ReportSectionTemplate(
            section_id="sec_positioning",
            title="2. 定位与目标用户",
            order=2,
            dimension=AnalysisDimension.POSITIONING,
            style="逐家给出定位差异，不做投资推荐",
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_pricing",
            title="3. 定价与变现能力",
            order=3,
            dimension=AnalysisDimension.PRICING_COMPARISON,
            style=(
                "对比各档定价、变现模型；含数字段落必须 is_quantitative=True"
                "并能在 evidence 中找到原值。"
            ),
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_swot",
            title="4. SWOT / 风险点",
            order=4,
            dimension=AnalysisDimension.SWOT,
            style="重点放在 Weakness / Threat 象限，作为投资风险提示",
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_source",
            title="5. 数据来源声明",
            order=5,
            dimension=None,
            style="逐字使用 template.disclaimer",
            min_paragraphs=1,
            is_disclaimer=True,
        ),
    ],
    banned_terms_extra=["稳赚", "必涨", "包赚", "无风险"],
    disclaimer=_DEFAULT_DISCLAIMER
    + " 本报告不构成任何投资建议，所列定价/估值/趋势性结论仅供研究参考。",
    min_total_paragraphs=4,
)


# ---------- pm_v1：产品规划视角，突出差异化机会 ----------

PM_V1 = ReportTemplate(
    template_id="pm_v1",
    target_audience="产品规划经理",
    summary_style="强调差异化机会与下一步动作，但避免‘最佳产品’这种绝对化表述",
    sections=[
        ReportSectionTemplate(
            section_id="sec_overview",
            title="1. 竞品扫描概览",
            order=1,
            dimension=None,
            style="1 段说明扫描了哪些竞品、抓取的维度",
            min_paragraphs=1,
            is_overview=True,
        ),
        ReportSectionTemplate(
            section_id="sec_features",
            title="2. 能力差异速览",
            order=2,
            dimension=AnalysisDimension.FEATURE_COMPARISON,
            style="按 claim 编排，突出目标产品的能力缺口",
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_opportunities",
            title="3. 差异化机会",
            order=3,
            dimension=AnalysisDimension.DIFFERENTIATION,
            style=(
                "可输出 is_soft_conclusion=True 的探索性段落，但需在 self_critique"
                "中说明假设"
            ),
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_swot",
            title="4. SWOT",
            order=4,
            dimension=AnalysisDimension.SWOT,
            style="只提取 Strengths / Weaknesses 两类，作为下一步规划的输入",
            min_paragraphs=1,
        ),
        ReportSectionTemplate(
            section_id="sec_source",
            title="5. 数据来源声明",
            order=5,
            dimension=None,
            style="逐字使用 template.disclaimer",
            min_paragraphs=1,
            is_disclaimer=True,
        ),
    ],
    banned_terms_extra=[],
    disclaimer=_DEFAULT_DISCLAIMER,
    min_total_paragraphs=4,
)


# ---------- 注册表 ----------

TEMPLATES: dict[str, ReportTemplate] = {
    STANDARD_V1.template_id: STANDARD_V1,
    INVESTOR_V1.template_id: INVESTOR_V1,
    PM_V1.template_id: PM_V1,
}


def get_template(template_id: str) -> ReportTemplate | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[str]:
    return list(TEMPLATES.keys())


__all__ = [
    "INVESTOR_V1",
    "PM_V1",
    "STANDARD_V1",
    "TEMPLATES",
    "ReportSectionTemplate",
    "ReportTemplate",
    "get_template",
    "list_templates",
]
