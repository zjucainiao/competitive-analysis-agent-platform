/**
 * Frontend TypeScript types mirroring backend Pydantic models.
 *
 * 真正的 source of truth 是 backend/schemas + backend/api/schemas.py。
 * 后续可用 openapi-typescript 从 /openapi.json 自动生成；v1 手维护。
 *
 * 命名 / 字段大小写**完全匹配后端 JSON 序列化（snake_case）**。
 */

export type ProjectStatus =
  | "draft"
  | "planning"
  | "running"
  | "reviewing"
  | "done"
  | "failed"
  | "archived"
  | "deleted";

export type AgentStatus = "success" | "partial" | "needs_rework" | "failed";

export type NodeStatus =
  | "pending"
  | "ready"
  | "running"
  | "success"
  | "failed"
  | "needs_rework"
  | "skipped";

export type AnalysisDimension =
  | "feature_comparison"
  | "pricing_comparison"
  | "user_feedback"
  | "swot"
  | "differentiation_opportunities"
  | "positioning";

export type CollectDimension =
  | "homepage"
  | "features"
  | "pricing"
  | "help_docs"
  | "changelog"
  | "customer_cases"
  | "blog"
  | "user_reviews"
  | "app_market";

export type RunMode = "mock" | "hybrid" | "real";

export interface CollectConstraints {
  max_pages_per_dimension: number;
  timeout_seconds: number;
  respect_robots_txt: boolean;
  allow_paid_content: boolean;
  fallback_to_mock: boolean;
}

export interface ProjectMetrics {
  accuracy: number;
  coverage: number;
  edit_rate: number;
  evidence_count: number;
  fields_filled_ratio: number;
  total_tokens: number;
  total_cost_usd: number;
  duration_seconds: number;
  qa_round_count: number;
  real_fetch_count: number;
  mock_fetch_count: number;
  manual_edits: number;
}

export interface RunRef {
  run_id: string;
  plan_id: string;
  started_at: string;
  ended_at: string | null;
  final_status: string | null; // "done" / "failed" / "stopped"
}

export interface ProjectMetricsSnapshot {
  captured_at: string;
  metrics: ProjectMetrics;
}

export interface Project {
  project_id: string;
  project_name: string;
  owner: string;
  created_at: string; // ISO
  target_product: string;
  competitors: string[];
  industry: string;
  industry_schema_version: string;
  analysis_dimensions: AnalysisDimension[];
  report_template_id: string;
  target_audience: string | null;
  mode: RunMode;
  collect_constraints: CollectConstraints;
  status: ProjectStatus;
  current_report_id: string | null;
  metrics: ProjectMetrics | null;
  metrics_history: ProjectMetricsSnapshot[];
  runs: RunRef[];
  archived_at: string | null;
  deleted_at: string | null;
}

export interface ProjectListResponse {
  projects: Project[];
}

export interface ProjectCreateRequest {
  project_name: string;
  owner: string;
  target_product: string;
  competitors: string[];
  industry: string;
  industry_schema_version?: string;
  analysis_dimensions?: AnalysisDimension[];
  report_template_id?: string;
  target_audience?: string | null;
  mode?: "real";
  collect_constraints?: Partial<CollectConstraints>;
}

export interface RunStartedResponse {
  project_id: string;
  plan_id: string;
  thread_id: string;
  started_at: string;
}

/* ── DAG ─────────────────────────────────────────────────────────────── */

export type NodeType =
  | "start"
  | "end"
  | "agent_call"
  | "parallel_fork"
  | "parallel_join"
  | "conditional"
  | "feedback";

export interface DAGNode {
  node_id: string;
  project_id: string;
  node_type: NodeType;
  agent_name: string | null;
  status: NodeStatus;
  input_refs: string[];
  output_ref: string | null;
  retry_count: number;
  max_retries: number;
  timeout_ms: number;
  started_at: string | null;
  ended_at: string | null;
  parent_node_id: string | null;
  revision: number;
  metadata: Record<string, unknown>;
}

export interface DAGEdge {
  edge_id: string;
  from_node: string;
  to_node: string;
  edge_type: "dependency" | "feedback" | "conditional";
  condition: string | null;
}

export interface DAGPlan {
  plan_id: string;
  project_id: string;
  template_id: string | null;
  nodes: DAGNode[];
  edges: DAGEdge[];
  rationale: string;
  confidence: number;
  complexity_score: number;
}

/* ── Agent outputs (discriminated by node_id / agent_name) ───────────── */

export interface AgentError {
  code: string;
  message: string;
  severity: "warn" | "error" | "fatal";
  retriable: boolean;
  details: Record<string, unknown>;
}

