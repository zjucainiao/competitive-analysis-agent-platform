"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DownloadIcon, CopyIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  MOCK_REPORT,
  type MockReport,
  type MockSection,
  type MockParagraph,
} from "@/lib/report-mock";
import { ReportToc } from "./report-toc";
import { ReportSection } from "./report-section";
import { EvidenceDrawer } from "./evidence-drawer";
import { EditHistoryToggle } from "./edit-history-toggle";
import { REPORT_ACTIONS, emitIntervention } from "@/lib/workspace-actions";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import { renderReportAsMarkdown, downloadMarkdown } from "@/lib/report-export";
import {
  EvidenceLookupProvider,
} from "@/lib/evidence-context";
import { patchParagraph } from "@/lib/api/client";
import { revalidate } from "@/lib/api/hooks";
import type {
  Evidence as ApiEvidence,
  QAVerdict as ApiQAVerdict,
  ReporterOutput,
  ReportDraft as ApiReportDraft,
  ReportSection as ApiReportSection,
  ReportParagraph as ApiReportParagraph,
} from "@/lib/api/types";

interface ReportLayoutProps {
  /** 真实 API 模式：传入最新 reporter 节点 output */
  apiReporter?: { nodeId: string; output: ReporterOutput } | null;
  apiEvidences?: ApiEvidence[];
  apiProjectId?: string;
  apiVerdicts?: ApiQAVerdict[];
}

/**
 * Report tab。
 *
 *  - 双模式：apiReporter 提供 → 真实 ReportDraft；否则用 MOCK_REPORT
 *  - 段落保存：API 模式调 PATCH /api/projects/{id}/reports/{nodeId}/paragraphs/{pid}
 *    + 自动 revalidate state（manual_edits / edit_rate 由后端更新）
 *  - Evidence 查找：EvidenceLookupProvider 注入 ApiEvidence map，子组件透明使用
 */
