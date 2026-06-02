"use client";

import { useEffect, useMemo } from "react";
import Link from "next/link";
import { AlertTriangleIcon, Loader2Icon, PlugZapIcon } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/layout/app-shell";
import type { RunContext } from "@/components/layout/context-bar";
import { DagCanvas } from "@/components/dag";
import { EditPromptDialog } from "@/components/dag/edit-prompt-dialog";
import { ExportMenu } from "@/components/layout/export-menu";
import { ReportLayout } from "@/components/report";
import { TraceLayout } from "@/components/trace";
import { EvidenceLayout } from "@/components/evidence";
import { MetricsLayout } from "@/components/metrics";
import { Button } from "@/components/ui/button";
import { GlobalNav } from "@/components/layout/global-nav";
import { useProjectState, revalidate } from "@/lib/api/hooks";
import { useProjectEvents } from "@/lib/api/ws";
import { apiStateToDagData, findLatestReporter, aggregateEvidences } from "@/lib/api/adapters";
import { DEMO_RUN_CONTEXT } from "@/lib/mock-run";
import { WorkspaceApiProvider } from "@/lib/workspace-api-context";
import type { TabKey } from "@/components/layout/tabs-row";
import type { Project, ProjectStateResponse } from "@/lib/api/types";

/**
 * Workspace 客户端壳。
 *
 *  - useProjectState（SWR）拉 plan + outputs + verdicts
 *  - useProjectEvents（WS）订阅 /events，每条 NodeExecutionResult 触发 revalidate
 *  - 把适配后的数据下发到各 tab
 *  - projectId === "demo" 走 mock（保留 design preview 路径）
 */
export function ClientWorkspace({
  projectId,
  runId,
  tab,
}: {
  projectId: string;
  runId: string;
  tab: TabKey;
}) {
  const isDemoMock = projectId === "demo";

  /* mock 路径直接渲染（与之前一致） */
  if (isDemoMock) {
    return <DemoMockWorkspace tab={tab} />;
  }
  return (
    <ApiWorkspace projectId={projectId} runId={runId} tab={tab} />
  );
}

/* ── demo mock path（保留 /projects/demo/runs/01 不变） ─────────────── */

function DemoMockWorkspace({ tab }: { tab: TabKey }) {
  return (
    <AppShell ctx={DEMO_RUN_CONTEXT}>
      {tab === "dag" && <DagCanvas />}
      {tab === "report" && <ReportLayout />}
      {tab === "trace" && <TraceLayout />}
      {tab === "evidence" && <EvidenceLayout />}
      {tab === "metrics" && <MetricsLayout ctx={DEMO_RUN_CONTEXT} />}
    </AppShell>
  );
}

/* ── live API path ───────────────────────────────────────────────────── */

function ApiWorkspace({
  projectId,
  tab,
}: {
  projectId: string;
  runId: string;
  tab: TabKey;
}) {
  const { data: state, error, isLoading, mutate } = useProjectState(projectId);

  /* WS subscribe → on event, revalidate state */
  const { status: wsStatus } = useProjectEvents(projectId, {
    onMessage: (msg) => {
      void revalidate.projectState(projectId);
      if (msg.status === "failed" && msg.error) {
        toast.error(`${msg.node_id} failed`, {
          description: msg.error.message.slice(0, 120),
        });
      } else if (msg.status === "needs_rework") {
        toast.warning(`${msg.node_id} → needs rework`);
      } else if (msg.status === "success") {
        /* 不刷屏，只在关键节点弹 */
        if (msg.node_id.startsWith("qa")) {
          toast.success(`${msg.node_id} verdict ready`);
        }
      }
    },
  });

  if (error) {
    return <ErrorView error={error} projectId={projectId} onRetry={() => mutate()} />;
  }
  if (isLoading || !state) {
    return <LoadingView />;
  }

  return (
    <ApiWorkspaceShell state={state} tab={tab} wsConnected={wsStatus === "open"} projectId={projectId} />
  );
}

