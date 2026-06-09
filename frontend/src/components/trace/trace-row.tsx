"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  ArrowUpRightIcon,
  CircleAlertIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { LLMCallDetail } from "./llm-call-detail";
import type { TraceSpan } from "@/lib/trace-mock";

/**
 * Trace 时间轴单行 + inline expand。
 *
 *   14:00:55 ┃ ⚠qa             revise   8.2s   7.8k tok       ▼
 *             ┃ summary text (issues / routing)
 *             ┃   ... 展开后 LLM calls / Tool calls ...
 */

const STATUS_BG: Record<string, string> = {
  success: "bg-success-base",
  running: "bg-running-base",
  rework: "bg-rework-base",
  warning: "bg-warning-base",
  error: "bg-error-base",
  neutral: "bg-neutral-base",
};

const STATUS_LABEL: Record<string, string> = {
  success: "已完成",
  running: "运行中",
  rework: "需返工",
  warning: "警告",
  error: "失败",
  neutral: "等待中",
};

const STATUS_TEXT: Record<string, string> = {
  success: "text-success-base",
  running: "text-running-base",
  rework: "text-rework-base",
  warning: "text-warning-base",
  error: "text-error-base",
  neutral: "text-text-muted",
};

/** 概要里残留的英文/内部短语 → 中文（数据源可能仍是英文，展示前兜底清洗） */
function summaryLabel(raw: string): string {
  const text = raw.trim();
  if (/needs_revision/i.test(text)) {
    const m = text.match(/(\d+)\s*issues?/i);
    return m ? `需修订 · ${m[1]} 项问题 · 已触发质检反馈` : "需修订 · 已触发质检反馈";
  }
  if (/^running\b/i.test(text)) return "运行中 · 等待完成";
  if (/^pending\b/i.test(text)) return "等待上游 · 暂未开始";
  if (/\bok$/i.test(text)) return "已完成";
  return text;
}

/** 节点 id 里的内部版本后缀（_v2 / _v3）→ 中文修订标记，避免暴露裸命名 */
function nodeIdLabel(nodeId: string): string {
  const m = nodeId.match(/^(.*)_v(\d+)$/i);
  if (m) return `${m[1]} · 第${m[2]}版`;
  return nodeId;
}

function OpenInDagLink({ nodeId }: { nodeId: string }) {
  const pathname = usePathname();
  const search = useSearchParams();
  const sp = new URLSearchParams(search.toString());
  sp.set("tab", "dag");
  sp.set("node", nodeId);
  return (
    <Link
      href={`${pathname}?${sp.toString()}`}
      className="inline-flex items-center gap-1 rounded-md border border-border-subtle bg-bg-raised px-2 py-1 text-[11px] text-text-secondary transition-colors duration-120 ease-out-quart hover:border-accent-border hover:text-accent-base"
    >
      <ArrowUpRightIcon className="h-3 w-3" />
      <span>在工作流中查看</span>
      <code className="ml-1 font-mono text-[10px] text-text-muted">
        {nodeIdLabel(nodeId)}
      </code>
    </Link>
  );
}

