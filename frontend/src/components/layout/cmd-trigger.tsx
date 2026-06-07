"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import {
  SearchIcon,
  LayoutDashboardIcon,
  FileTextIcon,
  ClockIcon,
  LibraryIcon,
  GaugeIcon,
  SparklesIcon,
  PauseIcon,
  CheckCheckIcon,
  DownloadIcon,
  ShareIcon,
  PaletteIcon,
  WorkflowIcon,
  FolderIcon,
} from "lucide-react";
import { toast } from "sonner";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { Kbd } from "./kbd";
import { DEMO_DAG_NODES } from "@/lib/dag-mock";
import { listEvidences } from "@/lib/evidence-index";
import { emitIntervention } from "@/lib/workspace-actions";
import {
  ApiError,
  overrideQA,
  pauseRun,
  stopRun,
} from "@/lib/api/client";
import { revalidate, useProjectState } from "@/lib/api/hooks";
import { apiStateToDagData, aggregateEvidences } from "@/lib/api/adapters";
import { apiEvidenceToMock } from "@/lib/evidence-context";

/**
 * 全局 ⌘K 命令面板。
 *
 * 命令源（grouped）：
 *  - Navigate         · 5 tab + Projects / Metrics / Design system
 *  - Workspace actions · Pause / Override / Stop / Export / Share / New（仅 workspace）
 *  - Jump to node / Search evidence（仅 workspace）
 *
 * 键盘优先的全局入口。主题切换在顶栏的 ThemeToggle。
 */
