import type { StatusTone } from "@/components/layout/status-pill";

/**
 * DAG demo —— 7 agent · 3×3+1 矩形排版（对齐 AgentResearch 参考图）。
 *
 * 故事线：
 *   - 任务规划 已完成 → 信息采集 正在跑 (75%) → 证据入库 已完成
 *   - 结构化分析 正在跑 (60%)，下游 reporter / qa / end 等待中
 *   - 返工 dashed 边从 qa 回指 collector（视觉占位，第一轮 QA 尚未触发）
 *
 * Sprint 2 接 Orchestrator 后本文件退役为 fixture-only。
 */

export type DagNodeStatus = StatusTone;

export interface DagNodeData {
  label: string;
  agent: string;
  status: DagNodeStatus;
  durationMs: number | null;
  tokens: { input: number; output: number } | null;
  costUsd: number | null;
  confidence: number | null;
  selfCritique: string | null;
  llmCalls: Array<{
    model: string;
    durationMs: number;
    tokensIn: number;
    tokensOut: number;
  }>;
  inputs: Array<{ key: string; value: string }>;
  outputs: Array<{ key: string; value: string }>;
  revision: number;
  parentNodeId: string | null;
  storyHint?: string;
  /** 失败原因（来自 node.metadata.error，如超时）；非失败节点为 null。 */
  errorMessage?: string | null;
}

export interface DagNodeRecord {
  id: string;
  position: { x: number; y: number };
  data: DagNodeData;
}

export interface DagEdgeRecord {
  id: string;
  source: string;
  target: string;
  type: "dependency" | "feedback";
}

/* ── 3×3 + 1 网格坐标 ──────────────────────────────────────────────────
 *
 *   col0      col1      col2
 *  ┌──────┬─────────┬─────────┐
 *  │ 规划 │ 采集    │ 入库    │  row 0
 *  ├──────┼─────────┼─────────┤
 *  │ 分析 │ 报告    │ 质检    │  row 1
 *  ├──────┼─────────┼─────────┤
 *  │      │ 输出报告 │         │  row 2（中列单节点）
 *  └──────┴─────────┴─────────┘
 *
 * 节点 280×~180，间距 60 横 / 60 纵。
 */
const NODE_W = 280;
const NODE_H = 190;
const GAP_X = 60;
const GAP_Y = 60;

const COL0 = 0;
const COL1 = NODE_W + GAP_X;
const COL2 = (NODE_W + GAP_X) * 2;

const ROW0 = 0;
const ROW1 = NODE_H + GAP_Y;
const ROW2 = (NODE_H + GAP_Y) * 2;

const k = (n: number) => Math.round(n);

function llm(
  model: string,
  durationMs: number,
  tokensIn: number,
  tokensOut: number
) {
  return { model, durationMs, tokensIn, tokensOut };
}

/* ── nodes ─────────────────────────────────────────────────────────────── */

