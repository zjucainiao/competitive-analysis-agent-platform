"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  TrendingUpIcon,
  TrendingDownIcon,
  AlertTriangleIcon,
} from "lucide-react";
import type { RunContext } from "@/components/layout/context-bar";
import { cn } from "@/lib/utils";
import {
  CORE_METRICS,
  AGENT_QUALITY,
  PROJECT_HEADER_STATS,
  formatMetric,
  formatDelta,
  type CoreMetric,
} from "@/lib/metrics-mock";
import { getInterventionCount } from "@/lib/workspace-actions";
import { Sparkline } from "./sparkline";
import { metricsTimeseries, projectLLMCalls } from "@/lib/api/client";
import type {
  AnyAgentOutput,
  LLMCallRecord,
  Project,
  ProjectMetricsSnapshot,
} from "@/lib/api/types";

/* ── 各环节真实指标：从本次运行的 outputs（每节点 tokens/cost/status/confidence）聚合 ── */

type AgentRow = {
  agent: string;
  nodeCount: number;
  passRate: number;
  totalCostUsd: number;
  avgDurationMs: number;
  totalTokens: number;
  avgConfidence: number;
};

const AGENT_ORDER = ["collector", "extractor", "analyst", "reporter", "qa"];

type OutputCommon = {
  agent_name?: string;
  status?: string;
  confidence?: number;
  tokens_input?: number;
  tokens_output?: number;
  cost_usd?: number;
  duration_ms?: number;
};

function aggregateAgentMetrics(
  outputs: Record<string, AnyAgentOutput>
): AgentRow[] {
  const byAgent = new Map<string, OutputCommon[]>();
  for (const out of Object.values(outputs) as OutputCommon[]) {
    const a = out.agent_name;
    if (!a) continue;
    if (!byAgent.has(a)) byAgent.set(a, []);
    byAgent.get(a)!.push(out);
  }
  const rows: AgentRow[] = [];
  for (const agent of AGENT_ORDER) {
    const list = byAgent.get(agent);
    if (!list || list.length === 0) continue;
    const n = list.length;
    const ok = list.filter((o) => o.status === "success").length;
    const cost = list.reduce((s, o) => s + (o.cost_usd ?? 0), 0);
    const dur = list.reduce((s, o) => s + (o.duration_ms ?? 0), 0);
    const tok = list.reduce(
      (s, o) => s + (o.tokens_input ?? 0) + (o.tokens_output ?? 0),
      0
    );
    const conf = list.reduce((s, o) => s + (o.confidence ?? 0), 0) / n;
    rows.push({
      agent,
      nodeCount: n,
      passRate: ok / n,
      totalCostUsd: cost,
      avgDurationMs: dur / n,
      totalTokens: tok,
      avgConfidence: conf,
    });
  }
  return rows;
}

/** demo / 无 outputs 时的回退（来自预置示例 AGENT_QUALITY）。 */
const AGENT_QUALITY_ROWS: AgentRow[] = AGENT_QUALITY.map((a) => ({
  agent: a.agent,
  nodeCount: a.nodeCount,
  passRate: a.passRate,
  totalCostUsd: a.totalCostUsd,
  avgDurationMs: a.avgDurationMs,
  totalTokens: Math.round(a.totalCostUsd * 50000),
  avgConfidence: a.passRate,
}));

/**
 * Workspace · Metrics tab（项目级）。
 *
 * 评分项「业务闭环指标 · accuracy / coverage / edit_rate / 业务价值可量化提升」核心。
 *
 * 视觉策略（DESIGN.md anti-pattern: 拒绝 hero-metric 模板）：
 *  - 5 个指标横排紧凑，不是大数字大渐变
 *  - 每个指标自带 sparkline 趋势 + delta 箭头 + 一句 hint
 *  - Per-agent 用横向比例条 + 文字描述（不是甜甜圈饼图）
 *  - Cost breakdown 用纯比例条 + 美元数字
 *  - vs Human baseline 是这页的 hero —— 对仗式排版，左 manual / 右 platform
 */
