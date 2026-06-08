"use client";

import { useCallback, useEffect, useState } from "react";
import { SunIcon, MoonIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "atlas:theme";

type Theme = "light" | "dark";

function readPreferredTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem(STORAGE_KEY) as Theme | null;
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function applyTheme(theme: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

/**
 * Theme toggle (light / dark)。
 *  - 首次访问：跟 system 偏好
 *  - localStorage 持久化
 *  - 快捷键 ⌘+Shift+L / Ctrl+Shift+L
 *  - 不闪烁：layout 在 <head> inline script 读 localStorage 提前同步（见 layout.tsx）
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  // 挂载后从 localStorage 同步真实主题（首屏 inline script 已防闪烁）。这是 SSR 安全的
  // 「挂载后初始化」——服务端无 localStorage，必须等挂载再读，故对该规则刻意豁免。
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const t = readPreferredTheme();
    setTheme(t);
    applyTheme(t);
    setMounted(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // toggle 定义在键盘 effect **之前**（修 react-hooks/refs「变量声明前访问」）；
  // useCallback 稳定身份 → 键盘 effect 依赖 [mounted, toggle] 即可，去掉 exhaustive-deps 豁免。
  const toggle = useCallback(() => {
    const next: Theme = theme === "light" ? "dark" : "light";
    setTheme(next);
    applyTheme(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, next);
    }
  }, [theme]);

  /* keyboard shortcut */
  useEffect(() => {
    if (!mounted) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "l" && e.shiftKey && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mounted, toggle]);

  return (
    <button
      type="button"
      onClick={toggle}
      title={`Switch to ${theme === "light" ? "dark" : "light"} (⌘⇧L)`}
      aria-label="toggle theme"
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-md border border-border-default bg-bg-raised text-text-secondary",
        "transition-colors duration-120 ease-out-quart",
        "hover:border-border-strong hover:text-text-primary",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
      )}
    >
      {mounted && theme === "dark" ? (
        <MoonIcon className="h-3.5 w-3.5" />
      ) : (
        <SunIcon className="h-3.5 w-3.5" />
      )}
    </button>
  );
}
