"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangleIcon,
  BarChart3Icon,
  FileTextIcon,
  Loader2Icon,
  PlugZapIcon,
} from "lucide-react";
import { toast } from "sonner";
import { SidebarShell } from "@/components/layout/sidebar-shell";
import { WorkspaceEmpty } from "@/components/layout/workspace-empty";
import { WorkspaceShell } from "@/components/layout/workspace-shell";
import { WorkspaceDetailsRail } from "@/components/layout/workspace-details-rail";
import { type StatusTone } from "@/components/layout/status-pill";
import { DagCanvas } from "@/components/dag";
import { WorkflowStepper } from "@/components/dag/workflow-stepper";
import { ExecutionLogCard } from "@/components/dag/execution-log-card";
import { EditPromptDialog } from "@/components/dag/edit-prompt-dialog";
import { ExportMenu } from "@/components/layout/export-menu";
import { ReportLayout } from "@/components/report";
import { TraceLayout } from "@/components/trace";
import { EvidenceLayout } from "@/components/evidence";
import { MetricsLayout } from "@/components/metrics";
import { Button } from "@/components/ui/button";
import { useProjectState, useRunState, revalidate } from "@/lib/api/hooks";
import { useProjectEvents } from "@/lib/api/ws";
import {
  apiStateToDagData,
  findLatestReporter,
  aggregateEvidences,
} from "@/lib/api/adapters";
import { DEMO_RUN_CONTEXT } from "@/lib/mock-run";
import { WorkspaceApiProvider } from "@/lib/workspace-api-context";
import type { RunContext } from "@/components/layout/context-bar";
import type { TabKey } from "@/components/layout/tabs-row";
import type {
  Project,
  ProjectStateResponse,
  RunStateView,
  RunRef,
  DAGNode,
} from "@/lib/api/types";
import type { RunStatus } from "@/lib/workspace-actions";
import type { DagNodeData } from "@/lib/dag-mock";

/**
 * Workspace 客户端壳 —— 三栏 AgentResearch 风格。
 *
 * 布局：
 *  ┌────┬────────────────────────────────┬────────┐
 *  │ 80 │ TopBar（项目 + 进度 + 时长）  │ 320    │
 *  │ ic │ Main（DAG / Report / Trace…）  │ Details│
 *  └────┴────────────────────────────────┴────────┘
 *
 * - 左 80px 窄图标栏（WorkspaceSidebar）
 * - 顶部专属 WorkspaceTopBar：进度条 + 运行时长 + workspace actions
 * - 右 320px 常驻 WorkspaceDetailsRail：选中节点 → 5 tab（概览/输入/输出/日志/证据）
 * - 颜色仍是浅紫 Similarweb 主题，只改结构
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
  runState,
  projectId,
  onSelectNode,
}: {
  tab: TabKey;
  state: ProjectStateResponse | null;
  runState?: RunStateView | null;
  projectId: string | null;
  onSelectNode?: (id: string, data: DagNodeData | null) => void;
}) {
  if (!state) {
    const ctx = DEMO_RUN_CONTEXT;
    return (
      <>
        {tab === "dag" && (
          <div className="flex h-[calc(100vh-7rem)] flex-col gap-4">
            <div className="min-h-0 flex-1">
              <DagCanvas onSelectNode={onSelectNode} />
            </div>
            <div className="shrink-0">
              <ExecutionLogCard />
            </div>
          </div>
        )}
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
        <div className="flex h-[calc(100vh-7rem)] flex-col gap-4">
          <div className="min-h-0 flex-1">
            {runState ? (
              <WorkflowStepper
                // run 切换（run_id 变）→ remount，重置「手动选中阶段」回到跟随实时
                key={runState.run_id ?? projectId ?? "ws"}
                view={runState}
                onOpenDetail={(runRef) => {
                  const rec = dagData.nodes.find((n) => n.id === runRef);
                  onSelectNode?.(runRef, rec?.data ?? null);
                }}
              />
            ) : (
              <DagCanvas
                nodes={dagData.nodes}
                edges={dagData.edges}
                isLiveData={true}
                onSelectNode={onSelectNode}
              />
            )}
          </div>
          <div className="shrink-0">
            <ExecutionLogCard state={state} />
          </div>
        </div>
      )}
      {tab === "report" &&
        (latestReporter ? (
          <ReportLayout
            apiReporter={latestReporter}
            apiEvidences={allEvidences}
            apiProjectId={projectId ?? undefined}
            apiVerdicts={state.verdicts}
            apiTarget={state.project?.target_product}
            apiCompetitors={state.project?.competitors}
          />
        ) : (
          <WorkspaceEmpty
            icon={FileTextIcon}
            title="报告尚未生成"
            desc="等流水线跑到 reporter 节点产出草稿，报告会显示在这里。"
          />
        ))}
      {tab === "trace" && <TraceLayout apiState={state} />}
      {tab === "evidence" && (
        <EvidenceLayout apiEvidences={allEvidences} apiOutputs={state.outputs} />
      )}
      {tab === "metrics" &&
        (state.project?.metrics ? (
          <MetricsLayout ctx={ctx} apiProject={state.project} />
        ) : (
          <WorkspaceEmpty
            icon={BarChart3Icon}
            title="指标尚未生成"
            desc="首次跑完后，这里会显示准确率、覆盖率、Token、人工修正率等指标。"
          />
        ))}
    </>
  );
}

/* ── demo mock path ──────────────────────────────────────────────────── */

