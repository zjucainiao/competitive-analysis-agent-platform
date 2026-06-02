import type { RunContext } from "@/components/layout/context-bar";

/**
 * Workspace 演示用 mock run context。
 * Sprint 1 阶段 hard-coded；Sprint 2 之后由后端 API 提供。
 *
 * 与 backend/fixtures/mock_data/projects/collab_saas_demo.json 同步：
 * 同样的 target / competitors / template，便于 demo 闭环。
 */
export const DEMO_RUN_CONTEXT: RunContext = {
  projectId: "demo",
  projectName: "协作办公 SaaS · Demo",
  runId: "01",
  runNumber: 3,
  status: {
    /* QA 检出 2 处问题 → 当前等用户裁决；reporter_v2 同时 running
     * 用 rework 让顶部出现 "Override · accept v1" 这一关键 CTA */
    tone: "rework",
    label: "QA rework · reporter_v2 running",
    pulse: true,
  },
  target: "Notion",
  competitors: ["ClickUp", "Asana"],
  templateId: "standard_v1",
  industry: "collaboration_saas",
};

export function getRunContext(
  projectId: string,
  runId: string
): RunContext | null {
  // v1 只支持 demo run，后续走 fetch
  if (projectId === "demo" && runId === "01") {
    return DEMO_RUN_CONTEXT;
  }
  return null;
}
