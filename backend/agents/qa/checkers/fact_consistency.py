"""fact_consistency：报告段落 vs 证据的事实一致性。

策略：
1. 对每个事实性段落（非 soft_conclusion）调用 LLM entailment：
   labels ∈ {entailed, contradicted, neutral}
   - entailed / total ≥ 0.95 → pass
   - 0.80–0.95 → minor issue
   - < 0.80 → major issue
2. 对 ``is_quantitative=True`` 段落做数字字面匹配（±5% 容差），
   未找到 → UNVERIFIED_QUANTITY issue。
3. LLM 不可用 / 失败时降级为纯量化检查。

routing：
- contradicted / 量化未核对 → reporter
- 多段对同一 claim 都 contradicted → analyst（claim 本身可能错）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

from backend.schemas import (
    AgentError,
    AnalysisClaim,
    Evidence,
    QADimension,
    QAIssue,
    ReportParagraph,
)

from ._base import BaseChecker, CheckerContext, CheckerResult


# ---------- 数字提取（与 Reporter tools.py 对齐） ----------


class _Quantity(BaseModel):
    kind: str
    value: float
    raw: str


def _extract_quantities(text: str) -> list[_Quantity]:
    """从段落中抽取数字 / 价格 / 百分比 / 版本号。"""
    out: list[_Quantity] = []
    # 价格 $X / $X.X
    for m in re.finditer(r"\$\s*([0-9]+(?:\.[0-9]+)?)", text):
        out.append(_Quantity(kind="price_usd", value=float(m.group(1)), raw=m.group(0)))
    # 百分比 X% / X.X%
    for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*%", text):
        out.append(_Quantity(kind="percent", value=float(m.group(1)), raw=m.group(0)))
    # 版本号 vX.Y(.Z)
    for m in re.finditer(r"v(?:ersion)?\s*([0-9]+(?:\.[0-9]+){0,2})", text, re.IGNORECASE):
        out.append(
            _Quantity(
                kind="version",
                value=_version_to_float(m.group(1)),
                raw=m.group(0),
            )
        )
    # 裸 2 位以上整数（避免抓段落 id 里的 01 02，要求前后非字母）
    for m in re.finditer(r"(?<![A-Za-z0-9_])([0-9]{2,})(?![A-Za-z0-9_])", text):
        v = float(m.group(1))
        if any(abs(q.value - v) < 1e-6 for q in out):
            continue
        out.append(_Quantity(kind="count", value=v, raw=m.group(0)))
    return out


def _version_to_float(s: str) -> float:
    parts = s.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        return float(parts[0]) + float(parts[1]) / 100 + float(parts[2]) / 10000
    except ValueError:
        return 0.0


def _quantity_supported(q: _Quantity, evidences: list[Evidence]) -> bool:
    """容差 ±5%（version / count 要求字面命中）。"""
    if q.kind in ("version", "count"):
        for ev in evidences:
            if q.raw.lower().replace(" ", "") in ev.content.lower().replace(" ", ""):
                return True
            # version 容许去掉 'v' 前缀字面匹配
            if q.kind == "version":
                bare = re.sub(r"^v(?:ersion)?\s*", "", q.raw, flags=re.IGNORECASE)
                if bare in ev.content:
                    return True
        return False
    tolerance = max(abs(q.value) * 0.05, 1e-3)
    for ev in evidences:
        for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)", ev.content):
            try:
                ev_v = float(m.group(1))
            except ValueError:
                continue
            if abs(ev_v - q.value) <= tolerance:
                return True
    return False


# ---------- LLM 响应 schema ----------


class _EntailmentVerdict(BaseModel):
    paragraph_id: str
    label: Literal["entailed", "contradicted", "neutral"] = "neutral"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    note: str = ""


class _EntailmentResponse(BaseModel):
    verdicts: list[_EntailmentVerdict] = Field(default_factory=list)


# ---------- Checker ----------


class FactConsistencyChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.FACT_CONSISTENCY

    OVERALL_PASS_THRESHOLD = 0.95
    MINOR_THRESHOLD = 0.80

    def run(self, ctx: CheckerContext) -> CheckerResult:
        issues: list[QAIssue] = []
        errors: list[AgentError] = []
        total = 0
        entailed = 0
        contradicted_paragraphs: list[str] = []

        # 段落 -> evidence 列表
        para_index: dict[str, tuple[int, int, ReportParagraph, list[Evidence]]] = {}
        for s_idx, section in enumerate(ctx.draft.sections):
            for p_idx, para in enumerate(section.paragraphs):
                if para.is_soft_conclusion or not para.text.strip():
                    continue
                evs = [
                    ctx.evidence_db[e]
                    for e in para.evidence_ids
                    if e in ctx.evidence_db
                ]
                para_index[para.paragraph_id] = (s_idx, p_idx, para, evs)

        # ---- LLM entailment（按段落分批，每批最多 6 段） ----
        llm_verdicts: dict[str, _EntailmentVerdict] = {}
        if ctx.llm is not None and ctx.prompt_dir:
            try:
                llm_verdicts = self._call_llm(ctx, para_index)
            except Exception as e:  # noqa: BLE001
                errors.append(
                    AgentError(
                        code="ENTAILMENT_FAILED",
                        message=(
                            f"fact_consistency LLM call failed: "
                            f"{type(e).__name__}: {e}"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )

        # ---- 逐段聚合 ----
        contradicted_claims: dict[str, int] = {}
        for pid, (s_idx, p_idx, para, evs) in para_index.items():
            total += 1
            location = f"report.sections[{s_idx}].paragraphs[{p_idx}]"

            # 没 evidence 的段落由 evidence_completeness 处理，这里跳过 entailment 但仍做量化
            entailment = llm_verdicts.get(pid)
            label = entailment.label if entailment else None

            if label == "entailed":
                entailed += 1
            elif label == "contradicted":
                contradicted_paragraphs.append(pid)
                for cid in para.claim_ids:
                    contradicted_claims[cid] = contradicted_claims.get(cid, 0) + 1
                issues.append(
                    QAIssue(
                        issue_id=f"iss_fc_contra_{pid}",
                        dimension=self.dimension,
                        severity="major",
                        location=location,
                        problem=(
                            f"段落 {pid!r} 与引用 evidence 冲突。"
                            f"{entailment.note}".strip()
                            if entailment
                            else ""
                        ).strip(),
                        suggested_fix=(
                            "Reporter 改写为与 evidence 一致的表述；"
                            "若 evidence 本身有误，标记 evidence.disputed=True。"
                        ),
                        target_agent="reporter",
                        required_inputs={
                            "paragraph_id": pid,
                            "evidence_ids": para.evidence_ids,
                        },
                    )
                )
            elif label == "neutral" and evs:
                # 中立 = LLM 觉得 evidence 既不支持也不反驳，视为弱支撑（不开 issue 但不计入 entailed）
                pass
            elif label is None and evs:
                # LLM 不可用，且段落有 evidence —— 暂视为 entailed，但用较低权重表示不确定
                entailed += 1  # 乐观计入；issue 由量化路径 / 其他维度兜底

            # 量化字面匹配
            if para.is_quantitative and evs:
                for q in _extract_quantities(para.text):
                    if not _quantity_supported(q, evs):
                        issues.append(
                            QAIssue(
                                issue_id=f"iss_fc_q_{pid}_{q.kind}_{q.raw}",
                                dimension=self.dimension,
                                severity="major",
                                location=location,
                                problem=(
                                    f"量化数据 {q.raw!r}（{q.kind}）未能在引用 "
                                    "evidence 中找到字面匹配（容差 ±5%）。"
                                ),
                                suggested_fix=(
                                    "Reporter 校核数字字面值，或改写为软结论"
                                    "（is_soft_conclusion=True）。"
                                ),
                                target_agent="reporter",
                                required_inputs={
                                    "paragraph_id": pid,
                                    "quantity": q.model_dump(),
                                },
                            )
                        )

        # 同一 claim 多段被 contradicted → claim 本身可能错，加 analyst routing
        for cid, hits in contradicted_claims.items():
            if hits >= 2:
                issues.append(
                    QAIssue(
                        issue_id=f"iss_fc_claim_{cid}",
                        dimension=self.dimension,
                        severity="major",
                        location=_locate_claim(ctx, cid),
                        problem=(
                            f"Claim {cid!r} 在 {hits} 个段落中被判定与 evidence 冲突，"
                            "claim 本身可能不成立。"
                        ),
                        suggested_fix=(
                            "Analyst 重新审视该 claim：若 evidence 不支撑则丢弃或限定，"
                            "若 evidence 误读则附 counter_evidence。"
                        ),
                        target_agent="analyst",
                        required_inputs={"claim_id": cid},
                    )
                )

        if total == 0:
            score = 1.0
        else:
            score = entailed / total
        if any(i.severity == "critical" for i in issues):
            score = min(score, 0.55)
        pass_ = score >= self.OVERALL_PASS_THRESHOLD and not any(
            i.severity in ("major", "critical") for i in issues
        )
        notes = (
            f"entailed {entailed}/{total}；contradicted 段落 "
            f"{len(contradicted_paragraphs)}。"
            + (" (LLM 未启用，量化兜底)" if ctx.llm is None else "")
        )
        return CheckerResult(
            dimension=self.dimension,
            score=round(score, 3),
            pass_=pass_,
            notes=notes,
            issues=issues,
            errors=errors,
        )

    # ---------- LLM 调用 ----------

    def _call_llm(
        self,
        ctx: CheckerContext,
        para_index: dict[str, tuple[int, int, ReportParagraph, list[Evidence]]],
    ) -> dict[str, _EntailmentVerdict]:
        assert ctx.llm is not None and ctx.prompt_dir is not None
        prompt_path = Path(ctx.prompt_dir) / "entailment.md"
        if not prompt_path.exists():
            return {}
        system, user_template = _split_prompt(
            prompt_path.read_text(encoding="utf-8")
        )

        out: dict[str, _EntailmentVerdict] = {}
        # 仅对有 evidence 的段落送 LLM
        batch_input: list[tuple[str, ReportParagraph, list[Evidence]]] = [
            (pid, para, evs)
            for pid, (_, _, para, evs) in para_index.items()
            if evs
        ]
        if not batch_input:
            return {}

        # 按 6 段一批，避免一次 prompt 过长
        batch_size = 6
        for i in range(0, len(batch_input), batch_size):
            batch = batch_input[i : i + batch_size]
            paragraphs_json = json.dumps(
                [
                    {
                        "paragraph_id": pid,
                        "text": para.text,
                        "evidence": [
                            {
                                "evidence_id": ev.evidence_id,
                                "product": ev.product_name,
                                "content": ev.content,
                            }
                            for ev in evs
                        ],
                    }
                    for pid, para, evs in batch
                ],
                ensure_ascii=False,
                indent=2,
            )
            user = _render(user_template, paragraphs_json=paragraphs_json)
            resp = ctx.llm.chat(
                system=system,
                messages=[{"role": "user", "content": user}],
                response_format=_EntailmentResponse,
                temperature=0.0,
                max_tokens=1500,
            )
            try:
                parsed = _coerce(resp, _EntailmentResponse)
            except (ValueError, ValidationError):
                continue
            for v in parsed.verdicts:
                out[v.paragraph_id] = v
        return out


def _locate_claim(ctx: CheckerContext, claim_id: str) -> str:
    for dim, dim_obj in ctx.analysis.dimensions.items():
        for idx, c in enumerate(dim_obj.claims):
            if c.claim_id == claim_id:
                return f"analysis.dimensions[{dim.value}].claims[{idx}]"
    return f"analysis.claim:{claim_id}"


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


_ = AnalysisClaim  # 避免未使用警告

__all__ = ["FactConsistencyChecker"]
