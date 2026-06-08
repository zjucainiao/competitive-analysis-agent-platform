"use client";

import { Fragment, useState } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import {
  CheckIcon,
  LoaderIcon,
  RotateCwIcon,
  OctagonXIcon,
  FileTextIcon,
  ClockIcon,
  HashIcon,
  ChevronRightIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  runViewToStepper,
  formatDuration,
  formatTokens,
  formatTokenTotal,
  formatConfidence,
  type StepVM,
  type StepStatus,
} from "@/lib/workflow-model";
import {
  nodeActionsFor,
  type ActionDef,
  type RunStatus,
} from "@/lib/workspace-actions";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import type {
  RunStateView,
  StageInstance,
  AnyAgentOutput,
  ReporterOutput,
  QAOutput,
  AnalystOutput,
  CollectorOutput,
  ExtractorOutput,
} from "@/lib/api/types";

/**
 * 工作流视图 —— 横向 5 阶段步进器 + 选中阶段内联详情。
 *
 * 取代旧的 React Flow 大卡画布：原生引擎就是 5 个固定阶段（采集→抽取→分析→撰写→
 * 质检），步进器与之 1:1，永不溢出。数据直接读 RunStateView.stages。
 *
 * 交互：
 *  - 点阶段 → 下方内联详情切到该阶段（默认选中当前活跃阶段，未手动点选前跟随实时进度）
 *  - 详情里点产品 / 轮次的「详情」→ onOpenDetail(run_ref) 驱动右侧 320 栏深挖
 */

const DOT_BG: Record<StepStatus, string> = {
  success: "bg-success-base text-text-inverse border-success-base",
  running: "bg-running-base text-text-inverse border-running-base",
  rework: "bg-rework-base text-text-inverse border-rework-base",
  error: "bg-error-base text-text-inverse border-error-base",
  pending: "bg-bg-raised text-text-muted border-border-default",
};

/** 小圆点（详情里每产品行）的纯底色。 */
const DOT_SOLID: Record<StepStatus, string> = {
  success: "bg-success-base",
  running: "bg-running-base",
  rework: "bg-rework-base",
  error: "bg-error-base",
  pending: "bg-border-strong",
};

/** 连接线配色：与圆点状态语义一致（success 绿 / rework 橙 / error 红 /
 * running 渐变 / pending 灰虚线），避免返工/失败阶段后的线被画成「等待」。 */
const CONNECTOR: Record<StepStatus, string> = {
  success: "bg-success-base",
  rework: "bg-rework-base",
  error: "bg-error-base",
  running: "bg-gradient-to-r from-running-base to-border-default",
  pending:
    "bg-[repeating-linear-gradient(90deg,var(--color-border-default)_0_6px,transparent_6px_12px)]",
};

const TEXT_TONE: Record<StepStatus, string> = {
  success: "text-success-base",
  running: "text-running-base",
  rework: "text-rework-base",
  error: "text-error-base",
  pending: "text-text-muted",
};

const STATUS_LABEL: Record<StepStatus, string> = {
  success: "已完成",
  running: "运行中",
  rework: "需返工",
  error: "失败",
  pending: "等待中",
};

function statusToRun(s: StepStatus): RunStatus {
  switch (s) {
    case "success":
      return "success";
    case "running":
      return "running";
    case "rework":
      return "rework";
    case "error":
      return "failed";
    default:
      return "pending";
  }
}

function StepDot({ status, order }: { status: StepStatus; order: number }) {
  const cls = "h-3.5 w-3.5";
  let icon: React.ReactNode = (
    <span className="font-mono text-xs font-semibold tabular-nums" data-num>
      {order}
    </span>
  );
  if (status === "success") icon = <CheckIcon className={cls} />;
  else if (status === "running")
    icon = <LoaderIcon className={cn(cls, "animate-spin")} />;
  // 返工 = 重处理(↻ 旋转)，不是警告(⚠)——避免「系统在自我改进」被误读成报错。
  else if (status === "rework") icon = <RotateCwIcon className={cls} />;
  else if (status === "error") icon = <OctagonXIcon className={cls} />;

  return (
    <span
      className={cn(
        "relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 shadow-card transition-colors duration-180",
        DOT_BG[status],
        status === "running" && "animate-pulse-soft"
      )}
    >
      {icon}
    </span>
  );
}

/* ── stepper ─────────────────────────────────────────────────────────── */

