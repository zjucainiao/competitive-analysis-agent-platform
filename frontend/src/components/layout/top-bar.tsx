"use client";

import { ThemeToggle } from "./theme-toggle";
import { CmdTrigger } from "./cmd-trigger";

/**
 * 主内容区上方的 slim top bar（56px 高，不占整页宽度，只占主内容区）。
 *
 * 内容：
 *  - 左侧：page-specific 内容（如 workspace 顶栏的项目名、actions）—— 由 children slot 接收
 *  - 右侧：固定的 ⌘K 命令面板触发 + 主题切换 + 用户头像
 *
 * 设计原则：
 *  - 不抢戏：低饱和、底色与主内容一致
 *  - sticky top-0，让滚动时仍可用 ⌘K
 */
export function TopBar({
  left,
  right,
}: {
  left?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border-subtle bg-bg-overlay/80 px-6 backdrop-blur">
      <div className="min-w-0 flex-1">{left}</div>
      <div className="flex items-center gap-2">
        {right}
        <CmdTrigger />
        <ThemeToggle />
        <UserChip />
      </div>
    </div>
  );
}

function UserChip() {
  return (
    <button
      type="button"
      aria-label="用户菜单"
      className="flex h-7 w-7 items-center justify-center rounded-full bg-bg-sunken text-[10px] font-semibold text-text-secondary hover:bg-bg-hover"
    >
      XF
    </button>
  );
}
