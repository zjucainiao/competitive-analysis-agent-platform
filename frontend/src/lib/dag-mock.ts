import type { StatusTone } from "@/components/layout/status-pill";

/**
 * DAG demo snapshot for Sprint 1.
 *
 * 故事线：「Notion vs ClickUp vs Asana」run #03 正卡在 QA 反馈闭环上：
 *   - 第一轮 reporter 产出后 QA 检出 2 个 evidence 缺失 issue
 *   - QA 路由回 reporter，新建 reporter_v2 节点
 *   - reporter_v2 正在 running（带 pulse）
 *   - qa_v2 + end 还是 pending
 *
 * 这是评分项「反馈闭环真实可触发」的视觉证据，必须放在第一眼能看到的位置。
 *
 * Sprint 2 接 Orchestrator 的 WebSocket 流，本文件改成 fixture-only fallback。
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
  /** 节点在故事线里的角色，用于详情抽屉的副标题 */
  storyHint?: string;
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

/* ── layout coordinates ────────────────────────────────────────────────── */

const COL_NOTION = 60;
const COL_CLICKUP = 280;
const COL_ASANA = 500;
const COL_CENTER = 280;
const COL_V2 = 500;

const ROW_START = 0;
const ROW_COLLECT = 110;
const ROW_EXTRACT = 230;
const ROW_ANALYST = 360;
const ROW_REPORTER = 480;
const ROW_QA = 600;
const ROW_END = 720;