export interface AgentOutputBase {
  agent_name: string;
  agent_version: string;
  task_id: string;
  trace_id: string;
  span_id: string;
  status: AgentStatus;
  confidence: number;
  self_critique: string;
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
  duration_ms: number;
  errors: AgentError[];
}

/* RawSourceDoc / Evidence */
export interface RawSourceDoc {
  source_id: string;
  product_name: string;
  dimension: CollectDimension;
  source_url: string;
  source_type: string;
  title: string | null;
  raw_text: string;
  summary: string | null;
  language: string;
  collected_at: string;
  fetch_method: "search" | "firecrawl" | "playwright" | "mock" | "manual";
  http_status: number | null;
  robots_allowed: boolean;
  source_authority: number;
  detected_paywall: boolean;
  detected_outdated: boolean;
}

export interface Evidence {
  evidence_id: string;
  source_id: string;
  product_name: string;
  source_url: string;
  source_type: string;
  source_authority: number;
  content: string;
  content_hash: string;
  context_before: string | null;
  context_after: string | null;
  location: {
    char_start: number | null;
    char_end: number | null;
    selector: string | null;
    page_section: string | null;
  };
  language: string;
  collected_at: string;
  source_published_at: string | null;
  extracted_at: string;
  confidence: number;
  tags: string[];
  embedding_id: string | null;
  disputed: boolean;
}

/* AnalysisResult / DimensionAnalysis / Claim */
export interface AnalysisClaim {
  claim_id: string;
  text: string;
  products_involved: string[];
  evidence_ids: string[];
  confidence: number;
  counter_evidence_ids: string[];
  qualifier: string | null;
}

export interface DimensionAnalysis {
  dimension: AnalysisDimension;
  summary: string;
  claims: AnalysisClaim[];
  comparison_matrix: Record<string, unknown> | null;
  confidence: number;
}

export interface AnalysisResult {
  target_product: string;
  competitors: string[];
  dimensions: Record<AnalysisDimension, DimensionAnalysis>;
}

/* ReportDraft */
export interface ReportParagraph {
  paragraph_id: string;
  text: string;
  claim_ids: string[];
  evidence_ids: string[];
  is_quantitative: boolean;
  is_soft_conclusion: boolean;
}

export interface ReportSection {
  section_id: string;
  title: string;
  order: number;
  paragraphs: ReportParagraph[];
}

export interface ReportDraft {
  report_id: string;
  version: number;
  template_id: string;
  sections: ReportSection[];
  summary: string;
  metadata: Record<string, unknown>;
}

/* QA */
export type QAStatusValue = "pass" | "needs_revision" | "reject";
export type QADimension =
  | "fact_consistency"
  | "evidence_completeness"
  | "schema_completeness"
  | "logic_consistency"
  | "freshness"
  | "expression";

export interface QAIssue {
  issue_id: string;
  dimension: QADimension;
  severity: "minor" | "major" | "critical";
  location: string;
  problem: string;
  suggested_fix: string;
  target_agent: "collector" | "extractor" | "analyst" | "reporter";
  required_inputs: Record<string, unknown>;
}

export interface QARouting {
  target_agent: "collector" | "extractor" | "analyst" | "reporter";
  reason: string;
  payload: Record<string, unknown>;
}

export interface QADimensionResult {
  dimension: QADimension;
  score: number;
  pass: boolean;
  notes: string;
}

export interface QAVerdict {
  verdict_id: string;
  overall_status: QAStatusValue;
  dimension_results: Record<QADimension, QADimensionResult>;
  issues: QAIssue[];
  routing: QARouting[];
  blocking: boolean;
}

/* Output discriminators by shape — outputs[node_id] is one of these */
export interface CollectorOutput extends AgentOutputBase {
  raw_sources: RawSourceDoc[];
  coverage_by_dimension: Record<CollectDimension, number>;
}

export interface CompetitorProfile {
  profile_id: string;
  schema_version: string;
  industry: string;
  basic_info: {
    name: string;
    company: string | null;
    official_website: string | null;
    category: string;
    positioning: string | null;
    target_users: Array<{ name: string; size_range: string | null; industry: string | null }>;
    main_scenarios: string[];
    founded_year: number | null;
    headquarters: string | null;
    languages_supported: string[];
    evidence_refs: Record<string, string[]>;
  };
  features: Record<string, unknown>;
  pricing: Record<string, unknown>;
  user_feedback: Record<string, unknown>;
  competitive: Record<string, unknown>;
  industry_extension: Record<string, unknown> | null;
  extracted_at: string;
  field_confidence: Record<string, number>;
  field_status: Record<string, string>;
}

