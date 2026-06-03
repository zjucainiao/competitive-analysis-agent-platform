"use client";

import { useState, use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { SearchIcon, ArrowRightIcon } from "lucide-react";
import { SidebarShell } from "@/components/layout/sidebar-shell";
import { ANALYSIS_SECTIONS } from "@/components/layout/sidebar";
import { Button } from "@/components/ui/button";
import { useProjects } from "@/lib/api/hooks";
import { apiProjectToCard } from "@/components/projects-list/adapters";
import { StatusPill } from "@/components/layout/status-pill";

/**
 * /analysis/[dim] —— 用户未选项目时点击 sidebar 章节的落地页。
 *
 *  - 顶部：维度标题 + 一句话说明
 *  - 中部：大搜索框（"查找想分析的产品..."），按 Enter 直接跳 wizard
 *  - 下方：已有项目列表，点进去即可在该项目下看本维度
 */
export default function AnalysisLandingPage({
  params,
}: {
  params: Promise<{ dim: string }>;
}) {
  const { dim } = use(params);
  const sec = ANALYSIS_SECTIONS.find((s) => s.slug === dim);

  return (
    <SidebarShell
      topBarLeft={
        <div className="text-xs">
          <span className="text-text-muted">竞品分析</span>
          <span className="mx-2 text-text-muted">/</span>
          <span className="font-medium text-text-secondary">
            {sec?.label ?? dim}
          </span>
        </div>
      }
    >
      <div className="mx-auto max-w-3xl">
        <PickerHero dimLabel={sec?.label ?? dim} dimSlug={dim} />
        <ProjectsToPick dimSlug={dim} />
      </div>
    </SidebarShell>
  );
}

/* ── hero search ─────────────────────────────────────────────────────── */

function PickerHero({ dimLabel, dimSlug }: { dimLabel: string; dimSlug: string }) {
  const router = useRouter();
  const [query, setQuery] = useState("");

  function handleSubmit(e: React.SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    const q = query.trim();
    const params = new URLSearchParams();
    if (q) params.set("target", q);
    params.set("dimension", dimSlug);
    router.push(`/projects/new?${params.toString()}`);
  }

  return (
    <section className="px-2 py-6">
      <div className="text-[11px] font-medium tracking-wide text-text-muted">
        {dimLabel}
      </div>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight text-text-primary md:text-3xl">
        想看哪个产品的{dimLabel}？
      </h1>
      <p className="mt-2 text-sm text-text-secondary leading-relaxed">
        先选一个目标产品，几分钟就能拿到对应的对比内容。
      </p>

      <form onSubmit={handleSubmit} className="mt-6 max-w-2xl">
        <div className="flex items-center gap-3 rounded-pill border border-border-default bg-bg-raised px-5 py-3 shadow-card transition-all focus-within:border-accent-base focus-within:shadow-popover">
          <SearchIcon className="h-4 w-4 shrink-0 text-text-muted" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="查找任何竞品：Notion、Shopify、Salesforce……"
            className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
            autoFocus
          />
          <Button type="submit" size="sm" className="rounded-pill px-5">
            开始分析
          </Button>
        </div>
      </form>
    </section>
  );
}

/* ── existing projects to pick ───────────────────────────────────────── */

function ProjectsToPick({ dimSlug }: { dimSlug: string }) {
  const { data, isLoading } = useProjects();

  const projects = (data?.projects ?? [])
    .filter((p) => p.status !== "archived" && p.status !== "deleted")
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )
    .slice(0, 8)
    .map(apiProjectToCard);

  if (isLoading) return null;
  if (projects.length === 0) return null;

  const sectionId = ANALYSIS_SECTIONS.find((s) => s.slug === dimSlug)?.id ?? "";

  return (
    <section className="mt-8 card-soft overflow-hidden">
      <header className="border-b border-border-subtle px-5 py-3 text-[12px] text-text-muted">
        或者从已有项目里选一个
      </header>
      <ul className="divide-y divide-border-subtle">
        {projects.map((p) => {
          const href = p.isLive
            ? `/projects/${p.id}/runs/${p.lastRunId}?tab=report&section=${sectionId}`
            : `/projects/${p.id}`;
          return (
            <li key={p.id}>
              <Link
                href={href}
                className="group flex items-center gap-3 px-5 py-3 transition-colors hover:bg-bg-hover"
              >
                <div
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-xs font-semibold text-text-inverse"
                  style={{
                    background:
                      "linear-gradient(135deg, var(--accent-base), oklch(64% 0.22 305))",
                  }}
                >
                  {p.target.slice(0, 1).toUpperCase()}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-text-primary">
                    {p.name}
                  </div>
                  <div className="truncate text-[11px] text-text-muted">
                    {p.target} vs {p.competitors.slice(0, 2).join(" · ")}
                  </div>
                </div>
                <StatusPill
                  tone={p.status.tone}
                  label={p.status.label}
                  pulse={p.status.pulse}
                />
                <ArrowRightIcon className="h-3.5 w-3.5 shrink-0 text-text-muted opacity-0 transition-opacity group-hover:opacity-100" />
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
