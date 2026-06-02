"""Executor —— 单节点执行：input 解包 + 重试 + 超时 + 降级。

调用模式：

    executor = Executor(registry=..., project=..., trace_id=...)
    result = await executor.execute(node, outputs={...}, qa_feedback=None)

Executor 不感知 DAG 拓扑 / LangGraph state，仅按"一个节点 + 上游 outputs"工作；
拓扑调度由 ``backend.orchestrator.orchestrator.Orchestrator`` 负责。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from ulid import ULID

from backend.schemas import (
    AgentError,
    AgentInputBase,
    AgentOutputBase,
    AgentStatus,
    AnalystInput,
    CollectorInput,
    CompetitorProfile,
    DAGNode,
    Evidence,
    ExtractorInput,
    NodeExecutionResult,
    NodeStatus,
    NodeType,
    Project,
    QAInput,
    ReporterInput,
)
from backend.schemas.evidence import CollectDimension

from .agent_registry import AgentRegistry


# ---------- 常量 ----------

# 重试指数退避：1s, 4s, 16s（max_retries=3 默认值下覆盖三轮）
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MULT = 4

# 控制类节点（START / END / JOIN / FORK）在 executor 里直接 SUCCESS，无 agent 调用
_CONTROL_NODE_TYPES = frozenset(
    [
        NodeType.START,
        NodeType.END,
        NodeType.PARALLEL_JOIN,
        NodeType.PARALLEL_FORK,
    ]
)


class BuildInputError(ValueError):
    """无法从 Project + 上游 outputs 装配出 Agent input。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_span_id() -> str:
    return f"span_{ULID()}"


def _industry_schema_id(project: Project) -> str:
    """``collaboration_saas`` + ``1.0.0`` → ``collaboration_saas_v1``。"""
    major = project.industry_schema_version.split(".", 1)[0]
    return f"{project.industry}_v{major}"


def _profiles_from_outputs(
    outputs: dict[str, AgentOutputBase],
) -> dict[str, CompetitorProfile]:
    """从已完成节点 outputs 中收集所有 Extractor profile（key=product 显示名）。"""
    profiles: dict[str, CompetitorProfile] = {}
    for nid, out in outputs.items():
        if not nid.startswith("extract."):
            continue
        profile = getattr(out, "profile", None)
        if profile is None:
            continue
        profiles[profile.basic_info.name] = profile
    return profiles


