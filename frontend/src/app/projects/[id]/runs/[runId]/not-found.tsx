import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="min-h-full bg-background">
      <div className="mx-auto flex max-w-xl flex-col items-start gap-5 px-10 py-32">
        <div className="text-xs font-medium uppercase tracking-wider text-text-muted">
          404 · run not found
        </div>
        <h1 className="text-2xl font-semibold text-text-primary">
          这个 run 不存在
        </h1>
        <p className="text-sm text-text-secondary">
          v1 阶段只接入了演示 run。后续接入 API 后会从 ProjectService 拉真实数据。
        </p>
        <Button render={<Link href="/projects/demo/runs/01" />}>
          打开 demo workspace
        </Button>
      </div>
    </div>
  );
}
