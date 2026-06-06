import type { LucideIcon } from "lucide-react";

/**
 * 工作区统一空状态。真实项目里某 tab 数据尚未产出时用它，
 * 而不是回退到 demo / mock 内容。
 */
export function WorkspaceEmpty({
  title,
  desc,
  icon: Icon,
}: {
  title: string;
  desc?: string;
  icon?: LucideIcon;
}) {
  return (
    <div className="flex min-h-[320px] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-default bg-bg-raised/40 px-6 py-12 text-center">
      {Icon ? <Icon className="h-6 w-6 text-text-muted" /> : null}
      <div className="text-sm font-medium text-text-secondary">{title}</div>
      {desc ? (
        <p className="max-w-sm text-xs leading-relaxed text-text-muted">{desc}</p>
      ) : null}
    </div>
  );
}
