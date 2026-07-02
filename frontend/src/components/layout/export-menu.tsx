"use client";

import { useEffect, useState } from "react";
import {
  FileTextIcon,
  FileTypeIcon,
  FileJsonIcon,
  FileIcon,
  DownloadIcon,
  AlertTriangleIcon,
} from "lucide-react";
import { toast } from "sonner";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useWorkspaceApi } from "@/lib/workspace-api-context";
import { fetchProjectExport, describeError, ApiError, API_BASE } from "@/lib/api/client";
import { downloadBlob } from "@/lib/report-export";
import type { ExportFormat } from "@/lib/api/types";

const FORMATS: Array<{
  fmt: ExportFormat;
  label: string;
  desc: string;
  icon: typeof FileTextIcon;
  client?: boolean;
}> = [
  {
    fmt: "markdown",
    label: "Markdown",
    desc: "纯文本报告，含 evidence 引用 + 数据来源声明",
    icon: FileTextIcon,
  },
  {
    fmt: "pdf",
    label: "PDF",
    desc: "A4 排版 · 适合发给老板 / 客户 · 缺 reportlab 依赖时 503",
    icon: FileIcon,
  },
  {
    fmt: "docx",
    label: "DOCX",
    desc: "Word 格式 · 缺 python-docx 依赖时 503",
    icon: FileTypeIcon,
  },
  {
    fmt: "json",
    label: "JSON (full state)",
    desc: "完整数据 dump，方便数据团队二次处理",
    icon: FileJsonIcon,
  },
];

/**
 * 全局 Export 菜单。
 *
 * 监听 `atlas:open-export` 事件（顶栏 Export 按钮 / ⌘K Export report 触发），
 * 弹一个 sheet 让用户选 Markdown / PDF / DOCX / JSON。
 *
 *  - 下载走 GET /api/projects/{id}/export?format=...，后端要求 Bearer JWT，
 *    所以用带 Authorization 的 fetch → blob → objectURL 触发下载
 *    （纯 anchor GET 带不上 header，真实部署会 401）
 *  - PDF / DOCX 后端缺依赖返 503 → toast 降级提示
 *  - 文件名跟随后端 Content-Disposition（{project_id}.{ext}）
 */
export function ExportMenu() {
  const api = useWorkspaceApi();
  const [open, setOpen] = useState(false);
  const [busyFmt, setBusyFmt] = useState<ExportFormat | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    function onOpen() {
      setOpen(true);
    }
    window.addEventListener("atlas:open-export", onOpen);
    return () => window.removeEventListener("atlas:open-export", onOpen);
  }, []);

  async function handleClick(fmt: ExportFormat) {
    if (!api) {
      toast.info("Demo 模式 · 此入口在真实 workspace 才能下载");
      return;
    }
    setBusyFmt(fmt);
    try {
      /* 带 Authorization 的 fetch → blob，成功才触发下载（文件名来自
       * Content-Disposition）；503 = PDF / DOCX 依赖缺失，toast 降级提示 */
      const { blob, filename } = await fetchProjectExport(api.projectId, fmt);
      downloadBlob(filename, blob);
      toast.success(`${fmt.toUpperCase()} 下载已开始`, {
        description: filename,
      });
      setOpen(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        toast.warning(`${fmt.toUpperCase()} 导出不可用`, {
          description: `后端缺导出依赖 · 在后端跑：pip install '.[export-pdf-docx]'`,
        });
        return;
      }
      toast.error(`${fmt.toUpperCase()} 导出失败`, {
        description: describeError(e),
      });
    } finally {
      setBusyFmt(null);
    }
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent
        side="right"
        className="!w-[480px] !max-w-[480px] gap-0 overflow-y-auto p-0"
      >
        <SheetHeader className="gap-2 border-b border-border-subtle p-5">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-text-muted">
            <DownloadIcon className="h-3 w-3" />
            <span>Export report</span>
          </div>
          <SheetTitle className="text-base font-semibold">
            导出最终报告
          </SheetTitle>
          <SheetDescription className="text-xs text-text-secondary">
            服务端会按当前最新 reporter draft +
            evidence 索引拼装，含数据来源声明（不含个人隐私 / 非公开内容）。
          </SheetDescription>
        </SheetHeader>

        <ul className="divide-y divide-border-subtle p-2">
          {FORMATS.map((f) => {
            const Icon = f.icon;
            const busy = busyFmt === f.fmt;
            return (
              <li key={f.fmt}>
                <button
                  type="button"
                  disabled={busy || !api}
                  onClick={() => handleClick(f.fmt)}
                  className="flex w-full items-start gap-3 rounded-md px-3 py-3 text-left transition-colors duration-120 hover:bg-bg-sunken disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-text-secondary" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-text-primary">
                        {f.label}
                      </span>
                      {busy ? (
                        <span className="text-[10px] uppercase tracking-wider text-text-muted">
                          downloading…
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-text-muted">
                      {f.desc}
                    </p>
                  </div>
                  <DownloadIcon className="mt-1 h-3 w-3 shrink-0 text-text-muted" />
                </button>
              </li>
            );
          })}
        </ul>

        {!api ? (
          <div className="mx-3 mb-3 flex items-start gap-2 rounded-md border border-warning-border bg-warning-bg/40 px-3 py-2 text-[11px] text-warning-base">
            <AlertTriangleIcon className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span className="text-text-secondary">
              Demo / mock 模式无 projectId，导出不可用。请在真实 workspace 打开。
            </span>
          </div>
        ) : (
          <div className="mx-3 mb-3 text-[10px] text-text-muted leading-relaxed">
            导出 URL：
            <code className="font-mono">
              {API_BASE}/api/projects/{api.projectId}/export?format=…
            </code>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