export function WorkflowStepper({
  view,
  onOpenDetail,
}: {
  view: RunStateView;
  onOpenDetail?: (runRef: string) => void;
}) {
  const vm = runViewToStepper(view);
  // 未手动点选前跟随实时活跃阶段；点选后固定。
  const [picked, setPicked] = useState<number | null>(null);
  const active = picked ?? vm.activeIndex;
  const step = vm.steps[active];

  return (
    <div className="flex h-full flex-col gap-4">
      {/* 步进器条 */}
      <ol
        className="flex items-start rounded-xl border border-border-subtle bg-bg-raised px-4 py-5 shadow-card"
        aria-label="工作流阶段"
      >
        {vm.steps.map((s, i) => {
          const isLast = i === vm.steps.length - 1;
          const isActive = i === active;
          return (
            <Fragment key={s.stage}>
              <li className="relative flex min-w-0 flex-1 flex-col items-center">
                {/* 连接线（到下一步）：配色随本步状态，居中于圆点中线 */}
                {!isLast ? (
                  <span
                    className={cn(
                      "absolute left-1/2 top-5 -z-0 h-0.5 w-full -translate-y-1/2",
                      CONNECTOR[s.status]
                    )}
                    aria-hidden
                  />
                ) : null}

                <button
                  type="button"
                  // 再次点击当前选中阶段 → 取消固定，回到跟随实时活跃阶段
                  onClick={() => setPicked((p) => (p === i ? null : i))}
                  className={cn(
                    "group flex flex-col items-center gap-1.5 rounded-lg px-2 py-1.5 transition-colors duration-120",
                    isActive ? "bg-accent-bg/50" : "hover:bg-bg-hover"
                  )}
                  aria-current={isActive ? "step" : undefined}
                  aria-label={`第 ${i + 1} 步 · ${s.label} · ${STATUS_LABEL[s.status]}`}
                >
                  <StepDot status={s.status} order={i + 1} />
                  <span
                    title={s.label}
                    className={cn(
                      "max-w-[96px] truncate text-xs font-semibold",
                      isActive ? "text-text-primary" : "text-text-secondary"
                    )}
                  >
                    {s.label}
                  </span>
                  <span
                    className={cn("text-[11px] font-medium", TEXT_TONE[s.status])}
                  >
                    {s.status === "pending"
                      ? "待"
                      : s.status === "running"
                        ? "运行中"
                        : formatDuration(s.durationMs)}
                  </span>
                  {/* 副标签：多产品 / 置信度 / 返工轮次 */}
                  <span className="flex h-4 items-center gap-1">
                    {s.isProductStage && s.productCount > 0 ? (
                      <span
                        title={`${s.productCount} 个产品`}
                        className="rounded-pill bg-bg-sunken px-1.5 text-[10px] text-text-muted"
                      >
                        {s.productCount} 产品
                      </span>
                    ) : null}
                    {s.status !== "pending" &&
                    s.status !== "running" &&
                    formatConfidence(s.confidence) ? (
                      <span
                        title="该阶段平均置信度（Agent 自评成色，仅供参考）"
                        className="rounded-pill bg-bg-sunken px-1.5 text-[10px] text-text-muted tabular-nums"
                      >
                        置信 {formatConfidence(s.confidence)}
                      </span>
                    ) : null}
                    {s.maxRound > 1 ? (
                      <span
                        title={`已返工至第 ${s.maxRound} 轮`}
                        className="rounded-pill bg-rework-bg px-1.5 text-[10px] font-medium text-rework-base"
                      >
                        ↻ v{s.maxRound}
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            </Fragment>
          );
        })}
      </ol>

      {/* 选中阶段内联详情 */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {step ? (
          <StageDetail
            step={step}
            outputs={view.outputs}
            onOpenDetail={onOpenDetail}
          />
        ) : null}
      </div>
    </div>
  );
}

/* ── inline detail ───────────────────────────────────────────────────── */

function StageDetail({
  step,
  outputs,
  onOpenDetail,
}: {
  step: StepVM;
  outputs: Record<string, AnyAgentOutput>;
  onOpenDetail?: (runRef: string) => void;
}) {
  const api = useWorkspaceApi();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const goToTab = (tab: string) => {
    const sp = new URLSearchParams(searchParams.toString());
    sp.set("tab", tab);
    router.push(`${pathname}?${sp.toString()}`);
  };

  // 全局阶段取最新一轮；产品阶段详情走 instances 列表（下方单独渲染）。
  const latestRev =
    step.revisions.length > 0
      ? step.revisions.reduce((a, b) => (b.round >= a.round ? b : a))
      : null;
  const headerRunRef = latestRev?.run_ref ?? step.instances[0]?.run_ref ?? null;
  const headerOut = headerRunRef ? outputs[headerRunRef] : undefined;

  const records = step.isProductStage ? step.instances : step.revisions;
  const totalTokens = records.reduce(
    (a, r) => a + (r.tokens_input ?? 0) + (r.tokens_output ?? 0),
    0
  );

  const actions = headerRunRef
    ? nodeActionsFor({
        nodeId: headerRunRef,
        label: step.label,
        agentName: step.agent,
        status: statusToRun(step.status),
        api,
      })
    : [];

  return (
    <section className="rounded-xl border border-border-subtle bg-bg-raised p-5 shadow-card">
      {/* header */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border-subtle pb-3">
        <h2 className="text-base font-semibold text-text-primary">
          {step.label}
        </h2>
        <span className={cn("text-xs font-medium", TEXT_TONE[step.status])}>
          {STATUS_LABEL[step.status]}
        </span>
        {step.maxRound > 1 ? (
          <span className="rounded-pill bg-rework-bg px-2 py-0.5 text-[11px] font-medium text-rework-base">
            第 {step.maxRound} 轮 · QA 返工
          </span>
        ) : null}
        <span className="ml-auto flex items-center gap-3 font-mono text-[11px] text-text-muted">
          <span className="inline-flex items-center gap-1">
            <ClockIcon className="h-3 w-3" />
            {formatDuration(step.durationMs)}
          </span>
          {totalTokens > 0 ? (
            <span className="inline-flex items-center gap-1">
              <HashIcon className="h-3 w-3" />
              {formatTokenTotal(totalTokens)} tok
            </span>
          ) : null}
        </span>
      </header>

      <div className="mt-4 space-y-4">
        {step.isProductStage ? (
          <ProductInstances
            instances={step.instances}
            outputs={outputs}
            onOpenDetail={onOpenDetail}
          />
        ) : (
          <GlobalRevisionDetail
            step={step}
            out={headerOut}
            runRef={headerRunRef}
            onOpenDetail={onOpenDetail}
            onGoToTab={goToTab}
          />
        )}

        {/* 操作按钮 */}
        {actions.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
            {actions.map((a) => (
              <ActionButton key={a.id} action={a} />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

/* 产品阶段（采集 / 抽取）：每产品一行 */
function ProductInstances({
  instances,
  outputs,
  onOpenDetail,
}: {
  instances: StageInstance[];
  outputs: Record<string, AnyAgentOutput>;
  onOpenDetail?: (runRef: string) => void;
}) {
  if (instances.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        该阶段尚未开始 · 等上游产出后并行采集各产品。
      </p>
    );
  }
  return (
    <div>
      <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
        {instances.length} 个产品
      </div>
      <ul className="divide-y divide-border-subtle rounded-lg border border-border-subtle">
        {instances.map((inst) => {
          const out = inst.run_ref ? outputs[inst.run_ref] : undefined;
          const metric = productMetric(out);
          const stepStatus = mapInstStatus(inst.status);
          return (
            <li
              key={inst.run_ref ?? inst.product}
              className="flex items-center gap-3 px-3 py-2.5"
            >
              <span
                className={cn("h-2 w-2 shrink-0 rounded-pill", DOT_SOLID[stepStatus])}
              />
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">
                {inst.product}
              </span>
              {formatConfidence(inst.confidence) ? (
                <span
                  title="该产品本阶段的平均置信度（Agent 自评成色）"
                  className="shrink-0 rounded-pill bg-bg-sunken px-1.5 text-[10px] text-text-muted tabular-nums"
                >
                  置信 {formatConfidence(inst.confidence)}
                </span>
              ) : null}
              {metric ? (
                <span className="shrink-0 font-mono text-xs text-text-secondary tabular-nums">
                  {metric}
                </span>
              ) : null}
              <span className="shrink-0 font-mono text-[11px] text-text-muted tabular-nums">
                {formatTokens(inst.tokens_input, inst.tokens_output)}
              </span>
              {inst.run_ref && onOpenDetail ? (
                <button
                  type="button"
                  onClick={() => onOpenDetail(inst.run_ref!)}
                  className="inline-flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-accent-base hover:text-accent-hover"
                >
                  详情
                  <ChevronRightIcon className="h-3 w-3" />
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* 全局阶段（分析 / 撰写 / 质检）：产物摘要 + self-critique */
function GlobalRevisionDetail({
  step,
  out,
  runRef,
  onOpenDetail,
  onGoToTab,
}: {
  step: StepVM;
  out: AnyAgentOutput | undefined;
  runRef: string | null;
  onOpenDetail?: (runRef: string) => void;
  onGoToTab?: (tab: string) => void;
}) {
  if (!out) {
    return (
      <p className="text-sm text-text-muted">
        该阶段尚未产出 · 等上游阶段完成后开始。
      </p>
    );
  }
  const summary = globalSummary(step.stage, out);
  const critique = out.self_critique?.trim();

  return (
    <div className="space-y-4">
      {summary ? (
        <div className="flex items-start gap-2 rounded-lg bg-bg-sunken/60 px-3 py-2.5">
          <FileTextIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-muted" />
          <span className="text-sm text-text-secondary">{summary}</span>
        </div>
      ) : null}

      {critique ? (
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-text-muted">
            自我评价
          </div>
          <p className="text-sm leading-relaxed text-text-secondary">
            {critique}
          </p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {step.stage === "reporter" && onGoToTab ? (
          <DetailLink label="打开报告" onClick={() => onGoToTab("report")} />
        ) : null}
        {step.stage === "qa" && onGoToTab ? (
          <DetailLink label="查看质检详情" onClick={() => onGoToTab("trace")} />
        ) : null}
        {runRef && onOpenDetail ? (
          <DetailLink
            label="完整输入 / 输出 / LLM 调用"
            onClick={() => onOpenDetail(runRef)}
          />
        ) : null}
      </div>
    </div>
  );
}

function DetailLink({
  label,
  onClick,
}: {
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-0.5 text-[11px] font-medium text-accent-base hover:text-accent-hover"
    >
      {label}
      <ChevronRightIcon className="h-3 w-3" />
    </button>
  );
}

function ActionButton({ action }: { action: ActionDef }) {
  const Icon = action.icon;
  const variant: string =
    action.variant === "primary"
      ? "bg-accent-base text-text-inverse border-accent-base hover:bg-accent-hover"
      : action.variant === "destructive"
        ? "bg-error-bg text-error-base border-error-border hover:bg-error-bg/80"
        : "bg-bg-raised text-text-secondary border-border-default hover:bg-bg-hover hover:text-text-primary";
  return (
    <button
      type="button"
      onClick={action.run}
      title={action.hint ?? action.label}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[11px] font-medium transition-colors duration-120",
        variant
      )}
    >
      <Icon className="h-3 w-3" />
      <span>{action.label}</span>
    </button>
  );
}

/* ── output summarizers ──────────────────────────────────────────────── */

function mapInstStatus(s: string): StepStatus {
  // needs_rework 是 Agent 自评（信息性），不渲染成返工警告 —— 视作已完成，
  // 成色交由置信度标注体现（与 workflow-model.mapStatus 保持一致）。
  if (s === "success" || s === "partial" || s === "needs_rework") return "success";
  if (s === "failed") return "error";
  return "pending";
}

/** 产品实例的关键产出（文档 / 证据数）。 */
function productMetric(out: AnyAgentOutput | undefined): string | null {
  if (!out) return null;
  if ("raw_sources" in out && Array.isArray((out as CollectorOutput).raw_sources)) {
    return `${(out as CollectorOutput).raw_sources.length} 文档`;
  }
  if ("evidences" in out && Array.isArray((out as ExtractorOutput).evidences)) {
    return `${(out as ExtractorOutput).evidences.length} 证据`;
  }
  return null;
}

/** QA 终判枚举 → 中文（避免在中文 UI 暴露 needs_revision 等裸枚举值）。 */
const QA_STATUS_LABEL: Record<string, string> = {
  pass: "通过",
  needs_revision: "需修订",
  reject: "驳回",
};

/** 全局阶段产物一句话摘要。 */
function globalSummary(stage: string, out: AnyAgentOutput): string | null {
  if (stage === "analyst" && "result" in out && (out as AnalystOutput).result) {
    const dims = Object.keys((out as AnalystOutput).result.dimensions ?? {});
    return `已分析 ${dims.length} 个维度`;
  }
  if (stage === "reporter" && "draft" in out && (out as ReporterOutput).draft) {
    const d = (out as ReporterOutput).draft;
    return `报告草稿 v${d.version} · ${d.sections.length} 个章节`;
  }
  if (stage === "qa" && "verdict" in out && (out as QAOutput).verdict) {
    const v = (out as QAOutput).verdict;
    const label = QA_STATUS_LABEL[v.overall_status] ?? v.overall_status;
    return `质检结论：${label} · ${v.issues.length} 处问题`;
  }
  return null;
}
