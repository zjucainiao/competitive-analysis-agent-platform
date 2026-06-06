"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

/** 不需要登录就能访问的路径前缀。 */
const PUBLIC_PREFIXES = ["/login"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`)
  );
}

/**
 * 全局路由守卫：未登录访问受保护页 → 跳 /login。
 * - 初次挂载校验 token 期间显示占位，避免闪现登录页。
 * - 登录页本身（PUBLIC_PREFIXES）始终放行。
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const pub = isPublic(pathname);

  useEffect(() => {
    if (!loading && !user && !pub) {
      router.replace("/login");
    }
  }, [loading, user, pub, router]);

  if (pub) return <>{children}</>;
  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-text-muted">
        加载中…
      </div>
    );
  }
  return <>{children}</>;
}
