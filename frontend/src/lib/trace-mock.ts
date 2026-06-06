import { DEMO_DAG_NODES, type DagNodeData, type DagNodeStatus } from "./dag-mock";

/**
 * Trace tab 的扩展 mock。
 *
 * 在 DAG mock 之上注入：
 *  - 起始时间戳 startedAt（让时间轴有真实节奏）
 *  - 完整 LLM call 内容（system / messages / response / temperature / finish_reason）
 *  - 工具调用 tool calls
 *  - QA feedback 注入到 reporter_v2 system prompt（v1↔v2 diff 的核心）
 *
 * Sprint 2 后由 Orchestrator trace store 替代。
 */

export interface FullLLMCall {
  callId: string;
  model: string;
  temperature: number;
  maxTokens: number;
  systemPrompt: string;
  messages: Array<{
    role: "user" | "assistant";
    content: string;
  }>;
  /** 已结构化解析后的 response（JSON 形式 stringified） */
  responseJson: string;
  tokensIn: number;
  tokensOut: number;
  finishReason: "stop" | "length" | "tool_use" | "max_tokens";
  durationMs: number;
  costUsd: number;
}

export interface ToolCall {
  callId: string;
  toolName: string;
  arguments: Record<string, unknown>;
  result: string;
  durationMs: number;
  error?: string;
}

export interface TraceSpan {
  spanId: string;
  nodeId: string;
  label: string;
  agent: string;
  status: DagNodeStatus;
  startedAt: string; // HH:MM:SS local
  durationMs: number | null;
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
  confidence: number | null;
  selfCritique: string | null;
  storyHint: string | null;
  revision: number;
  parentNodeId: string | null;
  /** 完整 LLM call 详情（部分节点详写，其他用 placeholder） */
  llmCalls: FullLLMCall[];
  toolCalls: ToolCall[];
  /** 顶层 issues / routing 摘要，用于 row 子标题 */
  summary: string;
  /** 是否被 QA 路由触发了重做（feedback edge target） */
  isFeedbackTarget: boolean;
}

/* ── Real prompt content (the heart of the diff demo) ───────────────── */

const REPORTER_SYSTEM_V1 = `# Reporter Agent · standard_v1

You are a competitive analysis report writer.
Compose a markdown report based on the provided AnalysisResult.

## HARD RULES (enforced post-hoc by QA)

1. Use ONLY claims provided in the input AnalysisResult.
2. Every factual paragraph MUST cite >=1 evidence_id from the claim's
   evidence pool. Soft conclusions ("可能"、"通常") may be evidence-free
   but must set is_soft_conclusion: true.
3. Numeric segments (prices / %/ version / count) MUST be
   character-exact with at least one cited evidence. Set is_quantitative.
4. Banned phrases: "行业唯一", "绝对领先", "完美", "100%", "最佳产品",
   "无可替代".

## OUTPUT FORMAT
Strict JSON conforming to schemas.ReportSection (Pydantic).

## STYLE
- target_audience: 产品经理
- voice: 事实陈述 + sentence-case 英文
- no marketing exclamations`;

const REPORTER_SYSTEM_V2 = REPORTER_SYSTEM_V1 + `

## QA FEEDBACK (verdict_id: qa_revise_001)
The previous run (reporter v1) produced a draft that QA found 2 issues with.
You MUST address these before regenerating. Other paragraphs may stay intact.

### Issue iss_001 · major · evidence_completeness
- Location: report.sections[3].paragraphs[0]  (paragraph_id: p_sw_01)
- Problem:  段落 "优势：文档+数据库灵活组合，AI 能力内嵌于编辑器。"
            引用了 claim cl_swot_001 但段落自身 evidence_ids 为空
- Required: Add evidence_ids referencing the evidences that back
            cl_swot_001 (ev_notion_home_01, ev_notion_feature_01)

### Issue iss_002 · minor · fact_consistency
- Location: report.sections[2].paragraphs[1]  (paragraph_id: p_pr_02)
- Problem:  "$15 与 $12" 没说明是 per seat / monthly 单位
- Required: Add explicit "$15/seat/月" 等单位；可附加溢价比例量化

### must_address (you cannot skip these)
- p_sw_01
- p_pr_02

DO NOT modify other paragraphs unless strictly necessary.`;

