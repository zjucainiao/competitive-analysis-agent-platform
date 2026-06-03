"use client";

import { useState } from "react";
import { XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { StatusPill } from "./status-pill";
import type { DagNodeData } from "@/lib/dag-mock";

/**
 * Workspace 右侧 320px 常驻详情栏。
 *
 * 与之前的 NodeDetailSheet（弹出抽屉）不同 —— 这里是常驻列。
 * - 没选节点时：显示「选择左边的节点查看详情」空态
 * - 选了节点时：顶部 5 tab（概览 / 输入 / 输出 / 日志 / 证据），下方展示对应内容
 *
 * Tab 设计仅在 DAG tab 上有意义；其他 tab（Report / Trace 等）右栏可被该 tab
 * 自己的「上下文 inspector」覆盖。v1 先做 DAG 上的节点详情，作为最有用的入口。
 */

type DetailTab = "overview" | "inputs" | "outputs" | "logs" | "evidence";

const TABS: Array<{ id: DetailTab; label: string }> = [
  { id: "overview", label: "概览" },
  { id: "inputs", label: "输入" },
  { id: "outputs", label: "输出" },
  { id: "logs", label: "日志" },
  { id: "evidence", label: "证据" },
];

export function WorkspaceDetailsRail({
  nodeId,
  data,
  onClose,
}: {
  nodeId: string | null;
  data: DagNodeData | null;
  onClose?: () => void;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");

  return (
    <aside
      style={{ width: 320 }}
      className="fixed bottom-0 right-0 top-16 z-20 flex flex-col border-l border-border-subtle bg-bg-raised"
      aria-label="节点详情"
    >
      <header className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <span className="text-sm font-semibold text-text-primary">节点详情</span>
        {onClose && data ? (
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-text-muted hover:bg-bg-hover hover:text-text-primary"
          >
            <XIcon className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </header>

      {/* tab strip */}
      <div className="border-b border-border-subtle px-2">
        <div className="flex items-center">
          {TABS.map((t) => {
            const active = t.id === tab;
            const disabled = !data;
            return (
              <button
                key={t.id}
                type="button"
                disabled={disabled}
                onClick={() => setTab(t.id)}
                className={cn(
                  "relative px-2.5 py-2 text-xs font-medium transition-colors",
                  active
                    ? "text-accent-base"
                    : disabled
                      ? "text-text-muted/50"
                      : "text-text-muted hover:text-text-secondary"
                )}
              >
                {t.label}
                {active ? (
                  <span
                    aria-hidden
                    className="absolute inset-x-1.5 -bottom-px h-[2px] rounded-pill bg-accent-base"
                  />
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      {/* body */}
      <div className="flex-1 overflow-y-auto">
        {!data || !nodeId ? (
          <EmptyState />
        ) : tab === "overview" ? (
          <OverviewTab nodeId={nodeId} data={data} />
        ) : tab === "inputs" ? (
          <KVTab items={data.inputs} emptyHint="该节点无输入" />
        ) : tab === "outputs" ? (
          <KVTab items={data.outputs} emptyHint="该节点尚未产出" />
        ) : tab === "logs" ? (
          <LogsTab data={data} />
        ) : (
          <EvidenceTab data={data} />
        )}
      </div>
    </aside>
  );
}

/* ── empty ───────────────────────────────────────────────────────────── */

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="rounded-full bg-bg-sunken p-3">
        <div className="h-6 w-6 rounded-full bg-accent-bg" />
      </div>
      <p className="mt-3 text-sm font-medium text-text-secondary">
        选个节点查看详情
      </p>
      <p className="mt-1 text-[11px] text-text-muted">
        点击左侧 DAG 中任一节点
      </p>
    </div>
  );
}

/* ── overview ────────────────────────────────────────────────────────── */

function OverviewTab({ nodeId, data }: { nodeId: string; data: DagNodeData }) {
  return (
    <div className="space-y-5 p-4">
      <div>
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-text-primary">
            {data.label}
          </span>
          <StatusPill
            tone={data.status === "neutral" ? "neutral" : (data.status as never)}
            label={statusLabel(data.status)}
            pulse={data.status === "running"}
          />
        </div>
        <div className="mt-1 font-mono text-[11px] text-text-muted">
          {nodeId} · {data.agent}
        </div>
      </div>

      <Section title="节点信息">
        <KV k="状态" v={statusLabel(data.status)} />
        <KV k="重试次数" v={"0 / 3"} />
        {data.revision > 1 ? <KV k="版本" v={`v${data.revision}`} /> : null}
        {data.confidence != null && data.confidence > 0 ? (
          <KV k="置信度" v={data.confidence.toFixed(2)} />
        ) : null}
      </Section>

      <Section title="耗时与成本">
        <div className="grid grid-cols-2 gap-2">
          <Tile
            label="时长"
            value={formatDuration(data.durationMs)}
            tone="default"
          />
          <Tile
            label="Token"
            value={formatTokens(data.tokens)}
            tone="default"
          />
          <Tile
            label="成本"
            value={data.costUsd != null ? `$${data.costUsd.toFixed(2)}` : "—"}
            tone="accent"
          />
          <Tile
            label="LLM 调用"
            value={String(data.llmCalls.length)}
            tone="default"
          />
        </div>
      </Section>

      {data.selfCritique ? (
        <Section title="自我评价">
          <p className="text-xs leading-relaxed text-text-secondary">
            {data.selfCritique}
          </p>
        </Section>
      ) : null}
    </div>
  );
}

function KVTab({
  items,
  emptyHint,
}: {
  items: Array<{ key: string; value: string }>;
  emptyHint: string;
}) {
  if (items.length === 0) {
    return (
      <div className="p-6 text-center text-xs text-text-muted">{emptyHint}</div>
    );
  }
  return (
    <div className="space-y-3 p-4">
      {items.map((it, i) => (
        <div key={i}>
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
            {it.key}
          </div>
          <div className="mt-0.5 text-xs leading-relaxed text-text-primary break-words">
            {it.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function LogsTab({ data }: { data: DagNodeData }) {
  if (data.llmCalls.length === 0) {
    return (
      <div className="p-6 text-center text-xs text-text-muted">
        该节点暂无 LLM 调用记录
      </div>
    );
  }
  return (
    <ul className="divide-y divide-border-subtle">
      {data.llmCalls.map((c, i) => (
        <li key={i} className="space-y-1 px-4 py-3">
          <div className="flex items-center gap-2">
            <span
              className="font-mono text-[10px] text-text-muted tabular-nums"
              data-num
            >
              #{i + 1}
            </span>
            <span className="truncate font-mono text-xs text-text-primary">
              {c.model}
            </span>
          </div>
          <div className="flex items-center gap-3 font-mono text-[11px] text-text-muted tabular-nums">
            <span>{(c.durationMs / 1000).toFixed(1)}s</span>
            <span>·</span>
            <span>{c.tokensIn.toLocaleString()} in</span>
            <span>·</span>
            <span>{c.tokensOut.toLocaleString()} out</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function EvidenceTab({ data }: { data: DagNodeData }) {
  // 节点级 evidence 列表暂无直接字段；引导到 Evidence tab
  const out = data.outputs.find((o) => o.key === "evidences");
  return (
    <div className="space-y-3 p-4">
      {out ? (
        <div>
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
            产出 evidence
          </div>
          <div className="mt-0.5 text-sm text-text-primary">{out.value}</div>
        </div>
      ) : null}
      <p className="text-xs text-text-muted leading-relaxed">
        查看完整 evidence 列表 + 引用反查 → 切到「证据库」tab。
      </p>
    </div>
  );
}

/* ── small ───────────────────────────────────────────────────────────── */

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        {title}
      </div>
      {children}
    </section>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-xs">
      <span className="text-text-muted">{k}</span>
      <span className="font-mono tabular-nums text-text-primary" data-num>
        {v}
      </span>
    </div>
  );
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "default" | "accent";
}) {
  return (
    <div className="rounded-md border border-border-subtle bg-bg-overlay/60 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-text-muted">
        {label}
      </div>
      <div
        className={cn(
          "mt-0.5 font-mono text-sm font-semibold tabular-nums",
          tone === "accent" ? "text-accent-base" : "text-text-primary"
        )}
        data-num
      >
        {value}
      </div>
    </div>
  );
}

function statusLabel(status: DagNodeData["status"]): string {
  return (
    {
      success: "已完成",
      running: "运行中",
      rework: "需返工",
      warning: "警告",
      error: "失败",
      neutral: "等待中",
    }[status] ?? status
  );
}

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
