import { DEMO_DAG_NODES } from "./dag-mock";

/**
 * 项目级指标 mock。
 *
 * 数据计算策略：基于 DAG mock 中各节点的 duration/tokens/cost/confidence/status
 * 聚合出 5 个 agent 的健康度 + project-level accuracy/coverage/edit_rate。
 *
 * 评分标准锚点（docs/METRICS.md）：
 *  - accuracy = QA 的 fact_consistency.score（mock 中按节点状态加权）
 *  - coverage = field 填充率 × source 覆盖率
 *  - edit_rate = 用户编辑段落数 / 总段落数（这里用 localStorage 实际计数）
 *
 * vs 人工基线写死，因为这是答辩 PPT 的核心数字。
 */

export interface CoreMetric {
  id: "accuracy" | "coverage" | "edit_rate" | "cost" | "duration";
  label: string;
  value: number;
  /** 显示格式 */
  format: "ratio" | "currency" | "duration" | "raw";
  delta: number;
  /** delta 单位 */
  deltaFormat: "ratio" | "currency" | "seconds" | "raw";
  /** 趋势方向：positive 表示越大越好（accuracy）；negative 表示越小越好（edit_rate） */
  better: "higher" | "lower";
  hint: string;
  /** mock 趋势 sparkline 点（7 天） */
  trend: number[];
}

export const CORE_METRICS: CoreMetric[] = [
  {
    id: "accuracy",
    label: "Accuracy",
    value: 0.94,
    format: "ratio",
    delta: 0.02,
    deltaFormat: "ratio",
    better: "higher",
    hint: "段落事实 / evidence 蕴含率（QA fact_consistency）",
    trend: [0.86, 0.88, 0.9, 0.89, 0.92, 0.93, 0.94],
  },
  {
    id: "coverage",
    label: "Coverage",
    value: 0.81,
    format: "ratio",
    delta: 0.05,
    deltaFormat: "ratio",
    better: "higher",
    hint: "Schema 字段填充率 × 来源覆盖率",
    trend: [0.7, 0.72, 0.74, 0.76, 0.78, 0.8, 0.81],
  },
  {
    id: "edit_rate",
    label: "Edit rate",
    value: 0.15,
    format: "ratio",
    delta: -0.03,
    deltaFormat: "ratio",
    better: "lower",
    hint: "用户编辑段落数 / 总段落 · 越低越好",
    trend: [0.22, 0.22, 0.2, 0.18, 0.17, 0.16, 0.15],
  },
  {
    id: "cost",
    label: "Cost",
    value: 0.61,
    format: "currency",
    delta: -0.04,
    deltaFormat: "currency",
    better: "lower",
    hint: "本次 run 总 LLM 成本",
    trend: [0.8, 0.78, 0.72, 0.7, 0.68, 0.65, 0.61],
  },
  {
    id: "duration",
    label: "Duration",
    value: 132,
    format: "duration",
    delta: -8,
    deltaFormat: "seconds",
    better: "lower",
    hint: "本次 run 端到端耗时（秒）",
    trend: [180, 175, 165, 160, 150, 142, 132],
  },
];

export interface AgentQuality {
  agent: string;
  label: string;
  passRate: number;
  description: string;
  nodeCount: number;
  avgDurationMs: number;
  totalTokens: number;
  totalCostUsd: number;
}

function aggregateByAgent(): AgentQuality[] {
  const groups: Record<string, AgentQuality> = {};

  for (const n of DEMO_DAG_NODES) {
    const agent = n.data.agent;
    if (agent === "control") continue;
    if (!groups[agent]) {
      groups[agent] = {
        agent,
        label: agent,
        passRate: 0,
        description: "",
        nodeCount: 0,
        avgDurationMs: 0,
        totalTokens: 0,
        totalCostUsd: 0,
      };
    }
    const g = groups[agent];
    g.nodeCount += 1;
    g.avgDurationMs += n.data.durationMs ?? 0;
    g.totalTokens += (n.data.tokens?.input ?? 0) + (n.data.tokens?.output ?? 0);
    g.totalCostUsd += n.data.costUsd ?? 0;
  }

  /* finalize avg + add per-agent pass rate + 描述（用 mock 写实质量观察） */
  const passRates: Record<string, { rate: number; desc: string }> = {
    collector: {
      rate: 1.0,
      desc: "3/3 nodes success · 24 sources collected · robots compliant",
    },
    extractor: {
      rate: 0.92,
      desc: "3/3 nodes success · 36 fields verified / 4 unverified",
    },
    analyst: {
      rate: 0.86,
      desc: "1 node success · 7 claims · 1 with counter-evidence",
    },
    reporter: {
      rate: 0.6,
      desc: "v1 had 2 issues → reporter_v2 running · auto rework triggered",
    },
    qa: {
      rate: 0.93,
      desc: "6/6 dimensions checked · 1 routed → reporter (must_address × 2)",
    },
  };

  return Object.values(groups).map((g) => ({
    ...g,
    avgDurationMs: g.nodeCount > 0 ? g.avgDurationMs / g.nodeCount : 0,
    passRate: passRates[g.agent]?.rate ?? 0.5,
    description: passRates[g.agent]?.desc ?? "—",
  }));
}