const QA_FACT_CHECK_SYSTEM = `# QA Agent · fact_consistency dimension

You are a strict reviewer. For each ReportParagraph, judge whether
its factual claims are entailed by the cited evidence.

## DECISION TYPES
- entailed: text is fully supported by evidence
- contradicted: text contradicts at least one evidence
- neutral: evidence doesn't speak to the claim (e.g., soft conclusion)

## QUANT RULES
- Numbers / prices / % / version / count must be character-exact with
  at least one evidence
- Tolerance: ±5% only when label says "approximately" / "约"
- Missing unit → flag as minor unless ambiguous

## OUTPUT
Strict JSON conforming to schemas.QADimensionResult (Pydantic).`;

const COLLECTOR_RANKER_SYSTEM = `# Collector Agent · url_ranker

You receive search result URLs for a target product and dimension.
Pick the top-K most relevant for the dimension.

## RULES
- Prefer official sources (homepage, /pricing, /docs, /changelog)
- For 'reviews' dimension: G2 / Capterra / app market reviews
- Reject paywalled / login-required pages
- Reject obviously stale (>1 year for pricing/features)

## OUTPUT
JSON array of { url, source_type, relevance: 0..1, reason }.`;

/* ── Helper: short truncated prompt for placeholder calls ───────────── */

function shortPrompt(agent: string): string {
  switch (agent) {
    case "collector":
      return COLLECTOR_RANKER_SYSTEM;
    case "extractor":
      return `# Extractor Agent · industry: collaboration_saas

You extract a CompetitorProfile from raw_sources.

## HARD RULES
- Every non-null field MUST be backed by a source_quote
- If the original text does not mention a field, return null
- Output strict JSON conforming to schemas.CompetitorProfile`;
    case "analyst":
      return `# Analyst Agent · feature_comparison dimension

You produce structured comparison claims across competitors.

## HARD RULES
- Every AnalysisClaim.evidence_ids ≥ 1
- counter_evidence_ids encouraged when nuanced
- Output JSON conforming to schemas.DimensionAnalysis`;
    default:
      return `# ${agent} Agent

Mock system prompt for ${agent}.`;
  }
}

/* ── Build spans from existing DAG mock ──────────────────────────────── */

const NODE_TIMING: Record<string, { startedAt: string }> = {
  start:                  { startedAt: "14:00:00" },
  "collect.notion":       { startedAt: "14:00:02" },
  "collect.clickup":      { startedAt: "14:00:02" },
  "collect.asana":        { startedAt: "14:00:02" },
  "extract.notion":       { startedAt: "14:00:07" },
  "extract.clickup":      { startedAt: "14:00:07" },
  "extract.asana":        { startedAt: "14:00:07" },
  analyst:                { startedAt: "14:00:20" },
  reporter:               { startedAt: "14:00:37" },
  qa:                     { startedAt: "14:00:56" },
  reporter_v2:            { startedAt: "14:01:04" },
  qa_v2:                  { startedAt: "—" },
  end:                    { startedAt: "—" },
};