export function TraceRow({
  span,
  defaultOpen = false,
}: {
  span: TraceSpan;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const expandable = span.llmCalls.length > 0 || span.toolCalls.length > 0;

  return (
    <div
      className={cn(
        "border-l-2 transition-colors duration-120 ease-out-quart",
        span.status === "rework" && "border-l-rework-base",
        span.status === "error" && "border-l-error-base",
        span.status === "running" && "border-l-running-base",
        span.status === "success" && "border-l-transparent",
        span.status === "neutral" && "border-l-transparent",
        span.isFeedbackTarget && "border-l-rework-base"
      )}
    >
      {/* Row header */}
      <button
        type="button"
        onClick={() => expandable && setOpen((v) => !v)}
        disabled={!expandable}
        aria-expanded={expandable ? open : undefined}
        aria-label={open ? "折叠调用详情" : "展开调用详情"}
        className={cn(
          "flex w-full items-center gap-3 px-4 py-2.5 text-left",
          expandable && "hover:bg-bg-hover"
        )}
      >
        <span
          className="w-[60px] shrink-0 font-mono text-[11px] text-text-muted tabular-nums"
          data-num
        >
          {span.startedAt}
        </span>

        <span
          className={cn(
            "h-2 w-2 shrink-0 rounded-pill",
            STATUS_BG[span.status] ?? "bg-neutral-base",
            span.status === "running" && "animate-pulse-soft"
          )}
        />

        <span className="font-medium text-text-primary w-[140px] shrink-0 truncate">
          {span.label}
          {span.revision > 1 ? (
            <span className="ml-1.5 inline-flex items-center rounded-pill bg-rework-bg px-1 text-[9px] font-medium text-rework-base">
              第{span.revision}版
            </span>
          ) : null}
        </span>

        <span
          className={cn(
            "w-[110px] shrink-0 text-xs font-medium",
            STATUS_TEXT[span.status] ?? "text-text-muted"
          )}
        >
          {STATUS_LABEL[span.status] ?? span.status}
        </span>

        <span
          className="w-[60px] shrink-0 font-mono text-xs text-text-secondary tabular-nums"
          data-num
        >
          {span.durationMs != null
            ? `${(span.durationMs / 1000).toFixed(1)}s`
            : "—"}
        </span>

        <span
          className="w-[90px] shrink-0 font-mono text-xs text-text-secondary tabular-nums"
          data-num
        >
          {span.tokensIn + span.tokensOut > 0
            ? `${(
                (span.tokensIn + span.tokensOut) / 1000
              ).toFixed(1)}k tok`
            : "—"}
        </span>

        <span className="flex-1 truncate text-xs text-text-muted">
          {summaryLabel(span.summary)}
        </span>

        {expandable ? (
          open ? (
            <ChevronDownIcon className="h-4 w-4 text-text-muted" />
          ) : (
            <ChevronRightIcon className="h-4 w-4 text-text-muted" />
          )
        ) : (
          <span className="w-4" />
        )}
      </button>

      {/* 自评 needs_rework：是 Agent 对自己产出的自评（置信偏低 / 多源字段冲突），
          ≠ 被 QA 打回返工（后者由下方 isFeedbackTarget 的「反馈重跑」表达）。
          故用中性告警色 + 准确文案，避免误读成「质检打回」。 */}
      {span.status === "rework" ? (
        <div className="ml-[88px] flex items-center gap-2 pb-2 text-[11px]">
          <CircleAlertIcon className="h-3 w-3 text-warning-base" />
          <span className="text-warning-base font-medium">自评偏低 · 待复核</span>
        </div>
      ) : null}
      {span.isFeedbackTarget ? (
        <div className="ml-[88px] flex items-center gap-2 pb-2 text-[11px]">
          <span className="rounded-pill bg-rework-bg px-1.5 py-0.5 font-medium text-rework-base">
            反馈重跑
          </span>
          <span className="text-text-muted">
            {span.parentNodeId
              ? `从 ${span.parentNodeId} 的质检反馈重跑`
              : "质检打回待修复"}
          </span>
        </div>
      ) : null}

      {/* Expanded body */}
      {open ? (
        <div className="ml-[88px] mr-4 mb-3 space-y-2">
          <OpenInDagLink nodeId={span.nodeId} />

          {span.selfCritique ? (
            <div className="rounded-md bg-bg-sunken px-3 py-2">
              <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
                自我审视
              </div>
              <p className="mt-1 text-xs leading-relaxed text-text-secondary">
                {span.selfCritique}
              </p>
            </div>
          ) : null}

          {span.toolCalls.length > 0 ? (
            <div className="rounded-md border border-border-subtle bg-bg-raised">
              <div className="border-b border-border-subtle px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-text-muted">
                工具调用 ({span.toolCalls.length})
              </div>
              <ul className="divide-y divide-border-subtle">
                {span.toolCalls.map((t) => (
                  <li
                    key={t.callId}
                    className="grid grid-cols-[120px_1fr_60px] items-center gap-2 px-3 py-1.5 text-[11px]"
                  >
                    <code className="font-mono text-text-primary">
                      {t.toolName}
                    </code>
                    <span className="text-text-secondary truncate">
                      {t.result}
                    </span>
                    <span
                      className="font-mono text-text-muted tabular-nums text-right"
                      data-num
                    >
                      {t.durationMs}ms
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {span.llmCalls.length > 0 ? (
            <div className="space-y-2">
              <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
                模型调用 ({span.llmCalls.length})
              </div>
              {span.llmCalls.map((c, i) => (
                <LLMCallDetail key={c.callId} call={c} index={i} />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
