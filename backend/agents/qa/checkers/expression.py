"""expression：表达规范性。

规则（docs/QA.md § 3.6）：
- 禁用词列表（绝对化表述）→ 每命中 1 处一条 issue
- 第一人称（"我们" / "本公司"）→ issue
- LLM 检查（可选）：过度推断、章节缺 topic sentence

routing：均回 reporter（minor 居多，单次重写即可改好）。
LLM 失败 / 未配置时仅走规则路径，不阻塞。
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, Field, ValidationError

from backend.schemas import AgentError, QADimension, QAIssue

from ._base import BaseChecker, CheckerContext, CheckerResult

BANNED_TERMS = (
    "行业唯一",
    "绝对领先",
    "完美",
    "100%",
    "最佳",
    "无可替代",
    "全球第一",
    "最强",
    "唯一选择",
    "perfect",
    "best in class",
    "world's best",
)

FIRST_PERSON_PATTERNS = (
    "我们",
    "本公司",
    "本团队",
    "我司",
)


class _ExpressionLLMVerdict(BaseModel):
    paragraph_id: str
    has_overclaim: bool = Field(default=False, description="是否存在过度推断")
    missing_topic_sentence: bool = Field(default=False)
    notes: str = ""


class _ExpressionLLMResponse(BaseModel):
    verdicts: list[_ExpressionLLMVerdict] = Field(default_factory=list)


class ExpressionChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.EXPRESSION

    OVERALL_PASS_THRESHOLD = 0.85
    PENALTY_BANNED = 0.05
    PENALTY_FIRST_PERSON = 0.03
    PENALTY_LLM = 0.04

    def run(self, ctx: CheckerContext) -> CheckerResult:
        issues: list[QAIssue] = []
        errors: list[AgentError] = []
        banned_hits = 0
        first_person_hits = 0
        total_paras = 0

        # ---- 规则：禁用词 + 第一人称 ----
        for sec_idx, section in enumerate(ctx.draft.sections):
            for para_idx, para in enumerate(section.paragraphs):
                if not para.text.strip():
                    continue
                total_paras += 1
                location = f"report.sections[{sec_idx}].paragraphs[{para_idx}]"

                hits = _find_banned(para.text)
                if hits:
                    banned_hits += len(hits)
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_ex_banned_{para.paragraph_id}",
                            dimension=self.dimension,
                            severity="minor",
                            location=location,
                            problem=(
                                f"段落命中绝对化表述：{hits}"
                            ),
                            suggested_fix=(
                                "Reporter 改写为软性表述，例如 '业内领先' "
                                "→ '在 X 维度上明显领先于对比对象'。"
                            ),
                            target_agent="reporter",
                            required_inputs={
                                "paragraph_id": para.paragraph_id,
                                "banned_terms": hits,
                            },
                        )
                    )

                fp_hits = _find_first_person(para.text)
                if fp_hits:
                    first_person_hits += len(fp_hits)
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_ex_fp_{para.paragraph_id}",
                            dimension=self.dimension,
                            severity="minor",
                            location=location,
                            problem=(
                                f"段落使用第一人称：{fp_hits}"
                            ),
                            suggested_fix=(
                                "Reporter 将第一人称改写为第三人称客观叙述。"
                            ),
                            target_agent="reporter",
                            required_inputs={
                                "paragraph_id": para.paragraph_id,
                                "first_person_terms": fp_hits,
                            },
                        )
                    )

        # ---- LLM：过度推断 / topic sentence ----
        llm_hits = 0
        if ctx.llm is not None and ctx.prompt_dir:
            try:
                llm_resp = self._call_llm(ctx)
            except Exception as e:
                errors.append(
                    AgentError(
                        code="LLM_SCHEMA_INVALID",
                        message=(
                            f"expression LLM check failed: "
                            f"{type(e).__name__}: {e}"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )
                llm_resp = None
            if llm_resp is not None:
                para_index = {
                    p.paragraph_id: (s_idx, p_idx)
                    for s_idx, sec in enumerate(ctx.draft.sections)
                    for p_idx, p in enumerate(sec.paragraphs)
                }
                for v in llm_resp.verdicts:
                    pos = para_index.get(v.paragraph_id)
                    if pos is None:
                        continue
                    if not (v.has_overclaim or v.missing_topic_sentence):
                        continue
                    llm_hits += 1
                    s_idx, p_idx = pos
                    problems = []
                    if v.has_overclaim:
                        problems.append("过度推断")
                    if v.missing_topic_sentence:
                        problems.append("缺少主旨句")
                    issues.append(
                        QAIssue(
                            issue_id=f"iss_ex_llm_{v.paragraph_id}",
                            dimension=self.dimension,
                            severity="minor",
                            location=(
                                f"report.sections[{s_idx}].paragraphs[{p_idx}]"
                            ),
                            problem=(
                                f"LLM 检测到：{'、'.join(problems)}。{v.notes}"
                            ).strip(),
                            suggested_fix=(
                                "Reporter 复写该段：开头补 1 句 topic sentence；"
                                "把推断性结论加上 evidence 限定或 qualifier。"
                            ),
                            target_agent="reporter",
                            required_inputs={
                                "paragraph_id": v.paragraph_id,
                                "issues": problems,
                            },
                        )
                    )

        # ---- 评分 ----
        base = 1.0
        base -= self.PENALTY_BANNED * banned_hits
        base -= self.PENALTY_FIRST_PERSON * first_person_hits
        base -= self.PENALTY_LLM * llm_hits
        score = max(0.0, min(1.0, base))
        pass_ = score >= self.OVERALL_PASS_THRESHOLD
        notes = (
            f"段落 {total_paras}：禁用词 {banned_hits} 处，"
            f"第一人称 {first_person_hits} 处，LLM 标记 {llm_hits} 处。"
        )
        return CheckerResult(
            dimension=self.dimension,
            score=round(score, 3),
            pass_=pass_,
            notes=notes,
            issues=issues,
            errors=errors,
        )

    # ---- LLM 调用 ----

    def _call_llm(self, ctx: CheckerContext) -> _ExpressionLLMResponse | None:
        from pathlib import Path

        assert ctx.llm is not None and ctx.prompt_dir is not None
        prompt_path = Path(ctx.prompt_dir) / "expression.md"
        if not prompt_path.exists():
            return None
        system, user_template = _split_prompt(
            prompt_path.read_text(encoding="utf-8")
        )
        paragraphs = [
            {
                "paragraph_id": p.paragraph_id,
                "text": p.text,
                "is_soft_conclusion": p.is_soft_conclusion,
            }
            for s in ctx.draft.sections
            for p in s.paragraphs
            if p.text.strip()
        ]
        if not paragraphs:
            return None
        import json

        user = _render(
            user_template,
            report_id=ctx.draft.report_id,
            paragraphs_json=json.dumps(paragraphs, ensure_ascii=False, indent=2),
        )
        resp = ctx.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_ExpressionLLMResponse,
            temperature=0.0,
            max_tokens=1500,
        )
        try:
            return _coerce(resp, _ExpressionLLMResponse)
        except (ValueError, ValidationError):
            return None


# ---------- helpers ----------


def _find_banned(text: str) -> list[str]:
    lower = text.lower()
    return [t for t in BANNED_TERMS if t.lower() in lower]


def _find_first_person(text: str) -> list[str]:
    hits: list[str] = []
    for pat in FIRST_PERSON_PATTERNS:
        if pat in text:
            hits.append(pat)
    # 英文 'we' / 'our' 边界匹配
    if re.search(r"\b(we|our|us)\b", text, re.IGNORECASE):
        hits.append("we/our")
    return hits


def _split_prompt(prompt: str) -> tuple[str, str]:
    sys_marker = "## System"
    usr_marker = "## User"
    si = prompt.find(sys_marker)
    ui = prompt.find(usr_marker)
    if si < 0 or ui < 0 or ui < si:
        return prompt.strip(), ""
    system = prompt[si + len(sys_marker) : ui].strip()
    user = prompt[ui + len(usr_marker) :].strip()
    return system, user


def _render(template: str, **vars: object) -> str:
    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        value = vars.get(expr)
        return "" if value is None else str(value)

    return re.sub(r"{{\s*(.+?)\s*}}", repl, template)


def _coerce(resp: object, model: type[BaseModel]) -> BaseModel:
    if isinstance(resp, model):
        return resp
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, model):
        return parsed
    if isinstance(parsed, dict):
        return model.model_validate(parsed)
    if isinstance(resp, dict):
        return model.model_validate(resp)
    if hasattr(resp, "model_dump"):
        return model.model_validate(resp.model_dump())
    raise ValueError(
        f"cannot coerce response to {model.__name__}: {type(resp).__name__}"
    )


__all__ = ["BANNED_TERMS", "ExpressionChecker"]
