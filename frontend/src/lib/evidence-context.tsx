"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import {
  getEvidence as getMockEvidence,
  type MockEvidence,
} from "@/lib/report-mock";
import type { Evidence as ApiEvidence } from "@/lib/api/types";

type Lookup = (id: string) => MockEvidence | undefined;

const EvidenceLookupContext = createContext<Lookup>(getMockEvidence);

export function useEvidenceLookup(): Lookup {
  return useContext(EvidenceLookupContext);
}

/* ── ApiEvidence → MockEvidence 适配 ─────────────────────────────────── */

export function apiEvidenceToMock(ev: ApiEvidence): MockEvidence {
  const status: MockEvidence["status"] = ev.disputed
    ? "disputed"
    : isStale(ev)
      ? "stale"
      : "verified";
  return {
    id: ev.evidence_id,
    product: ev.product_name,
    sourceUrl: ev.source_url,
    sourceType: ev.source_type,
    sourceLabel: shortDomain(ev.source_url),
    authority: ev.source_authority,
    language: ev.language === "zh" ? "zh" : "en",
    content: ev.content,
    contextBefore: ev.context_before ?? undefined,
    contextAfter: ev.context_after ?? undefined,
    collectedAt: ev.collected_at,
    tags: ev.tags,
    status,
  };
}

function isStale(ev: ApiEvidence): boolean {
  const sensitive = ev.tags.some((t) =>
    ["pricing", "version", "changelog"].includes(t)
  );
  const threshold = sensitive ? 90 : 365;
  const base = ev.source_published_at ?? ev.collected_at;
  const t = new Date(base).getTime();
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) / 86_400_000 > threshold;
}

function shortDomain(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + u.pathname.replace(/\/$/, "");
  } catch {
    return url.slice(0, 40);
  }
}

/* ── Provider ────────────────────────────────────────────────────────── */

export function EvidenceLookupProvider({
  apiEvidences,
  children,
}: {
  /** 若提供则覆盖 mock 查找；否则走 getMockEvidence */
  apiEvidences?: ApiEvidence[];
  children: ReactNode;
}) {
  const lookup = useMemo<Lookup>(() => {
    if (!apiEvidences || apiEvidences.length === 0) return getMockEvidence;
    const map = new Map<string, MockEvidence>();
    apiEvidences.forEach((e) => map.set(e.evidence_id, apiEvidenceToMock(e)));
    return (id: string) => map.get(id);
  }, [apiEvidences]);

  return (
    <EvidenceLookupContext.Provider value={lookup}>
      {children}
    </EvidenceLookupContext.Provider>
  );
}
