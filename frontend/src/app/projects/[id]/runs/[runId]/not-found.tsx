import Link from "next/link";
import { SidebarShell } from "@/components/layout/sidebar-shell";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <SidebarShell
      topBarLeft={
        <div className="text-xs text-text-muted">
          <span className="font-medium text-text-secondary">404 · run not found</span>
        </div>
      }
    >
      <div className="mx-auto flex max-w-xl flex-col items-start gap-5 py-16">
        <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
          404 · run not found
        </div>
        <h1 className="text-2xl font-semibold text-text-primary">
          这个 run 不存在
        </h1>
        <p className="text-sm text-text-secondary">
          检查 project_id / run_id 是否正确；或打开 demo workspace 看 design 预览。
        </p>
        <div className="flex gap-2">
          <Button render={<Link href="/projects" />}>← 我的项目</Button>
          <Button variant="ghost" render={<Link href="/projects/demo/runs/01" />}>
            打开 demo
          </Button>
        </div>
      </div>
    </SidebarShell>
  );
}
