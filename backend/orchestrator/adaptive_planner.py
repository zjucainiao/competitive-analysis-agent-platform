"""自适应 Planner —— 用一次 LLM 调用动态生成 DAGPlan。

跟 ``Planner`` 的 YAML 模板模式互补：

- ``Planner`` (template): 模板 + product_urls 硬编码，**已知行业 / 已知竞品**最稳
- ``AdaptivePlanner``    : LLM 推断每个产品的 ``official_url`` + 推断需采的
  dimension 子集，**陌生竞品 / 长尾行业**也能跑

最终输出仍是 ``DAGPlan``（同一个 schema），下游 Executor / FeedbackRouter 不感知
是哪种 planner 产的，完全互换。

设计要点（v1 简化）：

- 拓扑形状仍走标准 collab/CRM-like：``start → collect.* → extract.* → join →
  analyst → reporter → qa → end``。AdaptivePlanner 不创造新形状，只决定
  "采哪些维度 / 用什么 URL"，因为新形状会让前端可视化 / Executor 都需要
  适配，v1 不动这个边界。
- LLM 输出走 ``response_format`` 三层兜底（tool_call / json-repair / 修复重试），
  与其他 Agent 一致。
- 失败时不阻断：调用方可降级回 ``Planner`` template 路径。

调用入口（建议从 ``Planner.plan(project, mode="adaptive")`` 转发）::

    planner = AdaptivePlanner(llm=build_llm_from_env())
    plan = planner.plan(project)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from backend.schemas import (
    AnalysisDimension,
    DAGEdge,
    DAGNode,
    DAGPlan,
    NodeStatus,
    NodeType,
    Project,
)
from backend.schemas.evidence import CollectDimension

_log = logging.getLogger(__name__)


# ============================================================
# LLM 输出 schema
# ============================================================


class _AdaptiveProduct(BaseModel):
    """LLM 对每个产品的推断结果。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="产品名（与 Project 输入一致）")
    official_url: str | None = Field(
        default=None,
        description=(
            "产品官网 URL（https://）。LLM 不确定时填 null，Collector 会回退到搜索。"
        ),
    )
    notes: str = Field(default="", description="该产品的简短特征描述（10-50 字）")


class _AdaptivePlanOutput(BaseModel):
    """LLM 对整个 Project 的规划草图。"""

    model_config = ConfigDict(extra="ignore")

    rationale: str = Field(description="为什么这样规划：100-200 字")
    products: list[_AdaptiveProduct] = Field(
        description="target + competitors 全部列出，含推断的 official_url"
    )
    collect_dimensions: list[str] = Field(
        description=(
            "Collector 应该抓的维度。从这些里挑："
            "homepage / features / pricing / help_docs / user_reviews / "
            "changelog / customer_cases / blog"
        )
    )
    confidence: float = Field(ge=0, le=1)


# ============================================================
# AdaptivePlanner
# ============================================================


_DEFAULT_DIMENSIONS = (
    CollectDimension.HOMEPAGE,
    CollectDimension.FEATURES,
    CollectDimension.PRICING,
    CollectDimension.HELP_DOCS,
    CollectDimension.REVIEWS,
)


