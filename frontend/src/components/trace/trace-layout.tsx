"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { InfoIcon } from "lucide-react";
import {
  TRACE_SPANS,
  summarizeTrace,
  type DiffPair,
  type FullLLMCall,
  type TraceSpan,
  type TraceSummary,
} from "@/lib/trace-mock";
import { TraceHeader, type TraceFilter } from "./trace-summary";
import { TraceRow } from "./trace-row";
import { DiffSheet } from "./diff-sheet";
import { projectLLMCalls } from "@/lib/api/client";
import type {
  ProjectStateResponse,
  AnyAgentOutput,
  AgentStatus,
  LLMCallRecord,
} from "@/lib/api/types";

interface TraceLayoutProps {
  /** 真实 API 模式：从 outputs 派生 TraceSpan 列表 */
  apiState?: ProjectStateResponse;
}

/**
 * Workspace · Trace tab。
 *
 * 双模式：
 *  - apiState 提供 → 用真实 outputs 派生 span（无完整 LLM call 详情）
 *  - 否则使用 TRACE_SPANS mock（含 prompt 完整内容 + diff sheet）
 *
 * v1 后端 /state 不返回 LLM call records（属于 TraceRecord 独立表）。
 * Sprint 2 backend 暴露 /api/projects/{id}/trace 后可填满详情。
 */
export function TraceLayout({ apiState }: TraceLayoutProps = {}) {
  const [filter, setFilter] = useState<TraceFilter>("all");
  const [agentFilter, setAgentFilter] = useState<Set<string>>(new Set());
  const [diffOpen, setDiffOpen] = useState(false);

  const isApi = apiState !== undefined;
  const projectId = apiState?.project.project_id ?? null;

  /* 拉 LLM call 流水（仅 API 模式）。每 15s 自动刷新，捕捉运行时新调用。 */
  const { data: llmCallsData } = useSWR(
    isApi && projectId ? ["llm-calls", projectId] : null,
    () => projectLLMCalls(projectId!, { limit: 500 }),
    { refreshInterval: 15000, revalidateOnFocus: false }
  );
  const llmCalls = llmCallsData?.calls ?? [];

  const spans: TraceSpan[] = useMemo(() => {
    if (isApi && apiState) return apiStateToSpans(apiState, llmCalls);
    return TRACE_SPANS;
  }, [isApi, apiState, llmCalls]);

  const summary: TraceSummary = useMemo(() => summarizeTrace(spans), [spans]);

  /* 真实返工对的 v1↔v2 prompt diff（API 模式）。null = 本次运行还没返工轮次。 */
  const apiDiffPair = useMemo(
    () => (isApi ? buildApiDiffPair(spans) : undefined),
    [isApi, spans]
  );

  const visibleSpans = useMemo(() => {
    let arr = spans;
    if (filter === "errors") {
      arr = arr.filter((s) => s.status === "error");
    } else if (filter === "rework") {
      arr = arr.filter(
        (s) =>
          s.status === "rework" ||
          s.isFeedbackTarget ||
          (s.status === "neutral" && s.parentNodeId !== null)
      );
    }
    if (agentFilter.size > 0) {
      arr = arr.filter((s) => agentFilter.has(s.agent));
    }
    return arr;
  }, [spans, filter, agentFilter]);

  return (
    <div className="space-y-4">
      <TraceHeader
        summary={summary}
        filter={filter}
        onFilterChange={setFilter}
        agentFilter={agentFilter}
        onAgentFilterChange={setAgentFilter}
        onOpenDiff={() => setDiffOpen(true)}
      />

      {isApi ? (
        <div className="flex items-start gap-2 rounded-md border border-running-border bg-running-bg/40 px-4 py-2.5 text-[11px] text-running-base">
          <InfoIcon className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <p className="leading-relaxed text-text-secondary">
            <span className="font-medium text-running-base">
              实时模式 · 已记录 {llmCalls.length} 次模型调用 · 每 15 秒自动刷新
            </span>{" "}
            · 每条记录含状态 / 耗时 / Token / 成本 / 置信度；
            点击返工或运行中的行展开，可查看真实的
            提示词摘要 · 模型回复 · 执行阶段 · 完成原因
            （来源 <code className="font-mono">GET /api/projects/&#123;id&#125;/llm-calls</code>）。
          </p>
        </div>
      ) : null}

      <div className="overflow-hidden rounded-lg border border-border-subtle bg-bg-raised">
        {visibleSpans.length === 0 ? (
          <div className="p-10 text-center text-sm text-text-muted">
            {isApi
              ? "暂无执行记录 · 等流水线开始跑各节点"
              : "没有匹配筛选的记录"}
          </div>
        ) : (
          <ol className="divide-y divide-border-subtle">
            {visibleSpans.map((s) => (
              <li key={s.spanId}>
                <TraceRow
                  span={s}
                  defaultOpen={s.status === "rework" || s.isFeedbackTarget}
                />
              </li>
            ))}
          </ol>
        )}
      </div>

      {!isApi ? (
        <div className="rounded-md border border-dashed border-border-default bg-bg-sunken/60 px-4 py-3 text-[11px] text-text-muted">
          <strong className="font-medium text-text-secondary">
            决策回放（time-travel）
          </strong>
          ：本面板是 evaluator 一眼看到「QA 反馈闭环 + Agent
          如何收到反馈」的关键证据。点击 rework 行展开 LLM call →
          看到 reporter_v2 system prompt 里已经注入了 QA FEEDBACK 章节；
          点击右上「diff v1 ↔ v2」并排对比 v1/v2 prompt。
        </div>
      ) : apiDiffPair ? (
        <div className="rounded-md border border-dashed border-border-default bg-bg-sunken/60 px-4 py-3 text-[11px] text-text-muted">
          <strong className="font-medium text-text-secondary">
            决策回放（真实运行）
          </strong>
          ：本次运行触发了 QA 返工 ——{" "}
          <code className="font-mono">{apiDiffPair.rightLabel}</code> 的 system
          prompt 顶部已注入 QA FEEDBACK（来自{" "}
          <code className="font-mono">GET /api/projects/&#123;id&#125;/llm-calls</code>{" "}
          的真实 prompt_preview）。点击右上「diff v1 ↔ v2」并排对比{" "}
          <code className="font-mono">{apiDiffPair.leftLabel}</code> ↔{" "}
          <code className="font-mono">{apiDiffPair.rightLabel}</code>。
        </div>
      ) : null}

      <DiffSheet
        open={diffOpen}
        onOpenChange={setDiffOpen}
        /* demo: undefined → mock diff；API 有返工 → 真实 diff；API 无返工 → null → 空状态 */
        pair={isApi ? (apiDiffPair ?? null) : undefined}
      />
    </div>
  );
}

