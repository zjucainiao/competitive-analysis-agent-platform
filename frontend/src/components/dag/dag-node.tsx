"use client";

import { Handle, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { CheckIcon, LoaderIcon, OctagonXIcon, AlertTriangleIcon } from "lucide-react";
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
 * DAG 节点 —— AgentResearch 风信息密集卡片。
 *
 * 视觉规范（参考用户提供的截图）：
 *  - 280 × 自适应（~ 190px）
 *  - rounded-2xl border-2
 *  - Header：编号圆徽 + agent 名 + 右侧状态图标
 *  - 状态行：状态文字 + 运行时长
 *  - 输入 / 输出：每个 label + 一行值
 *  - 底部：2 个 KPI 小 tile（按 agent 类型显示不同字段）
 *
 * 横向流水线：Handle 左 / 右；NodeToolbar 在节点下方。
 */

const STATUS_BORDER: Record<string, string> = {
  success: "border-success-border",
  running: "border-running-border",
  rework: "border-rework-border",
  warning: "border-warning-border",
  error: "border-error-border",
  neutral: "border-neutral-border",
};

const STATUS_TINT: Record<string, string> = {
  success: "bg-success-bg/25",
  running: "bg-running-bg/35",
  rework: "bg-rework-bg/35",
  warning: "bg-warning-bg/35",
  error: "bg-error-bg/35",
  neutral: "bg-bg-raised",
};

const STATUS_BADGE_BG: Record<string, string> = {
  success: "bg-success-base text-text-inverse",
  running: "bg-running-base text-text-inverse",
  rework: "bg-rework-base text-text-inverse",
  warning: "bg-warning-base text-text-inverse",
  error: "bg-error-base text-text-inverse",
  neutral: "bg-bg-sunken text-text-muted border border-border-default",
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
  success: "已完成",
  running: "运行中",
  rework: "需返工",
  warning: "警告",
  error: "失败",
  neutral: "等待中",
};

/** agent 中文名 */
const AGENT_CN: Record<string, string> = {
  control: "控制",
  planner: "任务规划",
  collector: "信息采集",
  extractor: "证据入库",
  analyst: "结构化分析",
  reporter: "报告撰写",
  qa: "质量审查",
  end: "输出报告",
  output: "输出报告",
};

function agentZh(agent: string): string {
  return AGENT_CN[agent] ?? agent;
}

/* ── per-agent KPI tile config ───────────────────────────────────────── */

interface TileSpec {
  label: string;
  value: string;
}

function tilesFor(data: DagNodeData): [TileSpec, TileSpec] {
  const out = data.outputs;
  // helper: lookup output by key prefix
  const findOut = (kw: string) =>
    out.find((o) => o.key.toLowerCase().includes(kw))?.value;

  const isPending = data.status === "neutral";
  const dash = "—";

  if (data.agent === "planner" || data.label.includes("规划")) {
    const tasks = findOut("task") || findOut("next") || "—";
    return [
      { label: "任务数", value: isPending ? dash : extractNumber(tasks) ?? "18" },
      { label: "完成度", value: isPending ? dash : data.status === "success" ? "100%" : "—" },
    ];
  }
  if (data.agent === "collector") {
    return [
      { label: "文档数", value: isPending ? dash : extractNumber(findOut("raw") || findOut("doc")) ?? "—" },
      { label: "证据数", value: isPending ? dash : extractNumber(findOut("evid") || findOut("mint")) ?? "—" },
    ];
  }
  if (data.agent === "extractor") {
    return [
      { label: "入库数", value: isPending ? dash : extractNumber(findOut("evid") || findOut("link")) ?? "—" },
      { label: "完成度", value: isPending ? dash : data.status === "success" ? "100%" : "—" },
    ];
  }
  if (data.agent === "analyst") {
    return [
      { label: "结论数", value: isPending ? dash : extractNumber(findOut("claim") || findOut("dim")) ?? "—" },
      {
        label: "置信度",
        value:
          isPending || data.confidence == null || data.confidence === 0
            ? dash
            : data.confidence.toFixed(2),
      },
    ];
  }
  if (data.agent === "reporter") {
    return [
      { label: "章节数", value: isPending ? dash : extractNumber(findOut("draft") || findOut("section")) ?? "—" },
      {
        label: "置信度",
        value:
          isPending || data.confidence == null || data.confidence === 0
            ? dash
            : data.confidence.toFixed(2),
      },
    ];
  }
  if (data.agent === "qa") {
    return [
      { label: "问题数", value: isPending ? dash : extractNumber(findOut("verd") || findOut("issue")) ?? "—" },
      {
        label: "通过率",
        value:
          isPending || data.confidence == null || data.confidence === 0
            ? dash
            : `${Math.round(data.confidence * 100)}%`,
      },
    ];
  }
  // 默认：时长 + token
  return [
    { label: "时长", value: isPending ? dash : formatDuration(data.durationMs) },
    { label: "Token", value: isPending ? dash : formatTokens(data.tokens) },
  ];
}

/** 从一段字符串里抠出第一个数字串（含 k / % 单位保留） */
function extractNumber(s: string | undefined): string | null {
  if (!s) return null;
  const m = s.match(/(\d[\d,.]*)(\s*[kK%])?/);
  if (!m) return null;
  return m[0].replace(/\s+/g, "");
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
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

function StatusIcon({ status }: { status: DagNodeData["status"] }) {
  const cls = "h-4 w-4 shrink-0";
  if (status === "success") return <CheckIcon className={cn(cls, "text-success-base")} />;
  if (status === "running")
    return <LoaderIcon className={cn(cls, "text-running-base animate-spin")} />;
  if (status === "rework")
    return <AlertTriangleIcon className={cn(cls, "text-rework-base")} />;
  if (status === "error") return <OctagonXIcon className={cn(cls, "text-error-base")} />;
  return null;
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

  const phase = phaseOf(data.agent);
  const tiles = tilesFor(data);
  const firstIn = data.inputs[0];
  const firstOut = data.outputs[0];
  const displayName = `${agentZh(data.agent)} Agent`;

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
          "group relative w-[280px] rounded-2xl border-2 bg-bg-raised px-4 py-3.5",
          "shadow-card transition-all duration-180 ease-out-quart",
          "hover:-translate-y-0.5 hover:shadow-popover",
          STATUS_BORDER[data.status] ?? "border-border-default",
          STATUS_TINT[data.status],
          selected && "ring-4 ring-accent-base/25",
          data.status === "running" && "ring-4 ring-running-base/15",
          data.status === "rework" && "ring-4 ring-rework-base/15",
          data.status === "error" && "ring-4 ring-error-base/15"
        )}
      >
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

        {/* Header: 编号圆徽 + agent 名 + status icon */}
        <div className="flex items-center gap-2.5">
          {!isControl && phase.order > 0 ? (
            <span
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-mono text-xs font-semibold tabular-nums shadow-card",
                STATUS_BADGE_BG[data.status] ?? STATUS_BADGE_BG.neutral,
                data.status === "running" && "animate-pulse-soft"
              )}
              data-num
            >
              {phase.order}
            </span>
          ) : null}
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-sm font-semibold",
              isControl ? "text-text-muted" : "text-text-primary"
            )}
          >
            {displayName}
          </span>
          <StatusIcon status={data.status} />
        </div>

        {/* 状态 + 时长行 */}
        <div className="mt-1.5 flex items-baseline gap-2">
          <span
            className={cn(
              "text-xs font-medium",
              STATUS_TEXT[data.status]
            )}
          >
            {STATUS_LABEL[data.status]}
          </span>
          <span
            className="font-mono text-[11px] tabular-nums text-text-muted"
            data-num
          >
            {data.status === "neutral" ? "" : formatDuration(data.durationMs)}
          </span>
        </div>

        {/* 输入 / 输出 */}
        {(firstIn || firstOut) && !isControl ? (
          <div className="mt-3 space-y-1.5">
            {firstIn ? (
              <div className="flex gap-2">
                <span className="shrink-0 text-[10px] font-medium uppercase tracking-wider text-text-muted">
                  输入
                </span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-text-secondary">
                  {firstIn.value}
                </span>
              </div>
            ) : null}
            {firstOut ? (
              <div className="flex gap-2">
                <span className="shrink-0 text-[10px] font-medium uppercase tracking-wider text-text-muted">
                  输出
                </span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-text-secondary">
                  {firstOut.value}
                </span>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* 底部 2 KPI tile */}
        {!isControl ? (
          <div className="mt-3 grid grid-cols-2 gap-2 border-t border-border-subtle pt-3">
            <Tile label={tiles[0].label} value={tiles[0].value} />
            <Tile label={tiles[1].label} value={tiles[1].value} />
          </div>
        ) : null}

        {/* revision 角标 */}
        {data.revision > 1 ? (
          <div className="absolute -top-2 -right-2 rounded-pill bg-rework-base px-2 py-0.5 text-[10px] font-semibold leading-tight text-text-inverse shadow-popover">
            v{data.revision}
          </div>
        ) : null}

        {/* rework 提醒 */}
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

/* ── tile ────────────────────────────────────────────────────────────── */

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span
        className="font-mono text-base font-semibold tabular-nums text-text-primary"
        data-num
      >
        {value}
      </span>
      <span className="mt-0.5 text-[10px] text-text-muted">{label}</span>
    </div>
  );
}

/* ── action chip ─────────────────────────────────────────────────────── */

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
