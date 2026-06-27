"""logic_consistency：报告内部逻辑自洽性。

策略：
1. LLM-first：抽出所有事实陈述做两两对比，标 ``contradiction``
2. Fallback 规则：
   - 同一产品 + 同一 plan 价格出现 ≥ 2 个不同值 → 矛盾
   - SWOT 中 strengths 与 weaknesses 对同一主体的描述高度重合 → 同义反复

routing：
- 段落级矛盾 → reporter
- 矛盾源头追到 AnalysisClaim → analyst
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, ValidationError

from backend.schemas import AgentError, AnalysisDimension, QADimension, QAIssue

from ._base import BaseChecker, CheckerContext, CheckerResult


class _LogicPair(BaseModel):
    paragraph_a: str
    paragraph_b: str
    rationale: str = ""
    severity: str = Field(default="major")


class _LogicResponse(BaseModel):
    contradictions: list[_LogicPair] = Field(default_factory=list)


class LogicConsistencyChecker(BaseChecker):
    dimension: ClassVar[QADimension] = QADimension.LOGIC_CONSISTENCY

    # 阈值 0.85 给"软冲突 1 处"留容差；硬冲突 ≥2 处或 critical 直接挂掉。
    OVERALL_PASS_THRESHOLD = 0.85
    # 按 severity 区分：硬冲突（major+）权重重，措辞模糊（minor）只是提示。
    PENALTY_BY_SEVERITY: ClassVar[dict[str, float]] = {
        "critical": 0.20,
        "major": 0.10,
        "minor": 0.04,
    }

    def run(self, ctx: CheckerContext) -> CheckerResult:
        issues: list[QAIssue] = []
        errors: list[AgentError] = []

        # 段落定位
        para_loc: dict[str, str] = {}
        for s_idx, section in enumerate(ctx.draft.sections):
            for p_idx, para in enumerate(section.paragraphs):
                para_loc[para.paragraph_id] = (
                    f"report.sections[{s_idx}].paragraphs[{p_idx}]"
                )

        contradiction_count = 0

        # ---- LLM 检查 ----
        if ctx.llm is not None and ctx.prompt_dir:
            try:
                pairs = self._call_llm(ctx)
            except Exception as e:  # noqa: BLE001
                errors.append(
                    AgentError(
                        code="LLM_SCHEMA_INVALID",
                        message=(
                            f"logic_consistency LLM failed: "
                            f"{type(e).__name__}: {e}"
                        ),
                        severity="warn",
                        retriable=True,
                    )
                )
                pairs = []
            for pair in pairs:
                loc_a = para_loc.get(pair.paragraph_a)
                if loc_a is None:
                    continue
                contradiction_count += 1
                sev: str = pair.severity if pair.severity in {
                    "minor",
                    "major",
                    "critical",
                } else "major"
                issues.append(
                    QAIssue(
                        issue_id=(
                            f"iss_lc_pair_{pair.paragraph_a}_{pair.paragraph_b}"
                        ),
                        dimension=self.dimension,
                        severity=sev,  # type: ignore[arg-type]
                        location=loc_a,
                        problem=(
                            f"段落 {pair.paragraph_a!r} 与 {pair.paragraph_b!r} "
                            f"逻辑矛盾。{pair.rationale}"
                        ).strip(),
                        suggested_fix=(
                            "Reporter 重写其中一段使两者一致；"
                            "若上游 claim 本身打架，回到 Analyst。"
                        ),
                        target_agent="reporter",
                        required_inputs={
                            "paragraph_a": pair.paragraph_a,
                            "paragraph_b": pair.paragraph_b,
                        },
                    )
                )

        # ---- 规则 1：同一产品 + 同 plan 价格冲突 ----
        # 形如 "Notion Business $15" / "Notion Business $18" 同时出现
        price_records: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
        for section in ctx.draft.sections:
            for para in section.paragraphs:
                for product, plan, value in _extract_plan_prices(
                    para.text, set(ctx.profiles.keys())
                ):
                    price_records[(product, plan)].append(
                        (para.paragraph_id, value)
                    )
        for (product, plan), records in price_records.items():
            values = {round(v, 2) for _, v in records}
            if len(values) > 1:
                pids = sorted({pid for pid, _ in records})
                contradiction_count += 1
                issues.append(
                    QAIssue(
                        issue_id=f"iss_lc_price_{_slug(product)}_{_slug(plan)}",
                        dimension=self.dimension,
                        severity="major",
                        location=para_loc.get(pids[0], "report"),
                        problem=(
                            f"{product} {plan} 在报告中出现多个不一致价格："
                            f"{sorted(values)}（段落 {pids}）。"
                        ),
                        suggested_fix=(
                            "Reporter 校核所有提到该 plan 的段落，"
                            "统一到 evidence 中的官方价格。"
                        ),
                        target_agent="reporter",
                        required_inputs={
                            "product": product,
                            "plan": plan,
                            "paragraph_ids": pids,
                            # WI-3：同 plan 出现 ≥2 个不同价格是**确定性**子发现（正则/数值
                            # 比对，非 LLM），可定位、可复核 → 标 hard_block，使其在
                            # 「LLM 维度默认 advisory」后仍保留一票阻塞（见 routing._gates_control_flow）。
                            # LLM 判矛盾对 / SWOT 同义反复仍 advisory（不标），交确定性信号 gate。
                            "hard_block": True,
                        },
                    )
                )

        # ---- 规则 2：SWOT 中 S 与 W 同义反复 ----
        swot_section = _find_section_by_dimension(ctx, AnalysisDimension.SWOT)
        if swot_section is not None:
            strengths = [
                p for p in swot_section.paragraphs if _swot_role(p.text) == "strength"
            ]
            weaknesses = [
                p for p in swot_section.paragraphs if _swot_role(p.text) == "weakness"
            ]
            for s in strengths:
                for w in weaknesses:
                    if _token_overlap(s.text, w.text) >= 0.6:
                        contradiction_count += 1
                        issues.append(
                            QAIssue(
                                issue_id=f"iss_lc_swot_{s.paragraph_id}_{w.paragraph_id}",
                                dimension=self.dimension,
                                severity="minor",
                                location=para_loc.get(s.paragraph_id, "report"),
                                problem=(
                                    f"SWOT 中 strength {s.paragraph_id!r} 与 "
                                    f"weakness {w.paragraph_id!r} 描述高度重合，"
                                    "疑似同义反复。"
                                ),
                                suggested_fix=(
                                    "Reporter 区分两段视角："
                                    "strength 强调优势场景，weakness 给出对比短板。"
                                ),
                                target_agent="reporter",
                                required_inputs={
                                    "strength_paragraph": s.paragraph_id,
                                    "weakness_paragraph": w.paragraph_id,
                                },
                            )
                        )
                        break  # 同一 strength 只配一条 issue

        penalty = sum(
            self.PENALTY_BY_SEVERITY.get(i.severity, 0.10) for i in issues
        )
        score = max(0.0, 1.0 - penalty)
        pass_ = score >= self.OVERALL_PASS_THRESHOLD and not any(
            i.severity == "critical" for i in issues
        )
        notes = (
            f"检出 {contradiction_count} 处逻辑冲突。"
            + (" (LLM 未启用，仅规则)" if ctx.llm is None else "")
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

    def _call_llm(self, ctx: CheckerContext) -> list[_LogicPair]:
        assert ctx.llm is not None and ctx.prompt_dir is not None
        prompt_path = Path(ctx.prompt_dir) / "contradiction.md"
        if not prompt_path.exists():
            return []
        system, user_template = _split_prompt(
            prompt_path.read_text(encoding="utf-8")
        )
        paragraphs = [
            {
                "paragraph_id": p.paragraph_id,
                "section_id": s.section_id,
                "text": p.text,
            }
            for s in ctx.draft.sections
            for p in s.paragraphs
            if p.text.strip() and not p.is_soft_conclusion
        ]
        if len(paragraphs) < 2:
            return []
        user = _render(
            user_template,
            report_id=ctx.draft.report_id,
            paragraphs_json=json.dumps(paragraphs, ensure_ascii=False, indent=2),
        )
        resp = ctx.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_LogicResponse,
            temperature=0.0,
            max_tokens=1500,
        )
        try:
            parsed = _coerce(resp, _LogicResponse)
        except (ValueError, ValidationError):
            return []
        return parsed.contradictions


# ---------- helpers ----------


_PRICE_PATTERN = re.compile(
    r"(?P<product>[A-Z][A-Za-z0-9]+)\s+"
    r"(?P<plan>[A-Z][A-Za-z0-9 ]{2,30}?)\s+"
    r"\$\s*(?P<value>[0-9]+(?:\.[0-9]+)?)"
)


def _extract_plan_prices(
    text: str, known_products: set[str]
) -> list[tuple[str, str, float]]:
    """从段落抽取 (product, plan_name, price_usd) 三元组。"""
    out: list[tuple[str, str, float]] = []
    for m in _PRICE_PATTERN.finditer(text):
        product = m.group("product")
        if known_products and product not in known_products:
            # 也接受 case-insensitive 匹配
            lower_map = {p.lower(): p for p in known_products}
            product = lower_map.get(product.lower(), product)
            if product not in known_products:
                continue
        plan = m.group("plan").strip()
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        out.append((product, plan, value))
    return out


def _swot_role(text: str) -> str | None:
    head = text.strip()[:20]
    if any(k in head for k in ("优势", "Strength", "strength")):
        return "strength"
    if any(k in head for k in ("劣势", "Weakness", "weakness")):
        return "weakness"
    return None


def _token_overlap(a: str, b: str) -> float:
    """简单中英 token 重合度。"""
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return len(inter) / max(len(ta), len(tb))


def _tokenize(text: str) -> set[str]:
    text = text.lower()
    # 取中文 2 字 token + 英文单词
    en = set(re.findall(r"[a-z]{3,}", text))
    zh = set()
    chars = [c for c in text if "一" <= c <= "鿿"]
    for i in range(len(chars) - 1):
        zh.add(chars[i] + chars[i + 1])
    return en | zh


def _find_section_by_dimension(ctx: CheckerContext, dim: AnalysisDimension):  # type: ignore[no-untyped-def]
    target_keys = {
        AnalysisDimension.SWOT: ("swot",),
        AnalysisDimension.FEATURE_COMPARISON: ("features", "feature", "功能"),
        AnalysisDimension.PRICING_COMPARISON: ("pricing", "price", "定价", "价格"),
    }.get(dim, ())
    for section in ctx.draft.sections:
        sid = section.section_id.lower()
        tt = section.title.lower()
        if any(k in sid for k in target_keys) or any(k in tt for k in target_keys):
            return section
    return None


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in text.strip().lower())
    return out.strip("_") or "x"


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


__all__ = ["LogicConsistencyChecker"]
