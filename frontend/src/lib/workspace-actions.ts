"use client";

import { toast } from "sonner";
import type { LucideIcon } from "lucide-react";
import {
  PauseIcon,
  PlayIcon,
  SquareIcon,
  RotateCcwIcon,
  CheckCheckIcon,
  PenLineIcon,
  ShareIcon,
  DownloadIcon,
  SparklesIcon,
  RefreshCcwIcon,
  SkipForwardIcon,
  MessageSquarePlusIcon,
  StarIcon,
  CopyIcon,
} from "lucide-react";
import {
  ApiError,
  overrideQA,
  patchEvidence,
  pauseRun,
  stopRun,
  restartRun,
  startRun,
  retryNode,
  skipNode,
  forceStartNode,
  editPromptAndRerun,
} from "@/lib/api/client";
import type { WorkspaceApi } from "@/lib/workspace-api-context";

/**
 * Workspace / DAG / Report 三块共用的「动作」定义。
 *
 * 双路径设计：
 *  - `api` 参数 = WorkspaceApi（projectId + revalidate）→ 真实打后端 + 刷 SWR
 *  - 没传 = demo / mock 路径，handler 只 toast（保留给 /projects/demo 演示）
 *
 * 所有 handler 都 emitIntervention，方便客户端累计 metrics.edit_rate；
 * 真实路径下后端 metrics.manual_edits 也会同步增加（双计也无所谓，UI 取后端值）。
 */

export type RunStatus = "running" | "rework" | "success" | "failed" | "pending";

export interface ActionDef {
  id: string;
  label: string;
  icon: LucideIcon;
  hint?: string;
  /** 'primary' 用 accent fill；'destructive' 走 error；'secondary' / 'ghost' 默认低调 */
  variant?: "primary" | "destructive" | "secondary" | "ghost";
  /** 是否需要二次确认（destructive 默认 true） */
  confirm?: boolean;
  run: () => void | Promise<void>;
}

/** 统一处理 ApiError → toast.error，避免每个 handler 写一遍。 */
function reportApiError(action: string, err: unknown): void {
  const msg =
    err instanceof ApiError
      ? `${err.status} · ${err.message}`
      : err instanceof Error
        ? err.message
        : String(err);
  toast.error(`${action} 失败`, { description: msg });
}

async function withRevalidate(api: WorkspaceApi): Promise<void> {
  try {
    await api.revalidate();
  } catch {
    /* 静默 — SWR 内部会再重试，不要打扰用户 */
  }
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Intervention tracking — 计入 metrics.edit_rate / 评分项「人工修正率」     */

const INTERVENTION_KEY = "atlas:interventions";

interface InterventionEntry {
  at: number;
  type: string;
  target: string;
}

export function emitIntervention(type: string, target: string) {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(INTERVENTION_KEY);
    const list: InterventionEntry[] = raw ? JSON.parse(raw) : [];
    list.push({ at: Date.now(), type, target });
    window.localStorage.setItem(INTERVENTION_KEY, JSON.stringify(list.slice(-100)));
    window.dispatchEvent(new CustomEvent("atlas:intervention", { detail: { type, target } }));
  } catch {
    /* localStorage 可能被 disable，吞掉 */
  }
}

