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

import time
import traceback
from abc import ABC, abstractmethod
from contextlib import contextmanager
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
        self.llm = llm
        self.tools = tools
        self.tracer = tracer
        self.mock = mock

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

        with self._open_span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            node_id=node_id,
        ) as span:
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

            self._safe_set_output(span, out)

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
