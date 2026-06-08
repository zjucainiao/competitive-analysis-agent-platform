"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  SearchIcon,
  ArrowRightIcon,
  TrendingUpIcon,
  SparklesIcon,
  FolderIcon,
  ClockIcon,
} from "lucide-react";
import { SidebarShell } from "@/components/layout/sidebar-shell";
import { OverviewRail } from "@/components/layout/overview-rail";
import { Button } from "@/components/ui/button";
import { useProjects } from "@/lib/api/hooks";
import { apiProjectToCard } from "@/components/projects-list/adapters";
import { StatusPill } from "@/components/layout/status-pill";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

/**
 * 首页 · search-first hub（三栏 SimilarWeb 风）
 *
 *  ┌──────┬──────────┬───────────────────────┐
 *  │ Nav  │ 概况左栏 │ Hero + 最近项目 + CTA │
 *  │ 240  │   280    │       自适应          │
 *  └──────┴──────────┴───────────────────────┘
 *
 * 概况左栏（OverviewRail）固定 280px，仅 ≥ xl 视口显示；< xl 窄屏自动隐藏，
 * 主区退回到 240 nav 边。
 *
 * 主区相比旧版收紧：
 *  - hero 不再用整页 wash，改成块状 search card（与左栏视觉对齐）
 *  - 最近项目从 4 列宽卡 → 紧凑表格行（密度 ×2）
 *  - CTA section 收为单卡，不占满整宽
 */
export default function HomePage() {
  return (
    <SidebarShell
      overviewRail={<OverviewRail />}
      topBarLeft={
        <div className="text-xs">
          <span className="font-medium text-text-secondary">首页</span>
        </div>
      }
    >
      <div className="mx-auto max-w-5xl space-y-6">
        <HeroSearchCard />
        <RecentProjectsSection />
        <CtaSection />
      </div>
    </SidebarShell>
  );
}

/* ── hero search card ────────────────────────────────────────────────── */

function HeroSearchCard() {
  const router = useRouter();
  const { user } = useAuth();
  const { data: projectsData } = useProjects();
  const [query, setQuery] = useState("");

  const greeting = useMemo(() => {
    const h = new Date().getHours();
    if (h < 5) return "夜深了";
    if (h < 12) return "早上好";
    if (h < 14) return "中午好";
    if (h < 18) return "下午好";
    return "晚上好";
  }, []);

  // 当前登录用户名（昵称 → 邮箱前缀 → 兜底）
  const name = user?.display_name?.trim() || user?.email?.split("@")[0] || "你好";

  function handleSubmit(e: React.SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    const q = query.trim();
    if (!q) {
      router.push("/projects/new");
      return;
    }
    // 若已分析过同名产品 → 直接打开它的报告，不重复新建
    const match = (projectsData?.projects ?? [])
      .filter((p) => p.status !== "archived" && p.status !== "deleted")
      .find((p) => p.target_product.trim().toLowerCase() === q.toLowerCase());
    if (match) {
      router.push(
        `/projects/${match.project_id}/runs/${match.project_id}?tab=report`
      );
      return;
    }
    router.push(`/projects/new?target=${encodeURIComponent(q)}`);
  }

  return (
    <section className="relative px-2 py-6">
      <div className="relative z-10">
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary md:text-3xl">
          {name}，{greeting}
        </h1>
        <p className="mt-2 text-sm text-text-secondary leading-relaxed">
          挑一个想了解的产品，几分钟就能拿到一份带原文引用的对比报告。
        </p>

        <form onSubmit={handleSubmit} className="mt-6 max-w-2xl">
          <div className="group flex items-center gap-3 rounded-pill border border-border-default bg-bg-raised px-5 py-3 shadow-card transition-all focus-within:border-accent-base focus-within:shadow-popover">
            <SearchIcon className="h-4 w-4 shrink-0 text-text-muted" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="输入目标产品：Notion、Shopify、Salesforce……"
              className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
              autoFocus
            />
            <Button type="submit" size="sm" className="rounded-pill px-5">
              开始分析
            </Button>
          </div>
        </form>

        <p className="mt-3 text-xs text-text-muted">
          想不到分析什么？
          <Link
            href="/projects/new"
            className="ml-1 font-medium text-accent-base hover:text-accent-hover hover:underline"
          >
            看看常见的对比组合 →
          </Link>
        </p>
      </div>
    </section>
  );
}

/* ── recent projects (compact table-row style) ───────────────────────── */

function RecentProjectsSection() {
  const { data, error, isLoading } = useProjects();

  const recent = useMemo(() => {
    const list = (data?.projects ?? [])
      .filter((p) => p.status !== "archived" && p.status !== "deleted")
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )
      .slice(0, 6)
      .map(apiProjectToCard);
    return list;
  }, [data]);

  return (
    <section className="card-soft overflow-hidden">
      <header className="flex items-center justify-between border-b border-border-subtle px-5 py-3">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
          <ClockIcon className="h-3 w-3" />
          <span>最近项目</span>
          {recent.length > 0 ? (
            <span
              className="ml-1 font-mono text-text-secondary tabular-nums"
              data-num
            >
              · {recent.length}
            </span>
          ) : null}
        </div>
        <Link
          href="/projects"
          className="text-[11px] text-accent-base hover:text-accent-hover hover:underline"
        >
          查看全部 →
        </Link>
      </header>

      {isLoading ? (
        <SkeletonRows />
      ) : error ? (
        <EmptyHint
          title="无法连接后端"
          desc="检查 uvicorn 是否在 :8000 运行；首页只是入口，无后端也能浏览"
        />
      ) : recent.length === 0 ? (
        <EmptyHint
          title="还没有项目"
          desc="在上方搜索框输入目标产品名开始第一份分析"
        />
      ) : (
        <ul className="divide-y divide-border-subtle">
          {recent.map((p) => (
            <RecentRow key={p.id} project={p} />
          ))}
        </ul>
      )}
    </section>
  );
}

