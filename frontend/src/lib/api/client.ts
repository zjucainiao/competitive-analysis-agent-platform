import type {
  AggregateMetricsResponse,
  EditPromptRequest,
  EvidenceDisputeRequest,
  EvidenceDisputeResponse,
  ExportFormat,
  LLMCallsResponse,
  MetricsTimeseriesResponse,
  NodeActionResponse,
  Project,
  ProjectCreateRequest,
  ProjectListResponse,
  ProjectStateResponse,
  ProjectStatus,
  QAOverrideResponse,
  RunControlResponse,
  RunListResponse,
  RunSnapshotResponse,
  RunStartedResponse,
  ParagraphPatchRequest,
  ParagraphPatchResponse,
} from "./types";

/**
 * 典型 fetch wrapper。
 *
 * 设计：
 *  - 单一 API_BASE 来自 NEXT_PUBLIC_API_BASE
 *  - 失败抛 ApiError，含 status + body，便于上层 toast
 *  - 默认 cache: 'no-store'（SWR 自己管 caching）
 */

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"
).replace(/\/$/, "");

export const WS_BASE =
  (process.env.NEXT_PUBLIC_WS_BASE ??
    API_BASE.replace(/^http/, "ws")).replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (e) {
    throw new ApiError(
      0,
      `Network error: ${e instanceof Error ? e.message : String(e)}`,
      null
    );
  }
  const ct = res.headers.get("content-type") ?? "";
  const body = ct.includes("application/json")
    ? await res.json().catch(() => null)
    : await res.text().catch(() => "");
  if (!res.ok) {
    const detail =
      (body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : null) ?? body;
    throw new ApiError(
      res.status,
      `${init?.method ?? "GET"} ${path} → ${res.status} ${res.statusText}: ${
        typeof detail === "string" ? detail : JSON.stringify(detail)
      }`,
      body
    );
  }
  return body as T;
}

/* ── projects ────────────────────────────────────────────────────────── */

export function listProjects(
  filter: { owner?: string; status?: ProjectStatus } = {}
): Promise<ProjectListResponse> {
  const params = new URLSearchParams();
  if (filter.owner) params.set("owner", filter.owner);
  if (filter.status) params.set("project_status", filter.status);
  const qs = params.toString();
  return request<ProjectListResponse>(`/api/projects${qs ? `?${qs}` : ""}`);
}

export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/api/projects/${encodeURIComponent(projectId)}`);
}

export function createProject(
  req: ProjectCreateRequest
): Promise<Project> {
  return request<Project>("/api/projects", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function startRun(projectId: string): Promise<RunStartedResponse> {
  return request<RunStartedResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/run`,
    { method: "POST" }
  );
}

export function getProjectState(
  projectId: string
): Promise<ProjectStateResponse> {
  return request<ProjectStateResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/state`
  );
}

/* ── reports PATCH ───────────────────────────────────────────────────── */

export function patchParagraph(
  projectId: string,
  reportNodeId: string,
  paragraphId: string,
  body: ParagraphPatchRequest
): Promise<ParagraphPatchResponse> {
  return request<ParagraphPatchResponse>(
    `/api/projects/${encodeURIComponent(
      projectId
    )}/reports/${encodeURIComponent(
      reportNodeId
    )}/paragraphs/${encodeURIComponent(paragraphId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
    }
  );
}

/* ── QA Override / 节点级动作 ────────────────────────────────────────── */

