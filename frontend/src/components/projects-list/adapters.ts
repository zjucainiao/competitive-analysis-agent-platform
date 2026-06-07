import type { Project } from "@/lib/api/types";
import type { MockProject } from "@/lib/projects-mock";
import type { StatusTone } from "@/components/layout/status-pill";

/**
 * 把后端 Project 适配成 ProjectCard 期望的形状。
 * 共用 MockProject 的字段结构，避免 Card 组件重写。
 */

const INDUSTRY_LABEL: Record<string, string> = {
  collaboration_saas: "协作办公",
  crm_saas: "CRM",
  cross_border_ecommerce_saas: "跨境电商",
  edu_saas: "教育 SaaS",
};

function statusTone(s: Project["status"]): {
  tone: StatusTone;
  label: string;
  pulse?: boolean;
} {
  switch (s) {
    case "running":
      return { tone: "running", label: "进行中", pulse: true };
    case "reviewing":
      return { tone: "rework", label: "审核中" };
    case "done":
      return { tone: "success", label: "已完成" };
    case "failed":
      return { tone: "error", label: "失败" };
    case "planning":
      return { tone: "running", label: "准备中", pulse: true };
    case "draft":
    default:
      return { tone: "neutral", label: "草稿" };
  }
}

function fmtRelative(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} minutes ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hours ago`;
  if (diff < 86400 * 2) return "yesterday";
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} days ago`;
  if (diff < 86400 * 365) return `${Math.floor(diff / 86400 / 30)} months ago`;
  return `${Math.floor(diff / 86400 / 365)} years ago`;
}

export function apiProjectToCard(p: Project): MockProject {
  const m = p.metrics;
  return {
    id: p.project_id,
    isLive: true, // 真实 API 项目都可进入 workspace
    name: p.project_name,
    target: p.target_product,
    competitors: p.competitors,
    industry: p.industry,
    industryLabel: INDUSTRY_LABEL[p.industry] ?? p.industry,
    templateId: p.report_template_id,
    status: statusTone(p.status),
    runCount: m?.qa_round_count ?? 0,
    lastRunId: p.project_id, // v1 runId = projectId
    lastUpdatedAt: fmtRelative(p.created_at),
    owner: p.owner,
    metrics: {
      accuracy: m?.accuracy ?? null,
      coverage: m?.coverage ?? null,
      editRate: m?.edit_rate ?? null,
      costUsd: m?.total_cost_usd ?? 0,
      spans: m?.qa_round_count ?? 0,
    },
  };
}

/**
 * 后端 v1 没有 "run" 子资源，runId 直接等于 projectId。
 * 之后扩展真正多 run 时改这里。
 */
export function pickInitialRunId(projectId: string): string {
  return projectId;
}
