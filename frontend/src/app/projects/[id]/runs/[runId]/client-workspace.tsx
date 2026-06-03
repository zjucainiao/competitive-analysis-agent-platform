"use client";

import { useMemo } from "react";
import Link from "next/link";
import { AlertTriangleIcon, Loader2Icon, PlugZapIcon } from "lucide-react";
import { toast } from "sonner";
import { SidebarShell } from "@/components/layout/sidebar-shell";
import { StatusPill, type StatusTone } from "@/components/layout/status-pill";
import { WorkspaceActions } from "@/components/layout/workspace-actions";
import { WorkspaceTopTabs } from "@/components/layout/workspace-top-tabs";
import { RunHistoryBadge } from "@/components/layout/run-history-badge";
import { DagCanvas } from "@/components/dag";
import { EditPromptDialog } from "@/components/dag/edit-prompt-dialog";
import { ExportMenu } from "@/components/layout/export-menu";
import { ReportLayout } from "@/components/report";
import { TraceLayout } from "@/components/trace";
import { EvidenceLayout } from "@/components/evidence";
import { MetricsLayout } from "@/components/metrics";
import { Button } from "@/components/ui/button";
import { useProjectState, revalidate } from "@/lib/api/hooks";
import { useProjectEvents } from "@/lib/api/ws";
import { apiStateToDagData, findLatestReporter, aggregateEvidences } from "@/lib/api/adapters";
import { DEMO_RUN_CONTEXT } from "@/lib/mock-run";
import { WorkspaceApiProvider } from "@/lib/workspace-api-context";
import type { RunContext } from "@/components/layout/context-bar";
import type { TabKey } from "@/components/layout/tabs-row";
import type { Project, ProjectStateResponse, RunRef } from "@/lib/api/types";
import type { RunStatus } from "@/lib/workspace-actions";

/**
 * Workspace 客户端壳（Phase 2 重构后）。
 *
 *  - SidebarShell 提供左侧栏（5 tab 子菜单 + 当前项目 context）+ 顶部 slim TopBar
 *  - TopBar 左：项目名 + run 编号 + 状态 pill + 历史下拉
 *  - TopBar 右：WorkspaceActions（Publish / Rerun / Share / Export 等）
 *  - 旧 ContextBar + TabsRow 全删
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

  if (isDemoMock) {
    return <DemoMockWorkspace tab={tab} />;
  }
  return <ApiWorkspace projectId={projectId} runId={runId} tab={tab} />;
}

/* ── tab body switch ─────────────────────────────────────────────────── */

function TabBody({
  tab,
  state,
  projectId,
}: {
  tab: TabKey;
  state: ProjectStateResponse | null;
  projectId: string | null;
}) {
  if (!state) {
    // demo mock 路径
    const ctx = DEMO_RUN_CONTEXT;
    return (
      <>
        {tab === "dag" && <DagCanvas />}
        {tab === "report" && <ReportLayout />}
        {tab === "trace" && <TraceLayout />}
        {tab === "evidence" && <EvidenceLayout />}
        {tab === "metrics" && <MetricsLayout ctx={ctx} />}
      </>
    );
  }
  const dagData = apiStateToDagData(state);
  const latestReporter = findLatestReporter(state.outputs);
  const allEvidences = aggregateEvidences(state.outputs);
  const ctx = projectToRunContext(state.project);
  return (
    <>
      {tab === "dag" && (
        <DagCanvas
          nodes={dagData.nodes}
          edges={dagData.edges}
          isLiveData={true}
        />
      )}
      {tab === "report" && (
        <ReportLayout
          apiReporter={latestReporter}
          apiEvidences={allEvidences}
          apiProjectId={projectId ?? undefined}
          apiVerdicts={state.verdicts}
        />
      )}
      {tab === "trace" && <TraceLayout apiState={state} />}
      {tab === "evidence" && (
        <EvidenceLayout apiEvidences={allEvidences} apiOutputs={state.outputs} />
      )}
      {tab === "metrics" && (
        <MetricsLayout ctx={ctx} apiProject={state.project} />
      )}
    </>
  );
}

/* ── demo mock path ──────────────────────────────────────────────────── */

