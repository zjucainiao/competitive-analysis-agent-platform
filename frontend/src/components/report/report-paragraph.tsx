"use client";

import { useEffect, useRef, useState } from "react";
import {
  PenLineIcon,
  AlertTriangleIcon,
  CheckIcon,
  XIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  REPORT_ACTIONS,
} from "@/lib/workspace-actions";
import {
  getQuantityNumbers,
  type MockEvidence,
  type MockParagraph,
} from "@/lib/report-mock";
import { useEvidenceLookup } from "@/lib/evidence-context";

/**
 * 单个段落：核心人工介入面。
 *
 * 视觉/交互（必看 DESIGN.md 6.1 + PRODUCT.md § Strategic Principles 6）：
 *  - hover：底色微微变暖（bg-accent-bg/40） + 右上 ✎ 编辑 chip 露出
 *  - hover：evidence chips 跟随高亮
 *  - 点击 ✎ → 进入编辑模式（inline Textarea + Save / Cancel）
 *  - is_quantitative：额外 # N verified 角标
 *  - has qaIssue：左侧橙色竖条 + 顶部 issue 行
 *  - has pendingV2（v2 已规划）：底部「v2 预览」可展开 diff
 */
export function ReportParagraph({
  paragraph,
  index,
  showV2,
  isFocused,
  onEvidenceClick,
  onFocusEvidence,
  onSaved,
}: {
  paragraph: MockParagraph;
  index: number;
  showV2: boolean;
  isFocused: boolean;
  onEvidenceClick: (evidenceId: string) => void;
  onFocusEvidence: (evidenceId: string | null) => void;
  /** 父组件控制：返回 Promise 时 ReportParagraph 在 await 期间显示 saving 态；
   *  抛错时 rollback。Mock 模式可以返回 void */
  onSaved: (paragraphId: string, newText: string) => void | Promise<void>;
}) {
  const lookupEvidence = useEvidenceLookup();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(paragraph.text);
  const [edited, setEdited] = useState(false);
  const [saving, setSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const displayText = edited ? draft : paragraph.text;
  const hasIssue = !!paragraph.qaIssue;
  const evidences = paragraph.evidenceIds
    .map((id) => lookupEvidence(id))
    .filter((e) => e != null);
  const verifiedNumberCount = paragraph.isQuantitative
    ? getQuantityNumbers(displayText).length
    : 0;
  const v2 = paragraph.pendingV2;
  const showingV2Preview = showV2 && v2;

  useEffect(() => {
    if (editing) {
      requestAnimationFrame(() => {
        textareaRef.current?.focus();
        textareaRef.current?.setSelectionRange(draft.length, draft.length);
      });
    }
  }, [editing, draft.length]);

  const handleSave = async () => {
    if (draft.trim() === paragraph.text.trim()) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      const result = onSaved(paragraph.id, draft);
      if (result instanceof Promise) await result;
      setEdited(true);
      setEditing(false);
      REPORT_ACTIONS.saveEdit(paragraph.id);
    } catch {
      /* parent 已 toast；保留编辑态让用户重试 */
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setDraft(edited ? draft : paragraph.text);
    setEditing(false);
    REPORT_ACTIONS.cancelEdit();
  };

  /* ── editing mode ─────────────────────────────────────────────────── */
  if (editing) {
    return (
      <div
        id={`para-${paragraph.id}`}
        className={cn(
          "group relative -mx-3 my-3 rounded-md border border-accent-border bg-accent-bg/40 px-3 py-3",
          hasIssue && "border-l-2 border-l-rework-base"
        )}
      >
        <EditingHeader id={paragraph.id} />
        <Textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="min-h-[120px] resize-y border-border-default bg-bg-raised text-base leading-relaxed"
        />
        <div className="mt-3 flex items-center justify-between">
          <span className="text-[11px] text-text-muted">
            保存后段落标记为 v2（用户编辑） · 计入 metrics.edit_rate
          </span>
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={handleCancel}
              disabled={saving}
              className="gap-1.5"
            >
              <XIcon className="h-3.5 w-3.5" />
              <span>Cancel</span>
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={saving}
              className="gap-1.5"
            >
              <CheckIcon className="h-3.5 w-3.5" />
              <span>{saving ? "Saving…" : "Save edit"}</span>
            </Button>
          </div>
        </div>
      </div>
    );
  }

  /* ── read mode ────────────────────────────────────────────────────── */
  return (
    <div
      id={`para-${paragraph.id}`}
      className={cn(
        "group relative -mx-3 my-1 rounded-md px-3 py-2",
        "transition-colors duration-120 ease-out-quart",
        "hover:bg-accent-bg/30",
        isFocused && "bg-accent-bg/50 ring-1 ring-accent-border",
        hasIssue && "border-l-2 border-l-rework-base"
      )}
      onMouseEnter={() => {
        if (paragraph.evidenceIds[0]) onFocusEvidence(paragraph.evidenceIds[0]);
      }}
      onMouseLeave={() => onFocusEvidence(null)}
    >
      {hasIssue ? <QaIssueLine issue={paragraph.qaIssue!} /> : null}

      <p
        className={cn(
          "text-base leading-relaxed text-text-primary",
          paragraph.isSoftConclusion && "text-text-secondary italic"
        )}
      >
        <span className="mr-2 select-none font-mono text-[11px] text-text-muted tabular-nums">
          {String(index + 1).padStart(2, "0")}
        </span>
        {displayText}
        {edited ? <UserEditedBadge /> : null}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {evidences.map((ev, i) => (
          <EvidenceChip
            key={ev!.id}
            evidence={ev!}
            index={i + 1}
            onClick={() => onEvidenceClick(ev!.id)}
          />
        ))}
        {paragraph.isQuantitative && verifiedNumberCount > 0 ? (
          <span
            className="inline-flex items-center gap-1 rounded-pill border border-success-border bg-success-bg px-1.5 py-0.5 text-[10px] font-medium text-success-base"
            title="数字校验通过：可在 evidence 中找到字面"
          >
            <span className="font-mono">#{verifiedNumberCount}</span>
            <span>verified</span>
          </span>
        ) : null}
        {paragraph.isSoftConclusion ? (
          <span className="inline-flex items-center rounded-pill border border-border-default bg-bg-sunken px-1.5 py-0.5 text-[10px] text-text-muted">
            soft conclusion
          </span>
        ) : null}

        {/* hover 露出 ✎ 编辑 */}
        <button
          type="button"
          onClick={() => setEditing(true)}
          className={cn(
            "ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium",
            "border border-transparent text-text-muted",
            "opacity-0 transition-all duration-120 ease-out-quart",
            "group-hover:opacity-100 hover:border-accent-border hover:bg-accent-bg hover:text-accent-base"
          )}
        >
          <PenLineIcon className="h-3 w-3" />
          <span>Edit</span>
        </button>
      </div>

      {showingV2Preview ? <V2Preview v2={v2!} current={displayText} /> : null}
    </div>
  );
}

/* ── pieces ────────────────────────────────────────────────────────────── */

function EditingHeader({ id }: { id: string }) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <PenLineIcon className="h-3.5 w-3.5 text-accent-base" />
      <span className="text-xs font-medium text-text-primary">
        Editing paragraph
      </span>
      <code className="font-mono text-[10px] text-text-muted">{id}</code>
    </div>
  );
}

