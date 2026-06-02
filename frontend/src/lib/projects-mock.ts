import type { StatusTone } from "@/components/layout/status-pill";

/**
 * Projects 列表 mock。Sprint 2 接 ProjectService.list()。
 */

export interface MockProject {
  id: string;
  /** 真实可点击进入 workspace 的 demo 项目用 isLive=true */
  isLive: boolean;
  name: string;
  target: string;
  competitors: string[];
  industry: string;
  industryLabel: string;
  templateId: string;
  status: { tone: StatusTone; label: string; pulse?: boolean };
  runCount: number;
  lastRunId: string;
  lastUpdatedAt: string; // e.g. "2 minutes ago"
  owner: string;
  /** 项目是否被归档（默认 list 隐藏） */
  archived?: boolean;
  /** project-level metrics 概览 */
  metrics: {
    accuracy: number | null;
    coverage: number | null;
    editRate: number | null;
    costUsd: number;
    spans: number;
  };
}

export const MOCK_PROJECTS: MockProject[] = [
  {
    id: "demo",
    isLive: true,
    name: "协作办公 SaaS · Demo",
    target: "Notion",
    competitors: ["ClickUp", "Asana"],
    industry: "collaboration_saas",
    industryLabel: "协作办公",
    templateId: "standard_v1",
    status: { tone: "rework", label: "QA rework · v2 running", pulse: true },
    runCount: 3,
    lastRunId: "01",
    lastUpdatedAt: "2 minutes ago",
    owner: "XF",
    metrics: {
      accuracy: 0.94,
      coverage: 0.81,
      editRate: 0.15,
      costUsd: 0.61,
      spans: 13,
    },
  },
  {
    id: "crm-q1",
    isLive: false,
    name: "CRM Q1 · Salesforce 阵营",
    target: "Salesforce",
    competitors: ["HubSpot", "Pipedrive", "Zoho"],
    industry: "crm_saas",
    industryLabel: "CRM",
    templateId: "investor_v1",
    status: { tone: "success", label: "v3 published" },
    runCount: 5,
    lastRunId: "05",
    lastUpdatedAt: "3 hours ago",
    owner: "XF",
    metrics: {
      accuracy: 0.96,
      coverage: 0.88,
      editRate: 0.08,
      costUsd: 1.42,
      spans: 28,
    },
  },
  {
    id: "ecom-spring",
    isLive: false,
    name: "跨境电商 · Shopify 生态",
    target: "Shopify",
    competitors: ["BigCommerce", "Wix Stores", "Webflow"],
    industry: "cross_border_ecommerce_saas",
    industryLabel: "跨境电商",
    templateId: "pm_v1",
    status: { tone: "running", label: "running 4m 12s", pulse: true },
    runCount: 1,
    lastRunId: "01",
    lastUpdatedAt: "just now",
    owner: "XF",
    metrics: {
      accuracy: null,
      coverage: null,
      editRate: null,
      costUsd: 0.21,
      spans: 8,
    },
  },
  {
    id: "linear-jira",
    isLive: false,
    name: "Linear vs Jira · 开发者工具",
    target: "Linear",
    competitors: ["Jira", "Shortcut"],
    industry: "collaboration_saas",
    industryLabel: "协作办公",
    templateId: "pm_v1",
    status: { tone: "success", label: "v2 published" },
    runCount: 2,
    lastRunId: "02",
    lastUpdatedAt: "yesterday",
    owner: "XF",
    metrics: {
      accuracy: 0.93,
      coverage: 0.84,
      editRate: 0.12,
      costUsd: 0.48,
      spans: 11,
    },
  },
  {
    id: "ai-agents",
    isLive: false,
    name: "AI Agent 平台对比",
    target: "LangChain",
    competitors: ["LlamaIndex", "Haystack", "Semantic Kernel"],
    industry: "collaboration_saas",
    industryLabel: "AI tooling",
    templateId: "investor_v1",
    status: { tone: "error", label: "failed at extractor" },
    runCount: 1,
    lastRunId: "01",
    lastUpdatedAt: "1 day ago",
    owner: "XF",
    metrics: {
      accuracy: null,
      coverage: 0.42,
      editRate: null,
      costUsd: 0.08,
      spans: 5,
    },
  },
  {
    id: "design-tools",
    isLive: false,
    name: "设计工具 Q2 · Figma 阵营",
    target: "Figma",
    competitors: ["Sketch", "Adobe XD", "Penpot"],
    industry: "collaboration_saas",
    industryLabel: "Design",
    templateId: "standard_v1",
    status: { tone: "neutral", label: "draft" },
    runCount: 0,
    lastRunId: "—",
    lastUpdatedAt: "3 days ago",
    owner: "XF",
    metrics: {
      accuracy: null,
      coverage: null,
      editRate: null,
      costUsd: 0,
      spans: 0,
    },
  },
  {
    id: "archived-1",
    isLive: false,
    name: "客户支持平台对比 · 2025 Q4",
    target: "Zendesk",
    competitors: ["Intercom", "Freshdesk"],
    industry: "crm_saas",
    industryLabel: "客服系统",
    templateId: "standard_v1",
    status: { tone: "success", label: "v4 archived" },
    runCount: 4,
    lastRunId: "04",
    lastUpdatedAt: "3 months ago",
    owner: "XF",
    archived: true,
    metrics: {
      accuracy: 0.91,
      coverage: 0.79,
      editRate: 0.18,
      costUsd: 0.92,
      spans: 24,
    },
  },
];

/* derived facets */
export function listProjectIndustries() {
  return Array.from(
    new Set(
      MOCK_PROJECTS.filter((p) => !p.archived).map((p) => ({
        id: p.industry,
        label: p.industryLabel,
      }))
    )
  ).filter(
    (v, i, arr) => arr.findIndex((x) => x.id === v.id) === i
  );
}

export type ProjectStatusFilter = "all" | "running" | "rework" | "success" | "draft" | "failed";

export const PROJECT_STATUS_FILTERS: Array<{
  id: ProjectStatusFilter;
  label: string;
  tones: StatusTone[];
}> = [
  { id: "all", label: "全部", tones: [] },
  { id: "running", label: "运行中", tones: ["running"] },
  { id: "rework", label: "等裁决", tones: ["rework"] },
  { id: "success", label: "已完成", tones: ["success"] },
  { id: "draft", label: "草稿", tones: ["neutral"] },
  { id: "failed", label: "失败", tones: ["error"] },
];
