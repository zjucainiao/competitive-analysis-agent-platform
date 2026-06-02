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

/** Cell width / row gap for auto-layout. */
const COL_WIDTH = 220;
const ROW_HEIGHT = 130;

/**
 * 拓扑分层：BFS depth-from-start。
 * 每个 depth 内同级节点按出现顺序水平排开。
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

  /* feedback children (parent_node_id ≠ null) override depth → 与 parent 同级 +0.5 */
  nodes.forEach((n) => {
    if (n.parent_node_id && depth[n.parent_node_id] != null) {
      depth[n.node_id] = depth[n.parent_node_id]! + 0.5;
    }
  });

  /* group by depth */
  const byDepth = new Map<number, DAGNode[]>();
  nodes.forEach((n) => {
    const d = depth[n.node_id] ?? 0;
    const arr = byDepth.get(d) ?? [];
    arr.push(n);
    byDepth.set(d, arr);
  });
  const sortedDepths = Array.from(byDepth.keys()).sort((a, b) => a - b);

  const pos: Record<string, { x: number; y: number }> = {};
  sortedDepths.forEach((d) => {
    const row = byDepth.get(d)!;
    row.forEach((n, i) => {
      pos[n.node_id] = {
        x: i * COL_WIDTH - ((row.length - 1) * COL_WIDTH) / 2,
        y: d * ROW_HEIGHT,
      };
    });
  });

  /* normalize: shift so min x is 60 */
  const allX = Object.values(pos).map((p) => p.x);
  const minX = allX.length > 0 ? Math.min(...allX) : 0;
  Object.values(pos).forEach((p) => {
    p.x -= minX - 60;
  });

  return pos;
}

/** node_id → 可读 label（fallback 到 node_id 本身） */
function nodeLabel(n: DAGNode): string {
  if (n.agent_name && n.node_id.includes(".")) {
    return n.node_id; // e.g. "collect.notion"
  }
  if (n.agent_name) {
    return n.node_id; // e.g. "reporter" / "reporter_v2"
  }
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

export function apiStateToDagData(state: ProjectStateResponse): {
  nodes: DagNodeRecord[];
  edges: DagEdgeRecord[];
} {
  const plan = state.plan;
  if (!plan) return { nodes: [], edges: [] };

  const pos = computeLayout(plan.nodes, plan.edges);

  const nodes: DagNodeRecord[] = plan.nodes.map((n) => {
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
    };
    return {
      id: n.node_id,
      position: pos[n.node_id] ?? { x: 0, y: 0 },
      data: outputToData(baseData, out),
    };
  });

  const edges: DagEdgeRecord[] = plan.edges.map((e) => ({
    id: e.edge_id,
    source: e.from_node,
    target: e.to_node,
    type: e.edge_type === "feedback" ? "feedback" : "dependency",
  }));

  return { nodes, edges };
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