function QaIssueLine({
  issue,
}: {
  issue: NonNullable<MockParagraph["qaIssue"]>;
}) {
  const severityClass =
    issue.severity === "critical"
      ? "text-error-base"
      : issue.severity === "major"
        ? "text-rework-base"
        : "text-warning-base";

  return (
    <div className="mb-1.5 flex items-start gap-1.5 text-[11px]">
      <AlertTriangleIcon
        className={cn("h-3 w-3 mt-0.5 shrink-0", severityClass)}
      />
      <span className={cn("font-medium", severityClass)}>
        QA · {issue.dimension}
      </span>
      <span className="text-text-muted shrink-0">·</span>
      <span className="text-text-secondary">{issue.note}</span>
    </div>
  );
}

function UserEditedBadge() {
  return (
    <span className="ml-2 inline-flex items-center rounded-pill border border-accent-border bg-accent-bg px-1.5 py-0.5 align-middle text-[10px] font-medium text-accent-base">
      user-edited · v2
    </span>
  );
}

function EvidenceChip({
  evidence,
  index,
  onClick,
}: {
  evidence: MockEvidence | undefined;
  index: number;
  onClick: () => void;
}) {
  if (!evidence) return null;
  const status = evidence.status;
  const toneBase: Record<string, string> = {
    verified: "bg-success-base",
    disputed: "bg-error-base",
    stale: "bg-warning-base",
  };
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className="inline-flex items-center gap-1 rounded-pill border border-border-default bg-bg-raised px-1.5 py-0.5 text-[10px] font-medium text-text-secondary transition-colors duration-120 ease-out-quart hover:border-accent-border hover:text-accent-base"
      title={`${evidence.id} · ${evidence.sourceLabel} · authority ${evidence.authority}`}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-pill",
          toneBase[status] ?? "bg-text-muted"
        )}
      />
      <span className="font-mono">{evidence.id.slice(3, 11)}</span>
      <span className="text-text-muted">#{index}</span>
    </button>
  );
}

function V2Preview({
  v2,
  current,
}: {
  v2: NonNullable<MockParagraph["pendingV2"]>;
  current: string;
}) {
  return (
    <div className="mt-3 rounded-md border border-running-border bg-running-bg/40 p-3">
      <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-running-base">
        <span className="block h-1.5 w-1.5 rounded-pill bg-running-base animate-pulse-soft" />
        <span>v2 preview · reporter_v2 will replace this with…</span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-text-primary">
        {v2.text}
      </p>
      <p className="mt-2 text-[11px] text-text-muted">
        reason · {v2.reason}
      </p>
      <details className="mt-2 text-[11px]">
        <summary className="cursor-pointer text-text-muted hover:text-text-secondary">
          v1 → v2 diff
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <div className="rounded-sm bg-error-bg/40 p-2">
            <div className="mb-1 font-mono text-error-base">- v1</div>
            <p className="text-text-secondary">{current}</p>
          </div>
          <div className="rounded-sm bg-success-bg/40 p-2">
            <div className="mb-1 font-mono text-success-base">+ v2</div>
            <p className="text-text-secondary">{v2.text}</p>
          </div>
        </div>
      </details>
    </div>
  );
}