export function getInterventionCount(): number {
  if (typeof window === "undefined") return 0;
  try {
    const raw = window.localStorage.getItem(INTERVENTION_KEY);
    if (!raw) return 0;
    return (JSON.parse(raw) as InterventionEntry[]).length;
  } catch {
    return 0;
  }
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Workspace-level actions（顶部右侧）                                       */

export function workspaceActionsFor(
  status: RunStatus,
  api?: WorkspaceApi | null
): ActionDef[] {
  const common: ActionDef[] = [
    {
      id: "workspace.share",
      label: "Share",
      icon: ShareIcon,
      hint: "复制可分享链接",
      run: () => {
        if (typeof navigator !== "undefined" && navigator.clipboard) {
          navigator.clipboard
            .writeText(window.location.href)
            .then(() => toast.success("链接已复制", { description: "可发送给团队成员" }))
            .catch(() => toast.error("复制失败"));
        } else {
          toast.success("链接已复制");
        }
        emitIntervention("share", "workspace");
      },
    },
    {
      id: "workspace.export",
      label: "Export",
      icon: DownloadIcon,
      hint: "导出 Markdown / PDF / DOCX / JSON",
      run: () => {
        // 顶栏「Export」点击 → 触发自定义事件，让 workspace 顶层打开 export menu sheet。
        // 这里不走 toast.info 提示，避免和真实 menu 行为冲突。
        if (typeof window !== "undefined") {
          window.dispatchEvent(new CustomEvent("atlas:open-export"));
        }
        emitIntervention("export", "report");
      },
    },
  ];

  if (status === "running" || status === "rework") {
    return [
      {
        id: "workspace.pause",
        label: status === "rework" ? "Pause run" : "Pause",
        icon: PauseIcon,
        hint: "暂停 Orchestrator 派发新节点",
        variant: "secondary",
        run: async () => {
          if (api) {
            try {
              const res = await pauseRun(api.projectId);
              toast.info("Run 已暂停", {
                description: res.cancelled_task
                  ? "后台任务已取消 · 用 Resume 恢复"
                  : "当前未在跑（task already done）",
              });
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Pause", e);
              return;
            }
          } else {
            toast.info("Run 已暂停", {
              description: "已停止派发新节点；正在运行的节点会跑完",
            });
          }
          emitIntervention("pause", "run");
        },
      },
      ...(status === "rework"
        ? [
            {
              id: "workspace.override",
              label: "Override · accept v1",
              icon: CheckCheckIcon,
              variant: "primary" as const,
              hint: "接受当前 reporter 版本，跳过反馈环重跑",
              run: async () => {
                if (api) {
                  try {
                    const res = await overrideQA(api.projectId);
                    toast.success("已 override · 接受为终稿", {
                      description: `final node = ${res.accepted_report_node_id} · ${
                        res.skipped_node_ids.length
                      } 个 pending 节点已 skip · edit_rate ${(
                        res.edit_rate * 100
                      ).toFixed(0)}%`,
                    });
                    await withRevalidate(api);
                  } catch (e) {
                    reportApiError("Override", e);
                    return;
                  }
                } else {
                  toast.success("已 override · v1 接受为终稿", {
                    description:
                      "QA verdict blocking=false · end 节点已 ready · 计入人工修正率",
                  });
                }
                emitIntervention("override", "qa");
              },
            },
          ]
        : []),
      ...common,
      {
        id: "workspace.new",
        label: "New analysis",
        icon: SparklesIcon,
        variant: "primary",
        run: () => {
          if (typeof window !== "undefined") {
            window.location.href = "/projects/new";
          }
        },
      },
    ];
  }

  if (status === "success") {
    return [
      {
        id: "workspace.publish",
        label: "Publish report",
        icon: CheckCheckIcon,
        variant: "primary",
        hint: "发布为团队可见",
        run: () => {
          toast.success("报告已发布", {
            description: "团队成员可在 /projects 中查看终稿",
          });
          emitIntervention("publish", "report");
        },
      },
      {
        id: "workspace.rerun",
        label: "Rerun",
        icon: RotateCcwIcon,
        hint: "新起一次 run（生成新 run_id，旧 run 历史保留）",
        run: async () => {
          if (api) {
            try {
              const res = await restartRun(api.projectId);
              toast.info("已派发新一轮 run", {
                description: `${res.action} · cancelled_task=${res.cancelled_task}`,
              });
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Rerun", e);
              return;
            }
          } else {
            toast.info("已派发新一轮 run");
          }
          emitIntervention("rerun", "run");
        },
      },
      ...common,
    ];
  }

  if (status === "failed") {
    return [
      {
        id: "workspace.investigate",
        label: "Investigate",
        icon: PenLineIcon,
        variant: "primary",
        run: () => {
          toast.warning("跳转到失败 trace", {
            description: "已选中第一个 FAILED 节点",
          });
        },
      },
      {
        id: "workspace.restart",
        label: "Restart from start",
        icon: RotateCcwIcon,
        variant: "destructive",
        confirm: true,
        run: async () => {
          if (api) {
            try {
              await restartRun(api.projectId);
              toast.info("Run 已重启 · 新 plan 生成");
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Restart", e);
              return;
            }
          } else {
            toast.info("Run 已重启");
          }
          emitIntervention("restart", "run");
        },
      },
      ...common,
    ];
  }

  /* pending */
  return [
    {
      id: "workspace.start",
      label: "Start run",
      icon: PlayIcon,
      variant: "primary",
      run: async () => {
        if (api) {
          try {
            await startRun(api.projectId);
            toast.success("Run 启动");
            await withRevalidate(api);
          } catch (e) {
            reportApiError("Start", e);
            return;
          }
        } else {
          toast.success("Run 启动");
        }
        emitIntervention("start", "run");
      },
    },
    ...common,
    {
      id: "workspace.stop",
      label: "Stop",
      icon: SquareIcon,
      variant: "destructive",
      confirm: true,
      hint: "硬停：取消任务 + 全部 pending 节点 skip",
      run: async () => {
        if (api) {
          try {
            const res = await stopRun(api.projectId);
            toast.warning("Run 已强停", {
              description: `cancelled_task=${res.cancelled_task} · plan_reset=${res.plan_status_reset}`,
            });
            await withRevalidate(api);
          } catch (e) {
            reportApiError("Stop", e);
            return;
          }
        } else {
          toast.warning("Run 已强停");
        }
        emitIntervention("stop", "run");
      },
    },
  ];
}

/* ────────────────────────────────────────────────────────────────────────── */
/* DAG node-level actions（hover chips + sheet footer）                       */

export interface NodeContext {
  nodeId: string;
  label: string;
  agentName: string;
  status: RunStatus | "neutral" | "warning";
  /** 真实模式下传 api context；不传则 toast-only */
  api?: WorkspaceApi | null;
}

export function nodeActionsFor(ctx: NodeContext): ActionDef[] {
  const { nodeId, label, agentName, status, api } = ctx;

  const baseEdit: ActionDef = {
    id: `node.${nodeId}.edit-prompt`,
    label: "Edit prompt",
    icon: PenLineIcon,
    hint: `编辑 ${agentName} 的 prompt 并 rerun`,
    run: async () => {
      if (api) {
        // 真实模式：触发自定义事件让 workspace 弹一个 prompt 编辑 dialog
        if (typeof window !== "undefined") {
          window.dispatchEvent(
            new CustomEvent("atlas:edit-prompt", {
              detail: { nodeId, label, agentName, projectId: api.projectId },
            })
          );
        }
      } else {
        toast.info("Prompt 编辑器打开", {
          description: `${label} · 编辑保存后会触发该节点 + 下游节点重新运行`,
        });
      }
      emitIntervention("edit-prompt", nodeId);
    },
  };

  const baseNote: ActionDef = {
    id: `node.${nodeId}.note`,
    label: "Add note",
    icon: MessageSquarePlusIcon,
    hint: "团队批注（v1 本地，未持久化）",
    variant: "ghost",
    run: () => {
      toast.info("批注面板", { description: `已为 ${label} 添加批注线程` });
      emitIntervention("note", nodeId);
    },
  };

  if (status === "success") {
    return [
      {
        id: `node.${nodeId}.rerun`,
        label: "Rerun",
        icon: RotateCcwIcon,
        hint: "节点重置为 PENDING + 所有下游重置",
        run: async () => {
          if (api) {
            try {
              const res = await retryNode(api.projectId, nodeId);
              toast.info(`${label} 已重跑`, {
                description: `${res.affected_downstream.length} 个下游节点已重置 · 调用 POST /run 继续`,
              });
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Rerun", e);
              return;
            }
          } else {
            toast.info(`${label} 已重跑`, {
              description: "下游节点已重置为 PENDING",
            });
          }
          emitIntervention("rerun", nodeId);
        },
      },
      baseEdit,
      baseNote,
    ];
  }

  if (status === "running") {
    return [
      {
        id: `node.${nodeId}.pause`,
        label: "Pause this",
        icon: PauseIcon,
        variant: "secondary",
        hint: "节点级 pause = 整 run pause（v1 简化）",
        run: async () => {
          if (api) {
            try {
              await pauseRun(api.projectId);
              toast.info(`${label} 所在 run 已暂停`);
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Pause", e);
              return;
            }
          } else {
            toast.info(`${label} 已暂停`);
          }
          emitIntervention("pause", nodeId);
        },
      },
      baseEdit,
      baseNote,
    ];
  }

  if (status === "rework") {
    return [
      {
        id: `node.${nodeId}.accept-v1`,
        label: "Override · accept v1",
        icon: CheckCheckIcon,
        variant: "primary",
        hint: "接受当前 reporter，跳过反馈环重跑",
        run: async () => {
          if (api) {
            try {
              const res = await overrideQA(api.projectId);
              toast.success("Verdict overridden", {
                description: `final = ${res.accepted_report_node_id} · ${res.skipped_node_ids.length} 节点 skip`,
              });
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Override", e);
              return;
            }
          } else {
            toast.success("Verdict overridden · v1 accepted", {
              description: `${label} blocking=false · 下游 reporter_v2 取消`,
            });
          }
          emitIntervention("override", nodeId);
        },
      },
      baseNote,
    ];
  }

  if (status === "failed") {
    return [
      {
        id: `node.${nodeId}.retry`,
        label: "Retry",
        icon: RefreshCcwIcon,
        variant: "primary",
        run: async () => {
          if (api) {
            try {
              const res = await retryNode(api.projectId, nodeId);
              toast.info(`${label} 已重试`, {
                description: `${res.affected_downstream.length} 个下游节点重置`,
              });
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Retry", e);
              return;
            }
          } else {
            toast.info(`${label} 已重试`);
          }
          emitIntervention("retry", nodeId);
        },
      },
      {
        id: `node.${nodeId}.skip`,
        label: "Skip",
        icon: SkipForwardIcon,
        variant: "destructive",
        confirm: true,
        hint: "跳过本节点，下游用 partial 数据继续",
        run: async () => {
          if (api) {
            try {
              await skipNode(api.projectId, nodeId);
              toast.warning(`${label} 已跳过`, {
                description: "下游将以 partial 数据继续；最终报告会标注「未完整覆盖」",
              });
              await withRevalidate(api);
            } catch (e) {
              reportApiError("Skip", e);
              return;
            }
          } else {
            toast.warning(`${label} 已跳过`);
          }
          emitIntervention("skip", nodeId);
        },
      },
      baseEdit,
      baseNote,
    ];
  }

  /* pending / neutral */
  return [
    {
      id: `node.${nodeId}.force-start`,
      label: "Force start",
      icon: PlayIcon,
      hint: "忽略未满足的依赖，立即启动",
      variant: "secondary",
      run: async () => {
        if (api) {
          try {
            await forceStartNode(api.projectId, nodeId);
            toast.warning(`${label} force-started`, {
              description: "未满足的依赖被标 SKIPPED · 调用 POST /run 触发调度",
            });
            await withRevalidate(api);
          } catch (e) {
            reportApiError("Force-start", e);
            return;
          }
        } else {
          toast.warning(`${label} force-started`, {
            description: "依赖未全部 ready · 视情况降级",
          });
        }
        emitIntervention("force-start", nodeId);
      },
    },
    {
      id: `node.${nodeId}.skip`,
      label: "Skip",
      icon: SkipForwardIcon,
      variant: "ghost",
      run: async () => {
        if (api) {
          try {
            await skipNode(api.projectId, nodeId);
            toast.info(`${label} 已跳过`);
            await withRevalidate(api);
          } catch (e) {
            reportApiError("Skip", e);
            return;
          }
        } else {
          toast.info(`${label} 已跳过`);
        }
        emitIntervention("skip", nodeId);
      },
    },
    baseNote,
  ];
}

/** 暴露给 sheet「Edit prompt」dialog 的 submit handler。 */
export async function submitEditedPrompt(
  api: WorkspaceApi,
  nodeId: string,
  promptOverride: string
): Promise<void> {
  try {
    const res = await editPromptAndRerun(api.projectId, nodeId, {
      prompt_override: promptOverride,
    });
    toast.success(`${nodeId} prompt 已覆盖 · 节点重置`, {
      description: `${res.affected_downstream.length} 个下游节点 reset · 调用 POST /run 重启调度`,
    });
    await withRevalidate(api);
  } catch (e) {
    reportApiError("Edit prompt", e);
  }
}

/* ────────────────────────────────────────────────────────────────────────── */
/* Report-level actions（章节 / 段落 / Evidence）                            */

/**
 * Report 行内动作。
 *
 * 段落保存（saveEdit）的真实 PATCH 在 ReportLayout 自己里调，因为需要 reportNodeId
 * + paragraphId 完整上下文；这里 saveEdit 只剩 toast + intervention（保留接口形状）。
 *
 * Evidence dispute 是真业务动作 → markEvidenceDisputed 接 PATCH /evidence/{id}?auto_rework=true
 * 走真实 API（带 api 时）；不带 api 时 toast-only。
 */
export const REPORT_ACTIONS = {
  saveEdit(paragraphId: string) {
    toast.success("段落已保存为 v2", {
      description: `${paragraphId} · 计入 metrics.edit_rate`,
    });
    emitIntervention("edit-paragraph", paragraphId);
  },
  cancelEdit() {
    toast("编辑已取消");
  },
  async markEvidenceDisputed(
    evidenceId: string,
    opts: { api?: WorkspaceApi | null; reason?: string; autoRework?: boolean } = {}
  ): Promise<void> {
    const { api, reason, autoRework = true } = opts;
    if (api) {
      try {
        const res = await patchEvidence(
          api.projectId,
          evidenceId,
          { disputed: true, reason: reason ?? null },
          { autoRework }
        );
        if (res.auto_rework_triggered) {
          toast.warning("Evidence disputed · QA 重审已触发", {
            description: `引用此 evidence 的 ${
              res.affected_paragraph_ids.length
            } 个段落 · 派生节点 ${res.rework_new_node_ids.join(", ") || "—"}`,
          });
        } else {
          toast.warning("Evidence 已标记 disputed", {
            description: `${res.located_in_node} · manual_edits=${res.manual_edits}`,
          });
        }
        await withRevalidate(api);
      } catch (e) {
        reportApiError("Mark disputed", e);
        return;
      }
    } else {
      toast.warning("Evidence 已标记 disputed", {
        description: `${evidenceId} · 引用此 evidence 的段落会触发 QA 重审`,
      });
    }
    emitIntervention("dispute-evidence", evidenceId);
  },
  async unmarkEvidenceDisputed(
    evidenceId: string,
    opts: { api?: WorkspaceApi | null } = {}
  ): Promise<void> {
    const { api } = opts;
    if (api) {
      try {
        await patchEvidence(
          api.projectId,
          evidenceId,
          { disputed: false, reason: null },
          { autoRework: false }
        );
        toast.success("Disputed 撤销", { description: evidenceId });
        await withRevalidate(api);
      } catch (e) {
        reportApiError("Undispute", e);
        return;
      }
    } else {
      toast.success("Disputed 标记已撤销", { description: evidenceId });
    }
    emitIntervention("undispute-evidence", evidenceId);
  },
  copyEvidence(evidenceId: string) {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(`Evidence ${evidenceId}`);
    }
    toast.success("Evidence 已复制", { description: evidenceId });
  },
  starEvidence(evidenceId: string) {
    toast.success("Evidence 已加星");
    emitIntervention("star-evidence", evidenceId);
  },
} as const;

/* ────────────────────────────────────────────────────────────────────────── */
/* Misc icon re-export (避免每个文件单独从 lucide 引一遍)                    */

export const ActionIcons = {
  Pause: PauseIcon,
  Play: PlayIcon,
  Stop: SquareIcon,
  Rerun: RotateCcwIcon,
  Override: CheckCheckIcon,
  Edit: PenLineIcon,
  Share: ShareIcon,
  Export: DownloadIcon,
  New: SparklesIcon,
  Retry: RefreshCcwIcon,
  Skip: SkipForwardIcon,
  Note: MessageSquarePlusIcon,
  Star: StarIcon,
  Copy: CopyIcon,
};
