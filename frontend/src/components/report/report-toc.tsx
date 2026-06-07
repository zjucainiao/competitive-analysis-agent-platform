"use client";

import { cn } from "@/lib/utils";
import type { MockSection } from "@/lib/report-mock";

/**
 * Report 左侧 TOC（目录）。固定 180px。
 *  - 当前 section 朱漆橙强调
 *  - 含 qaIssue 的 section 显示 ⚠ 提示点
 *  - 用 anchor 滚动到对应区块
 */
export function ReportToc({
  sections,
  activeSectionId,
  onSectionClick,
}: {
  sections: MockSection[];
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
}) {
  return (
    <aside className="sticky top-[136px] self-start">
      <div className="mb-3 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        章节
      </div>
      <nav className="flex flex-col gap-0.5" aria-label="report table of contents">
        {sections.map((s) => {
          const hasIssue = s.paragraphs.some((p) => p.qaIssue);
          const active = s.id === activeSectionId;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onSectionClick(s.id)}
              className={cn(
                "group flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                "transition-colors duration-120 ease-out-quart",
                active
                  ? "bg-accent-bg text-text-accent font-medium"
                  : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
              )}
            >
              <span
                className={cn(
                  "font-mono text-[11px] tabular-nums shrink-0 w-4",
                  active ? "text-accent-base" : "text-text-muted"
                )}
                data-num
              >
                {s.number}
              </span>
              <span className="flex-1 truncate">{s.title}</span>
              {hasIssue ? (
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-pill bg-rework-base"
                  aria-label="contains QA issue"
                />
              ) : null}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
