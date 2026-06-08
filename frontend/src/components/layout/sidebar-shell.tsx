"use client";

import { Sidebar } from "./sidebar";
import { TopBar } from "./top-bar";
import { OnboardingHint } from "./onboarding-hint";

/**
 * 应用主壳：左侧 240px Sidebar + 主内容区（TopBar + page content）。
 *
 * 替代旧 GlobalNav + AppShell 的顶部导航模型。
 *
 * 用法：
 *  <SidebarShell topBarLeft={<...>} topBarRight={<...>}>
 *    <PageContent />
 *  </SidebarShell>
 *
 *  // 三栏（首页 / 平台总览类）
 *  <SidebarShell overviewRail={<OverviewRail />}>...</SidebarShell>
 *
 * projectName 仅用于 workspace 路径下，sidebar 会渲染「当前项目」子菜单。
 *
 * overviewRail：可选 280px 概况左栏。传入后主内容区从 240+280=520px 起；
 * 在 < xl (1280px) 视口下，OverviewRail 自身用 hidden xl:flex 退场，
 * 此处主内容也会同步在窄屏退回到 240px 起，避免空白。
 */
export function SidebarShell({
  children,
  topBarLeft,
  topBarRight,
  topTabs,
  hero,
  overviewRail,
}: {
  children: React.ReactNode;
  topBarLeft?: React.ReactNode;
  topBarRight?: React.ReactNode;
  /** 主内容上方的横条 tab（仅 workspace 用 WorkspaceTopTabs），位于 TopBar 之下 */
  topTabs?: React.ReactNode;
  projectName?: string;
  /** 可选 hero 区（如首页大渐变 wash），传入后 main 容器宽度延伸至边缘 */
  hero?: React.ReactNode;
  /** 可选 280px 概况左栏，固定在 nav 右侧。仅 ≥ xl 视口生效。 */
  overviewRail?: React.ReactNode;
}) {
  // 主内容左 margin：默认 240 (nav)；有 overviewRail 时 ≥ xl 推到 520
  const mainOffset = overviewRail
    ? "ml-[240px] xl:ml-[520px]"
    : "ml-[240px]";

  return (
    <div className="min-h-screen">
      <Sidebar />
      {overviewRail}
      <div className={`${mainOffset} flex min-h-screen flex-col`}>
        <TopBar left={topBarLeft} right={topBarRight} />
        {topTabs}
        {hero}
        <main className="flex-1 px-8 py-6">{children}</main>
      </div>
      <OnboardingHint />
    </div>
  );
}