export function overrideQA(projectId: string): Promise<QAOverrideResponse> {
  return request<QAOverrideResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/override`,
    { method: "POST" }
  );
}

function nodeAction(
  projectId: string,
  nodeId: string,
  action: "retry" | "skip" | "force-start"
): Promise<NodeActionResponse> {
  return request<NodeActionResponse>(
    `/api/projects/${encodeURIComponent(
      projectId
    )}/nodes/${encodeURIComponent(nodeId)}/${action}`,
    { method: "POST" }
  );
}

export function retryNode(projectId: string, nodeId: string) {
  return nodeAction(projectId, nodeId, "retry");
}

export function skipNode(projectId: string, nodeId: string) {
  return nodeAction(projectId, nodeId, "skip");
}

export function forceStartNode(projectId: string, nodeId: string) {
  return nodeAction(projectId, nodeId, "force-start");
}

export function editPromptAndRerun(
  projectId: string,
  nodeId: string,
  body: EditPromptRequest
): Promise<NodeActionResponse> {
  return request<NodeActionResponse>(
    `/api/projects/${encodeURIComponent(
      projectId
    )}/nodes/${encodeURIComponent(nodeId)}/edit-prompt`,
    { method: "POST", body: JSON.stringify(body) }
  );
}

/* ── Run 控制 ────────────────────────────────────────────────────────── */

function runControl(
  projectId: string,
  action: "pause" | "resume" | "stop" | "restart"
): Promise<RunControlResponse> {
  return request<RunControlResponse>(
    `/api/projects/${encodeURIComponent(
      projectId
    )}/runs/current/${action}`,
    { method: "POST" }
  );
}

export function pauseRun(projectId: string) {
  return runControl(projectId, "pause");
}

export function resumeRun(projectId: string) {
  return runControl(projectId, "resume");
}

export function stopRun(projectId: string) {
  return runControl(projectId, "stop");
}

export function restartRun(projectId: string) {
  return runControl(projectId, "restart");
}

/* ── Evidence dispute ────────────────────────────────────────────────── */

export function patchEvidence(
  projectId: string,
  evidenceId: string,
  body: EvidenceDisputeRequest,
  opts: { autoRework?: boolean } = {}
): Promise<EvidenceDisputeResponse> {
  const params = new URLSearchParams();
  if (opts.autoRework) params.set("auto_rework", "true");
  const qs = params.toString();
  return request<EvidenceDisputeResponse>(
    `/api/projects/${encodeURIComponent(
      projectId
    )}/evidence/${encodeURIComponent(evidenceId)}${qs ? `?${qs}` : ""}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
}

/* ── Runs 历史 ───────────────────────────────────────────────────────── */

export function listRuns(projectId: string): Promise<RunListResponse> {
  return request<RunListResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs`
  );
}

export function getRunSnapshot(
  projectId: string,
  runId: string
): Promise<RunSnapshotResponse> {
  return request<RunSnapshotResponse>(
    `/api/projects/${encodeURIComponent(
      projectId
    )}/runs/${encodeURIComponent(runId)}/state`
  );
}

/* ── 归档 / 删除 ─────────────────────────────────────────────────────── */

export function archiveProject(projectId: string): Promise<Project> {
  return request<Project>(
    `/api/projects/${encodeURIComponent(projectId)}/archive`,
    { method: "POST" }
  );
}

export function restoreProject(projectId: string): Promise<Project> {
  return request<Project>(
    `/api/projects/${encodeURIComponent(projectId)}/restore`,
    { method: "POST" }
  );
}

export function deleteProject(projectId: string): Promise<Project> {
  return request<Project>(
    `/api/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE" }
  );
}

/* ── 全局指标 / LLM calls / 时序 ─────────────────────────────────────── */

export function aggregateMetrics(
  sinceIso?: string
): Promise<AggregateMetricsResponse> {
  const qs = sinceIso ? `?since_iso=${encodeURIComponent(sinceIso)}` : "";
  return request<AggregateMetricsResponse>(`/api/metrics/aggregate${qs}`);
}

export function metricsTimeseries(
  projectId: string
): Promise<MetricsTimeseriesResponse> {
  return request<MetricsTimeseriesResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/metrics/timeseries`
  );
}

export function projectLLMCalls(
  projectId: string,
  opts: { nodeId?: string; agentName?: string; limit?: number } = {}
): Promise<LLMCallsResponse> {
  const params = new URLSearchParams();
  if (opts.nodeId) params.set("node_id", opts.nodeId);
  if (opts.agentName) params.set("agent_name", opts.agentName);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return request<LLMCallsResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/llm-calls${qs ? `?${qs}` : ""}`
  );
}

export function allLLMCalls(limit = 500): Promise<LLMCallsResponse> {
  return request<LLMCallsResponse>(`/api/llm-calls?limit=${limit}`);
}

/* ── 导出 ────────────────────────────────────────────────────────────── */

/**
 * 导出文件下载 URL —— 走浏览器 GET（带 Content-Disposition），
 * 不通过 request() 是因为 PDF / DOCX 是二进制流，直接 anchor click 触发下载更省事。
 * PDF / DOCX 后端缺依赖时返回 503，调用方需要 fetch HEAD 预检或捕错处理。
 */
export function exportProjectUrl(
  projectId: string,
  fmt: ExportFormat
): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(
    projectId
  )}/export?format=${fmt}`;
}

/* ── ws helper ───────────────────────────────────────────────────────── */

export function eventsWsUrl(projectId: string): string {
  return `${WS_BASE}/api/projects/${encodeURIComponent(projectId)}/events`;
}

/* ── health ──────────────────────────────────────────────────────────── */

export function getHealth(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
