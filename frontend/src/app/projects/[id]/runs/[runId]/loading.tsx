import { Loader2Icon } from "lucide-react";
import { SidebarShell } from "@/components/layout/sidebar-shell";

/**
 * Workspace 路由的 loading skeleton。
 * Next.js 16 自动在 async route 之上挂这个。
 */
export default function Loading() {
  return (
    <SidebarShell
      topBarLeft={
        <div className="inline-flex items-center gap-2 text-xs text-text-muted">
          <Loader2Icon className="h-3.5 w-3.5 animate-spin" />
          <span>初始化 workspace…</span>
        </div>
      }
    >
      <div className="mx-auto max-w-6xl space-y-4">
        <div className="flex gap-3">
          <Skel className="h-7 w-24 rounded-pill" />
          <Skel className="h-7 w-24 rounded-pill" />
          <Skel className="h-7 w-24 rounded-pill" />
        </div>
        <div className="card-soft p-6">
          <Skel className="h-4 w-64 mb-4" />
          <Skel className="h-[500px] w-full rounded-md" />
        </div>
      </div>
    </SidebarShell>
  );
}

function Skel({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse-soft rounded bg-bg-sunken ${className}`}
      aria-hidden
    />
  );
}
