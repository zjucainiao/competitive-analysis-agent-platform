"use client";

import { createContext, useContext, type ReactNode } from "react";

/**
 * Workspace 内所有「真实可执行」action 共用的 API 上下文。
 *
 * 设计：
 *  - `null` = 当前不在 workspace（或 demo 路径），action handler 走 toast-only 旧路径
 *  - `{ projectId, revalidate }` 时 handler 直接打后端 + 拉 SWR 刷 UI
 *
 * 这样做的好处：workspace-actions.ts 的 nodeActionsFor / workspaceActionsFor / REPORT_ACTIONS
 * 保持「纯函数 + 可选 api 参数」的形态，不被 React hook 污染；只在 consumer 端调
 * useWorkspaceApi() 读 context 后传进去。
 */
export interface WorkspaceApi {
  projectId: string;
  /** 当前 run id，用于深链（如 evidence 反向跳转回 report 段落）。 */
  runId: string;
  /** 触发 SWR /state 立刻重拉（也会顺手让 projects 列表失效）。 */
  revalidate: () => void | Promise<unknown>;
  /** 历史运行只读回放：为 true 时干预类动作（节点重跑 / 编辑 prompt /
   *  段落编辑 / 证据异议等）一律隐藏或拒绝，深链仍复用 projectId/runId。 */
  readOnly?: boolean;
}

const WorkspaceApiContext = createContext<WorkspaceApi | null>(null);

export function WorkspaceApiProvider({
  value,
  children,
}: {
  value: WorkspaceApi | null;
  children: ReactNode;
}) {
  return (
    <WorkspaceApiContext.Provider value={value}>
      {children}
    </WorkspaceApiContext.Provider>
  );
}

export function useWorkspaceApi(): WorkspaceApi | null {
  return useContext(WorkspaceApiContext);
}
