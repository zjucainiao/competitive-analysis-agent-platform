"""Agent 输入快照摘要 —— 让 node detail 的「输入」区与「输出」区对称可观测。

背景：
- 输出侧的摘要在**前端** ``summarizeOutput`` 算（前端已持有完整 output 对象）；
- 输入侧前端拿不到 input 对象，故在**后端** ``BaseAgent.invoke`` 出口算一份紧凑摘要
  （计数 + 关键名），脱敏后挂到 ``AgentOutputBase.input_snapshot``，随 outputs 流到前端。

设计取舍：
- **纯摘要、不 dump 大对象**：reporter input 含整份 ``AnalysisResult``、qa input 含
  ``ReportDraft`` + 多份 ``CompetitorProfile``，直接 dump 会撑爆存储/响应。这里只留计数与
  关键名（产品、维度数、源文档数、画像名、草稿版本、QA 反馈项数…）。
- **纯 getattr introspection**：不按 input 类型分支，靠字段名鸭子类型识别，五类输入
  （collector / extractor / analyst / reporter / qa）共用一套逻辑，新增字段零改动。
- **绝不抛异常**：观测层永不打断主流程。调用点（``_base``）已 try/except 包裹，本函数
  本身也尽量容错（最坏返回已累计的部分结果）。
"""

from __future__ import annotations

from typing import Any

from backend.tools import sanitize

# 单个摘要值的最大长度（防止某字段把快照撑大）
_MAX_VAL_LEN = 200


def _s(value: Any) -> str:
    """stringify + 截断 + 脱敏。"""
    text = str(value)
    if len(text) > _MAX_VAL_LEN:
        text = text[:_MAX_VAL_LEN].rstrip() + "…"
    return sanitize(text)


def summarize_agent_input(inp: Any) -> dict[str, str]:
    """把任意 ``*Input`` introspect 成紧凑的 ``{key: 摘要字符串}``。

    覆盖 collector / extractor / analyst / reporter / qa 五类输入的关键字段；未知字段忽略。
    键沿用「输出」面板的技术风格（英文键 + 紧凑值），让两侧视觉一致。
    """
    out: dict[str, str] = {}

    def put(key: str, value: Any) -> None:
        if value is None or value == "":
            return
        out[key] = _s(value)

    # --- 标识：不同 input 命名不同（product_name / target_product / project_name） ---
    put(
        "product",
        getattr(inp, "product_name", None)
        or getattr(inp, "target_product", None)
        or getattr(inp, "project_name", None),
    )
    put("official_url", getattr(inp, "official_url", None))
    put(
        "industry",
        getattr(inp, "industry", None) or getattr(inp, "industry_schema_id", None),
    )
    put("template_id", getattr(inp, "template_id", None))
    put("output_format", getattr(inp, "output_format", None))
    put("target_audience", getattr(inp, "target_audience", None))

    # --- 列表 / 字典 → 计数 ---
    dims = getattr(inp, "dimensions", None)
    if dims is not None:
        out["dimensions"] = f"{len(dims)} 维度"

    competitors = getattr(inp, "competitors", None)
    if competitors:
        out["competitors"] = _s("、".join(str(c) for c in competitors))

    raw_sources = getattr(inp, "raw_sources", None)
    if raw_sources is not None:
        out["raw_sources"] = f"{len(raw_sources)} 源文档"

    profiles = getattr(inp, "profiles", None)
    if isinstance(profiles, dict) and profiles:
        out["profiles"] = _s(f"{len(profiles)} 画像：" + "、".join(profiles.keys()))

    # 单个 profile（保险，理论上没有）
    profile = getattr(inp, "profile", None)
    if profile is not None:
        name = getattr(getattr(profile, "basic_info", None), "name", None)
        put("profile", name or "CompetitorProfile")

    # --- AnalysisResult（reporter / qa 的 analysis） ---
    analysis = getattr(inp, "analysis", None)
    if analysis is not None:
        adims = getattr(analysis, "dimensions", None)
        n = len(adims) if isinstance(adims, dict) else 0
        out["analysis"] = f"AnalysisResult · {n} 维度"

    # --- ReportDraft（qa 的 draft / reporter 的 prior_draft） ---
    draft = getattr(inp, "draft", None)
    if draft is not None:
        ver = getattr(draft, "version", "?")
        secs = getattr(draft, "sections", None) or []
        out["draft"] = f"ReportDraft v{ver} · {len(secs)} 章"

    prior = getattr(inp, "prior_draft", None)
    if prior is not None:
        ver = getattr(prior, "version", "?")
        out["prior_draft"] = f"v{ver}（定向改稿基线）"

    prior_verdicts = getattr(inp, "prior_verdicts", None)
    if prior_verdicts:
        out["prior_verdicts"] = f"{len(prior_verdicts)} 轮历史质检"

    # --- QA 返工反馈（返工轮才有） ---
    qa_feedback = getattr(inp, "qa_feedback", None)
    if isinstance(qa_feedback, dict) and qa_feedback:
        label = "QA 返工反馈"
        must = qa_feedback.get("must_address")
        if isinstance(must, list):
            label += f" · {len(must)} 项必改"
        rev = qa_feedback.get("revision")
        if rev:
            label += f" · 第 {rev} 轮"
        out["qa_feedback"] = label

    upstream = getattr(inp, "upstream_statuses", None)
    if isinstance(upstream, dict) and upstream:
        out["upstream"] = _s("、".join(f"{k}={v}" for k, v in upstream.items()))

    exclude = getattr(inp, "exclude_source_urls", None)
    if exclude:
        out["exclude_urls"] = f"{len(exclude)} 个跳过 URL"

    schema_fields = getattr(inp, "schema_fields", None)
    if schema_fields:
        out["schema_fields"] = f"{len(schema_fields)} 指定字段"

    return out


__all__ = ["summarize_agent_input"]
