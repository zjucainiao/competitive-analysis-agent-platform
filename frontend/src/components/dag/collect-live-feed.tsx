"use client";

import {
  CheckIcon,
  CircleHelpIcon,
  AlertTriangleIcon,
  RadioIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { CollectProgressSource } from "@/lib/api/types";

/**
 * 实时采集面板 —— 采集节点运行**期间**逐条显示抓到的来源 + 身份判定。
 *
 * 数据来自 WebSocket 的 ``collect_progress`` 事件（见 ws / collector emit）。
 * 编排器在 collector 每抓+校验完一条来源时推一条，因此这里能「边采边看」，
 * 身份 mismatch（疑似抓到别的产品）当场可见，而不是等整段采集跑完。
 */
export function CollectLiveFeed({
  sources,
  runStatus,
}: {
  sources: CollectProgressSource[];
  runStatus: string;
}) {
  if (sources.length === 0) return null;
  const live = runStatus === "running" || runStatus === "rework";

  const byProduct = new Map<string, CollectProgressSource[]>();
  for (const s of sources) {
    const arr = byProduct.get(s.product) ?? [];
    arr.push(s);
    byProduct.set(s.product, arr);
  }
  const mismatchCount = sources.filter(
    (s) => s.identity_status === "mismatch"
  ).length;

  return (
    <div className="mb-4 rounded-lg border border-border-subtle bg-bg-raised/40 p-3">
      <div className="mb-2 flex items-center gap-2 text-[11px]">
        {live ? (
          <RadioIcon className="h-3.5 w-3.5 animate-pulse text-running-base" />
        ) : (
          <CheckIcon className="h-3.5 w-3.5 text-success-base" />
        )}
        <span className="font-medium text-text-secondary">
          {live ? "实时采集中" : "本轮采集"} · 已抓取 {sources.length} 条来源
        </span>
        {mismatchCount > 0 ? (
          <span className="rounded-pill bg-warning-bg px-1.5 text-[10px] font-medium text-warning-base">
            {mismatchCount} 条疑似别的产品已标记
          </span>
        ) : null}
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        {[...byProduct.entries()].map(([product, items]) => (
          <div
            key={product}
            className="rounded-md border border-border-subtle bg-bg-base p-2"
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs font-semibold text-text-primary">
                {product}
              </span>
              <span className="text-[10px] text-text-muted">{items.length} 源</span>
            </div>
            <ul className="space-y-1">
              {items.map((s, i) => (
                <li
                  key={`${s.url}-${i}`}
                  className="flex items-start gap-1.5 text-[11px]"
                >
                  <IdentityBadge
                    status={s.identity_status}
                    detected={s.detected_product_name}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-text-secondary">
                      {s.title || hostOf(s.url)}
                    </div>
                    <div className="truncate text-[10px] text-text-muted">
                      {s.dimension} · {hostOf(s.url)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function IdentityBadge({
  status,
  detected,
}: {
  status: string;
  detected: string | null;
}) {
  const base = "mt-0.5 h-3 w-3 shrink-0";
  if (status === "mismatch") {
    return (
      <span title={`疑似${detected ?? "别的产品"}，已标记`} className="contents">
        <AlertTriangleIcon className={cn(base, "text-warning-base")} />
      </span>
    );
  }
  if (status === "ambiguous") {
    return (
      <span title="身份存疑（无法确证属目标产品）" className="contents">
        <CircleHelpIcon className={cn(base, "text-text-muted")} />
      </span>
    );
  }
  return (
    <span title="确属目标产品" className="contents">
      <CheckIcon className={cn(base, "text-success-base")} />
    </span>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 48);
  }
}
