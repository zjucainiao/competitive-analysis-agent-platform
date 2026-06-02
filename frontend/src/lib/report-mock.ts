/**
 * Report tab 的 mock 数据。
 *
 * 故事线（与 DAG mock 对齐）：
 *   - run #03 当前正在跑 reporter_v2，所以 v2 尚未完成；v1 是已发布版本
 *   - QA 在 v1 找出 2 处问题：p_sw_01 缺 evidence、p_pr_02 数字未严格匹配
 *   - 用户可以在 UI 上看到 v1 全文 + v2 diff 预览（"两处会改成什么样"）
 *   - 用户也可以直接 override v1 当作终稿
 *
 * 这是 PRODUCT.md 第 6 条原则「可介入而非黑盒」的演示核心。
 */

export type Sentiment = "positive" | "negative" | "neutral";

export interface MockEvidence {
  id: string;
  product: string;
  sourceUrl: string;
  sourceType: string;
  sourceLabel: string;
  authority: number;
  language: "en" | "zh";
  content: string;
  contextBefore?: string;
  contextAfter?: string;
  collectedAt: string; // ISO-like
  tags: string[];
  status: "verified" | "disputed" | "stale";
}

export interface MockParagraph {
  id: string;
  text: string;
  /** evidence_ids referenced in order */
  evidenceIds: string[];
  /** 含数字 / 价格 / 百分比时为 true，需要 QA 额外校验 */
  isQuantitative?: boolean;
  /** 软结论：允许空 evidence，不强制引用 */
  isSoftConclusion?: boolean;
  /** v2 中要被替换的新文本 + 新 evidence（演示 v1↔v2 diff） */
  pendingV2?: {
    text: string;
    evidenceIds: string[];
    reason: string;
  };
  /** v1 中 QA 检出的 issue（rework 来源） */
  qaIssue?: {
    severity: "minor" | "major" | "critical";
    dimension: string;
    note: string;
  };
}

export interface MockSection {
  id: string;
  title: string;
  number: string; // "1" / "2" / ...
  paragraphs: MockParagraph[];
}

export interface MockReport {
  id: string;
  version: number;
  templateId: string;
  generatedAt: string;
  target: string;
  competitors: string[];
  summary: string;
  sections: MockSection[];
  metadata: {
    wordCount: number;
    claimCount: number;
    evidenceCount: number;
  };
}

/* ── Evidence library ──────────────────────────────────────────────────── */

