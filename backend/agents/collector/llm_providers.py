"""LLM Provider 实现（OpenAI 兼容，覆盖 OpenAI / DeepSeek / 豆包 Seed / Qwen 等）。

> v1 临时存放点。等架构 / 基础设施窗口产出 `backend/llm/` 通用层后，
> 把本文件迁过去并删除此处。

实现 ``LLMProviderProtocol``（见 ``backend/agents/_base.py``）::

    chat(system, messages, response_format=SomePydanticModel, ...) -> LLMResponse
        - .parsed 为 pydantic 实例或 None
        - .content 是模型原始文本（含或不含 tool_call 反序列化的 args）

## 结构化输出三层兜底（``response_format`` 非空时启用）

第 1 层（首选）—— ``tools`` + ``tool_choice="function"`` 强制函数调用
    schema 包成伪函数 ``submit_result``，服务端在 token 解码阶段
    约束输出必须匹配 schema。所有主流 provider 都支持，最稳。

第 2 层（兜底）—— 自由文本 + ``json-repair`` 容错解析
    若 provider 不支持 tool_choice 强制（少数老 endpoint 会 BadRequest），
    退回到"schema 注入 system prompt + 解析 content"。``json-repair`` 自动
    修复尾随逗号、未引号 key、markdown 代码块、单引号等常见 LLM 错误。

第 3 层（修复重试）—— 把上一次的错误响应带回去让模型自己改
    前两层都失败时，append assistant + user 纠错消息再调一次。
    最后还失败就返回 ``parsed=None``，由调用方按 LLM_SCHEMA_INVALID 处理。

``embed()`` v1 不支持。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from json_repair import repair_json
from openai import BadRequestError, OpenAI
from pydantic import BaseModel, ValidationError


_SCHEMA_TOOL_NAME = "submit_result"


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """委托给 ``backend.llm.pricing.estimate_cost``。

    函数内 lazy import 是为了切断 collector ↔ backend.llm 的循环：
    ``backend/llm/__init__.py`` 把 ``OpenAICompatibleLLM`` 从这里 re-export，
    模块顶部直接 ``from backend.llm.pricing import ...`` 会触发循环。
    """
    from backend.llm.pricing import estimate_cost

    return estimate_cost(model, tokens_in, tokens_out)

# ----- 每次 LLM 调用的可观测日志 -----
#
# 用法（在外部代码或 .env 里）::
#     logging.getLogger("backend.llm.calls").setLevel(logging.INFO)
#     # 默认 INFO 直接进 root logger，演示时 stdout 就能看到
#
# 日志格式（单行可 grep）::
#     [LLM] agent=collector model=ep-xxx phase=tool_call tokens=120/85
#           duration=1.42s cost~=$0.0012 finish=stop
#
# 价格表已迁出到 backend/llm/pricing.py（I 窗口产出）。
# 此处保留 `_estimate_cost` 的别名 import，调用点零改动。
# 新增模型 / 改价 / .env 覆盖（``LLM_PRICING_<MODEL>=in,out``）都在 pricing.py 处理。

_call_logger = logging.getLogger("backend.llm.calls")


def _log_llm_call(
    *,
    model: str,
    phase: str,
    tokens_in: int,
    tokens_out: int,
    duration_s: float,
    finish_reason: str | None,
    prompt_preview: str = "",
    response_preview: str = "",
) -> None:
    """打一行结构化 LLM 调用日志。

    - phase: ``tool_call`` / ``json_mode`` / ``freeform`` / ``retry`` — 标识三层兜底走的哪一层
    - prompt_preview: system + user 消息前 200 字符（默认空，避免泄漏 prompt 进生产日志）
    - cost 估算保留在 extra 字段，**不打印到 stdout**（演示侧不展示金额）
    """
    cost = _estimate_cost(model, tokens_in, tokens_out)
    _call_logger.info(
        "[LLM] phase=%s model=%s tokens=%d/%d duration=%.2fs finish=%s",
        phase,
        model,
        tokens_in,
        tokens_out,
        duration_s,
        finish_reason or "?",
        extra={
            "llm_phase": phase,
            "llm_model": model,
            "llm_tokens_input": tokens_in,
            "llm_tokens_output": tokens_out,
            "llm_duration_s": duration_s,
            "llm_cost_usd": cost,
            "llm_finish_reason": finish_reason,
            "llm_prompt_preview": prompt_preview,
        },
    )
    # 同时 push 到环形缓冲，让 HTTP /api/.../llm-calls 能拿到
    try:
        from backend.observability.llm_call_log import push_call

        push_call(
            model=model,
            phase=phase,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            duration_s=duration_s,
            finish_reason=finish_reason,
            cost_usd=cost,
            prompt_preview=prompt_preview,
            response_preview=response_preview,
        )
    except Exception:  # noqa: BLE001 — 观测层永远不能搞挂主流程
        pass


def _preview_messages(messages: list[dict[str, Any]], *, limit: int = 1200) -> str:
    """Compact prompt preview for UI logs.

    The full prompt can be very large. Keep enough context for debugging while
    avoiding huge ring-buffer entries.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(x) for x in content)
        parts.append(f"{role}: {str(content)}")
    text = "\n\n".join(parts).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _preview_text(text: str, *, limit: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


@dataclass
class LLMResponse:
    """统一返回结构。BaseAgent / Collector 通过 .parsed 字段取 pydantic 实例。

    ``cost_usd`` 由 provider 内部用 ``_estimate_cost(model, tokens_in, tokens_out)``
    填好，调用方直接读，不必再查价表 —— 让 Collector / Extractor / ... 累加
    成本时不再各自重算。pricing 表命中失败时为 0（豆包 EP 等走方舟控制台计费的场景）。
    """

    parsed: Any = None
    raw: Any = None
    content: str = ""
    model: str = ""
    finish_reason: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


@dataclass
class OpenAICompatibleLLM:
    """OpenAI / DeepSeek / 豆包 / 兼容协议的同步客户端。

    ``supports_json_mode``：是否在 L2 兜底里继续传 ``response_format={"type": "json_object"}``。
    OpenAI / DeepSeek 支持；火山方舟（豆包）不支持，会返回 400 InvalidParameter。
    """

    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout: float = 30.0
    default_temperature: float = 0.2
    default_max_tokens: int = 1024
    supports_json_mode: bool = True

    _client: OpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )

    # ---------- 工厂：从 .env / os.environ 选可用 provider ----------

    @classmethod
    def from_env(cls) -> "OpenAICompatibleLLM | None":
        """按 DOUBAO > DEEPSEEK > OPENAI 优先级选可用的 provider。都没有返回 None。

        豆包（火山方舟）：API key 通常是 EP（Endpoint）形式，挂在 `DOUBAO_API_KEY`；
        base_url 默认指向方舟北京区。**不支持** `response_format={"type": "json_object"}`，
        所以 `supports_json_mode=False`，结构化输出走 L1 tool_call + L3 修复重试。
        """
        doubao_key = os.getenv("DOUBAO_API_KEY")
        if doubao_key:
            return cls(
                api_key=doubao_key,
                base_url=os.getenv(
                    "DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
                ),
                model=os.getenv("DOUBAO_MODEL", "doubao-seed-1-6"),
                supports_json_mode=False,
            )
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        if deepseek_key:
            return cls(
                api_key=deepseek_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            )
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            return cls(
                api_key=openai_key,
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            )
        return None

    # ---------- 公开 chat 入口 ----------

    def chat(
        self,
        *,
        system: str,
        messages: list[dict],
        response_format: type[BaseModel] | None = None,
        tools: list[Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """对话入口。``response_format`` 非空时启用三层兜底；否则原样调用。"""
        if response_format is None:
            return self._call_api(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format_param=None,
                schema_cls=None,
            )

        # 调用方自己传了 tools（业务 function calling）就跳过 L1 的伪函数注入，
        # 直接走 L2/L3，避免和调用方意图冲突。
        if not tools:
            try:
                resp = self._chat_via_tool_call(
                    system=system,
                    messages=messages,
                    schema_cls=response_format,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if resp.parsed is not None:
                    return resp
                # tool_call 拿到了 args 但内容没通过 schema 校验 —— 直接走 L3 修复
            except _ToolCallUnsupported:
                pass  # provider 不支持，落到 L2

        # L2：schema 注入 prompt + json-repair
        resp = self._call_api(
            system=self._system_with_schema(system, response_format),
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format_param=(
                {"type": "json_object"} if self.supports_json_mode else None
            ),
            schema_cls=response_format,
        )
        if resp.parsed is not None:
            return resp

        # L3：把上一次的错误返回带回去，要求模型修
        retry_messages: list[dict[str, Any]] = list(messages)
        retry_messages.append({"role": "assistant", "content": resp.content or ""})
        retry_messages.append(
            {
                "role": "user",
                "content": (
                    "你刚才的回复无法被解析为有效 JSON。请严格只输出符合 schema 的"
                    " JSON 对象，不要任何 markdown 代码块、不要任何解释文字。"
                ),
            }
        )
        retry = self._call_api(
            system=self._system_with_schema(system, response_format),
            messages=retry_messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format_param=(
                {"type": "json_object"} if self.supports_json_mode else None
            ),
            schema_cls=response_format,
        )
        # 三层都失败时返回的 resp.parsed 仍是 None，调用方据此报 LLM_SCHEMA_INVALID
        return retry

    # ---------- LLMProviderProtocol.embed ----------

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """v1 阶段不支持。Collector 不用 embed；Extractor / Analyst 走专门的 embedding provider。"""
        raise NotImplementedError(
            "OpenAICompatibleLLM.embed not supported in v1 — wire a dedicated embedding provider"
        )

    # ============================================================
    # 内部：三层实现
    # ============================================================

    def _chat_via_tool_call(
        self,
        *,
        system: str,
        messages: list[dict],
        schema_cls: type[BaseModel],
        max_tokens: int | None,
        temperature: float | None,
    ) -> LLMResponse:
        """L1：用 tools + tool_choice="function" 强制模型产出 schema-合规 JSON。"""
        schema = schema_cls.model_json_schema()
        tool_spec = [
            {
                "type": "function",
                "function": {
                    "name": _SCHEMA_TOOL_NAME,
                    "description": (
                        f"Submit the final structured {schema_cls.__name__} result. "
                        "Arguments MUST match the provided schema exactly."
                    ),
                    "parameters": schema,
                },
            }
        ]
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ]
        full_messages.extend(messages)

        _t0 = time.monotonic()
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=tool_spec,
                tool_choice={
                    "type": "function",
                    "function": {"name": _SCHEMA_TOOL_NAME},
                },
                temperature=(
                    temperature if temperature is not None else self.default_temperature
                ),
                max_tokens=(
                    max_tokens if max_tokens is not None else self.default_max_tokens
                ),
            )
        except BadRequestError as e:
            # 服务端不支持强制 tool_choice / 不支持 tools / schema 过大等
            raise _ToolCallUnsupported(str(e)[:500]) from e

        _duration = time.monotonic() - _t0
        choice = completion.choices[0]
        tool_calls = getattr(choice.message, "tool_calls", None) or []
        usage = getattr(completion, "usage", None)
        _tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        _tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0

        args_json = (tool_calls[0].function.arguments or "") if tool_calls else ""
        _log_llm_call(
            model=self.model,
            phase="tool_call",
            tokens_in=_tokens_in,
            tokens_out=_tokens_out,
            duration_s=_duration,
            finish_reason=choice.finish_reason,
            prompt_preview=_preview_messages(full_messages),
            response_preview=_preview_text(args_json),
        )

        if not tool_calls:
            # 模型在强制 tool_choice 下竟然没产出 tool_call —— 罕见但有可能（小模型）
            raise _ToolCallUnsupported(
                "model returned no tool_calls under forced tool_choice"
            )

        parsed = _parse_with_repair(args_json, schema_cls)
        return LLMResponse(
            parsed=parsed,
            raw=completion,
            content=args_json,  # 把 args 字符串当 content 留给调用方调试
            model=completion.model,
            finish_reason=choice.finish_reason,
            tokens_input=_tokens_in,
            tokens_output=_tokens_out,
            cost_usd=_estimate_cost(completion.model, _tokens_in, _tokens_out),
        )

    def _call_api(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[Any] | None,
        max_tokens: int | None,
        temperature: float | None,
        response_format_param: dict[str, str] | None,
        schema_cls: type[BaseModel] | None,
    ) -> LLMResponse:
        """通用 chat.completions 调用 + json-repair 解析（L2 / L3 共用 / 无 schema 也走这）。"""
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ]
        full_messages.extend(messages)

        call_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
            "temperature": (
                temperature if temperature is not None else self.default_temperature
            ),
            "max_tokens": (
                max_tokens if max_tokens is not None else self.default_max_tokens
            ),
        }
        if response_format_param is not None:
            call_kwargs["response_format"] = response_format_param
        if tools:
            call_kwargs["tools"] = tools

        _t0 = time.monotonic()
        try:
            completion = self._client.chat.completions.create(**call_kwargs)
        except BadRequestError:
            # response_format 不被认时退一步：去掉它重试一次（不会无限递归）
            if response_format_param is not None:
                call_kwargs.pop("response_format")
                completion = self._client.chat.completions.create(**call_kwargs)
            else:
                raise
        _duration = time.monotonic() - _t0

        choice = completion.choices[0]
        content = choice.message.content or ""

        parsed: Any = None
        if schema_cls is not None and content:
            parsed = _parse_with_repair(content, schema_cls)

        usage = getattr(completion, "usage", None)
        _tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        _tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0
        # phase 区分 L2 (schema 注入 + json_mode) / freeform / L3 retry
        _phase = "json_mode" if response_format_param is not None else (
            "freeform_schema" if schema_cls is not None else "freeform"
        )
        _log_llm_call(
            model=self.model,
            phase=_phase,
            tokens_in=_tokens_in,
            tokens_out=_tokens_out,
            duration_s=_duration,
            finish_reason=choice.finish_reason,
            prompt_preview=_preview_messages(full_messages),
            response_preview=_preview_text(content),
        )
        return LLMResponse(
            parsed=parsed,
            raw=completion,
            content=content,
            model=completion.model,
            finish_reason=choice.finish_reason,
            tokens_input=_tokens_in,
            tokens_output=_tokens_out,
            cost_usd=_estimate_cost(completion.model, _tokens_in, _tokens_out),
        )

    @staticmethod
    def _system_with_schema(system: str, schema_cls: type[BaseModel]) -> str:
        """把 Pydantic schema 注入 system prompt（L2 / L3 兜底用）。"""
        schema = schema_cls.model_json_schema()
        return (
            f"{system}\n\n"
            "You must respond with a single valid JSON object that matches this schema. "
            "Output ONLY the JSON object — no markdown fences, no commentary.\n\n"
            f"JSON schema:\n{json.dumps(schema, ensure_ascii=False)}"
        )