export const DEMO_DAG_NODES: DagNodeRecord[] = [
  // Row 0
  {
    id: "planner",
    position: { x: COL0, y: ROW0 },
    data: {
      label: "任务规划",
      agent: "planner",
      status: "success",
      durationMs: 83_000,
      tokens: { input: k(1840), output: k(620) },
      costUsd: 0.01,
      confidence: 1.0,
      selfCritique: null,
      llmCalls: [llm("doubao-pro", 4200, 1840, 620)],
      inputs: [
        { key: "input", value: "用户需求、竞品列表" },
      ],
      outputs: [
        { key: "next", value: "计划 18 个子任务" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "collector",
    position: { x: COL1, y: ROW0 },
    data: {
      label: "信息采集",
      agent: "collector",
      status: "running",
      durationMs: 452_000,
      tokens: { input: k(8240), output: k(2180) },
      costUsd: 0.04,
      confidence: 0,
      selfCritique: null,
      llmCalls: [llm("doubao-pro", 12_800, 8240, 2180)],
      inputs: [
        { key: "input", value: "爬取任务、网站列表" },
      ],
      outputs: [
        { key: "raw_sources", value: "12 篇文档 / 236 条证据" },
      ],
      revision: 1,
      parentNodeId: null,
      storyHint: "Notion / ClickUp / Asana 三家产品页 / 文档 / 评论站正在并发抓取",
    },
  },
  {
    id: "extractor",
    position: { x: COL2, y: ROW0 },
    data: {
      label: "证据入库",
      agent: "extractor",
      status: "success",
      durationMs: 138_000,
      tokens: { input: k(6420), output: k(3120) },
      costUsd: 0.05,
      confidence: 0.92,
      selfCritique: null,
      llmCalls: [llm("doubao-pro", 18_400, 6420, 3120)],
      inputs: [
        { key: "input", value: "原始数据、元数据" },
      ],
      outputs: [
        { key: "evidences", value: "236 条证据已入库" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  // Row 1
  {
    id: "analyst",
    position: { x: COL0, y: ROW1 },
    data: {
      label: "结构化分析",
      agent: "analyst",
      status: "running",
      durationMs: 341_000,
      tokens: { input: k(5240), output: k(1480) },
      costUsd: 0.03,
      confidence: 0.86,
      selfCritique: null,
      llmCalls: [llm("doubao-pro", 22_400, 5240, 1480)],
      inputs: [
        { key: "input", value: "证据数据" },
      ],
      outputs: [
        { key: "claims", value: "48 个结构化结论" },
      ],
      revision: 1,
      parentNodeId: null,
      storyHint: "维度对比矩阵正在生成；每条结论都绑定 evidence_id",
    },
  },
  {
    id: "reporter",
    position: { x: COL1, y: ROW1 },
    data: {
      label: "报告撰写",
      agent: "reporter",
      status: "neutral",
      durationMs: null,
      tokens: null,
      costUsd: null,
      confidence: 0,
      selfCritique: null,
      llmCalls: [],
      inputs: [
        { key: "input", value: "结构化结论、大纲" },
      ],
      outputs: [
        { key: "draft", value: "完整分析报告（草稿）" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "qa",
    position: { x: COL2, y: ROW1 },
    data: {
      label: "质量审查",
      agent: "qa",
      status: "neutral",
      durationMs: null,
      tokens: null,
      costUsd: null,
      confidence: 0,
      selfCritique: null,
      llmCalls: [],
      inputs: [
        { key: "input", value: "报告草稿、证据链" },
      ],
      outputs: [
        { key: "verdict", value: "质检结果、修改建议" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  // Row 2（中列单节点）
  {
    id: "end",
    position: { x: COL1, y: ROW2 },
    data: {
      label: "输出报告",
      agent: "end",
      status: "neutral",
      durationMs: null,
      tokens: null,
      costUsd: null,
      confidence: 0,
      selfCritique: null,
      llmCalls: [],
      inputs: [
        { key: "input", value: "最终报告、证据链" },
      ],
      outputs: [
        { key: "output", value: "竞品分析报告（最终版）" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
];

/* ── edges ─────────────────────────────────────────────────────────────── */

export const DEMO_DAG_EDGES: DagEdgeRecord[] = [
  // Row 0 横向
  { id: "e1", source: "planner", target: "collector", type: "dependency" },
  { id: "e2", source: "collector", target: "extractor", type: "dependency" },
  // Row 0 → Row 1（跨行下移）
  { id: "e3", source: "extractor", target: "analyst", type: "dependency" },
  // Row 1 横向
  { id: "e4", source: "analyst", target: "reporter", type: "dependency" },
  { id: "e5", source: "reporter", target: "qa", type: "dependency" },
  // Row 1 → Row 2（跨行下移）
  { id: "e6", source: "qa", target: "end", type: "dependency" },
  // 返工：质量审查 → 信息采集（dashed feedback）
  { id: "e7", source: "qa", target: "collector", type: "feedback" },
];

/* ── summary stats (toolbar header) ────────────────────────────────────── */

export interface DagRunSummary {
  totalNodes: number;
  completed: number;
  running: number;
  rework: number;
  pending: number;
  totalTokens: number;
  totalCostUsd: number;
  elapsedMs: number;
  qaRoundCount: number;
}

export function summarizeDag(nodes: DagNodeRecord[]): DagRunSummary {
  let completed = 0;
  let running = 0;
  let rework = 0;
  let pending = 0;
  let totalTokens = 0;
  let totalCostUsd = 0;
  let elapsedMs = 0;
  let qaRoundCount = 0;

  for (const n of nodes) {
    const s = n.data.status;
    if (s === "success") completed += 1;
    else if (s === "running") running += 1;
    else if (s === "rework") rework += 1;
    else pending += 1;

    if (n.data.tokens) {
      totalTokens += n.data.tokens.input + n.data.tokens.output;
    }
    if (n.data.costUsd) totalCostUsd += n.data.costUsd;
    if (n.data.durationMs) elapsedMs += n.data.durationMs;
    if (n.data.label.startsWith("qa") || n.data.agent === "qa") qaRoundCount += 1;
  }

  return {
    totalNodes: nodes.length,
    completed,
    running,
    rework,
    pending,
    totalTokens,
    totalCostUsd,
    elapsedMs,
    qaRoundCount,
  };
}
