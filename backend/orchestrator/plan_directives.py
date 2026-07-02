"""plan_directives —— 从 DAGPlan 提取 native 引擎可消费的指令集。

修「native 引擎完全不消费 Planner 产物」的架构缺陷：API 每次 run 前都会
``orch.plan(project)`` 生成 DAGPlan（模板 Planner 带官网种子 product_urls；
AdaptivePlanner 真实烧一次 LLM 推断 official_url / 采集维度 / 每节点精调超时），
但 ``_run_native`` 过去通篇不用它——LLM 结果被扔掉、官网种子不生效（collector
永远走搜索兜底，是「抓错产品」身份问题的上游诱因）、改 planner 配置无效。

本模块把 plan 折叠成 **JSON-可序列化** 的最小指令集（进 RunState 走 checkpoint
serde 往返，只允许纯 dict/str/int/list）::

    {
        "products": {产品显示名: {"official_url": str|None, "collect_dims": [str,...]}},
        "nodes": {"collector": {"timeout_ms": int, "max_retries": int}, ...},
    }

兼容模板 plan 与 adaptive plan 两种产物：两者的 collector 节点 metadata 均带
``product``（显示名）/ ``collect_dimensions`` / ``official_url``；产品键取自
metadata.product，**不**靠 ``collect.<slug>`` 反推（slug 是小写化的展示名，不可逆）。

fail-soft：plan 为 None / 形状不符 / 提取中任何异常 → 返回空 dict，消费方全部
回退现状（缺省 dims / None URL / 下限表超时），不新增失败模式。
"""

from __future__ import annotations

import logging
from typing import Any

from backend.schemas.evidence import CollectDimension

_log = logging.getLogger(__name__)

# 各节点单次执行超时下限(ms)。这张表是「节点超时连锁失败」事故后精调的**下限**：
# collector 一个产品要 search + 抓多页 + 每页 page_type 分类 LLM + 身份校验 LLM
# （第三方噪音多的产品如 Figma 可达 100+ 次 LLM 调用），60s 必然撞超时 → 节点
# failed → 下游全 "upstream output missing" 连锁失败。plan 里低于此表的超时
# 一律钳到下限（见 extract_plan_directives），高于此表的值（如 adaptive 给
# reporter 的 600s）原样保留。
NODE_TIMEOUT_FLOOR_MS: dict[str, int] = {
    "collector": 300_000,
    "extractor": 300_000,
    "analyst": 240_000,
    "reporter": 240_000,
    "qa": 180_000,
}

# 无 plan 指令时的缺省重试次数（与 run_agent_node 的参数缺省一致，保持现状）。
_DEFAULT_MAX_RETRIES = 3


def _valid_dims(raw: Any) -> list[str]:
    """过滤出合法 CollectDimension 值（非法值直接丢掉并 debug 记录）。

    plan metadata 里的维度可能来自 LLM（adaptive）或人写模板；混入非法值会让
    ``build_collector_input`` 里的 ``CollectDimension(d)`` 抛 ValueError 崩节点，
    在提取层就挡掉。
    """
    dims: list[str] = []
    for d in raw or []:
        try:
            dims.append(CollectDimension(str(d).strip().lower()).value)
        except ValueError:
            _log.debug("plan_directives: dropping unknown collect dimension %r", d)
    return dims


def extract_plan_directives(plan: Any) -> dict[str, Any]:
    """DAGPlan → plan_directives 指令集；任何异常都 fail-soft 返回 ``{}``。"""
    if plan is None:
        return {}
    try:
        products: dict[str, dict[str, Any]] = {}
        nodes_cfg: dict[str, dict[str, int]] = {}

        for node in plan.nodes:
            agent = getattr(node, "agent_name", None)
            if not agent:
                continue  # start/end/join 等结构节点无 agent，跳过

            # ---- 超时/重试：低于下限的钳到下限，更高的值保留 ----
            timeout = int(getattr(node, "timeout_ms", 0) or 0)
            floor = NODE_TIMEOUT_FLOOR_MS.get(agent)
            if floor is not None:
                timeout = max(timeout, floor)
            retries = int(getattr(node, "max_retries", 0) or 0)
            # 同 agent 多节点(collect.a / collect.b)由同一 planner 配置、值一致；
            # 取 max 归并，保守防个别节点被配了过小值。
            cfg = nodes_cfg.setdefault(agent, {"timeout_ms": timeout, "max_retries": retries})
            cfg["timeout_ms"] = max(cfg["timeout_ms"], timeout)
            cfg["max_retries"] = max(cfg["max_retries"], retries)

            # ---- collector 节点：官网种子 + 采集维度（按产品显示名归档） ----
            if agent == "collector":
                meta = getattr(node, "metadata", None) or {}
                product = meta.get("product")
                if isinstance(product, str) and product:
                    url = meta.get("official_url")
                    products[product] = {
                        "official_url": url if isinstance(url, str) and url else None,
                        "collect_dims": _valid_dims(meta.get("collect_dimensions")),
                    }

        return {"products": products, "nodes": nodes_cfg}
    except Exception:
        # fail-soft：提取失败一切回退现状（缺省 dims / None URL / 下限表超时）。
        _log.debug("extract_plan_directives failed; falling back to defaults", exc_info=True)
        return {}


def resolve_node_limits(plan_directives: dict[str, Any] | None, agent: str) -> tuple[int, int]:
    """按优先级解析某 agent 的 (timeout_ms, max_retries)。

    plan_directives 里有合法值 → 用 plan 的（planner 精调）；缺省 → 回退
    ``NODE_TIMEOUT_FLOOR_MS`` 下限表 + 现行缺省重试次数（3），与消费 plan 之前
    的行为完全一致。注意超时语义不变：TimeoutError 直接 FAILED、不重试
    （见 run_agent_node，重试只作用于普通失败）。
    """
    cfg = ((plan_directives or {}).get("nodes") or {}).get(agent) or {}
    timeout = cfg.get("timeout_ms")
    if not isinstance(timeout, int) or timeout <= 0:
        timeout = NODE_TIMEOUT_FLOOR_MS.get(agent, 60_000)
    retries = cfg.get("max_retries")
    if not isinstance(retries, int) or retries < 0:
        retries = _DEFAULT_MAX_RETRIES
    return timeout, retries


__all__ = [
    "NODE_TIMEOUT_FLOOR_MS",
    "extract_plan_directives",
    "resolve_node_limits",
]
