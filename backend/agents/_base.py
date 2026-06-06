"""BaseAgent：所有专职 Agent 的统一基类。

职责：
- Pydantic 输入 / 输出 schema 校验
- Trace 注入（trace_id / span_id / 开关 span）
- 异常捕获 + AgentError 归集
- 自评估强制：confidence < 0.6 必须填 self_critique
- 引用强制（Reporter 在子类实现 _post_validate）
- 度量统计（token / 耗时 / 成本）

I 窗口产出 LLMProvider / Tracer / ToolRegistry 的具体实现，本文件只声明
BaseAgent 依赖的最小 Protocol 接口，作为跨窗口约定。
"""

from __future__ import annotations

import threading
import time
import traceback
from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from typing import (
    Any,
    ClassVar,
    Generic,
    Iterator,
    Protocol,
    TypeVar,
    runtime_checkable,
)

from pydantic import BaseModel, ValidationError

from backend.schemas.agent_io import (
    AgentError,
    AgentInputBase,
    AgentOutputBase,
    AgentStatus,
)

# ---------- User prompt override (节点级人工介入) ----------
#
# Executor 在调度节点前，若 ``node.metadata['user_prompt_override']`` 非空就把它
# set 到这个 ContextVar；``_TrackingLLMWrapper.chat`` 在调用 LLM 前从 ContextVar
# 读出并 **prepend** 到 system prompt 顶部，让 LLM 在所有默认 prompt 之上优先服从
# 用户输入。退出节点（不论成功失败）reset 还原。
#
# 这是个跨 Agent 的人工介入兜底：用户 PATCH /nodes/{nid}/edit-prompt 后，无需
# 任何 Agent 子类配合即可生效；具体效果取决于 LLM 对 USER OVERRIDE 块的服从度。

_USER_PROMPT_OVERRIDE: ContextVar[str | None] = ContextVar(
    "_USER_PROMPT_OVERRIDE", default=None
)


def set_user_prompt_override(text: str | None):
    """Executor 在 invoke 前调用；返回 token 供 reset 使用。"""
    return _USER_PROMPT_OVERRIDE.set(text)


def reset_user_prompt_override(token) -> None:
    """invoke 结束（含异常）后必须调，避免污染后续节点。"""
    _USER_PROMPT_OVERRIDE.reset(token)


def _current_user_prompt_override() -> str | None:
    return _USER_PROMPT_OVERRIDE.get()

# ---------- Type variables ----------

TInput = TypeVar("TInput", bound=AgentInputBase)
TOutput = TypeVar("TOutput", bound=AgentOutputBase)


# ---------- Cross-window dependency protocols ----------
#
# 以下 Protocol 是 BaseAgent 对其他窗口产出的最小依赖声明。
# I 窗口的 LLMProvider / Tracer / ToolRegistry 实现只要在结构上满足这些
# Protocol（duck typing）即可，不需要继承。
#
# 如果 I 窗口的实际接口扩充了字段或方法，本文件不需要同步——上层调用方
# 会拿到具体类型，类型推断会更精确。