function DemoMockWorkspace({ tab }: { tab: TabKey }) {
  const ctx = DEMO_RUN_CONTEXT;
  return (
    <SidebarShell
      projectName={ctx.projectName}
      topBarLeft={<WorkspaceCrumb ctx={ctx} />}
      topBarRight={<WorkspaceActions status={toneToRunStatus(ctx.status.tone)} />}
      topTabs={<WorkspaceTopTabs />}
    >
      <TabBody tab={tab} state={null} projectId={null} />
      <EditPromptDialog />
      <ExportMenu />
    </SidebarShell>
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
    <ApiWorkspaceShell
      state={state}
      tab={tab}
      wsConnected={wsStatus === "open"}
      projectId={projectId}
    />
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
      <SidebarShell
        projectName={ctx.projectName}
        topBarLeft={<WorkspaceCrumb ctx={ctx} />}
        topBarRight={<WorkspaceActions status={toneToRunStatus(ctx.status.tone)} />}
        topTabs={<WorkspaceTopTabs />}
      >
        {!wsConnected && (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-warning-border bg-warning-bg px-3 py-1.5 text-[11px] text-warning-base">
            <AlertTriangleIcon className="h-3 w-3" />
            <span>WebSocket 未连接 · 每 30 秒 SWR 轮询兜底</span>
          </div>
        )}
        <TabBody tab={tab} state={state} projectId={projectId} />
        <EditPromptDialog />
        <ExportMenu />
      </SidebarShell>
    </WorkspaceApiProvider>
  );
}

/* ── top-bar pieces ──────────────────────────────────────────────────── */

function WorkspaceCrumb({ ctx }: { ctx: RunContext }) {
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs">
      <Link
        href="/projects"
        className="shrink-0 text-text-muted hover:text-text-secondary"
      >
        项目
      </Link>
      <span className="text-text-muted">/</span>
      <span className="truncate text-sm font-medium text-text-primary">
        {ctx.projectName}
      </span>
      <span className="text-text-muted">·</span>
      <span className="shrink-0 font-mono tabular-nums text-text-secondary" data-num>
        run #{String(ctx.runNumber).padStart(2, "0")}
      </span>
      <StatusPill
        tone={ctx.status.tone}
        label={ctx.status.label}
        pulse={ctx.status.pulse}
      />
      {ctx.runs && ctx.runs.length > 0 ? (
        <RunHistoryBadge runs={ctx.runs} activeRunId={ctx.runId} />
      ) : null}
    </div>
  );
}

/* ── adapters ────────────────────────────────────────────────────────── */

function projectToRunContext(p: Project): RunContext {
  const statusToneMap: Record<
    string,
    { tone: StatusTone; label: string; pulse?: boolean }
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
  const latestRun: RunRef | null = p.runs.length > 0 ? p.runs[p.runs.length - 1] : null;
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

function toneToRunStatus(tone: StatusTone): RunStatus {
  switch (tone) {
    case "running":
    case "warning":
      return "running";
    case "rework":
      return "rework";
    case "success":
      return "success";
    case "error":
      return "failed";
    default:
      return "pending";
  }
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
    <SidebarShell
      topBarLeft={
        <div className="text-xs text-text-muted">
          <span className="font-medium text-text-secondary">无法加载</span>
        </div>
      }
    >
      <div className="mx-auto max-w-2xl">
        <div className="card-soft border-error-border bg-error-bg p-6">
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
    </SidebarShell>
  );
}

function LoadingView() {
  return (
    <SidebarShell
      topBarLeft={
        <div className="inline-flex items-center gap-2 text-xs text-text-muted">
          <Loader2Icon className="h-3.5 w-3.5 animate-spin" />
          <span>加载 workspace state…</span>
        </div>
      }
    >
      <div className="mx-auto max-w-2xl">
        <div className="card-soft flex items-center gap-3 px-5 py-4">
          <Loader2Icon className="h-4 w-4 animate-spin text-accent-base" />
          <span className="text-sm text-text-secondary">
            正在从后端拉取 plan / outputs / verdicts…
          </span>
        </div>
      </div>
    </SidebarShell>
  );
}