export const MOCK_EVIDENCES: Record<string, MockEvidence> = {
  ev_notion_home_01: {
    id: "ev_notion_home_01",
    product: "Notion",
    sourceUrl: "https://www.notion.so/",
    sourceType: "homepage",
    sourceLabel: "notion.so",
    authority: 0.95,
    language: "en",
    content:
      "Notion is the connected workspace where better, faster work happens. Bring teams, projects, and tools together.",
    collectedAt: "2026-05-26 14:32",
    tags: ["positioning", "homepage"],
    status: "verified",
  },
  ev_notion_feature_01: {
    id: "ev_notion_feature_01",
    product: "Notion",
    sourceUrl: "https://www.notion.so/",
    sourceType: "homepage",
    sourceLabel: "notion.so",
    authority: 0.95,
    language: "en",
    content:
      "Notion AI helps you write, summarize, and brainstorm directly in your docs.",
    contextBefore:
      "AI capabilities are integrated into the editor experience…",
    collectedAt: "2026-05-26 14:32",
    tags: ["feature", "ai"],
    status: "verified",
  },
  ev_notion_price_01: {
    id: "ev_notion_price_01",
    product: "Notion",
    sourceUrl: "https://www.notion.so/pricing",
    sourceType: "pricing_page",
    sourceLabel: "notion.so/pricing",
    authority: 0.95,
    language: "en",
    content:
      "Notion offers four plans: Free, Plus at $10 per seat/month, Business at $15 per seat/month, and Enterprise (contact sales).",
    collectedAt: "2026-05-26 14:32",
    tags: ["pricing"],
    status: "verified",
  },
  ev_notion_price_02: {
    id: "ev_notion_price_02",
    product: "Notion",
    sourceUrl: "https://www.notion.so/pricing",
    sourceType: "pricing_page",
    sourceLabel: "notion.so/pricing",
    authority: 0.95,
    language: "en",
    content:
      "Plus plan includes unlimited blocks for teams, unlimited file uploads, and 30 day page history.",
    collectedAt: "2026-05-26 14:32",
    tags: ["pricing", "feature"],
    status: "verified",
  },
  ev_clickup_feature_01: {
    id: "ev_clickup_feature_01",
    product: "ClickUp",
    sourceUrl: "https://clickup.com/features/automations",
    sourceType: "features_page",
    sourceLabel: "clickup.com/features/automations",
    authority: 0.95,
    language: "en",
    content:
      "ClickUp Automations include 100+ pre-built automations and the ability to build custom workflow triggers across tasks, docs, and integrations.",
    collectedAt: "2026-05-26 14:30",
    tags: ["feature", "automation"],
    status: "verified",
  },
  ev_clickup_price_01: {
    id: "ev_clickup_price_01",
    product: "ClickUp",
    sourceUrl: "https://clickup.com/pricing",
    sourceType: "pricing_page",
    sourceLabel: "clickup.com/pricing",
    authority: 0.95,
    language: "en",
    content:
      "ClickUp provides Free Forever, Unlimited at $7 per user/month, Business at $12 per user/month, and Enterprise plans.",
    collectedAt: "2026-05-26 14:30",
    tags: ["pricing"],
    status: "verified",
  },
  ev_asana_review_01: {
    id: "ev_asana_review_01",
    product: "Asana",
    sourceUrl: "https://www.g2.com/products/asana/reviews",
    sourceType: "user_review",
    sourceLabel: "g2.com/products/asana",
    authority: 0.75,
    language: "en",
    content:
      "Reviewers consistently praise Asana for its visual project tracking across boards, lists, and timelines, calling it best-in-class for cross-functional project coordination.",
    collectedAt: "2026-05-26 14:35",
    tags: ["user_review"],
    status: "verified",
  },
  ev_asana_price_01: {
    id: "ev_asana_price_01",
    product: "Asana",
    sourceUrl: "https://asana.com/pricing",
    sourceType: "pricing_page",
    sourceLabel: "asana.com/pricing",
    authority: 0.95,
    language: "en",
    content:
      "Asana plans: Personal (free), Starter at $10.99 per user/month, Advanced at $24.99 per user/month, plus Enterprise tiers.",
    collectedAt: "2026-05-23 09:11",
    tags: ["pricing"],
    /* 演示 stale：3 天前抓的，对定价类型来说接近过期阈值 */
    status: "stale",
  },
  ev_notion_help_01: {
    id: "ev_notion_help_01",
    product: "Notion",
    sourceUrl: "https://www.notion.so/help/team-spaces",
    sourceType: "help_docs",
    sourceLabel: "notion.so/help",
    authority: 0.92,
    language: "en",
    content:
      "Team spaces let admins organize content with permissions scoped to specific teams. Available on Business and Enterprise plans.",
    collectedAt: "2026-05-28 11:02",
    tags: ["feature", "permission"],
    status: "verified",
  },
  ev_clickup_review_01: {
    id: "ev_clickup_review_01",
    product: "ClickUp",
    sourceUrl: "https://www.g2.com/products/clickup/reviews",
    sourceType: "user_review",
    sourceLabel: "g2.com/products/clickup",
    authority: 0.72,
    language: "en",
    content:
      "Several reviewers note ClickUp's interface can feel overwhelming for new users due to the breadth of features.",
    collectedAt: "2026-05-28 14:15",
    tags: ["user_review", "ux"],
    /* 演示 disputed：用户标记不准确 */
    status: "disputed",
  },
  ev_asana_feature_01: {
    id: "ev_asana_feature_01",
    product: "Asana",
    sourceUrl: "https://asana.com/product/timeline",
    sourceType: "features_page",
    sourceLabel: "asana.com/product/timeline",
    authority: 0.95,
    language: "en",
    content:
      "Timeline lets you map out dependencies, see how the pieces of your plan fit together, and shift them when things change.",
    collectedAt: "2026-05-28 09:44",
    tags: ["feature", "timeline"],
    status: "verified",
  },
  ev_notion_changelog_01: {
    id: "ev_notion_changelog_01",
    product: "Notion",
    sourceUrl: "https://www.notion.so/releases/2025-12-15",
    sourceType: "changelog",
    sourceLabel: "notion.so/releases",
    authority: 0.95,
    language: "en",
    content:
      "Notion AI now supports document-wide summarization and cross-database queries (released 2025-12-15).",
    collectedAt: "2026-03-12 16:30",
    tags: ["feature", "ai", "changelog"],
    /* 演示 stale：3 个月前抓，AI 功能可能已有新进展 */
    status: "stale",
  },
  ev_clickup_blog_01: {
    id: "ev_clickup_blog_01",
    product: "ClickUp",
    sourceUrl: "https://clickup.com/blog/clickup-3-launch",
    sourceType: "blog",
    sourceLabel: "clickup.com/blog",
    authority: 0.88,
    language: "en",
    content:
      "ClickUp 3.0 introduces a redesigned hierarchy with Spaces > Folders > Lists > Tasks and 25% faster load times.",
    collectedAt: "2026-05-27 19:20",
    tags: ["feature", "release"],
    status: "verified",
  },
};

/* ── Report draft v1 ──────────────────────────────────────────────────── */

