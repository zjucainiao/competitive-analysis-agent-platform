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
    : "#";

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
                  live demo
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
              template{" "}
              <code className="font-mono text-text-secondary">
                {project.templateId}
              </code>
            </span>
            <span className="text-border-default">·</span>
            <span>
              {project.runCount} run{project.runCount === 1 ? "" : "s"}
            </span>
            <span className="text-border-default">·</span>
            <span>updated {project.lastUpdatedAt}</span>
          </div>

          {/* compact metrics */}
          {project.metrics.spans > 0 ? (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
              <Metric
                label="accuracy"
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
                label="coverage"
                value={project.metrics.coverage?.toFixed(2) ?? "—"}
              />
              <Metric
                label="edit_rate"
                value={project.metrics.editRate?.toFixed(2) ?? "—"}
              />
              <Metric
                label="cost"
                value={`$${project.metrics.costUsd.toFixed(2)}`}
              />
              <Metric label="spans" value={String(project.metrics.spans)} />
            </div>
          ) : null}
        </div>

        {/* right-side actions */}
        <div className="flex shrink-0 items-center gap-1">
          {/* hover actions */}
          <div className="flex items-center gap-1 opacity-0 transition-opacity duration-120 ease-out-quart group-hover:opacity-100">
            <ActionIconButton
              icon={RotateCcwIcon}
              label="Rerun"
              onClick={async () => {
                if (project.isLive) {
                  const ok = await callOrToast(
                    "Rerun",
                    () => restartRun(project.id),
                    `${project.name} · 已派发新一轮 run`
                  );
                  if (ok) await revalidate.projects();
                } else {
                  toast.info(`${project.name} · 已派发新一轮 run（mock）`);
                }
                emitIntervention("rerun", project.id);
              }}
            />
            <ActionIconButton
              icon={CopyIcon}
              label="Duplicate"
              onClick={() => {
                toast.info(`${project.name} · 已复制为草稿`, {
                  description: "v1 仅 toast · 后端 duplicate endpoint 待补",
                });
                emitIntervention("duplicate", project.id);
              }}
            />
            <ActionIconButton
              icon={ArchiveIcon}
              label={project.archived ? "Unarchive" : "Archive"}
              onClick={async () => {
                onToggleArchive(project.id);
                if (project.isLive) {
                  const action = project.archived ? "Unarchive" : "Archive";
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
              label="Delete"
              onClick={async () => {
                if (project.isLive) {
                  const ok = await callOrToast(
                    "Delete",
                    () => deleteProject(project.id),
                    `${project.name} · 已移至回收站 · 30 天内可恢复`
                  );
                  if (ok) await revalidate.projects();
                } else {
                  toast.warning(`${project.name} · 已移至回收站`, {
                    description: "30 天内可恢复（mock）",
                  });
                }
                emitIntervention("delete", project.id);
              }}
              tone="destructive"
            />
          </div>
          {project.isLive ? (
            <Button render={<Link href={href} />} size="sm">
              <span>Open</span>
              <ArrowRightIcon className="h-3 w-3" />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                toast.info(`${project.name}`, {
                  description: "此项目还在草稿状态 · 创建 run 后会有可进入的 workspace",
                })
              }
              className="gap-1.5"
            >
              <MoreHorizontalIcon className="h-3.5 w-3.5" />
              <span>Details</span>
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
