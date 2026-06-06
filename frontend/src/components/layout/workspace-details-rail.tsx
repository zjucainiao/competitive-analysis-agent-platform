"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { FileJsonIcon, ListTreeIcon, ScrollTextIcon, XIcon } from "lucide-react";
import { projectLLMCalls } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { StatusPill } from "./status-pill";
import type { DagNodeData } from "@/lib/dag-mock";
import type {
  AnyAgentOutput,
  CollectorOutput,
  DAGNode,
  ExtractorOutput,
  LLMCallRecord,
  ProjectStateResponse,
} from "@/lib/api/types";

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
  projectId,
  state,
  onClose,
}: {
  nodeId: string | null;
  data: DagNodeData | null;
  projectId?: string | null;
  state?: ProjectStateResponse | null;
  onClose?: () => void;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");
  const node = useMemo(
    () => state?.plan?.nodes.find((n) => n.node_id === nodeId) ?? null,
    [nodeId, state?.plan?.nodes]
  );
  const output = nodeId && state ? state.outputs[nodeId] : undefined;
  const { data: callsData } = useSWR(
    projectId && nodeId ? ["node-llm-calls", projectId, nodeId] : null,
    () => projectLLMCalls(projectId!, { nodeId: nodeId!, limit: 100 }),
    { refreshInterval: 10000, revalidateOnFocus: false }
  );
  const llmCalls = callsData?.calls ?? [];

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
          <OverviewTab
            nodeId={nodeId}
            data={data}
            node={node}
            output={output}
            llmCallCount={projectId && state ? llmCalls.length : data.llmCalls.length}
          />
        ) : tab === "inputs" ? (
          <InputsTab
            nodeId={nodeId}
            data={data}
            node={node}
            state={state ?? null}
          />
        ) : tab === "outputs" ? (
          <OutputsTab data={data} output={output} />
        ) : tab === "logs" ? (
          <LogsTab
            data={data}
            output={output}
            calls={llmCalls}
            isLive={Boolean(projectId && state)}
          />
        ) : (
          <EvidenceTab data={data} output={output} />
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

function OverviewTab({
  nodeId,
  data,
  node,
  output,
  llmCallCount,
}: {
  nodeId: string;
  data: DagNodeData;
  node: DAGNode | null;
  output: AnyAgentOutput | undefined;
  llmCallCount: number;
}) {
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
        <KV
          k="重试次数"
          v={`${node?.retry_count ?? 0} / ${node?.max_retries ?? 3}`}
        />
        {node?.started_at ? <KV k="开始" v={formatTime(node.started_at)} /> : null}
        {node?.ended_at ? <KV k="结束" v={formatTime(node.ended_at)} /> : null}
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
            value={String(llmCallCount)}
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

      {output?.errors && output.errors.length > 0 ? (
        <Section title="错误">
          <div className="space-y-2">
            {output.errors.map((e, i) => (
              <div
                key={`${e.code}-${i}`}
                className="rounded-md border border-error-border bg-error-bg/50 px-3 py-2 text-xs"
              >
                <div className="font-medium text-error-base">{e.code}</div>
                <p className="mt-1 leading-relaxed text-text-secondary">
                  {e.message}
                </p>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}

function InputsTab({
  nodeId,
  data,
  node,
  state,
}: {
  nodeId: string;
  data: DagNodeData;
  node: DAGNode | null;
  state: ProjectStateResponse | null;
}) {
  const items = buildInputItems(nodeId, data, node, state);
  const upstream = node?.input_refs ?? [];
  if (!state && items.length === 0) {
    return (
      <div className="p-6 text-center text-xs text-text-muted">该节点无输入</div>
    );
  }
  return (
    <div className="space-y-4 p-4">
      <IconSection icon={ListTreeIcon} title="本次 Agent 入参">
        <KVList items={items} />
      </IconSection>

      {upstream.length > 0 ? (
        <IconSection icon={ScrollTextIcon} title="上游依赖">
          <div className="space-y-2">
            {upstream.map((ref) => (
              <div
                key={ref}
                className="rounded-md border border-border-subtle bg-bg-overlay/60 px-3 py-2"
              >
                <div className="font-mono text-[11px] text-text-primary">
                  {ref}
                </div>
                <p className="mt-1 text-xs leading-relaxed text-text-muted">
                  {summarizeUpstream(ref, state?.outputs[ref])}
                </p>
              </div>
            ))}
          </div>
        </IconSection>
      ) : null}

      {node ? (
        <JsonBlock
          title="node metadata"
          value={{
            node_type: node.node_type,
            agent_name: node.agent_name,
            input_refs: node.input_refs,
            metadata: node.metadata,
          }}
        />
      ) : null}
    </div>
  );
}

function OutputsTab({
  data,
  output,
}: {
  data: DagNodeData;
  output: AnyAgentOutput | undefined;
}) {
  if (!output && data.outputs.length === 0) {
    return (
      <div className="p-6 text-center text-xs text-text-muted">
        该节点尚未产出
      </div>
    );
  }
  return (
    <div className="space-y-4 p-4">
      {data.outputs.length > 0 ? (
        <IconSection icon={ListTreeIcon} title="输出摘要">
          <KVList items={data.outputs} />
        </IconSection>
      ) : null}

      {output ? (
        <JsonBlock title="完整 Agent output" value={output} />
      ) : null}
    </div>
  );
}

function LogsTab({
  data,
  output,
  calls,
  isLive,
}: {
  data: DagNodeData;
  output: AnyAgentOutput | undefined;
  calls: LLMCallRecord[];
  isLive: boolean;
}) {
  const fallback = data.llmCalls.map((c) => ({
    timestamp: 0,
    trace_id: output?.trace_id ?? null,
    span_id: output?.span_id ?? null,
    node_id: output?.task_id ?? null,
    agent_name: output?.agent_name ?? null,
    model: c.model,
    phase: "summary",
    tokens_input: c.tokensIn,
    tokens_output: c.tokensOut,
    duration_s: c.durationMs / 1000,
    finish_reason: null,
    cost_usd: 0,
    prompt_preview: "",
    response_preview: "",
  }));
  const visibleCalls = calls.length > 0 ? calls : isLive ? [] : fallback;
  if (visibleCalls.length === 0 && !output) {
    return (
      <div className="p-6 text-center text-xs text-text-muted">
        该节点暂无日志
      </div>
    );
  }
  return (
    <div className="space-y-4 p-4">
      {output ? (
        <IconSection icon={ScrollTextIcon} title="运行日志">
          <KVList
            items={[
              { key: "trace_id", value: output.trace_id || "—" },
              { key: "span_id", value: output.span_id || "—" },
              { key: "agent_version", value: output.agent_version || "—" },
              { key: "status", value: output.status },
              {
                key: "tokens",
                value: `${output.tokens_input.toLocaleString()} in / ${output.tokens_output.toLocaleString()} out`,
              },
              {
                key: "duration",
                value: formatDuration(output.duration_ms),
              },
            ]}
          />
        </IconSection>
      ) : null}

      {visibleCalls.length > 0 ? (
        <IconSection icon={FileJsonIcon} title={`LLM calls (${visibleCalls.length})`}>
          <div className="space-y-3">
            {visibleCalls.map((c, i) => (
              <div
                key={`${c.span_id ?? "call"}-${c.timestamp}-${i}`}
                className="rounded-md border border-border-subtle bg-bg-overlay/60"
              >
                <div className="border-b border-border-subtle px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-xs text-text-primary">
                      {c.model}
                    </span>
                    <span className="rounded-sm bg-bg-sunken px-1.5 py-0.5 font-mono text-[10px] text-text-muted">
                      {c.phase}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] text-text-muted tabular-nums">
                    <span>{c.duration_s.toFixed(1)}s</span>
                    <span>{c.tokens_input.toLocaleString()} in</span>
                    <span>{c.tokens_output.toLocaleString()} out</span>
                    {c.finish_reason ? <span>{c.finish_reason}</span> : null}
                  </div>
                </div>
                {c.prompt_preview || c.response_preview ? (
                  <div className="space-y-2 px-3 py-2">
                    {c.prompt_preview ? (
                      <PreviewBlock label="prompt" text={c.prompt_preview} />
                    ) : null}
                    {c.response_preview ? (
                      <PreviewBlock label="response" text={c.response_preview} />
                    ) : null}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </IconSection>
      ) : isLive ? (
        <div className="rounded-md border border-border-subtle bg-bg-overlay/60 px-3 py-3 text-xs leading-relaxed text-text-muted">
          暂无实时 LLM 调用记录。当前日志来自后端进程内缓存；如果后端重启过，
          旧 run 的调用流水会被清空，重新跑一次节点后会显示真实调用。
        </div>
      ) : null}
    </div>
  );
}

function EvidenceTab({
  data,
  output,
}: {
  data: DagNodeData;
  output: AnyAgentOutput | undefined;
}) {
  // 节点级 evidence 列表暂无直接字段；引导到 Evidence tab
  const out = data.outputs.find((o) => o.key === "evidences");
  const evidences =
    output && "evidences" in output && Array.isArray((output as ExtractorOutput).evidences)
      ? (output as ExtractorOutput).evidences
      : [];
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
      {evidences.length > 0 ? (
        <div className="space-y-2">
          {evidences.slice(0, 8).map((ev) => (
            <div
              key={ev.evidence_id}
              className="rounded-md border border-border-subtle bg-bg-overlay/60 px-3 py-2"
            >
              <div className="font-mono text-[10px] text-accent-base">
                {ev.evidence_id}
              </div>
              <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-text-secondary">
                {ev.content}
              </p>
            </div>
          ))}
          {evidences.length > 8 ? (
            <p className="text-[11px] text-text-muted">
              还有 {evidences.length - 8} 条，切到「证据库」查看完整列表。
            </p>
          ) : null}
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

function IconSection({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        <Icon className="h-3 w-3" />
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

function KVList({ items }: { items: Array<{ key: string; value: string }> }) {
  if (items.length === 0) {
    return <div className="text-xs text-text-muted">暂无</div>;
  }
  return (
    <div className="space-y-3">
      {items.map((it, i) => (
        <div key={`${it.key}-${i}`}>
          <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
            {it.key}
          </div>
          <div className="mt-0.5 break-words text-xs leading-relaxed text-text-primary">
            {it.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <IconSection icon={FileJsonIcon} title={title}>
      <pre className="max-h-[420px] overflow-auto rounded-md border border-border-subtle bg-bg-sunken p-3 text-[10px] leading-relaxed text-text-secondary">
        {safeStringify(value)}
      </pre>
    </IconSection>
  );
}

function PreviewBlock({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
        {label}
      </div>
      <pre className="mt-1 max-h-36 overflow-auto whitespace-pre-wrap rounded-sm bg-bg-sunken p-2 text-[10px] leading-relaxed text-text-secondary">
        {text}
      </pre>
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

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatTokens(t: DagNodeData["tokens"]): string {
  if (!t) return "—";
  const total = t.input + t.output;
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`;
  return String(total);
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function buildInputItems(
  nodeId: string,
  data: DagNodeData,
  node: DAGNode | null,
  state: ProjectStateResponse | null
): Array<{ key: string; value: string }> {
  if (!state) return data.inputs;
  const project = state.project;
  const metadata = node?.metadata ?? {};
  const items: Array<{ key: string; value: string }> = [
    { key: "project", value: project.project_name },
    { key: "target_product", value: project.target_product },
    {
      key: "competitors",
      value: project.competitors.length > 0 ? project.competitors.join(", ") : "—",
    },
    { key: "industry", value: project.industry },
    { key: "analysis_dimensions", value: project.analysis_dimensions.join(", ") },
  ];

  if (data.agent === "collector") {
    items.push(
      { key: "product_name", value: String(metadata.product ?? "—") },
      { key: "official_url", value: String(metadata.official_url ?? "—") },
      {
        key: "collect_dimensions",
        value: Array.isArray(metadata.collect_dimensions)
          ? metadata.collect_dimensions.join(", ")
          : "—",
      },
      {
        key: "constraints",
        value: compactObject(project.collect_constraints),
      }
    );
  } else if (data.agent === "extractor") {
    const upstream = firstUpstreamOutput(node, state);
    const rawSources =
      upstream && "raw_sources" in upstream
        ? (upstream as CollectorOutput).raw_sources
        : [];
    items.push(
      { key: "product_name", value: String(metadata.product ?? "—") },
      {
        key: "raw_sources",
        value: `${rawSources.length} docs from ${node?.input_refs[0] ?? "upstream"}`,
      },
      { key: "industry_schema_id", value: `${project.industry}_v${project.industry_schema_version.split(".")[0]}` }
    );
  } else if (data.agent === "analyst") {
    items.push(
      { key: "profiles", value: `${countExtractorProfiles(state)} competitor profiles` },
      { key: "dimensions", value: project.analysis_dimensions.join(", ") }
    );
  } else if (data.agent === "reporter") {
    items.push(
      { key: "template_id", value: project.report_template_id },
      { key: "target_audience", value: project.target_audience ?? "—" },
      { key: "analysis_input", value: summarizeLatestOutput(state, "analyst") }
    );
  } else if (data.agent === "qa") {
    items.push(
      { key: "draft", value: summarizeLatestOutput(state, "reporter") },
      { key: "analysis", value: summarizeLatestOutput(state, "analyst") },
      { key: "prior_verdicts", value: `${countPriorVerdicts(state, nodeId)} verdicts` }
    );
  }
  return items;
}

function compactObject(value: unknown): string {
  return safeStringify(value).replace(/\s+/g, " ").trim();
}

function firstUpstreamOutput(
  node: DAGNode | null,
  state: ProjectStateResponse
): AnyAgentOutput | undefined {
  const ref = node?.input_refs[0];
  return ref ? state.outputs[ref] : undefined;
}

function summarizeUpstream(
  nodeId: string,
  output: AnyAgentOutput | undefined
): string {
  if (!output) return "尚未产出 output";
  if ("raw_sources" in output) {
    return `Collector output · ${(output as CollectorOutput).raw_sources.length} raw sources`;
  }
  if ("evidences" in output) {
    const out = output as ExtractorOutput;
    return `Extractor output · ${out.profile.basic_info.name} · ${out.evidences.length} evidences`;
  }
  if ("result" in output) {
    return `Analyst output · ${Object.keys(output.result.dimensions ?? {}).length} dimensions`;
  }
  if ("draft" in output) {
    return `Reporter output · v${output.draft.version} · ${output.draft.sections.length} sections`;
  }
  if ("verdict" in output) {
    return `QA output · ${output.verdict.overall_status} · ${output.verdict.issues.length} issues`;
  }
  return `${nodeId} · ${output.status}`;
}

function countExtractorProfiles(state: ProjectStateResponse): number {
  return Object.values(state.outputs).filter((out) => "profile" in out).length;
}

function countPriorVerdicts(state: ProjectStateResponse, currentNodeId: string): number {
  return Object.entries(state.outputs).filter(
    ([nodeId, out]) => nodeId !== currentNodeId && "verdict" in out
  ).length;
}

function summarizeLatestOutput(state: ProjectStateResponse, prefix: string): string {
  const entries = Object.entries(state.outputs)
    .filter(([nodeId]) => nodeId === prefix || nodeId.startsWith(`${prefix}_v`))
    .sort(([a], [b]) => b.localeCompare(a));
  const [nodeId, out] = entries[0] ?? [];
  if (!nodeId || !out) return "尚未产出";
  return summarizeUpstream(nodeId, out);
}
