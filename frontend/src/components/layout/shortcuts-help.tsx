"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Kbd } from "./kbd";

/**
 * `?` shortcut overlay (Linear-style)。
 * 全局监听 Shift+/ → 弹出快捷键参考。
 */

const SHORTCUTS = [
  {
    group: "Global",
    items: [
      { label: "Open command palette", keys: ["⌘", "K"] },
      { label: "Show this help", keys: ["?"] },
      { label: "Toggle theme", keys: ["⌘", "⇧", "L"] },
    ],
  },
  {
    group: "Workspace",
    items: [
      { label: "Switch to DAG / Report / Trace / Evidence / Metrics", keys: ["⌘", "K", "→ tab"] },
      { label: "Jump to specific node", keys: ["⌘", "K", "→ Jump"] },
      { label: "Search evidence", keys: ["⌘", "K", "→ Evidence"] },
    ],
  },
  {
    group: "DAG canvas",
    items: [
      { label: "Click node → details Sheet", keys: ["click"] },
      { label: "Play replay", keys: ["▶ button"] },
      { label: "Drag slider to scrub time", keys: ["drag"] },
      { label: "Hover rework node → action chips", keys: ["hover"] },
    ],
  },
  {
    group: "Report",
    items: [
      { label: "Hover paragraph to edit", keys: ["hover"] },
      { label: "Click ✎ → inline edit mode", keys: ["click"] },
      { label: "Export markdown", keys: ["⬇ Export"] },
      { label: "Toggle v1 / v2 preview", keys: ["click toggle"] },
    ],
  },
];

export function ShortcutsHelp() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if (!inField && e.key === "?" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            按 <Kbd>?</Kbd> 再次开关此面板
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-5 py-2">
          {SHORTCUTS.map((g) => (
            <section key={g.group}>
              <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-text-muted">
                {g.group}
              </div>
              <ul className="space-y-1.5">
                {g.items.map((it, i) => (
                  <li
                    key={i}
                    className="flex items-center justify-between gap-3 rounded-sm px-2 py-1 hover:bg-bg-hover"
                  >
                    <span className="text-sm text-text-secondary">
                      {it.label}
                    </span>
                    <span className="flex items-center gap-0.5">
                      {it.keys.map((k, j) => (
                        <Kbd key={j}>{k}</Kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
