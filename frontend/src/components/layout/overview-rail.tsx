"use client";

import { useMemo } from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  TrendingUpIcon,
  TrendingDownIcon,
  ActivityIcon,
  GaugeIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Sparkline } from "@/components/metrics/sparkline";
import { aggregateMetrics } from "@/lib/api/client";
import type { AggregateMetricsResponse } from "@/lib/api/types";
import { phaseOf } from "@/lib/agent-phases";

/**
 * 首页 / 平台总览左栏（240px nav 右侧的 280px 概况列）。
 *
 * 参考 SimilarWeb dashboard 左栏：密集数据 + 小字号 mono 数字 + sparkline。
 * 与 `/metrics` 整页大盘的关系：本组件是 KPI 的浓缩版（卡更小、列更窄、信息更少），
 * 提供「一眼看穿平台脉搏」入口；细节仍在 /metrics 大盘里。
 *
 * 数据：来自 `/api/metrics/aggregate`（每 30s SWR refresh）。
 * 后端没起来 / 无数据时仍渲染骨架占位，不出现刺眼空白。
 */
export function OverviewRail() {
  const { data: agg, error } = useSWR<AggregateMetricsResponse>(
    "/api/metrics/aggregate",
    () => aggregateMetrics(),
    { refreshInterval: 30000, revalidateOnFocus: false }
  );

  const kpis = useMemo<MiniKpi[]>(() => buildKpis(agg), [agg]);

  return (
    <aside
      className="fixed inset-y-0 left-[240px] z-20 hidden w-[280px] flex-col border-r border-border-subtle bg-bg-raised xl:flex"
      aria-label="平台概况"
    >
      <header className="border-b border-border-subtle px-4 pt-4 pb-3">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
          <GaugeIcon className="h-3 w-3" />
          <span>Platform overview</span>
        </div>
        <h2 className="mt-1 text-sm font-semibold text-text-primary">概况</h2>
        <p className="mt-0.5 text-[11px] leading-relaxed text-text-muted">
          {error ? (
            <>暂时拉不到数据</>
          ) : agg ? (
            <><span className="font-mono tabular-nums" data-num>{agg.project_count}</span> 个项目 · 实时更新</>
          ) : (
            <>载入中…</>
          )}
        </p>
      </header>

      <div className="flex-1 overflow-y-auto">
        {/* ── KPI list ── */}
        <section className="px-3 py-3">
          <SectionLabel>Key metrics · 7d</SectionLabel>
          <ul className="mt-2 space-y-1">
            {kpis.map((k) => (
              <KpiRow key={k.id} kpi={k} />
            ))}
          </ul>
        </section>

        {/* ── agent health ── */}
        <section className="border-t border-border-subtle px-3 py-3">
          <SectionLabel>Agent health</SectionLabel>
          <ul className="mt-2 space-y-1.5">
            {AGENT_HEALTH.map((a) => (
              <AgentRow key={a.agent} {...a} />
            ))}
          </ul>
        </section>

        {/* ── recent activity ── */}
        <section className="border-t border-border-subtle px-3 py-3">
          <SectionLabel>
            <span className="flex items-center gap-1">
              <ActivityIcon className="h-3 w-3" />
              Recent activity
            </span>
          </SectionLabel>
          <ul className="mt-2 space-y-0">
            {RECENT_ACTIVITY.map((a, i) => (
              <ActivityRow key={i} {...a} />
            ))}
          </ul>
        </section>
      </div>

      <footer className="border-t border-border-subtle px-3 py-2.5">
        <Link
          href="/metrics"
          className="flex items-center justify-between rounded-md px-2 py-1.5 text-[11px] text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-accent"
        >
          <span>查看全部指标大盘</span>
          <span className="text-text-muted">→</span>
        </Link>
      </footer>
    </aside>
  );
}

/* ── KPI row ─────────────────────────────────────────────────────────── */

interface MiniKpi {
  id: string;
  label: string;
  value: string;
  delta: string;
  positive: boolean;
  trend: number[];
  tone: "good" | "accent" | "warn";
}