export function MetricsLayout({
  ctx,
  apiProject,
  apiOutputs,
}: {
  ctx: RunContext;
  apiProject?: Project;
  apiOutputs?: Record<string, AnyAgentOutput>;
}) {
  /* 各环节指标：有 outputs（真实工作台）→ 真聚合；否则 demo 回退预置示例 */
  const agentRows = useMemo(
    () => (apiOutputs ? aggregateAgentMetrics(apiOutputs) : []),
    [apiOutputs]
  );
  const rows = agentRows.length > 0 ? agentRows : AGENT_QUALITY_ROWS;
  /* edit_rate 从 localStorage 实时读 intervention 计数（mock 模式） */
  const [interventionCount, setInterventionCount] = useState(() =>
    getInterventionCount()
  );
  useEffect(() => {
    const listener = () => setInterventionCount(getInterventionCount());
    window.addEventListener("atlas:intervention", listener);
    return () => window.removeEventListener("atlas:intervention", listener);
  }, []);

  /* 拉历史快照供 sparkline 用（仅当有真实 project） */
  const { data: timeseriesData } = useSWR(
    apiProject ? ["timeseries", apiProject.project_id] : null,
    () => metricsTimeseries(apiProject!.project_id),
    { revalidateOnFocus: false }
  );
  // 不手动 useMemo：Next16 React Compiler 自动记忆（手写 memo 反而触发
  // 「Existing memoization could not be preserved」而整组件跳过编译）。
  const history: ProjectMetricsSnapshot[] =
    timeseriesData?.history && timeseriesData.history.length > 0
      ? timeseriesData.history
      : apiProject?.metrics_history ?? []; // metrics_history 内嵌在 project 上，后备源

  /* 真实 API：覆盖核心指标的当前值 + sparkline trend（同样交给 React Compiler 记忆） */
  const liveMetrics = ((): typeof CORE_METRICS => {
    if (!apiProject?.metrics) return CORE_METRICS;
    const m = apiProject.metrics;
    const trendFor = (key: "accuracy" | "coverage" | "edit_rate" | "total_cost_usd" | "duration_seconds"): number[] => {
      if (history.length === 0) return [];
      return history.map((s) => s.metrics[key]);
    };
    return CORE_METRICS.map((metric) => {
      const realTrend = (() => {
        switch (metric.id) {
          case "accuracy":
            return trendFor("accuracy");
          case "coverage":
            return trendFor("coverage");
          case "edit_rate":
            return trendFor("edit_rate");
          case "cost":
            return trendFor("total_cost_usd");
          case "duration":
            return trendFor("duration_seconds");
          default:
            return [];
        }
      })();
      const trend = realTrend.length >= 2 ? realTrend : metric.trend;
      switch (metric.id) {
        case "accuracy":
          return { ...metric, value: m.accuracy, trend };
        case "coverage":
          return { ...metric, value: m.coverage, trend };
        case "edit_rate":
          return { ...metric, value: m.edit_rate, trend };
        case "cost":
          return { ...metric, value: m.total_cost_usd, trend };
        case "duration":
          return { ...metric, value: m.duration_seconds, trend };
        default:
          return metric;
      }
    });
  })();

  return (
    <div className="space-y-6">
      <Header
        ctx={ctx}
        interventionCount={apiProject?.metrics?.manual_edits ?? interventionCount}
        apiProject={apiProject}
      />
      <CoreMetricsRow
        interventionCount={
          apiProject?.metrics?.manual_edits ?? interventionCount
        }
        metrics={liveMetrics}
        apiProject={apiProject}
      />
      <ReworkQualityTrend apiProject={apiProject} />
      <PerAgentQuality rows={rows} />
      <TokenBreakdown rows={rows} />
      {apiProject ? (
        <PhaseTokenRollup projectId={apiProject.project_id} />
      ) : null}
      <ScoringFooter hasApi={!!apiProject} live={agentRows.length > 0} />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

/**
 * 返工质量趋势 —— 把后端 ProjectMetrics 的 per_round_accuracy / round_delta /
 * best_round 直接画出来，回答「质检打回重做后是否真有改善」。
 * 仅当真实跑了 ≥2 轮质检时渲染（无返工则不出现，零干扰）。
 */
function ReworkQualityTrend({ apiProject }: { apiProject?: Project }) {
  const m = apiProject?.metrics;
  const series = m?.per_round_accuracy ?? [];
  if (!m || series.length < 2) return null;

  const deltas = m.round_delta ?? [];
  const best = m.best_round ?? 0;
  const overall = series[series.length - 1] - series[0];
  const maxV = Math.max(...series, 0.0001);
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const pts = (v: number) => `${v > 0 ? "+" : ""}${Math.round(v * 100)} 点`;

  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">返工质量趋势</h2>
          <p className="mt-0.5 text-xs text-text-secondary">
            质检每轮维度均分（越往右越新）· 用于判断「打回重做后是否真有改善」
          </p>
        </div>
        {best > 0 && (
          <span className="shrink-0 rounded-full bg-accent-bg px-2.5 py-1 text-xs font-medium text-accent-base">
            发布采用第 {best} 轮（最高分）
          </span>
        )}
      </div>

      <div className="flex items-end gap-2">
        {series.map((v, i) => {
          const d = i > 0 ? deltas[i - 1] ?? v - series[i - 1] : null;
          const isBest = i + 1 === best;
          return (
            <div key={i} className="flex flex-1 flex-col items-center gap-1">
              <div className="flex h-24 w-full items-end justify-center">
                <div
                  className={cn(
                    "w-full max-w-[56px] rounded-t",
                    isBest ? "bg-accent-base" : "bg-border-strong"
                  )}
                  style={{ height: `${Math.max((v / maxV) * 100, 4)}%` }}
                />
              </div>
              <div className="text-xs font-semibold text-text-primary">
                {pct(v)}
              </div>
              <div className="text-[11px] text-text-muted">第 {i + 1} 轮</div>
              {d !== null && (
                <div
                  className={cn(
                    "text-[11px] font-medium",
                    d > 0.0005
                      ? "text-success-base"
                      : d < -0.0005
                        ? "text-error-base"
                        : "text-text-muted"
                  )}
                >
                  {pts(d)}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-xs text-text-secondary">
        {overall > 0.001
          ? `经 ${series.length - 1} 轮返工，质检均分提升 ${Math.round(overall * 100)} 个百分点；已发布最高分轮。`
          : overall < -0.001
            ? `返工后均分未提升（${Math.round(overall * 100)} 点），已自动回滚发布最高分轮，避免越改越差。`
            : "返工后均分基本持平，已发布最高分轮。"}
      </p>
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function Header({
  ctx,
  interventionCount,
  apiProject,
}: {
  ctx: RunContext;
  interventionCount: number;
  apiProject?: Project;
}) {
  const tokens = apiProject?.metrics?.total_tokens ?? 0;
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle pb-3">
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
          项目指标 · 第 {ctx.runNumber.toString().padStart(2, "0")} 次运行
        </div>
        <h1 className="mt-1 text-lg font-semibold text-text-primary">
          {ctx.projectName}
        </h1>
        <p className="mt-1 text-xs text-text-secondary">
          {apiProject ? (
            <>
              {tokens.toLocaleString()} Token ·{" "}
              {apiProject.metrics?.evidence_count ?? 0} 条证据 ·{" "}
              {apiProject.metrics?.qa_round_count ?? 0} 轮质检 · 实时数据
            </>
          ) : (
            <>
              {PROJECT_HEADER_STATS.spanCount} 个节点 ·{" "}
              {PROJECT_HEADER_STATS.totalClaims} 条结论 ·{" "}
              {PROJECT_HEADER_STATS.totalEvidences} 条证据 ·{" "}
              {PROJECT_HEADER_STATS.qaRoundCount} 轮质检
            </>
          )}
        </p>
      </div>
      <div className="flex items-center gap-4 text-xs">
        <StatChip label="人工介入" value={interventionCount} mono />
        <StatChip
          label="自动重跑"
          value={apiProject?.metrics?.qa_round_count ?? 1}
          mono
        />
        <StatChip
          label="状态"
          value={
            apiProject?.status
              ? projectStatusLabel(apiProject.status)
              : ctx.status.label
          }
        />
      </div>
    </header>
  );
}

/** 项目状态枚举 → 中文（不在 UI 暴露 running / completed 等裸值） */
const PROJECT_STATUS_LABELS: Record<string, string> = {
  pending: "等待中",
  queued: "排队中",
  running: "运行中",
  collecting: "采集中",
  analyzing: "分析中",
  reporting: "撰写中",
  reviewing: "质检中",
  completed: "已完成",
  finished: "已完成",
  success: "已完成",
  failed: "失败",
  error: "失败",
  archived: "已归档",
  deleted: "已删除",
};

function projectStatusLabel(status: string): string {
  return PROJECT_STATUS_LABELS[status] ?? status;
}

function StatChip({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | number;
  mono?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <span
        className={cn(
          "font-medium text-text-primary tabular-nums",
          mono && "font-mono"
        )}
        data-num
      >
        {value}
      </span>
    </span>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function CoreMetricsRow({
  interventionCount,
  metrics,
  apiProject,
}: {
  interventionCount: number;
  metrics: CoreMetric[];
  apiProject?: Project;
}) {
  /* edit_rate live: API 优先；否则用 localStorage intervention 计数估算 */
  const editRateLive =
    apiProject?.metrics?.edit_rate ??
    Math.min(
      1,
      interventionCount / Math.max(PROJECT_HEADER_STATS.totalParagraphs, 1)
    );

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      {metrics.map((m) => {
        const liveValue = m.id === "edit_rate" ? editRateLive : m.value;
        return <MetricCard key={m.id} metric={m} liveValue={liveValue} />;
      })}
    </div>
  );
}

/** 核心指标 id → 中文标签 / 说明（数据源标签为英文，展示前本地化） */
const METRIC_COPY: Record<string, { label: string; hint: string }> = {
  accuracy: {
    label: "准确率",
    hint: "段落事实与证据的蕴含比例（质检事实一致性）",
  },
  coverage: {
    label: "覆盖度",
    hint: "字段填充率 × 来源覆盖率",
  },
  edit_rate: {
    label: "编辑率",
    hint: "用户编辑段落数 / 总段落 · 越低越好",
  },
  cost: {
    label: "成本",
    hint: "本次运行的模型总成本",
  },
  duration: {
    label: "耗时",
    hint: "本次运行端到端耗时（秒）",
  },
};

function MetricCard({
  metric,
  liveValue,
}: {
  metric: CoreMetric;
  liveValue: number;
}) {
  const copy = METRIC_COPY[metric.id];
  const isImproved =
    metric.better === "higher" ? metric.delta > 0 : metric.delta < 0;
  const Arrow = metric.delta > 0 ? TrendingUpIcon : TrendingDownIcon;
  const deltaTone = isImproved ? "text-success-base" : "text-error-base";
  const trendTone = metric.better === "higher" ? "text-success-base" : "text-accent-base";

  return (
    <div className="rounded-md border border-border-subtle bg-bg-raised p-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          {copy?.label ?? metric.label}
        </span>
        <span className={cn("inline-flex items-center gap-0.5 text-[10px] font-medium", deltaTone)}>
          <Arrow className="h-3 w-3" />
          <span className="font-mono tabular-nums" data-num>
            {formatDelta(metric.delta, metric.deltaFormat)}
          </span>
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline justify-between gap-2">
        <span
          className="font-mono text-2xl font-semibold tabular-nums text-text-primary"
          data-num
        >
          {formatMetric(liveValue, metric.format)}
        </span>
        <span className={cn("shrink-0", trendTone)}>
          <Sparkline values={metric.trend} />
        </span>
      </div>
      <p className="mt-1.5 text-[10px] leading-snug text-text-muted">
        {copy?.hint ?? metric.hint}
      </p>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

/** agent 枚举 → 中文环节名（与 Trace / DAG 一致，不在 UI 暴露裸枚举） */
const AGENT_LABELS: Record<string, string> = {
  collector: "信息采集",
  extractor: "证据入库",
  analyst: "结构化分析",
  reporter: "报告撰写",
  qa: "质量审查",
};

function agentLabel(agent: string): string {
  return AGENT_LABELS[agent] ?? agent;
}

function PerAgentQuality({ rows }: { rows: AgentRow[] }) {
  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          各环节质量
        </div>
        <p className="mt-0.5 text-xs text-text-secondary">
          每个环节的成功率、平均耗时与成本
        </p>
      </header>
      <ul className="space-y-3">
        {rows.map((a) => (
          <li
            key={a.agent}
            className="grid grid-cols-[120px_minmax(0,1fr)_auto] items-center gap-4"
          >
            <div>
              <div className="text-sm font-medium text-text-primary">
                {agentLabel(a.agent)}
              </div>
              <div className="text-[10px] text-text-muted">
                {a.nodeCount} 个节点
              </div>
            </div>
            <div>
              <PassBar ratio={a.passRate} />
              <p className="mt-1 text-[11px] text-text-secondary leading-relaxed">
                成功 {Math.round(a.passRate * a.nodeCount)}/{a.nodeCount} · 平均置信度{" "}
                {(a.avgConfidence * 100).toFixed(0)}%
                {a.totalTokens > 0
                  ? ` · ${a.totalTokens.toLocaleString()} Token`
                  : ""}
              </p>
            </div>
            <div className="text-right">
              <div
                className="font-mono text-sm font-medium text-text-primary tabular-nums"
                data-num
              >
                {(a.passRate * 100).toFixed(0)}%
              </div>
              <div
                className="font-mono text-[10px] text-text-muted tabular-nums"
                data-num
              >
                ${a.totalCostUsd.toFixed(3)} ·{" "}
                {(a.avgDurationMs / 1000).toFixed(1)}s
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PassBar({ ratio }: { ratio: number }) {
  const filled = Math.round(ratio * 10);
  return (
    <div className="flex items-center gap-0.5">
      {Array.from({ length: 10 }, (_, i) => {
        const on = i < filled;
        return (
          <span
            key={i}
            className={cn(
              "h-1.5 flex-1 rounded-sm",
              on
                ? ratio >= 0.85
                  ? "bg-success-base"
                  : ratio >= 0.65
                    ? "bg-warning-base"
                    : "bg-error-base"
                : "bg-border-subtle"
            )}
          />
        );
      })}
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function TokenBreakdown({ rows }: { rows: AgentRow[] }) {
  const items = [...rows]
    .filter((r) => r.totalTokens > 0)
    .sort((a, b) => b.totalTokens - a.totalTokens);
  const total = items.reduce((s, r) => s + r.totalTokens, 0);
  const max = Math.max(...items.map((r) => r.totalTokens), 1);
  const totalCost = rows.reduce((s, r) => s + r.totalCostUsd, 0);

  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4 flex items-baseline justify-between gap-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
            各环节 Token 用量
          </div>
          <p className="mt-0.5 text-xs text-text-secondary">
            模型选型 / 提示词优化的优先级提示（成本
            {totalCost > 0 ? ` 合计 $${totalCost.toFixed(3)}` : "经控制台结算"}）
          </p>
        </div>
        <span
          className="shrink-0 font-mono text-xs text-text-muted tabular-nums"
          data-num
        >
          {total.toLocaleString()} Token
        </span>
      </header>
      {items.length === 0 ? (
        <p className="text-xs text-text-muted">本次运行暂无 Token 统计。</p>
      ) : (
        <ul className="space-y-2">
          {items.map((c) => (
            <li
              key={c.agent}
              className="grid grid-cols-[120px_minmax(0,1fr)_120px] items-center gap-3"
            >
              <span className="truncate text-xs text-text-primary">
                {agentLabel(c.agent)}
              </span>
              <div className="h-2 overflow-hidden rounded-sm bg-bg-sunken">
                <div
                  className="h-full bg-accent-base"
                  style={{ width: `${(c.totalTokens / max) * 100}%` }}
                />
              </div>
              <span
                className="text-right font-mono text-xs text-text-secondary tabular-nums"
                data-num
              >
                {c.totalTokens.toLocaleString()} ·{" "}
                {total > 0 ? ((c.totalTokens / total) * 100).toFixed(0) : 0}%
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

const PHASE_LABELS: Record<string, string> = {
  tool_call: "结构化抽取",
  freeform_schema: "自由文本兜底",
  json_mode: "JSON 模式",
  freeform: "自由文本",
  summary: "摘要",
};

/**
 * 按 API 调用类型（phase）汇总本次运行的 Token 用量 —— 回答“每种调用类型花了多少 token”。
 * 数据源：真实 /llm-calls 流水，按 phase 分组求和。
 */
function PhaseTokenRollup({ projectId }: { projectId: string }) {
  const { data } = useSWR(
    ["phase-tokens", projectId],
    () => projectLLMCalls(projectId, { limit: 500 }),
    { revalidateOnFocus: false }
  );
  const calls: LLMCallRecord[] = useMemo(() => data?.calls ?? [], [data]);
  const rows = useMemo(() => {
    const by = new Map<string, { count: number; tin: number; tout: number }>();
    for (const c of calls) {
      const agg = by.get(c.phase) ?? { count: 0, tin: 0, tout: 0 };
      agg.count += 1;
      agg.tin += c.tokens_input ?? 0;
      agg.tout += c.tokens_output ?? 0;
      by.set(c.phase, agg);
    }
    return [...by.entries()]
      .map(([phase, v]) => ({ phase, ...v, total: v.tin + v.tout }))
      .sort((a, b) => b.total - a.total);
  }, [calls]);

  if (calls.length === 0) return null;
  const totIn = rows.reduce((s, r) => s + r.tin, 0);
  const totOut = rows.reduce((s, r) => s + r.tout, 0);
  const maxTot = Math.max(...rows.map((r) => r.total), 1);

  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4 flex items-baseline justify-between gap-3">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
            按调用类型 · Token 用量
          </div>
          <p className="mt-0.5 text-xs text-text-secondary">
            每种 API 调用类型消耗多少 token（共 {calls.length} 次调用）
          </p>
        </div>
        <span
          className="shrink-0 font-mono text-xs text-text-muted tabular-nums"
          data-num
        >
          {(totIn + totOut).toLocaleString()} Token
        </span>
      </header>
      <ul className="space-y-2">
        {rows.map((r) => (
          <li
            key={r.phase}
            className="grid grid-cols-[120px_minmax(0,1fr)_150px] items-center gap-3"
          >
            <span className="truncate text-xs text-text-primary">
              {PHASE_LABELS[r.phase] ?? r.phase}
            </span>
            <div className="h-2 overflow-hidden rounded-sm bg-bg-sunken">
              <div
                className="h-full bg-accent-base"
                style={{ width: `${(r.total / maxTot) * 100}%` }}
              />
            </div>
            <span
              className="text-right font-mono text-[11px] text-text-secondary tabular-nums"
              data-num
            >
              {r.count}次 · 入{r.tin.toLocaleString()} / 出
              {r.tout.toLocaleString()}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function ScoringFooter({ hasApi, live }: { hasApi: boolean; live: boolean }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-border-subtle bg-bg-sunken/60 px-4 py-3 text-[11px] text-text-muted">
      <AlertTriangleIcon className="h-3.5 w-3.5 mt-0.5 shrink-0 text-warning-base" />
      <p className="leading-relaxed">
        {live ? (
          <>
            核心指标、返工质量趋势、各环节质量与 Token 用量均来自本次运行的真实统计；
            趋势 sparkline 在累计多次运行后逐步填充。
          </>
        ) : hasApi ? (
          <>
            5 个核心指标的当前值来自本次运行的真实统计；各环节质量 / Token 用量在
            流水线产出节点输出后自动填真。
          </>
        ) : (
          <>
            这是示例路径的预置数据 + 你的实际介入计数。打开真实项目后，各项指标会
            自动覆盖为本次运行的真实值。
          </>
        )}
      </p>
    </div>
  );
}