function ApiWorkspaceShell({
  state,
  tab,
  wsConnected,
  projectId,
}: {
  state: ProjectStateResponse;
  tab: TabKey;
  wsConnected: boolean;
  projectId: string;
}) {
  const ctx = useMemo(() => projectToRunContext(state.project), [state.project]);
  const dagData = useMemo(() => apiStateToDagData(state), [state]);
  const latestReporter = useMemo(() => findLatestReporter(state.outputs), [state.outputs]);
  const allEvidences = useMemo(() => aggregateEvidences(state.outputs), [state.outputs]);

  const apiContext = useMemo(
    () => ({
      projectId,
      revalidate: () =>
        Promise.all([
          revalidate.projectState(projectId),
          revalidate.project(projectId),
          revalidate.projects(),
        ]),
    }),
    [projectId]
  );

  return (
    <WorkspaceApiProvider value={apiContext}>
    <AppShell ctx={ctx}>
      {!wsConnected && (
        <div className="-mx-10 mb-4 border-b border-warning-border bg-warning-bg px-10 py-1.5 text-[11px] text-warning-base">
          WebSocket 未连接 · 每 30 秒 SWR 轮询兜底
        </div>
      )}
      {tab === "dag" && (
        <DagCanvas
          nodes={dagData.nodes}
          edges={dagData.edges}
          isLiveData={wsConnected}
        />
      )}
      {tab === "report" && (
        <ReportLayout
          apiReporter={latestReporter}
          apiEvidences={allEvidences}
          apiProjectId={projectId}
          apiVerdicts={state.verdicts}
        />
      )}
      {tab === "trace" && (
        <TraceLayout apiState={state} />
      )}
      {tab === "evidence" && (
        <EvidenceLayout apiEvidences={allEvidences} apiOutputs={state.outputs} />
      )}
      {tab === "metrics" && (
        <MetricsLayout ctx={ctx} apiProject={state.project} />
      )}
      <EditPromptDialog />
      <ExportMenu />
    </AppShell>
    </WorkspaceApiProvider>
  );
}

/* ── adapters ────────────────────────────────────────────────────────── */

function projectToRunContext(p: Project): RunContext {
  const statusToneMap: Record<
    string,
    { tone: RunContext["status"]["tone"]; label: string; pulse?: boolean }
  > = {
    running: { tone: "running", label: "running", pulse: true },
    planning: { tone: "running", label: "planning", pulse: true },
    reviewing: { tone: "rework", label: "reviewing" },
    done: { tone: "success", label: "done" },
    failed: { tone: "error", label: "failed" },
    draft: { tone: "neutral", label: "draft" },
  };
  const industryLabel =
    {
      collaboration_saas: "collaboration_saas",
      crm_saas: "crm_saas",
      cross_border_ecommerce_saas: "cross_border_ecommerce_saas",
      edu_saas: "edu_saas",
    }[p.industry] ?? p.industry;
  // 最新 run 的 run_id，没有就 fallback 到 project_id
  const latestRun = p.runs.length > 0 ? p.runs[p.runs.length - 1] : null;
  return {
    projectId: p.project_id,
    projectName: p.project_name,
    runId: latestRun?.run_id ?? p.project_id,
    runNumber: p.runs.length > 0 ? p.runs.length : 1,
    status: statusToneMap[p.status] ?? statusToneMap.draft,
    target: p.target_product,
    competitors: p.competitors,
    templateId: p.report_template_id,
    industry: industryLabel,
    runs: p.runs,
  };
}

/* ── states ──────────────────────────────────────────────────────────── */

function ErrorView({
  error,
  projectId,
  onRetry,
}: {
  error: unknown;
  projectId: string;
  onRetry: () => void;
}) {
  const msg = error instanceof Error ? error.message : String(error);
  const is404 = msg.includes("404");

  return (
    <div className="min-h-full bg-background">
      <GlobalNav />
      <div className="mx-auto max-w-2xl px-10 py-20">
        <div className="rounded-lg border border-error-border bg-error-bg p-6">
          <div className="flex items-start gap-3">
            <AlertTriangleIcon className="h-5 w-5 mt-1 shrink-0 text-error-base" />
            <div>
              <h2 className="text-lg font-semibold text-text-primary">
                {is404 ? "Project 不存在" : "无法加载 workspace"}
              </h2>
              <p className="mt-1 text-sm text-text-secondary">
                project_id: <code className="font-mono">{projectId}</code>
              </p>
              <p className="mt-2 text-xs text-text-muted leading-relaxed break-words">
                {msg}
              </p>
              <div className="mt-4 flex items-center gap-2">
                <Button onClick={onRetry} className="gap-1.5">
                  <PlugZapIcon className="h-3.5 w-3.5" />
                  Retry
                </Button>
                <Button render={<Link href="/projects" />} variant="ghost">
                  ← Projects
                </Button>
                <Button
                  render={<Link href="/projects/demo/runs/01?tab=dag" />}
                  variant="ghost"
                >
                  Open demo
                </Button>
              </div>
              {!is404 ? (
                <p className="mt-4 text-[11px] text-text-muted">
                  检查后端是否已启动：
                  <code className="font-mono">
                    uvicorn backend.api.app:app --reload --port 8000
                  </code>
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function LoadingView() {
  return (
    <div className="min-h-full bg-background">
      <GlobalNav />
      <div className="mx-auto max-w-2xl px-10 py-20">
        <div className="inline-flex items-center gap-2 text-sm text-text-muted">
          <Loader2Icon className="h-4 w-4 animate-spin" />
          <span>正在加载 workspace state…</span>
        </div>
      </div>
    </div>
  );
}
