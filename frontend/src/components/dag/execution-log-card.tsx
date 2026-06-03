"use client";

import { useMemo } from "react";
import Link from "next/link";
import { CircleIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  AnyAgentOutput,
  DAGNode,
  ProjectStateResponse,
} from "@/lib/api/types";

/**
 * DAG 主区下方的「执行日志」卡 —— 把最近一批节点状态切换 / agent 完成 / QA 通过等
 * 事件列成时间线条目。参考 AgentResearch 风格。
 *
 * 数据源：
 *  - API 模式：从 state.plan.nodes + state.outputs 排序派生条目
 *  - Mock 模式（无 state）：硬编码示例
 *
 * 每条条目：彩色 dot（按 tone） + agent 名 + 状态描述 + 相对时间。
 */

type LogTone = "success" | "running" | "rework" | "error" | "neutral";

interface LogEntry {
  at: string; // e.g. "10:24:12" or "2m ago"
  label: string;
  tone: LogTone;
}

const TONE_DOT: Record<LogTone, string> = {
  success: "text-success-base",
  running: "text-running-base",
  rework: "text-rework-base",
  error: "text-error-base",
  neutral: "text-neutral-base",
};

const MOCK_ENTRIES: LogEntry[] = [
  { at: "10:24:12", label: "信息采集 Agent 开始执行", tone: "running" },
  { at: "10:24:10", label: "证据入库 Agent 执行完成", tone: "success" },
  { at: "10:24:08", label: "信息采集 Agent 内容爬取中 (36/48)", tone: "running" },
  { at: "10:24:05", label: "任务规划 Agent 执行完成", tone: "success" },
  { at: "10:23:50", label: "项目启动 · plan 已生成", tone: "neutral" },
];

export function ExecutionLogCard({
  state,
  maxRows = 5,
}: {
  state?: ProjectStateResponse | null;
  maxRows?: number;
}) {
  const entries = useMemo<LogEntry[]>(() => {
    if (!state || !state.plan) return MOCK_ENTRIES.slice(0, maxRows);
    return deriveEntriesFromState(state, maxRows);
  }, [state, maxRows]);

  return (
    <section className="rounded-xl border border-border-subtle bg-bg-raised shadow-card">
      <header className="flex items-center justify-between border-b border-border-subtle px-4 py-2">
        <div className="text-xs font-semibold text-text-primary">执行日志</div>
        <button
          type="button"
          className="text-[11px] text-accent-base hover:text-accent-hover hover:underline"
        >
          查看全部
        </button>
      </header>
      {entries.length === 0 ? (
        <div className="px-5 py-4 text-center text-xs text-text-muted">
          暂无活动
        </div>
      ) : (
        <ul className="max-h-[136px] divide-y divide-border-subtle overflow-y-auto">
          {entries.map((e, i) => (
            <li
              key={i}
              className="flex items-center gap-3 px-4 py-1.5 text-xs"
            >
              <CircleIcon
                className={cn(
                  "h-2 w-2 shrink-0 fill-current",
                  TONE_DOT[e.tone]
                )}
              />
              <span
                className="shrink-0 font-mono tabular-nums text-text-muted"
                data-num
              >
                {e.at}
              </span>
              <span className="min-w-0 flex-1 truncate text-text-secondary">
                {e.label}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ── derive entries from API state ───────────────────────────────────── */

function deriveEntriesFromState(
  state: ProjectStateResponse,
  max: number
): LogEntry[] {
  const nodes = state.plan?.nodes ?? [];
  const outputs = state.outputs;
  // 1) 已结束的节点 → success / error / rework / skipped
  // 2) 正在跑的 → running
  // 3) 按 ended_at / started_at 时间倒序
  const records: Array<{
    sortKey: number;
    at: string;
    label: string;
    tone: LogTone;
  }> = [];
  for (const n of nodes) {
    const out: AnyAgentOutput | undefined = outputs[n.node_id];
    if (n.status === "running") {
      records.push({
        sortKey: parseTime(n.started_at) ?? 0,
        at: shortTime(n.started_at) ?? "—",
        label: `${labelOf(n)} 运行中`,
        tone: "running",
      });
    } else if (n.status === "success") {
      records.push({
        sortKey: parseTime(n.ended_at) ?? parseTime(n.started_at) ?? 0,
        at: shortTime(n.ended_at) ?? shortTime(n.started_at) ?? "—",
        label: `${labelOf(n)} 执行完成`,
        tone: "success",
      });
    } else if (n.status === "needs_rework") {
      records.push({
        sortKey: parseTime(n.ended_at) ?? 0,
        at: shortTime(n.ended_at) ?? "—",
        label: `${labelOf(n)} 标记需要返工${
          out && "verdict" in out
            ? ` · ${out.verdict.issues.length} 处问题`
            : ""
        }`,
        tone: "rework",
      });
    } else if (n.status === "failed") {
      records.push({
        sortKey: parseTime(n.ended_at) ?? 0,
        at: shortTime(n.ended_at) ?? "—",
        label: `${labelOf(n)} 执行失败`,
        tone: "error",
      });
    } else if (n.status === "skipped") {
      records.push({
        sortKey: parseTime(n.ended_at) ?? 0,
        at: shortTime(n.ended_at) ?? "—",
        label: `${labelOf(n)} 已跳过`,
        tone: "neutral",
      });
    }
  }
  return records
    .sort((a, b) => b.sortKey - a.sortKey)
    .slice(0, max)
    .map(({ at, label, tone }) => ({ at, label, tone }));
}

function labelOf(n: DAGNode): string {
  const agentName = n.agent_name;
  if (!agentName) return n.node_id;
  const cn = AGENT_CN[agentName];
  return cn ? `${cn} Agent` : `${agentName} Agent`;
}

const AGENT_CN: Record<string, string> = {
  collector: "信息采集",
  extractor: "证据入库",
  analyst: "结构化分析",
  reporter: "报告撰写",
  qa: "质量审查",
};

function parseTime(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

function shortTime(iso: string | null): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return null;
  }
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}
