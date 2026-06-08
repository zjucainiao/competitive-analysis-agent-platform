"use client";

import Link from "next/link";
import {
  ArrowRightIcon,
  RotateCcwIcon,
  ArchiveIcon,
  CopyIcon,
  Trash2Icon,
  MoreHorizontalIcon,
} from "lucide-react";
import { toast } from "sonner";
import { StatusPill } from "@/components/layout/status-pill";
import { Button } from "@/components/ui/button";
import { emitIntervention } from "@/lib/workspace-actions";
import {
  ApiError,
  archiveProject,
  deleteProject,
  restartRun,
  restoreProject,
} from "@/lib/api/client";
import { revalidate } from "@/lib/api/hooks";
import type { MockProject } from "@/lib/projects-mock";
import { cn } from "@/lib/utils";

/* 报告模板 id → 中文标签（隐藏内部 *_v1 命名） */
const TEMPLATE_LABELS: Record<string, string> = {
  standard_v1: "标准对比模板",
  single_research_v1: "单产品调研模板",
};
const templateLabel = (id: string): string => TEMPLATE_LABELS[id] ?? id;

async function callOrToast(
  label: string,
  op: () => Promise<unknown>,
  successMsg: string
): Promise<boolean> {
  try {
    await op();
    toast.success(successMsg);
    return true;
  } catch (e) {
    const msg = e instanceof ApiError ? `${e.status} · ${e.message}` : String(e);
    toast.error(`${label} 失败`, { description: msg });
    return false;
  }
}

