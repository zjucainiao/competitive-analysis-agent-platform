import Link from "next/link";
import { ArrowLeftIcon } from "lucide-react";
import { GlobalNav } from "@/components/layout/global-nav";
import { WizardLayout } from "@/components/wizard";

/**
 * /projects/new · 真实 4 步创建向导。
 * Sprint 2 接入 ProjectService.create()。
 */
export default function NewProjectPage() {
  return (
    <div className="min-h-full bg-background">
      <GlobalNav />
      <div className="mx-auto max-w-5xl px-10 py-10">
        <Link
          href="/projects"
          className="inline-flex items-center gap-1 text-xs text-text-muted transition-colors duration-120 ease-out-quart hover:text-text-secondary"
        >
          <ArrowLeftIcon className="h-3 w-3" />
          <span>Back to Projects</span>
        </Link>
        <div className="mt-6">
          <WizardLayout />
        </div>
      </div>
    </div>
  );
}
