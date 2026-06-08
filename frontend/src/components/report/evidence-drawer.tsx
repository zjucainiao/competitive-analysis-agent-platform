"use client";

import {
  ExternalLinkIcon,
  AlertTriangleIcon,
  CopyIcon,
  StarIcon,
  CircleCheckIcon,
  ClockIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { type MockEvidence } from "@/lib/report-mock";
import { useEvidenceLookup } from "@/lib/evidence-context";
import { REPORT_ACTIONS } from "@/lib/workspace-actions";

/**
 * Report 右侧 Evidence 抽屉。固定 360px，sticky 跟随 scroll。
 *
 * 状态：
 *  - 默认：显示当前 hover 段落的第一条 evidence（如果有）
 *  - 用户点 chip / 卡片：钉住该 evidence
 *  - 抽屉内动作：⚠ Mark inaccurate / ↗ Open source / 📋 Copy / ⭐ Star
 */
export function EvidenceDrawer({
  focusedId,
  pinnedId,
  disputed,
  onUnpin,
  onToggleDisputed,
}: {
  focusedId: string | null;
  pinnedId: string | null;
  disputed: Set<string>;
  onUnpin: () => void;
  onToggleDisputed: (evidenceId: string) => void;
}) {
  const lookup = useEvidenceLookup();
  const id = pinnedId ?? focusedId;
  const ev = id ? lookup(id) : null;

  return (
    <aside className="sticky top-[136px] self-start">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          证据
        </div>
        {pinnedId ? (
          <button
            type="button"
            onClick={onUnpin}
            className="text-[10px] text-text-muted hover:text-text-secondary"
          >
            取消固定
          </button>
        ) : null}
      </div>

      {ev ? (
        <EvidenceCard
          evidence={ev}
          isPinned={ev.id === pinnedId}
          isDisputed={disputed.has(ev.id)}
          onToggleDisputed={onToggleDisputed}
        />
      ) : (
        <EmptyState />
      )}
    </aside>
  );
}

/** evidence.status 枚举 → 中文（badge 里给读者看，不暴露机器枚举值） */
const STATUS_LABELS: Record<string, string> = {
  verified: "已验证",
  disputed: "有异议",
  stale: "已过期",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** evidence.sourceType → 简短人类可读标签（不暴露机器枚举值） */
const SOURCE_TYPE_LABELS: Record<string, string> = {
  homepage: "官网",
  features: "功能页",
  features_page: "功能页",
  pricing: "定价页",
  pricing_page: "定价页",
  help_docs: "帮助文档",
  docs: "帮助文档",
  changelog: "更新日志",
  customer_cases: "案例",
  cases: "案例",
  blog: "博客",
  user_review: "用户评价",
  user_reviews: "用户评价",
  review: "用户评价",
  reviews: "用户评价",
  app_market: "应用市场",
};

function sourceTypeLabel(sourceType: string): string {
  return SOURCE_TYPE_LABELS[sourceType] ?? "公开来源";
}

function EmptyState() {
  return (
    <div className="rounded-md border border-dashed border-border-default bg-bg-raised/60 p-4 text-xs text-text-muted">
      悬停段落或点击证据标记查看证据原文
    </div>
  );
}

function EvidenceCard({
  evidence,
  isPinned,
  isDisputed,
  onToggleDisputed,
}: {
  evidence: MockEvidence;
  isPinned: boolean;
  isDisputed: boolean;
  onToggleDisputed: (evidenceId: string) => void;
}) {
  const displayStatus = isDisputed ? "disputed" : evidence.status;
  const toneBg: Record<string, string> = {
    verified: "bg-success-bg",
    disputed: "bg-error-bg",
    stale: "bg-warning-bg",
  };
  const toneText: Record<string, string> = {
    verified: "text-success-base",
    disputed: "text-error-base",
    stale: "text-warning-base",
  };
  const toneBorder: Record<string, string> = {
    verified: "border-success-border",
    disputed: "border-error-border",
    stale: "border-warning-border",
  };

  return (
    <article
      className={cn(
        "rounded-md border bg-bg-raised p-4 shadow-popover",
        isPinned ? "border-accent-border" : "border-border-default"
      )}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="space-y-1">
          <code className="font-mono text-xs text-text-primary">
            {evidence.id}
          </code>
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-pill border px-1.5 py-0.5 text-[10px] font-medium",
                toneBg[displayStatus],
                toneBorder[displayStatus],
                toneText[displayStatus]
              )}
            >
              {displayStatus === "verified" ? (
                <CircleCheckIcon className="h-2.5 w-2.5" />
              ) : displayStatus === "stale" ? (
                <ClockIcon className="h-2.5 w-2.5" />
              ) : (
                <AlertTriangleIcon className="h-2.5 w-2.5" />
              )}
              <span>{statusLabel(displayStatus)}</span>
            </span>
            <span
              className="font-mono text-[10px] text-text-muted tabular-nums"
              data-num
            >
              可信度 {evidence.authority}
            </span>
          </div>
        </div>
        <span className="text-[10px] uppercase tracking-wider text-text-muted">
          {evidence.product}
        </span>
      </header>

      {evidence.contextBefore ? (
        <p className="mt-3 text-xs text-text-muted leading-relaxed italic">
          …{evidence.contextBefore}
        </p>
      ) : null}

      <blockquote
        className={cn(
          "mt-2 rounded-sm border-l-2 pl-2.5 text-sm leading-relaxed text-text-primary",
          isDisputed ? "border-l-error-base" : "border-l-accent-base"
        )}
      >
        “{evidence.content}”
      </blockquote>

      {evidence.contextAfter ? (
        <p className="mt-1.5 text-xs text-text-muted leading-relaxed italic">
          {evidence.contextAfter}…
        </p>
      ) : null}

      <div className="mt-3 flex items-center gap-1.5 text-[10px] text-text-muted">
        <ExternalLinkIcon className="h-3 w-3" />
        <a
          href={evidence.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-text-secondary hover:text-text-accent hover:underline"
        >
          {evidence.sourceLabel}
        </a>
        <span>·</span>
        <span>{sourceTypeLabel(evidence.sourceType)}</span>
      </div>
      <div className="mt-1 text-[10px] text-text-muted">
        采集于 {evidence.collectedAt}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-border-subtle pt-3">
        <Button
          type="button"
          size="sm"
          variant={isDisputed ? "outline" : "ghost"}
          onClick={() => onToggleDisputed(evidence.id)}
          className="gap-1.5"
        >
          <AlertTriangleIcon className="h-3 w-3" />
          <span>{isDisputed ? "取消异议" : "标记有异议"}</span>
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => REPORT_ACTIONS.copyEvidence(evidence.id)}
          className="gap-1.5"
        >
          <CopyIcon className="h-3 w-3" />
          <span>复制</span>
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => REPORT_ACTIONS.starEvidence(evidence.id)}
          className="gap-1.5"
        >
          <StarIcon className="h-3 w-3" />
          <span>收藏</span>
        </Button>
      </div>

      <div className="mt-2 text-[10px] text-text-muted">
        想看「引用此证据的所有段落」？切到证据页点该卡片展开
      </div>
    </article>
  );
}
