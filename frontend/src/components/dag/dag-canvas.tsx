"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./dag-styles.css";

import {
  DEMO_DAG_NODES,
  DEMO_DAG_EDGES,
  summarizeDag,
  type DagNodeData,
  type DagNodeStatus,
  type DagNodeRecord,
  type DagEdgeRecord,
} from "@/lib/dag-mock";
import { DAG_PHASES, FINAL_PHASE_INDEX } from "@/lib/dag-phases";
import { DAG_NODE_TYPES } from "./dag-node";
import { DagToolbar } from "./dag-toolbar";
import { NodeDetailSheet } from "./node-detail-sheet";

interface DagCanvasProps {
  /** 真实 API 模式：直接传 nodes / edges（来自 apiStateToDagData） */
  nodes?: DagNodeRecord[];
  edges?: DagEdgeRecord[];
  /** 是否处于 live 模式（WS 推流中），决定工具条文案 */
  isLiveData?: boolean;
  /**
   * 节点选中回调。
   *  - 传入：workspace 接管选中状态（搭配右侧 DetailsRail），不再弹内嵌 Sheet
   *  - 不传：保持原行为（点节点弹 NodeDetailSheet 抽屉）
   */
  onSelectNode?: (id: string, data: DagNodeData | null) => void;
}

/**
 * Workspace · DAG tab canvas。
 *
 * 双模式：
 *  - **API 模式**（传 nodes/edges）：直接渲染后端数据，无 Play 回放
 *    （真实运行是单向流，Play 仅供 mock 演示使用）
 *  - **Mock 模式**（不传）：DEMO_DAG_NODES + Play / 时间滑块
 */
