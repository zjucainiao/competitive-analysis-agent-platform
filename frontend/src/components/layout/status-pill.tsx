import { cn } from "@/lib/utils";

/**
 * 状态 pill。与 DESIGN.md § Status Badge 映射严格一致。
 * 用于 ContextBar 的 run status、Trace 行、DAG 监听等。
 */
export type StatusTone =
  | "success"
  | "running"
  | "rework"
  | "warning"
  | "error"
  | "neutral";

const TONE_CLASSES: Record<
  StatusTone,
  { bg: string; border: string; text: string; dot: string }
> = {
  success: {
    bg: "bg-success-bg",
    border: "border-success-border",
    text: "text-success-base",
    dot: "bg-success-base",
  },
  running: {
    bg: "bg-running-bg",
    border: "border-running-border",
    text: "text-running-base",
    dot: "bg-running-base",
  },
  rework: {
    bg: "bg-rework-bg",
    border: "border-rework-border",
    text: "text-rework-base",
    dot: "bg-rework-base",
  },
  warning: {
    bg: "bg-warning-bg",
    border: "border-warning-border",
    text: "text-warning-base",
    dot: "bg-warning-base",
  },
  error: {
    bg: "bg-error-bg",
    border: "border-error-border",
    text: "text-error-base",
    dot: "bg-error-base",
  },
  neutral: {
    bg: "bg-neutral-bg",
    border: "border-neutral-border",
    text: "text-neutral-base",
    dot: "bg-neutral-base",
  },
};

export function StatusPill({
  tone,
  label,
  pulse,
  className,
}: {
  tone: StatusTone;
  label: React.ReactNode;
  pulse?: boolean;
  className?: string;
}) {
  const t = TONE_CLASSES[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 text-xs font-medium",
        t.bg,
        t.border,
        t.text,
        className
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-pill shrink-0",
          t.dot,
          pulse && "animate-pulse-soft"
        )}
      />
      {label}
    </span>
  );
}