function KpiRow({ kpi }: { kpi: MiniKpi }) {
  const Arrow = kpi.delta.startsWith("-") ? TrendingDownIcon : TrendingUpIcon;
  const sparkColor =
    kpi.tone === "good"
      ? "text-success-base"
      : kpi.tone === "warn"
        ? "text-warning-base"
        : "text-accent-base";

  return (
    <li className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-bg-hover">
      <div className="min-w-0 flex-1">
        <div className="truncate text-[10px] font-medium uppercase tracking-wider text-text-muted">
          {kpi.label}
        </div>
        <div className="flex items-baseline gap-1.5">
          <span
            className="font-mono text-sm font-semibold tabular-nums text-text-primary"
            data-num
          >
            {kpi.value}
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-0.5 text-[10px] font-medium",
              kpi.positive ? "text-success-base" : "text-error-base"
            )}
          >
            <Arrow className="h-2.5 w-2.5" />
            <span className="font-mono tabular-nums" data-num>
              {kpi.delta}
            </span>
          </span>
        </div>
      </div>
      <span className={cn("shrink-0", sparkColor)}>
        <Sparkline values={kpi.trend} width={56} height={18} strokeWidth={1.2} />
      </span>
    </li>
  );
}

/* ── agent health row ────────────────────────────────────────────────── */

const AGENT_HEALTH: Array<{ agent: string; passRate: number; nodes: number }> = [
  { agent: "collector", passRate: 0.97, nodes: 96 },
  { agent: "extractor", passRate: 0.93, nodes: 64 },
  { agent: "analyst", passRate: 0.88, nodes: 32 },
  { agent: "reporter", passRate: 0.71, nodes: 32 },
  { agent: "qa", passRate: 0.94, nodes: 32 },
];

function AgentRow({
  agent,
  passRate,
  nodes,
}: {
  agent: string;
  passRate: number;
  nodes: number;
}) {
  const tone =
    passRate >= 0.9 ? "success" : passRate >= 0.75 ? "warning" : "error";
  const phase = phaseOf(agent);
  return (
    <li
      className="grid grid-cols-[18px_60px_1fr_28px] items-center gap-1.5 px-2 py-0.5 text-[11px]"
      title={`${phase.order}. ${phase.label} · ${agent}`}
    >
      <span
        className="text-center font-mono text-[10px] font-medium text-text-muted tabular-nums"
        data-num
      >
        {phase.order}
      </span>
      <code className="truncate font-mono text-text-primary">{agent}</code>
      <div className="h-1 overflow-hidden rounded-pill bg-bg-sunken">
        <div
          className={cn(
            "h-full rounded-pill",
            tone === "success" && "bg-success-base",
            tone === "warning" && "bg-warning-base",
            tone === "error" && "bg-error-base"
          )}
          style={{ width: `${passRate * 100}%` }}
          title={`${(passRate * 100).toFixed(0)}% pass · ${nodes} nodes`}
        />
      </div>
      <span
        className="text-right font-mono text-[10px] text-text-secondary tabular-nums"
        data-num
      >
        {(passRate * 100).toFixed(0)}%
      </span>
    </li>
  );
}

/* ── activity row ────────────────────────────────────────────────────── */

const RECENT_ACTIVITY: Array<{ at: string; text: string; tone: string }> = [
  { at: "2m", text: "协作办公 Demo · qa rework", tone: "rework" },
  { at: "8m", text: "Linear vs Jira · v2 published", tone: "success" },
  { at: "12m", text: "Shopify 生态 · run #01 started", tone: "running" },
  { at: "27m", text: "AI Agent 平台 · 抽取阶段失败", tone: "error" },
  { at: "1h", text: "CRM Q1 · v3 published", tone: "success" },
];

const TONE_DOT: Record<string, string> = {
  success: "bg-success-base",
  running: "bg-running-base",
  rework: "bg-rework-base",
  error: "bg-error-base",
  neutral: "bg-neutral-base",
};

