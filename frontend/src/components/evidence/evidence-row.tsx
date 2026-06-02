"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  AlertTriangleIcon,
  CircleCheckIcon,
  ClockIcon,
  CopyIcon,
  StarIcon,
  ArrowLeftRightIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { REPORT_ACTIONS } from "@/lib/workspace-actions";
import { type MockEvidence } from "@/lib/report-mock";
import { type ParagraphRef } from "@/lib/evidence-index";

const STATUS_META: Record<
  string,
  { bg: string; border: string; text: string; icon: typeof CircleCheckIcon }
> = {
  verified: {
    bg: "bg-success-bg",
    border: "border-success-border",
    text: "text-success-base",
    icon: CircleCheckIcon,
  },
  disputed: {
    bg: "bg-error-bg",
    border: "border-error-border",
    text: "text-error-base",
    icon: AlertTriangleIcon,
  },
  stale: {
    bg: "bg-warning-bg",
    border: "border-warning-border",
    text: "text-warning-base",
    icon: ClockIcon,
  },
};

export function EvidenceRow({
  evidence,
  refs,
  selected,
  expanded,
  isDisputedOverride,
  onToggleSelect,
  onToggleExpand,
  onToggleDisputed,
}: {
  evidence: MockEvidence;
  refs: ParagraphRef[];
  selected: boolean;
  expanded: boolean;
  isDisputedOverride: boolean;
  onToggleSelect: () => void;
  onToggleExpand: () => void;
  onToggleDisputed: () => void;
}) {
  /* user-overridden disputed wins over fixture status */
  const displayStatus = isDisputedOverride ? "disputed" : evidence.status;
  const meta = STATUS_META[displayStatus] ?? STATUS_META.verified;
  const StatusIcon = meta.icon;

  return (
    <div
      className={cn(
        "rounded-md border bg-bg-raised transition-colors duration-120 ease-out-quart",
        selected
          ? "border-accent-border ring-1 ring-accent-base/15"
          : "border-border-subtle hover:border-border-default"
      )}
    >
      {/* row header */}
      <div className="flex items-start gap-3 px-3 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          aria-label={`select ${evidence.id}`}
          className="mt-1 h-3.5 w-3.5 shrink-0 rounded-sm border-border-default accent-accent-base"
        />

        <button
          type="button"
          onClick={onToggleExpand}
          className="flex-1 text-left"
        >
          <div className="flex flex-wrap items-center gap-2">
            <code className="font-mono text-xs font-medium text-text-primary">
              {evidence.id}
            </code>
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-pill border px-1.5 py-0.5 text-[10px] font-medium",
                meta.bg,
                meta.border,
                meta.text
              )}
            >
              <StatusIcon className="h-2.5 w-2.5" />
              <span>{displayStatus}</span>
            </span>
            <span className="text-[11px] text-text-muted">{evidence.product}</span>
            <span className="text-[11px] text-text-muted">·</span>
            <span className="font-mono text-[11px] text-text-secondary">
              {evidence.sourceType}
            </span>
            <span className="text-[11px] text-text-muted">·</span>
            <span
              className="font-mono text-[11px] text-text-muted tabular-nums"
              data-num
            >
              authority {evidence.authority}
            </span>
            <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-text-muted">
              <ArrowLeftRightIcon className="h-3 w-3" />
              <span className="font-mono tabular-nums" data-num>
                {refs.length}
              </span>
              <span>ref{refs.length === 1 ? "" : "s"}</span>
              {expanded ? (
                <ChevronDownIcon className="h-3.5 w-3.5" />
              ) : (
                <ChevronRightIcon className="h-3.5 w-3.5" />
              )}
            </span>
          </div>
          <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-text-secondary">
            “{evidence.content}”
          </p>
        </button>
      </div>

      {/* expanded body */}
      {expanded ? (
        <div className="border-t border-border-subtle p-4 space-y-4">
          {evidence.contextBefore ? (
            <p className="text-xs italic text-text-muted leading-relaxed">
              …{evidence.contextBefore}
            </p>
          ) : null}
          <blockquote
            className={cn(
              "rounded-sm border-l-2 pl-3 text-sm leading-relaxed text-text-primary",
              displayStatus === "disputed"
                ? "border-l-error-base"
                : "border-l-accent-base"
            )}
          >
            “{evidence.content}”
          </blockquote>
          {evidence.contextAfter ? (
            <p className="text-xs italic text-text-muted leading-relaxed">
              {evidence.contextAfter}…
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-muted">
            <span className="inline-flex items-center gap-1">
              <ExternalLinkIcon className="h-3 w-3" />
              <a
                href={evidence.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-text-secondary hover:text-text-accent hover:underline"
              >
                {evidence.sourceLabel}
              </a>
            </span>
            <span>·</span>
            <span>collected {evidence.collectedAt}</span>
            <span>·</span>
            <span className="font-mono">lang={evidence.language}</span>
            {evidence.tags.length > 0 ? (
              <span className="ml-2 inline-flex items-center gap-1">
                {evidence.tags.map((t) => (
                  <span
                    key={t}
                    className="rounded-sm bg-bg-sunken px-1 font-mono text-[10px]"
                  >
                    {t}
                  </span>
                ))}
              </span>
            ) : null}
          </div>

          {/* reverse lookup */}
          <section>
            <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
              Referenced by ({refs.length} paragraph{refs.length === 1 ? "" : "s"})
            </div>
            {refs.length === 0 ? (
              <p className="text-xs text-text-muted">
                No paragraph references this evidence yet.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {refs.map((r) => (
                  <li key={r.paragraphId}>
                    <Link
                      href={`/projects/demo/runs/01?tab=report#para-${r.paragraphId}`}
                      className="group flex items-start gap-2 rounded-sm bg-bg-sunken px-2.5 py-1.5 hover:bg-accent-bg/50"
                    >
                      <span
                        className="font-mono text-[11px] text-accent-base tabular-nums shrink-0"
                        data-num
                      >
                        §{r.sectionNumber}
                      </span>
                      <span className="text-[11px] text-text-secondary shrink-0">
                        {r.sectionTitle}
                      </span>
                      <span className="text-[11px] text-text-muted truncate">
                        — {r.textPreview}
                      </span>
                      <span className="ml-auto shrink-0 text-[10px] text-text-muted opacity-0 transition-opacity duration-120 ease-out-quart group-hover:opacity-100">
                        open ↗
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* actions */}
          <div className="flex flex-wrap items-center gap-1.5 border-t border-border-subtle pt-3">
            <Button
              type="button"
              size="sm"
              variant={isDisputedOverride ? "outline" : "ghost"}
              onClick={onToggleDisputed}
              className="gap-1.5"
            >
              <AlertTriangleIcon className="h-3 w-3" />
              <span>
                {isDisputedOverride ? "Undispute" : "Mark inaccurate"}
              </span>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => REPORT_ACTIONS.copyEvidence(evidence.id)}
              className="gap-1.5"
            >
              <CopyIcon className="h-3 w-3" />
              <span>Copy</span>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => REPORT_ACTIONS.starEvidence(evidence.id)}
              className="gap-1.5"
            >
              <StarIcon className="h-3 w-3" />
              <span>Star</span>
            </Button>
            <span className="ml-auto text-[10px] text-text-muted">
              {evidence.id}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
