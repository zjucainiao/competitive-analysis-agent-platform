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
    { revalidateOnFocus: false, ...config }
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
      refreshInterval: 30000,
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
