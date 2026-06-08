"use client";

import { cn } from "@/lib/utils";

export interface EvidenceFilterState {
  products: Set<string>;
  statuses: Set<string>;
  sourceTypes: Set<string>;
}

const STATUS_META: Array<{
  id: "verified" | "disputed" | "stale";
  label: string;
  dot: string;
}> = [
  { id: "verified", label: "已验证", dot: "bg-success-base" },
  { id: "disputed", label: "有异议", dot: "bg-error-base" },
  { id: "stale", label: "已过期", dot: "bg-warning-base" },
];

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

export function EvidenceFilter({
  filter,
  onChange,
  products,
  sourceTypes,
  counts,
  totalCount,
  resetAll,
}: {
  filter: EvidenceFilterState;
  onChange: (next: EvidenceFilterState) => void;
  products: string[];
  sourceTypes: string[];
  counts: {
    status: Record<string, number>;
    product: Record<string, number>;
    sourceType: Record<string, number>;
  };
  totalCount: number;
  resetAll: () => void;
}) {
  const toggle = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  const activeCount =
    filter.products.size + filter.statuses.size + filter.sourceTypes.size;

  return (
    <aside className="sticky top-[152px] self-start space-y-5">
      <div className="flex items-center justify-between">
        <div className="text-[10px] font-medium uppercase tracking-wider text-text-muted">
          筛选
        </div>
        {activeCount > 0 ? (
          <button
            type="button"
            onClick={resetAll}
            className="text-[10px] text-text-muted hover:text-text-secondary"
          >
            重置（{activeCount}）
          </button>
        ) : null}
      </div>

      <FilterGroup label="状态">
        {STATUS_META.map((s) => (
          <FilterChip
            key={s.id}
            active={filter.statuses.has(s.id)}
            onClick={() =>
              onChange({ ...filter, statuses: toggle(filter.statuses, s.id) })
            }
            count={counts.status[s.id] ?? 0}
          >
            <span className={cn("h-1.5 w-1.5 rounded-pill", s.dot)} />
            <span>{s.label}</span>
          </FilterChip>
        ))}
      </FilterGroup>

      <FilterGroup label="产品">
        {products.map((p) => (
          <FilterChip
            key={p}
            active={filter.products.has(p)}
            onClick={() =>
              onChange({ ...filter, products: toggle(filter.products, p) })
            }
            count={counts.product[p] ?? 0}
          >
            <span>{p}</span>
          </FilterChip>
        ))}
      </FilterGroup>

      <FilterGroup label="来源类型">
        {sourceTypes.map((s) => (
          <FilterChip
            key={s}
            active={filter.sourceTypes.has(s)}
            onClick={() =>
              onChange({
                ...filter,
                sourceTypes: toggle(filter.sourceTypes, s),
              })
            }
            count={counts.sourceType[s] ?? 0}
          >
            <span className="text-[11px]">{sourceTypeLabel(s)}</span>
          </FilterChip>
        ))}
      </FilterGroup>

      <div className="border-t border-border-subtle pt-3 text-[10px] text-text-muted">
        共{" "}
        <span
          className="font-mono font-medium text-text-primary tabular-nums"
          data-num
        >
          {totalCount}
        </span>{" "}
        条证据
      </div>
    </aside>
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        {label}
      </div>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-[11px] font-medium",
        "transition-colors duration-120 ease-out-quart",
        active
          ? "border-accent-border bg-accent-bg text-accent-base"
          : "border-border-subtle bg-bg-raised text-text-secondary hover:border-border-default hover:text-text-primary"
      )}
    >
      {children}
      <span
        className={cn(
          "font-mono text-[10px] tabular-nums",
          active ? "text-accent-base" : "text-text-muted"
        )}
        data-num
      >
        {count}
      </span>
    </button>
  );
}
