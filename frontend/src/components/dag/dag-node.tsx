"use client";

import { Handle, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";
import {
  nodeActionsFor,
  type ActionDef,
  type RunStatus,
} from "@/lib/workspace-actions";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import type { DagNodeData } from "@/lib/dag-mock";

/**
 * DAG 节点视觉 + 动作。
 *
 * 视觉规范见 DESIGN.md § DAG 节点视觉
 *   156 × 72 · rounded-md (6px) · bg-raised + status border + status dot
 *   running: ● pulse
 *   selected: ring accent + shadow-popover
 *   v>=2: 右上角 v2 角标
 *
 * 交互（修复"viewer 不是 product"的关键）：
 *   - 节点被选中 OR status ∈ {rework, error}：NodeToolbar 右侧展开 action chips
 *   - rework 节点：chip 「Override · accept v1」以朱漆橙 fill 主调
 *   - chip 点击 stopPropagation，防止重开 Sheet
 */

const STATUS_BORDER: Record<string, string> = {
  success: "border-success-border",
  running: "border-running-border",
  rework: "border-rework-border",
  warning: "border-warning-border",
  error: "border-error-border",
  neutral: "border-neutral-border",
};

const STATUS_DOT: Record<string, string> = {
  success: "bg-success-base",
  running: "bg-running-base",
  rework: "bg-rework-base",
  warning: "bg-warning-base",
  error: "bg-error-base",
  neutral: "bg-neutral-base",
};

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(t: DagNodeData["tokens"]): string {
  if (!t) return "—";
  const total = t.input + t.output;
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`;
  return String(total);
}

function statusToRun(s: DagNodeData["status"]): RunStatus {
  switch (s) {
    case "success":
      return "success";
    case "running":
      return "running";
    case "rework":
      return "rework";
    case "error":
      return "failed";
    default:
      return "pending";
  }
}

export function DagNode({
  data,
  selected,
  id,
}: NodeProps & { data: DagNodeData }) {
  const isControl = data.agent === "control";
  /* 选中、rework、failed 时露出 action chips —— 让"需要人工介入"的节点天然显眼 */
  const toolbarVisible =
    !!selected || data.status === "rework" || data.status === "error";

  const api = useWorkspaceApi();
  const actions = nodeActionsFor({
    nodeId: id,
    label: data.label,
    agentName: data.agent,
    status: statusToRun(data.status),
    api,
  });

  return (
    <>
      <NodeToolbar
        isVisible={toolbarVisible}
        position={Position.Right}
        offset={10}
        align="center"
      >
        <div className="flex flex-col items-stretch gap-1.5">
          {actions.map((a) => (
            <ActionChip key={a.id} action={a} />
          ))}
        </div>
      </NodeToolbar>

      <div
        className={cn(
          "relative w-[156px] rounded-md border bg-bg-raised px-3 py-2.5",
          "transition-shadow duration-180 ease-out-quart",
          STATUS_BORDER[data.status] ?? "border-border-default",
          selected
            ? "ring-2 ring-accent-base/40 shadow-popover"
            : "shadow-none hover:shadow-popover",
          isControl && "bg-bg-sunken",
          /* rework / error 微高光：边框更显眼 */
          data.status === "rework" && "ring-2 ring-rework-base/15",
          data.status === "error" && "ring-2 ring-error-base/15"
        )}
      >
        <Handle
          type="target"
          position={Position.Top}
          className="!h-1 !w-1 !min-w-0 !border-0 !bg-border-default"
        />
        <Handle
          type="source"
          position={Position.Bottom}
          className="!h-1 !w-1 !min-w-0 !border-0 !bg-border-default"
        />

        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "h-2 w-2 shrink-0 rounded-pill",
              STATUS_DOT[data.status] ?? "bg-neutral-base",
              data.status === "running" && "animate-pulse-soft"
            )}
            aria-hidden
          />
          <span
            className={cn(
              "truncate text-sm font-medium",
              isControl ? "text-text-muted" : "text-text-primary"
            )}
          >
            {data.label}
          </span>
        </div>

        <div
          className="mt-1.5 truncate font-mono text-[11px] tabular-nums text-text-muted"
          data-num
        >
          {data.status === "neutral" ? (
            <span className="italic">pending</span>
          ) : (
            <>
              {formatDuration(data.durationMs)} · {formatTokens(data.tokens)} tok
            </>
          )}
        </div>

        {data.revision > 1 ? (
          <div className="absolute -top-1.5 -right-1.5 rounded-pill bg-rework-base px-1.5 py-px text-[9px] font-medium leading-tight text-text-inverse">
            v{data.revision}
          </div>
        ) : null}

        {/* rework 节点角标：⚠ 需要人工裁决 */}
        {data.status === "rework" && !data.parentNodeId ? (
          <div className="absolute -top-1.5 -left-1.5 rounded-pill bg-rework-base px-1.5 py-px text-[9px] font-medium leading-tight text-text-inverse">
            ⚠ action
          </div>
        ) : null}
      </div>
    </>
  );
}

/* ── action chip ───────────────────────────────────────────────────────── */

function ActionChip({ action }: { action: ActionDef }) {
  const Icon = action.icon;
  const isPrimary = action.variant === "primary";
  const isDestructive = action.variant === "destructive";

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        action.run();
      }}
      title={action.hint ?? action.label}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium",
        "shadow-popover transition-colors duration-120 ease-out-quart",
        "whitespace-nowrap",
        isPrimary && [
          "bg-accent-base text-text-inverse border-accent-base",
          "hover:bg-accent-hover hover:border-accent-hover",
        ],
        isDestructive && [
          "bg-error-bg text-error-base border-error-border",
          "hover:bg-error-bg/80",
        ],
        !isPrimary &&
          !isDestructive && [
            "bg-bg-raised text-text-secondary border-border-default",
            "hover:bg-bg-hover hover:text-text-primary",
          ]
      )}
    >
      <Icon className="h-3 w-3" />
      <span>{action.label}</span>
    </button>
  );
}

export const DAG_NODE_TYPES = { dag: DagNode };