/* ── adapter: ProjectStateResponse → TraceSpan[] ─────────────────────── */

const AGENT_STATUS_MAP: Record<AgentStatus, TraceSpan["status"]> = {
  success: "success",
  partial: "success",
  needs_rework: "rework",
  failed: "error",
};

function apiStateToSpans(
  state: ProjectStateResponse,
  llmCalls: LLMCallRecord[] = []
): TraceSpan[] {
  const plan = state.plan;
  if (!plan) return [];

  /* group LLM calls by node_id (按 timestamp 升序) */
  const callsByNode = new Map<string, LLMCallRecord[]>();
  for (const c of [...llmCalls].sort((a, b) => a.timestamp - b.timestamp)) {
    if (!c.node_id) continue;
    const list = callsByNode.get(c.node_id) ?? [];
    list.push(c);
    callsByNode.set(c.node_id, list);
  }

  return plan.nodes.map((n) => {
    const out: AnyAgentOutput | undefined = state.outputs[n.node_id];
    const status: TraceSpan["status"] = out
      ? AGENT_STATUS_MAP[out.status] ?? "neutral"
      : n.status === "running"
        ? "running"
        : n.status === "needs_rework"
          ? "rework"
          : n.status === "failed"
            ? "error"
            : n.status === "success"
              ? "success"
              : "neutral";

    return {
      spanId: out?.span_id ?? `span_${n.node_id}`,
      nodeId: n.node_id,
      label: n.node_id,
      agent: n.agent_name ?? "control",
      status,
      startedAt: n.started_at
        ? new Date(n.started_at).toISOString().slice(11, 19)
        : "—",
      durationMs: out?.duration_ms ?? null,
      tokensIn: out?.tokens_input ?? 0,
      tokensOut: out?.tokens_output ?? 0,
      costUsd: out?.cost_usd ?? 0,
      confidence: out?.confidence ?? null,
      selfCritique: out?.self_critique || null,
      storyHint: null,
      revision: n.revision,
      parentNodeId: n.parent_node_id,
      llmCalls: (callsByNode.get(n.node_id) ?? []).map(apiCallToFull),
      toolCalls: [],
      summary: summarizeNode(n.node_id, out),
      isFeedbackTarget: n.parent_node_id !== null,
    };
  });
}

