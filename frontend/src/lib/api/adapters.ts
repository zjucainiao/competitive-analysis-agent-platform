import type {
  AnyAgentOutput,
  CollectorOutput,
  ExtractorOutput,
  AnalystOutput,
  ReporterOutput,
  QAOutput,
  DAGNode,
  DAGEdge,
  NodeStatus,
  ProjectStateResponse,
  Evidence as ApiEvidence,
} from "./types";
import type {
  DagNodeRecord,
  DagEdgeRecord,
  DagNodeStatus,
  DagNodeData,
} from "@/lib/dag-mock";

/**
 * 后端 API → 前端展示层适配器。
 *
 * 这一层把 backend pydantic（snake_case · 业务字段） 翻译成现有 DAG / Report /
 * Trace / Evidence / Metrics 组件期望的 shape，避免大改既有 UI 组件签名。
 */

/* ── DAG plan + outputs → DagNodeRecord[] + DagEdgeRecord[] ─────────── */

const STATUS_MAP: Record<NodeStatus, DagNodeStatus> = {
  pending: "neutral",
  ready: "neutral",
  running: "running",
  success: "success",
  failed: "error",
  needs_rework: "rework",
  skipped: "neutral",
};

/** 横向布局：depth 决定 X，分支序号决定 Y。
 *  节点尺寸 280×190，间距按之留出 60-80px 通道走线。 */
const STAGE_GAP = 360;  // 同一管线相邻 stage 的 X 间距
const BRANCH_GAP = 240; // 同一 stage 内相邻并行分支的 Y 间距

/**
 * 拓扑分层：BFS depth-from-start。
 * 横向版：depth → X 偏移；同 depth 内节点按出现顺序在 Y 轴上排开。
 * feedback 子节点（parent_node_id ≠ null）放在 parent 同 X、Y 下移 BRANCH_GAP。
 */
function computeLayout(nodes: DAGNode[], edges: DAGEdge[]): Record<string, { x: number; y: number }> {
  const adj: Record<string, string[]> = {};
  const indeg: Record<string, number> = {};
  nodes.forEach((n) => {
    adj[n.node_id] = [];
    indeg[n.node_id] = 0;
  });
  edges.forEach((e) => {
    if (!adj[e.from_node]) adj[e.from_node] = [];
    adj[e.from_node].push(e.to_node);
    indeg[e.to_node] = (indeg[e.to_node] ?? 0) + 1;
  });

  /* depth via BFS from roots (indeg=0) */
  const depth: Record<string, number> = {};
  const queue: string[] = [];
  nodes.forEach((n) => {
    if ((indeg[n.node_id] ?? 0) === 0) {
      depth[n.node_id] = 0;
      queue.push(n.node_id);
    }
  });
  while (queue.length > 0) {
    const cur = queue.shift()!;
    const d = depth[cur] ?? 0;
    for (const next of adj[cur] ?? []) {
      const nd = d + 1;
      if (depth[next] == null || nd > depth[next]) {
        depth[next] = nd;
      }
      queue.push(next);
    }
  }

  /* feedback children (parent_node_id ≠ null) 用 parent depth + 0.5
   * 这样横向上 feedback 节点会落在 parent 与下一 stage 中间，避免和 parent 重叠 */
  nodes.forEach((n) => {
    if (n.parent_node_id && depth[n.parent_node_id] != null) {
      depth[n.node_id] = depth[n.parent_node_id]! + 0.5;
    }
  });

  /* group by depth → 决定 X */
  const byDepth = new Map<number, DAGNode[]>();
  nodes.forEach((n) => {
    const d = depth[n.node_id] ?? 0;
    const arr = byDepth.get(d) ?? [];
    arr.push(n);
    byDepth.set(d, arr);
  });
  const sortedDepths = Array.from(byDepth.keys()).sort((a, b) => a - b);

  /* feedback 节点放在分支行（Y 下移）：parent_node_id ≠ null → branch_offset +1 */
  const pos: Record<string, { x: number; y: number }> = {};
  sortedDepths.forEach((d) => {
    const col = byDepth.get(d)!;
    col.forEach((n, i) => {
      const isFeedback = n.parent_node_id !== null;
      pos[n.node_id] = {
        x: d * STAGE_GAP,
        y:
          i * BRANCH_GAP -
          ((col.length - 1) * BRANCH_GAP) / 2 +
          (isFeedback ? BRANCH_GAP * 1.5 : 0),
      };
    });
  });

  /* normalize: shift so min y is 60 (留出 sheet 关闭按钮空间) */
  const allY = Object.values(pos).map((p) => p.y);
  const minY = allY.length > 0 ? Math.min(...allY) : 0;
  Object.values(pos).forEach((p) => {
    p.y -= minY - 60;
  });

  return pos;
}