export function CmdTrigger() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  /* ⌘K 全局 hotkey */
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const inWorkspace = useMemo(
    () => /^\/projects\/[^/]+\/runs\//.test(pathname),
    [pathname]
  );

  /** 从 /projects/{id}/runs/... 提 projectId；非 workspace / demo 时为 null。 */
  const workspaceProjectId = useMemo<string | null>(() => {
    const m = pathname.match(/^\/projects\/([^/]+)\/runs\//);
    if (!m) return null;
    if (m[1] === "demo") return null; // demo 不接 API
    return m[1];
  }, [pathname]);

  /* ⌘K「跳节点 / 搜证据」数据源：真实工作台读当前项目真实 state（与
   * client-workspace 共享 SWR 缓存，不额外打请求）；demo / 非 workspace
   * （workspaceProjectId 为 null → hook 不 fetch）回退预置示例，避免真实项目里漏 demo 数据。 */
  const { data: wsState } = useProjectState(workspaceProjectId);
  const jumpNodes = useMemo(
    () =>
      workspaceProjectId && wsState
        ? apiStateToDagData(wsState).nodes
        : DEMO_DAG_NODES,
    [workspaceProjectId, wsState]
  );
  const searchEvidences = useMemo(
    () =>
      workspaceProjectId && wsState
        ? aggregateEvidences(wsState.outputs).map(apiEvidenceToMock)
        : listEvidences(),
    [workspaceProjectId, wsState]
  );

  /** Workspace action 真实调用 + 失败 toast + 拉新 state */
  const runApi = (label: string, op: (pid: string) => Promise<unknown>) => async () => {
    if (!workspaceProjectId) {
      toast.info(`${label} · demo 模式不发请求`);
      return;
    }
    try {
      await op(workspaceProjectId);
      await Promise.all([
        revalidate.projectState(workspaceProjectId),
        revalidate.project(workspaceProjectId),
      ]);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status} · ${e.message}` : String(e);
      toast.error(`${label} 失败`, { description: msg });
    }
  };

  const close = () => setOpen(false);

  const runCmd = (fn: () => void) => () => {
    close();
    /* defer to next tick so dialog can unmount before toast / navigation */
    requestAnimationFrame(fn);
  };

  /* Tab navigation within workspace keeps pathname, swaps ?tab= */
  const goToTab = (tab: string) => {
    if (inWorkspace) {
      const sp = new URLSearchParams(searchParams.toString());
      sp.set("tab", tab);
      router.push(`${pathname}?${sp.toString()}`);
    } else {
      router.push(`/projects/demo/runs/01?tab=${tab}`);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex h-8 items-center gap-2 rounded-md border border-border-default bg-bg-raised pl-2.5 pr-1.5 text-text-muted transition-colors duration-120 ease-out-quart hover:border-border-strong hover:text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
        aria-label="打开命令面板"
      >
        <SearchIcon className="h-3.5 w-3.5" />
        <span className="text-xs">搜索 / 命令</span>
        <span className="flex items-center gap-0.5">
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>

      <CommandDialog
        open={open}
        onOpenChange={setOpen}
        title="Command Palette"
        description="跳转 · 操作 · 跳节点"
      >
        <CommandInput placeholder="搜索命令、节点、产品名…" autoFocus />
        <CommandList>
          <CommandEmpty>没有匹配的命令</CommandEmpty>

          <CommandGroup heading="Navigate">
            <CommandItem
              keywords={["dag", "graph", "flow"]}
              onSelect={runCmd(() => goToTab("dag"))}
            >
              <WorkflowIcon />
              <span>DAG · 任务流转</span>
              <CommandShortcut>tab</CommandShortcut>
            </CommandItem>
            <CommandItem
              keywords={["report"]}
              onSelect={runCmd(() => goToTab("report"))}
            >
              <FileTextIcon />
              <span>Report · 报告查看</span>
              <CommandShortcut>tab</CommandShortcut>
            </CommandItem>
            <CommandItem
              keywords={["trace", "time travel", "replay"]}
              onSelect={runCmd(() => goToTab("trace"))}
            >
              <ClockIcon />
              <span>Trace · 决策回放</span>
              <CommandShortcut>tab</CommandShortcut>
            </CommandItem>
            <CommandItem
              keywords={["evidence", "source"]}
              onSelect={runCmd(() => goToTab("evidence"))}
            >
              <LibraryIcon />
              <span>Evidence · 证据库</span>
              <CommandShortcut>tab</CommandShortcut>
            </CommandItem>
            <CommandItem
              keywords={["metrics"]}
              onSelect={runCmd(() => goToTab("metrics"))}
            >
              <GaugeIcon />
              <span>Metrics · 项目指标</span>
              <CommandShortcut>tab</CommandShortcut>
            </CommandItem>
            <CommandSeparator />
            <CommandItem
              keywords={["projects", "home", "list"]}
              onSelect={runCmd(() => router.push("/projects"))}
            >
              <FolderIcon />
              <span>Projects · 项目列表</span>
              <CommandShortcut>route</CommandShortcut>
            </CommandItem>
            <CommandItem
              keywords={["dashboard", "global metrics"]}
              onSelect={runCmd(() => router.push("/metrics"))}
            >
              <LayoutDashboardIcon />
              <span>Global Metrics</span>
              <CommandShortcut>route</CommandShortcut>
            </CommandItem>
            <CommandItem
              keywords={["design", "system", "tokens"]}
              onSelect={runCmd(() => router.push("/design-system"))}
            >
              <PaletteIcon />
              <span>Design system · token 参考页</span>
              <CommandShortcut>route</CommandShortcut>
            </CommandItem>
          </CommandGroup>

          {inWorkspace ? (
            <>
              <CommandGroup heading="Workspace actions">
                <CommandItem
                  keywords={["pause", "stop run"]}
                  onSelect={runCmd(async () => {
                    await runApi("Pause", pauseRun)();
                    if (workspaceProjectId) {
                      toast.info("Run 已暂停");
                    }
                    emitIntervention("pause", "run");
                  })}
                >
                  <PauseIcon />
                  <span>Pause this run</span>
                </CommandItem>
                <CommandItem
                  keywords={["override", "accept v1", "qa override"]}
                  onSelect={runCmd(async () => {
                    if (workspaceProjectId) {
                      try {
                        const res = await overrideQA(workspaceProjectId);
                        toast.success("已 override · 接受为终稿", {
                          description: `final = ${res.accepted_report_node_id} · ${res.skipped_node_ids.length} 节点 skip · edit_rate ${(res.edit_rate * 100).toFixed(0)}%`,
                        });
                        await Promise.all([
                          revalidate.projectState(workspaceProjectId),
                          revalidate.project(workspaceProjectId),
                        ]);
                      } catch (e) {
                        const msg = e instanceof ApiError ? `${e.status} · ${e.message}` : String(e);
                        toast.error("Override 失败", { description: msg });
                        return;
                      }
                    } else {
                      toast.success("已 override · v1 接受为终稿", {
                        description: "QA blocking=false · 计入人工修正率",
                      });
                    }
                    emitIntervention("override", "qa");
                  })}
                >
                  <CheckCheckIcon />
                  <span>Override · accept v1</span>
                  <CommandShortcut>QA</CommandShortcut>
                </CommandItem>
                <CommandItem
                  keywords={["stop", "hard stop", "kill run"]}
                  onSelect={runCmd(async () => {
                    await runApi("Stop", stopRun)();
                    if (workspaceProjectId) {
                      toast.warning("Run 已强停 · 全部 pending 节点 skip");
                    }
                    emitIntervention("stop", "run");
                  })}
                >
                  <PauseIcon />
                  <span>Stop run</span>
                  <CommandShortcut>destructive</CommandShortcut>
                </CommandItem>
                <CommandItem
                  keywords={["export", "download", "pdf"]}
                  onSelect={runCmd(() => {
                    // 与顶栏 Export 一致：通过自定义事件让 workspace 打开导出菜单
                    if (typeof window !== "undefined") {
                      window.dispatchEvent(new CustomEvent("atlas:open-export"));
                    }
                  })}
                >
                  <DownloadIcon />
                  <span>Export report</span>
                </CommandItem>
                <CommandItem
                  keywords={["share", "link"]}
                  onSelect={runCmd(() => {
                    if (typeof navigator !== "undefined" && navigator.clipboard) {
                      navigator.clipboard.writeText(window.location.href);
                    }
                    toast.success("链接已复制");
                  })}
                >
                  <ShareIcon />
                  <span>Copy share link</span>
                </CommandItem>
                <CommandItem
                  keywords={["new", "create", "analysis"]}
                  onSelect={runCmd(() => router.push("/projects/new"))}
                >
                  <SparklesIcon />
                  <span>New analysis</span>
                  <CommandShortcut>create</CommandShortcut>
                </CommandItem>
              </CommandGroup>

              {jumpNodes.length > 0 ? (
                <CommandGroup heading="Jump to node">
                  {jumpNodes.map((n) => (
                    <CommandItem
                      key={n.id}
                      keywords={[
                        n.data.label,
                        n.data.agent,
                        n.id,
                        n.data.status,
                      ]}
                      onSelect={runCmd(() => {
                        const sp = new URLSearchParams(searchParams.toString());
                        sp.set("tab", "dag");
                        sp.set("node", n.id);
                        router.push(`${pathname}?${sp.toString()}`);
                        toast.info(`已选中 ${n.data.label}`, {
                          description: `${n.data.agent} · ${n.data.status}`,
                        });
                      })}
                    >
                      <NodeStatusDot status={n.data.status} />
                      <span>{n.data.label}</span>
                      <span className="ml-auto font-mono text-[10px] text-text-muted">
                        {n.data.agent}
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              ) : null}

              {searchEvidences.length > 0 ? (
                <CommandGroup heading="Search evidence">
                  {searchEvidences
                    .slice(0, 8)
                    .map((ev) => (
                    <CommandItem
                      key={ev.id}
                      keywords={[
                        ev.id,
                        ev.product,
                        ev.sourceType,
                        ev.content.slice(0, 80),
                      ]}
                      onSelect={runCmd(() => {
                        const sp = new URLSearchParams(
                          searchParams.toString()
                        );
                        sp.set("tab", "evidence");
                        router.push(`${pathname}?${sp.toString()}`);
                        toast.info(`跳转到 Evidence · ${ev.id}`, {
                          description: `${ev.product} · ${ev.sourceType}`,
                        });
                      })}
                    >
                      <code className="font-mono text-[10px] text-text-muted shrink-0">
                        {ev.id.slice(0, 14)}
                      </code>
                      <span className="truncate text-text-secondary">
                        {ev.content.slice(0, 60)}
                        {ev.content.length > 60 ? "…" : ""}
                      </span>
                      <span className="ml-auto font-mono text-[10px] text-text-muted shrink-0">
                        {ev.product}
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              ) : null}
            </>
          ) : null}
        </CommandList>
      </CommandDialog>
    </>
  );
}

function NodeStatusDot({ status }: { status: string }) {
  const cls: Record<string, string> = {
    success: "bg-success-base",
    running: "bg-running-base",
    rework: "bg-rework-base",
    error: "bg-error-base",
    neutral: "bg-neutral-base",
    warning: "bg-warning-base",
  };
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-pill ${cls[status] ?? "bg-neutral-base"}`}
      aria-hidden
    />
  );
}
