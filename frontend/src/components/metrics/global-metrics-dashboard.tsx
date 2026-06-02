"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  TrendingUpIcon,
  TrendingDownIcon,
  ActivityIcon,
  CalendarIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Sparkline } from "./sparkline";
import { MOCK_PROJECTS } from "@/lib/projects-mock";
import { aggregateMetrics } from "@/lib/api/client";
import type { AggregateMetricsResponse } from "@/lib/api/types";

/**
 * /metrics · 全局指标仪表盘
 *
 * 不抄 hero-metric 模板。横向密集排版 + 趋势图 + 跨项目对比。
 * 视觉锚点：「整个平台运行了多少 run、质量怎么样、花了多少钱」。
 */

type Range = "7d" | "30d" | "90d";

const RANGE_OPTIONS: Array<{ id: Range; label: string }> = [
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
  { id: "90d", label: "Last quarter" },
];

interface GlobalCore {
  id: string;
  label: string;
  value: string;
  rawValue: number;
  delta: string;
  positive: boolean;
  hint: string;
  trend: number[];
  trendTone: "good" | "accent" | "warn";
}

const GLOBAL_METRICS_7D: GlobalCore[] = [
  {
    id: "accuracy",
    label: "Accuracy (avg)",
    value: "0.94",
    rawValue: 0.94,
    delta: "+0.02",
    positive: true,
    hint: "QA fact_consistency · 跨 6 项目均值",
    trend: [0.86, 0.88, 0.9, 0.89, 0.92, 0.93, 0.94],
    trendTone: "good",
  },
  {
    id: "coverage",
    label: "Coverage (avg)",
    value: "0.82",
    rawValue: 0.82,
    delta: "+0.04",
    positive: true,
    hint: "Schema 字段填充率 × source 覆盖率",
    trend: [0.71, 0.73, 0.75, 0.77, 0.79, 0.81, 0.82],
    trendTone: "good",
  },
  {
    id: "edit_rate",
    label: "Edit rate (avg)",
    value: "0.13",
    rawValue: 0.13,
    delta: "-0.02",
    positive: true,
    hint: "用户介入率 · 越低越好",
    trend: [0.18, 0.17, 0.16, 0.16, 0.15, 0.14, 0.13],
    trendTone: "accent",
  },
  {
    id: "qa_pass",
    label: "QA pass rate",
    value: "0.79",
    rawValue: 0.79,
    delta: "+0.06",
    positive: true,
    hint: "首发通过率 (无 rework)",
    trend: [0.65, 0.68, 0.71, 0.73, 0.75, 0.77, 0.79],
    trendTone: "good",
  },
  {
    id: "cost",
    label: "Cost (total)",
    value: "$24.18",
    rawValue: 24.18,
    delta: "-$3.40",
    positive: true,
    hint: "全部 LLM 调用累计成本",
    trend: [42, 38, 36, 34, 31, 28, 24],
    trendTone: "accent",
  },
  {
    id: "runs",
    label: "Runs",
    value: "43",
    rawValue: 43,
    delta: "+12",
    positive: true,
    hint: "本周期完成 / 进行中 run 数",
    trend: [4, 5, 7, 6, 8, 7, 6],
    trendTone: "accent",
  },
];

const AGENT_HEALTH = [
  {
    agent: "collector",
    passRate: 0.97,
    nodes: 96,
    cost: 4.4,
    notes: "robots-blocked 2 次，自动 fallback Playwright",
  },
  {
    agent: "extractor",
    passRate: 0.93,
    nodes: 64,
    cost: 5.8,
    notes: "field 填充率均 ≥ 0.85",
  },
  {
    agent: "analyst",
    passRate: 0.88,
    nodes: 32,
    cost: 5.1,
    notes: "1 次 INSUFFICIENT_EVIDENCE · 已回流",
  },
  {
    agent: "reporter",
    passRate: 0.71,
    nodes: 32,
    cost: 5.6,
    notes: "9 次 rework · 2 次用户 override v1",
  },
  {
    agent: "qa",
    passRate: 0.94,
    nodes: 32,
    cost: 3.3,
    notes: "6 维度覆盖 · 平均 8.4s · 0 次 MAX_RETRY_REACHED",
  },
];

