"use client";

import { useMemo } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  WorkflowIcon,
  FileTextIcon,
  ClockIcon,
  LibraryIcon,
  GaugeIcon,
  ArrowLeftIcon,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Workspace 左侧栏 —— 80px 窄列，icon + label 常驻。
 *
 * fixed 在 TopBar（h-16）下方：top-16 left-0 bottom-0。
 * 品牌不在这里（已移到全宽 TopBar 左侧）。
 * 顶部「返回」+ 各视图 tab。
 */

const ITEMS: Array<{ tab: string; label: string; icon: LucideIcon }> = [
  { tab: "dag", label: "工作流", icon: WorkflowIcon },
  { tab: "report", label: "报告", icon: FileTextIcon },
  { tab: "trace", label: "决策回放", icon: ClockIcon },
  { tab: "evidence", label: "证据库", icon: LibraryIcon },
  { tab: "metrics", label: "指标", icon: GaugeIcon },
];

export function WorkspaceSidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentTab = searchParams.get("tab") ?? "dag";

  const buildHref = useMemo(
    () => (tab: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      sp.set("tab", tab);
      return `${pathname}?${sp.toString()}`;
    },
    [pathname, searchParams]
  );

  return (
    <aside
      style={{ width: 80 }}
      className="fixed bottom-0 left-0 top-16 z-20 flex flex-col items-stretch border-r border-sidebar-border bg-sidebar py-3"
      aria-label="workspace 导航"
    >
      {/* 返回项目列表 */}
      <Link
        href="/projects"
        aria-label="返回 我的项目"
        className="mb-1 flex flex-col items-center gap-0.5 rounded-lg py-2 text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary"
      >
        <ArrowLeftIcon className="h-4 w-4" />
        <span className="text-[10px]">返回</span>
      </Link>

      <div className="mx-auto my-2 h-px w-10 bg-sidebar-border" aria-hidden />

      {/* tab icons */}
      <nav className="flex flex-1 flex-col gap-1">
        {ITEMS.map((t) => {
          const Icon = t.icon;
          const active = currentTab === t.tab;
          return (
            <Link
              key={t.tab}
              href={buildHref(t.tab)}
              aria-current={active ? "page" : undefined}
              aria-label={t.label}
              className={cn(
                "relative mx-2 flex flex-col items-center gap-0.5 rounded-lg py-2.5 transition-all duration-120 ease-out-quart",
                active
                  ? "bg-sidebar-accent text-accent-base shadow-card"
                  : "text-text-muted hover:bg-bg-hover hover:text-text-primary"
              )}
            >
              <Icon className="h-5 w-5" />
              <span className={cn("text-[11px]", active && "font-medium")}>
                {t.label}
              </span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
