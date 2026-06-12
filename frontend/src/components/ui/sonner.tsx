"use client";

import { Toaster as Sonner, type ToasterProps } from "sonner";
import {
  CircleCheckIcon,
  InfoIcon,
  TriangleAlertIcon,
  OctagonXIcon,
  Loader2Icon,
} from "lucide-react";

/**
 * Toast 容器。v1 light-only，不接 next-themes。
 *
 * 视觉跟齐 DESIGN.md：淡紫底 + 紫罗兰 accent + 状态色四档。
 * 用法：from "sonner" import { toast } → toast.success / .error / .info / .warning / .loading
 */
const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="light"
      className="toaster group"
      position="bottom-right"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
        loading: <Loader2Icon className="size-4 animate-spin" />,
      }}
      style={
        {
          "--normal-bg": "var(--bg-raised)",
          "--normal-text": "var(--text-primary)",
          "--normal-border": "var(--border-default)",
          "--success-bg": "var(--success-bg)",
          "--success-text": "var(--success-base)",
          "--success-border": "var(--success-border)",
          "--info-bg": "var(--running-bg)",
          "--info-text": "var(--running-base)",
          "--info-border": "var(--running-border)",
          "--warning-bg": "var(--warning-bg)",
          "--warning-text": "var(--warning-base)",
          "--warning-border": "var(--warning-border)",
          "--error-bg": "var(--error-bg)",
          "--error-text": "var(--error-base)",
          "--error-border": "var(--error-border)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
};

export { Toaster };
