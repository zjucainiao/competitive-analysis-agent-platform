import type {
  AggregateMetricsResponse,
  DiscoverCompetitorsRequest,
  DiscoverCompetitorsResponse,
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
  RunStateView,
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

/**
 * 把任意错误转成给用户看的中文提示：优先后端 detail，常见状态码给友好话术，
 * 兜底才用原始 message。toast 里用它，避免暴露 "POST /api/... → 400" 这种开发文案。
 */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return "无法连接后端，请确认服务正在运行";
    if (e.status === 401) return "登录已过期，请重新登录";
    if (e.status === 403) return "没有权限执行此操作";
    if (e.status === 409) return "已有一个运行正在进行中";
    const detail =
      e.body && typeof e.body === "object" && "detail" in e.body
        ? (e.body as { detail: unknown }).detail
        : null;
    if (typeof detail === "string" && detail) return detail;
    if (e.status >= 500) return "服务器出错了，请稍后重试";
    return "请求失败，请重试";
  }
  return e instanceof Error ? e.message : String(e);
}

/* ── auth token ──────────────────────────────────────────────────────────
 * JWT 存 localStorage，并缓存在内存里（避免每次请求读 storage / SSR 安全）。
 * 401 时由 onUnauthorized 钩子通知 auth-context 跳登录页。 */

const TOKEN_KEY = "caap_token";
let _token: string | null = null;
let _onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null): void {
  _token = token;
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export function getAuthToken(): string | null {
  if (_token) return _token;
  if (typeof window === "undefined") return null;
  _token = window.localStorage.getItem(TOKEN_KEY);
  return _token;
}

/** auth-context 注册：收到 401 时清 token + 跳登录。 */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  _onUnauthorized = fn;
}

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  let res: Response;
  const token = getAuthToken();
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
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
  if (res.status === 401) {
    // token 失效/缺失：清掉并通知上层跳登录
    setAuthToken(null);
    _onUnauthorized?.();
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

/** 自动发现竞品（auto_discover 模式专用）。失败时返 200 + competitors=[] + error。 */
export function discoverCompetitors(
  req: DiscoverCompetitorsRequest
): Promise<DiscoverCompetitorsResponse> {
  return request<DiscoverCompetitorsResponse>("/api/discover-competitors", {
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

/** LIVE RunStateView：原生引擎当前/最近一次 run 的「前端友好」视图（工作流步进器数据源）。 */
export function getRunStateView(projectId: string): Promise<RunStateView> {
  return request<RunStateView>(
    `/api/projects/${encodeURIComponent(projectId)}/run-state`
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
 * 导出文件下载 URL。注意后端 /export 与其它路由一样要求 Bearer JWT，
 * 浏览器 anchor GET 带不上 Authorization header 会 401，
 * 所以真正下载走 fetchProjectExport()（fetch → blob），此 URL 仅用于 UI 展示。
 */
export function exportProjectUrl(
  projectId: string,
  fmt: ExportFormat
): string {
  return `${API_BASE}/api/projects/${encodeURIComponent(
    projectId
  )}/export?format=${fmt}`;
}

/** Content-Disposition 兜底扩展名（后端文件名为 {project_id}.{ext}） */
const EXPORT_EXT: Record<ExportFormat, string> = {
  markdown: "md",
  pdf: "pdf",
  docx: "docx",
  json: "json",
};

/** 从 Content-Disposition 解析文件名；解析不出返回 null 由调用方兜底 */
function parseDispositionFilename(header: string | null): string | null {
  if (!header) return null;
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  if (!m) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return m[1];
  }
}

/**
 * 带 Authorization 的导出下载。不复用 request() 是因为 PDF / DOCX 是二进制流，
 * 要拿 blob 而非 json / text。失败抛 ApiError（503 = 后端缺 PDF / DOCX 导出依赖），
 * 文件名优先取后端 Content-Disposition，缺失时按 {projectId}.{ext} 兜底。
 */
export async function fetchProjectExport(
  projectId: string,
  fmt: ExportFormat
): Promise<{ blob: Blob; filename: string }> {
  const token = getAuthToken();
  let res: Response;
  try {
    res = await fetch(exportProjectUrl(projectId, fmt), {
      cache: "no-store",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch (e) {
    throw new ApiError(
      0,
      `Network error: ${e instanceof Error ? e.message : String(e)}`,
      null
    );
  }
  if (res.status === 401) {
    // token 失效/缺失：清掉并通知上层跳登录（与 request() 同一逻辑）
    setAuthToken(null);
    _onUnauthorized?.();
  }
  if (!res.ok) {
    const ct = res.headers.get("content-type") ?? "";
    const body = ct.includes("application/json")
      ? await res.json().catch(() => null)
      : await res.text().catch(() => "");
    const detail =
      (body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : null) ?? body;
    throw new ApiError(
      res.status,
      `GET /export?format=${fmt} → ${res.status} ${res.statusText}: ${
        typeof detail === "string" ? detail : JSON.stringify(detail)
      }`,
      body
    );
  }
  const blob = await res.blob();
  const filename =
    parseDispositionFilename(res.headers.get("content-disposition")) ??
    `${projectId}.${EXPORT_EXT[fmt]}`;
  return { blob, filename };
}

/* ── ws helper ───────────────────────────────────────────────────────── */

export function eventsWsUrl(projectId: string): string {
  // 浏览器 WebSocket 不能带 Authorization header，token 走 query param。
  const token = getAuthToken();
  const base = `${WS_BASE}/api/projects/${encodeURIComponent(projectId)}/events`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

/* ── auth ────────────────────────────────────────────────────────────── */

export interface AuthUser {
  user_id: string;
  email: string;
  display_name: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export function register(
  email: string,
  password: string,
  displayName = ""
): Promise<TokenResponse> {
  return request<TokenResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, display_name: displayName }),
  });
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function fetchMe(): Promise<AuthUser> {
  return request<AuthUser>("/api/auth/me");
}

/* ── health ──────────────────────────────────────────────────────────── */

export function getHealth(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