export function DagCanvas({
  nodes: nodesProp,
  edges: edgesProp,
  isLiveData = false,
  onSelectNode,
}: DagCanvasProps = {}) {
  const searchParams = useSearchParams();
  const requestedNode = searchParams.get("node");
  const railMode = !!onSelectNode;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  /* Mock-only state */
  const [phaseIdx, setPhaseIdx] = useState<number>(FINAL_PHASE_INDEX);
  const [isPlaying, setIsPlaying] = useState(false);
  const intervalRef = useRef<number | null>(null);

  const isApiMode = nodesProp !== undefined;

  useEffect(() => {
    if (requestedNode) {
      setSelectedId(requestedNode);
      setSheetOpen(true);
    }
  }, [requestedNode]);

  useEffect(() => {
    if (isApiMode || !isPlaying) {
      if (intervalRef.current) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }
    intervalRef.current = window.setInterval(() => {
      setPhaseIdx((cur) => {
        if (cur >= FINAL_PHASE_INDEX) {
          setIsPlaying(false);
          return FINAL_PHASE_INDEX;
        }
        return cur + 1;
      });
    }, 1500);
    return () => {
      if (intervalRef.current) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [isPlaying, isApiMode]);

  const rawSourceNodes = nodesProp ?? DEMO_DAG_NODES;
  const rawSourceEdges = edgesProp ?? DEMO_DAG_EDGES;

  // Control 节点（start / end / parallel_join / parallel_fork）对用户没有决策意义，
  // 隐藏掉只留 4 阶段的业务节点 —— 与 README「4 阶段流水线」叙事对齐。
  // API 模式下 adapter 已经过滤过；mock 模式（DEMO_DAG_NODES 含 start/end）在这里兜底。
  const { sourceNodes, sourceEdges } = useMemo(
    () => hideControlNodes(rawSourceNodes, rawSourceEdges),
    [rawSourceNodes, rawSourceEdges]
  );

  const phase = DAG_PHASES[phaseIdx];
  const isLive = isApiMode ? isLiveData : phaseIdx === FINAL_PHASE_INDEX;

  const nodes: Node[] = useMemo(() => {
    return sourceNodes.map((n) => {
      const status: DagNodeStatus = isApiMode
        ? n.data.status
        : (phase.status[n.id] ?? n.data.status);
      const isPending = status === "neutral";
      return {
        id: n.id,
        position: n.position,
        type: "dag",
        data: {
          ...n.data,
          status,
          durationMs: isPending ? null : n.data.durationMs,
          tokens: isPending ? null : n.data.tokens,
        },
        selected: n.id === selectedId,
      } as Node;
    });
  }, [sourceNodes, phase, selectedId, isApiMode]);

  const edges: Edge[] = useMemo(
    () =>
      sourceEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        animated: e.type === "feedback",
        className: e.type === "feedback" ? "feedback" : undefined,
      })),
    [sourceEdges]
  );

  const summary = useMemo(() => {
    if (isApiMode) {
      return summarizeDag(sourceNodes);
    }
    const phasedNodes = sourceNodes.map((n) => ({
      ...n,
      data: { ...n.data, status: phase.status[n.id] ?? n.data.status },
    }));
    return summarizeDag(phasedNodes);
  }, [sourceNodes, phase, isApiMode]);

  const selectedData: DagNodeData | null = useMemo(() => {
    if (!selectedId) return null;
    const base = sourceNodes.find((n) => n.id === selectedId)?.data ?? null;
    if (!base) return null;
    if (isApiMode) return base;
    return { ...base, status: phase.status[selectedId] ?? base.status };
  }, [selectedId, phase, sourceNodes, isApiMode]);

  const handleNodeClick: NodeMouseHandler = (_, node) => {
    setSelectedId(node.id);
    if (railMode) {
      // rail 模式：通知 workspace 接管，不弹 Sheet
      const base = sourceNodes.find((n) => n.id === node.id)?.data ?? null;
      const data = base && !isApiMode
        ? { ...base, status: phase.status[node.id] ?? base.status }
        : base;
      onSelectNode?.(node.id, data);
    } else {
      setSheetOpen(true);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border-subtle bg-bg-raised">
      <DagToolbar
        summary={summary}
        phaseIdx={isApiMode ? FINAL_PHASE_INDEX : phaseIdx}
        phaseCount={DAG_PHASES.length}
        phaseLabel={isApiMode ? "live" : phase.label}
        phaseDescription={
          isApiMode
            ? isLiveData
              ? "WebSocket connected · real-time pushes"
              : "API snapshot · polling fallback"
            : phase.description
        }
        isPlaying={isPlaying}
        isLive={isLive}
        replayDisabled={isApiMode}
        liveSource={isApiMode ? (isLiveData ? "ws" : "poll") : "mock"}
        onTogglePlay={() => {
          if (isApiMode) return;
          if (phaseIdx >= FINAL_PHASE_INDEX) {
            setPhaseIdx(0);
            setIsPlaying(true);
          } else {
            setIsPlaying((v) => !v);
          }
        }}
        onSeek={(idx) => {
          if (isApiMode) return;
          setIsPlaying(false);
          setPhaseIdx(Math.max(0, Math.min(FINAL_PHASE_INDEX, idx)));
        }}
        onJumpLive={() => {
          if (isApiMode) return;
          setIsPlaying(false);
          setPhaseIdx(FINAL_PHASE_INDEX);
        }}
      />
      <div className="min-h-0 flex-1" style={{ minHeight: 420 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={DAG_NODE_TYPES}
          onNodeClick={handleNodeClick}
          onPaneClick={() => setSelectedId(null)}
          fitView
          fitViewOptions={{ padding: 0.05, maxZoom: 1 }}
          minZoom={0.4}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={24}
            size={1.2}
            color="var(--border-default)"
          />
          <Controls showInteractive={false} position="bottom-right" />
          <MiniMap
            pannable
            zoomable
            position="top-right"
            nodeStrokeWidth={0}
            nodeColor={(n) => {
              const data = n.data as unknown as DagNodeData;
              switch (data.status) {
                case "success":
                  return "oklch(58% 0.16 150)";
                case "running":
                  return "oklch(60% 0.18 230)";
                case "rework":
                  return "oklch(64% 0.20 35)";
                case "error":
                  return "oklch(56% 0.21 25)";
                default:
                  return "oklch(56% 0.012 280)";
              }
            }}
            maskColor="oklch(96% 0.018 285 / 0.6)"
          />
        </ReactFlow>
      </div>

      {/* rail 模式下不弹 Sheet（详情在右侧 320px 常驻栏） */}
      {!railMode ? (
        <NodeDetailSheet
          open={sheetOpen}
          onOpenChange={(open) => {
            setSheetOpen(open);
            if (!open) setSelectedId(null);
          }}
          nodeId={selectedId}
          data={selectedData}
        />
      ) : null}
    </div>
  );
}

/* ── 隐藏 control 节点：把 start / end / join / fork 从视图剔掉，
   边重连透传（让 business 节点之间直连），保证用户视角只看到 4 阶段业务节点。 ── */
function hideControlNodes(
  nodes: DagNodeRecord[],
  edges: DagEdgeRecord[]
): { sourceNodes: DagNodeRecord[]; sourceEdges: DagEdgeRecord[] } {
  const controlIds = new Set(
    nodes.filter((n) => n.data.agent === "control").map((n) => n.id)
  );
  if (controlIds.size === 0) {
    return { sourceNodes: nodes, sourceEdges: edges };
  }

  const businessNodes = nodes.filter((n) => !controlIds.has(n.id));

  // edges 重连：对每条业务节点出边，若目标是 control，沿边 BFS 找下游可达的业务节点；
  // 若 source 是 control，跳过（由其他业务上游接管）。
  const adj = new Map<string, string[]>();
  for (const e of edges) {
    if (!adj.has(e.source)) adj.set(e.source, []);
    adj.get(e.source)!.push(e.target);
  }
  const reachable = (from: string): string[] => {
    const out: string[] = [];
    const seen = new Set<string>();
    const stack = [from];
    while (stack.length > 0) {
      const cur = stack.pop()!;
      if (seen.has(cur)) continue;
      seen.add(cur);
      if (!controlIds.has(cur)) {
        out.push(cur);
        continue;
      }
      for (const next of adj.get(cur) ?? []) stack.push(next);
    }
    return out;
  };

  const seenEdgeKey = new Set<string>();
  const businessEdges: DagEdgeRecord[] = [];
  for (const e of edges) {
    if (controlIds.has(e.source)) continue;
    const targets = controlIds.has(e.target) ? reachable(e.target) : [e.target];
    for (const t of targets) {
      const key = `${e.source}→${t}`;
      if (seenEdgeKey.has(key)) continue;
      seenEdgeKey.add(key);
      businessEdges.push({
        id: `${e.id}::${t}`,
        source: e.source,
        target: t,
        type: e.type,
      });
    }
  }

  return { sourceNodes: businessNodes, sourceEdges: businessEdges };
}