function DemoMockWorkspace({ tab }: { tab: TabKey }) {
  const ctx = DEMO_RUN_CONTEXT;
  const [selected, setSelected] = useState<{
    id: string;
    data: DagNodeData;
  } | null>(null);

  return (
    <WorkspaceShell
      projectName={ctx.projectName}
      statusTone={ctx.status.tone}
      statusLabel={ctx.status.label}
      statusPulse={ctx.status.pulse}
      progressDone={8}
      progressTotal={11}
      startedAt={"2026-06-02T10:24:12Z"}
      endedAt={null}
      runStatus={toneToRunStatus(ctx.status.tone)}
      runs={[]}
      activeRunId={ctx.runId}
      detailsRail={
        <WorkspaceDetailsRail
          nodeId={selected?.id ?? null}
          data={selected?.data ?? null}
          onClose={() => setSelected(null)}
        />
      }
    >
      <TabBody
        tab={tab}
        state={null}
        projectId={null}
        onSelectNode={(id, data) =>
          data ? setSelected({ id, data }) : setSelected(null)
        }
      />
      <EditPromptDialog />
      <ExportMenu />
    </WorkspaceShell>
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

  const { status: wsStatus, reconnect: wsReconnect } = useProjectEvents(projectId, {
    onMessage: (msg) => {
      void revalidate.projectState(projectId);
      void revalidate.runState(projectId);
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
    return (
      <ErrorView error={error} projectId={projectId} onRetry={() => mutate()} />
    );
  }
  if (isLoading || !state) {
    return <LoadingView />;
  }

  return (
    <ApiWorkspaceShell
      state={state}
      tab={tab}
      wsConnected={wsStatus === "open"}
      wsStatus={wsStatus}
      onReconnect={wsReconnect}
      projectId={projectId}
    />
  );
}

function ApiWorkspaceShell({
  state,
  tab,
  wsConnected,
  wsStatus,
  onReconnect,
  projectId,
}: {
  state: ProjectStateResponse;
  tab: TabKey;
  wsConnected: boolean;
  wsStatus: string;
  onReconnect: () => void;
  projectId: string;
}) {
  const ctx = useMemo(() => projectToRunContext(state.project), [state.project]);
  // 工作流步进器数据源（原生引擎视图）；与 /state 并存，二者皆来自同一 native run。
  const { data: runState } = useRunState(projectId);
  const [selected, setSelected] = useState<{
    id: string;
    data: DagNodeData;
  } | null>(null);

  const apiContext = useMemo(
    () => ({
      projectId,
      runId: ctx.runId,
      revalidate: () =>
        Promise.all([
          revalidate.projectState(projectId),
          revalidate.project(projectId),
          revalidate.projects(),
        ]),
    }),
    [projectId, ctx.runId]
  );

  // 计算 DAG 整体进度：成功 / 失败 / 跳过算"已结束"，其余算"未结束"
  const { progressDone, progressTotal } = useMemo(
    () => computeProgress(state.plan?.nodes ?? []),
    [state.plan]
  );

  // 找最后一次 run 算运行时长
  const latestRun: RunRef | null =
    state.project.runs.length > 0
      ? state.project.runs[state.project.runs.length - 1]
      : null;

  return (
    <WorkspaceApiProvider value={apiContext}>
      <WorkspaceShell
        projectName={ctx.projectName}
        statusTone={ctx.status.tone}
        statusLabel={ctx.status.label}
        statusPulse={ctx.status.pulse}
        progressDone={progressDone}
        progressTotal={progressTotal}
        startedAt={latestRun?.started_at ?? null}
        endedAt={latestRun?.ended_at ?? null}
        runStatus={toneToRunStatus(ctx.status.tone)}
        runs={ctx.runs}
        activeRunId={ctx.runId}
        detailsRail={
          <WorkspaceDetailsRail
            nodeId={selected?.id ?? null}
            data={selected?.data ?? null}
            projectId={projectId}
            state={state}
            onClose={() => setSelected(null)}
          />
        }
      >
        {!wsConnected && (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-warning-border bg-warning-bg px-3 py-1.5 text-[11px] text-warning-base">
            <AlertTriangleIcon className="h-3 w-3 shrink-0" />
            <span>
              {wsStatus === "connecting"
                ? "正在连接实时进度…"
                : "实时连接已断开，已转为定时刷新"}
            </span>
            {wsStatus !== "connecting" && (
              <button
                type="button"
                onClick={onReconnect}
                className="ml-auto rounded px-1.5 py-0.5 font-medium underline-offset-2 hover:underline"
              >
                重连
              </button>
            )}
          </div>
        )}
        <TabBody
          tab={tab}
          state={state}
          runState={runState}
          projectId={projectId}
          onSelectNode={(id, data) =>
            data ? setSelected({ id, data }) : setSelected(null)
          }
        />
        <EditPromptDialog />
        <ExportMenu />
      </WorkspaceShell>
    </WorkspaceApiProvider>
  );
}

/* ── progress helper ─────────────────────────────────────────────────── */

function computeProgress(nodes: DAGNode[]): {
  progressDone: number;
  progressTotal: number;
} {
  if (nodes.length === 0) return { progressDone: 0, progressTotal: 0 };
  const done = nodes.filter(
    (n) =>
      n.status === "success" ||
      n.status === "failed" ||
      n.status === "skipped"
  ).length;
  return { progressDone: done, progressTotal: nodes.length };
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
  const latestRun: RunRef | null =
    p.runs.length > 0 ? p.runs[p.runs.length - 1] : null;
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
            正在从后端拉取项目状态…
          </span>
        </div>
      </div>
    </SidebarShell>
  );
}
