import type { DagNodeStatus } from "./dag-mock";

/**
 * DAG「▶ Play」回放的时间轴。
 *
 * 每一格代表一个相位（动画 step）。点击 Play → setInterval 每 1.5s 推进一格。
 *
 * Phase 0 = 全 pending（项目刚创建）
 * Phase 6 = 当前 snapshot（QA rework + reporter_v2 running）
 *
 * 每个 phase 列出**该 phase 的整体节点状态**（不是 diff，绝对值），
 * 渲染时直接覆盖 DEMO_DAG_NODES 的 status。
 */

export interface DagPhase {
  label: string; // 时间戳，显示在滑块上
  description: string;
  status: Record<string, DagNodeStatus>;
}

const ALL = [
  "start",
  "collect.notion",
  "collect.clickup",
  "collect.asana",
  "extract.notion",
  "extract.clickup",
  "extract.asana",
  "analyst",
  "reporter",
  "qa",
  "reporter_v2",
  "qa_v2",
  "end",
] as const;

function neutralAll(): Record<string, DagNodeStatus> {
  return Object.fromEntries(ALL.map((id) => [id, "neutral"]));
}

function setMany(
  base: Record<string, DagNodeStatus>,
  overrides: Record<string, DagNodeStatus>
): Record<string, DagNodeStatus> {
  return { ...base, ...overrides };
}

export const DAG_PHASES: DagPhase[] = [
  {
    label: "0:00",
    description: "Run dispatched · awaiting upstream",
    status: neutralAll(),
  },
  {
    label: "0:02",
    description: "Collectors running in parallel",
    status: setMany(neutralAll(), {
      start: "success",
      "collect.notion": "running",
      "collect.clickup": "running",
      "collect.asana": "running",
    }),
  },
  {
    label: "0:07",
    description: "Collectors done · extractors starting",
    status: setMany(neutralAll(), {
      start: "success",
      "collect.notion": "success",
      "collect.clickup": "success",
      "collect.asana": "success",
      "extract.notion": "running",
      "extract.clickup": "running",
      "extract.asana": "running",
    }),
  },
  {
    label: "0:20",
    description: "Extractors done · analyst comparing dimensions",
    status: setMany(neutralAll(), {
      start: "success",
      "collect.notion": "success",
      "collect.clickup": "success",
      "collect.asana": "success",
      "extract.notion": "success",
      "extract.clickup": "success",
      "extract.asana": "success",
      analyst: "running",
    }),
  },
  {
    label: "0:37",
    description: "Reporter composing draft v1",
    status: setMany(neutralAll(), {
      start: "success",
      "collect.notion": "success",
      "collect.clickup": "success",
      "collect.asana": "success",
      "extract.notion": "success",
      "extract.clickup": "success",
      "extract.asana": "success",
      analyst: "success",
      reporter: "running",
    }),
  },
  {
    label: "0:56",
    description: "QA checking 6 dimensions · 2 issues found",
    status: setMany(neutralAll(), {
      start: "success",
      "collect.notion": "success",
      "collect.clickup": "success",
      "collect.asana": "success",
      "extract.notion": "success",
      "extract.clickup": "success",
      "extract.asana": "success",
      analyst: "success",
      reporter: "success",
      qa: "running",
    }),
  },
  {
    label: "1:04",
    description:
      "Feedback loop · reporter_v2 spawned with QA payload (current snapshot)",
    status: setMany(neutralAll(), {
      start: "success",
      "collect.notion": "success",
      "collect.clickup": "success",
      "collect.asana": "success",
      "extract.notion": "success",
      "extract.clickup": "success",
      "extract.asana": "success",
      analyst: "success",
      reporter: "success",
      qa: "rework",
      reporter_v2: "running",
    }),
  },
];

export const FINAL_PHASE_INDEX = DAG_PHASES.length - 1;
