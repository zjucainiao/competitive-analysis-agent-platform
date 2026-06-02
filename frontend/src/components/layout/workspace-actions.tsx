"use client";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  workspaceActionsFor,
  type ActionDef,
  type RunStatus,
} from "@/lib/workspace-actions";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import { cn } from "@/lib/utils";

/**
 * Workspace 右上动作组。
 *
 * 设计原则：
 *  - 主要 action（primary）用朱漆橙 fill
 *  - 二级 action 用 secondary / ghost
 *  - destructive 用 destructive variant + confirm
 *  - 每个按钮都有 Tooltip，避免靠 label 长度判断
 *  - 紧凑：sm size，间距 6/8px
 */
export function WorkspaceActions({ status }: { status: RunStatus }) {
  const api = useWorkspaceApi();
  const actions = workspaceActionsFor(status, api);

  return (
    <div className="flex items-center gap-1.5">
      {actions.map((a, i) => (
        <ActionButton
          key={a.id}
          action={a}
          /* primary 永远在最右（视线终点） */
          emphasized={a.variant === "primary"}
          last={i === actions.length - 1}
        />
      ))}
    </div>
  );
}

function ActionButton({
  action,
  emphasized,
}: {
  action: ActionDef;
  emphasized?: boolean;
  last?: boolean;
}) {
  const Icon = action.icon;

  const variant: "default" | "secondary" | "ghost" | "destructive" | "outline" =
    action.variant === "primary"
      ? "default"
      : action.variant === "destructive"
        ? "destructive"
        : action.variant === "secondary"
          ? "outline"
          : "ghost";

  const button = (
    <Button
      type="button"
      size="sm"
      variant={variant}
      onClick={action.run}
      className={cn(
        "gap-1.5",
        emphasized && "shadow-[0_1px_0_0_oklch(76%_0.16_42)]"
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{action.label}</span>
    </Button>
  );

  if (!action.hint) return button;

  return (
    <Tooltip>
      <TooltipTrigger render={button} />
      <TooltipContent>{action.hint}</TooltipContent>
    </Tooltip>
  );
}
