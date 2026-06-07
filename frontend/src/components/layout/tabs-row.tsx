"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";

export type TabKey = "dag" | "report" | "trace" | "evidence" | "metrics";

export const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "dag", label: "工作流" },
  { key: "report", label: "报告" },
  { key: "trace", label: "决策回放" },
  { key: "evidence", label: "证据库" },
  { key: "metrics", label: "指标" },
];

export const DEFAULT_TAB: TabKey = "dag";

/**
 * Workspace 5 tab 导航。
 *
 * 设计选择：tab 状态走 URL `?tab=` search param，理由：
 *  - 可分享、可截图、决策回放页可直接深链
 *  - 服务器组件可读 searchParams 决定渲染哪个 tab
 *  - 不需要 client state，刷新不丢失上下文
 */
export function TabsRow() {
  const pathname = usePathname();
  const search = useSearchParams();
  const current = (search.get("tab") as TabKey | null) ?? DEFAULT_TAB;

  return (
    <nav
      aria-label="workspace tabs"
      className="border-b border-border-subtle bg-background"
    >
      <div className="mx-auto flex h-11 max-w-[1600px] items-stretch gap-1 px-10">
        {TABS.map((t) => {
          const active = t.key === current;
          return (
            <Link
              key={t.key}
              href={{ pathname, query: { tab: t.key } }}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative inline-flex items-center px-3 text-sm transition-colors duration-120 ease-out-quart",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                active
                  ? "text-text-primary font-medium"
                  : "text-text-muted hover:text-text-secondary"
              )}
            >
              {t.label}
              {active ? (
                <span className="absolute inset-x-3 -bottom-px h-0.5 rounded-pill bg-accent-base" />
              ) : null}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