const MODELS = [
  { name: "claude-sonnet-4-6", calls: 286, tokens: 1_240_510, costRatio: 0.62 },
  { name: "claude-opus-4-7", calls: 18, tokens: 96_320, costRatio: 0.24 },
  { name: "deepseek-chat", calls: 42, tokens: 211_400, costRatio: 0.09 },
  { name: "qwen-max", calls: 14, tokens: 78_280, costRatio: 0.05 },
];

const RECENT_ACTIVITY = [
  { at: "2m", text: "协作办公 Demo · qa rework", tone: "rework" },
  { at: "8m", text: "Linear vs Jira · run #02 v2 published", tone: "success" },
  { at: "12m", text: "Shopify 生态 · run #01 started", tone: "running" },
  { at: "27m", text: "AI Agent 平台对比 · failed at extractor", tone: "error" },
  { at: "1h", text: "CRM Q1 · v3 published", tone: "success" },
  { at: "2h", text: "客户支持平台对比 · archived (3 months)", tone: "neutral" },
];

const TONE_DOT: Record<string, string> = {
  success: "bg-success-base",
  running: "bg-running-base",
  rework: "bg-rework-base",
  error: "bg-error-base",
  neutral: "bg-neutral-base",
};

export function GlobalMetricsDashboard() {
  const [range, setRange] = useState<Range>("7d");

  const { data: agg } = useSWR<AggregateMetricsResponse>(
    "/api/metrics/aggregate",
    () => aggregateMetrics(),
    { refreshInterval: 30000, revalidateOnFocus: false }
  );

  const activeProjects = agg
    ? agg.project_count -
      (agg.by_status?.deleted ?? 0) -
      (agg.by_status?.archived ?? 0)
    : MOCK_PROJECTS.filter((p) => !p.archived).length;

  /* 真实数：把 6 张卡的 value / rawValue 用 aggregate 覆盖
     （trend 数组依旧是 mock 7d preview —— 后端 timeseries 是 per-project 的，
     全局趋势需要 /metrics/aggregate?bucket=day 后端再做）*/
  const cards = useMemo<GlobalCore[]>(() => {
    if (!agg) return GLOBAL_METRICS_7D;
    const fmt = (n: number) => n.toFixed(2);
    return GLOBAL_METRICS_7D.map((m) => {
      switch (m.id) {
        case "accuracy":
          return { ...m, value: fmt(agg.avg_accuracy), rawValue: agg.avg_accuracy };
        case "coverage":
          return { ...m, value: fmt(agg.avg_coverage), rawValue: agg.avg_coverage };
        case "edit_rate":
          return { ...m, value: fmt(agg.avg_edit_rate), rawValue: agg.avg_edit_rate };
        case "cost":
          return {
            ...m,
            value: `$${agg.total_cost_usd.toFixed(2)}`,
            rawValue: agg.total_cost_usd,
          };
        case "runs":
          return {
            ...m,
            value: String(agg.project_count),
            rawValue: agg.project_count,
            label: "Projects",
            hint: "项目总数（含 archived / deleted）",
          };
        case "qa_pass":
          // 没有直接字段；用 finished_project_count / project_count 当近似
          const passRate =
            agg.project_count > 0
              ? agg.finished_project_count / agg.project_count
              : 0;
          return { ...m, value: fmt(passRate), rawValue: passRate };
        default:
          return m;
      }
    });
  }, [agg]);

  return (
    <div className="space-y-6">
      <Header
        range={range}
        onChangeRange={setRange}
        activeProjects={activeProjects}
        hasApi={agg !== undefined}
      />

      <section>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          {cards.map((m) => (
            <GlobalMetricCard key={m.id} metric={m} />
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <AgentHealthTable />
        <RecentActivity />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ModelMix />
        <CostTrendChart />
      </div>

      <FooterNote />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function Header({
  range,
  onChangeRange,
  activeProjects,
  hasApi,
}: {
  range: Range;
  onChangeRange: (r: Range) => void;
  activeProjects: number;
  hasApi: boolean;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle pb-4">
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
          Metrics · global dashboard
        </div>
        <h1 className="mt-1 text-xl font-semibold text-text-primary">
          平台运行健康度
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          <span className="font-mono tabular-nums" data-num>
            {activeProjects}
          </span>{" "}
          active projects ·{" "}
          {hasApi
            ? "headline 数字来自 /api/metrics/aggregate · 每 30s 刷新"
            : "等后端 /api/metrics/aggregate 上线后自动接入"}
        </p>
      </div>
      <div className="flex items-center gap-1.5">
        <CalendarIcon className="h-3.5 w-3.5 text-text-muted" />
        {RANGE_OPTIONS.map((o) => {
          const active = o.id === range;
          return (
            <button
              key={o.id}
              type="button"
              onClick={() => onChangeRange(o.id)}
              className={cn(
                "rounded-pill border px-2.5 py-0.5 text-xs font-medium transition-colors duration-120 ease-out-quart",
                active
                  ? "border-accent-border bg-accent-bg text-accent-base"
                  : "border-border-subtle bg-bg-raised text-text-secondary hover:border-border-default"
              )}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </header>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function GlobalMetricCard({ metric }: { metric: GlobalCore }) {
  const Arrow = metric.delta.startsWith("-") ? TrendingDownIcon : TrendingUpIcon;
  const sparkColor =
    metric.trendTone === "good"
      ? "text-success-base"
      : metric.trendTone === "warn"
        ? "text-warning-base"
        : "text-accent-base";

  return (
    <div className="rounded-md border border-border-subtle bg-bg-raised p-3">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          {metric.label}
        </span>
        <span
          className={cn(
            "inline-flex items-center gap-0.5 text-[10px] font-medium",
            metric.positive ? "text-success-base" : "text-error-base"
          )}
        >
          <Arrow className="h-3 w-3" />
          <span className="font-mono tabular-nums" data-num>
            {metric.delta}
          </span>
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline justify-between gap-2">
        <span
          className="font-mono text-xl font-semibold tabular-nums text-text-primary"
          data-num
        >
          {metric.value}
        </span>
        <span className={cn("shrink-0", sparkColor)}>
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

function AgentHealthTable() {
  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised">
      <header className="border-b border-border-subtle px-5 py-3">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Per-agent health · cross-projects
        </div>
      </header>
      <table className="w-full text-xs">
        <thead className="text-[10px] uppercase tracking-wider text-text-muted">
          <tr>
            <th className="px-5 py-2 text-left font-medium">Agent</th>
            <th className="px-3 py-2 text-left font-medium">Pass</th>
            <th className="px-3 py-2 text-right font-medium">Nodes</th>
            <th className="px-3 py-2 text-right font-medium">Cost</th>
            <th className="px-5 py-2 text-left font-medium">Notes</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {AGENT_HEALTH.map((a) => (
            <tr key={a.agent}>
              <td className="px-5 py-2.5">
                <code className="font-mono font-medium text-text-primary">
                  {a.agent}
                </code>
              </td>
              <td className="px-3 py-2.5 w-[140px]">
                <PassMini ratio={a.passRate} />
              </td>
              <td
                className="px-3 py-2.5 text-right font-mono text-text-secondary tabular-nums"
                data-num
              >
                {a.nodes}
              </td>
              <td
                className="px-3 py-2.5 text-right font-mono text-text-secondary tabular-nums"
                data-num
              >
                ${a.cost.toFixed(2)}
              </td>
              <td className="px-5 py-2.5 text-text-secondary text-[11px]">
                {a.notes}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function PassMini({ ratio }: { ratio: number }) {
  const tone =
    ratio >= 0.9 ? "success" : ratio >= 0.75 ? "warning" : "error";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-pill bg-bg-sunken">
        <div
          className={cn(
            "h-full rounded-pill",
            tone === "success" && "bg-success-base",
            tone === "warning" && "bg-warning-base",
            tone === "error" && "bg-error-base"
          )}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
      <span
        className="font-mono text-[11px] font-medium text-text-primary tabular-nums w-9 text-right"
        data-num
      >
        {(ratio * 100).toFixed(0)}%
      </span>
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function RecentActivity() {
  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised">
      <header className="flex items-center gap-2 border-b border-border-subtle px-5 py-3">
        <ActivityIcon className="h-3.5 w-3.5 text-text-muted" />
        <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Recent activity
        </span>
      </header>
      <ul className="divide-y divide-border-subtle">
        {RECENT_ACTIVITY.map((a, i) => (
          <li
            key={i}
            className="flex items-center gap-3 px-5 py-2 text-xs"
          >
            <span
              className={cn(
                "h-1.5 w-1.5 shrink-0 rounded-pill",
                TONE_DOT[a.tone] ?? "bg-neutral-base"
              )}
            />
            <span
              className="font-mono text-[10px] text-text-muted tabular-nums w-8"
              data-num
            >
              {a.at}
            </span>
            <span className="flex-1 truncate text-text-secondary">{a.text}</span>
          </li>
        ))}
      </ul>
      <Link
        href="/projects"
        className="block border-t border-border-subtle px-5 py-2 text-[11px] text-text-muted hover:text-text-secondary"
      >
        → view all projects
      </Link>
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function ModelMix() {
  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Model usage · cost share
        </div>
      </header>
      <div className="flex items-center overflow-hidden rounded-md border border-border-subtle">
        {MODELS.map((m, i) => {
          const COLORS = ["bg-viz-1", "bg-viz-2", "bg-viz-3", "bg-viz-5"];
          return (
            <div
              key={m.name}
              className={cn("h-8", COLORS[i] ?? "bg-viz-6")}
              style={{ width: `${m.costRatio * 100}%` }}
              title={`${m.name} · ${(m.costRatio * 100).toFixed(0)}%`}
            />
          );
        })}
      </div>
      <ul className="mt-3 space-y-1.5">
        {MODELS.map((m, i) => {
          const COLORS = ["bg-viz-1", "bg-viz-2", "bg-viz-3", "bg-viz-5"];
          return (
            <li key={m.name} className="grid grid-cols-[12px_1fr_auto_auto] items-center gap-3 text-xs">
              <span
                className={cn("h-2 w-2 rounded-pill", COLORS[i] ?? "bg-viz-6")}
              />
              <code className="font-mono text-text-primary">{m.name}</code>
              <span
                className="text-text-muted font-mono tabular-nums"
                data-num
              >
                {m.calls} calls
              </span>
              <span
                className="text-text-secondary font-mono tabular-nums"
                data-num
              >
                {(m.tokens / 1000).toFixed(0)}k tok
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function CostTrendChart() {
  /* 30 个数据点的成本趋势 */
  const data = [
    1.2, 1.4, 1.3, 1.1, 1.5, 1.6, 1.4, 1.2, 1.1, 0.9, 1.0, 1.2, 1.4, 1.3, 1.1, 1.0,
    0.9, 0.8, 0.7, 0.9, 1.0, 1.1, 0.8, 0.7, 0.6, 0.6, 0.7, 0.5, 0.4, 0.3,
  ];
  const max = Math.max(...data);
  const avg = data.reduce((a, b) => a + b, 0) / data.length;

  return (
    <section className="rounded-lg border border-border-subtle bg-bg-raised p-5">
      <header className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
            Daily cost · trend
          </div>
        </div>
        <div className="text-xs">
          <span className="text-text-muted">avg </span>
          <span
            className="font-mono font-medium text-text-primary tabular-nums"
            data-num
          >
            ${avg.toFixed(2)}
          </span>
          <span className="text-text-muted"> / day</span>
        </div>
      </header>

      <div className="flex h-24 items-end gap-0.5">
        {data.map((v, i) => {
          const h = (v / max) * 100;
          return (
            <div
              key={i}
              className="flex-1 rounded-t-sm bg-accent-base/60 hover:bg-accent-base transition-colors duration-120 ease-out-quart"
              style={{ height: `${h}%` }}
              title={`day -${data.length - i} · $${v.toFixed(2)}`}
            />
          );
        })}
      </div>
      <div
        className="mt-2 flex items-center justify-between text-[10px] text-text-muted font-mono tabular-nums"
        data-num
      >
        <span>-30 d</span>
        <span>today</span>
      </div>
    </section>
  );
}

/* ──────────────────────────────────────────────────────────────────────── */

function FooterNote() {
  return (
    <div className="rounded-md border border-dashed border-border-default bg-bg-sunken/60 px-4 py-3 text-[11px] text-text-muted leading-relaxed">
      全局指标 endpoint 尚未在后端 v1 暴露——本页展示预置视觉素材 +
      <code className="font-mono">{" GET /api/projects "}</code>
      聚合的 active projects 数。后端落 <code className="font-mono">GET /api/metrics/global</code>{" "}
      后所有数字会从真实 trace / verdict / intervention 事件流自动计算。
      告警阈值规则、Slack / 邮件 webhook 配置见{" "}
      <code className="font-mono">docs/METRICS.md § 7</code>。
    </div>
  );
}