/** node_id → 可读 label。
 *
 * Agent 节点保留原 id（如 `collect.notion` / `reporter_v2`），用户可一眼定位。
 * Control 节点（START / END / PARALLEL_JOIN / PARALLEL_FORK）改成中文 plumbing 提示，
 * 避免用户把 `join_extract` 误读成「联合抽取」业务步骤。
 */
function nodeLabel(n: DAGNode): string {
  if (n.agent_name) {
    return n.node_id;
  }
  // control 节点：按 node_type 给中文短词
  const nt = n.node_type;
  if (nt === "start") return "开始";
  if (nt === "end") return "结束";
  if (nt === "parallel_join") return `汇合 · ${n.node_id}`;
  if (nt === "parallel_fork") return `分发 · ${n.node_id}`;
  return n.node_id;
}

function outputToData(
  base: DagNodeData,
  out: AnyAgentOutput | undefined
): DagNodeData {
  if (!out) return base;
  return {
    ...base,
    durationMs: out.duration_ms || base.durationMs,
    tokens:
      out.tokens_input || out.tokens_output
        ? { input: out.tokens_input, output: out.tokens_output }
        : base.tokens,
    costUsd: out.cost_usd ?? base.costUsd,
    confidence: out.confidence ?? base.confidence,
    selfCritique: out.self_critique || base.selfCritique,
    llmCalls: base.llmCalls,
    inputs: base.inputs,
    outputs: summarizeOutput(out),
  };
}

function summarizeOutput(out: AnyAgentOutput): DagNodeData["outputs"] {
  const result: { key: string; value: string }[] = [];
  if ("raw_sources" in out && Array.isArray((out as CollectorOutput).raw_sources)) {
    result.push({
      key: "raw_sources",
      value: `${(out as CollectorOutput).raw_sources.length} docs`,
    });
  }
  if ("evidences" in out && Array.isArray((out as ExtractorOutput).evidences)) {
    result.push({
      key: "evidences",
      value: `${(out as ExtractorOutput).evidences.length} minted`,
    });
  }
  if ("profile" in out && (out as ExtractorOutput).profile) {
    result.push({
      key: "profile",
      value: `CompetitorProfile(${(out as ExtractorOutput).profile.basic_info.name})`,
    });
  }
  if ("result" in out && (out as AnalystOutput).result) {
    const r = (out as AnalystOutput).result;
    const dims = Object.keys(r.dimensions ?? {});
    result.push({ key: "dimensions", value: `${dims.length} analyzed` });
  }
  if ("draft" in out && (out as ReporterOutput).draft) {
    const d = (out as ReporterOutput).draft;
    result.push({
      key: "draft",
      value: `ReportDraft v${d.version} · ${d.sections.length} sections`,
    });
  }
  if ("verdict" in out && (out as QAOutput).verdict) {
    const v = (out as QAOutput).verdict;
    result.push({
      key: "verdict",
      value: `${v.overall_status} · ${v.issues.length} issues`,
    });
    if (v.routing.length > 0) {
      result.push({
        key: "routing",
        value: v.routing.map((r) => `→ ${r.target_agent}`).join(", "),
      });
    }
  }
  return result;
}

/** DAG 视图把 control 节点（start / end / parallel_join / parallel_fork）
 *  从用户视野里完全隐藏 —— 它们只是 Orchestrator plumbing，用户视角看到
 *  的应该是 4 阶段流水线（采集与结构化 / 分析 / 撰写 / 质检）。
 *
 *  隐藏后的边重连：跨过 control 的路径要透传，比如
 *    extract.notion → join_extract → analyst  →→  extract.notion → analyst
 *    extract.clickup → join_extract → analyst →→  extract.clickup → analyst
 *
 *  这样多竞品下视觉变成「N 个 extract 直接 fan-in 到 analyst」，省一层 plumbing。
 *
 *  需要看完整拓扑的高级用户可以走 GET /api/projects/{id}/state 看原始 plan。
 */
const _CONTROL_NODE_TYPES = new Set([
  "start",
  "end",
  "parallel_join",
  "parallel_fork",
]);

function _isControlNode(n: DAGNode): boolean {
  return _CONTROL_NODE_TYPES.has(n.node_type);
}

/** 在边图上把所有 control 节点折叠掉：给定一组 source（business 节点），
 *  沿边 BFS 找到所有可达的非 control 节点。 */
function _businessReachable(
  fromIds: string[],
  edges: DAGEdge[],
  controlIds: Set<string>
): string[] {
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    if (!adj.has(e.from_node)) adj.set(e.from_node, []);
    adj.get(e.from_node)!.push(e.to_node);
  }
  const result: string[] = [];
  const seen = new Set<string>();
  const stack = [...fromIds];
  while (stack.length > 0) {
    const cur = stack.pop()!;
    if (seen.has(cur)) continue;
    seen.add(cur);
    if (!controlIds.has(cur)) {
      // 业务节点：作为目标返回，不再继续往下追
      result.push(cur);
      continue;
    }
    // control 节点：透传，继续找下游
    for (const next of adj.get(cur) ?? []) {
      stack.push(next);
    }
  }
  return result;
}

