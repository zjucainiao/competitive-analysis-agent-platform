import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 统一的空状态。
 * 不是"没东西"的灰底页，而是教用户「这里会显示什么 + 下一步可以做什么」。
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-dashed border-border-default bg-bg-raised/60 px-8 py-12 text-center",
        className
      )}
    >
      {Icon ? (
        <div className="mx-auto inline-flex h-10 w-10 items-center justify-center rounded-pill bg-bg-sunken">
          <Icon className="h-4 w-4 text-text-muted" />
        </div>
      ) : null}
      <h3 className="mt-3 text-sm font-medium text-text-primary">{title}</h3>
      {description ? (
        <p className="mt-1 text-xs text-text-secondary leading-relaxed">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-4 inline-flex">{action}</div> : null}
    </div>
  );
}
