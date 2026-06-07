"use client";

import { useState } from "react";
import { WorkspaceSidebar } from "./workspace-sidebar";
import { WorkspaceTopBar } from "./workspace-top-bar";
import { OnboardingHint } from "./onboarding-hint";
import { cn } from "@/lib/utils";
import type { StatusTone } from "./status-pill";
import type { RunRef } from "@/lib/api/types";
import type { RunStatus } from "@/lib/workspace-actions";

/**
 * Workspace 四区布局（响应式）：
 *
 *  ┌───────────────────────────────────────────────────────┐
 *  │ TopBar 全宽 64px                                       │
 *  ├──────┬─────────────────────────────────────┬─────────┤
 *  │ 80   │  Main：DAG / Report …                │ 320     │
 *  │ Side │                                     │ Details │
 *  │ bar  │                                     │ Rail    │
 *  └──────┴─────────────────────────────────────┴─────────┘
 *
 * 响应式行为：
 *  - sidebar 永远显示（80px，窄不占地）
 *  - rail 在 ≥ lg (1024px) 默认展开；< lg 默认收起，用户可点按钮手动切换
 *  - rail 状态持久化到 localStorage，下次访问保留
 *  - main margin 根据 rail 开关自适应（无 rail 时 main 撑满到右边）
 */

const RAIL_OPEN_KEY = "atlas:rail-open";

export function WorkspaceShell({
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
  detailsRail,
  children,
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
  detailsRail?: React.ReactNode;
  children: React.ReactNode;
}) {
  // rail 开关：SSR 安全；惰性初始化时读 localStorage / 视口宽度。
  // 服务端（无 window）返回默认开；客户端首次渲染即取真实值。
  const [railOpen, setRailOpen] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const stored = window.localStorage.getItem(RAIL_OPEN_KEY);
    if (stored !== null) {
      return stored === "true";
    }
    return window.innerWidth >= 1024;
  });

  const toggleRail = () => {
    setRailOpen((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(RAIL_OPEN_KEY, String(next));
      } catch {
        /* localStorage may be disabled, ignore */
      }
      return next;
    });
  };

  const hasRail = !!detailsRail && railOpen;
  const mainStyle: React.CSSProperties = hasRail
    ? { marginLeft: 80, marginRight: 320 }
    : { marginLeft: 80, marginRight: 0 };

  return (
    <div className="min-h-screen">
      <WorkspaceTopBar
        projectName={projectName}
        statusTone={statusTone}
        statusLabel={statusLabel}
        statusPulse={statusPulse}
        progressDone={progressDone}
        progressTotal={progressTotal}
        startedAt={startedAt}
        endedAt={endedAt}
        runStatus={runStatus}
        runs={runs}
        activeRunId={activeRunId}
        railOpen={railOpen}
        onToggleRail={detailsRail ? toggleRail : undefined}
      />
      <WorkspaceSidebar />
      <div style={mainStyle} className="flex min-h-screen flex-col pt-16 transition-[margin] duration-200 ease-out">
        <main className="flex-1 px-6 py-5">{children}</main>
      </div>
      {/* rail 用 div 包一层控制显隐，避免内部 fixed 元素总是渲染 */}
      <div className={cn(hasRail ? "block" : "hidden")}>{detailsRail}</div>
      <OnboardingHint />
    </div>
  );
}

