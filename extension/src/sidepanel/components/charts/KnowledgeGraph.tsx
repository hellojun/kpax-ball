/**
 * 知识图谱可视化 — 力导向布局的 SVG 图
 * 纯 SVG + 简单物理模拟，无第三方依赖
 */

import { useEffect, useRef, useState } from "react";
import type { Lang } from "@shared/types";
import { t } from "@shared/i18n";
import { API_BASE_URL } from "@shared/constants";

interface GraphNode {
  id: string;
  type: "team" | "analysis" | "competition" | "insight";
  label: string;
  fact?: string;
  // 布局用
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}

interface GraphEdge {
  source: string;
  target: string;
  label?: string;
}

interface Props {
  lang: Lang;
}

const NODE_COLORS: Record<string, string> = {
  team: "#3b82f6",        // blue
  analysis: "#a855f7",    // purple
  competition: "#f97316", // orange
  insight: "#10b981",     // emerald
};

const NODE_RADIUS: Record<string, number> = {
  team: 20,
  analysis: 14,
  competition: 22,
  insight: 12,
};

export default function KnowledgeGraph({ lang }: Props) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const WIDTH = 380;
  const HEIGHT = 320;

  useEffect(() => {
    fetchGraphData();
  }, []);

  async function fetchGraphData() {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/graph/data`);
      if (!resp.ok) throw new Error(`${resp.status}`);
      const data = await resp.json();

      if (data.nodes.length === 0) {
        setError(lang === "zh" ? "图谱暂无数据，分析更多比赛后图谱会逐渐丰富" : "No graph data yet. Analyze more matches to build the graph.");
        setLoading(false);
        return;
      }

      // 初始化节点位置（随机）
      const initNodes = data.nodes.map((n: GraphNode) => ({
        ...n,
        x: WIDTH / 2 + (Math.random() - 0.5) * WIDTH * 0.6,
        y: HEIGHT / 2 + (Math.random() - 0.5) * HEIGHT * 0.6,
        vx: 0,
        vy: 0,
      }));

      // 运行简单力导向布局
      runForceLayout(initNodes, data.edges);
      setNodes(initNodes);
      setEdges(data.edges);
    } catch (err) {
      setError(lang === "zh" ? "获取图谱数据失败" : "Failed to load graph data");
    }
    setLoading(false);
  }

  function runForceLayout(nodes: GraphNode[], edges: GraphEdge[]) {
    const iterations = 80;
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));

    for (let iter = 0; iter < iterations; iter++) {
      const alpha = 1 - iter / iterations;

      // 斥力（节点间）
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          const dx = (b.x ?? 0) - (a.x ?? 0);
          const dy = (b.y ?? 0) - (a.y ?? 0);
          const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
          const force = (800 * alpha) / (dist * dist);
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx = (a.vx ?? 0) - fx;
          a.vy = (a.vy ?? 0) - fy;
          b.vx = (b.vx ?? 0) + fx;
          b.vy = (b.vy ?? 0) + fy;
        }
      }

      // 引力（边）
      for (const edge of edges) {
        const a = nodeMap.get(edge.source);
        const b = nodeMap.get(edge.target);
        if (!a || !b) continue;
        const dx = (b.x ?? 0) - (a.x ?? 0);
        const dy = (b.y ?? 0) - (a.y ?? 0);
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const force = (dist - 60) * 0.05 * alpha;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx = (a.vx ?? 0) + fx;
        a.vy = (a.vy ?? 0) + fy;
        b.vx = (b.vx ?? 0) - fx;
        b.vy = (b.vy ?? 0) - fy;
      }

      // 居中引力
      for (const n of nodes) {
        n.vx = (n.vx ?? 0) + (WIDTH / 2 - (n.x ?? 0)) * 0.01 * alpha;
        n.vy = (n.vy ?? 0) + (HEIGHT / 2 - (n.y ?? 0)) * 0.01 * alpha;
      }

      // 应用速度
      for (const n of nodes) {
        n.x = Math.max(30, Math.min(WIDTH - 30, (n.x ?? 0) + (n.vx ?? 0)));
        n.y = Math.max(30, Math.min(HEIGHT - 30, (n.y ?? 0) + (n.vy ?? 0)));
        n.vx = (n.vx ?? 0) * 0.8;
        n.vy = (n.vy ?? 0) * 0.8;
      }
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4 text-center">
        <div className="mb-2 text-2xl">🕸️</div>
        <p className="text-xs text-slate-400">{error}</p>
      </div>
    );
  }

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-slate-400">
          {lang === "zh" ? "知识图谱" : "Knowledge Graph"}
        </span>
        <span className="text-[10px] text-slate-600">
          {nodes.length} {lang === "zh" ? "个节点" : "nodes"}
        </span>
      </div>

      <svg
        ref={svgRef}
        width="100%"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="rounded bg-slate-950/50"
      >
        {/* 边 */}
        {edges.map((e, i) => {
          const source = nodeMap.get(e.source);
          const target = nodeMap.get(e.target);
          if (!source || !target) return null;
          return (
            <line
              key={i}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
              stroke="#334155"
              strokeWidth={1}
              opacity={0.6}
            />
          );
        })}

        {/* 节点 */}
        {nodes.map((n) => {
          const r = NODE_RADIUS[n.type] || 14;
          const color = NODE_COLORS[n.type] || "#64748b";
          const isSelected = selectedNode?.id === n.id;

          return (
            <g
              key={n.id}
              onClick={() => setSelectedNode(isSelected ? null : n)}
              className="cursor-pointer"
            >
              <circle
                cx={n.x}
                cy={n.y}
                r={r}
                fill={color}
                opacity={isSelected ? 1 : 0.75}
                stroke={isSelected ? "#fff" : "none"}
                strokeWidth={isSelected ? 2 : 0}
              />
              <text
                x={n.x}
                y={(n.y ?? 0) + r + 12}
                textAnchor="middle"
                className="fill-slate-400"
                style={{ fontSize: 8 }}
              >
                {n.label.length > 12 ? n.label.slice(0, 12) + "…" : n.label}
              </text>
            </g>
          );
        })}
      </svg>

      {/* 选中节点详情 */}
      {selectedNode && (
        <div className="mt-2 rounded bg-slate-800/50 p-2 text-xs">
          <div className="font-medium text-slate-300">{selectedNode.label}</div>
          <div className="text-[10px] text-slate-500">
            {selectedNode.type === "team" && (lang === "zh" ? "球队" : "Team")}
            {selectedNode.type === "analysis" && (lang === "zh" ? "分析记录" : "Analysis")}
            {selectedNode.type === "competition" && (lang === "zh" ? "赛事" : "Competition")}
          </div>
          {selectedNode.fact && (
            <div className="mt-1 text-slate-400">{selectedNode.fact}</div>
          )}
        </div>
      )}

      {/* 图例 */}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[9px] text-slate-600">
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: NODE_COLORS.team }} />
          {lang === "zh" ? "球队" : "Team"}
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: NODE_COLORS.analysis }} />
          {lang === "zh" ? "分析" : "Analysis"}
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: NODE_COLORS.competition }} />
          {lang === "zh" ? "赛事" : "Competition"}
        </span>
      </div>
    </div>
  );
}