function buildFullLLMCalls(nodeId: string, data: DagNodeData): FullLLMCall[] {
  if (data.llmCalls.length === 0) return [];

  /* For reporter / reporter_v2 / qa, emit real, distinguishable prompts */
  if (nodeId === "reporter") {
    return data.llmCalls.map((c, i) => ({
      callId: `call_${nodeId}_${i + 1}`,
      model: c.model,
      temperature: 0.5,
      maxTokens: 4096,
      systemPrompt: REPORTER_SYSTEM_V1,
      messages: [
        {
          role: "user",
          content:
            i === 0
              ? `Compose the overview section. AnalysisResult includes 7 claims across 3 dimensions. Target = Notion, competitors = ClickUp · Asana.`
              : i === 1
                ? `Compose the feature_comparison section. Cite cl_feat_001 / cl_feat_002 / cl_feat_003.`
                : i === 2
                  ? `Compose the pricing_comparison section. Use cl_price_001 / cl_price_002. Include numbers verbatim.`
                  : `Compose the SWOT section. Use cl_swot_001 / cl_swot_002.`,
        },
        {
          role: "assistant",
          content:
            i === 3
              ? `{
  "section_id": "sec_swot",
  "title": "SWOT（以 Notion 为视角）",
  "order": 4,
  "paragraphs": [
    {
      "paragraph_id": "p_sw_01",
      "text": "优势：文档+数据库灵活组合，AI 能力内嵌于编辑器。",
      "claim_ids": ["cl_swot_001"],
      "evidence_ids": [],   // ← QA 后续会标 missing_citation
      "is_quantitative": false
    },
    ...
  ]
}`
              : `{ "section_id": "...", "paragraphs": [ ... ] }`,
        },
      ],
      responseJson: `{ "section_id": "sec_${i}", "paragraphs": [...] }`,
      tokensIn: c.tokensIn,
      tokensOut: c.tokensOut,
      finishReason: "stop",
      durationMs: c.durationMs,
      costUsd: (c.tokensIn * 3) / 1_000_000 + (c.tokensOut * 15) / 1_000_000,
    }));
  }

  if (nodeId === "reporter_v2") {
    return data.llmCalls.map((c, i) => ({
      callId: `call_${nodeId}_${i + 1}`,
      model: c.model,
      temperature: 0.5,
      maxTokens: 4096,
      /* ★ 关键：v2 的 system prompt 多出 QA FEEDBACK 章节 */
      systemPrompt: REPORTER_SYSTEM_V2,
      messages: [
        {
          role: "user",
          content: `Regenerate section sec_swot AND fix paragraph p_pr_02 per QA feedback. Other paragraphs unchanged.`,
        },
        {
          role: "assistant",
          content: `{
  "section_id": "sec_swot",
  "paragraphs": [
    {
      "paragraph_id": "p_sw_01",
      "text": "优势：文档+数据库灵活组合，AI 能力内嵌于编辑器，适合知识密集型工作流。",
      "claim_ids": ["cl_swot_001"],
      "evidence_ids": ["ev_notion_home_01","ev_notion_feature_01"],   // ✓ 已修复
      "is_quantitative": false
    },
    ...
  ]
}`,
        },
      ],
      responseJson: `{ "draft": "ReportDraft v2 (in progress)", "must_address_resolved": ["p_sw_01"] }`,
      tokensIn: c.tokensIn,
      tokensOut: c.tokensOut,
      finishReason: i === 0 ? "stop" : "length",
      durationMs: c.durationMs,
      costUsd: (c.tokensIn * 3) / 1_000_000 + (c.tokensOut * 15) / 1_000_000,
    }));
  }

  if (nodeId === "qa") {
    const dims = [
      "fact_consistency",
      "evidence_completeness",
      "schema_completeness",
      "logic_consistency",
      "freshness",
      "expression",
    ];
    return data.llmCalls.map((c, i) => ({
      callId: `call_${nodeId}_${i + 1}`,
      model: c.model,
      temperature: 0,
      maxTokens: 1024,
      systemPrompt:
        i === 0 ? QA_FACT_CHECK_SYSTEM : QA_FACT_CHECK_SYSTEM.replace("fact_consistency", dims[i] ?? "expression"),
      messages: [
        {
          role: "user",
          content: `Check dimension ${dims[i]} on ReportDraft v1. Return per-paragraph verdict + global score.`,
        },
        {
          role: "assistant",
          content:
            i === 1
              ? `{
  "dimension": "evidence_completeness",
  "score": 0.72,
  "pass": false,
  "notes": "p_sw_01 has empty evidence_ids despite citing cl_swot_001",
  "issues": [
    { "issue_id": "iss_001", "severity": "major",
      "location": "report.sections[3].paragraphs[0]",
      "target_agent": "reporter" }
  ]
}`
              : i === 0
                ? `{
  "dimension": "fact_consistency",
  "score": 0.88,
  "pass": true,
  "notes": "p_pr_02 数字未注明单位，标 minor",
  "issues": [
    { "issue_id": "iss_002", "severity": "minor", "target_agent": "reporter" }
  ]
}`
                : `{ "dimension": "${dims[i]}", "score": 0.94, "pass": true, "issues": [] }`,
        },
      ],
      responseJson: `{ "dimension": "${dims[i]}", "score": ${i === 1 ? 0.72 : 0.94}, "pass": ${i !== 1} }`,
      tokensIn: c.tokensIn,
      tokensOut: c.tokensOut,
      finishReason: "stop",
      durationMs: c.durationMs,
      costUsd: (c.tokensIn * 3) / 1_000_000 + (c.tokensOut * 15) / 1_000_000,
    }));
  }

  /* 其他节点 placeholder：填 shortPrompt + minimal user/assistant 演示 */
  return data.llmCalls.map((c, i) => ({
    callId: `call_${nodeId}_${i + 1}`,
    model: c.model,
    temperature: data.agent === "extractor" ? 0.1 : 0.2,
    maxTokens: 2048,
    systemPrompt: shortPrompt(data.agent),
    messages: [
      {
        role: "user",
        content: `[${data.agent} input #${i + 1} for ${data.label} — abbreviated mock]`,
      },
      {
        role: "assistant",
        content: `[structured output omitted in mock — see Outputs panel]`,
      },
    ],
    responseJson: `{ "agent": "${data.agent}", "ok": true }`,
    tokensIn: c.tokensIn,
    tokensOut: c.tokensOut,
    finishReason: "stop",
    durationMs: c.durationMs,
    costUsd: (c.tokensIn * 3) / 1_000_000 + (c.tokensOut * 15) / 1_000_000,
  }));
}

