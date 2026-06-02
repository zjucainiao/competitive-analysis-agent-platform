import { cn } from "@/lib/utils";

/**
 * 键盘快捷键显示器。Linear / Raycast 风格。
 * 用于全局命令面板按钮、tooltip 提示等。
 */
export function Kbd({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-sm",
        "border border-border-default bg-bg-raised px-1",
        "font-mono text-[11px] leading-none text-text-muted",
        className
      )}
    >
      {children}
    </kbd>
  );
}
