"use client";

import { useCallback, useMemo, useState } from "react";
import { SearchIcon, AlertTriangleIcon, XIcon } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { emitIntervention, REPORT_ACTIONS } from "@/lib/workspace-actions";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import {
  buildReverseIndex,
  listEvidences,
  listProducts,
  listSourceTypes,
} from "@/lib/evidence-index";
import { apiEvidenceToMock } from "@/lib/evidence-context";
import type {
  Evidence as ApiEvidence,
  AnyAgentOutput,
  ReporterOutput,
} from "@/lib/api/types";
import type { MockEvidence } from "@/lib/report-mock";
import { EvidenceFilter, type EvidenceFilterState } from "./evidence-filter";
import { EvidenceRow } from "./evidence-row";

interface EvidenceLayoutProps {
  /** 真实 API 模式：替换 mock evidence pool */
  apiEvidences?: ApiEvidence[];
  /** 真实 API 模式：用于反向定位段落 */
  apiOutputs?: Record<string, AnyAgentOutput>;
}

/**
 * Workspace · Evidence tab.
 *
 * 评分项「信息溯源完整 · 反向定位」核心。
 * 左侧 sticky filter（status / product / source type）·
 * 右侧 evidence 列表（hover 展开行内详情 + 反向引用列表）·
 * 顶部搜索 + 批量选择 + bulk action bar。
 *
 * 用户标记 disputed 全部走 REPORT_ACTIONS.markEvidenceDisputed →
 * 计入 metrics.edit_rate / 人工修正率。
 */
