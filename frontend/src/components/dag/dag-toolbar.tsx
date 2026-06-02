"use client";

import { PlayIcon, PauseIcon, FastForwardIcon, RotateCcwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/layout/status-pill";
import { cn } from "@/lib/utils";
import type { DagRunSummary } from "@/lib/dag-mock";

/**
 * DAG canvas 顶部工具条。
 *  - 左：图例 + Play/Pause + 时间滑块 + phase 标签
 *  - 右：summary stats（节点 / token / cost / 时长）+ live indicator
 *
 * Sprint 1 时只静态显示当前 snapshot；本版本加上 Play 回放 + slider。
 */
export function DagToolbar({
  summary,
  phaseIdx,
  phaseCount,
  phaseLabel,
  phaseDescription,
  isPlaying,
  isLive,
  replayDisabled = false,
  liveSource,
  onTogglePlay,
  onSeek,
  onJumpLive,
}: {
  summary: DagRunSummary;
  phaseIdx: number;
  phaseCount: number;
  phaseLabel: string;
  phaseDescription: string;
  isPlaying: boolean;
  isLive: boolean;
  /** API live 模式禁用 replay/slider */
  replayDisabled?: boolean;
  /** 数据源：'mock' = demo 路径硬编码 · 'ws' = WS 实时推送 · 'poll' = SWR 轮询兜底 */
  liveSource?: "mock" | "ws" | "poll";
  onTogglePlay: () => void;
  onSeek: (idx: number) => void;
  onJumpLive: () => void;
}) {
  const elapsedSec = Math.round(summary.elapsedMs / 1000);
  const sourceLabel = liveSource ?? "mock";
  const sourceTone: Record<"mock" | "ws" | "poll", { tooltip: string }> = {
    mock: { tooltip: "Demo 路径 · 数据来自 fixtures，不是后端" },
    ws: { tooltip: "WebSocket 已连接 · 后端真实推流" },
    poll: { tooltip: "WS 未连接 · 每 30 秒 SWR 轮询 /state 兜底" },
  };

  return (
    <div className="border-b border-border-subtle bg-bg-raised">
      {/* row 1 — legend + summary */}
      <div className="flex flex-wrap items-center gap-4 px-4 py-3">
        <Legend />
        <div className="ml-auto flex items-center gap-4 text-xs">
          <Stat
            label="completed"
            value={`${summary.completed}/${summary.totalNodes}`}
          />
          <Stat label="tokens" value={summary.totalTokens.toLocaleString()} />
          <Stat label="cost" value={`$${summary.totalCostUsd.toFixed(2)}`} />
          <Stat label="elapsed" value={`${elapsedSec}s`} />
          <Stat label="qa rounds" value={String(summary.qaRoundCount)} />
          <span
            title={sourceTone[sourceLabel].tooltip}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-xs font-medium",
              isLive
                ? sourceLabel === "ws"
                  ? "border-success-border bg-success-bg text-success-base"
                  : sourceLabel === "poll"
                    ? "border-warning-border bg-warning-bg text-warning-base"
                    : "border-running-border bg-running-bg text-running-base"
                : "border-rework-border bg-rework-bg text-rework-base"
            )}
          >
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-pill",
                isLive
                  ? sourceLabel === "ws"
                    ? "bg-success-base animate-pulse-soft"
                    : sourceLabel === "poll"
                      ? "bg-warning-base"
                      : "bg-running-base animate-pulse-soft"
                  : "bg-rework-base"
              )}
            />
            {isLive
              ? sourceLabel === "ws"
                ? "live · ws"
                : sourceLabel === "poll"
                  ? "live · polling"
                  : "live · mock"
              : `replay · phase ${phaseIdx + 1}/${phaseCount}`}
          </span>
        </div>
      </div>

      {/* row 2 — play controls + slider (隐藏于 API live 模式) */}
      {!replayDisabled ? (
      <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle px-4 py-2.5">
        <Button
          size="sm"
          variant={isPlaying ? "outline" : "default"}
          onClick={onTogglePlay}
          className="gap-1.5"
          title={isPlaying ? "Pause replay" : "Play DAG replay from current phase"}
        >
          {isPlaying ? (
            <PauseIcon className="h-3.5 w-3.5" />
          ) : phaseIdx === phaseCount - 1 ? (
            <RotateCcwIcon className="h-3.5 w-3.5" />
          ) : (
            <PlayIcon className="h-3.5 w-3.5" />
          )}
          <span>
            {isPlaying
              ? "Pause"
              : phaseIdx === phaseCount - 1
                ? "Replay run"
                : "Play"}
          </span>
        </Button>

        <div className="flex flex-1 items-center gap-3 min-w-0">
          <input
            type="range"
            min={0}
            max={phaseCount - 1}
            value={phaseIdx}
            onChange={(e) => onSeek(parseInt(e.target.value, 10))}
            className="dag-slider flex-1 min-w-[160px] accent-accent-base"
            aria-label="DAG replay phase"
          />
          <code
            className="font-mono text-[11px] text-text-secondary tabular-nums shrink-0"
            data-num
          >
            {phaseLabel}
          </code>
          <span
            className="hidden md:block truncate text-[11px] text-text-muted shrink min-w-0"
            title={phaseDescription}
          >
            {phaseDescription}
          </span>
        </div>

        {!isLive ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onJumpLive}
            className="gap-1.5"
            title="Jump to current live snapshot"
          >
            <FastForwardIcon className="h-3.5 w-3.5" />
            <span>Live</span>
          </Button>
        ) : null}
      </div>
      ) : null}
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
        legend
      </span>
      <StatusPill tone="success" label="success" />
      <StatusPill tone="running" label="running" pulse />
      <StatusPill tone="rework" label="rework" />
      <StatusPill tone="error" label="failed" />
      <StatusPill tone="neutral" label="pending" />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <span
        className="font-mono font-medium text-text-primary tabular-nums"
        data-num
      >
        {value}
      </span>
    </span>
  );
}