function buildToolCalls(nodeId: string, data: DagNodeData): ToolCall[] {
  if (!data.agent.startsWith("collect")) return [];
  if (!nodeId.startsWith("collect.")) return [];
  return [
    {
      callId: `tool_${nodeId}_search`,
      toolName: "search.tavily",
      arguments: { query: `${data.label.replace("collect:", "")} site:official` },
      result: "→ 8 candidate URLs",
      durationMs: 420,
    },
    {
      callId: `tool_${nodeId}_robots`,
      toolName: "robots_checker.check",
      arguments: { urls: ["…/pricing", "…/help", "…/blog"] },
      result: "all allowed",
      durationMs: 12,
    },
    {
      callId: `tool_${nodeId}_scrape`,
      toolName: "scrape.firecrawl",
      arguments: { urls: ["8 URLs"], render: false },
      result: "8 docs · 14ms avg parse",
      durationMs: 3120,
    },
  ];
}

function buildSummary(data: DagNodeData): string {
  if (data.status === "rework") {
    return "needs_revision · 2 issues · routing → reporter";
  }
  if (data.status === "running") {
    return "running · awaiting completion";
  }
  if (data.status === "neutral") {
    return "pending · awaiting upstream";
  }
  return `${data.label} ok`;
}

/* ── Exported builder ────────────────────────────────────────────────── */

export const TRACE_SPANS: TraceSpan[] = DEMO_DAG_NODES.map((n) => ({
  spanId: `span_${n.id}`,
  nodeId: n.id,
  label: n.data.label,
  agent: n.data.agent,
  status: n.data.status,
  startedAt: NODE_TIMING[n.id]?.startedAt ?? "—",
  durationMs: n.data.durationMs,
  tokensIn: n.data.tokens?.input ?? 0,
  tokensOut: n.data.tokens?.output ?? 0,
  costUsd: n.data.costUsd ?? 0,
  confidence: n.data.confidence,
  selfCritique: n.data.selfCritique,
  storyHint: n.data.storyHint ?? null,
  revision: n.data.revision,
  parentNodeId: n.data.parentNodeId,
  llmCalls: buildFullLLMCalls(n.id, n.data),
  toolCalls: buildToolCalls(n.id, n.data),
  summary: buildSummary(n.data),
  isFeedbackTarget: n.id === "reporter_v2",
}));

/* ── Trace-level summary stats ───────────────────────────────────────── */

export interface TraceSummary {
  traceId: string;
  spanCount: number;
  totalDurationMs: number;
  totalTokensIn: number;
  totalTokensOut: number;
  totalCostUsd: number;
  successCount: number;
  reworkCount: number;
  runningCount: number;
  pendingCount: number;
  failedCount: number;
}

export function summarizeTrace(spans: TraceSpan[]): TraceSummary {
  return spans.reduce<TraceSummary>(
    (acc, s) => {
      acc.totalDurationMs += s.durationMs ?? 0;
      acc.totalTokensIn += s.tokensIn;
      acc.totalTokensOut += s.tokensOut;
      acc.totalCostUsd += s.costUsd;
      if (s.status === "success") acc.successCount += 1;
      else if (s.status === "rework") acc.reworkCount += 1;
      else if (s.status === "running") acc.runningCount += 1;
      else if (s.status === "error") acc.failedCount += 1;
      else acc.pendingCount += 1;
      return acc;
    },
    {
      traceId: "trace_abc123",
      spanCount: spans.length,
      totalDurationMs: 0,
      totalTokensIn: 0,
      totalTokensOut: 0,
      totalCostUsd: 0,
      successCount: 0,
      reworkCount: 0,
      runningCount: 0,
      pendingCount: 0,
      failedCount: 0,
    }
  );
}

/* ── v1 ↔ v2 diff payload (Reporter prompt comparison) ──────────────── */

export interface DiffPair {
  id: string;
  label: string;
  description: string;
  leftLabel: string;
  rightLabel: string;
  leftContent: string;
  rightContent: string;
}

export const DIFF_PAIRS: DiffPair[] = [
  {
    id: "reporter-system",
    label: "Reporter · system prompt",
    description:
      "v2 在 v1 基础上注入 QA FEEDBACK 章节，包含 2 个 must_address 段落 ID + 修复要求",
    leftLabel: "reporter (v1)",
    rightLabel: "reporter_v2",
    leftContent: REPORTER_SYSTEM_V1,
    rightContent: REPORTER_SYSTEM_V2,
  },
];
