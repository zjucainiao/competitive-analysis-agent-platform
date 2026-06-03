/**
 * Agent → Pipeline Phase 映射。
 *
 * 用户视角呈现「4 阶段流水线」，但内部仍是 5 个专职 Agent。两个理由：
 *   1. Collector 和 Extractor 在 DAG 上仍是两个独立节点（独立失败 / 独立重试 /
 *      独立反馈环），用户看到的应该是 "采集与结构化" 一个阶段
 *   2. DAG 拓扑 / 节点详情 / observability 仍按 agent 粒度展示，但 swimlane /
 *      徽章 / 副标题用 phase 维度
 *
 * 映射规则锁定（不要轻易改顺序，前端 sparkline / 颜色都按 order 取色）：
 *   order=1  采集与结构化  (collector + extractor)
 *   order=2  分析          (analyst)
 *   order=3  撰写          (reporter)
 *   order=4  质检          (qa)
 *   order=0  调度          (start / end / fork / join 等控制节点)
 */

export type PhaseId =
  | "collect_extract"
  | "analyze"
  | "write"
  | "review"
  | "control";

export interface PhaseMeta {
  id: PhaseId;
  /** 排序号（1-4 是业务阶段，0 是控制节点） */
  order: number;
  /** UI 主标签（中文短词） */
  label: string;
  /** 简短描述，节点详情侧栏 / tooltip 用 */
  description: string;
  /** 节点边框 / 徽章颜色 token（与 design system 对齐） */
  tone: "accent" | "viz-1" | "viz-2" | "viz-3" | "muted";
  /** Lucide 图标名（实际组件按需 import，避免循环依赖） */
  iconName: "Database" | "Brain" | "PenLine" | "Shield" | "Workflow";
}

export const PHASE_BY_AGENT: Record<string, PhaseMeta> = {
  collector: {
    id: "collect_extract",
    order: 1,
    label: "采集与结构化",
    description: "公开来源抓取 + LLM 抽取到结构化 Schema + 证据链编织",
    tone: "viz-1",
    iconName: "Database",
  },
  extractor: {
    id: "collect_extract",
    order: 1,
    label: "采集与结构化",
    description: "公开来源抓取 + LLM 抽取到结构化 Schema + 证据链编织",
    tone: "viz-1",
    iconName: "Database",
  },
  analyst: {
    id: "analyze",
    order: 2,
    label: "分析",
    description: "多维度跨产品对比 / 单产品深度调研 · 每条 claim 绑 evidence",
    tone: "viz-2",
    iconName: "Brain",
  },
  reporter: {
    id: "write",
    order: 3,
    label: "撰写",
    description: "结构化报告生成 · 数字 / 引用强制校验 · 反幻觉自修复",
    tone: "viz-3",
    iconName: "PenLine",
  },
  qa: {
    id: "review",
    order: 4,
    label: "质检",
    description: "6 维度自动审查 · 不合理触发反馈环 · 用户介入可 Override",
    tone: "accent",
    iconName: "Shield",
  },
  control: {
    id: "control",
    order: 0,
    label: "调度",
    description: "Orchestrator 编排节点（START / END / FORK / JOIN）",
    tone: "muted",
    iconName: "Workflow",
  },
};

/** 4 阶段顺序（用于 legend / phase indicator）。control 不在里面。 */
export const ORDERED_PHASES: PhaseMeta[] = [
  PHASE_BY_AGENT.collector,
  PHASE_BY_AGENT.analyst,
  PHASE_BY_AGENT.reporter,
  PHASE_BY_AGENT.qa,
];

/** Agent name → phase meta；未知 agent 落到 "control"。 */
export function phaseOf(agentName: string | undefined | null): PhaseMeta {
  if (!agentName) return PHASE_BY_AGENT.control;
  return PHASE_BY_AGENT[agentName] ?? PHASE_BY_AGENT.control;
}
