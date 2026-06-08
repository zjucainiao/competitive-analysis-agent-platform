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
import { DIFF_PAIRS, type DiffPair } from "@/lib/trace-mock";
import { emitIntervention } from "@/lib/workspace-actions";
import { cn } from "@/lib/utils";

/**
 * 展示前清洗：把标题/说明里的内部命名（reporter_v2 / Reporter / _v2 / (v1)）
 * 替换成读者向中文，避免在「决策回放」面板泄漏内部术语。
 */
function sanitizeText(text: string): string {
  return text
    .replace(/reporter[\s_]*v(\d+)/gi, "报告（修订版）")
    .replace(/reporter\s*\(v1\)/gi, "报告（当前版）")
    .replace(/\bqa[\s_]*v(\d+)/gi, "质检（修订版）")
    .replace(/\bReporter\b/g, "报告")
    .replace(/\bAnalyst\b/g, "分析")
    .replace(/\(v1\)/g, "（当前版）")
    .replace(/_v(\d+)\b/g, "（修订版）");
}

/**
 * v1 ↔ v2 prompt diff 抽屉。
 *
 * 评分项「Agent 决策回放 · QA 反馈闭环可视化」的杀手锏：
 *   并排 monospace 显示 v1 system prompt vs v2 system prompt，
 *   diff 行用绿/红/灰按 prefix 着色，让评委一眼看到 QA FEEDBACK 是怎么注入的。
 *
 * ``pair`` 缺省时回退到 mock DIFF_PAIRS[0]；API 模式下由 TraceLayout 用真实
 * 返工节点的 prompt_preview（含 prepend 到 system 顶部的 QA FEEDBACK）构造，
 * 让「决策回放」在真实运行里也成立，而非只在 demo。
 */
export function DiffSheet({
  open,
  onOpenChange,
  pair: pairProp,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pair?: DiffPair | null;
}) {
  // null = API 模式但本次运行还没有返工轮次（区别于 demo 的 undefined → mock）
  if (pairProp === null) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          className="!w-[min(960px,95vw)] !max-w-none gap-0 overflow-y-auto p-0"
        >
          <SheetHeader className="gap-2 border-b border-border-subtle p-5">
            <SheetTitle className="text-base font-semibold">
              当前版 ↔ 修订版 提示词对比
            </SheetTitle>
            <SheetDescription className="text-xs text-text-secondary">
              本次运行暂无返工轮次
            </SheetDescription>
          </SheetHeader>
          <div className="p-10 text-center text-sm text-text-muted">
            本次运行暂无返工轮次。
            <br />
            若质检触发修复，将在此显示修订前后的提示词对比。
          </div>
        </SheetContent>
      </Sheet>
    );
  }

  const pair = pairProp ?? DIFF_PAIRS[0];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="!w-[min(960px,95vw)] !max-w-none gap-0 overflow-y-auto p-0"
      >
        <SheetHeader className="gap-2 border-b border-border-subtle p-5">
          <SheetTitle className="text-base font-semibold">
            当前版 ↔ 修订版 提示词对比
          </SheetTitle>
          <SheetDescription className="text-xs text-text-secondary">
            {sanitizeText(pair.description)}
          </SheetDescription>
        </SheetHeader>

        <div className="p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm font-medium text-text-primary">
              {sanitizeText(pair.label)}
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  if (typeof navigator !== "undefined" && navigator.clipboard) {
                    navigator.clipboard.writeText(pair.rightContent);
                  }
                  toast.success("修订版提示词已复制");
                  emitIntervention("copy-diff", pair.id);
                }}
                className="gap-1.5"
              >
                <CopyIcon className="h-3 w-3" />
                复制修订版
              </Button>
            </div>
          </div>

          <DiffGrid
            left={pair.leftContent}
            right={pair.rightContent}
            leftTitle={pair.leftLabel}
            rightTitle={pair.rightLabel}
          />

          <p className="mt-4 text-[11px] text-text-muted leading-relaxed">
            高亮行：<span className="text-success-base font-medium">绿 +</span>{" "}
            修订版新增（重写时根据质检反馈调整）·{" "}
            <span className="text-error-base font-medium">红 −</span>{" "}
            修订版删除 · 灰色为未变行。
          </p>
        </div>
      </SheetContent>
    </Sheet>
  );
}

/** agent / 节点 slug → 中文环节名（与 Trace 过滤器 / DAG 一致，不暴露裸枚举） */
const AGENT_LABELS: Record<string, string> = {
  collector: "信息采集",
  extractor: "证据入库",
  analyst: "结构化分析",
  reporter: "报告撰写",
  qa: "质量审查",
};

/** 列标题里出现的内部版本标记 → 中文版本词（不暴露 reporter_v2 / (v1) 等裸名）。
 *  把 `_v2` / `(v2)` / `· v2` 这类后缀映射为「修订版」，`v1` 映射为「当前版」，
 *  剩余 base 若是 agent slug 再映射为中文环节名，否则原样透传。 */
function versionTitle(raw: string): string {
  let s = raw;
  s = s.replace(/_v\d+\b/gi, "");
  s = s.replace(/\s*[·•]?\s*\(?\bv\d+\)?/gi, "");
  s = s.trim().replace(/[·•]\s*$/, "").trim();
  const base = AGENT_LABELS[s.toLowerCase()] ?? s;
  const suffix = /_v[2-9]\d*\b|\bv[2-9]\d*\b/i.test(raw)
    ? " · 修订版"
    : /\bv1\b/i.test(raw)
      ? " · 当前版"
      : "";
  return (base || raw) + suffix;
}

function DiffGrid({
  left,
  right,
  leftTitle = "当前版",
  rightTitle = "修订版",
}: {
  left: string;
  right: string;
  leftTitle?: string;
  rightTitle?: string;
}) {
  const leftLines = left.split("\n");
  const rightLines = right.split("\n");
  const leftSet = new Set(leftLines);

  return (
    <div className="grid grid-cols-2 gap-3">
      <DiffColumn
        title={versionTitle(leftTitle)}
        lines={leftLines.map((line) => ({ line, kind: "ctx" as const }))}
      />
      <DiffColumn
        title={versionTitle(rightTitle)}
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
