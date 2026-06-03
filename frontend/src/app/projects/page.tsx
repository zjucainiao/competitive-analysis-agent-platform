import { SidebarShell } from "@/components/layout/sidebar-shell";
import { ProjectsList } from "@/components/projects-list";

/**
 * Project 列表页。
 *
 * 用新 SidebarShell（左侧 nav + slim 顶栏）替换旧 GlobalNav 顶部布局。
 */
export default function ProjectsPage() {
  return (
    <SidebarShell
      topBarLeft={
        <div className="text-xs text-text-muted">
          <span className="font-medium text-text-secondary">我的项目</span>
        </div>
      }
    >
      <div className="mx-auto max-w-6xl">
        <ProjectsList />
      </div>
    </SidebarShell>
  );
}