# ============================================================
# 模块级 helpers
# ============================================================


class _ToolCallUnsupported(RuntimeError):
    """L1 失败信号：provider 不支持 tool_choice 强制、或模型没产 tool_call。"""


def _parse_with_repair(content: str, model: type[BaseModel]) -> Any:
    """容错解析：先剥代码块 → json.loads → 失败时 json-repair → 再 validate。"""
    if not content or not content.strip():
        return None
    text = content.strip()

    # 剥 ```json ... ``` 代码块
    if text.startswith("```"):
        lines = text.splitlines()
        # 去首行 ```json / ```
        lines = lines[1:]
        # 去末行 ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 第一次纯 json.loads
    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 第二次走 json-repair
        try:
            repaired = repair_json(text)
            if not repaired or repaired in ("{}", "[]", '""'):
                # repair_json 解析不出来时返回空骨架，视为失败
                return None
            data = json.loads(repaired)
        except Exception:  # noqa: BLE001
            return None

    if not isinstance(data, dict):
        return None

    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def _safe_parse(content: str, model: type[BaseModel]) -> Any:
    """旧入口名，转发到 _parse_with_repair 以保持向后兼容。"""
    return _parse_with_repair(content, model)


__all__ = ["LLMResponse", "OpenAICompatibleLLM"]