/* ── helpers ───────────────────────────────────────────────────────────── */

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
  {
    id: "start",
    position: { x: COL_CENTER, y: ROW_START },
    data: {
      label: "start",
      agent: "control",
      status: "success",
      durationMs: 12,
      tokens: null,
      costUsd: 0,
      confidence: 1,
      selfCritique: null,
      llmCalls: [],
      inputs: [{ key: "project_id", value: "demo" }],
      outputs: [{ key: "next", value: "collect ×3" }],
      revision: 1,
      parentNodeId: null,
      storyHint: "DAG 入口控制节点",
    },
  },
  {
    id: "collect.notion",
    position: { x: COL_NOTION, y: ROW_COLLECT },
    data: {
      label: "collect:notion",
      agent: "collector",
      status: "success",
      durationMs: 4200,
      tokens: { input: k(1234), output: 421 },
      costUsd: 0.018,
      confidence: 0.92,
      selfCritique: "4 dimensions covered with high-authority sources",
      llmCalls: [llm("claude-sonnet-4-6", 1432, 1234, 421)],
      inputs: [
        { key: "product", value: "Notion" },
        { key: "dimensions", value: "homepage / pricing / docs / reviews" },
      ],
      outputs: [
        { key: "raw_sources", value: "8 docs" },
        { key: "evidences", value: "12 minted" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "collect.clickup",
    position: { x: COL_CLICKUP, y: ROW_COLLECT },
    data: {
      label: "collect:clickup",
      agent: "collector",
      status: "success",
      durationMs: 3800,
      tokens: { input: k(1102), output: 380 },
      costUsd: 0.016,
      confidence: 0.94,
      selfCritique: "4 dimensions covered",
      llmCalls: [llm("claude-sonnet-4-6", 1380, 1102, 380)],
      inputs: [
        { key: "product", value: "ClickUp" },
        { key: "dimensions", value: "homepage / pricing / docs / reviews" },
      ],
      outputs: [
        { key: "raw_sources", value: "7 docs" },
        { key: "evidences", value: "11 minted" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "collect.asana",
    position: { x: COL_ASANA, y: ROW_COLLECT },
    data: {
      label: "collect:asana",
      agent: "collector",
      status: "success",
      durationMs: 5100,
      tokens: { input: k(1310), output: 460 },
      costUsd: 0.019,
      confidence: 0.9,
      selfCritique:
        "user_reviews dim 仅采集到 2 条高权威源，confidence 略低",
      llmCalls: [llm("claude-sonnet-4-6", 1610, 1310, 460)],
      inputs: [
        { key: "product", value: "Asana" },
        { key: "dimensions", value: "homepage / pricing / docs / reviews" },
      ],
      outputs: [
        { key: "raw_sources", value: "9 docs" },
        { key: "evidences", value: "13 minted" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "extract.notion",
    position: { x: COL_NOTION, y: ROW_EXTRACT },
    data: {
      label: "extract:notion",
      agent: "extractor",
      status: "success",
      durationMs: 12400,
      tokens: { input: k(4842), output: 916 },
      costUsd: 0.061,
      confidence: 0.87,
      selfCritique:
        "全部必填字段已填充；user_feedback.review_count 来自二手数据，标 unverified",
      llmCalls: [
        llm("claude-sonnet-4-6", 4231, 1820, 432),
        llm("claude-sonnet-4-6", 4012, 1640, 280),
        llm("claude-sonnet-4-6", 4157, 1382, 204),
      ],
      inputs: [
        { key: "raw_sources", value: "8 docs (collect:notion)" },
        { key: "industry_schema", value: "collaboration_saas_v1" },
      ],
      outputs: [
        { key: "profile", value: "CompetitorProfile(Notion)" },
        { key: "evidences", value: "12 linked" },
        { key: "field_status", value: "10 verified · 2 unverified" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "extract.clickup",
    position: { x: COL_CLICKUP, y: ROW_EXTRACT },
    data: {
      label: "extract:clickup",
      agent: "extractor",
      status: "success",
      durationMs: 11900,
      tokens: { input: k(4612), output: 882 },
      costUsd: 0.058,
      confidence: 0.89,
      selfCritique: "所有字段 verified",
      llmCalls: [
        llm("claude-sonnet-4-6", 4012, 1742, 392),
        llm("claude-sonnet-4-6", 3920, 1580, 290),
        llm("claude-sonnet-4-6", 3968, 1290, 200),
      ],
      inputs: [{ key: "raw_sources", value: "7 docs" }],
      outputs: [
        { key: "profile", value: "CompetitorProfile(ClickUp)" },
        { key: "evidences", value: "11 linked" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "extract.asana",
    position: { x: COL_ASANA, y: ROW_EXTRACT },
    data: {
      label: "extract:asana",
      agent: "extractor",
      status: "success",
      durationMs: 13200,
      tokens: { input: k(5104), output: 970 },
      costUsd: 0.064,
      confidence: 0.85,
      selfCritique:
        "industry_extension.team_permission 字段在 raw_text 中未找到，填 null",
      llmCalls: [
        llm("claude-sonnet-4-6", 4421, 1920, 412),
        llm("claude-sonnet-4-6", 4302, 1730, 320),
        llm("claude-sonnet-4-6", 4477, 1454, 238),
      ],
      inputs: [{ key: "raw_sources", value: "9 docs" }],
      outputs: [
        { key: "profile", value: "CompetitorProfile(Asana)" },
        { key: "evidences", value: "13 linked" },
        { key: "field_status", value: "9 verified · 4 unknown" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "analyst",
    position: { x: COL_CENTER, y: ROW_ANALYST },
    data: {
      label: "analyst",
      agent: "analyst",
      status: "success",
      durationMs: 16800,
      tokens: { input: k(6512), output: 1820 },
      costUsd: 0.124,
      confidence: 0.86,
      selfCritique:
        "覆盖 3 维度；feature 维度有 1 条 counter_evidence 体现严谨",
      llmCalls: [
        llm("claude-sonnet-4-6", 5612, 2104, 612),
        llm("claude-sonnet-4-6", 5410, 2208, 604),
        llm("claude-sonnet-4-6", 5778, 2200, 604),
      ],
      inputs: [
        { key: "profiles", value: "Notion / ClickUp / Asana" },
        { key: "dimensions", value: "feature / pricing / swot" },
      ],
      outputs: [
        { key: "result", value: "AnalysisResult · 7 claims" },
        { key: "evidence pool", value: "36 ids referenced" },
      ],
      revision: 1,
      parentNodeId: null,
    },
  },
  {
    id: "reporter",
    position: { x: COL_CENTER, y: ROW_REPORTER },
    data: {
      label: "reporter",
      agent: "reporter",
      status: "success",
      durationMs: 18600,
      tokens: { input: k(8412), output: 2104 },
      costUsd: 0.184,
      confidence: 0.84,
      selfCritique:
        "所有量化段落与 evidence 字面一致；overview 段为软结论",
      llmCalls: [
        llm("claude-sonnet-4-6", 4220, 2304, 612),
        llm("claude-sonnet-4-6", 4530, 2104, 580),
        llm("claude-sonnet-4-6", 4612, 2210, 510),
        llm("claude-sonnet-4-6", 5238, 1794, 402),
      ],
      inputs: [
        { key: "analysis", value: "AnalysisResult · 7 claims" },
        { key: "template_id", value: "standard_v1" },
      ],
      outputs: [
        { key: "draft", value: "ReportDraft v1 · 4 sections" },
      ],
      revision: 1,
      parentNodeId: null,
      storyHint: "第一版报告 · QA 检出 2 个 evidence 缺失",
    },
  },
  {
    id: "qa",
    position: { x: COL_CENTER, y: ROW_QA },
    data: {
      label: "qa",
      agent: "qa",
      status: "rework",
      durationMs: 8200,
      tokens: { input: k(7812), output: 612 },
      costUsd: 0.096,
      confidence: 0.93,
      selfCritique:
        "发现 2 处段落 evidence 缺失，已路由回 Reporter（must_address: p_sw_01, p_pr_02）",
      llmCalls: [
        llm("claude-sonnet-4-6", 1620, 1320, 102),
        llm("claude-sonnet-4-6", 1432, 1280, 92),
        llm("claude-sonnet-4-6", 1502, 1340, 110),
        llm("claude-sonnet-4-6", 1418, 1260, 96),
        llm("claude-sonnet-4-6", 1228, 1304, 102),
        llm("claude-sonnet-4-6", 1000, 1308, 110),
      ],
      inputs: [
        { key: "draft", value: "ReportDraft v1" },
        { key: "prior_verdicts", value: "0" },
      ],
      outputs: [
        { key: "verdict", value: "needs_revision · 2 issues" },
        { key: "routing", value: "→ reporter (must_address × 2)" },
        { key: "blocking", value: "true" },
      ],
      revision: 1,
      parentNodeId: null,
      storyHint: "质检失败 · 触发反馈闭环",
    },
  },
  {
    id: "reporter_v2",
    position: { x: COL_V2, y: ROW_REPORTER },
    data: {
      label: "reporter_v2",
      agent: "reporter",
      status: "running",
      durationMs: null,
      tokens: { input: k(3210), output: 442 },
      costUsd: 0.04,
      confidence: 0,
      selfCritique: null,
      llmCalls: [llm("claude-sonnet-4-6", 4220, 3210, 442)],
      inputs: [
        { key: "analysis", value: "AnalysisResult · 7 claims" },
        { key: "qa_feedback", value: "2 issues · must_address × 2" },
        { key: "template_id", value: "standard_v1" },
        { key: "parent", value: "reporter (v1)" },
      ],
      outputs: [{ key: "draft", value: "ReportDraft v2 (generating…)" }],
      revision: 2,
      parentNodeId: "reporter",
      storyHint: "Orchestrator 根据 QA routing 重建的版本，正按 must_address 重写",
    },
  },
  {
    id: "qa_v2",
    position: { x: COL_V2, y: ROW_QA },
    data: {
      label: "qa_v2",
      agent: "qa",
      status: "neutral",
      durationMs: null,
      tokens: null,
      costUsd: null,
      confidence: 0,
      selfCritique: null,
      llmCalls: [],
      inputs: [
        { key: "draft", value: "ReportDraft v2 (waiting)" },
        { key: "prior_verdicts", value: "1" },
      ],
      outputs: [],
      revision: 2,
      parentNodeId: "qa",
    },
  },
  {
    id: "end",
    position: { x: COL_CENTER + (COL_V2 - COL_CENTER) / 2, y: ROW_END },
    data: {
      label: "end",
      agent: "control",
      status: "neutral",
      durationMs: null,
      tokens: null,
      costUsd: null,
      confidence: 0,
      selfCritique: null,
      llmCalls: [],
      inputs: [{ key: "verdict", value: "(待 qa_v2 通过)" }],
      outputs: [],
      revision: 1,
      parentNodeId: null,
    },
  },
];

/* ── edges ─────────────────────────────────────────────────────────────── */

export const DEMO_DAG_EDGES: DagEdgeRecord[] = [
  { id: "e1", source: "start", target: "collect.notion", type: "dependency" },
  { id: "e2", source: "start", target: "collect.clickup", type: "dependency" },
  { id: "e3", source: "start", target: "collect.asana", type: "dependency" },
  { id: "e4", source: "collect.notion", target: "extract.notion", type: "dependency" },
  { id: "e5", source: "collect.clickup", target: "extract.clickup", type: "dependency" },
  { id: "e6", source: "collect.asana", target: "extract.asana", type: "dependency" },
  { id: "e7", source: "extract.notion", target: "analyst", type: "dependency" },
  { id: "e8", source: "extract.clickup", target: "analyst", type: "dependency" },
  { id: "e9", source: "extract.asana", target: "analyst", type: "dependency" },
  { id: "e10", source: "analyst", target: "reporter", type: "dependency" },
  { id: "e11", source: "reporter", target: "qa", type: "dependency" },
  /* feedback edge — 这是评分项「反馈闭环真实可触发」的视觉证据 */
  { id: "e12", source: "qa", target: "reporter_v2", type: "feedback" },
  { id: "e13", source: "reporter_v2", target: "qa_v2", type: "dependency" },
  { id: "e14", source: "qa_v2", target: "end", type: "dependency" },
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
    if (n.data.label.startsWith("qa")) qaRoundCount += 1;
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
