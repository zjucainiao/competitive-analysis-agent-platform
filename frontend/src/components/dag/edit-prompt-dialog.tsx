"use client";

import { useEffect, useState } from "react";
import { PenLineIcon, RotateCcwIcon } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import { submitEditedPrompt } from "@/lib/workspace-actions";

interface OpenEvent {
  nodeId: string;
  label: string;
  agentName: string;
  projectId: string;
}

/**
 * 全局 "Edit prompt" sheet。
 *
 * 监听 `atlas:edit-prompt` 自定义事件（由 nodeActionsFor 的 Edit prompt 触发），
 * 弹一个右侧 sheet 让用户改 prompt 文本，submit 后调
 * POST /api/projects/{id}/nodes/{nodeId}/edit-prompt 并触发该节点 + 下游重跑。
 *
 * 挂载于 ApiWorkspaceShell 顶层，全局只一份。
 */
export function EditPromptDialog() {
  const api = useWorkspaceApi();
  const [state, setState] = useState<OpenEvent | null>(null);
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    function onOpen(e: Event) {
      const detail = (e as CustomEvent<OpenEvent>).detail;
      if (!detail) return;
      setState(detail);
      setText("");
    }
    window.addEventListener("atlas:edit-prompt", onOpen);
    return () => window.removeEventListener("atlas:edit-prompt", onOpen);
  }, []);

  const open = state !== null;
  const minLen = 10;
  const canSubmit = text.trim().length >= minLen && !submitting && api !== null;

  async function handleSubmit() {
    if (!state || !api || !canSubmit) return;
    setSubmitting(true);
    try {
      await submitEditedPrompt(api, state.nodeId, text.trim());
      setState(null);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={(o) => !o && setState(null)}>
      <SheetContent
        side="right"
        className="!w-[520px] !max-w-[520px] gap-0 overflow-y-auto p-0"
      >
        {state ? (
          <>
            <SheetHeader className="gap-2 border-b border-border-subtle p-5">
              <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-text-muted">
                <PenLineIcon className="h-3 w-3" />
                <span>Edit prompt · rerun node</span>
              </div>
              <SheetTitle className="text-base font-semibold">
                {state.label}
              </SheetTitle>
              <SheetDescription className="text-xs text-text-secondary">
                <code className="font-mono">{state.nodeId}</code> · agent{" "}
                <code className="font-mono">{state.agentName}</code> ·
                提交后该节点 + 全部传递下游会重置为 PENDING，等下次 dispatch
                轮触发重跑。
              </SheetDescription>
            </SheetHeader>

            <div className="space-y-4 p-5">
              <div>
                <div className="mb-2 flex items-center justify-between text-[10px] font-medium uppercase tracking-wider text-text-muted">
                  <span>Prompt override</span>
                  <span className="font-mono tabular-nums">
                    {text.trim().length} / min {minLen}
                  </span>
                </div>
                <Textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={14}
                  placeholder={`例：${state.agentName} 重新抓取时只保留过去 6 个月的 changelog，跳过 changelog v1 / v2 早期版本。`}
                  className="font-mono text-xs leading-relaxed"
                />
                <p className="mt-1.5 text-[11px] text-text-muted leading-relaxed">
                  这段文本会写入节点 metadata.user_prompt_override · Agent 在重跑时
                  优先读取该字段（具体生效程度取决于 Agent 实现）。
                </p>
              </div>
            </div>

            <div className="sticky bottom-0 flex items-center justify-end gap-2 border-t border-border-subtle bg-bg-overlay px-5 py-3">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setState(null)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="gap-1.5"
              >
                <RotateCcwIcon className="h-3.5 w-3.5" />
                <span>{submitting ? "Submitting…" : "覆盖 prompt 并 rerun"}</span>
              </Button>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
