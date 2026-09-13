/**
 * 环形图 — 显示主/平/客概率分布
 * 纯 SVG，无第三方依赖
 */

import type { Lang } from "@shared/types";
import { t } from "@shared/i18n";

interface Segment {
  key: string;
  label: string;
  value: number;
  color: string;
}

interface Props {
  home: number;
  draw: number;
  away: number;
  homeLabel?: string;
  awayLabel?: string;
  lang: Lang;
  size?: number;
}

const COLORS = {
  home: "#3b82f6",  // blue-500
  draw: "#a855f7",  // purple-500
  away: "#f97316",  // orange-500
};

export default function DonutChart({
  home,
  draw,
  away,
  homeLabel,
  awayLabel,
  lang,
  size = 160,
}: Props) {
  const segments: Segment[] = [
    { key: "home", label: homeLabel || t(lang, "home"), value: home, color: COLORS.home },
    { key: "draw", label: t(lang, "draw"), value: draw, color: COLORS.draw },
    { key: "away", label: awayLabel || t(lang, "away"), value: away, color: COLORS.away },
  ].filter((s) => s.value > 0);

  const total = segments.reduce((sum, s) => sum + s.value, 0) || 1;
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 8;
  const innerRadius = radius * 0.6;

  // 构建 SVG 路径
  let startAngle = -Math.PI / 2; // 从 12 点钟开始
  const paths = segments.map((seg) => {
    const ratio = seg.value / total;
    const angle = ratio * Math.PI * 2;
    const endAngle = startAngle + angle;
    const largeArc = angle > Math.PI ? 1 : 0;

    const x1 = cx + radius * Math.cos(startAngle);
    const y1 = cy + radius * Math.sin(startAngle);
    const x2 = cx + radius * Math.cos(endAngle);
    const y2 = cy + radius * Math.sin(endAngle);
    const ix1 = cx + innerRadius * Math.cos(endAngle);
    const iy1 = cy + innerRadius * Math.sin(endAngle);
    const ix2 = cx + innerRadius * Math.cos(startAngle);
    const iy2 = cy + innerRadius * Math.sin(startAngle);

    const path = [
      `M ${x1} ${y1}`,
      `A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`,
      `L ${ix1} ${iy1}`,
      `A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${ix2} ${iy2}`,
      "Z",
    ].join(" ");

    const midAngle = startAngle + angle / 2;
    startAngle = endAngle;

    return { ...seg, path, midAngle, ratio };
  });

  // 找最大的一项，显示在中心
  const largest = segments.reduce((a, b) => (a.value > b.value ? a : b), segments[0]);

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {paths.map((p) => (
          <path key={p.key} d={p.path} fill={p.color} opacity={0.85} />
        ))}
        {/* 中心文字 */}
        <text
          x={cx}
          y={cy - 6}
          textAnchor="middle"
          className="fill-white text-lg font-bold"
          style={{ fontSize: 20 }}
        >
          {(largest.value * 100).toFixed(1)}%
        </text>
        <text
          x={cx}
          y={cy + 14}
          textAnchor="middle"
          className="fill-slate-400 text-xs"
          style={{ fontSize: 11 }}
        >
          {largest.label}
        </text>
      </svg>
      {/* 图例 */}
      <div className="flex items-center gap-3 text-[10px]">
        {segments.map((s) => (
          <span key={s.key} className="flex items-center gap-1 text-slate-400">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            {s.label} {(s.value * 100).toFixed(1)}%
          </span>
        ))}
      </div>
    </div>
  );
}