function RecentRow({
  project,
}: {
  project: ReturnType<typeof apiProjectToCard>;
}) {
  const href = project.isLive
    ? `/projects/${project.id}/runs/${project.lastRunId}`
    : `/projects/${project.id}`;
  return (
    <li>
      <Link
        href={href}
        className="group flex items-center gap-3 px-5 py-2.5 transition-colors hover:bg-bg-hover"
      >
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold text-text-inverse"
          style={{
            background:
              "linear-gradient(135deg, var(--accent-base), oklch(64% 0.22 305))",
          }}
        >
          {project.target.slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-text-primary">
            {project.name}
          </div>
          <div className="truncate text-[11px] text-text-muted">
            {project.target} vs {project.competitors.slice(0, 2).join(" · ")}
            {project.competitors.length > 2 ? "…" : ""}
          </div>
        </div>
        <StatusPill
          tone={project.status.tone}
          label={project.status.label}
          pulse={project.status.pulse}
        />
        <ArrowRightIcon className="h-3.5 w-3.5 shrink-0 text-text-muted opacity-0 transition-opacity group-hover:opacity-100" />
      </Link>
    </li>
  );
}

function SkeletonRows() {
  return (
    <ul className="divide-y divide-border-subtle">
      {Array.from({ length: 4 }).map((_, i) => (
        <li
          key={i}
          className="flex items-center gap-3 px-5 py-3"
          style={{ animationDelay: `${i * 80}ms` }}
        >
          <div className="h-7 w-7 shrink-0 animate-pulse rounded-md bg-bg-sunken" />
          <div className="flex-1 space-y-1.5">
            <div className="h-3 w-1/2 animate-pulse rounded bg-bg-sunken" />
            <div className="h-2.5 w-1/3 animate-pulse rounded bg-bg-sunken" />
          </div>
        </li>
      ))}
    </ul>
  );
}

function EmptyHint({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="px-6 py-8 text-center">
      <div className="text-sm font-medium text-text-primary">{title}</div>
      <p className="mt-1 text-xs text-text-muted">{desc}</p>
    </div>
  );
}

/* ── CTA section ─────────────────────────────────────────────────────── */

function CtaSection() {
  return (
    <section
      className={cn(
        "card-soft relative overflow-hidden p-6",
        "bg-gradient-to-br from-[oklch(96%_0.04_280)] to-[oklch(96%_0.05_295)]"
      )}
    >
      <div className="relative z-10 grid grid-cols-1 gap-4 md:grid-cols-[1fr_auto] md:items-center">
        <div>
          <h3 className="text-base font-semibold text-text-primary">
            一份能直接交付的报告
          </h3>
          <p className="mt-1 text-xs text-text-secondary leading-relaxed">
            每段话都标注出处，点开就能跳到原文；不合理的地方系统会主动重写，不用你逐字校对。
          </p>
          <FeatureList />
        </div>
        <div className="flex flex-col items-start gap-2 md:items-end">
          <Button
            size="default"
            className="rounded-pill px-4 shadow-card gap-1.5"
            render={<Link href="/projects/new" />}
          >
            <SparklesIcon className="h-3.5 w-3.5" />
            <span>新建分析</span>
            <ArrowRightIcon className="h-3 w-3" />
          </Button>
          <Link
            href="/projects/demo/runs/01?tab=dag"
            className="inline-flex items-center gap-1 text-[11px] text-text-secondary hover:text-text-accent"
          >
            <span>或先看演示项目</span>
            <ArrowRightIcon className="h-3 w-3" />
          </Link>
        </div>
      </div>

      <div
        aria-hidden
        className="pointer-events-none absolute -right-20 -bottom-20 h-52 w-52 rounded-full opacity-40 blur-3xl"
        style={{
          background:
            "radial-gradient(circle, oklch(70% 0.20 285) 0%, transparent 70%)",
        }}
      />
    </section>
  );
}

function FeatureList() {
  const features = [
    { icon: FolderIcon, text: "按行业适配的分析维度" },
    { icon: TrendingUpIcon, text: "整个过程实时可见" },
    { icon: SparklesIcon, text: "随时可以人工修正" },
  ];
  return (
    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-text-secondary">
      {features.map((f, i) => {
        const Icon = f.icon;
        return (
          <li key={i} className="inline-flex items-center gap-1.5">
            <Icon className="h-3 w-3 text-accent-base" />
            <span>{f.text}</span>
          </li>
        );
      })}
    </ul>
  );
}
