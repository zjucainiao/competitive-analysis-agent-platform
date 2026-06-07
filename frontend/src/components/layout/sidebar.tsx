"use client";

import { useMemo } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  HomeIcon,
  FolderIcon,
  SparklesIcon,
  GaugeIcon,
  BookOpenIcon,
  GridIcon,
  CircleDollarSignIcon,
  StarIcon,
  TargetIcon,
  TrendingUpIcon,
  CompassIcon,
  FileSearchIcon,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 左侧 sidebar 导航 —— Similarweb 风（始终统一结构）。
 *
 * 结构（不再随路径切换两套）：
 *  - 首页（始终）
 *  - 项目分组：我的项目 / 新建分析
 *  - 竞品分析分组：概况 / 功能 / 定价 / 评价 / SWOT / 差异化 / 定位 / 数据来源
 *  - 平台分组：全局指标
 *
 * 「竞品分析」分组里的章节项一直显示（即使没进 workspace）。
 * 点击时：
 *  - 当前在某个 workspace → 路由到该 workspace 的 ?tab=report&section=<id>
 *  - 当前不在 workspace → 路由到 demo workspace 的 ?tab=report&section=<id>，
 *    让用户至少能看到示例内容；真实用户有项目时通常已经在 workspace 里
 */

interface NavItem {
  href: string;
  icon: LucideIcon;
  label: string;
  matchPath?: (pathname: string, searchParams: URLSearchParams) => boolean;
}

/**
 * 章节 slug ⇆ 报告 sec_id 双向映射。
 * slug 用在 `/analysis/<slug>` 路由（未选项目时的空态页）。
 * id 用在 `?tab=report&section=<id>` 锚点 / 过滤参数。
 */
export const ANALYSIS_SECTIONS: Array<{
  id: string;
  slug: string;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "sec_overview", slug: "overview", label: "概况", icon: BookOpenIcon },
  { id: "sec_features", slug: "features", label: "功能", icon: GridIcon },
  { id: "sec_pricing", slug: "pricing", label: "定价", icon: CircleDollarSignIcon },
  { id: "sec_userfeedback", slug: "reviews", label: "评价", icon: StarIcon },
  { id: "sec_swot", slug: "swot", label: "SWOT", icon: TargetIcon },
  { id: "sec_differentiation", slug: "differentiation", label: "差异化", icon: TrendingUpIcon },
  { id: "sec_positioning", slug: "positioning", label: "定位", icon: CompassIcon },
  { id: "sec_sources", slug: "sources", label: "数据来源", icon: FileSearchIcon },
];

export function Sidebar(_props: { projectName?: string } = {}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const inWorkspace = useMemo(
    () => /^\/projects\/[^/]+\/runs\//.test(pathname),
    [pathname]
  );
  const currentTab = searchParams.get("tab") ?? "dag";
  const currentSection = searchParams.get("section");

  /** 章节项 href：workspace 里就在当前 url 上换 tab=report&section=...；
   *  非 workspace 则跳到 `/analysis/<slug>` 的空态搜索页 */
  const sectionHref = (sec: { id: string; slug: string }) => {
    if (inWorkspace) {
      const sp = new URLSearchParams(searchParams.toString());
      sp.set("tab", "report");
      sp.set("section", sec.id);
      return `${pathname}?${sp.toString()}`;
    }
    return `/analysis/${sec.slug}`;
  };

  return (
    <aside
      className="fixed inset-y-0 left-0 z-30 flex w-[240px] flex-col border-r border-sidebar-border bg-sidebar"
      aria-label="主导航"
    >
      {/* brand */}
      <div className="flex items-center gap-3 px-4 py-5">
        <div
          className="flex h-9 w-9 items-center justify-center rounded-xl text-base font-bold text-text-inverse shadow-card"
          style={{
            background:
              "linear-gradient(135deg, var(--accent-base) 0%, oklch(62% 0.22 305) 50%, oklch(68% 0.18 30) 100%)",
          }}
        >
          A
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-semibold tracking-tight text-text-primary">
            Atlas
          </div>
          <div className="text-[11px] text-text-muted">竞品分析</div>
        </div>
      </div>

      {/* nav body */}
      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        {/* 首页 —— 永远在 */}
        <NavRow
          item={{
            href: "/",
            icon: HomeIcon,
            label: "首页",
            matchPath: (p) => p === "/",
          }}
          activePath={pathname}
          searchParams={searchParams}
        />

        {/* 项目 */}
        <SectionHeader>项目</SectionHeader>
        <NavRow
          item={{
            href: "/projects",
            icon: FolderIcon,
            label: "我的项目",
            matchPath: (p) =>
              p === "/projects" ||
              (p.startsWith("/projects/") && !inWorkspace && p !== "/projects/new"),
          }}
          activePath={pathname}
          searchParams={searchParams}
        />
        <NavRow
          item={{
            href: "/projects/new",
            icon: SparklesIcon,
            label: "新建分析",
            matchPath: (p) => p === "/projects/new",
          }}
          activePath={pathname}
          searchParams={searchParams}
        />

        {/* 竞品分析 —— 单独成块，加底色 + 标题与子项同字号 */}
        <div className="mt-6 rounded-xl border border-sidebar-border bg-[oklch(97%_0.03_280)] p-2 dark:bg-[oklch(24%_0.04_285)]">
          <div className="px-3 pt-1.5 pb-1 text-sm font-semibold text-text-primary">
            竞品分析
          </div>
          {ANALYSIS_SECTIONS.map((s) => {
            const Icon = s.icon;
            const onAnalysisRoute = pathname === `/analysis/${s.slug}`;
            const inWorkspaceActive =
              inWorkspace && currentTab === "report" && currentSection === s.id;
            const isActive = onAnalysisRoute || inWorkspaceActive;
            return (
              <Link
                key={s.id}
                href={sectionHref(s)}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "group relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors duration-120 ease-out-quart",
                  isActive
                    ? "bg-bg-raised text-text-accent font-medium shadow-card"
                    : "text-text-secondary hover:bg-bg-raised/60 hover:text-text-primary"
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate">{s.label}</span>
              </Link>
            );
          })}
        </div>

        {/* 平台 */}
        <SectionHeader>平台</SectionHeader>
        <NavRow
          item={{
            href: "/metrics",
            icon: GaugeIcon,
            label: "全局指标",
            matchPath: (p) => p === "/metrics",
          }}
          activePath={pathname}
          searchParams={searchParams}
        />
      </nav>
    </aside>
  );
}

/* ── shared ──────────────────────────────────────────────────────────── */

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-6 mb-1.5 px-3 text-[11px] font-medium tracking-wide text-text-muted">
      {children}
    </div>
  );
}

function NavRow({
  item,
  activePath,
  searchParams,
}: {
  item: NavItem;
  activePath: string;
  searchParams: URLSearchParams;
}) {
  const Icon = item.icon;
  const active = item.matchPath
    ? item.matchPath(activePath, searchParams)
    : false;

  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors duration-120 ease-out-quart",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
          : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}