class Executor:
    """按节点执行 Agent。无状态，可在多节点间复用。"""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        project: Project,
        trace_id: str | None = None,
        backoff_base: float = _BACKOFF_BASE_SECONDS,
    ) -> None:
        self.registry = registry
        self.project = project
        self.trace_id = trace_id or f"trace_{project.project_id}"
        self.backoff_base = backoff_base

    # ----- 公开入口 -----

    async def execute(
        self,
        node: DAGNode,
        outputs: dict[str, AgentOutputBase],
        *,
        qa_feedback: dict | None = None,
    ) -> NodeExecutionResult:
        """执行一个节点，返回 ``NodeExecutionResult``（含 status / output / errors）。"""
        if node.node_type in _CONTROL_NODE_TYPES:
            return self._control_result(node)

        if node.node_type == NodeType.AGENT_CALL:
            return await self._run_agent_with_retries(node, outputs, qa_feedback)

        # CONDITIONAL / FEEDBACK：v1 不在 Executor 里直接处理（feedback_router 负责）
        return NodeExecutionResult(
            project_id=node.project_id,
            node_id=node.node_id,
            status=NodeStatus.SKIPPED,
            trace_id=self.trace_id,
            started_at=_now(),
            ended_at=_now(),
            metadata={"reason": f"node_type={node.node_type.value} not handled by Executor"},
        )

    # ----- 核心：带重试 / 超时 / 降级 -----

    async def _run_agent_with_retries(
        self,
        node: DAGNode,
        outputs: dict[str, AgentOutputBase],
        qa_feedback: dict | None,
    ) -> NodeExecutionResult:
        started_at = _now()
        last_error: AgentError | None = None

        # 1. input 解包（不可重试）
        try:
            input_obj = self._build_input(node, outputs, qa_feedback)
        except BuildInputError as e:
            return self._failure_result(
                node,
                AgentError(
                    code="INPUT_BUILD_FAILED",
                    message=str(e),
                    severity="fatal",
                    retriable=False,
                ),
                started_at,
                attempts=0,
            )

        # Reporter / QA 需要把当前 outputs 里的 Evidence 汇总注入构造期
        agent = self._resolve_agent(node, outputs)

        # 设置 trace contextvar 让 LLM call 日志能关联回本节点
        from backend.agents._base import (
            reset_user_prompt_override,
            set_user_prompt_override,
        )
        from backend.observability.llm_call_log import (
            reset_trace_context,
            set_trace_context,
        )

        # 节点级 user_prompt_override：由 POST /nodes/{nid}/edit-prompt 注入到
        # node.metadata['user_prompt_override']。所有重试共用同一个 override。
        user_override = node.metadata.get("user_prompt_override")

        # 2. 重试循环
        for attempt in range(node.max_retries + 1):
            span_id = _new_span_id()
            ctx_token = set_trace_context(
                trace_id=self.trace_id,
                span_id=span_id,
                node_id=node.node_id,
                agent_name=node.agent_name,
            )
            override_token = set_user_prompt_override(user_override)
            try:
                try:
                    output = await asyncio.wait_for(
                        asyncio.to_thread(
                            agent.invoke,
                            input_obj,
                            trace_id=self.trace_id,
                            span_id=span_id,
                            node_id=node.node_id,
                        ),
                        timeout=node.timeout_ms / 1000.0,
                    )
                except asyncio.TimeoutError:
                    last_error = AgentError(
                        code="LLM_TIMEOUT",
                        message=(
                            f"node {node.node_id} attempt {attempt + 1} timed out "
                            f"after {node.timeout_ms}ms"
                        ),
                        severity="error",
                        retriable=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = AgentError(
                        code="UNEXPECTED",
                        message=f"{type(exc).__name__}: {exc}",
                        severity="error",
                        retriable=True,
                    )
                else:
                    # Agent 自带状态判断
                    if output.status == AgentStatus.FAILED:
                        first = output.errors[0] if output.errors else None
                        last_error = first or AgentError(
                            code="AGENT_FAILED",
                            message="agent returned FAILED status without errors",
                            severity="error",
                            retriable=True,
                        )
                        # 非可重试错误（如 INPUT_INVALID）立即终止重试
                        if not last_error.retriable:
                            return self._failure_result(
                                node, last_error, started_at, attempts=attempt + 1
                            )
                    else:
                        # SUCCESS / PARTIAL / NEEDS_REWORK 都视作"agent 跑完了"，由下游 QA / feedback 处理
                        return self._success_result(
                            node, output, started_at, attempts=attempt + 1
                        )
            finally:
                reset_user_prompt_override(override_token)
                reset_trace_context(ctx_token)

            # 还有重试次数则退避
            if attempt < node.max_retries:
                await asyncio.sleep(self.backoff_base * (_BACKOFF_MULT ** attempt))

        # 3. 全部重试用完 —— Collector 在 hybrid 模式下尝试降级到 mock
        if self._should_degrade_collector(node):
            try:
                degraded = await self._collector_fallback(node, input_obj)
                return self._success_result(
                    node, degraded, started_at, attempts=node.max_retries + 1, degraded=True
                )
            except Exception as exc:  # noqa: BLE001
                last_error = AgentError(
                    code="DEGRADATION_FAILED",
                    message=f"collector mock fallback failed: {exc}",
                    severity="fatal",
                    retriable=False,
                )

        return self._failure_result(
            node, last_error, started_at, attempts=node.max_retries + 1
        )

    def _resolve_agent(
        self, node: DAGNode, outputs: dict[str, AgentOutputBase]
    ):
        """为节点选择正确的 Agent 实例。

        Reporter / QA：每次新建，注入当前 outputs 汇总出的 Evidence DB（防止
        QA fallback 到 mock fixtures、Reporter 的数字校验跑空）。其他 agent
        走 registry 的缓存实例。
        """
        if node.agent_name == "reporter":
            from backend.agents.reporter.tools import StaticEvidenceProvider

            ev_db = self._collect_evidences(outputs)
            return self.registry.make_reporter(
                evidence_provider=StaticEvidenceProvider(ev_db)
            )
        if node.agent_name == "qa":
            ev_db = self._collect_evidences(outputs)
            return self.registry.make_qa(evidence_db=ev_db)
        return self.registry.get(node.agent_name)  # type: ignore[arg-type]

    @staticmethod
    def _collect_evidences(
        outputs: dict[str, AgentOutputBase],
    ) -> dict[str, Evidence]:
        """从所有 extract.* outputs 中汇总成 evidence_id -> Evidence dict。

        多个 Extractor 报相同 evidence_id 时取后写入者（实际不会冲突，因为
        evidence_id 跨产品全局唯一，按产品 + dimension + hash 派生）。
        """
        db: dict[str, Evidence] = {}
        for nid, out in outputs.items():
            if not nid.startswith("extract."):
                continue
            evidences = getattr(out, "evidences", None) or []
            for ev in evidences:
                db[ev.evidence_id] = ev
        return db

    def _should_degrade_collector(self, node: DAGNode) -> bool:
        return (
            node.agent_name == "collector"
            and self.project.mode == "hybrid"
            and self.project.collect_constraints.fallback_to_mock
        )

    async def _collector_fallback(
        self,
        node: DAGNode,
        input_obj: AgentInputBase,
    ) -> AgentOutputBase:
        from backend.agents.collector import Collector

        mock_agent = Collector(mock=True)
        return await asyncio.to_thread(
            mock_agent.invoke,
            input_obj,
            trace_id=self.trace_id,
            span_id=_new_span_id(),
            node_id=node.node_id,
        )

    # ----- input builders -----

    def _build_input(
        self,
        node: DAGNode,
        outputs: dict[str, AgentOutputBase],
        qa_feedback: dict | None,
    ) -> AgentInputBase:
        agent_name = node.agent_name
        if agent_name == "collector":
            return self._build_collector_input(node, qa_feedback)
        if agent_name == "extractor":
            return self._build_extractor_input(node, outputs, qa_feedback)
        if agent_name == "analyst":
            return self._build_analyst_input(node, outputs, qa_feedback)
        if agent_name == "reporter":
            return self._build_reporter_input(node, outputs, qa_feedback)
        if agent_name == "qa":
            return self._build_qa_input(node, outputs, qa_feedback)
        raise BuildInputError(
            f"node {node.node_id}: unknown agent_name={agent_name!r}"
        )

    def _build_collector_input(
        self, node: DAGNode, qa_feedback: dict | None
    ) -> CollectorInput:
        product = node.metadata.get("product")
        if not product:
            raise BuildInputError(
                f"node {node.node_id}: collector metadata missing 'product'"
            )
        dims = node.metadata.get("collect_dimensions") or []
        if not dims:
            raise BuildInputError(
                f"node {node.node_id}: collector metadata missing 'collect_dimensions'"
            )
        return CollectorInput(
            task_id=node.node_id,
            project_id=self.project.project_id,
            trace_id=self.trace_id,
            span_id=_new_span_id(),
            product_name=product,
            official_url=node.metadata.get("official_url"),
            industry=self.project.industry,
            dimensions=[CollectDimension(d) for d in dims],
            constraints=self.project.collect_constraints,
            qa_feedback=qa_feedback,
        )

    def _build_extractor_input(
        self,
        node: DAGNode,
        outputs: dict[str, AgentOutputBase],
        qa_feedback: dict | None,
    ) -> ExtractorInput:
        product = node.metadata.get("product")
        if not product:
            raise BuildInputError(
                f"node {node.node_id}: extractor metadata missing 'product'"
            )
        # 通过 input_refs 找上游 collector 输出
        upstream_id = next(iter(node.input_refs), None)
        if upstream_id is None or upstream_id not in outputs:
            raise BuildInputError(
                f"node {node.node_id}: missing upstream collector output "
                f"(input_refs={node.input_refs}, available={list(outputs)})"
            )
        collector_out = outputs[upstream_id]
        raw_sources = getattr(collector_out, "raw_sources", None)
        if raw_sources is None:
            raise BuildInputError(
                f"node {node.node_id}: upstream {upstream_id!r} has no raw_sources"
            )
        return ExtractorInput(
            task_id=node.node_id,
            project_id=self.project.project_id,
            trace_id=self.trace_id,
            span_id=_new_span_id(),
            product_name=product,
            industry_schema_id=_industry_schema_id(self.project),
            raw_sources=raw_sources,
            qa_feedback=qa_feedback,
        )

    def _build_analyst_input(
        self,
        node: DAGNode,
        outputs: dict[str, AgentOutputBase],
        qa_feedback: dict | None,
    ) -> AnalystInput:
        profiles = _profiles_from_outputs(outputs)
        if not profiles:
            raise BuildInputError(
                f"node {node.node_id}: no extractor outputs available"
            )
        return AnalystInput(
            task_id=node.node_id,
            project_id=self.project.project_id,
            trace_id=self.trace_id,
            span_id=_new_span_id(),
            target_product=self.project.target_product,
            competitors=list(self.project.competitors),
            profiles=profiles,
            dimensions=list(self.project.analysis_dimensions),
            qa_feedback=qa_feedback,
        )

    def _build_reporter_input(
        self,
        node: DAGNode,
        outputs: dict[str, AgentOutputBase],
        qa_feedback: dict | None,
    ) -> ReporterInput:
        analyst_out = self._latest_output(outputs, prefix_or_id="analyst")
        if analyst_out is None:
            raise BuildInputError(
                f"node {node.node_id}: missing analyst output"
            )
        return ReporterInput(
            task_id=node.node_id,
            project_id=self.project.project_id,
            trace_id=self.trace_id,
            span_id=_new_span_id(),
            project_name=self.project.project_name,
            analysis=analyst_out.result,  # type: ignore[attr-defined]
            template_id=self.project.report_template_id,
            output_format="markdown",
            target_audience=self.project.target_audience,
            qa_feedback=qa_feedback,
        )

    def _build_qa_input(
        self,
        node: DAGNode,
        outputs: dict[str, AgentOutputBase],
        qa_feedback: dict | None,
    ) -> QAInput:
        reporter_out = self._latest_output(outputs, prefix_or_id="reporter")
        analyst_out = self._latest_output(outputs, prefix_or_id="analyst")
        if reporter_out is None or analyst_out is None:
            raise BuildInputError(
                f"node {node.node_id}: missing reporter/analyst upstream output"
            )
        profiles = _profiles_from_outputs(outputs)
        prior_verdicts = self._prior_qa_verdicts(outputs, exclude_node_id=node.node_id)
        return QAInput(
            task_id=node.node_id,
            project_id=self.project.project_id,
            trace_id=self.trace_id,
            span_id=_new_span_id(),
            draft=reporter_out.draft,  # type: ignore[attr-defined]
            analysis=analyst_out.result,  # type: ignore[attr-defined]
            profiles=profiles,
            evidence_store_handle=None,
            prior_verdicts=prior_verdicts,
        )

    # ----- helpers -----

    @staticmethod
    def _latest_output(
        outputs: dict[str, AgentOutputBase], *, prefix_or_id: str
    ) -> AgentOutputBase | None:
        """取 ``prefix_or_id`` 与 ``prefix_or_id_v{n}`` 中 revision 最高的 output。

        feedback_router 派生 _v{n} 节点时，老节点输出（revision=1）仍留在 outputs，
        新节点输出（revision>1）写入 outputs；下游 builder 必须拿最新版本，否则
        QA 永远看不到重做后的 draft。
        """
        candidates: list[tuple[int, str]] = []
        if prefix_or_id in outputs:
            candidates.append((1, prefix_or_id))
        prefix = prefix_or_id + "_v"
        for nid in outputs:
            if not nid.startswith(prefix):
                continue
            try:
                rev = int(nid[len(prefix) :])
            except ValueError:
                continue
            candidates.append((rev, nid))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return outputs[candidates[0][1]]

    @staticmethod
    def _prior_qa_verdicts(
        outputs: dict[str, AgentOutputBase], *, exclude_node_id: str
    ) -> list:
        """收集所有 QA 节点的 verdict，用于防死循环。"""
        verdicts: list[Any] = []
        for nid, out in outputs.items():
            if nid == exclude_node_id:
                continue
            if not nid.startswith("qa"):
                continue
            verdict = getattr(out, "verdict", None)
            if verdict is not None:
                verdicts.append(verdict)
        return verdicts

    # ----- result builders -----

    def _control_result(self, node: DAGNode) -> NodeExecutionResult:
        now = _now()
        return NodeExecutionResult(
            project_id=node.project_id,
            node_id=node.node_id,
            status=NodeStatus.SUCCESS,
            output=None,
            trace_id=self.trace_id,
            started_at=now,
            ended_at=now,
            duration_ms=0,
        )

    def _success_result(
        self,
        node: DAGNode,
        output: AgentOutputBase,
        started_at: datetime,
        *,
        attempts: int,
        degraded: bool = False,
    ) -> NodeExecutionResult:
        ended_at = _now()
        # DAG.md § 2 约定：SUCCESS / PARTIAL / NEEDS_REWORK 三种 agent 状态对 DAG
        # 调度都视为"节点跑完了，输出可用"，统一映射到 NodeStatus.SUCCESS；
        # agent 的自评估意见保留在 output.status / self_critique，QA 节点会去看。
        node_status = NodeStatus.SUCCESS
        metadata: dict[str, Any] = {"attempts": attempts}
        if degraded:
            metadata["degraded"] = True
        if output.status == AgentStatus.PARTIAL:
            metadata["partial"] = True
        if output.status == AgentStatus.NEEDS_REWORK:
            metadata["needs_rework"] = True
        return NodeExecutionResult(
            project_id=node.project_id,
            node_id=node.node_id,
            status=node_status,
            output=output,
            trace_id=output.trace_id or self.trace_id,
            span_id=output.span_id,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=int((ended_at - started_at).total_seconds() * 1000),
            tokens_input=output.tokens_input,
            tokens_output=output.tokens_output,
            cost_usd=output.cost_usd,
            metadata=metadata,
        )

    def _failure_result(
        self,
        node: DAGNode,
        error: AgentError | None,
        started_at: datetime,
        *,
        attempts: int,
    ) -> NodeExecutionResult:
        ended_at = _now()
        return NodeExecutionResult(
            project_id=node.project_id,
            node_id=node.node_id,
            status=NodeStatus.FAILED,
            output=None,
            error=error,
            trace_id=self.trace_id,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=int((ended_at - started_at).total_seconds() * 1000),
            metadata={"attempts": attempts},
        )


__all__ = [
    "BuildInputError",
    "Executor",
]
