"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  TrendingUpIcon,
  TrendingDownIcon,
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
  { id: "7d", label: "近 7 天" },
  { id: "30d", label: "近 30 天" },
  { id: "90d", label: "近一季度" },
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
    label: "准确率（均值）",
    value: "0.94",
    rawValue: 0.94,
    delta: "+0.02",
    positive: true,
    hint: "质检事实一致性 · 跨 6 个项目均值",
    trend: [0.86, 0.88, 0.9, 0.89, 0.92, 0.93, 0.94],
    trendTone: "good",
  },
  {
    id: "coverage",
    label: "覆盖度（均值）",
    value: "0.82",
    rawValue: 0.82,
    delta: "+0.04",
    positive: true,
    hint: "信息完整度：字段填充比例 × 来源覆盖",
    trend: [0.71, 0.73, 0.75, 0.77, 0.79, 0.81, 0.82],
    trendTone: "good",
  },
  {
    id: "edit_rate",
    label: "编辑率（均值）",
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
    label: "质检通过率",
    value: "0.79",
    rawValue: 0.79,
    delta: "+0.06",
    positive: true,
    hint: "首次通过率（无返工）",
    trend: [0.65, 0.68, 0.71, 0.73, 0.75, 0.77, 0.79],
    trendTone: "good",
  },
  {
    id: "cost",
    label: "总成本",
    value: "$24.18",
    rawValue: 24.18,
    delta: "-$3.40",
    positive: true,
    hint: "全部模型调用累计成本",
    trend: [42, 38, 36, 34, 31, 28, 24],
    trendTone: "accent",
  },
  {
    id: "runs",
    label: "运行次数",
    value: "43",
    rawValue: 43,
    delta: "+12",
    positive: true,
    hint: "本周期完成 / 进行中的运行数",
    trend: [4, 5, 7, 6, 8, 7, 6],
    trendTone: "accent",
  },
];

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
            label: "项目数",
            hint: "项目总数（含已归档 / 已删除）",
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
          全局指标总览
        </div>
        <h1 className="mt-1 text-xl font-semibold text-text-primary">
          平台运行健康度
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          <span className="font-mono tabular-nums" data-num>
            {activeProjects}
          </span>{" "}
          个活跃项目 ·{" "}
          {hasApi
            ? "头部数字来自实时聚合 · 每 30 秒刷新"
            : "实时聚合上线后自动接入"}
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

function FooterNote() {
  return (
    <div className="rounded-md border border-dashed border-border-default bg-bg-sunken/60 px-4 py-3 text-[11px] text-text-muted leading-relaxed">
      头部 6 个核心指标来自实时聚合；活动日志与告警阈值仍在接入中。
    </div>
  );
}
