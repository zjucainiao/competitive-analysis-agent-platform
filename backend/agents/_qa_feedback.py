"""QA 反馈 → Agent prompt 渲染 helper（Reporter / Analyst / Collector 共用）。

设计目标：把 ``inp.qa_feedback`` dict 转成一段强标记 markdown 块，作为 ``{{ qa_feedback_block }}``
插入到各 Agent 的 user prompt 末尾。让 LLM 在重做（``_v2`` / ``_v3`` 节点）时能看到上一轮
QA 报的具体 issue，而不是只接到一个 ``revision`` 编号。

qa_feedback dict 来源：``backend.orchestrator.feedback_router._build_qa_feedback_payload``
结构：
    {
        "from_verdict_id": "v_xxx",
        "issues": [QAIssue.model_dump(), ...],  # 已过滤为 target_agent==<本 agent>
        "instructions": "<QARouting.reason>",
        "must_address": ["iss_xxx", ...],
        "revision": <int>,
    }

每个 agent 调用时传自己的 ``closing_instruction``（比如 Reporter "只改本 section 的 issue"，
Analyst "只改本 dimension 的 issue"），收尾段会拼接在 issue 列表之后。
"""

from __future__ import annotations


def render_qa_feedback_block(
    qa_feedback: dict | None,
    *,
    closing_instruction: str = "",
) -> str:
    """``qa_feedback`` dict → prompt 可读 markdown 块。

    Args:
        qa_feedback: ``AgentInputBase.qa_feedback``，由 FeedbackRouter 注入；None / 空 → 返空串
        closing_instruction: 各 agent 自定义的收尾指令，附在 issues 列表后

    Returns:
        渲染后的 markdown 块；无 feedback 时返空串（{{ slot }} 自动消失）

    实例输出（Reporter 场景）::

        ## ⚠️ QA Feedback (revision 2 · must address before publish)

        Instructions: 用户标了 ev_n_pricing_xyz 为 disputed，重写相关段落

        Must address (1 issue_id): `iss_disputed_para_777`

        Issues to fix:
          - `iss_disputed_para_777` [major · evidence_completeness] at `report.paragraphs[para_777]`
            Problem: ...
            Fix: ...
            Constraints: avoid_evidence_ids=['ev_n_pricing_xyz']

        <closing_instruction>
    """
    if not qa_feedback:
        return ""

    revision = qa_feedback.get("revision")
    instructions = (qa_feedback.get("instructions") or "").strip()
    must_address = qa_feedback.get("must_address") or []
    issues = qa_feedback.get("issues") or []

    # 没任何实质反馈时也跳过，避免空标题污染 prompt
    if not instructions and not must_address and not issues:
        return ""

    lines: list[str] = []
    header = "## ⚠️ QA Feedback"
    if revision:
        header += f" (revision {revision} · must address before publish)"
    else:
        header += " (must address before publish)"
    lines.append(header)
    lines.append("")

    if instructions:
        lines.append(f"Instructions: {instructions}")
        lines.append("")

    if must_address:
        lines.append(
            f"Must address ({len(must_address)} issue_id): "
            + ", ".join(f"`{i}`" for i in must_address)
        )
        lines.append("")

    if issues:
        lines.append("Issues to fix:")
        for iss in issues:
            issue_id = iss.get("issue_id", "?")
            dim = iss.get("dimension", "?")
            sev = iss.get("severity", "?")
            location = iss.get("location", "?")
            problem = (iss.get("problem") or "").strip()
            fix = (iss.get("suggested_fix") or "").strip()
            required = iss.get("required_inputs") or {}

            lines.append(f"  - `{issue_id}` [{sev} · {dim}] at `{location}`")
            if problem:
                lines.append(f"    Problem: {problem}")
            if fix:
                lines.append(f"    Fix: {fix}")
            if required:
                hints: list[str] = []
                for k, v in required.items():
                    hints.append(f"{k}={v!r}")
                if hints:
                    lines.append("    Constraints: " + "; ".join(hints))
        lines.append("")

    if closing_instruction:
        lines.append(closing_instruction.strip())
    return "\n".join(lines)


__all__ = ["render_qa_feedback_block"]
