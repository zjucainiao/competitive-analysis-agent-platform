"use client";

import { GitCommitIcon, GitBranchIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 报告顶部的版本切换条。
 *  v1 published   /   v2 preview (reporter_v2 running)
 *
 * 评分项：「Agent 决策回放（v1 ↔ v2 diff）」
 */
export function EditHistoryToggle({
  showV2,
  onChange,
  v2NodeRunning,
  pendingDiffCount,
  userEditCount,
}: {
  showV2: boolean;
  onChange: (showV2: boolean) => void;
  v2NodeRunning: boolean;
  pendingDiffCount: number;
  userEditCount: number;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border-subtle bg-bg-raised px-4 py-3">
      <div className="flex items-center gap-1">
        <ToggleBtn
          active={!showV2}
          onClick={() => onChange(false)}
          icon={<GitCommitIcon className="h-3.5 w-3.5" />}
          label="v1 · current"
          sub="published"
        />
        <ToggleBtn
          active={showV2}
          onClick={() => onChange(true)}
          icon={<GitBranchIcon className="h-3.5 w-3.5" />}
          label="v2 · preview"
          sub={
            v2NodeRunning
              ? `修订生成中 · ${pendingDiffCount} 项`
              : `${pendingDiffCount} 项`
          }
          highlight
        />
      </div>

      <div className="flex items-center gap-4 text-xs text-text-muted">
        {userEditCount > 0 ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-pill bg-accent-base" />
            <span>用户编辑</span>
            <span className="font-mono tabular-nums" data-num>
              {userEditCount}
            </span>
            <span>项</span>
          </span>
        ) : null}
        <span className="text-[10px] uppercase tracking-wider">
          metrics.edit_rate · auto-tracked
        </span>
      </div>
    </div>
  );
}

function ToggleBtn({
  active,
  onClick,
  icon,
  label,
  sub,
  highlight,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  sub: string;
  highlight?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-left transition-colors duration-120 ease-out-quart",
        active
          ? "border-accent-border bg-accent-bg text-text-primary"
          : "border-transparent bg-transparent text-text-muted hover:bg-bg-hover hover:text-text-secondary"
      )}
    >
      <span
        className={cn(
          active ? "text-accent-base" : "text-text-muted",
          highlight && !active && "text-running-base"
        )}
      >
        {icon}
      </span>
      <span>
        <span
          className={cn(
            "block text-xs font-medium",
            active ? "text-text-primary" : "text-text-secondary"
          )}
        >
          {label}
        </span>
        <span className="block text-[10px] text-text-muted">{sub}</span>
      </span>
    </button>
  );
}
