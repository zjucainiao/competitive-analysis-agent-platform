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
}: DagCanvasProps = {}) {
  const searchParams = useSearchParams();
  const requestedNode = searchParams.get("node");

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

  const sourceNodes = nodesProp ?? DEMO_DAG_NODES;
  const sourceEdges = edgesProp ?? DEMO_DAG_EDGES;
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

  const handleNodeClick: NodeMouseHandler = (_, node) => {
    setSelectedId(node.id);
    setSheetOpen(true);
  };

  const selectedData: DagNodeData | null = useMemo(() => {
    if (!selectedId) return null;
    const base = sourceNodes.find((n) => n.id === selectedId)?.data ?? null;
    if (!base) return null;
    if (isApiMode) return base;
    return { ...base, status: phase.status[selectedId] ?? base.status };
  }, [selectedId, phase, sourceNodes, isApiMode]);

  return (
    <div className="overflow-hidden rounded-lg border border-border-subtle bg-bg-raised">
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
      <div className="h-[720px]">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={DAG_NODE_TYPES}
          onNodeClick={handleNodeClick}
          onPaneClick={() => setSelectedId(null)}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
          minZoom={0.4}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={20}
            size={1}
            color="var(--border-subtle)"
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
                  return "oklch(62% 0.16 220)";
                case "rework":
                  return "oklch(64% 0.18 50)";
                case "error":
                  return "oklch(56% 0.21 25)";
                default:
                  return "oklch(54% 0.01 70)";
              }
            }}
            maskColor="oklch(96% 0.006 80 / 0.6)"
          />
        </ReactFlow>
      </div>

      <NodeDetailSheet
        open={sheetOpen}
        onOpenChange={(open) => {
          setSheetOpen(open);
          if (!open) setSelectedId(null);
        }}
        nodeId={selectedId}
        data={selectedData}
      />
    </div>
  );
}
