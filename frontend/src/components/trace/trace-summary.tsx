"use client";

import {
  GitCompareArrowsIcon,
  FilterIcon,
  CheckCircle2Icon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { TraceSummary } from "@/lib/trace-mock";

export type TraceFilter = "all" | "errors" | "rework";

const FILTERS: Array<{ id: TraceFilter; label: string }> = [
  { id: "all", label: "all" },
  { id: "rework", label: "rework only" },
  { id: "errors", label: "errors only" },
];

const AGENTS = [
  "collector",
  "extractor",
  "analyst",
  "reporter",
  "qa",
];

/**
 * Trace 顶部摘要 + 过滤器 + diff button。
 * 总览：spanCount / 总时长 / 总 token / 总 cost
 * 状态分布微图（success / running / rework / pending）
 * 右上角入口：⇄ diff v1 ↔ v2 → 打开 Sheet
 */
export function TraceHeader({
  summary,
  filter,
  onFilterChange,
  agentFilter,
  onAgentFilterChange,
  onOpenDiff,
}: {
  summary: TraceSummary;
  filter: TraceFilter;
  onFilterChange: (f: TraceFilter) => void;
  agentFilter: Set<string>;
  onAgentFilterChange: (next: Set<string>) => void;
  onOpenDiff: () => void;
}) {
  const toggleAgent = (a: string) => {
    const next = new Set(agentFilter);
    if (next.has(a)) next.delete(a);
    else next.add(a);
    onAgentFilterChange(next);
  };
  const durSec = (summary.totalDurationMs / 1000).toFixed(1);

  return (
    <div className="rounded-lg border border-border-subtle bg-bg-raised">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-border-subtle px-5 py-4">
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
            Trace
          </div>
          <code className="mt-0.5 block font-mono text-sm font-medium text-text-primary">
            {summary.traceId}
          </code>
        </div>
        <div className="flex flex-wrap items-center gap-5 text-xs">
          <Stat label="spans" value={String(summary.spanCount)} mono />
          <Stat label="elapsed" value={`${durSec}s`} mono />
          <Stat
            label="tokens"
            value={(summary.totalTokensIn + summary.totalTokensOut).toLocaleString()}
            mono
          />
          <Stat label="cost" value={`$${summary.totalCostUsd.toFixed(3)}`} mono />
          <StatusMix summary={summary} />
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
        <div className="flex items-center gap-1.5">
          <FilterIcon className="h-3.5 w-3.5 text-text-muted" />
          {FILTERS.map((f) => {
            const active = f.id === filter;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => onFilterChange(f.id)}
                className={cn(
                  "rounded-pill border px-2.5 py-0.5 text-xs font-medium transition-colors duration-120 ease-out-quart",
                  active
                    ? "border-accent-border bg-accent-bg text-accent-base"
                    : "border-transparent bg-bg-sunken text-text-muted hover:text-text-secondary"
                )}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onOpenDiff}
          className="gap-1.5"
        >
          <GitCompareArrowsIcon className="h-3.5 w-3.5" />
          <span>diff v1 ↔ v2</span>
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t border-border-subtle px-5 py-2.5">
        <span className="text-[10px] uppercase tracking-wider text-text-muted">
          agent
        </span>
        {AGENTS.map((a) => {
          const active = agentFilter.has(a);
          return (
            <button
              key={a}
              type="button"
              onClick={() => toggleAgent(a)}
              className={cn(
                "rounded-pill border px-2 py-0.5 text-[11px] font-mono transition-colors duration-120 ease-out-quart",
                active
                  ? "border-accent-border bg-accent-bg text-accent-base"
                  : "border-transparent text-text-muted hover:text-text-secondary"
              )}
            >
              {a}
            </button>
          );
        })}
        {agentFilter.size > 0 ? (
          <button
            type="button"
            onClick={() => onAgentFilterChange(new Set())}
            className="ml-1 inline-flex items-center gap-0.5 text-[11px] text-text-muted hover:text-text-secondary"
          >
            <XIcon className="h-3 w-3" />
            clear
          </button>
        ) : null}
      </div>
    </div>
  );
}

/* ── pieces ────────────────────────────────────────────────────────────── */

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
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

function StatusMix({ summary }: { summary: TraceSummary }) {
  const items: Array<{ label: string; count: number; cls: string }> = [
    { label: "success", count: summary.successCount, cls: "bg-success-base" },
    { label: "running", count: summary.runningCount, cls: "bg-running-base" },
    { label: "rework", count: summary.reworkCount, cls: "bg-rework-base" },
    { label: "failed", count: summary.failedCount, cls: "bg-error-base" },
    { label: "pending", count: summary.pendingCount, cls: "bg-neutral-base" },
  ].filter((x) => x.count > 0);

  return (
    <span className="inline-flex items-center gap-2">
      <CheckCircle2Icon className="h-3.5 w-3.5 text-text-muted" />
      {items.map((x) => (
        <span
          key={x.label}
          className="inline-flex items-center gap-1 text-text-secondary"
          title={x.label}
        >
          <span className={cn("h-1.5 w-1.5 rounded-pill", x.cls)} />
          <span className="font-mono text-[11px]">{x.count}</span>
        </span>
      ))}
    </span>
  );
}
