"use client";

import { useEffect, useState } from "react";
import { LightbulbIcon, XIcon, ArrowRightIcon } from "lucide-react";
import { Kbd } from "./kbd";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "atlas:onboarding-dismissed";

const TIPS = [
  {
    title: "Press ⌘K to open the command palette",
    body: "跳 tab · 跳节点 · 搜索 evidence · 触发 workspace 动作",
    kbd: [<Kbd key="cmd">⌘</Kbd>, <Kbd key="k">K</Kbd>],
  },
  {
    title: "Hover any report paragraph to edit",
    body: "点 ✎ 进入 inline 编辑 · 保存后会计入 metrics.edit_rate",
    kbd: null,
  },
  {
    title: "Click any DAG node for the full trace",
    body: "rework / failed 节点会自动浮出 contextual chips（Override · Edit · Retry）",
    kbd: null,
  },
];

/**
 * Workspace 首次访问引导。底部居中 toast-like，三条提示自动轮播 + 手动翻页。
 * 用户点 "Got it" 后存 localStorage，永久不再显示。
 */
export function OnboardingHint() {
  const [visible, setVisible] = useState(false);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const dismissed = window.localStorage.getItem(STORAGE_KEY) === "true";
    if (!dismissed) {
      const t = window.setTimeout(() => setVisible(true), 800);
      return () => window.clearTimeout(t);
    }
  }, []);

  /* auto advance every 5s */
  useEffect(() => {
    if (!visible) return;
    const t = window.setInterval(() => {
      setIdx((i) => (i + 1) % TIPS.length);
    }, 5000);
    return () => window.clearInterval(t);
  }, [visible]);

  if (!visible) return null;

  const tip = TIPS[idx];

  const dismiss = () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, "true");
    }
    setVisible(false);
  };

  return (
    <div
      role="status"
      className={cn(
        "fixed bottom-6 left-1/2 z-40 -translate-x-1/2",
        "flex max-w-xl items-start gap-3 rounded-md border border-accent-border bg-bg-overlay px-4 py-3 shadow-popover"
      )}
    >
      <div className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-pill bg-accent-bg">
        <LightbulbIcon className="h-3.5 w-3.5 text-accent-base" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text-primary">
            {tip.title}
          </span>
          {tip.kbd ? (
            <span className="inline-flex items-center gap-0.5">{tip.kbd}</span>
          ) : null}
        </div>
        <p className="mt-0.5 text-[11px] text-text-secondary leading-relaxed">
          {tip.body}
        </p>
        <div className="mt-2 flex items-center gap-2">
          <div className="flex gap-1">
            {TIPS.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setIdx(i)}
                aria-label={`tip ${i + 1}`}
                className={cn(
                  "h-1.5 w-1.5 rounded-pill transition-colors duration-120 ease-out-quart",
                  i === idx ? "bg-accent-base" : "bg-border-default"
                )}
              />
            ))}
          </div>
          <span className="text-[10px] text-text-muted">
            tip {idx + 1} / {TIPS.length}
          </span>
          <button
            type="button"
            onClick={() => setIdx((i) => (i + 1) % TIPS.length)}
            className="ml-2 inline-flex items-center gap-0.5 text-[11px] text-text-muted hover:text-text-secondary"
          >
            <span>next</span>
            <ArrowRightIcon className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={dismiss}
            className="ml-auto text-[11px] font-medium text-accent-base hover:text-accent-hover"
          >
            Got it
          </button>
        </div>
      </div>
      <button
        type="button"
        onClick={dismiss}
        className="text-text-muted hover:text-text-secondary"
        aria-label="dismiss"
      >
        <XIcon className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
