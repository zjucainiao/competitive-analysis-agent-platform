"use client";

import { useEffect, useRef, useState } from "react";
import { eventsWsUrl } from "./client";
import type { NodeExecutionResult } from "./types";

/**
 * WebSocket hook · 订阅 /api/projects/{id}/events，推 NodeExecutionResult。
 *
 * 用法：
 *   const { events, status } = useProjectEvents(projectId);
 *   // events 是有序累积；新的事件附加在末尾
 *
 * 失败时自动重连（指数退避，最多 5 次），onMessage 回调用于即时响应。
 */

export type WsStatus = "idle" | "connecting" | "open" | "closed" | "error";

export interface UseProjectEventsOptions {
  /** 收到事件时即时回调（用于 toast / SWR mutate） */
  onMessage?: (msg: NodeExecutionResult) => void;
  /** 是否启用。false 时不连。默认 true */
  enabled?: boolean;
}

export function useProjectEvents(
  projectId: string | null | undefined,
  opts: UseProjectEventsOptions = {}
): {
  events: NodeExecutionResult[];
  status: WsStatus;
  /** 主动重连 */
  reconnect: () => void;
} {
  const { onMessage, enabled = true } = opts;
  const [events, setEvents] = useState<NodeExecutionResult[]>([]);
  const [status, setStatus] = useState<WsStatus>("idle");
  const [retryKey, setRetryKey] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!projectId || !enabled || typeof window === "undefined") {
      return;
    }

    let retries = 0;
    const maxRetries = 5;
    let cancelled = false;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (cancelled) return;
      setStatus("connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(eventsWsUrl(projectId));
      } catch {
        setStatus("error");
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        setStatus("open");
        retries = 0;
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as NodeExecutionResult;
          setEvents((prev) => [...prev, data]);
          onMessageRef.current?.(data);
        } catch {
          /* ignore malformed frames */
        }
      };

      ws.onerror = () => {
        setStatus("error");
      };

      ws.onclose = (e) => {
        if (cancelled) return;
        setStatus("closed");
        /* 1000 = normal close，don't auto-reconnect */
        if (e.code !== 1000 && retries < maxRetries) {
          const delay = Math.min(8000, 500 * Math.pow(2, retries));
          retries += 1;
          reconnectTimer = window.setTimeout(connect, delay);
        }
      };
    };

    setEvents([]); // 重连或 projectId 变更时清空旧事件
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer != null) {
        window.clearTimeout(reconnectTimer);
      }
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close(1000, "unmount");
      }
      wsRef.current = null;
    };
  }, [projectId, enabled, retryKey]);

  return {
    events,
    status,
    reconnect: () => setRetryKey((v) => v + 1),
  };
}
