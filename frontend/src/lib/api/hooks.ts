"use client";

import useSWR, { type SWRConfiguration, type SWRResponse, mutate } from "swr";
import {
  listProjects,
  getProject,
  getRunStateView,
} from "./client";
import type {
  Project,
  ProjectListResponse,
  ProjectStatus,
  RunStateView,
} from "./types";

/**
 * SWR hooks. 用 endpoint URL 当 cache key，全局 mutate("/api/projects") 等
 * 触发 revalidation。
 */

export const KEYS = {
  projects(filter?: { owner?: string; status?: ProjectStatus }) {
    const sp = new URLSearchParams();
    if (filter?.owner) sp.set("owner", filter.owner);
    if (filter?.status) sp.set("project_status", filter.status);
    const qs = sp.toString();
    return `/api/projects${qs ? `?${qs}` : ""}`;
  },
  project(id: string) {
    return `/api/projects/${id}`;
  },
  runState(id: string) {
    return `/api/projects/${id}/run-state`;
  },
};

export function useProjects(
  filter?: { owner?: string; status?: ProjectStatus },
  config?: SWRConfiguration<ProjectListResponse>
): SWRResponse<ProjectListResponse> {
  return useSWR<ProjectListResponse>(
    KEYS.projects(filter),
    () => listProjects(filter ?? {}),
    {
      revalidateOnFocus: false,
      ...config,
    }
  );
}

export function useProject(
  id: string | null | undefined,
  config?: SWRConfiguration<Project>
): SWRResponse<Project> {
  return useSWR<Project>(
    id ? KEYS.project(id) : null,
    () => getProject(id!),
    {
      revalidateOnFocus: false,
      // 运行中的项目轮询，让顶部状态 pill 在 run 结束后能自动翻到 done/failed；
      // 静态项目(draft/done/failed)停轮询。
      refreshInterval: (latest?: Project) =>
        latest === undefined ||
        latest.status === "running" ||
        latest.status === "reviewing" ||
        latest.status === "planning"
          ? 5000
          : 0,
      ...config,
    }
  );
}

export function useRunState(
  id: string | null | undefined,
  config?: SWRConfiguration<RunStateView>
): SWRResponse<RunStateView> {
  return useSWR<RunStateView>(
    id ? KEYS.runState(id) : null,
    () => getRunStateView(id!),
    {
      revalidateOnFocus: false,
      // 动态轮询：运行中(含 QA 返工，后端此时 status 仍为 "running")每 3s 拉一次，
      // 给足「实时感」；跑到终态(done/failed/aborted)即停，避免空轮询。
      // 旧实现固定 30s —— run 每 ~15s 推进一个节点，30s 轮询会让进度「跳着走」，
      // 返工时尤其割裂。
      refreshInterval: (latest?: RunStateView) =>
        latest === undefined || latest.status === "running" ? 3000 : 0,
      ...config,
    }
  );
}

/* ── revalidation helpers ───────────────────────────────────────────── */

export const revalidate = {
  projects() {
    return mutate(
      (k: unknown): k is string =>
        typeof k === "string" && k.startsWith("/api/projects"),
      undefined,
      { revalidate: true }
    );
  },
  project(id: string) {
    return mutate(KEYS.project(id));
  },
  runState(id: string) {
    return mutate(KEYS.runState(id));
  },
};
