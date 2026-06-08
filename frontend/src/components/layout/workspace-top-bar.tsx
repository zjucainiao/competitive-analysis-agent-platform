"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ClockIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
} from "lucide-react";
import { StatusPill, type StatusTone } from "./status-pill";
import { CmdTrigger } from "./cmd-trigger";
import { ThemeToggle } from "./theme-toggle";
import { RunHistoryBadge } from "./run-history-badge";
import { WorkspaceActions } from "./workspace-actions";
import { BrandMark } from "./brand-mark";
import { UserMenu } from "./user-menu";
import type { RunRef } from "@/lib/api/types";
import type { RunStatus } from "@/lib/workspace-actions";

/**
 * Workspace 全宽顶部栏（fixed, h-16）—— 参考 AgentResearch。
 *
 * 区域：
 *  - 左 ~280：BrandMark + 「Atlas / 竞品分析」品牌（占同时含侧栏顶部空间）
 *  - 中：项目面包屑 + 状态 pill + 整体进度条 + 百分比
 *  - 右：运行时长 + workspace actions + ⌘K + 主题 + 通知 + 头像
 */
export function WorkspaceTopBar({
  projectName,
  statusTone,
  statusLabel,
  statusPulse,
  progressDone,
  progressTotal,
  startedAt,
  endedAt,
  runStatus,
  runs,
  activeRunId,
  railOpen,
  onToggleRail,
}: {
  projectName: string;
  statusTone: StatusTone;
  statusLabel: string;
  statusPulse?: boolean;
  progressDone: number;
  progressTotal: number;
  startedAt: string | null;
  endedAt: string | null;
  runStatus: RunStatus;
  runs?: RunRef[];
  activeRunId?: string;
  railOpen?: boolean;
  onToggleRail?: () => void;
}) {
  const pct = (() => {
    if (progressTotal <= 0) return 0;
    const raw = Math.min(100, Math.round((progressDone / progressTotal) * 100));
    // 运行中(含 QA 返工)永不显示 100%：返工时 round-1 的节点已全部终态，raw 会到
    // 100，但 run 仍在迭代 → 「100% 却还在跑」自相矛盾。封顶 99，待真正跑完再满。
    if ((runStatus === "running" || runStatus === "rework") && raw >= 100) {
      return 99;
    }
    return raw;
  })();

  return (
    <div className="fixed inset-x-0 top-0 z-30 flex h-16 items-center border-b border-border-subtle bg-bg-overlay/80 backdrop-blur">
      {/* 品牌 —— 与下方 sidebar 同宽 80px */}
      <Link
        href="/"
        style={{ width: 80 }}
        className="flex h-full shrink-0 flex-col items-center justify-center gap-0.5 border-r border-border-subtle"
        aria-label="回首页"
      >
        <BrandMark className="h-7 w-7" />
        <span className="text-[10px] font-semibold tracking-tight text-text-primary">
          Atlas
        </span>
      </Link>

      {/* 中：面包屑 + 项目 + 状态 + 进度 */}
      <div className="flex min-w-0 flex-1 items-center gap-3 px-6">
        <Link
          href="/projects"
          className="shrink-0 text-xs text-text-muted hover:text-text-secondary"
        >
          项目
        </Link>
        <span className="text-xs text-text-muted">/</span>
        <span className="truncate text-sm font-semibold text-text-primary">
          {projectName}
        </span>
        <StatusPill
          tone={statusTone}
          label={statusLabel}
          pulse={statusPulse}
        />
        {runs && runs.length > 0 && activeRunId ? (
          <RunHistoryBadge runs={runs} activeRunId={activeRunId} />
        ) : null}

        {/* 中段进度 —— 占主要宽度 */}
        <div className="ml-4 hidden min-w-[200px] max-w-[420px] flex-1 items-center gap-3 md:flex">
          <span className="shrink-0 text-[11px] text-text-muted">整体进度</span>
          <div className="relative h-1.5 flex-1 overflow-hidden rounded-pill bg-bg-sunken">
            <div
              className="absolute inset-y-0 left-0 rounded-pill bg-accent-base transition-[width] duration-500 ease-out-quart"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span
            className="shrink-0 font-mono text-xs font-semibold tabular-nums text-text-primary"
            data-num
          >
            {pct}%
          </span>
        </div>
      </div>

      {/* 右：runtime + actions */}
      <div className="flex shrink-0 items-center gap-2 px-4">
        <RunTimer startedAt={startedAt} endedAt={endedAt} runStatus={runStatus} />
        <div className="hidden lg:flex">
          <WorkspaceActions status={runStatus} />
        </div>
        <CmdTrigger />
        <ThemeToggle />
        {onToggleRail ? (
          <RailToggleButton open={!!railOpen} onClick={onToggleRail} />
        ) : null}
        <UserMenu />
      </div>
    </div>
  );
}

/* ── run timer ───────────────────────────────────────────────────────── */

function RunTimer({
  startedAt,
  endedAt,
  runStatus,
}: {
  startedAt: string | null;
  endedAt: string | null;
  runStatus: RunStatus;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (runStatus !== "running" && runStatus !== "rework") return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [runStatus]);

  const duration = (() => {
    if (!startedAt) return null;
    const start = Date.parse(startedAt);
    if (Number.isNaN(start)) return null;
    const end = endedAt ? Date.parse(endedAt) : now;
    return Math.max(0, end - start);
  })();

  if (duration == null) return null;

  return (
    <div className="hidden items-center gap-1.5 rounded-md border border-border-subtle bg-bg-raised/60 px-2.5 py-1 text-xs lg:flex">
      <ClockIcon className="h-3 w-3 text-text-muted" />
      <span className="text-text-muted">运行时长</span>
      <span
        className="font-mono font-semibold tabular-nums text-text-primary"
        data-num
      >
        {formatDuration(duration)}
      </span>
    </div>
  );
}

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

/** rail 收/展 toggle 按钮 */
function RailToggleButton({
  open,
  onClick,
}: {
  open: boolean;
  onClick: () => void;
}) {
  const Icon = open ? PanelRightCloseIcon : PanelRightOpenIcon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={open ? "收起节点详情" : "展开节点详情"}
      title={open ? "收起节点详情" : "展开节点详情"}
      className="flex h-8 w-8 items-center justify-center rounded-md text-text-muted hover:bg-bg-hover hover:text-text-primary"
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}
