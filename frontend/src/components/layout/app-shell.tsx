import { GlobalNav } from "./global-nav";
import { ContextBar, type RunContext } from "./context-bar";
import { TabsRow } from "./tabs-row";
import { OnboardingHint } from "./onboarding-hint";

/**
 * Workspace 整体外壳：
 *
 *   ┌────────────────────────────┐
 *   │ GlobalNav      52px        │  sticky
 *   ├────────────────────────────┤
 *   │ ContextBar     72px        │
 *   ├────────────────────────────┤
 *   │ TabsRow        44px        │
 *   ├────────────────────────────┤
 *   │ Main                       │  ← children (tab content)
 *   │                            │     drawer/sheet 由各 tab 自管
 *   └────────────────────────────┘
 */
export function AppShell({
  ctx,
  children,
}: {
  ctx: RunContext;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-full bg-background">
      <GlobalNav />
      <ContextBar ctx={ctx} />
      <TabsRow />
      <main className="mx-auto max-w-[1600px] px-10 py-8">{children}</main>
      <OnboardingHint />
    </div>
  );
}