export interface ExtractorOutput extends AgentOutputBase {
  profile: CompetitorProfile;
  evidences: Evidence[];
  field_confidence: Record<string, number>;
  schema_version: string;
  unmatched_quotes: string[];
}

export interface AnalystOutput extends AgentOutputBase {
  result: AnalysisResult;
}

export interface ReporterOutput extends AgentOutputBase {
  draft: ReportDraft;
}

export interface QAOutput extends AgentOutputBase {
  verdict: QAVerdict;
}

export type AnyAgentOutput =
  | CollectorOutput
  | ExtractorOutput
  | AnalystOutput
  | ReporterOutput
  | QAOutput
  | AgentOutputBase;

/* ── Aggregate state ─────────────────────────────────────────────────── */

export interface ProjectStateResponse {
  project: Project;
  plan: DAGPlan | null;
  outputs: Record<string, AnyAgentOutput>;
  verdicts: QAVerdict[];
}

/* ── PATCH paragraph ─────────────────────────────────────────────────── */

export interface ParagraphPatchRequest {
  text: string;
  is_soft_conclusion?: boolean;
  is_quantitative?: boolean;
}

export interface ParagraphPatchResponse {
  paragraph: ReportParagraph;
  report_node_id: string;
  manual_edits: number;
  edit_rate: number;
  status: "ok";
}

/* ── WS message ──────────────────────────────────────────────────────── */

export interface NodeExecutionResult {
  node_id: string;
  status: NodeStatus;
  output: AnyAgentOutput | null;
  error: AgentError | null;
  next_nodes: string[];
}

/* ── interventions ───────────────────────────────────────────────────── */

export interface QAOverrideResponse {
  project_id: string;
  accepted_report_node_id: string;
  skipped_node_ids: string[];
  manual_edits: number;
  edit_rate: number;
  overridden_verdict_id: string | null;
}

export interface NodeActionResponse {
  project_id: string;
  node_id: string;
  new_status: NodeStatus;
  affected_downstream: string[];
}

export interface EditPromptRequest {
  prompt_override: string;
}

export type RunControlAction = "paused" | "resumed" | "stopped" | "restarted";

export interface RunControlResponse {
  project_id: string;
  action: RunControlAction;
  cancelled_task: boolean;
  plan_status_reset: boolean;
}

/* ── evidence dispute ────────────────────────────────────────────────── */

export interface EvidenceDisputeRequest {
  disputed: boolean;
  reason?: string | null;
}

export interface EvidenceDisputeResponse {
  evidence_id: string;
  disputed: boolean;
  located_in_node: string;
  manual_edits: number;
  auto_rework_triggered: boolean;
  rework_verdict_id: string | null;
  rework_new_node_ids: string[];
  affected_paragraph_ids: string[];
}

/* ── runs history ────────────────────────────────────────────────────── */

export interface RunListResponse {
  project_id: string;
  runs: RunRef[];
}

export interface RunSnapshotResponse {
  project_id: string;
  run_id: string;
  captured_at: string;
  plan: DAGPlan;
  outputs: Record<string, AnyAgentOutput>;
  verdicts: QAVerdict[];
  metrics: ProjectMetrics | null;
  final_status: string;
}

/* ── metrics / LLM calls ─────────────────────────────────────────────── */

export interface AggregateMetricsResponse {
  project_count: number;
  finished_project_count: number;
  avg_accuracy: number;
  avg_coverage: number;
  avg_edit_rate: number;
  total_evidence: number;
  total_tokens: number;
  total_cost_usd: number;
  total_duration_seconds: number;
  total_qa_rounds: number;
  total_manual_edits: number;
  by_status: Record<string, number>;
  by_industry: Record<string, number>;
}

export interface MetricsTimeseriesResponse {
  project_id: string;
  history: ProjectMetricsSnapshot[];
}

export interface LLMCallRecord {
  timestamp: number; // epoch seconds (float)
  trace_id: string | null;
  span_id: string | null;
  node_id: string | null;
  agent_name: string | null;
  model: string;
  phase: string; // tool_call / json_mode / freeform / retry
  tokens_input: number;
  tokens_output: number;
  duration_s: number;
  finish_reason: string | null;
  cost_usd: number;
  prompt_preview: string;
  response_preview: string;
}

export interface LLMCallsResponse {
  calls: LLMCallRecord[];
  total: number;
}

/* ── export ──────────────────────────────────────────────────────────── */

export type ExportFormat = "json" | "markdown" | "pdf" | "docx";
