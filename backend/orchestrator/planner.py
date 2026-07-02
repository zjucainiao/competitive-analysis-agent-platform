"""Planner —— YAML 模板 → ``DAGPlan``。

v1 是模板加载器（不是 LLM Planner）。模板里允许出现两类扩展语法：

- ``for_each: products`` + 节点 id 内 ``{product}`` 占位符——按产品列表展开
- ``depends_on: [extract.*]`` 通配符——匹配同前缀展开后的节点

v2（``M5``）追加 ``AdaptiveLLMPlanner``，输出仍是 ``DAGPlan``，所以下游
``Executor`` / ``FeedbackRouter`` 不用感知 planner 类型变化。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from ulid import ULID

from backend.schemas import (
    DAGEdge,
    DAGNode,
    DAGPlan,
    NodeStatus,
    NodeType,
    Project,
)

_DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"

# industry_id → 模板文件名（docs/DAG.md § 4.2 锁定）
_INDUSTRY_TEMPLATE_MAP: dict[str, str] = {
    "collaboration_saas": "collab_saas_standard",
    "crm_saas": "crm_saas_standard",
    "cross_border_ecommerce_saas": "cross_border_standard",
    "edu_saas": "edu_saas_standard",
}


class TemplateNotFoundError(FileNotFoundError):
    """模板 id 找不到对应 YAML 文件。"""


class TemplateExpandError(ValueError):
    """模板解析阶段语法 / 引用错误。"""


def _slug(product_name: str) -> str:
    """与 ``backend.agents.*.fixtures._slug`` 对齐，保证节点 id 和 mock fixture 命名一致。"""
    return product_name.strip().lower().replace(" ", "_")


class Planner:
    """v1 模板加载器（默认）+ 自适应 Planner 转发。

    - ``plan(project)`` / ``plan(project, mode="template")``：YAML 模板加载，最稳
    - ``plan(project, mode="adaptive")``：``AdaptivePlanner`` 用 LLM 推断 URL +
      维度。需要在 ``__init__`` 传 ``llm`` 参数；未传时抛 ``RuntimeError``
    - ``plan(project, mode="auto")``：先试 adaptive，失败回退 template
    """

    def __init__(
        self,
        templates_dir: Path | None = None,
        *,
        llm: Any = None,
    ) -> None:
        self.templates_dir = templates_dir or _DEFAULT_TEMPLATES_DIR
        self._llm = llm

    # ----- 公开 API -----

    def plan(
        self,
        project: Project,
        *,
        template_id: str | None = None,
        mode: str = "template",
    ) -> DAGPlan:
        if mode == "adaptive":
            return self._plan_adaptive(project)
        if mode == "auto":
            try:
                return self._plan_adaptive(project)
            except Exception:
                # adaptive 失败兜回 template，DAG 不能因此卡住
                pass
        return self._plan_template(project, template_id=template_id)

    def _plan_template(
        self, project: Project, *, template_id: str | None = None
    ) -> DAGPlan:
        tid = template_id or self._default_template_id(project)
        raw = self._load_template(tid)
        return self._expand(raw, project, template_id=tid)

    def _plan_adaptive(self, project: Project) -> DAGPlan:
        if self._llm is None:
            raise RuntimeError(
                "Planner(mode='adaptive') requires llm to be passed at __init__"
            )
        from .adaptive_planner import AdaptivePlanner

        return AdaptivePlanner(llm=self._llm).plan(project)

    def list_templates(self) -> list[str]:
        if not self.templates_dir.exists():
            return []
        return sorted(p.stem for p in self.templates_dir.glob("*.yaml"))

    # ----- 内部 -----

    def _default_template_id(self, project: Project) -> str:
        # industry_id 与模板文件名不一定同名（见 _INDUSTRY_TEMPLATE_MAP）
        mapped = _INDUSTRY_TEMPLATE_MAP.get(project.industry)
        if mapped is not None:
            return mapped
        # 回退：直接拿 industry 当模板名（兼容未来新增 industry 时无需改 map）
        return f"{project.industry}_standard"

    def _load_template(self, template_id: str) -> dict[str, Any]:
        path = self.templates_dir / f"{template_id}.yaml"
        if not path.exists():
            raise TemplateNotFoundError(
                f"template {template_id!r} not found at {path}"
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TemplateExpandError(
                f"template {template_id!r} root must be a mapping, got {type(data).__name__}"
            )
        return data

    def _expand(
        self,
        raw: dict[str, Any],
        project: Project,
        *,
        template_id: str,
    ) -> DAGPlan:
        variables = self._resolve_variables(raw.get("variables", {}), project)
        products: list[str] = variables.get("products", [])
        if not products:
            raise TemplateExpandError(
                f"template {template_id!r}: products variable empty after resolution"
            )

        raw_nodes = raw.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise TemplateExpandError(
                f"template {template_id!r} must declare a non-empty 'nodes' list"
            )

        expanded: dict[str, dict[str, Any]] = {}
        for spec in raw_nodes:
            for_each = spec.get("for_each")
            if for_each is None:
                node_id = spec["id"]
                expanded[node_id] = dict(spec)
                continue

            if for_each not in variables:
                raise TemplateExpandError(
                    f"node {spec.get('id')!r}: for_each={for_each!r} "
                    f"not in variables {list(variables)}"
                )
            items = variables[for_each]
            if for_each != "products":
                raise TemplateExpandError(
                    f"v1 planner only supports for_each=products, got {for_each!r}"
                )
            for product in items:
                slug = _slug(product)
                concrete = {k: v for k, v in spec.items() if k != "for_each"}
                concrete["id"] = self._substitute(spec["id"], product=slug)
                concrete["depends_on"] = [
                    self._substitute(d, product=slug)
                    for d in spec.get("depends_on", [])
                ]
                # 业务字段：runtime 用 metadata.product 知道这个节点服务哪个产品
                concrete["_product"] = product
                expanded[concrete["id"]] = concrete

        # 解析通配 depends_on（必须在所有节点都已展开之后）
        for node_id, spec in expanded.items():
            resolved: list[str] = []
            for dep in spec.get("depends_on", []) or []:
                if dep.endswith(".*"):
                    prefix = dep[:-2] + "."
                    matches = sorted(
                        n for n in expanded if n.startswith(prefix)
                    )
                    if not matches:
                        raise TemplateExpandError(
                            f"node {node_id!r}: depends_on={dep!r} matches no nodes"
                        )
                    resolved.extend(matches)
                else:
                    if dep not in expanded:
                        raise TemplateExpandError(
                            f"node {node_id!r}: depends_on={dep!r} not found"
                        )
                    resolved.append(dep)
            spec["depends_on"] = resolved

        # 剪枝：PARALLEL_JOIN / PARALLEL_FORK 节点入边只有 1 个时是冗余 barrier，
        # 直接把它从 DAG 里抽掉（典型场景：single_research 模式下只有 1 个 product，
        # extract.{product} 之后的 join_extract 等同于直通）。
        # 用户在 DAG 视图看到一个名叫「join_extract」的孤儿节点容易误解为「联合抽取」业务步骤，
        # 实际上它只是 plumbing。
        _prune_trivial_barriers(expanded)

        # 落 DAGNode + DAGEdge
        nodes = [
            self._to_dag_node(spec, project, variables=variables)
            for spec in expanded.values()
        ]
        edges = []
        for spec in expanded.values():
            for dep in spec["depends_on"]:
                edge_id = f"{dep}->{spec['id']}"
                edges.append(
                    DAGEdge(
                        edge_id=edge_id,
                        from_node=dep,
                        to_node=spec["id"],
                        edge_type="dependency",
                    )
                )

        return DAGPlan(
            plan_id=f"plan_{ULID()}",
            project_id=project.project_id,
            template_id=raw.get("template_id", template_id),
            nodes=nodes,
            edges=edges,
            rationale=(
                f"Loaded from template '{template_id}'; "
                f"expanded for {len(products)} products: "
                f"{', '.join(products)}"
            ),
            confidence=1.0,
            complexity_score=min(1.0, len(nodes) / 20.0),
        )

    # ----- 工具 -----

    def _resolve_variables(
        self, raw_vars: dict[str, Any], project: Project
    ) -> dict[str, Any]:
        """把模板里 ``project.*`` 形式的引用替换成实际值。"""
        resolved: dict[str, Any] = {}
        for key, value in raw_vars.items():
            if isinstance(value, str) and value.startswith("project."):
                resolved[key] = self._resolve_project_ref(value, project)
            else:
                resolved[key] = value
        return resolved

    def _resolve_project_ref(self, ref: str, project: Project) -> Any:
        path = ref[len("project.") :]
        if path == "target_plus_competitors":
            # single_research 模式下 competitors=[]，整张 DAG 退化为只跑 target 一路
            # collector/extractor —— 已被 API 层 single_research 分支保证 competitors=[]。
            return [project.target_product, *project.competitors]
        if path == "competitors":
            return list(project.competitors)
        if path == "target_product":
            return [project.target_product]
        if path == "analysis_dimensions":
            return [d.value for d in project.analysis_dimensions]
        raise TemplateExpandError(f"unknown project ref: {ref!r}")

    def _substitute(self, text: str, *, product: str) -> str:
        return text.replace("{product}", product)

    def _to_dag_node(
        self,
        spec: dict[str, Any],
        project: Project,
        *,
        variables: dict[str, Any] | None = None,
    ) -> DAGNode:
        ntype = NodeType(spec["type"])
        metadata: dict[str, Any] = {}
        product = spec.get("_product")
        if product:
            metadata["product"] = product
        # collector 需要知道采哪些 dimension + 官网种子 URL
        if spec.get("agent") == "collector" and variables is not None:
            dims = variables.get("collect_dimensions")
            if dims:
                metadata["collect_dimensions"] = list(dims)
            # product_urls 是模板里硬编码的"已知竞品 → 官网"表；命中就喂给 Collector
            # 当种子，避开依赖外部搜索 API（Tavily/Serper）
            urls = variables.get("product_urls") or {}
            if product and product in urls:
                metadata["official_url"] = urls[product]
        return DAGNode(
            node_id=spec["id"],
            project_id=project.project_id,
            node_type=ntype,
            agent_name=spec.get("agent"),
            status=NodeStatus.PENDING,
            input_refs=list(spec.get("depends_on", [])),
            output_ref=None,
            retry_count=0,
            max_retries=int(spec.get("max_retries", 3)),
            timeout_ms=int(spec.get("timeout_ms", 60000)),
            started_at=None,
            ended_at=None,
            parent_node_id=None,
            revision=1,
            metadata=metadata,
        )


# 便于 quick smoke test
def _now() -> datetime:  # pragma: no cover
    return datetime.now(UTC)


def _prune_trivial_barriers(expanded: dict[str, dict[str, Any]]) -> None:
    """剪枝：PARALLEL_JOIN / PARALLEL_FORK 入边 ≤ 1 时直接抽掉（直通）。

    就地修改 ``expanded`` —— 删除冗余节点 + 把下游 depends_on 重写到该节点的上游。

    典型触发：single_research 模式下 ``join_extract`` 入边只有 ``extract.{target}``
    一个，按设计应等同 ``analyst depends_on [extract.{target}]``。

    保留 ``start`` / ``end`` 不剪 —— 它们是固定锚点，即使度数小也对 DAG 拓扑可读性
    有意义。
    """
    barrier_types = {"parallel_join", "parallel_fork"}
    # 多轮，让 barrier 链上的所有冗余 join 都被剥掉
    while True:
        pruned: list[str] = []
        for nid, spec in list(expanded.items()):
            if spec.get("type") not in barrier_types:
                continue
            deps = spec.get("depends_on", []) or []
            if len(deps) > 1:
                continue
            # 1 个上游 → 直接 bypass；0 个上游极少见（孤儿 barrier）也一并剪掉
            upstream = deps[0] if deps else None
            for _other_id, other_spec in expanded.items():
                other_deps = other_spec.get("depends_on", []) or []
                if nid not in other_deps:
                    continue
                # 替换 nid → upstream（若有），否则直接移除该 dep
                new_deps: list[str] = []
                for d in other_deps:
                    if d != nid:
                        new_deps.append(d)
                    elif upstream is not None and upstream not in new_deps:
                        new_deps.append(upstream)
                other_spec["depends_on"] = new_deps
            del expanded[nid]
            pruned.append(nid)
        if not pruned:
            return


__all__ = [
    "Planner",
    "TemplateExpandError",
    "TemplateNotFoundError",
]
