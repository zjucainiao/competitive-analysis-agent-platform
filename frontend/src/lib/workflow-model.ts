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
  QAVerdict,
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
  confidence: number | null; // 阶段平均置信度（0–1），用于平静标注「成色」而非警告
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

/** QA routing 的 ``target_agent`` → 阶段 id（与后端 routing._AGENT_TO_ENTRY 同义）。 */
const AGENT_TO_STAGE: Record<string, string> = {
  collector: "collect",
  extractor: "extract",
  analyst: "analyst",
  reporter: "reporter",
};

/** 上游→下游优先序（与后端 routing._AGENT_ORDER 一致）：多条 routing 取**最上游**单一
 * 目标——最上游重跑必带动其下游一起重走。 */
const REWORK_AGENT_ORDER = ["collector", "extractor", "analyst", "reporter"];

/** 触发「当前返工轮」的最上游目标阶段 id（取最后一条 **blocking** verdict 的 routing）。
 *  无 verdict / 非阻塞 / 无 routing → null（非返工，或无法定位起点）。 */
function reworkTargetStage(verdicts: QAVerdict[]): string | null {
  if (verdicts.length === 0) return null;
  const last = verdicts[verdicts.length - 1];
  if (!last?.blocking) return null;
  const targets = new Set<string>((last.routing ?? []).map((r) => r.target_agent));
  const chosen = REWORK_AGENT_ORDER.find((a) => targets.has(a));
  return chosen ? AGENT_TO_STAGE[chosen] : null;
}

/** running 叠加 + 返工「重新走一遍」帧（就地改写 steps 状态）。
 *
 * - **首轮**（currentRound≤1）：按流水线顺序点亮第一个 pending 阶段为 running。
 * - **返工轮**（currentRound≥2，QA 已打回正在回灌）：从 rework 目标阶段起，把
 *   「目标 → 质检」这段**本轮尚未重跑**的阶段重置——最上游未追平者标 running、其后标
 *   pending，让用户实时看到管线**退回该阶段重走一遍**（而不是停在上一轮的全绿）。
 *   目标**上游**的阶段（本轮不重跑，产物复用）保持原状态；本轮**已跑完**的阶段保留其
 *   本轮终态（success/error）。
 *
 * 关键依据：原生图所有节点 ``round = qa_round + 1``，故运行中的返工轮号即
 * ``qa_round + 1``；``StepVM.maxRound`` < 该轮号 ⇒ 本阶段本轮还没重跑。 */
function applyRunningFrontier(steps: StepVM[], view: RunStateView): void {
  if (view.status !== "running") return;
  const currentRound = (view.qa_round ?? 0) + 1;

  // 首轮：维持原「第一个 pending → running」语义（零回归）。
  if (currentRound <= 1) {
    const idx = steps.findIndex((s) => s.status === "pending");
    if (idx >= 0) steps[idx].status = "running";
    return;
  }

  // 返工轮：定位重走起点（最后一条 blocking verdict 的最上游 routing 目标）。
  const target = reworkTargetStage(view.verdicts);
  const startIdx = target
    ? steps.findIndex((s) => s.stage === target)
    : // 兜底：routing 无法解析时，退化为「第一个未追平本轮的阶段」当起点。
      steps.findIndex((s) => s.maxRound < currentRound);
  if (startIdx < 0) {
    const idx = steps.findIndex((s) => s.status === "pending");
    if (idx >= 0) steps[idx].status = "running";
    return;
  }

  let runningPlaced = false;
  for (let i = startIdx; i < steps.length; i++) {
    if (steps[i].maxRound >= currentRound) continue; // 本轮已跑完 → 保留终态
    if (!runningPlaced) {
      steps[i].status = "running";
      runningPlaced = true;
    } else {
      steps[i].status = "pending";
    }
  }
}

/** AgentStatus → 步进器状态。pending 表示该阶段还没有终态记录（未跑 / 进行中）。
 *
 * 注意：``needs_rework`` 是 **Agent 对自己产出的自评**（置信度偏低 / 多源字段冲突
 * 等），属信息性提示，**并不触发 LangGraph 回环**——节点照常算完成、产出原样传下游。
 * 真正的 QA 返工由「轮次 > 1」(``↻ vN`` 角标) 表达。故这里把自评的 ``needs_rework``
 * 视作 **已完成**（success），不再渲染成橙色感叹号警告；自评的「成色」改由置信度标注
 * 体现（见步进器 / 产品行的「置信」标签）。 */
function mapStatus(s: string): StepStatus {
  if (s === "success" || s === "partial" || s === "needs_rework") return "success";
  if (s === "failed") return "error";
  return "pending";
}

type Timed = { started_at: string | null; ended_at: string | null; duration_ms: number | null };

