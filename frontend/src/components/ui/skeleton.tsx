import { cn } from "@/lib/utils";

/** 通用骨架占位块。带温和 pulse。 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse-soft rounded-sm bg-bg-sunken",
        className
      )}
      aria-hidden
      {...props}
    />
  );
}
