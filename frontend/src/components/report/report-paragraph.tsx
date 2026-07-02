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
  showV2,
  reviewMode,
  readOnly,
  isFocused,
  onEvidenceClick,
  onFocusEvidence,
  onSaved,
}: {
  paragraph: MockParagraph;
  index: number;
  showV2: boolean;
  /** 审阅模式：显示 QA 提示 / 数据核对标 / 编辑标等审阅信息；关闭时纯净阅读 */
  reviewMode: boolean;
  /** 历史运行只读回放：隐藏 hover ✎ 编辑入口，段落不可修改 */
  readOnly?: boolean;
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
  /* 审阅信息只在审阅模式露出；纯净阅读模式下隐藏（证据芯片始终保留） */
  const showIssue = reviewMode && hasIssue;
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
            保存后段落标记为修订版（用户编辑） · 计入编辑率指标
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
              <span>取消</span>
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={saving}
              className="gap-1.5"
            >
              <CheckIcon className="h-3.5 w-3.5" />
              <span>{saving ? "保存中…" : "保存"}</span>
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
        showIssue && "border-l-2 border-l-rework-base"
      )}
      onMouseEnter={() => {
        if (paragraph.evidenceIds[0]) onFocusEvidence(paragraph.evidenceIds[0]);
      }}
      onMouseLeave={() => onFocusEvidence(null)}
    >
      {showIssue ? <QaIssueLine issue={paragraph.qaIssue!} /> : null}

      {/* hover 露出 ✎ 编辑：绝对定位角标，纯净模式下也不占阅读版面；
          历史运行只读回放不给编辑入口 */}
      {!readOnly ? (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className={cn(
            "absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium",
            "border border-border-subtle bg-bg-raised/90 text-text-muted backdrop-blur-sm",
            "opacity-0 transition-all duration-120 ease-out-quart",
            "group-hover:opacity-100 hover:border-accent-border hover:bg-accent-bg hover:text-accent-base"
          )}
        >
          <PenLineIcon className="h-3 w-3" />
          <span>编辑</span>
        </button>
      ) : null}

      <p
        className={cn(
          "text-base leading-relaxed text-text-primary",
          paragraph.isSoftConclusion && "text-text-secondary italic"
        )}
      >
        {displayText}
        {reviewMode && edited ? <UserEditedBadge /> : null}
      </p>

      {/* 证据链：仅审阅模式显示（纯净模式只读正文，观感优先） */}
      {reviewMode ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {evidences.map((ev) => (
            <EvidenceChip
              key={ev!.id}
              evidence={ev!}
              onClick={() => onEvidenceClick(ev!.id)}
            />
          ))}
          {paragraph.isQuantitative && verifiedNumberCount > 0 ? (
            <span
              className="inline-flex items-center gap-1 rounded-pill border border-success-border bg-success-bg px-2 py-0.5 text-[10px] font-medium text-success-base"
              title="段落中的数字已在 evidence 原文中逐一核对"
            >
              <CheckIcon className="h-2.5 w-2.5" />
              <span>{verifiedNumberCount} 项数据已核对</span>
            </span>
          ) : null}
        </div>
      ) : null}

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
        编辑段落
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
        质检 · {qaDimensionLabel(issue.dimension)}
      </span>
      <span className="text-text-muted shrink-0">·</span>
      <span className="text-text-secondary">{issue.note}</span>
    </div>
  );
}

function UserEditedBadge() {
  return (
    <span className="ml-2 inline-flex items-center rounded-pill border border-accent-border bg-accent-bg px-1.5 py-0.5 align-middle text-[10px] font-medium text-accent-base">
      已编辑 · 修订版
    </span>
  );
}

/** evidence.sourceType → 简短人类可读标签（芯片里给读者看，不暴露机器枚举值） */
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

/** QA 维度枚举 → 中文（不在 UI 暴露 fact_consistency 等裸枚举值） */
const QA_DIMENSION_LABELS: Record<string, string> = {
  fact_consistency: "事实一致性",
  evidence_completeness: "证据完整性",
  schema_completeness: "字段完整性",
  logic_consistency: "逻辑一致性",
  freshness: "时新性",
  expression: "表达质量",
  coverage_density: "覆盖密度",
  identity_consistency: "产品身份一致性",
};

function qaDimensionLabel(dimension: string): string {
  return QA_DIMENSION_LABELS[dimension] ?? dimension;
}

function EvidenceChip({
  evidence,
  onClick,
}: {
  evidence: MockEvidence | undefined;
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
      className="inline-flex items-center gap-1 rounded-pill border border-border-default bg-bg-raised px-2 py-0.5 text-[11px] font-medium text-text-secondary transition-colors duration-120 ease-out-quart hover:border-accent-border hover:text-accent-base"
      title={`点击查看原文 · ${evidence.sourceLabel} · 可信度 ${evidence.authority}`}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-pill",
          toneBase[status] ?? "bg-text-muted"
        )}
      />
      <span>{evidence.product}</span>
      <span className="text-text-muted">· {sourceTypeLabel(evidence.sourceType)}</span>
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
        <span>修订版预览 · 将替换为以下内容…</span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-text-primary">
        {v2.text}
      </p>
      <p className="mt-2 text-[11px] text-text-muted">
        原因 · {v2.reason}
      </p>
      <details className="mt-2 text-[11px]">
        <summary className="cursor-pointer text-text-muted hover:text-text-secondary">
          当前版 → 修订版 对比
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <div className="rounded-sm bg-error-bg/40 p-2">
            <div className="mb-1 font-mono text-error-base">− 当前版</div>
            <p className="text-text-secondary">{current}</p>
          </div>
          <div className="rounded-sm bg-success-bg/40 p-2">
            <div className="mb-1 font-mono text-success-base">+ 修订版</div>
            <p className="text-text-secondary">{v2.text}</p>
          </div>
        </div>
      </details>
    </div>
  );
}
