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
  { id: "verified", label: "verified", dot: "bg-success-base" },
  { id: "disputed", label: "disputed", dot: "bg-error-base" },
  { id: "stale", label: "stale", dot: "bg-warning-base" },
];

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
          Filter
        </div>
        {activeCount > 0 ? (
          <button
            type="button"
            onClick={resetAll}
            className="text-[10px] text-text-muted hover:text-text-secondary"
          >
            reset ({activeCount})
          </button>
        ) : null}
      </div>

      <FilterGroup label="Status">
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

      <FilterGroup label="Product">
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

      <FilterGroup label="Source type">
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
            <span className="font-mono text-[11px]">{s}</span>
          </FilterChip>
        ))}
      </FilterGroup>

      <div className="border-t border-border-subtle pt-3 text-[10px] text-text-muted">
        total{" "}
        <span
          className="font-mono font-medium text-text-primary tabular-nums"
          data-num
        >
          {totalCount}
        </span>{" "}
        evidences
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
