"use client";

import { useState } from "react";
import { LogOutIcon } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useAuth } from "@/lib/auth-context";

function initials(nameOrEmail: string): string {
  const s = nameOrEmail.trim();
  if (!s) return "?";
  return s.slice(0, 2).toUpperCase();
}

/** 顶导航右侧用户菜单：头像 + 下拉（邮箱 + 登出）。未登录时不渲染。 */
export function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  if (!user) return null;

  const label = user.display_name || user.email;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={label}
        className="rounded-pill outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
      >
        <Avatar className="h-8 w-8 border border-border-default">
          <AvatarFallback className="bg-bg-sunken text-xs font-medium text-text-secondary">
            {initials(label)}
          </AvatarFallback>
        </Avatar>
      </button>

      {open ? (
        <>
          {/* 点外部关闭 */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div
            role="menu"
            className="absolute right-0 z-50 mt-2 w-56 rounded-lg border border-border-subtle bg-bg-overlay p-1.5 shadow-popover"
          >
            <div className="px-2.5 py-2">
              <div className="truncate text-sm font-medium text-text-primary">
                {user.display_name || "—"}
              </div>
              <div className="truncate text-xs text-text-muted">{user.email}</div>
            </div>
            <div className="my-1 h-px bg-border-subtle" />
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                logout();
              }}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-text-secondary transition-colors duration-120 ease-out-quart hover:bg-bg-hover hover:text-text-primary"
            >
              <LogOutIcon className="h-3.5 w-3.5" />
              <span>登出</span>
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