/** 后端 ring buffer 没存全 prompt/messages，只有 prompt_preview / response_preview，
 *  足够让 Trace tab 展开看到「这次调用大概问了什么 / 模型怎么答的」。 */
function apiCallToFull(c: LLMCallRecord): FullLLMCall {
  const finishReason: FullLLMCall["finishReason"] =
    c.finish_reason === "length" ||
    c.finish_reason === "tool_use" ||
    c.finish_reason === "max_tokens"
      ? c.finish_reason
      : "stop";
  return {
    callId: `${c.span_id ?? c.node_id ?? "call"}-${c.timestamp.toFixed(3)}`,
    model: c.model,
    temperature: 0,
    maxTokens: 0,
    systemPrompt: c.prompt_preview || "[提示词快照暂不可用]",
    messages: [],
    responseJson: c.response_preview || "",
    tokensIn: c.tokens_input,
    tokensOut: c.tokens_output,
    finishReason,
    durationMs: Math.round(c.duration_s * 1000),
    costUsd: c.cost_usd,
  };
}

/** 从真实 span 构造 v1↔v2 prompt diff：取一个返工节点（parent_node_id != null）
 *  及其父节点，各自第一条 LLM call 的 prompt_preview 做并排对比。返工节点的
 *  preview 顶部含 prepend 到 system 的 QA FEEDBACK，父节点没有 → diff 自然高亮注入。
 *  没有返工轮次（或缺 LLM call 记录）时返回 undefined。 */
function buildApiDiffPair(spans: TraceSpan[]): DiffPair | undefined {
  const reworks = spans.filter(
    (s) => s.parentNodeId !== null && s.llmCalls.length > 0
  );
  // 优先 reporter 返工：它把 QA FEEDBACK prepend 到 system 顶部，diff 最直观
  const rework =
    reworks.find((s) => s.agent === "reporter") ?? reworks[0];
  if (!rework || rework.parentNodeId === null) return undefined;
  const parent = spans.find((s) => s.nodeId === rework.parentNodeId);
  if (!parent || parent.llmCalls.length === 0) return undefined;

  const leftContent = parent.llmCalls[0].systemPrompt;
  const rightContent = rework.llmCalls[0].systemPrompt;
  if (!leftContent || !rightContent) return undefined;

  return {
    id: `diff-${rework.nodeId}`,
    label: `${rework.agent} · prompt（真实运行）`,
    description: `真实运行：${rework.nodeId} 的 system prompt 顶部注入了 QA FEEDBACK（来源 prompt_preview）`,
    leftLabel: parent.nodeId,
    rightLabel: rework.nodeId,
    leftContent,
    rightContent,
  };
}

function summarizeNode(
  nodeId: string,
  out: AnyAgentOutput | undefined
): string {
  if (!out) return "pending · awaiting upstream";
  if (out.status === "failed") {
    return out.errors[0]?.message ?? "failed";
  }
  if (out.status === "needs_rework") {
    return "needs_revision · QA routing triggered";
  }
  if ("verdict" in out) {
    return `verdict · ${(out as { verdict: { overall_status: string; issues: unknown[] } }).verdict.overall_status} · ${(out as { verdict: { issues: unknown[] } }).verdict.issues.length} issues`;
  }
  if ("draft" in out) {
    return `ReportDraft v${(out as { draft: { version: number } }).draft.version}`;
  }
  return `${nodeId} ok`;
}
