"use client";

import { useState } from "react";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  DownloadIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { emitIntervention } from "@/lib/workspace-actions";
import type { FullLLMCall } from "@/lib/trace-mock";

/**
 * 单次 LLM call 的展开卡片。
 * 行内组件，不弹层。提供：
 *  - System prompt（可折叠预览 / 全文）
 *  - Messages (user/assistant)
 *  - Response JSON
 *  - 工程级动作：Copy prompt / Export as fixture / Replay with edits
 */
export function LLMCallDetail({
  call,
  index,
}: {
  call: FullLLMCall;
  index: number;
}) {
  const [sysExpanded, setSysExpanded] = useState(false);

  return (
    <div className="rounded-md border border-border-subtle bg-bg-raised">
      {/* header bar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-border-subtle px-3 py-2">
        <span
          className="font-mono text-[11px] text-text-muted shrink-0 w-6 tabular-nums"
          data-num
        >
          #{index + 1}
        </span>
        <code className="font-mono text-xs text-text-primary">{call.model}</code>
        <span className="text-text-muted">·</span>
        <span
          className="font-mono text-xs text-text-secondary tabular-nums"
          data-num
        >
          {(call.durationMs / 1000).toFixed(2)}s
        </span>
        <span
          className="font-mono text-xs text-text-secondary tabular-nums"
          data-num
        >
          {call.tokensIn.toLocaleString()} / {call.tokensOut.toLocaleString()}
        </span>
        <span className="font-mono text-xs text-text-secondary">
          ${call.costUsd.toFixed(4)}
        </span>
        <span
          className={cn(
            "ml-auto rounded-pill px-1.5 py-0.5 text-[10px] font-mono",
            call.finishReason === "stop"
              ? "bg-success-bg text-success-base"
              : call.finishReason === "length"
                ? "bg-warning-bg text-warning-base"
                : "bg-neutral-bg text-neutral-base"
          )}
        >
          {call.finishReason}
        </span>
        <span className="text-[10px] font-mono text-text-muted">
          t={call.temperature}
        </span>
      </div>

      <div className="space-y-3 px-3 py-3">
        {/* System prompt */}
        <Block label="system prompt" mono>
          <button
            type="button"
            onClick={() => setSysExpanded((v) => !v)}
            className="inline-flex items-center gap-1 text-[10px] text-text-muted hover:text-text-secondary"
          >
            {sysExpanded ? (
              <ChevronDownIcon className="h-3 w-3" />
            ) : (
              <ChevronRightIcon className="h-3 w-3" />
            )}
            <span>{sysExpanded ? "collapse" : "show full"} · {call.systemPrompt.split("\n").length} lines</span>
          </button>
          <pre
            className={cn(
              "mt-1 rounded-sm bg-bg-sunken px-2.5 py-2 font-mono text-[11px] leading-relaxed text-text-primary",
              "whitespace-pre-wrap break-words",
              !sysExpanded && "max-h-32 overflow-hidden"
            )}
          >
            {call.systemPrompt}
          </pre>
        </Block>

        {/* Messages */}
        <Block label={`messages (${call.messages.length})`} mono>
          <div className="space-y-1.5">
            {call.messages.map((m, i) => (
              <div
                key={i}
                className={cn(
                  "rounded-sm border-l-2 bg-bg-sunken/60 px-2.5 py-1.5",
                  m.role === "user"
                    ? "border-l-running-base"
                    : "border-l-accent-base"
                )}
              >
                <div className="mb-1 flex items-center gap-1.5">
                  <span
                    className={cn(
                      "font-mono text-[10px] uppercase tracking-wider",
                      m.role === "user"
                        ? "text-running-base"
                        : "text-accent-base"
                    )}
                  >
                    {m.role}
                  </span>
                </div>
                <pre className="font-mono text-[11px] leading-relaxed text-text-secondary whitespace-pre-wrap break-words">
                  {m.content}
                </pre>
              </div>
            ))}
          </div>
        </Block>

        {/* Response */}
        <Block label="response" mono>
          <pre className="rounded-sm bg-bg-sunken px-2.5 py-2 font-mono text-[11px] text-text-secondary whitespace-pre-wrap break-words">
            {call.responseJson}
          </pre>
        </Block>

        {/* Actions */}
        <div className="flex flex-wrap items-center gap-1.5 border-t border-border-subtle pt-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              if (typeof navigator !== "undefined" && navigator.clipboard) {
                navigator.clipboard.writeText(call.systemPrompt);
              }
              toast.success("Prompt 已复制", {
                description: `${call.callId} · ${call.systemPrompt.split("\n").length} lines`,
              });
              emitIntervention("copy-prompt", call.callId);
            }}
            className="gap-1.5"
          >
            <CopyIcon className="h-3 w-3" />
            <span>Copy prompt</span>
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              toast.success("Fixture 已导出", {
                description: `tests/fixtures/${call.callId}.json · 可在单测中 replay`,
              });
              emitIntervention("export-fixture", call.callId);
            }}
            className="gap-1.5"
          >
            <DownloadIcon className="h-3 w-3" />
            <span>Export as fixture</span>
          </Button>
          <span className="ml-auto text-[10px] text-text-muted">
            {call.callId}
          </span>
        </div>
      </div>
    </div>
  );
}

function Block({
  label,
  children,
  mono,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <section>
      <div
        className={cn(
          "mb-1 text-[10px] uppercase tracking-wider text-text-muted",
          mono && "font-mono"
        )}
      >
        {label}
      </div>
      {children}
    </section>
  );
}