export function ReportLayout(props: ReportLayoutProps = {}) {
  const { apiReporter, apiEvidences, apiProjectId, apiVerdicts } = props;
  const isApi = !!apiReporter;

  /* report data: API 或 mock */
  const report: MockReport = useMemo(() => {
    if (isApi && apiReporter) {
      return apiDraftToMockReport(apiReporter.output.draft, apiVerdicts ?? []);
    }
    return MOCK_REPORT;
  }, [isApi, apiReporter, apiVerdicts]);

  const api = useWorkspaceApi();
  const [showV2, setShowV2] = useState(false);
  const [focusedEvidence, setFocusedEvidence] = useState<string | null>(null);
  const [pinnedEvidence, setPinnedEvidence] = useState<string | null>(null);
  const [disputed, setDisputed] = useState<Set<string>>(new Set());
  const [localEdits, setLocalEdits] = useState<Record<string, string>>({});
  const [focusedParagraphId, setFocusedParagraphId] = useState<string | null>(
    null
  );
  const [activeSectionId, setActiveSectionId] = useState<string | null>(
    report.sections[0]?.id ?? null
  );

  /* URL ?section=xxx → 只渲染该章节（章节聚焦模式） */
  const searchParams = useSearchParams();
  const sectionParam = searchParams.get("section");
  const sectionFocus = sectionParam
    ? report.sections.find((s) => s.id === sectionParam)
    : null;
  useEffect(() => {
    if (sectionFocus) setActiveSectionId(sectionFocus.id);
  }, [sectionFocus]);

  /* IO observer for section tracking */
  useEffect(() => {
    if (typeof window === "undefined") return;
    const ids = report.sections.map((s) => s.id);
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort(
            (a, b) =>
              a.target.getBoundingClientRect().top -
              b.target.getBoundingClientRect().top
          );
        if (visible[0]) {
          const id = visible[0].target.id.replace(/^sec-/, "");
          if (ids.includes(id)) setActiveSectionId(id);
        }
      },
      { rootMargin: "-152px 0px -60% 0px", threshold: 0.01 }
    );
    ids.forEach((id) => {
      const el = document.getElementById(`sec-${id}`);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [report.sections]);

  const pendingDiffCount = useMemo(
    () =>
      report.sections.flatMap((s) => s.paragraphs).filter((p) => p.pendingV2)
        .length,
    [report]
  );

  const userEditCount = Object.keys(localEdits).length;

  const handleSectionClick = (sectionId: string) => {
    const el = document.getElementById(`sec-${sectionId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveSectionId(sectionId);
    }
  };

  const handleEvidenceClick = (evidenceId: string) => {
    setPinnedEvidence((cur) => (cur === evidenceId ? null : evidenceId));
  };

  const handleFocusEvidence = (
    evidenceId: string | null,
    paragraphId?: string | null
  ) => {
    setFocusedEvidence(evidenceId);
    if (paragraphId !== undefined) setFocusedParagraphId(paragraphId);
  };

  const handleToggleDisputed = (evidenceId: string) => {
    setDisputed((cur) => {
      const next = new Set(cur);
      if (next.has(evidenceId)) {
        next.delete(evidenceId);
        void REPORT_ACTIONS.unmarkEvidenceDisputed(evidenceId, { api });
      } else {
        next.add(evidenceId);
        void REPORT_ACTIONS.markEvidenceDisputed(evidenceId, { api });
      }
      return next;
    });
  };

  /* save path: 优先 PATCH（API 模式）；否则只更新本地 */
  const handleSaved = async (paragraphId: string, newText: string) => {
    if (isApi && apiReporter && apiProjectId) {
      try {
        const res = await patchParagraph(
          apiProjectId,
          apiReporter.nodeId,
          paragraphId,
          { text: newText }
        );
        setLocalEdits((cur) => ({ ...cur, [paragraphId]: newText }));
        emitIntervention("edit-paragraph", paragraphId);
        toast.success("段落已保存", {
          description: `manual_edits=${res.manual_edits} · edit_rate=${res.edit_rate.toFixed(
            2
          )}`,
        });
        void revalidate.projectState(apiProjectId);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toast.error("保存失败", { description: msg });
        throw e;
      }
    } else {
      setLocalEdits((cur) => ({ ...cur, [paragraphId]: newText }));
      emitIntervention("edit-paragraph", paragraphId);
      toast.success("段落已保存为 v2", {
        description: `${paragraphId} · 计入 metrics.edit_rate (mock)`,
      });
    }
  };

  /* enrich paragraphs with localEdits (display layer only) */
  const sections = useMemo(() => {
    if (Object.keys(localEdits).length === 0) return report.sections;
    return report.sections.map((s) => ({
      ...s,
      paragraphs: s.paragraphs.map((p) =>
        localEdits[p.id] ? { ...p, text: localEdits[p.id] } : p
      ),
    }));
  }, [report.sections, localEdits]);

  let runningIndex = 0;
  const allSectionsWithIndex = sections.map((s) => {
    const start = runningIndex;
    runningIndex += s.paragraphs.length;
    return { s, start };
  });
  /* 章节聚焦模式：只渲染该章节；否则渲染全部 */
  const sectionsWithIndex = sectionFocus
    ? allSectionsWithIndex.filter(({ s }) => s.id === sectionFocus.id)
    : allSectionsWithIndex;

  return (
    <EvidenceLookupProvider apiEvidences={apiEvidences}>
      <div className="space-y-4">
        <ReportHeader
          report={report}
          localEdits={localEdits}
          showV2={showV2}
        />
        <EditHistoryToggle
          showV2={showV2}
          onChange={setShowV2}
          v2NodeRunning={!isApi}
          pendingDiffCount={pendingDiffCount}
          userEditCount={userEditCount}
        />
        <div
          className={cn(
            "grid gap-8",
            sectionFocus
              ? "grid-cols-[minmax(0,1fr)_360px]"
              : "grid-cols-[180px_minmax(0,1fr)_360px]"
          )}
        >
          {!sectionFocus ? (
            <ReportToc
              sections={report.sections}
              activeSectionId={activeSectionId}
              onSectionClick={handleSectionClick}
            />
          ) : null}

          <article className="min-w-0 max-w-[760px] space-y-10 rounded-lg border border-border-subtle bg-bg-raised px-8 py-10">
            {sectionsWithIndex.map(({ s, start }) => (
              <ReportSection
                key={s.id}
                section={s}
                globalParagraphStart={start}
                showV2={showV2}
                focusedParagraphId={focusedParagraphId}
                onEvidenceClick={handleEvidenceClick}
                onFocusEvidence={(eid) =>
                  handleFocusEvidence(
                    eid,
                    eid
                      ? s.paragraphs.find((p) => p.evidenceIds.includes(eid))
                          ?.id ?? null
                      : null
                  )
                }
                onSaved={handleSaved}
              />
            ))}
          </article>

          <EvidenceDrawer
            focusedId={focusedEvidence}
            pinnedId={pinnedEvidence}
            disputed={disputed}
            onUnpin={() => setPinnedEvidence(null)}
            onToggleDisputed={handleToggleDisputed}
          />
        </div>
      </div>
    </EvidenceLookupProvider>
  );
}

/* ── header ────────────────────────────────────────────────────────────── */

function ReportHeader({
  report,
  localEdits,
  showV2,
}: {
  report: MockReport;
  localEdits: Record<string, string>;
  showV2: boolean;
}) {
  const handleDownload = () => {
    const md = renderReportAsMarkdown(localEdits, showV2);
    const filename = `report-${report.id}-${
      showV2 ? "v2-preview" : "v1"
    }${Object.keys(localEdits).length ? "-edited" : ""}.md`;
    downloadMarkdown(filename, md);
    toast.success(`Markdown 已下载 · ${filename}`, {
      description: `${md.split("\n").length} 行 · 含 Evidence 附录 + 数据来源声明`,
    });
    emitIntervention("export-md", filename);
  };

  const handleCopy = () => {
    const md = renderReportAsMarkdown(localEdits, showV2);
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(md);
    }
    toast.success("Markdown 已复制", {
      description: `${md.length} chars · 可粘贴到 Notion / Slack / 邮件`,
    });
    emitIntervention("copy-md", "report");
  };

  return (
    <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle pb-4">
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
          Report · v{report.version}
        </div>
        <h1 className="mt-1 text-xl font-semibold text-text-primary">
          {report.target} vs {report.competitors.join(" · ")}
        </h1>
        <p className="mt-1 text-sm text-text-secondary">{report.summary}</p>
      </div>
      <div className="flex items-end gap-4">
        <div className="hidden md:flex flex-col items-end gap-1 text-xs text-text-muted">
          <Stat label="template" value={report.templateId} mono />
          <div className="flex items-center gap-3">
            <Stat
              label="claims"
              value={String(report.metadata.claimCount)}
              mono
            />
            <Stat
              label="evidence"
              value={String(report.metadata.evidenceCount)}
              mono
            />
          </div>
          <Stat label="generated" value={report.generatedAt} mono />
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={handleCopy}
            className="gap-1.5"
            title="复制 Markdown 到剪贴板"
          >
            <CopyIcon className="h-3 w-3" />
            <span>Copy MD</span>
          </Button>
          <Button
            size="sm"
            onClick={handleDownload}
            className="gap-1.5"
            title="下载 .md 文件"
          >
            <DownloadIcon className="h-3 w-3" />
            <span>Export Markdown</span>
          </Button>
        </div>
      </div>
    </header>
  );
}

function Stat({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-[10px] uppercase tracking-wider">{label}</span>
      <span
        className={
          (mono ? "font-mono " : "") + "font-medium text-text-secondary"
        }
      >
        {value}
      </span>
    </span>
  );
}

/* ── adapter: API draft → MockReport ───────────────────────────────────── */

function apiDraftToMockReport(
  draft: ApiReportDraft,
  verdicts: ApiQAVerdict[]
): MockReport {
  /* 把 verdict 的 issues 按 location 索引，方便挂到段落上 */
  const issueByLocation = new Map<string, ReturnType<typeof issueToHint>>();
  for (const v of verdicts) {
    for (const iss of v.issues) {
      issueByLocation.set(iss.location, issueToHint(iss));
    }
  }

  const sections: MockSection[] = draft.sections
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((s, sIdx) => ({
      id: s.section_id,
      number: String(sIdx + 1),
      title: s.title,
      paragraphs: s.paragraphs.map((p, pIdx) =>
        apiParaToMock(p, sIdx, pIdx, issueByLocation)
      ),
    }));

  const totalEvidenceIds = new Set<string>();
  draft.sections.forEach((s) =>
    s.paragraphs.forEach((p) =>
      p.evidence_ids.forEach((eid) => totalEvidenceIds.add(eid))
    )
  );

  return {
    id: draft.report_id,
    version: draft.version,
    templateId: draft.template_id,
    generatedAt: new Date().toISOString().replace("T", " ").slice(0, 19),
    /* target / competitors 通常在 ClientWorkspace 已知；这里 fallback 用 metadata */
    target:
      (draft.metadata?.target as string | undefined) ?? "—",
    competitors: (draft.metadata?.competitors as string[] | undefined) ?? [],
    summary: draft.summary,
    sections,
    metadata: {
      wordCount: countWords(draft.sections),
      claimCount: countClaims(draft.sections),
      evidenceCount: totalEvidenceIds.size,
    },
  };
}

function apiParaToMock(
  p: ApiReportParagraph,
  sIdx: number,
  pIdx: number,
  issueIndex: Map<string, ReturnType<typeof issueToHint>>
): MockParagraph {
  const loc1 = `report.sections[${sIdx}].paragraphs[${pIdx}]`;
  const loc2 = p.paragraph_id;
  const qaIssue = issueIndex.get(loc1) ?? issueIndex.get(loc2);
  return {
    id: p.paragraph_id,
    text: p.text,
    evidenceIds: p.evidence_ids,
    isQuantitative: p.is_quantitative,
    isSoftConclusion: p.is_soft_conclusion,
    qaIssue: qaIssue ?? undefined,
  };
}

function issueToHint(iss: ApiQAVerdict["issues"][number]):
  | NonNullable<MockParagraph["qaIssue"]>
  | undefined {
  return {
    severity: iss.severity,
    dimension: iss.dimension,
    note: iss.problem,
  };
}

function countWords(sections: ApiReportSection[]): number {
  return sections.reduce(
    (acc, s) => acc + s.paragraphs.reduce((a, p) => a + p.text.length, 0),
    0
  );
}

function countClaims(sections: ApiReportSection[]): number {
  const set = new Set<string>();
  sections.forEach((s) =>
    s.paragraphs.forEach((p) =>
      p.claim_ids.forEach((c) => set.add(c))
    )
  );
  return set.size;
}
