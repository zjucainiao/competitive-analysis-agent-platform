"""auto_discover 模式辅助接口。

``POST /api/discover-competitors`` 让 LLM 根据 target_product + industry 推荐
3-5 个常见竞品。前端 wizard 在用户选 ``analysis_mode=auto_discover`` 时调本接口，
把结果填进 competitors 输入框（允许用户编辑后再 POST /api/projects）。

设计：
- 不走 Agent / 不写 storage —— 只是一次轻量 LLM 调用，结果给前端做参考
- 在 API 层用 ``AgentRegistry.llm`` 拿 LLM provider，自己组 prompt
- 失败时返 200 + 空列表（带 ``error`` 字段），不影响前端兜底走手动输入
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.api.deps import get_agent_registry, get_current_user
from backend.orchestrator import AgentRegistry

router = APIRouter(tags=["discovery"])

_log = logging.getLogger(__name__)


class DiscoverCompetitorsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_product: str = Field(min_length=1, max_length=120)
    industry: str = Field(default="collaboration_saas")
    # 上限保护：避免 LLM 一次吐 20 个名字撑爆 wizard。
    max_competitors: int = Field(default=5, ge=1, le=8)


class DiscoveredCompetitor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str = Field(
        description="为什么把它列为竞品（一句话给用户做判断依据）"
    )
    official_url: str | None = None


class DiscoverCompetitorsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_product: str
    industry: str
    competitors: list[DiscoveredCompetitor]
    error: str | None = Field(
        default=None,
        description="LLM 失败时填错误信息，competitors 为空；前端兜底走手动输入",
    )


_DISCOVER_SYSTEM = """\
你是一个 B 端 SaaS 竞品研究助理。用户输入一个目标产品名 + 行业，
你要给出该产品在该行业内最直接的 3-5 个竞品。

约束：
1. 只列**真实存在**的产品（你确实知道的），**不要编造**
2. 必须是**同一品类**的直接竞品（功能 / 目标用户重合）
3. 提供 official_url 时必须是真实主域名（如 https://clickup.com），不要拼凑
4. 每个竞品写 1 句话 reason 说明为什么把它列为竞品（功能 / 用户群体 / 定价段位重合点）
5. 输出严格遵循 JSON Schema，不要 markdown 代码块、不要解释

如果你**真的不熟悉**该产品 / 行业，宁愿返回少于 3 个甚至空列表，也不要编造。
"""


_DISCOVER_USER_TEMPLATE = """\
目标产品: {target_product}
行业: {industry}
请最多输出 {max_competitors} 个直接竞品。
"""


class _DiscoverLLMResponse(BaseModel):
    """LLM 结构化输出 schema。"""

    model_config = ConfigDict(extra="ignore")  # LLM 可能多带字段，宽松接

    competitors: list[DiscoveredCompetitor]


@router.post(
    "/discover-competitors",
    response_model=DiscoverCompetitorsResponse,
)
async def discover_competitors(
    req: DiscoverCompetitorsRequest,
    registry: AgentRegistry = Depends(get_agent_registry),
    _user=Depends(get_current_user),
) -> DiscoverCompetitorsResponse:
    """调一次 LLM 推荐竞品。前端 auto_discover 模式专用。需登录（防匿名刷 LLM）。"""
    llm = registry.llm
    if llm is None:
        raise HTTPException(
            status_code=503,
            detail="LLM provider unavailable; auto-discover not usable",
        )

    user_prompt = _DISCOVER_USER_TEMPLATE.format(
        target_product=req.target_product,
        industry=req.industry,
        max_competitors=req.max_competitors,
    )

    try:
        # llm.chat 是同步阻塞客户端（底层 openai.OpenAI），一次调用数秒；
        # 挪进线程池执行，避免卡死整个事件循环（与 orchestrator run_agent 一致）。
        resp = await asyncio.to_thread(
            llm.chat,
            system=_DISCOVER_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            response_format=_DiscoverLLMResponse,
            temperature=0.2,
            max_tokens=800,
        )
    except Exception as e:
        _log.exception("discover-competitors LLM call failed: %s", e)
        return DiscoverCompetitorsResponse(
            target_product=req.target_product,
            industry=req.industry,
            competitors=[],
            error=f"LLM 调用失败：{type(e).__name__}: {e}",
        )

    parsed = _coerce(resp)
    if parsed is None:
        return DiscoverCompetitorsResponse(
            target_product=req.target_product,
            industry=req.industry,
            competitors=[],
            error="LLM 返回内容无法解析为 schema；请改用手动输入",
        )

    # 去掉 target_product 自己被列进去的情况（LLM 偶尔会犯）
    dedup_name = req.target_product.strip().lower()
    filtered = [
        c
        for c in parsed.competitors
        if c.name.strip().lower() != dedup_name
    ]
    # 限上限
    filtered = filtered[: req.max_competitors]

    return DiscoverCompetitorsResponse(
        target_product=req.target_product,
        industry=req.industry,
        competitors=filtered,
    )


def _coerce(resp: Any) -> _DiscoverLLMResponse | None:
    """把不同 LLM provider 返回值统一成 _DiscoverLLMResponse；失败返 None。"""
    if isinstance(resp, _DiscoverLLMResponse):
        return resp
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, _DiscoverLLMResponse):
        return parsed
    if isinstance(parsed, dict):
        try:
            return _DiscoverLLMResponse.model_validate(parsed)
        except Exception:
            return None
    # 兜底：从 content 字符串 JSON 解
    content = getattr(resp, "content", None) or getattr(resp, "text", None)
    if isinstance(content, str):
        try:
            data = json.loads(content)
            return _DiscoverLLMResponse.model_validate(data)
        except Exception:
            return None
    return None