export function EvidenceLayout({
  apiEvidences,
  apiOutputs,
}: EvidenceLayoutProps = {}) {
  const api = useWorkspaceApi();
  const isApi = apiEvidences !== undefined;

  // 反向跳转链接：真实 workspace 用当前 project/run；demo/mock 回退到 demo run。
  const paragraphHref = useCallback(
    (paragraphId: string) => {
      const base = api
        ? `/projects/${api.projectId}/runs/${api.runId}`
        : "/projects/demo/runs/01";
      return `${base}?tab=report#para-${paragraphId}`;
    },
    [api]
  );

  const allEvidences = useMemo<MockEvidence[]>(() => {
    if (isApi && apiEvidences) {
      return apiEvidences.map(apiEvidenceToMock);
    }
    return listEvidences();
  }, [isApi, apiEvidences]);

  const allProducts = useMemo(() => {
    if (isApi) return Array.from(new Set(allEvidences.map((e) => e.product)));
    return listProducts();
  }, [isApi, allEvidences]);

  const allSourceTypes = useMemo(() => {
    if (isApi) return Array.from(new Set(allEvidences.map((e) => e.sourceType)));
    return listSourceTypes();
  }, [isApi, allEvidences]);

  const reverseIndex = useMemo(() => {
    if (isApi && apiOutputs) return buildApiReverseIndex(apiOutputs);
    return buildReverseIndex();
  }, [isApi, apiOutputs]);

  const [filter, setFilter] = useState<EvidenceFilterState>({
    products: new Set(),
    statuses: new Set(),
    sourceTypes: new Set(),
  });
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [disputedOverride, setDisputedOverride] = useState<Set<string>>(
    new Set()
  );

  /* counts by facet, derived from full list (not filtered) */
  const counts = useMemo(() => {
    const status: Record<string, number> = {};
    const product: Record<string, number> = {};
    const sourceType: Record<string, number> = {};
    for (const ev of allEvidences) {
      const effective = disputedOverride.has(ev.id) ? "disputed" : ev.status;
      status[effective] = (status[effective] ?? 0) + 1;
      product[ev.product] = (product[ev.product] ?? 0) + 1;
      sourceType[ev.sourceType] = (sourceType[ev.sourceType] ?? 0) + 1;
    }
    return { status, product, sourceType };
  }, [allEvidences, disputedOverride]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allEvidences.filter((ev) => {
      const effective = disputedOverride.has(ev.id) ? "disputed" : ev.status;
      if (filter.statuses.size > 0 && !filter.statuses.has(effective))
        return false;
      if (filter.products.size > 0 && !filter.products.has(ev.product))
        return false;
      if (
        filter.sourceTypes.size > 0 &&
        !filter.sourceTypes.has(ev.sourceType)
      )
        return false;
      if (q) {
        if (
          !ev.content.toLowerCase().includes(q) &&
          !ev.id.toLowerCase().includes(q) &&
          !ev.product.toLowerCase().includes(q)
        )
          return false;
      }
      return true;
    });
  }, [allEvidences, filter, search, disputedOverride]);

  const handleToggleSelect = (id: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleToggleDisputed = (id: string) => {
    setDisputedOverride((cur) => {
      const next = new Set(cur);
      if (next.has(id)) {
        next.delete(id);
        void REPORT_ACTIONS.unmarkEvidenceDisputed(id, { api });
      } else {
        next.add(id);
        void REPORT_ACTIONS.markEvidenceDisputed(id, { api });
      }
      return next;
    });
  };

  const handleBulkMarkDisputed = () => {
    selected.forEach((id) => {
      if (!disputedOverride.has(id)) {
        // 批量场景关掉 auto_rework，避免一次 PATCH 触发 N 次 reporter 重跑
        void REPORT_ACTIONS.markEvidenceDisputed(id, {
          api,
          autoRework: false,
        });
      }
    });
    setDisputedOverride((cur) => {
      const next = new Set(cur);
      selected.forEach((id) => next.add(id));
      return next;
    });
    toast.warning(`已批量将 ${selected.size} 条证据标记为有异议`, {
      description: "引用这些证据的段落会触发质量复审",
    });
    emitIntervention("bulk-dispute", `count_${selected.size}`);
    setSelected(new Set());
  };

  const handleBulkExport = () => {
    const data = Array.from(selected)
      .map((id) => allEvidences.find((e) => e.id === id))
      .filter(Boolean);
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    }
    toast.success(`已复制 ${selected.size} 条证据（JSON）`);
    emitIntervention("bulk-export-evidence", `count_${selected.size}`);
  };

  return (
    <div className="grid grid-cols-[220px_minmax(0,1fr)] gap-8">
      <EvidenceFilter
        filter={filter}
        onChange={setFilter}
        products={allProducts}
        sourceTypes={allSourceTypes}
        counts={counts}
        totalCount={allEvidences.length}
        resetAll={() =>
          setFilter({
            products: new Set(),
            statuses: new Set(),
            sourceTypes: new Set(),
          })
        }
      />

      <div className="space-y-4">
        <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border-subtle pb-3">
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
              证据库
            </div>
            <h1 className="mt-1 text-lg font-semibold text-text-primary">
              <span className="font-mono tabular-nums" data-num>
                {filtered.length}
              </span>
              <span className="text-text-muted"> / {allEvidences.length}</span>
              <span className="ml-1.5 text-text-secondary">条证据</span>
            </h1>
          </div>
          <div className="relative">
            <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-text-muted" />
            <Input
              type="search"
              placeholder="搜索证据内容 · ID · 产品名…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-[320px] pl-8"
            />
            {search ? (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
                aria-label="clear search"
              >
                <XIcon className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        </header>

        {/* bulk action bar */}
        {selected.size > 0 ? (
          <div className="flex items-center gap-3 rounded-md border border-accent-border bg-accent-bg/40 px-4 py-2.5">
            <span className="text-sm font-medium text-text-primary">
              选中 <span className="font-mono tabular-nums" data-num>{selected.size}</span> 条
            </span>
            <div className="ml-auto flex items-center gap-1.5">
              <Button size="sm" variant="outline" onClick={handleBulkMarkDisputed} className="gap-1.5">
                <AlertTriangleIcon className="h-3 w-3" />
                <span>标记有误</span>
              </Button>
              <Button size="sm" variant="ghost" onClick={handleBulkExport}>
                复制为 JSON
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelected(new Set())}
              >
                取消
              </Button>
            </div>
          </div>
        ) : null}

        {filtered.length === 0 ? (
          <EmptyState
            hasQuery={!!search || filter.products.size + filter.statuses.size + filter.sourceTypes.size > 0}
          />
        ) : (
          <ul className="space-y-2.5">
            {filtered.map((ev) => (
              <li key={ev.id}>
                <EvidenceRow
                  evidence={ev}
                  refs={reverseIndex[ev.id] ?? []}
                  selected={selected.has(ev.id)}
                  expanded={expandedId === ev.id}
                  isDisputedOverride={disputedOverride.has(ev.id)}
                  paragraphHref={paragraphHref}
                  onToggleSelect={() => handleToggleSelect(ev.id)}
                  onToggleExpand={() =>
                    setExpandedId((cur) => (cur === ev.id ? null : ev.id))
                  }
                  onToggleDisputed={() => handleToggleDisputed(ev.id)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function EmptyState({ hasQuery }: { hasQuery: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-border-default bg-bg-raised/60 px-8 py-16 text-center">
      <div className="mx-auto inline-flex h-10 w-10 items-center justify-center rounded-pill bg-bg-sunken">
        <SearchIcon className="h-4 w-4 text-text-muted" />
      </div>
      <p className="mt-3 text-sm text-text-secondary">
        {hasQuery
          ? "没有匹配当前筛选 / 搜索条件的证据。"
          : "证据库为空，采集与抽取完成后会自动填充。"}
      </p>
    </div>
  );
}

/* ── API 反向索引：扫描 reporter outputs 拿引用 ────────────────────────── */

function buildApiReverseIndex(
  outputs: Record<string, AnyAgentOutput>
): Record<
  string,
  Array<{
    paragraphId: string;
    sectionId: string;
    sectionNumber: string;
    sectionTitle: string;
    textPreview: string;
  }>
> {
  const index: Record<
    string,
    Array<{
      paragraphId: string;
      sectionId: string;
      sectionNumber: string;
      sectionTitle: string;
      textPreview: string;
    }>
  > = {};
  for (const [nodeId, out] of Object.entries(outputs)) {
    if (!nodeId.startsWith("reporter")) continue;
    if (!("draft" in out)) continue;
    const draft = (out as ReporterOutput).draft;
    draft.sections
      .slice()
      .sort((a, b) => a.order - b.order)
      .forEach((s, sIdx) => {
        s.paragraphs.forEach((p) => {
          p.evidence_ids.forEach((eid) => {
            if (!index[eid]) index[eid] = [];
            if (!index[eid].some((r) => r.paragraphId === p.paragraph_id)) {
              index[eid].push({
                paragraphId: p.paragraph_id,
                sectionId: s.section_id,
                sectionNumber: String(sIdx + 1),
                sectionTitle: s.title,
                textPreview:
                  p.text.slice(0, 80) + (p.text.length > 80 ? "…" : ""),
              });
            }
          });
        });
      });
  }
  return index;
}