export function ProjectCard({
  project,
  onToggleArchive,
}: {
  project: MockProject;
  onToggleArchive: (id: string) => void;
}) {
  const href = project.isLive
    ? `/projects/${project.id}/runs/${project.lastRunId}`
    : `/projects/${project.id}`;

  return (
    <article
      className={cn(
        "group relative rounded-lg border bg-bg-raised p-5 transition-all duration-120 ease-out-quart",
        project.isLive
          ? "border-border-subtle hover:border-accent-border hover:shadow-popover"
          : "border-border-subtle hover:border-border-default",
        project.archived && "opacity-70"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-2">
          {/* title row */}
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={href}
              className="font-medium text-text-primary hover:text-text-accent"
            >
              {project.name}
              {project.isLive ? (
                <span className="ml-2 inline-flex items-center rounded-pill border border-accent-border bg-accent-bg px-1.5 py-0.5 align-middle text-[10px] font-medium text-accent-base">
                  演示
                </span>
              ) : null}
            </Link>
            <StatusPill
              tone={project.status.tone}
              label={project.status.label}
              pulse={project.status.pulse}
            />
          </div>

          {/* competitors */}
          <p className="text-sm text-text-secondary">
            <span className="font-medium text-text-accent">
              {project.target}
            </span>{" "}
            <span className="text-text-muted">vs</span>{" "}
            {project.competitors.join(" · ")}
          </p>

          {/* meta row */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-muted">
            <span>{project.industryLabel}</span>
            <span className="text-border-default">·</span>
            <span>
              报告模板{" "}
              <span className="text-text-secondary">
                {templateLabel(project.templateId)}
              </span>
            </span>
            <span className="text-border-default">·</span>
            <span>{project.runCount} 次运行</span>
            <span className="text-border-default">·</span>
            <span>更新于 {project.lastUpdatedAt}</span>
          </div>

          {/* compact metrics */}
          {project.metrics.spans > 0 ? (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
              <Metric
                label="准确率"
                value={project.metrics.accuracy?.toFixed(2) ?? "—"}
                tone={
                  project.metrics.accuracy == null
                    ? "muted"
                    : project.metrics.accuracy >= 0.9
                      ? "good"
                      : "warn"
                }
              />
              <Metric
                label="覆盖率"
                value={project.metrics.coverage?.toFixed(2) ?? "—"}
              />
              <Metric
                label="修订率"
                value={project.metrics.editRate?.toFixed(2) ?? "—"}
              />
              <Metric
                label="成本"
                value={`$${project.metrics.costUsd.toFixed(2)}`}
              />
              <Metric label="段落数" value={String(project.metrics.spans)} />
            </div>
          ) : null}
        </div>

        {/* right-side actions */}
        <div className="flex shrink-0 items-center gap-1">
          {/* hover actions */}
          <div className="flex items-center gap-1 opacity-0 transition-opacity duration-120 ease-out-quart group-hover:opacity-100">
            <ActionIconButton
              icon={RotateCcwIcon}
              label="重新运行"
              onClick={async () => {
                if (project.isLive) {
                  const ok = await callOrToast(
                    "重新运行",
                    () => restartRun(project.id),
                    `${project.name} · 已派发新一轮分析`
                  );
                  if (ok) await revalidate.projects();
                } else {
                  toast.info(`${project.name} · 已派发新一轮分析（演示）`);
                }
                emitIntervention("rerun", project.id);
              }}
            />
            <ActionIconButton
              icon={CopyIcon}
              label="复制"
              onClick={() => {
                toast.info(`${project.name} · 已复制为草稿`, {
                  description: "该功能暂未开放",
                });
                emitIntervention("duplicate", project.id);
              }}
            />
            <ActionIconButton
              icon={ArchiveIcon}
              label={project.archived ? "取消归档" : "归档"}
              onClick={async () => {
                onToggleArchive(project.id);
                if (project.isLive) {
                  const action = project.archived ? "取消归档" : "归档";
                  const ok = await callOrToast(
                    action,
                    () =>
                      project.archived
                        ? restoreProject(project.id)
                        : archiveProject(project.id),
                    `${project.name} · ${project.archived ? "已恢复" : "已归档"}`
                  );
                  if (ok) await revalidate.projects();
                }
                emitIntervention(
                  project.archived ? "unarchive" : "archive",
                  project.id
                );
              }}
            />
            <ActionIconButton
              icon={Trash2Icon}
              label="删除"
              onClick={async () => {
                if (project.isLive) {
                  const ok = await callOrToast(
                    "删除",
                    () => deleteProject(project.id),
                    `${project.name} · 已移至回收站 · 30 天内可恢复`
                  );
                  if (ok) await revalidate.projects();
                } else {
                  toast.warning(`${project.name} · 已移至回收站`, {
                    description: "30 天内可恢复（演示）",
                  });
                }
                emitIntervention("delete", project.id);
              }}
              tone="destructive"
            />
          </div>
          {project.isLive ? (
            <Button render={<Link href={href} />} size="sm">
              <span>进入</span>
              <ArrowRightIcon className="h-3 w-3" />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                toast.info(`${project.name}`, {
                  description: "此项目还在草稿状态 · 启动分析后即可进入工作台",
                })
              }
              className="gap-1.5"
            >
              <MoreHorizontalIcon className="h-3.5 w-3.5" />
              <span>详情</span>
            </Button>
          )}
        </div>
      </div>
    </article>
  );
}

function Metric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "muted" | "good" | "warn";
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-[10px] uppercase tracking-wider text-text-muted">
        {label}
      </span>
      <span
        className={cn(
          "font-mono font-medium tabular-nums",
          tone === "default" && "text-text-primary",
          tone === "muted" && "text-text-muted italic",
          tone === "good" && "text-success-base",
          tone === "warn" && "text-warning-base"
        )}
        data-num
      >
        {value}
      </span>
    </span>
  );
}

function ActionIconButton({
  icon: Icon,
  label,
  onClick,
  tone = "default",
}: {
  icon: typeof RotateCcwIcon;
  label: string;
  onClick: () => void;
  tone?: "default" | "destructive";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded-md border border-transparent bg-transparent transition-colors duration-120 ease-out-quart",
        tone === "destructive"
          ? "text-text-muted hover:bg-error-bg hover:text-error-base"
          : "text-text-muted hover:bg-bg-hover hover:text-text-primary"
      )}
    >
      <Icon className="h-3.5 w-3.5" />
    </button>
  );
}