class AdaptivePlanner:
    """LLM-driven Planner。"""

    def __init__(self, llm: Any) -> None:
        if llm is None:
            raise ValueError("AdaptivePlanner requires an LLM provider")
        self.llm = llm

    def plan(self, project: Project) -> DAGPlan:
        """让 LLM 推断 URL + 维度，组装出 DAGPlan。"""
        plan_sketch = self._invoke_llm(project)

        # 把推断结果落到节点 metadata（与 template 模式同结构）
        products = self._merge_products(project, plan_sketch)
        dimensions = self._select_dimensions(plan_sketch)

        return self._assemble_plan(
            project=project,
            products=products,
            dimensions=dimensions,
            rationale=plan_sketch.rationale,
            confidence=plan_sketch.confidence,
        )

    # ----- LLM 调用 -----

    def _invoke_llm(self, project: Project) -> _AdaptivePlanOutput:
        system = (
            "你是竞品分析平台的 Planner Agent。给定一次分析项目的目标产品和竞品列表，"
            "你要：\n"
            "1) 为每个产品推断其官网 URL（https://...），不确定就填 null；\n"
            "2) 决定 Collector 该抓哪些 dimension（默认 homepage/features/pricing/"
            "help_docs/user_reviews，可按行业精简）；\n"
            "3) 用 ≤200 字写一段为什么这样规划。\n"
            "目标是让后续 Extractor / Analyst 拿到足够而不冗余的素材。"
        )
        user = (
            f"项目名：{project.project_name}\n"
            f"行业：{project.industry}\n"
            f"目标产品：{project.target_product}\n"
            f"竞品：{', '.join(project.competitors)}\n"
            f"分析维度：{', '.join(d.value for d in project.analysis_dimensions)}\n"
        )
        resp = self.llm.chat(
            system=system,
            messages=[{"role": "user", "content": user}],
            response_format=_AdaptivePlanOutput,
            max_tokens=1500,
            temperature=0.2,
        )
        if resp.parsed is None:
            raise RuntimeError(
                "AdaptivePlanner: LLM returned no parseable plan; "
                "fall back to template Planner"
            )
        return resp.parsed

    # ----- 翻译为 DAG -----

    def _merge_products(
        self, project: Project, sketch: _AdaptivePlanOutput
    ) -> list[dict[str, Any]]:
        """LLM 出的 products 必须覆盖 target + 所有 competitors；缺的补 None URL。"""
        wanted = [project.target_product, *project.competitors]
        by_name = {p.name: p for p in sketch.products}
        out = []
        for name in wanted:
            p = by_name.get(name)
            out.append(
                {
                    "name": name,
                    "official_url": p.official_url if p else None,
                    "notes": p.notes if p else "",
                }
            )
        return out

    def _select_dimensions(
        self, sketch: _AdaptivePlanOutput
    ) -> list[CollectDimension]:
        """把 LLM 输出的字符串映射回 CollectDimension enum；非法值丢掉。"""
        valid = []
        for d in sketch.collect_dimensions:
            try:
                valid.append(CollectDimension(d.strip().lower()))
            except ValueError:
                _log.warning("AdaptivePlanner: dropping unknown dimension %r", d)
        if not valid:
            _log.warning(
                "AdaptivePlanner: LLM produced no valid dimensions, using defaults"
            )
            return list(_DEFAULT_DIMENSIONS)
        return valid

    def _assemble_plan(
        self,
        *,
        project: Project,
        products: list[dict[str, Any]],
        dimensions: list[CollectDimension],
        rationale: str,
        confidence: float,
    ) -> DAGPlan:
        nodes: list[DAGNode] = []
        edges: list[DAGEdge] = []

        # start
        nodes.append(
            DAGNode(
                node_id="start",
                project_id=project.project_id,
                node_type=NodeType.START,
                agent_name=None,
                status=NodeStatus.PENDING,
            )
        )

        collect_ids: list[str] = []
        extract_ids: list[str] = []

        for p in products:
            slug = _slug(p["name"])
            collect_id = f"collect.{slug}"
            extract_id = f"extract.{slug}"
            collect_ids.append(collect_id)
            extract_ids.append(extract_id)

            nodes.append(
                DAGNode(
                    node_id=collect_id,
                    project_id=project.project_id,
                    node_type=NodeType.AGENT_CALL,
                    agent_name="collector",
                    status=NodeStatus.PENDING,
                    input_refs=["start"],
                    timeout_ms=180000,
                    max_retries=2,
                    metadata={
                        "product": p["name"],
                        "collect_dimensions": [d.value for d in dimensions],
                        "official_url": p["official_url"],
                        "notes": p["notes"],
                    },
                )
            )
            edges.append(
                DAGEdge(
                    edge_id=f"start->{collect_id}",
                    from_node="start",
                    to_node=collect_id,
                )
            )

            nodes.append(
                DAGNode(
                    node_id=extract_id,
                    project_id=project.project_id,
                    node_type=NodeType.AGENT_CALL,
                    agent_name="extractor",
                    status=NodeStatus.PENDING,
                    input_refs=[collect_id],
                    timeout_ms=600000,
                    max_retries=1,
                    metadata={"product": p["name"]},
                )
            )
            edges.append(
                DAGEdge(
                    edge_id=f"{collect_id}->{extract_id}",
                    from_node=collect_id,
                    to_node=extract_id,
                )
            )

        # join_extract
        nodes.append(
            DAGNode(
                node_id="join_extract",
                project_id=project.project_id,
                node_type=NodeType.PARALLEL_JOIN,
                agent_name=None,
                status=NodeStatus.PENDING,
                input_refs=extract_ids,
            )
        )
        for eid in extract_ids:
            edges.append(
                DAGEdge(
                    edge_id=f"{eid}->join_extract",
                    from_node=eid,
                    to_node="join_extract",
                )
            )

        # analyst / reporter / qa / end —— 标准串行
        for spec_id, agent, deps, timeout_ms, retries in (
            ("analyst", "analyst", ["join_extract"], 120000, 2),
            ("reporter", "reporter", ["analyst"], 120000, 2),
            ("qa", "qa", ["reporter"], 60000, 2),
        ):
            nodes.append(
                DAGNode(
                    node_id=spec_id,
                    project_id=project.project_id,
                    node_type=NodeType.AGENT_CALL,
                    agent_name=agent,
                    status=NodeStatus.PENDING,
                    input_refs=deps,
                    timeout_ms=timeout_ms,
                    max_retries=retries,
                )
            )
            for d in deps:
                edges.append(
                    DAGEdge(edge_id=f"{d}->{spec_id}", from_node=d, to_node=spec_id)
                )

        nodes.append(
            DAGNode(
                node_id="end",
                project_id=project.project_id,
                node_type=NodeType.END,
                agent_name=None,
                status=NodeStatus.PENDING,
                input_refs=["qa"],
            )
        )
        edges.append(DAGEdge(edge_id="qa->end", from_node="qa", to_node="end"))

        return DAGPlan(
            plan_id=f"adaptive_plan_{ULID()}",
            project_id=project.project_id,
            template_id=None,  # 标记 adaptive 出来的
            nodes=nodes,
            edges=edges,
            rationale=f"[adaptive] {rationale}",
            confidence=float(confidence),
            complexity_score=min(1.0, len(nodes) / 20.0),
        )


def _slug(product_name: str) -> str:
    return product_name.strip().lower().replace(" ", "_")


__all__ = ["AdaptivePlanner"]
