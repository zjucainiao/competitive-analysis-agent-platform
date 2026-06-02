import Link from "next/link";
import { ArrowLeftIcon } from "lucide-react";
import { StatusPill, type StatusTone } from "./status-pill";
import { WorkspaceActions } from "./workspace-actions";
import { RunHistoryBadge } from "./run-history-badge";
import type { RunStatus } from "@/lib/workspace-actions";
import type { RunRef } from "@/lib/api/types";

export interface RunContext {
  projectId: string;
  projectName: string;
  runId: string;
  runNumber: number;
  status: {
    tone: StatusTone;
    label: string;
    pulse?: boolean;
  };
  target: string;
  competitors: string[];
  templateId: string;
  industry: string;
  /** API 模式下的全部 run 历史（最新在末尾）；mock 模式下空 */
  runs?: RunRef[];
}

/**
 * 上下文条 ~84px (2 行 + 右侧动作组)。固定在顶导航下方。
 *  - 第一行：返回项目列表 / 项目名 / run # / 状态 pill / 右侧 WorkspaceActions
 *  - 第二行：target · competitors · template · industry 元数据
 */
export function ContextBar({ ctx }: { ctx: RunContext }) {
  const runStatus = toneToRunStatus(ctx.status.tone);
  return (
    <div className="border-b border-border-subtle bg-background">
      <div className="mx-auto flex h-[84px] max-w-[1600px] flex-col justify-center gap-1.5 px-10">
        <div className="flex items-center gap-3 text-sm">
          <Link
            href="/projects"
            className="inline-flex items-center gap-1 text-text-muted transition-colors duration-120 ease-out-quart hover:text-text-secondary"
          >
            <ArrowLeftIcon className="h-3.5 w-3.5" />
            <span>Projects</span>
          </Link>
          <Slash />
          <span className="font-medium text-text-primary">
            {ctx.projectName}
          </span>
          <Slash />
          <span
            className="font-mono text-text-secondary tabular-nums"
            data-num
          >
            run #{String(ctx.runNumber).padStart(2, "0")}
          </span>
          {ctx.runs && ctx.runs.length > 0 ? (
            <RunHistoryBadge runs={ctx.runs} activeRunId={ctx.runId} />
          ) : null}
          <StatusPill
            tone={ctx.status.tone}
            label={ctx.status.label}
            pulse={ctx.status.pulse}
            className="ml-1"
          />
          <div className="ml-auto">
            <WorkspaceActions status={runStatus} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
          <MetaItem label="target" value={ctx.target} highlight />
          <MetaItem label="vs" value={ctx.competitors.join(" · ")} />
          <MetaItem label="template" value={ctx.templateId} mono />
          <MetaItem label="industry" value={ctx.industry} mono />
        </div>
      </div>
    </div>
  );
}

function toneToRunStatus(tone: StatusTone): RunStatus {
  switch (tone) {
    case "running":
    case "warning":
      return "running";
    case "rework":
      return "rework";
    case "success":
      return "success";
    case "error":
      return "failed";
    case "neutral":
    default:
      return "pending";
  }
}

function Slash() {
  return (
    <span className="text-text-muted" aria-hidden>
      /
    </span>
  );
}

function MetaItem({
  label,
  value,
  mono,
  highlight,
}: {
  label: string;
  value: string;
  mono?: boolean;
  highlight?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="uppercase tracking-wider text-[10px]">{label}</span>
      <span
        className={[
          mono ? "font-mono" : "",
          highlight ? "text-text-accent font-medium" : "text-text-secondary",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {value}
      </span>
    </span>
  );
}
