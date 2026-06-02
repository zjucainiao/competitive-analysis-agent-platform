"use client";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { CopyIcon } from "lucide-react";
import { DIFF_PAIRS } from "@/lib/trace-mock";
import { emitIntervention } from "@/lib/workspace-actions";
import { cn } from "@/lib/utils";

/**
 * v1 ↔ v2 prompt diff 抽屉。
 *
 * 评分项「Agent 决策回放 · QA 反馈闭环可视化」的杀手锏：
 *   并排 monospace 显示 reporter_v1 system prompt vs reporter_v2 system prompt，
 *   diff 行用绿/红/灰按 prefix 着色，让评委一眼看到 QA FEEDBACK 是怎么注入的。
 */
export function DiffSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const pair = DIFF_PAIRS[0];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="!w-[min(960px,95vw)] !max-w-none gap-0 overflow-y-auto p-0"
      >
        <SheetHeader className="gap-2 border-b border-border-subtle p-5">
          <SheetTitle className="text-base font-semibold">
            v1 ↔ v2 prompt diff
          </SheetTitle>
          <SheetDescription className="text-xs text-text-secondary">
            {pair.description}
          </SheetDescription>
        </SheetHeader>

        <div className="p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm font-medium text-text-primary">
              {pair.label}
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  if (typeof navigator !== "undefined" && navigator.clipboard) {
                    navigator.clipboard.writeText(pair.rightContent);
                  }
                  toast.success("v2 prompt 已复制");
                  emitIntervention("copy-diff", pair.id);
                }}
                className="gap-1.5"
              >
                <CopyIcon className="h-3 w-3" />
                Copy v2
              </Button>
            </div>
          </div>

          <DiffGrid left={pair.leftContent} right={pair.rightContent} />

          <p className="mt-4 text-[11px] text-text-muted leading-relaxed">
            高亮行：<span className="text-success-base font-medium">绿 +</span> v2
            新增（QA FEEDBACK 章节）·{" "}
            <span className="text-error-base font-medium">红 −</span> v2
            删除（本次为零） · 灰色为未变行。
            v2 的额外内容直接来自 QA verdict_id <code>qa_revise_001</code>，
            由 Orchestrator FeedbackRouter 注入。
          </p>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function DiffGrid({ left, right }: { left: string; right: string }) {
  const leftLines = left.split("\n");
  const rightLines = right.split("\n");
  const leftSet = new Set(leftLines);

  return (
    <div className="grid grid-cols-2 gap-3">
      <DiffColumn
        title="reporter · v1"
        lines={leftLines.map((line) => ({ line, kind: "ctx" as const }))}
      />
      <DiffColumn
        title="reporter_v2"
        lines={rightLines.map((line) => ({
          line,
          kind: leftSet.has(line)
            ? ("ctx" as const)
            : ("add" as const),
        }))}
      />
    </div>
  );
}

function DiffColumn({
  title,
  lines,
}: {
  title: string;
  lines: Array<{ line: string; kind: "ctx" | "add" | "del" }>;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border-subtle bg-bg-sunken">
      <div className="border-b border-border-subtle bg-bg-raised px-3 py-1.5">
        <code className="font-mono text-xs text-text-primary">{title}</code>
      </div>
      <pre className="overflow-x-auto whitespace-pre font-mono text-[11px] leading-relaxed">
        {lines.map((row, i) => (
          <div
            key={i}
            className={cn(
              "flex gap-2 px-3 py-px",
              row.kind === "add" && "bg-success-bg",
              row.kind === "del" && "bg-error-bg"
            )}
          >
            <span
              className={cn(
                "w-3 shrink-0 select-none text-right",
                row.kind === "add" && "text-success-base font-medium",
                row.kind === "del" && "text-error-base font-medium",
                row.kind === "ctx" && "text-text-muted"
              )}
              data-num
            >
              {row.kind === "add" ? "+" : row.kind === "del" ? "−" : " "}
            </span>
            <span
              className={cn(
                "flex-1 whitespace-pre-wrap break-words",
                row.kind === "add" && "text-success-base",
                row.kind === "del" && "text-error-base",
                row.kind === "ctx" && "text-text-secondary"
              )}
            >
              {row.line || " "}
            </span>
          </div>
        ))}
      </pre>
    </div>
  );
}
