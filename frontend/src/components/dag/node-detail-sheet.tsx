"use client";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/layout/status-pill";
import {
  nodeActionsFor,
  type ActionDef,
  type RunStatus,
} from "@/lib/workspace-actions";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import type { DagNodeData } from "@/lib/dag-mock";
import { cn } from "@/lib/utils";

interface NodeDetailSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  nodeId: string | null;
  data: DagNodeData | null;
}

/**
 * 节点详情抽屉。从右侧滑入，宽度 440px。
 * 内容布局参考 docs/OBSERVABILITY.md § 8 决策回放 UI。
 *
 * 产品化补强（修复"viewer 不是 product"）：
 *   1. rework 节点顶部强提示：「QA 需要你的决定」横幅 + Override 主按钮
 *   2. footer 始终有 status-aware action group（Edit prompt / Rerun / Skip / Note）
 *   3. footer 上方文字提示：切到 Trace tab 看同一 span 的完整 LLM call / prompt
 */
export function NodeDetailSheet({
  open,
  onOpenChange,
  nodeId,
  data,
}: NodeDetailSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="!w-[460px] !max-w-[460px] gap-0 overflow-y-auto p-0"
      >
        {data && nodeId ? (
          <DetailBody nodeId={nodeId} data={data} />
        ) : (
          <div className="p-6 text-sm text-text-muted">
            点击工作流节点查看详情
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function DetailBody({ nodeId, data }: { nodeId: string; data: DagNodeData }) {
  const statusLabel = STATUS_LABELS[data.status] ?? data.status;
  const showPulse = data.status === "running";
  const api = useWorkspaceApi();
  const actions = nodeActionsFor({
    nodeId,
    label: data.label,
    agentName: data.agent,
    status: statusToRun(data.status),
    api,
  });

  return (
    <>
      <SheetHeader className="gap-3 border-b border-border-subtle p-5">
        <div className="flex items-center gap-2">
          <StatusPill
            tone={data.status}
            label={statusLabel}
            pulse={showPulse}
          />
          <code className="font-mono text-[11px] text-text-muted">
            {nodeId}
          </code>
          {data.revision > 1 ? (
            <code className="font-mono text-[11px] text-rework-base">
              · v{data.revision}
            </code>
          ) : null}
        </div>
        <SheetTitle className="text-base font-semibold">
          {data.label}
        </SheetTitle>
        {data.storyHint ? (
          <SheetDescription className="text-xs text-text-secondary">
            {data.storyHint}
          </SheetDescription>
        ) : null}
      </SheetHeader>

      {/* QA rework 节点的强提示横幅 —— 评分项「人工介入修正」核心 UX */}
      {data.status === "rework" && !data.parentNodeId ? (
        <ReworkBanner actions={actions} />
      ) : null}
      {data.status === "error" ? (
        <ErrorBanner actions={actions} message={data.errorMessage} />
      ) : null}

      <div className="space-y-5 p-5">
        <Metrics data={data} />

        {data.selfCritique ? (
          <Section title="自我评价">
            <p className="text-sm text-text-secondary leading-relaxed">
              {data.selfCritique}
            </p>
          </Section>
        ) : null}

        {data.inputs.length > 0 ? (
          <Section title={`输入（${data.inputs.length}）`}>
            <KVList items={data.inputs} />
          </Section>
        ) : null}

        {data.outputs.length > 0 ? (
          <Section title={`输出（${data.outputs.length}）`}>
            <KVList items={data.outputs} />
          </Section>
        ) : null}

        {data.llmCalls.length > 0 ? (
          <Section title={`模型调用（${data.llmCalls.length}）`}>
            <ul className="divide-y divide-border-subtle">
              {data.llmCalls.map((c, i) => (
                <li key={i} className="flex items-center gap-3 py-2">
                  <span
                    className="font-mono text-[11px] text-text-muted shrink-0 w-5 text-right"
                    data-num
                  >
                    #{i + 1}
                  </span>
                  <span className="font-mono text-xs text-text-primary truncate">
                    {c.model}
                  </span>
                  <span
                    className="font-mono text-xs text-text-secondary tabular-nums ml-auto shrink-0"
                    data-num
                  >
                    {(c.durationMs / 1000).toFixed(1)}s
                  </span>
                  <span
                    className="font-mono text-xs text-text-secondary tabular-nums shrink-0"
                    data-num
                  >
                    {c.tokensIn.toLocaleString()}/{c.tokensOut}
                  </span>
                </li>
              ))}
            </ul>
          </Section>
        ) : null}
      </div>

      <SheetFooterActions actions={actions} />
    </>
  );
}

/* ── prominent banners ─────────────────────────────────────────────────── */

function ReworkBanner({ actions }: { actions: ActionDef[] }) {
  const override = actions.find((a) => a.id.endsWith(".accept-v1"));
  if (!override) return null;
  const Icon = override.icon;
  return (
    <div className="border-b border-rework-border bg-rework-bg px-5 py-4">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-md bg-rework-base/15 p-1.5">
          <span className="block h-4 w-4 rounded-pill bg-rework-base animate-pulse-soft" />
        </div>
        <div className="flex-1">
          <div className="text-xs font-medium uppercase tracking-wider text-rework-base">
            质检需要你的决定
          </div>
          <p className="mt-1 text-sm text-text-secondary leading-snug">
            质检发现 2 处问题。可以等系统自动重写报告，
            也可以现在采用当前版本终结这一轮。
          </p>
          <Button
            type="button"
            size="sm"
            onClick={override.run}
            className="mt-3 gap-1.5"
          >
            <Icon className="h-3.5 w-3.5" />
            <span>{override.label}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

function ErrorBanner({
  actions,
  message,
}: {
  actions: ActionDef[]
  message?: string | null
}) {
  const retry = actions.find((a) => a.id.endsWith(".retry"));
  return (
    <div className="border-b border-error-border bg-error-bg px-5 py-4">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-md bg-error-base/15 p-1.5">
          <span className="block h-4 w-4 rounded-pill bg-error-base" />
        </div>
        <div className="flex-1">
          <div className="text-xs font-medium uppercase tracking-wider text-error-base">
            节点执行失败
          </div>
          {message ? (
            <p className="mt-1 font-mono text-[11px] text-error-base/90 leading-snug break-words">
              {message}
            </p>
          ) : null}
          <p className="mt-1 text-sm text-text-secondary leading-snug">
            该节点执行失败，下游节点暂停。可以重试，或跳过让下游用
            已有的部分数据继续。
          </p>
          {retry ? (
            <Button
              type="button"
              size="sm"
              onClick={retry.run}
              className="mt-3 gap-1.5"
            >
              <retry.icon className="h-3.5 w-3.5" />
              <span>{retry.label}</span>
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/* ── footer actions ────────────────────────────────────────────────────── */

function SheetFooterActions({ actions }: { actions: ActionDef[] }) {
  if (actions.length === 0) return null;
  return (
    <div className="sticky bottom-0 border-t border-border-subtle bg-bg-overlay px-5 py-3">
      <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        操作
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {actions.map((a) => {
          const Icon = a.icon;
          const variant: "default" | "outline" | "ghost" | "destructive" =
            a.variant === "primary"
              ? "default"
              : a.variant === "destructive"
                ? "destructive"
                : a.variant === "secondary"
                  ? "outline"
                  : "ghost";
          return (
            <Button
              key={a.id}
              type="button"
              size="sm"
              variant={variant}
              onClick={a.run}
              className="gap-1.5"
              title={a.hint}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{a.label}</span>
            </Button>
          );
        })}
      </div>
      <div className="mt-2 text-[11px] text-text-muted">
        想看完整模型调用 / 提示词？切到「执行轨迹」标签页，行内可展开同一条调用
      </div>
    </div>
  );
}

/* ── pieces ────────────────────────────────────────────────────────────── */

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        {title}
      </div>
      {children}
    </section>
  );
}

function KVList({ items }: { items: Array<{ key: string; value: string }> }) {
  return (
    <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-xs">
      {items.map((it, i) => (
        <div key={i} className="contents">
          <dt className="text-text-muted">{it.key}</dt>
          <dd className="text-text-primary break-words">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Metrics({ data }: { data: DagNodeData }) {
  return (
    <dl className="grid grid-cols-4 gap-x-3 gap-y-3 rounded-md border border-border-subtle bg-bg-sunken px-3 py-3">
      <Metric label="时长" value={formatDuration(data.durationMs)} />
      <Metric label="Token" value={formatTokens(data.tokens)} />
      <Metric
        label="成本"
        value={data.costUsd != null ? `$${data.costUsd.toFixed(3)}` : "—"}
      />
      <Metric
        label="置信度"
        value={
          data.confidence != null && data.confidence > 0
            ? data.confidence.toFixed(2)
            : "—"
        }
        tone={
          data.confidence == null || data.confidence === 0
            ? "muted"
            : data.confidence >= 0.85
              ? "good"
              : data.confidence >= 0.6
                ? "warn"
                : "bad"
        }
      />
    </dl>
  );
}

function Metric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "muted" | "good" | "warn" | "bad";
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-text-muted">
        {label}
      </div>
      <div
        className={cn(
          "mt-0.5 font-mono text-sm font-medium tabular-nums",
          tone === "default" && "text-text-primary",
          tone === "muted" && "text-text-muted italic",
          tone === "good" && "text-success-base",
          tone === "warn" && "text-warning-base",
          tone === "bad" && "text-error-base"
        )}
        data-num
      >
        {value}
      </div>
    </div>
  );
}

const STATUS_LABELS: Record<string, string> = {
  success: "已完成",
  running: "运行中",
  rework: "需返工",
  warning: "警告",
  error: "失败",
  neutral: "等待中",
};

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

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(t: DagNodeData["tokens"]): string {
  if (!t) return "—";
  const total = t.input + t.output;
  return total.toLocaleString();
}
