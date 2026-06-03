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
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 主内容上方的 5 tab 横条 —— workspace 专用。
 *
 * 视觉：底部 underline，active 用 accent 色，inactive 灰。
 * 切换走 URL ?tab=xxx，下层 TabBody 根据 query 渲染。
 */

const TABS: Array<{ key: string; label: string; icon: LucideIcon }> = [
  { key: "dag", label: "任务流转", icon: WorkflowIcon },
  { key: "report", label: "报告", icon: FileTextIcon },
  { key: "trace", label: "决策回放", icon: ClockIcon },
  { key: "evidence", label: "证据库", icon: LibraryIcon },
  { key: "metrics", label: "指标", icon: GaugeIcon },
];

export function WorkspaceTopTabs() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const currentTab = searchParams.get("tab") ?? "dag";

  // 保留其他 query（如 ?section=xxx 用于章节锚点），仅替换 tab
  const buildHref = useMemo(
    () => (tab: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      sp.set("tab", tab);
      return `${pathname}?${sp.toString()}`;
    },
    [pathname, searchParams]
  );

  return (
    <div className="border-b border-border-subtle bg-bg-overlay/60 px-6 backdrop-blur">
      <div
        role="tablist"
        aria-label="workspace 视图切换"
        className="flex items-center gap-1"
      >
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = t.key === currentTab;
          return (
            <Link
              key={t.key}
              href={buildHref(t.key)}
              role="tab"
              aria-selected={active}
              className={cn(
                "relative inline-flex items-center gap-1.5 px-3.5 py-3 text-sm font-medium transition-colors duration-120 ease-out-quart",
                active
                  ? "text-accent-base"
                  : "text-text-muted hover:text-text-secondary"
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{t.label}</span>
              {active ? (
                <span
                  aria-hidden
                  className="absolute inset-x-2.5 -bottom-px h-[2px] rounded-pill bg-accent-base"
                />
              ) : null}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
