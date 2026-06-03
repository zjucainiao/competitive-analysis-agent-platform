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
import { phaseOf } from "@/lib/agent-phases";

/**
 * DAG 节点视觉 + 动作（Q 版横向流水线）。
 *
 * 视觉规范：
 *  - 220 × 88，rounded-2xl（16px）—— 比 v1 (156×72/6px) 更圆更胖
 *  - 双层背景：白底卡片 + 状态色超浅 wash（不喧宾夺主）
 *  - 大状态点（10px）位于左上，与圆角呼应
 *  - 软影常驻 hover 时加深
 *  - running 节点 ring pulse；selected 节点 accent ring
 *  - revision >1 / rework 角标：仍走右上角徽章
 *
 * 横向流水线交互：
 *  - Handle 在左右（target=Left, source=Right）
 *  - NodeToolbar 在节点下方展开（不挡同行邻居）
 *
 * 评分项「人工介入修正」核心：
 *  - 选中、rework、error 时露出 action chips（Override / Retry / Skip…）
 *  - 朱漆橙时代是 accent fill；现在紫罗兰 accent
 */

/* 极浅 wash（每个状态都是 var(--*-bg) 但再淡一点，让卡片白底为主） */
const STATUS_TINT: Record<string, string> = {
  success: "bg-success-bg/30",
  running: "bg-running-bg/40",
  rework: "bg-rework-bg/40",
  warning: "bg-warning-bg/40",
  error: "bg-error-bg/40",
  neutral: "bg-neutral-bg/20",
};

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

const STATUS_TEXT: Record<string, string> = {
  success: "text-success-base",
  running: "text-running-base",
  rework: "text-rework-base",
  warning: "text-warning-base",
  error: "text-error-base",
  neutral: "text-text-muted",
};

const STATUS_LABEL: Record<string, string> = {
  success: "ok",
  running: "running",
  rework: "rework",
  warning: "warning",
  error: "failed",
  neutral: "pending",
};

/** 阶段徽章 tone → tailwind class（柔和底色，不喧宾夺主于 status 颜色） */
const PHASE_BADGE_TONE: Record<string, string> = {
  "viz-1": "bg-viz-1/15 text-viz-1",
  "viz-2": "bg-viz-2/15 text-viz-2",
  "viz-3": "bg-viz-3/15 text-viz-3",
  accent: "bg-accent-bg text-accent-base",
  muted: "bg-neutral-bg text-text-muted",
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

  // 4 阶段流水线呈现：collector + extractor 同属阶段 1「采集与结构化」，
  // 内部 agent 名（collector / extractor）放第三行 mono 小字保留可追溯性。
  const phase = phaseOf(data.agent);

  return (
    <>
      <NodeToolbar
        isVisible={toolbarVisible}
        position={Position.Bottom}
        offset={12}
        align="center"
      >
        <div className="flex flex-wrap items-center justify-center gap-1.5">
          {actions.map((a) => (
            <ActionChip key={a.id} action={a} />
          ))}
        </div>
      </NodeToolbar>

      <div
        className={cn(
          "group relative w-[220px] rounded-2xl border-2 bg-bg-raised px-4 py-3",
          "shadow-card transition-all duration-180 ease-out-quart",
          "hover:-translate-y-0.5 hover:shadow-popover",
          STATUS_BORDER[data.status] ?? "border-border-default",
          STATUS_TINT[data.status],
          selected && "ring-4 ring-accent-base/25",
          /* rework / error 微高光 */
          data.status === "rework" && "ring-4 ring-rework-base/15",
          data.status === "error" && "ring-4 ring-error-base/15",
          /* running 节点呼吸感 */
          data.status === "running" && "ring-4 ring-running-base/15"
        )}
      >
        {/* 横向：target 在左，source 在右 */}
        <Handle
          type="target"
          position={Position.Left}
          className="!h-2.5 !w-2.5 !min-w-0 !border-2 !border-bg-raised !bg-border-strong"
        />
        <Handle
          type="source"
          position={Position.Right}
          className="!h-2.5 !w-2.5 !min-w-0 !border-2 !border-bg-raised !bg-border-strong"
        />

        {/* 顶行：大状态点 + label + 状态文字 */}
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "h-2.5 w-2.5 shrink-0 rounded-pill ring-2 ring-bg-raised",
              STATUS_DOT[data.status] ?? "bg-neutral-base",
              data.status === "running" && "animate-pulse-soft"
            )}
            aria-hidden
          />
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-sm font-semibold",
              isControl ? "text-text-muted" : "text-text-primary"
            )}
          >
            {data.label}
          </span>
          <span
            className={cn(
              "shrink-0 text-[10px] font-medium uppercase tracking-wider",
              STATUS_TEXT[data.status]
            )}
          >
            {STATUS_LABEL[data.status]}
          </span>
        </div>

        {/* 第二行：阶段徽章 + agent 内部代号 */}
        <div className="mt-1 ml-4.5 flex items-center gap-1.5 text-[11px]">
          {!isControl && phase.order > 0 ? (
            <span
              className={cn(
                "shrink-0 rounded-pill px-1.5 py-0 font-medium text-text-secondary",
                PHASE_BADGE_TONE[phase.tone]
              )}
            >
              阶段 {phase.order} · {phase.label}
            </span>
          ) : null}
          <span className="truncate font-mono text-[10px] text-text-muted">
            {data.agent}
            {data.revision > 1 ? ` · v${data.revision}` : null}
          </span>
        </div>

        {/* 第三行：metrics */}
        <div
          className="mt-2 flex items-center gap-3 font-mono text-[11px] tabular-nums text-text-muted"
          data-num
        >
          {data.status === "neutral" ? (
            <span className="italic">awaiting upstream</span>
          ) : (
            <>
              <span>{formatDuration(data.durationMs)}</span>
              <span className="text-border-default">·</span>
              <span>{formatTokens(data.tokens)} tok</span>
              {data.costUsd != null && data.costUsd > 0 ? (
                <>
                  <span className="text-border-default">·</span>
                  <span>${data.costUsd.toFixed(2)}</span>
                </>
              ) : null}
            </>
          )}
        </div>

        {/* 角标 */}
        {data.revision > 1 ? (
          <div className="absolute -top-2 -right-2 rounded-pill bg-rework-base px-2 py-0.5 text-[10px] font-semibold leading-tight text-text-inverse shadow-popover">
            v{data.revision}
          </div>
        ) : null}

        {data.status === "rework" && !data.parentNodeId ? (
          <div className="absolute -top-2 left-3 inline-flex items-center gap-1 rounded-pill bg-rework-base px-2 py-0.5 text-[10px] font-semibold leading-tight text-text-inverse shadow-popover">
            <span>⚠</span>
            <span>需介入</span>
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
        "inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[11px] font-medium",
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