export function apiStateToDagData(state: ProjectStateResponse): {
  nodes: DagNodeRecord[];
  edges: DagEdgeRecord[];
} {
  const plan = state.plan;
  if (!plan) return { nodes: [], edges: [] };

  const controlIds = new Set(
    plan.nodes.filter(_isControlNode).map((n) => n.node_id)
  );
  const businessNodes = plan.nodes.filter((n) => !_isControlNode(n));

  // 边重连：跨过所有 control，业务节点之间直连。同 source 多 target 用 set 去重。
  const seenEdgeKeys = new Set<string>();
  const rewiredEdges: DagEdgeRecord[] = [];
  for (const e of plan.edges) {
    if (controlIds.has(e.from_node)) {
      // from 是 control → 由它的上游业务节点接管，这条边等会儿会通过别的入口被加
      continue;
    }
    // from 是业务节点；找下游可达的所有业务节点
    const targets = controlIds.has(e.to_node)
      ? _businessReachable([e.to_node], plan.edges, controlIds)
      : [e.to_node];
    for (const t of targets) {
      const key = `${e.from_node}→${t}`;
      if (seenEdgeKeys.has(key)) continue;
      seenEdgeKeys.add(key);
      rewiredEdges.push({
        id: `${e.edge_id}::${t}`,
        source: e.from_node,
        target: t,
        type: e.edge_type === "feedback" ? "feedback" : "dependency",
      });
    }
  }

  // 布局算法仍喂原始 plan（保持 control 节点在图里参与拓扑排序、给出业务节点合理坐标），
  // 渲染时只暴露业务节点。
  const pos = computeLayout(plan.nodes, plan.edges);

  const nodes: DagNodeRecord[] = businessNodes.map((n) => {
    const out = state.outputs[n.node_id];
    const baseData: DagNodeData = {
      label: nodeLabel(n),
      agent: n.agent_name ?? "control",
      status: STATUS_MAP[n.status] ?? "neutral",
      durationMs: null,
      tokens: null,
      costUsd: null,
      confidence: null,
      selfCritique: null,
      llmCalls: [],
      inputs: [],
      outputs: [],
      revision: n.revision,
      parentNodeId: n.parent_node_id,
      storyHint: n.metadata?.story_hint as string | undefined,
      errorMessage:
        (n.metadata?.error as { message?: string } | undefined)?.message ??
        null,
    };
    return {
      id: n.node_id,
      position: pos[n.node_id] ?? { x: 0, y: 0 },
      data: outputToData(baseData, out),
    };
  });

  return { nodes, edges: rewiredEdges };
}

/* ── Reporter outputs → 最新 reporter version (节点 id) ───────────────── */

export function findLatestReporter(
  outputs: Record<string, AnyAgentOutput>
): { nodeId: string; output: ReporterOutput } | null {
  let bestNodeId: string | null = null;
  let bestVersion = -1;
  for (const [nodeId, out] of Object.entries(outputs)) {
    if (!nodeId.startsWith("reporter")) continue;
    if (!("draft" in out)) continue;
    const v = (out as ReporterOutput).draft?.version ?? 0;
    if (v > bestVersion) {
      bestVersion = v;
      bestNodeId = nodeId;
    }
  }
  if (!bestNodeId) return null;
  return {
    nodeId: bestNodeId,
    output: outputs[bestNodeId] as ReporterOutput,
  };
}

/** 列出所有 reporter 版本节点（用于 v1↔v2 diff tab） */
export function listReporterVersions(
  outputs: Record<string, AnyAgentOutput>
): Array<{ nodeId: string; output: ReporterOutput }> {
  const items: Array<{ nodeId: string; output: ReporterOutput }> = [];
  for (const [nodeId, out] of Object.entries(outputs)) {
    if (!nodeId.startsWith("reporter")) continue;
    if (!("draft" in out)) continue;
    items.push({ nodeId, output: out as ReporterOutput });
  }
  return items.sort(
    (a, b) => (a.output.draft.version ?? 0) - (b.output.draft.version ?? 0)
  );
}

/* ── Aggregate evidences across extractor outputs ───────────────────── */

export function aggregateEvidences(
  outputs: Record<string, AnyAgentOutput>
): ApiEvidence[] {
  const seen = new Set<string>();
  const out: ApiEvidence[] = [];
  for (const o of Object.values(outputs)) {
    if (!("evidences" in o)) continue;
    const evs = (o as ExtractorOutput).evidences;
    if (!Array.isArray(evs)) continue;
    for (const e of evs) {
      if (seen.has(e.evidence_id)) continue;
      seen.add(e.evidence_id);
      out.push(e);
    }
  }
  return out;
}