/** 阶段真实时长：**全部记录都已结束**才用「最早 started_at → 最晚 ended_at」跨度
 * （否则进行中阶段会被低估成「到第一个完成项为止」、还会随轮询跳变）；否则退化为
 * 已有 duration_ms 之和，再否则返回 null（前端显示 —）。 */
function stageDurationMs(records: Timed[]): number | null {
  if (records.length === 0) return null;
  const parse = (t: string | null): number | null => {
    if (!t) return null;
    const n = Date.parse(t);
    return Number.isNaN(n) ? null : n;
  };
  const starts = records.map((r) => parse(r.started_at)).filter((n): n is number => n !== null);
  const ends = records.map((r) => parse(r.ended_at)).filter((n): n is number => n !== null);
  // 只有「每条记录都有 ended_at」(阶段确已全部结束) 时才用墙钟跨度
  if (starts.length > 0 && ends.length === records.length) {
    const d = Math.max(...ends) - Math.min(...starts);
    if (d >= 0) return d;
  }
  const sum = records.reduce((acc, r) => acc + (r.duration_ms ?? 0), 0);
  return sum > 0 ? sum : null;
}

/** 阶段平均置信度：取所有记录非空 confidence 的均值（无则 null）。 */
function stageConfidence(records: { confidence: number | null }[]): number | null {
  const cs = records
    .map((r) => r.confidence)
    .filter((c): c is number => typeof c === "number");
  if (cs.length === 0) return null;
  return cs.reduce((a, b) => a + b, 0) / cs.length;
}

/** 单阶段状态聚合：
 * - 产品阶段（instances 已是每产品最新轮）：任一 error → error；任一 rework → rework；
 *   **未跑满全部产品且整条 run 仍在跑 → pending**（仍在并行采集，交给 running 叠加点亮，
 *   避免「跑了 1/N 就显示已完成、running 错误前移到下游」）；否则 success。
 * - 全局阶段（revisions 多轮）：取**最新一轮**的状态（返工已解决的早期轮次不应再标 rework）。
 */
function deriveStageStatus(
  st: RunStageView,
  productTotal: number,
  runStatus: string
): StepStatus {
  if (PRODUCT_STAGES.has(st.stage)) {
    if (st.instances.length === 0) return "pending";
    const statuses = st.instances.map((i) => mapStatus(i.status));
    if (statuses.includes("error")) return "error";
    if (statuses.includes("rework")) return "rework";
    if (runStatus === "running" && st.instances.length < productTotal) {
      return "pending"; // 还没采满全部产品 → 视为进行中
    }
    return "success";
  }
  if (st.revisions.length === 0) return "pending";
  const latest = st.revisions.reduce((a, b) => (b.round >= a.round ? b : a));
  return mapStatus(latest.status);
}

export function runViewToStepper(view: RunStateView): StepperVM {
  const productTotal = view.products.length;
  const steps: StepVM[] = view.stages.map((st) => {
    const isProductStage = PRODUCT_STAGES.has(st.stage);
    const records: Timed[] = isProductStage ? st.instances : st.revisions;
    // 产品阶段返工轮次来自 instances.revision；全局阶段来自 revisions.round
    const maxRound = isProductStage
      ? st.instances.reduce((m, i) => Math.max(m, i.revision ?? 1), 0)
      : st.revisions.reduce((m, r) => Math.max(m, r.round), 0);
    return {
      stage: st.stage,
      agent: st.agent,
      label: STAGE_LABEL[st.stage] ?? st.stage,
      isProductStage,
      status: deriveStageStatus(st, productTotal, view.status),
      durationMs: stageDurationMs(records),
      confidence: stageConfidence(isProductStage ? st.instances : st.revisions),
      productCount: st.instances.length,
      maxRound,
      instances: st.instances,
      revisions: st.revisions,
    };
  });

  // running 叠加：首轮点亮第一个 pending；返工轮则把管线「退回 rework 目标重走一遍」
  // （见 applyRunningFrontier）——修复返工时全阶段停在上一轮全绿、毫无实时返工观感。
  applyRunningFrontier(steps, view);

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

/** 格式化置信度为百分比（0–1 → "69%"）；null 返回 null（前端不渲染）。 */
export function formatConfidence(c: number | null): string | null {
  if (c == null || Number.isNaN(c)) return null;
  return `${Math.round(c * 100)}%`;
}

/** 格式化 token 总量（≥1000 用 k）。 */
export function formatTokenTotal(total: number): string {
  if (total <= 0) return "—";
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k`;
  return String(total);
}

/** 格式化 token（input+output 合计）。 */
export function formatTokens(
  tin: number | null,
  tout: number | null
): string {
  return formatTokenTotal((tin ?? 0) + (tout ?? 0));
}
