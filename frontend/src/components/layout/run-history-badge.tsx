"use client";

import { ChevronDownIcon, CircleCheckIcon, AlertTriangleIcon, OctagonXIcon, LoaderIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RunRef } from "@/lib/api/types";

/**
 * 项目运行历史下拉。
 *
 * - 只在 API 模式有 ≥1 个 run 时显示
 * - 用原生 details/summary 避免引入 popover 依赖
 * - 当前 active run 高亮；过去 run 显示 timestamp + final_status；点击仅 toast
 *   （v1 不实现 RunSnapshot 切换 UI——后端 GET /runs/{run_id}/state 已经准备好，
 *   只是 workspace 当前只显示 latest，切换历史 view 是更深的 refactor）
 */
export function RunHistoryBadge({
  runs,
  activeRunId,
}: {
  runs: RunRef[];
  activeRunId: string;
}) {
  if (runs.length === 0) return null;
  // 最新在最前面
  const ordered = [...runs].reverse();

  return (
    <details className="group relative">
      <summary
        className="inline-flex cursor-pointer items-center gap-1 rounded-pill border border-border-default bg-bg-raised px-2 py-0.5 text-[11px] font-medium text-text-secondary transition-colors duration-120 hover:border-border-strong hover:text-text-primary"
        title="切换 run 快照（v1 read-only 列表）"
      >
        <span className="font-mono tabular-nums" data-num>
          {runs.length} runs
        </span>
        <ChevronDownIcon className="h-3 w-3 transition-transform group-open:rotate-180" />
      </summary>
      <div
        className="absolute left-0 top-full z-40 mt-1 w-[320px] overflow-hidden rounded-md border border-border-default bg-bg-overlay shadow-popover"
        role="menu"
      >
        <div className="border-b border-border-subtle px-3 py-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
          Run history · {runs.length} 次
        </div>
        <ol className="max-h-[280px] divide-y divide-border-subtle overflow-y-auto">
          {ordered.map((r, i) => (
            <li
              key={r.run_id}
              className={cn(
                "flex items-center gap-2 px-3 py-2 text-xs",
                r.run_id === activeRunId
                  ? "bg-accent-bg/40 text-text-primary"
                  : "text-text-secondary"
              )}
            >
              <StatusIcon status={r.final_status} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-[10px] tabular-nums" data-num>
                    #{String(ordered.length - i).padStart(2, "0")}
                  </span>
                  <code className="truncate font-mono text-[10px] text-text-muted">
                    {r.run_id}
                  </code>
                  {r.run_id === activeRunId ? (
                    <span className="ml-auto rounded-pill bg-accent-base/15 px-1.5 py-px text-[9px] font-medium text-accent-base">
                      current
                    </span>
                  ) : null}
                </div>
                <div className="mt-0.5 flex items-center gap-1 text-[10px] text-text-muted">
                  <span>{formatStarted(r.started_at)}</span>
                  {r.ended_at ? (
                    <>
                      <span>·</span>
                      <span>{formatDuration(r.started_at, r.ended_at)}</span>
                    </>
                  ) : (
                    <>
                      <span>·</span>
                      <span className="text-running-base">in progress</span>
                    </>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
        <div className="border-t border-border-subtle bg-bg-sunken/60 px-3 py-2 text-[10px] leading-snug text-text-muted">
          切换到历史 run 的完整 state 走 GET
          <code className="mx-1 font-mono">/runs/{`{run_id}`}/state</code>
          · v1 暂未实现快照视图，列表仅 read-only
        </div>
      </div>
    </details>
  );
}

function StatusIcon({ status }: { status: string | null }) {
  if (status === "done") {
    return <CircleCheckIcon className="h-3.5 w-3.5 text-success-base shrink-0" />;
  }
  if (status === "failed") {
    return <OctagonXIcon className="h-3.5 w-3.5 text-error-base shrink-0" />;
  }
  if (status === "stopped") {
    return <AlertTriangleIcon className="h-3.5 w-3.5 text-warning-base shrink-0" />;
  }
  return <LoaderIcon className="h-3.5 w-3.5 text-running-base shrink-0 animate-spin" />;
}

function formatStarted(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDuration(startIso: string, endIso: string): string {
  try {
    const s = new Date(startIso).getTime();
    const e = new Date(endIso).getTime();
    const ms = Math.max(0, e - s);
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.round(ms / 60_000)}m`;
  } catch {
    return "—";
  }
}
