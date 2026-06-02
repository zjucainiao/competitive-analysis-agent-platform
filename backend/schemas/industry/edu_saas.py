"""教育 SaaS 行业扩展 Schema。

覆盖 K12 / 高等教育 / 企业培训 / 兴趣学习 等场景的核心能力。
v1 占接口位 + 模板可用，未在 demo 主链路中实际抽取。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._maturity import MaturityScore


class EduSaasExtension(BaseModel):
    """教育 SaaS 场景扩展字段。"""

    model_config = ConfigDict(extra="forbid")

    industry_id: Literal["edu_saas"] = "edu_saas"

    live_classroom: MaturityScore | None = None             # 直播课堂
    recorded_courses: MaturityScore | None = None           # 录播课程
    content_library: MaturityScore | None = None            # 课件 / 题库
    homework_management: MaturityScore | None = None        # 作业管理
    exam_quiz: MaturityScore | None = None                  # 在线考试 / 测验
    auto_grading: MaturityScore | None = None               # 自动批改
    ai_tutoring: MaturityScore | None = None                # AI 辅导 / 个性化推荐
    progress_tracking: MaturityScore | None = None          # 学习进度 / 学情报告
    parent_dashboard: MaturityScore | None = None           # 家长仪表盘
    multi_role: MaturityScore | None = None                 # 老师 / 学生 / 家长 / 管理员
    gamification: MaturityScore | None = None               # 积分 / 勋章 / 排行榜
    mobile_support: MaturityScore | None = None             # 移动端

    evidence_refs: dict[str, list[str]] = Field(default_factory=dict)
