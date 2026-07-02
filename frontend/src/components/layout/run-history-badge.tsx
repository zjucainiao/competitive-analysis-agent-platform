"use client";

import { useRef } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ChevronDownIcon,
  CircleCheckIcon,
  AlertTriangleIcon,
  OctagonXIcon,
  LoaderIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useRunHistory } from "@/lib/api/hooks";
import type { RunRef } from "@/lib/api/types";

/**
 * 项目运行历史下拉 —— 可点击切换。
 *
 * - 只在 API 模式有 ≥1 个 run 时显示
 * - 用原生 details/summary 避免引入 popover 依赖
 * - 数据源 GET /projects/{id}/runs（listRuns），props.runs 作首屏兜底；
 *   展开时再 revalidate 一次，保证 final_status / 新 run 及时
 * - 点击某个 run → 跳到 /projects/{id}/runs/{run_id}（保留当前 tab）：
 *   最新 run 走实时视图，历史 run 走只读回放
 */
export function RunHistoryBadge({
  projectId,
  runs,
  activeRunId,
}: {
  projectId: string;
  runs: RunRef[];
  activeRunId: string;
}) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const searchParams = useSearchParams();
  const tab = searchParams.get("tab") ?? "dag";
  const { data, mutate } = useRunHistory(projectId, runs);
  const list = data?.runs ?? runs;

  if (list.length === 0) return null;
  // 最新在最前面
  const ordered = [...list].reverse();
  const latestRunId = ordered[0]?.run_id ?? null;

  const closeMenu = () => detailsRef.current?.removeAttribute("open");

  return (
    <details
      ref={detailsRef}
      className="group relative"
      onToggle={(e) => {
        // 展开时刷新一次运行历史（final_status / 新 run 可能已变化）
        if ((e.target as HTMLDetailsElement).open) void mutate();
      }}
    >
      <summary
        className="inline-flex cursor-pointer items-center gap-1 rounded-pill border border-border-default bg-bg-raised px-2 py-0.5 text-[11px] font-medium text-text-secondary transition-colors duration-120 hover:border-border-strong hover:text-text-primary"
        title="运行历史"
      >
        <span className="font-mono tabular-nums" data-num>
          {list.length} 次运行
        </span>
        <ChevronDownIcon className="h-3 w-3 transition-transform group-open:rotate-180" />
      </summary>
      <div
        className="absolute left-0 top-full z-40 mt-1 w-[320px] overflow-hidden rounded-md border border-border-default bg-bg-overlay shadow-popover"
        role="menu"
      >
        <div className="border-b border-border-subtle px-3 py-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
          运行历史 · 共 {list.length} 次 · 点击切换
        </div>
        <ol className="max-h-[280px] divide-y divide-border-subtle overflow-y-auto">
          {ordered.map((r, i) => (
            <li key={r.run_id}>
              <Link
                href={`/projects/${encodeURIComponent(
                  projectId
                )}/runs/${encodeURIComponent(r.run_id)}?tab=${tab}`}
                onClick={closeMenu}
                className={cn(
                  "flex items-center gap-2 px-3 py-2 text-xs transition-colors duration-120",
                  r.run_id === activeRunId
                    ? "bg-accent-bg/40 text-text-primary"
                    : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
                )}
              >
                <StatusIcon status={r.final_status} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span
                      className="font-mono text-[10px] tabular-nums"
                      data-num
                    >
                      #{String(ordered.length - i).padStart(2, "0")}
                    </span>
                    <code className="truncate font-mono text-[10px] text-text-muted">
                      {r.run_id}
                    </code>
                    {r.run_id === activeRunId ? (
                      <span className="ml-auto shrink-0 rounded-pill bg-accent-base/15 px-1.5 py-px text-[9px] font-medium text-accent-base">
                        当前查看
                      </span>
                    ) : r.run_id === latestRunId ? (
                      <span className="ml-auto shrink-0 rounded-pill bg-bg-sunken px-1.5 py-px text-[9px] font-medium text-text-muted">
                        最新
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
                        <span className="text-running-base">进行中</span>
                      </>
                    )}
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ol>
      </div>
    </details>
  );
}

function StatusIcon({ status }: { status: string | null }) {
  if (status === "done") {
    return (
      <CircleCheckIcon className="h-3.5 w-3.5 text-success-base shrink-0" />
    );
  }
  if (status === "failed") {
    return <OctagonXIcon className="h-3.5 w-3.5 text-error-base shrink-0" />;
  }
  if (status === "stopped") {
    return (
      <AlertTriangleIcon className="h-3.5 w-3.5 text-warning-base shrink-0" />
    );
  }
  return (
    <LoaderIcon className="h-3.5 w-3.5 text-running-base shrink-0 animate-spin" />
  );
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