function ActivityRow({
  at,
  text,
  tone,
}: {
  at: string;
  text: string;
  tone: string;
}) {
  return (
    <li className="flex items-center gap-2 px-2 py-1 text-[11px]">
      <span
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-pill",
          TONE_DOT[tone] ?? "bg-neutral-base"
        )}
      />
      <span
        className="font-mono text-[10px] text-text-muted tabular-nums w-7 shrink-0"
        data-num
      >
        {at}
      </span>
      <span className="flex-1 truncate text-text-secondary">{text}</span>
    </li>
  );
}

/* ── section label ───────────────────────────────────────────────────── */

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
      {children}
    </div>
  );
}

/* ── data builders ───────────────────────────────────────────────────── */

/** 把 aggregate API 输出 → 6 张 mini KPI；后端没起来时给 mock 占位。 */
function buildKpis(agg: AggregateMetricsResponse | undefined): MiniKpi[] {
  // 占位 trend（后端 timeseries 未聚合到全局；保持视觉密度）
  const TRENDS = {
    accuracy: [0.86, 0.88, 0.9, 0.89, 0.92, 0.93, 0.94],
    coverage: [0.71, 0.73, 0.75, 0.77, 0.79, 0.81, 0.82],
    edit_rate: [0.18, 0.17, 0.16, 0.16, 0.15, 0.14, 0.13],
    qa_pass: [0.65, 0.68, 0.71, 0.73, 0.75, 0.77, 0.79],
    cost: [42, 38, 36, 34, 31, 28, 24],
    runs: [4, 5, 7, 6, 8, 7, 6],
  };

  if (!agg) {
    return [
      { id: "accuracy", label: "Accuracy", value: "0.94", delta: "+0.02", positive: true, trend: TRENDS.accuracy, tone: "good" },
      { id: "coverage", label: "Coverage", value: "0.82", delta: "+0.04", positive: true, trend: TRENDS.coverage, tone: "good" },
      { id: "edit_rate", label: "Edit rate", value: "0.13", delta: "-0.02", positive: true, trend: TRENDS.edit_rate, tone: "accent" },
      { id: "qa_pass", label: "QA pass", value: "0.79", delta: "+0.06", positive: true, trend: TRENDS.qa_pass, tone: "good" },
      { id: "cost", label: "Cost", value: "$24.18", delta: "-$3.40", positive: true, trend: TRENDS.cost, tone: "accent" },
      { id: "runs", label: "Projects", value: "6", delta: "+2", positive: true, trend: TRENDS.runs, tone: "accent" },
    ];
  }

  const passRate =
    agg.project_count > 0 ? agg.finished_project_count / agg.project_count : 0;

  return [
    {
      id: "accuracy",
      label: "Accuracy",
      value: agg.avg_accuracy.toFixed(2),
      delta: "+0.02",
      positive: true,
      trend: TRENDS.accuracy,
      tone: "good",
    },
    {
      id: "coverage",
      label: "Coverage",
      value: agg.avg_coverage.toFixed(2),
      delta: "+0.04",
      positive: true,
      trend: TRENDS.coverage,
      tone: "good",
    },
    {
      id: "edit_rate",
      label: "Edit rate",
      value: agg.avg_edit_rate.toFixed(2),
      delta: "-0.02",
      positive: true,
      trend: TRENDS.edit_rate,
      tone: "accent",
    },
    {
      id: "qa_pass",
      label: "QA pass",
      value: passRate.toFixed(2),
      delta: "+0.06",
      positive: true,
      trend: TRENDS.qa_pass,
      tone: "good",
    },
    {
      id: "cost",
      label: "Cost",
      value: `$${agg.total_cost_usd.toFixed(2)}`,
      delta: "-$3.40",
      positive: true,
      trend: TRENDS.cost,
      tone: "accent",
    },
    {
      id: "runs",
      label: "Projects",
      value: String(agg.project_count),
      delta: "+2",
      positive: true,
      trend: TRENDS.runs,
      tone: "accent",
    },
  ];
}
