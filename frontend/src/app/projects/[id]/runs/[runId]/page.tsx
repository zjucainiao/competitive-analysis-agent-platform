import { ClientWorkspace } from "./client-workspace";
import { DEFAULT_TAB, type TabKey } from "@/components/layout/tabs-row";

/**
 * Workspace 入口。所有数据走客户端 SWR + WS（client-workspace.tsx）。
 *
 *   projectId === "demo" → mock fallback
 *   其他              → /api/projects/{id}/state + WS /events
 */
export default async function WorkspacePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string; runId: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { id, runId } = await params;
  const { tab: rawTab } = await searchParams;
  const tab = normalizeTab(rawTab);
  return <ClientWorkspace projectId={id} runId={runId} tab={tab} />;
}

function normalizeTab(raw: string | undefined): TabKey {
  const allowed: TabKey[] = ["dag", "report", "trace", "evidence", "metrics"];
  if (raw && (allowed as string[]).includes(raw)) {
    return raw as TabKey;
  }
  return DEFAULT_TAB;
}
