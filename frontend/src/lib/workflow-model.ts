/**
 * 工作流步进器视图模型 —— 把 RunStateView 投影成 5 阶段步进器要的形状。
 *
 * 原生引擎的 RunStateView.stages 就是 5 个静态阶段（采集/抽取/分析/撰写/质检），
 * 与步进器天然 1:1。这里只做「阶段状态聚合 + 真实时长 + 默认选中」的轻量派生，
 * 不再有旧 DAG 那套 130 行布局算法。
 */
import type {
  RunStateView,
  RunStageView,
  StageInstance,
  StageRevision,
} from "@/lib/api/types";

export type StepStatus =
  | "success"
  | "running"
  | "rework"
  | "error"
  | "pending";

export interface StepVM {
  stage: string; // collect/extract/analyst/reporter/qa
  agent: string;
  label: string; // 中文短名
  isProductStage: boolean;
  status: StepStatus;
  durationMs: number | null;
  productCount: number; // 产品阶段：实例数
  maxRound: number; // 全局阶段：最大轮次（>1 → ↻ vN 角标）
  instances: StageInstance[];
  revisions: StageRevision[];
}

export interface StepperVM {
  steps: StepVM[];
  products: string[];
  runStatus: string; // running/done/failed/aborted
  activeIndex: number; // 默认选中的步骤下标
}

const STAGE_LABEL: Record<string, string> = {
  collect: "信息采集",
  extract: "证据入库",
  analyst: "结构化分析",
  reporter: "报告撰写",
  qa: "质量审查",
};

const PRODUCT_STAGES = new Set(["collect", "extract"]);

/** AgentStatus → 步进器状态。pending 表示该阶段还没有终态记录（未跑 / 进行中）。 */
function mapStatus(s: string): StepStatus {
  if (s === "success" || s === "partial") return "success";
  if (s === "needs_rework") return "rework";
  if (s === "failed") return "error";
  return "pending";
}

type Timed = { started_at: string | null; ended_at: string | null; duration_ms: number | null };

/** 阶段真实时长：最早 started_at → 最晚 ended_at；缺时间戳则退化为 duration_ms 之和。 */
function stageDurationMs(records: Timed[]): number | null {
  const parse = (t: string | null): number | null => {
    if (!t) return null;
    const n = Date.parse(t);
    return Number.isNaN(n) ? null : n;
  };
  const starts = records.map((r) => parse(r.started_at)).filter((n): n is number => n !== null);
  const ends = records.map((r) => parse(r.ended_at)).filter((n): n is number => n !== null);
  if (starts.length > 0 && ends.length > 0) {
    const d = Math.max(...ends) - Math.min(...starts);
    if (d >= 0) return d;
  }
  const sum = records.reduce((acc, r) => acc + (r.duration_ms ?? 0), 0);
  return sum > 0 ? sum : null;
}

/** 单阶段状态聚合：
 * - 产品阶段（instances 已是每产品最新轮）：任一 error → error；任一 rework → rework；否则 success。
 * - 全局阶段（revisions 多轮）：取**最新一轮**的状态（返工已解决的早期轮次不应再标 rework）。
 */
function deriveStageStatus(st: RunStageView): StepStatus {
  if (PRODUCT_STAGES.has(st.stage)) {
    if (st.instances.length === 0) return "pending";
    const statuses = st.instances.map((i) => mapStatus(i.status));
    if (statuses.includes("error")) return "error";
    if (statuses.includes("rework")) return "rework";
    return "success";
  }
  if (st.revisions.length === 0) return "pending";
  const latest = st.revisions.reduce((a, b) => (b.round >= a.round ? b : a));
  return mapStatus(latest.status);
}

export function runViewToStepper(view: RunStateView): StepperVM {
  const steps: StepVM[] = view.stages.map((st) => {
    const isProductStage = PRODUCT_STAGES.has(st.stage);
    const records: Timed[] = isProductStage ? st.instances : st.revisions;
    const maxRound = st.revisions.reduce((m, r) => Math.max(m, r.round), 0);
    return {
      stage: st.stage,
      agent: st.agent,
      label: STAGE_LABEL[st.stage] ?? st.stage,
      isProductStage,
      status: deriveStageStatus(st),
      durationMs: stageDurationMs(records),
      productCount: st.instances.length,
      maxRound,
      instances: st.instances,
      revisions: st.revisions,
    };
  });

  // running 叠加：整条 run 在跑时，按流水线顺序第一个 pending 的阶段就是当前活跃阶段。
  if (view.status === "running") {
    const idx = steps.findIndex((s) => s.status === "pending");
    if (idx >= 0) steps[idx].status = "running";
  }

  // 默认选中：running 阶段；否则最后一个有活动的阶段（done 的 run 落在质检，直接看终判）。
  let activeIndex = steps.findIndex((s) => s.status === "running");
  if (activeIndex < 0) {
    for (let i = steps.length - 1; i >= 0; i--) {
      if (steps[i].instances.length > 0 || steps[i].revisions.length > 0) {
        activeIndex = i;
        break;
      }
    }
  }
  if (activeIndex < 0) activeIndex = 0;

  return {
    steps,
    products: view.products,
    runStatus: view.status,
    activeIndex,
  };
}

/** 格式化时长（与旧 dag-node 一致：<1s ms，<1m s，否则 mm:ss）。 */
export function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

/** 格式化 token（input+output 合计，≥1000 用 k）。 */
export function formatTokens(
  tin: number | null,
  tout: number | null
): string {
  const total = (tin ?? 0) + (tout ?? 0);
  if (total <= 0) return "—";
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`;
  return String(total);
}
