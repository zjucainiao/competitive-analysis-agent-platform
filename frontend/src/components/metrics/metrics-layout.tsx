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
  COST_BREAKDOWN,
  HUMAN_BASELINE,
  PROJECT_HEADER_STATS,
  formatMetric,
  formatDelta,
  type CoreMetric,
} from "@/lib/metrics-mock";
import { getInterventionCount } from "@/lib/workspace-actions";
import { Sparkline } from "./sparkline";
import { metricsTimeseries } from "@/lib/api/client";
import type { Project, ProjectMetricsSnapshot } from "@/lib/api/types";

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
}: {
  ctx: RunContext;
  apiProject?: Project;
}) {
  /* edit_rate 从 localStorage 实时读 intervention 计数（mock 模式） */
  const [interventionCount, setInterventionCount] = useState(0);
  useEffect(() => {
    setInterventionCount(getInterventionCount());
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
  const history: ProjectMetricsSnapshot[] = useMemo(() => {
    if (timeseriesData?.history && timeseriesData.history.length > 0) {
      return timeseriesData.history;
    }
    // metrics_history 也内嵌在 project 上；后备数据源
    return apiProject?.metrics_history ?? [];
  }, [timeseriesData, apiProject?.metrics_history]);

  /* 真实 API：覆盖核心指标的当前值 + sparkline trend */
  const liveMetrics = useMemo(() => {
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
  }, [apiProject?.metrics, history]);

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
      <PerAgentQuality />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_1fr]">
        <CostBreakdown />
        <CostMix />
      </div>
      <VsHumanBaseline />
      <ScoringFooter hasApi={!!apiProject} />
    </div>
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
  const spans = apiProject?.metrics?.qa_round_count ?? PROJECT_HEADER_STATS.spanCount;
  const tokens = apiProject?.metrics?.total_tokens ?? 0;
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle pb-3">
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
          Project metrics · run #{ctx.runNumber.toString().padStart(2, "0")}
        </div>
        <h1 className="mt-1 text-lg font-semibold text-text-primary">
          {ctx.projectName}
        </h1>
        <p className="mt-1 text-xs text-text-secondary">
          {apiProject ? (
            <>
              {tokens.toLocaleString()} tokens ·{" "}
              {apiProject.metrics?.evidence_count ?? 0} evidences ·{" "}
              {apiProject.metrics?.qa_round_count ?? 0} QA round
              {(apiProject.metrics?.qa_round_count ?? 0) === 1 ? "" : "s"} ·{" "}
              live from /state
            </>
          ) : (
            <>
              {PROJECT_HEADER_STATS.spanCount} spans ·{" "}
              {PROJECT_HEADER_STATS.totalClaims} claims ·{" "}
              {PROJECT_HEADER_STATS.totalEvidences} evidences ·{" "}
              {PROJECT_HEADER_STATS.qaRoundCount} QA round
            </>
          )}
        </p>
      </div>
      <div className="flex items-center gap-4 text-xs">
        <StatChip label="user interventions" value={interventionCount} mono />
        <StatChip
          label="auto reruns"
          value={apiProject?.metrics?.qa_round_count ?? 1}
          mono
        />
        <StatChip
          label="status"
          value={apiProject?.status ?? ctx.status.label}
        />
      </div>
    </header>
  );
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

function MetricCard({
  metric,
  liveValue,
}: {
  metric: CoreMetric;
  liveValue: number;
}) {
  const isImproved =
    metric.better === "higher" ? metric.delta > 0 : metric.delta < 0;
  const Arrow = metric.delta > 0 ? TrendingUpIcon : TrendingDownIcon;
  const deltaTone = isImproved ? "text-success-base" : "text-error-base";
  const trendTone = metric.better === "higher" ? "text-success-base" : "text-accent-base";

  return (
    <div className="rounded-md border border-border-subtle bg-bg-raised p-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          {metric.label}
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
        {metric.hint}
      </p>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function PerAgentQuality() {
  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Per-agent quality
        </div>
        <p className="mt-0.5 text-xs text-text-secondary">
          每个环节的通过率、平均耗时与成本
        </p>
      </header>
      <ul className="space-y-3">
        {AGENT_QUALITY.map((a) => (
          <li
            key={a.agent}
            className="grid grid-cols-[120px_minmax(0,1fr)_auto] items-center gap-4"
          >
            <div>
              <div className="font-mono text-sm font-medium text-text-primary">
                {a.agent}
              </div>
              <div className="text-[10px] text-text-muted">
                {a.nodeCount} node{a.nodeCount === 1 ? "" : "s"}
              </div>
            </div>
            <div>
              <PassBar ratio={a.passRate} />
              <p className="mt-1 text-[11px] text-text-secondary leading-relaxed">
                {a.description}
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

function CostBreakdown() {
  const max = Math.max(...COST_BREAKDOWN.map((c) => c.costUsd));
  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Cost breakdown · per node
        </div>
      </header>
      <ul className="space-y-2">
        {COST_BREAKDOWN.map((c) => (
          <li
            key={c.label}
            className="grid grid-cols-[140px_minmax(0,1fr)_72px] items-center gap-3"
          >
            <code className="truncate font-mono text-xs text-text-primary">
              {c.label}
            </code>
            <div className="h-2 overflow-hidden rounded-sm bg-bg-sunken">
              <div
                className="h-full bg-accent-base"
                style={{ width: `${(c.costUsd / max) * 100}%` }}
              />
            </div>
            <span
              className="text-right font-mono text-xs text-text-secondary tabular-nums"
              data-num
            >
              ${c.costUsd.toFixed(3)} · {(c.ratio * 100).toFixed(0)}%
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CostMix() {
  /* by agent */
  const agentTotals = AGENT_QUALITY.map((a) => ({
    agent: a.agent,
    cost: a.totalCostUsd,
  })).sort((a, b) => b.cost - a.cost);
  const total = agentTotals.reduce((acc, a) => acc + a.cost, 0);

  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Cost by agent · proportional
        </div>
        <p className="mt-0.5 text-xs text-text-secondary">
          模型选型 / prompt 优化的优先级提示
        </p>
      </header>
      <div className="flex items-center overflow-hidden rounded-md border border-border-subtle">
        {agentTotals.map((a, i) => {
          const ratio = total > 0 ? a.cost / total : 0;
          const COLORS = [
            "bg-viz-1",
            "bg-viz-2",
            "bg-viz-3",
            "bg-viz-5",
            "bg-viz-4",
          ];
          return (
            <div
              key={a.agent}
              className={cn("h-8", COLORS[i] ?? "bg-viz-6")}
              style={{ width: `${ratio * 100}%` }}
              title={`${a.agent} · $${a.cost.toFixed(3)}`}
            />
          );
        })}
      </div>
      <ul className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5">
        {agentTotals.map((a, i) => {
          const ratio = total > 0 ? a.cost / total : 0;
          const COLORS = [
            "bg-viz-1",
            "bg-viz-2",
            "bg-viz-3",
            "bg-viz-5",
            "bg-viz-4",
          ];
          return (
            <li key={a.agent} className="flex items-center gap-2 text-xs">
              <span
                className={cn("h-2 w-2 rounded-pill", COLORS[i] ?? "bg-viz-6")}
              />
              <span className="font-mono text-text-secondary">{a.agent}</span>
              <span
                className="ml-auto font-mono text-text-muted tabular-nums"
                data-num
              >
                {(ratio * 100).toFixed(0)}%
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function VsHumanBaseline() {
  return (
    <section className="rounded-lg border border-accent-border bg-accent-bg/30 p-5">
      <header className="mb-1">
        <div className="text-[10px] font-medium uppercase tracking-wider text-accent-base">
          vs human baseline
        </div>
        <h2 className="mt-0.5 text-base font-semibold text-text-primary">
          可量化的业务价值
        </h2>
        <p className="mt-1 text-xs text-text-secondary">
          相比 1 名分析师人工跟 3 个竞品 · 5 个维度的基线（取自咨询行业平均）
        </p>
      </header>

      <ul className="mt-4 space-y-2.5">
        {HUMAN_BASELINE.map((row) => (
          <li
            key={row.dim}
            className="grid grid-cols-[100px_minmax(0,1fr)_minmax(0,1fr)_100px] items-center gap-3"
          >
            <span className="text-xs font-medium text-text-primary">
              {row.dim}
            </span>
            <BaselineBar
              label={row.manual.label}
              ratio={row.manual.ratio}
              side="manual"
              tone={row.manual.tone}
            />
            <BaselineBar
              label={row.platform.label}
              ratio={row.platform.ratio}
              side="platform"
            />
            <span className="text-right font-mono text-xs font-medium text-accent-base tabular-nums">
              {row.improvement}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function BaselineBar({
  label,
  ratio,
  side,
  tone,
}: {
  label: string;
  ratio: number;
  side: "manual" | "platform";
  tone?: "muted" | "warn";
}) {
  const isWarn = tone === "warn";
  const isMuted = tone === "muted";
  return (
    <div className="flex items-center gap-2">
      {side === "manual" ? (
        <span className="text-[10px] uppercase tracking-wider text-text-muted shrink-0 w-12">
          manual
        </span>
      ) : null}
      <div className="relative h-3 flex-1 overflow-hidden rounded-sm bg-bg-sunken">
        <div
          className={cn(
            "h-full",
            side === "platform"
              ? "bg-accent-base"
              : isWarn
                ? "bg-error-base/40"
                : isMuted
                  ? "bg-neutral-base/50"
                  : "bg-text-muted/40"
          )}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
      {side === "platform" ? (
        <span className="text-[10px] uppercase tracking-wider text-accent-base shrink-0 w-12 text-right">
          platform
        </span>
      ) : null}
      <span className="shrink-0 text-[11px] text-text-secondary">{label}</span>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function ScoringFooter({ hasApi }: { hasApi: boolean }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-border-subtle bg-bg-sunken/60 px-4 py-3 text-[11px] text-text-muted">
      <AlertTriangleIcon className="h-3.5 w-3.5 mt-0.5 shrink-0 text-warning-base" />
      <p className="leading-relaxed">
        {hasApi ? (
          <>
            5 个核心指标的当前值来自 <code className="font-mono">project.metrics</code>{" "}
            （后端 Orchestrator 实时计算）；趋势 sparkline / per-agent / cost
            breakdown 等是预置的视觉素材，等后端 metrics 历史 endpoint 接入后填真。
            vs human baseline 数字基于咨询行业平均，答辩前可定标到客户具体场景。
          </>
        ) : (
          <>
            demo 路径展示的是预置 mock 指标 + 用户实际 intervention 计数。
            打开真实项目（POST /api/projects 创建后）后，accuracy / coverage / edit_rate
            / cost / duration 五项会从 <code className="font-mono">project.metrics</code>{" "}
            自动覆盖；vs human baseline 数字基于咨询行业平均，可在答辩前定标。
          </>
        )}
      </p>
    </div>
  );
}