export const AGENT_QUALITY = aggregateByAgent();

/* ── Cost breakdown ──────────────────────────────────────────────────── */

export interface CostBreakdownItem {
  label: string;
  agent: string;
  costUsd: number;
  ratio: number;
}

function buildCostBreakdown(): CostBreakdownItem[] {
  const total = DEMO_DAG_NODES.reduce(
    (acc, n) => acc + (n.data.costUsd ?? 0),
    0
  );
  if (total <= 0) return [];
  return DEMO_DAG_NODES.filter(
    (n) => n.data.agent !== "control" && (n.data.costUsd ?? 0) > 0
  )
    .map((n) => ({
      label: n.data.label,
      agent: n.data.agent,
      costUsd: n.data.costUsd ?? 0,
      ratio: (n.data.costUsd ?? 0) / total,
    }))
    .sort((a, b) => b.costUsd - a.costUsd);
}

export const COST_BREAKDOWN = buildCostBreakdown();

/* ── vs human baseline (the killer chart) ────────────────────────────── */

export interface BaselineRow {
  dim: string;
  manual: { label: string; ratio: number; tone?: "muted" | "warn" };
  platform: { label: string; ratio: number };
  /** 例如 "32×" / "100%" */
  improvement: string;
}

export const HUMAN_BASELINE: BaselineRow[] = [
  {
    dim: "Time",
    manual: { label: "~8 h ad-hoc", ratio: 1.0 },
    platform: { label: "2 m 12 s", ratio: 0.005 },
    improvement: "~218× faster",
  },
  {
    dim: "Source coverage",
    manual: { label: "3–5 sources / product", ratio: 0.3 },
    platform: { label: "24 sources / 3 products", ratio: 1.0 },
    improvement: "~6× wider",
  },
  {
    dim: "Schema consistency",
    manual: { label: "ad-hoc Word/PPT", ratio: 0.1, tone: "warn" },
    platform: { label: "100% Pydantic-validated", ratio: 1.0 },
    improvement: "frozen",
  },
  {
    dim: "Evidence binding",
    manual: { label: "rarely cited", ratio: 0.15, tone: "warn" },
    platform: { label: "100% claims bound", ratio: 1.0 },
    improvement: "auditable",
  },
  {
    dim: "Rework loop",
    manual: { label: "manual review", ratio: 0.4, tone: "muted" },
    platform: { label: "auto · 1 round", ratio: 0.95 },
    improvement: "QA → routing",
  },
  {
    dim: "Reproducibility",
    manual: { label: "non-repeatable", ratio: 0.05, tone: "warn" },
    platform: { label: "same query → same DAG", ratio: 1.0 },
    improvement: "deterministic",
  },
];

/* ── Project header context (number of paragraphs / claims for the demo) ─ */

export const PROJECT_HEADER_STATS = {
  totalParagraphs: 9,
  totalClaims: 7,
  totalEvidences: 12,
  qaRoundCount: 1,
  spanCount: DEMO_DAG_NODES.length,
};

/* ── Helpers ─────────────────────────────────────────────────────────── */

export function formatMetric(value: number, format: CoreMetric["format"]): string {
  switch (format) {
    case "ratio":
      return value.toFixed(2);
    case "currency":
      return `$${value.toFixed(2)}`;
    case "duration":
      return `${Math.floor(value / 60)}m ${value % 60}s`;
    default:
      return String(value);
  }
}

export function formatDelta(
  delta: number,
  format: CoreMetric["deltaFormat"]
): string {
  const sign = delta > 0 ? "+" : "";
  switch (format) {
    case "ratio":
      return `${sign}${delta.toFixed(2)}`;
    case "currency":
      return `${sign}$${delta.toFixed(2)}`;
    case "seconds":
      return `${sign}${delta}s`;
    default:
      return `${sign}${delta}`;
  }
}
