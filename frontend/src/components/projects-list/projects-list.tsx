"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  SearchIcon,
  XIcon,
  SparklesIcon,
  AlertTriangleIcon,
  Loader2Icon,
  PlugZapIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useProjects } from "@/lib/api";
import {
  PROJECT_STATUS_FILTERS,
  type ProjectStatusFilter,
} from "@/lib/projects-mock";
import { ProjectCard } from "./project-card";
import { apiProjectToCard } from "./adapters";

type SortKey = "recent" | "name" | "competitors";

const SORTS: Array<{ id: SortKey; label: string }> = [
  { id: "recent", label: "最近更新" },
  { id: "name", label: "名称（A-Z）" },
  { id: "competitors", label: "竞品最多" },
];

/**
 * /projects · 真实 API 接入。
 *  - GET /api/projects via SWR
 *  - 失败显示 ErrorBanner + retry
 *  - 空状态：教用户「New analysis」
 *  - 卡片：每个 Project 适配成 ProjectCard 旧 props 形状
 */
export function ProjectsList() {
  const { data, error, isLoading, mutate } = useProjects();

  const [statusFilter, setStatusFilter] = useState<ProjectStatusFilter>("all");
  const [industryFilter, setIndustryFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("recent");

  // useMemo 稳定 projects 身份（裸 `?? []` 每次新建数组 → 触发下游 useMemo 每帧重算）
  const projects = useMemo(() => data?.projects ?? [], [data]);

  const industries = useMemo(() => {
    const map = new Map<string, string>();
    projects.forEach((p) => map.set(p.industry, displayIndustry(p.industry)));
    return Array.from(map.entries()).map(([id, label]) => ({ id, label }));
  }, [projects]);

  const filtered = useMemo(() => {
    const matchingStatus = PROJECT_STATUS_FILTERS.find(
      (s) => s.id === statusFilter
    );
    const q = search.trim().toLowerCase();

    return projects
      .filter((p) => {
        if (matchingStatus?.tones.length) {
          const card = apiProjectToCard(p);
          if (!matchingStatus.tones.includes(card.status.tone)) return false;
        }
        if (industryFilter && p.industry !== industryFilter) return false;
        if (q) {
          if (
            !p.project_name.toLowerCase().includes(q) &&
            !p.target_product.toLowerCase().includes(q) &&
            !p.competitors.some((c) => c.toLowerCase().includes(q))
          ) {
            return false;
          }
        }
        return true;
      })
      .sort((a, b) => {
        switch (sort) {
          case "name":
            return a.project_name.localeCompare(b.project_name);
          case "competitors":
            return b.competitors.length - a.competitors.length;
          case "recent":
          default:
            return (
              new Date(b.created_at).getTime() -
              new Date(a.created_at).getTime()
            );
        }
      });
  }, [projects, statusFilter, industryFilter, search, sort]);

  const hasFilter =
    statusFilter !== "all" || industryFilter !== null || search.length > 0;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle pb-4">
        <div>
          <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
            项目
          </div>
          <h1 className="mt-1 text-xl font-semibold text-text-primary">
            竞品分析项目
          </h1>
          <ListSubtitle
            isLoading={isLoading}
            error={error}
            totalCount={projects.length}
            shownCount={filtered.length}
          />
        </div>
        <div className="flex items-center gap-3">
          <Button render={<Link href="/projects/new" />}>
            <SparklesIcon className="h-3.5 w-3.5" />
            <span>新建分析</span>
          </Button>
        </div>
      </header>

      {error ? <ApiErrorBanner error={error} onRetry={() => mutate()} /> : null}

      <div className="space-y-3">
        {/* status filter chips */}
        <div className="flex flex-wrap items-center gap-1.5">
          {PROJECT_STATUS_FILTERS.map((f) => {
            const active = f.id === statusFilter;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => setStatusFilter(f.id)}
                className={cn(
                  "rounded-pill border px-2.5 py-0.5 text-xs font-medium transition-colors duration-120 ease-out-quart",
                  active
                    ? "border-accent-border bg-accent-bg text-accent-base"
                    : "border-border-subtle bg-bg-raised text-text-secondary hover:border-border-default"
                )}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        {/* industry + search + sort */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-text-muted">
              行业
            </span>
            <button
              type="button"
              onClick={() => setIndustryFilter(null)}
              className={cn(
                "rounded-pill border px-2 py-0.5 text-[11px]",
                industryFilter === null
                  ? "border-accent-border bg-accent-bg text-accent-base"
                  : "border-transparent text-text-muted hover:text-text-secondary"
              )}
            >
              不限
            </button>
            {industries.map((i) => (
              <button
                key={i.id}
                type="button"
                onClick={() =>
                  setIndustryFilter(industryFilter === i.id ? null : i.id)
                }
                className={cn(
                  "rounded-pill border px-2 py-0.5 text-[11px]",
                  industryFilter === i.id
                    ? "border-accent-border bg-accent-bg text-accent-base"
                    : "border-transparent text-text-muted hover:text-text-secondary"
                )}
              >
                {i.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <div className="relative">
              <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-text-muted" />
              <Input
                type="search"
                placeholder="搜索项目 / 目标产品 / 竞品"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-[260px] pl-8"
              />
              {search ? (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
                  aria-label="清除"
                >
                  <XIcon className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="h-8 rounded-md border border-border-default bg-bg-raised px-2 text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {SORTS.map((s) => (
                <option key={s.id} value={s.id}>
                  排序 · {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {isLoading && projects.length === 0 ? (
        <LoadingState />
      ) : filtered.length === 0 ? (
        <EmptyState
          hasFilter={hasFilter}
          hasAnyProjects={projects.length > 0}
          clear={() => {
            setStatusFilter("all");
            setIndustryFilter(null);
            setSearch("");
          }}
        />
      ) : (
        <ul className="space-y-3">
          {filtered.map((p) => (
            <li key={p.project_id}>
              <ProjectCard
                project={apiProjectToCard(p)}
                onToggleArchive={() => {
                  /* v1 后端无 archive endpoint，UI 端 toast 已在 card */
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function displayIndustry(id: string): string {
  switch (id) {
    case "collaboration_saas":
      return "协作办公";
    case "crm_saas":
      return "CRM";
    case "cross_border_ecommerce_saas":
      return "跨境电商";
    case "edu_saas":
      return "教育 SaaS";
    default:
      return id;
  }
}

function ListSubtitle({
  isLoading,
  error,
  totalCount,
  shownCount,
}: {
  isLoading: boolean;
  error: unknown;
  totalCount: number;
  shownCount: number;
}) {
  if (isLoading && totalCount === 0) {
    return (
      <p className="mt-1 inline-flex items-center gap-1.5 text-sm text-text-muted">
        <Loader2Icon className="h-3 w-3 animate-spin" />
        <span>正在拉取项目列表…</span>
      </p>
    );
  }
  if (error) {
    return (
      <p className="mt-1 text-sm text-text-secondary">
        无法连接后端，下方显示空列表。
      </p>
    );
  }
  return (
    <p className="mt-1 text-sm text-text-secondary">
      {shownCount} 个项目 · 共{" "}
      <span
        className="font-mono font-medium text-text-primary tabular-nums"
        data-num
      >
        {totalCount}
      </span>
    </p>
  );
}

function ApiErrorBanner({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-3 rounded-md border border-error-border bg-error-bg px-4 py-3">
      <AlertTriangleIcon className="h-4 w-4 mt-0.5 shrink-0 text-error-base" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-error-base">
          后端 API 不可达
        </div>
        <p className="mt-0.5 text-xs text-text-secondary leading-relaxed break-words">
          {msg}
        </p>
        <p className="mt-1 text-[11px] text-text-muted">
          检查：<code className="font-mono">uvicorn backend.api.app:app --reload --port 8000</code>
        </p>
      </div>
      <Button size="sm" variant="outline" onClick={onRetry} className="gap-1.5">
        <PlugZapIcon className="h-3 w-3" />
        重试
      </Button>
    </div>
  );
}

function LoadingState() {
  return (
    <ul className="space-y-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <li
          key={i}
          className="animate-pulse-soft rounded-lg border border-border-subtle bg-bg-raised h-[110px]"
        />
      ))}
    </ul>
  );
}

function EmptyState({
  hasFilter,
  hasAnyProjects,
  clear,
}: {
  hasFilter: boolean;
  hasAnyProjects: boolean;
  clear: () => void;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border-default bg-bg-raised/60 px-8 py-16 text-center">
      <p className="text-sm text-text-secondary">
        {hasFilter
          ? "没有匹配当前筛选条件的项目。"
          : hasAnyProjects
            ? "本筛选下无结果。"
            : "暂无项目。点右上「新建分析」创建第一个。"}
      </p>
      {hasFilter ? (
        <Button size="sm" variant="outline" onClick={clear} className="mt-3">
          清空筛选
        </Button>
      ) : (
        !hasAnyProjects && (
          <Button
            size="sm"
            render={<Link href="/projects/new" />}
            className="mt-3 gap-1.5"
          >
            <SparklesIcon className="h-3 w-3" />
            <span>新建分析</span>
          </Button>
        )
      )}
    </div>
  );
}
