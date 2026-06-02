import { GlobalNav } from "@/components/layout/global-nav";
import { ProjectsList } from "@/components/projects-list";

/**
 * Project 列表页 · v1 mock 多卡片 + 过滤 + 排序 + 搜索 + 快捷动作。
 * Sprint 2 接入 ProjectService.list()。
 */
export default function ProjectsPage() {
  return (
    <div className="min-h-full bg-background">
      <GlobalNav />
      <div className="mx-auto max-w-6xl px-10 py-12">
        <ProjectsList />
      </div>
    </div>
  );
}
