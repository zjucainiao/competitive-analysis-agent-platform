"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api/client";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const { user, loading, login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  /* 已登录则别停在登录页 */
  useEffect(() => {
    if (!loading && user) router.replace("/projects");
  }, [loading, user, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), password, displayName.trim());
      }
      router.replace("/projects");
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? typeof err.body === "object" &&
            err.body &&
            "detail" in err.body
            ? String((err.body as { detail: unknown }).detail)
            : err.message
          : err instanceof Error
            ? err.message
            : "请求失败";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg-base px-4">
      <div className="w-full max-w-sm rounded-xl border border-border-subtle bg-bg-raised p-8 shadow-popover">
        <h1 className="text-xl font-semibold text-text-primary">
          竞品分析 Agent 平台
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          {mode === "login" ? "登录以继续" : "创建账号"}
        </p>

        <form onSubmit={handleSubmit} className="mt-6 space-y-3">
          {mode === "register" ? (
            <div>
              <label className="mb-1 block text-xs text-text-muted">昵称（可选）</label>
              <Input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="你的名字"
                autoComplete="nickname"
              />
            </div>
          ) : null}

          <div>
            <label className="mb-1 block text-xs text-text-muted">邮箱</label>
            <Input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-text-muted">密码</label>
            <Input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "register" ? "至少 8 位" : "••••••••"}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </div>

          {error ? (
            <p className="rounded-md border border-error-border bg-error-bg px-3 py-2 text-xs text-error-base">
              {error}
            </p>
          ) : null}

          <Button type="submit" disabled={submitting} className="w-full">
            {submitting
              ? "处理中…"
              : mode === "login"
                ? "登录"
                : "注册并登录"}
          </Button>
        </form>

        <button
          type="button"
          onClick={() => {
            setMode((m) => (m === "login" ? "register" : "login"));
            setError(null);
          }}
          className="mt-4 w-full text-center text-xs text-text-muted hover:text-text-secondary"
        >
          {mode === "login" ? "没有账号？去注册" : "已有账号？去登录"}
        </button>
      </div>
    </div>
  );
}
