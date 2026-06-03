import Link from "next/link";
import { ArrowLeftIcon } from "lucide-react";
import { SidebarShell } from "@/components/layout/sidebar-shell";
import { WizardLayout } from "@/components/wizard";

/**
 * /projects/new · 4 步创建向导。
 */
export default function NewProjectPage() {
  return (
    <SidebarShell
      topBarLeft={
        <Link
          href="/projects"
          className="inline-flex items-center gap-1 text-xs text-text-muted transition-colors duration-120 hover:text-text-secondary"
        >
          <ArrowLeftIcon className="h-3 w-3" />
          <span>返回 我的项目</span>
        </Link>
      }
    >
      <div className="mx-auto max-w-5xl">
        <WizardLayout />
      </div>
    </SidebarShell>
  );
}
