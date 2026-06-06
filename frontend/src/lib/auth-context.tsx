"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import {
  type AuthUser,
  fetchMe,
  getAuthToken,
  login as apiLogin,
  register as apiRegister,
  setAuthToken,
  setUnauthorizedHandler,
} from "@/lib/api/client";

interface AuthState {
  user: AuthUser | null;
  /** 初次挂载时校验已存 token 的过程；true 时别急着判未登录 */
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (
    email: string,
    password: string,
    displayName?: string
  ) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    setAuthToken(null);
    setUser(null);
    router.replace("/login");
  }, [router]);

  /* 401 钩子：任何请求 401 → 清态跳登录 */
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      router.replace("/login");
    });
    return () => setUnauthorizedHandler(null);
  }, [router]);

  /* 挂载时：有 token 就拉 /me 校验 */
  useEffect(() => {
    let alive = true;
    const token = getAuthToken();
    if (!token) {
      setLoading(false);
      return;
    }
    fetchMe()
      .then((u) => {
        if (alive) setUser(u);
      })
      .catch(() => {
        /* 401 钩子已处理清态 */
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password);
    setAuthToken(res.access_token);
    setUser(res.user);
  }, []);

  const register = useCallback(
    async (email: string, password: string, displayName = "") => {
      const res = await apiRegister(email, password, displayName);
      setAuthToken(res.access_token);
      setUser(res.user);
    },
    []
  );

  const value = useMemo<AuthState>(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