export const MOCK_REPORT: MockReport = {
  id: "rep_collab_demo_v1",
  version: 1,
  templateId: "standard_v1",
  generatedAt: "2026-05-29 14:35",
  target: "Notion",
  competitors: ["ClickUp", "Asana"],
  summary:
    "Notion vs ClickUp · Asana 协作办公场景对比。Notion 在文档与 AI 能力上领先，ClickUp 在自动化与价格上有优势，Asana 在视觉化项目跟踪上口碑突出。",
  sections: [
    {
      id: "sec_overview",
      number: "1",
      title: "竞品概览",
      paragraphs: [
        {
          id: "p_ov_01",
          text: "本次对比聚焦 Notion、ClickUp、Asana 三款主流协作办公 SaaS，覆盖核心定位、目标用户、定价模型与差异化能力。",
          evidenceIds: [],
          isSoftConclusion: true,
        },
      ],
    },
    {
      id: "sec_features",
      number: "2",
      title: "核心功能对比",
      paragraphs: [
        {
          id: "p_fe_01",
          text: "Notion 以文档+数据库灵活组合见长，AI 能力内嵌于编辑器，适合知识密集型工作流。",
          evidenceIds: ["ev_notion_home_01", "ev_notion_feature_01"],
        },
        {
          id: "p_fe_02",
          text: "ClickUp 在自动化能力上明显强于 Notion，提供 100+ 预制自动化与自定义触发器，适合复杂跨任务工作流。",
          evidenceIds: ["ev_clickup_feature_01"],
        },
        {
          id: "p_fe_03",
          text: "Asana 的多视图项目跟踪能力被用户公认为业内领先之一，尤其适用于跨职能项目协调。",
          evidenceIds: ["ev_asana_review_01"],
        },
      ],
    },
    {
      id: "sec_pricing",
      number: "3",
      title: "定价策略对比",
      paragraphs: [
        {
          id: "p_pr_01",
          text: "三者均采用 Freemium 模式。ClickUp Unlimited 档 $7/seat/月，是三者中入门档最低价；Notion Plus $10/seat/月，Asana Starter $10.99/seat/月。",
          evidenceIds: [
            "ev_clickup_price_01",
            "ev_notion_price_01",
            "ev_asana_price_01",
          ],
          isQuantitative: true,
        },
        {
          /* 这是 QA 检出的 issue p_pr_02 */
          id: "p_pr_02",
          text: "Asana Advanced 档 $24.99/seat/月，相较 Notion Business $15 与 ClickUp Business $12，溢价明显，更适合预算充足的中大型团队。",
          evidenceIds: [
            "ev_asana_price_01",
            "ev_notion_price_01",
            "ev_clickup_price_01",
          ],
          isQuantitative: true,
          qaIssue: {
            severity: "minor",
            dimension: "fact_consistency",
            note: "数字 $15 在 ev_notion_price_01 中是 'Business at $15 per seat/month'，需确认引用是否精确",
          },
          pendingV2: {
            text: "Asana Advanced 档 $24.99/seat/月，相较 Notion Business（$15/seat/月）与 ClickUp Business（$12/seat/月），溢价约 67%–108%，定位偏向预算充足的中大型团队。",
            evidenceIds: [
              "ev_asana_price_01",
              "ev_notion_price_01",
              "ev_clickup_price_01",
            ],
            reason: "补足单位 + 量化溢价比例，让数字与 evidence 字面一致",
          },
        },
      ],
    },
    {
      id: "sec_swot",
      number: "4",
      title: "SWOT（以 Notion 为视角）",
      paragraphs: [
        {
          /* QA 检出 issue p_sw_01 缺 evidence */
          id: "p_sw_01",
          text: "优势：文档+数据库灵活组合，AI 能力内嵌于编辑器。",
          /* v1 里被检出 evidence_ids 空 */
          evidenceIds: [],
          qaIssue: {
            severity: "major",
            dimension: "evidence_completeness",
            note: "段落引用了 cl_swot_001，但段落自身 evidence_ids 为空",
          },
          pendingV2: {
            text: "优势：文档+数据库灵活组合，AI 能力内嵌于编辑器，适合知识密集型工作流。",
            evidenceIds: ["ev_notion_home_01", "ev_notion_feature_01"],
            reason: "补充 evidence_ids 引用 cl_swot_001 关联的 2 条证据",
          },
        },
        {
          id: "p_sw_02",
          text: "劣势：复杂项目管理与工作流自动化能力相对薄弱，在面对深度自动化与可视化项目跟踪需求时不如专业 PM 工具。",
          evidenceIds: ["ev_clickup_feature_01", "ev_asana_review_01"],
        },
      ],
    },
    {
      id: "sec_sources",
      number: "5",
      title: "数据来源声明",
      paragraphs: [
        {
          id: "p_src_01",
          text: "本报告基于以下公开渠道于 2026-05-29 采集的资料生成：各产品官网首页、定价页、帮助文档（详见 evidence 附录）、G2 / Capterra 用户公开评价、各产品官方博客与更新日志。采集遵循各站点 robots.txt 与公开服务条款。报告中观点为基于上述资料的分析推断，不构成投资 / 采购建议。",
          evidenceIds: [],
          isSoftConclusion: true,
        },
      ],
    },
  ],
  metadata: {
    wordCount: 360,
    claimCount: 7,
    evidenceCount: 10,
  },
};

/* ── helpers ───────────────────────────────────────────────────────────── */

export function getEvidence(id: string): MockEvidence | undefined {
  return MOCK_EVIDENCES[id];
}

export function getQuantityNumbers(text: string): string[] {
  /* 极简数字抓取：用于演示 #num verified 计数 */
  return Array.from(text.matchAll(/\$?\d+(?:\.\d+)?/g)).map((m) => m[0]);
}