@runtime_checkable
class LLMProviderProtocol(Protocol):
    """LLM 调用的最小接口。"""

    def chat(
        self,
        *,
        system: str,
        messages: list[dict],
        response_format: type[BaseModel] | None = None,
        tools: list[Any] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> Any:
        """同步对话。返回值由实现决定（通常含 .parsed / .usage / .raw）。"""
        ...

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        ...


@runtime_checkable
class TracerProtocol(Protocol):
    """可观测 trace 的最小接口。"""

    def span(
        self,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        agent_name: str,
        agent_version: str,
        node_id: str | None = None,
    ) -> Any:
        """返回一个 context manager。

        进入时开启 span；退出时根据是否有异常更新状态并落库。
        实现 .set_output / .set_error / .add_llm_call / .add_tool_call 等方法。
        """
        ...


@runtime_checkable
class ToolRegistryProtocol(Protocol):
    """工具注册表的最小接口。"""

    def get(self, name: str) -> Any:
        ...

    def has(self, name: str) -> bool:
        ...


# ---------- Errors ----------


class AgentRunError(RuntimeError):
    """Agent 执行期未捕获错误的统一封装。"""

    def __init__(self, code: str, message: str, *, retriable: bool = True):
        super().__init__(message)
        self.code = code
        self.retriable = retriable


class SelfCritiqueRequiredError(ValueError):
    """confidence < 0.6 但 self_critique 为空时抛出。"""


# ---------- LLM 用量自动累加 ----------
#
# BaseAgent 把传入的 ``llm`` 包成 _TrackingLLMWrapper，每次 ``chat()`` 调用后
# 从返回值（``LLMResponse`` 或 dict-like）取 ``tokens_input/output`` 累加到
# 本次 ``invoke()`` 的 ``_LLMUsageCounter``，``invoke()`` 末尾把累加结果
# 回填到 ``AgentOutput``（仅当子类未自己填）。
#
# cost 估算走 ``backend.llm.pricing.estimate_cost``（懒 import 避免循环）。


class _LLMUsageCounter:
    """单次 ``invoke()`` 期内的 LLM 用量累加器。

    Agent 可能并行发起多个 LLM 调用（如 Reporter 并行生成章节 / entailment），
    ``add`` 的 read-modify-write 用锁保护。
    """

    __slots__ = ("tokens_input", "tokens_output", "cost_usd", "_lock")

    def __init__(self) -> None:
        self.tokens_input: int = 0
        self.tokens_output: int = 0
        self.cost_usd: float = 0.0
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.tokens_input = 0
            self.tokens_output = 0
            self.cost_usd = 0.0

    def add(self, *, tokens_in: int, tokens_out: int, cost: float) -> None:
        with self._lock:
            self.tokens_input += tokens_in
            self.tokens_output += tokens_out
            self.cost_usd += cost


class _TrackingLLMWrapper:
    """包装真实 LLM provider，``chat()`` 调用后把用量累加到 counter。

    其余属性透传给 ``inner``（如 ``model`` / ``embed`` / ``supports_json_mode``）。
    """

    def __init__(self, inner: Any, counter: _LLMUsageCounter) -> None:
        self._inner = inner
        self._counter = counter
        self._span: Any = None  # 由 BaseAgent.invoke 在进入 tracer span 后 attach

    def _attach_span(self, span: Any) -> None:
        self._span = span

    def _detach_span(self) -> None:
        self._span = None

    def chat(self, **kwargs: Any) -> Any:
        # 用户 prompt 覆盖：把 ContextVar 中的 override 文本 prepend 到 system，
        # 让 LLM 在默认指令之前先看到用户的人工调整。
        override = _current_user_prompt_override()
        if override:
            base_system = kwargs.get("system", "") or ""
            kwargs["system"] = (
                "## ⚠️ USER PROMPT OVERRIDE (highest priority)\n"
                f"{override.strip()}\n\n"
                "## Default system prompt below — apply only where not "
                "overridden above.\n\n"
                f"{base_system}"
            )
        started = time.monotonic()
        resp = self._inner.chat(**kwargs)
        duration_ms = int((time.monotonic() - started) * 1000)

        tokens_in = int(getattr(resp, "tokens_input", 0) or 0)
        tokens_out = int(getattr(resp, "tokens_output", 0) or 0)
        model = getattr(resp, "model", "") or getattr(self._inner, "model", "")
        cost = (
            _estimate_call_cost(model, tokens_in, tokens_out)
            if model and (tokens_in or tokens_out)
            else 0.0
        )
        if tokens_in or tokens_out:
            self._counter.add(tokens_in=tokens_in, tokens_out=tokens_out, cost=cost)

        # 推到当前 span（OTLPSpan 落 llm.chat 子 span；NullSpan 是 no-op）
        span = self._span
        if span is not None:
            adder = getattr(span, "add_llm_call", None)
            if callable(adder):
                try:
                    adder(
                        model=model,
                        system_prompt=kwargs.get("system", ""),
                        messages=kwargs.get("messages"),
                        response=getattr(resp, "content", None)
                        or getattr(resp, "parsed", None),
                        tokens_input=tokens_in,
                        tokens_output=tokens_out,
                        cost_usd=cost,
                        finish_reason=getattr(resp, "finish_reason", None),
                        duration_ms=duration_ms,
                    )
                except Exception:  # noqa: BLE001
                    # tracer 异常永远不阻塞主流程
                    pass
        return resp

    def embed(self, texts: list[str], **kwargs: Any) -> Any:
        # embed 暂不计 token（多数 provider 价格按文本量而非 token）
        return self._inner.embed(texts, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # 透传 inner 上的其他属性（model / supports_json_mode / from_env / ...）
        return getattr(self._inner, name)


def _estimate_call_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """惰性 import 价格表，避免 backend.agents._base ↔ backend.llm 循环。"""
    try:
        from backend.llm.pricing import estimate_cost
    except Exception:  # noqa: BLE001
        return 0.0
    return estimate_cost(model, tokens_in, tokens_out)


# ---------- BaseAgent ----------


class BaseAgent(ABC, Generic[TInput, TOutput]):
    """所有 Agent 的基类。

    子类必须设置:
        name: str                 # 'collector' / 'extractor' / ...
        version: str              # '1.0.0'
        input_model:  type[TInput]
        output_model: type[TOutput]
        required_tools: list[str] = []   # 声明依赖的工具名

    子类必须实现:
        _run(self, inp: TInput) -> TOutput

    可选覆盖:
        _run_mock(self, inp: TInput) -> TOutput
        _post_validate(self, out: TOutput, inp: TInput) -> None
    """

    # 子类必须覆盖这四个 ClassVar
    name: ClassVar[str] = "base"
    version: ClassVar[str] = "0.0.0"
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]
    required_tools: ClassVar[list[str]] = []

    # 自评估阈值
    SELF_CRITIQUE_THRESHOLD: ClassVar[float] = 0.6

    def __init__(
        self,
        *,
        llm: LLMProviderProtocol | None = None,
        tools: ToolRegistryProtocol | None = None,
        tracer: TracerProtocol | None = None,
        mock: bool = False,
    ) -> None:
        self.tools = tools
        self.tracer = tracer
        self.mock = mock

        # 每个 Agent 实例独占一个 counter；invoke() 进入时 reset，
        # 退出时回填到 AgentOutput.tokens_input/output/cost_usd（仅当子类未自填）。
        self._llm_counter = _LLMUsageCounter()

        # 把 llm 包成跟踪版；子类用 self.llm.chat(...) 时自动累加 token。
        # mock=True 且 llm=None 时不包。
        if llm is None:
            self.llm = None  # type: ignore[assignment]
        else:
            self.llm = _TrackingLLMWrapper(llm, self._llm_counter)  # type: ignore[assignment]

        if not mock:
            # 真实模式必须配齐依赖
            if llm is None:
                raise ValueError(f"{self.name}: llm provider required in non-mock mode")
            if tracer is None:
                raise ValueError(f"{self.name}: tracer required in non-mock mode")
            if tools is None and self.required_tools:
                raise ValueError(
                    f"{self.name}: tool registry required (needs {self.required_tools})"
                )

    # ----- 公共 invoke 入口 -----

    def invoke(
        self,
        inp: TInput | dict,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None = None,
        node_id: str | None = None,
    ) -> TOutput:
        """统一入口。负责输入校验、trace 注入、输出校验、自评估强制。"""

        # 1. 输入校验
        if isinstance(inp, dict):
            try:
                inp = self.input_model.model_validate(inp)  # type: ignore[assignment]
            except ValidationError as e:
                raise AgentRunError(
                    code="INPUT_INVALID",
                    message=f"Input validation failed: {e}",
                    retriable=False,
                ) from e
        if not isinstance(inp, self.input_model):
            raise AgentRunError(
                code="INPUT_INVALID",
                message=(
                    f"Expected {self.input_model.__name__}, "
                    f"got {type(inp).__name__}"
                ),
                retriable=False,
            )

        # 2. 开 span，跑 _run
        started = time.monotonic()
        errors: list[AgentError] = []

        # 本次 invoke 的 LLM 用量计数清零（同一 Agent 实例多次 invoke 不串号）
        self._llm_counter.reset()

        with self._open_span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            node_id=node_id,
        ) as span:
            # 让 LLM wrapper 把每次 chat 推到当前 span，自动注入子 span。
            # 退出 span 时 detach，防止 invoke 之间互相串号。
            if isinstance(self.llm, _TrackingLLMWrapper):
                self.llm._attach_span(span)
            try:
                if self.mock:
                    out = self._run_mock(inp)  # type: ignore[arg-type]
                else:
                    out = self._run(inp)  # type: ignore[arg-type]
            except AgentRunError as e:
                errors.append(
                    AgentError(
                        code=e.code,
                        message=str(e),
                        severity="error",
                        retriable=e.retriable,
                    )
                )
                out = self._build_failure_output(
                    inp=inp,
                    trace_id=trace_id,
                    span_id=span_id,
                    errors=errors,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                self._safe_set_error(span, e)
            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                errors.append(
                    AgentError(
                        code="UNEXPECTED",
                        message=f"{type(e).__name__}: {e}",
                        severity="fatal",
                        retriable=False,
                        details={"traceback": tb},
                    )
                )
                out = self._build_failure_output(
                    inp=inp,
                    trace_id=trace_id,
                    span_id=span_id,
                    errors=errors,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                self._safe_set_error(span, e)

            # 3. 输出校验
            if not isinstance(out, self.output_model):
                # 子类返回了错误类型 — 视为 fatal
                errors.append(
                    AgentError(
                        code="OUTPUT_TYPE_MISMATCH",
                        message=(
                            f"Expected {self.output_model.__name__}, "
                            f"got {type(out).__name__}"
                        ),
                        severity="fatal",
                        retriable=False,
                    )
                )
                out = self._build_failure_output(
                    inp=inp,
                    trace_id=trace_id,
                    span_id=span_id,
                    errors=errors,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            # 4. 自评估强制
            try:
                self._enforce_self_critique(out)
            except SelfCritiqueRequiredError as e:
                errors.append(
                    AgentError(
                        code="SELF_CRITIQUE_REQUIRED",
                        message=str(e),
                        severity="error",
                        retriable=False,
                    )
                )
                out.errors.extend(errors)
                out.status = AgentStatus.NEEDS_REWORK

            # 5. 子类业务级后置校验（如 Reporter 引用强制）
            try:
                self._post_validate(out, inp)
            except AgentRunError as e:
                errors.append(
                    AgentError(
                        code=e.code,
                        message=str(e),
                        severity="error",
                        retriable=e.retriable,
                    )
                )
                out.errors.extend(errors)
                out.status = AgentStatus.NEEDS_REWORK

            # 6. 注入基础字段（覆盖子类可能漏填的）
            out.agent_name = self.name
            out.agent_version = self.version
            out.trace_id = trace_id
            out.span_id = span_id
            if out.duration_ms == 0:
                out.duration_ms = int((time.monotonic() - started) * 1000)

            # 回填 LLM 用量：仅当子类没自己累加时才覆盖。
            # extractor 等显式累加 tokens 的子类会保留自己的值。
            if out.tokens_input == 0 and self._llm_counter.tokens_input > 0:
                out.tokens_input = self._llm_counter.tokens_input
            if out.tokens_output == 0 and self._llm_counter.tokens_output > 0:
                out.tokens_output = self._llm_counter.tokens_output
            if out.cost_usd == 0.0 and self._llm_counter.cost_usd > 0:
                out.cost_usd = self._llm_counter.cost_usd

            self._safe_set_output(span, out)

            # 清理 wrapper 上的 span 引用（防止下一次 invoke 之外的 chat 误用）
            if isinstance(self.llm, _TrackingLLMWrapper):
                self.llm._detach_span()

        return out  # type: ignore[return-value]

    # ----- 抽象 / 可选钩子 -----

    @abstractmethod
    def _run(self, inp: TInput) -> TOutput:
        """子类实现真实业务逻辑。"""

    def _run_mock(self, inp: TInput) -> TOutput:
        """Mock 模式。默认子类应当覆盖（return fixture）。"""
        raise NotImplementedError(
            f"{self.name}._run_mock not implemented. "
            "Override to support mock mode."
        )

    def _post_validate(self, out: TOutput, inp: TInput) -> None:
        """业务级后置校验。子类按需覆盖。

        示例：
            Reporter._post_validate 中检查每个事实性 ReportParagraph 是否
            有非空 evidence_ids（引用强制），缺失时抛 AgentRunError。
        """
        return None

    # ----- 工具方法 -----

    def _enforce_self_critique(self, out: TOutput) -> None:
        """confidence 低于阈值时强制 self_critique 非空。"""
        if out.confidence < self.SELF_CRITIQUE_THRESHOLD and not out.self_critique.strip():
            raise SelfCritiqueRequiredError(
                f"{self.name}: confidence={out.confidence:.2f} < "
                f"{self.SELF_CRITIQUE_THRESHOLD} requires non-empty self_critique"
            )

    def _build_failure_output(
        self,
        *,
        inp: TInput,
        trace_id: str,
        span_id: str,
        errors: list[AgentError],
        duration_ms: int,
    ) -> TOutput:
        """构造一个最小可序列化的失败输出。

        子类的 output_model 通常有业务必填字段（如 ReporterOutput.draft），
        这里通过 model_construct 跳过校验，仅填写基础字段。
        """
        base = dict(
            agent_name=self.name,
            agent_version=self.version,
            task_id=inp.task_id,
            trace_id=trace_id,
            span_id=span_id,
            status=AgentStatus.FAILED,
            confidence=0.0,
            self_critique=(
                "Execution failed; see errors. "
                + "; ".join(e.message for e in errors)
            )[:1000],
            tokens_input=0,
            tokens_output=0,
            cost_usd=0.0,
            duration_ms=duration_ms,
            errors=errors,
        )
        return self.output_model.model_construct(**base)  # type: ignore[return-value]

    @contextmanager
    def _open_span(
        self,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        node_id: str | None,
    ) -> Iterator[Any]:
        """打开 trace span。Mock 或无 tracer 时退化为空 context。"""
        if self.tracer is None:
            yield _NullSpan()
            return
        cm = self.tracer.span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            agent_name=self.name,
            agent_version=self.version,
            node_id=node_id,
        )
        with cm as span:
            yield span

    @staticmethod
    def _safe_set_output(span: Any, out: AgentOutputBase) -> None:
        """容错调用 span.set_output，避免 tracer 接口差异打断主流程。"""
        setter = getattr(span, "set_output", None)
        if callable(setter):
            try:
                setter(out)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _safe_set_error(span: Any, err: BaseException) -> None:
        setter = getattr(span, "set_error", None)
        if callable(setter):
            try:
                setter(err)
            except Exception:  # noqa: BLE001
                pass


class _NullSpan:
    """无 tracer 时的占位 span（仅用于 mock 模式下的单元测试）。"""

    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def set_output(self, _out: Any) -> None:
        pass

    def set_error(self, _err: Any) -> None:
        pass
