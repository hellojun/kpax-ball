/**
 * 水平对比条形图 — 市场赔率 vs KPAX 赔率
 * 纯 SVG，无第三方依赖
 */

import type { Lang } from "@shared/types";
import { t } from "@shared/i18n";

interface Props {
  marketOdds: Record<string, number>;
  kpaxOdds: Record<string, number>;
  lang: Lang;
  homeLabel?: string;
  awayLabel?: string;
}

const OUTCOMES = ["home", "draw", "away"] as const;

export default function OddsBarChart({
  marketOdds,
  kpaxOdds,
  lang,
  homeLabel,
  awayLabel,
}: Props) {
  const labels: Record<string, string> = {
    home: homeLabel || t(lang, "home"),
    draw: t(lang, "draw"),
    away: awayLabel || t(lang, "away"),
  };

  const rows = OUTCOMES.filter(
    (k) => (marketOdds[k] ?? 0) > 0 || (kpaxOdds[k] ?? 0) > 0
  );

  // 根据最长标签名动态调整标签区宽度
  const maxLabelLen = Math.max(...rows.map((k) => labels[k].length));
  const labelWidth = Math.min(Math.max(maxLabelLen * 7, 30), 90);
  const barWidth = 160;
  const rowHeight = 44;
  const svgWidth = barWidth + labelWidth + 80;
  const svgHeight = rows.length * rowHeight + 30;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${svgWidth} ${svgHeight}`}
      className="overflow-visible"
    >
      {/* 标题 */}
      <text x={labelWidth} y={12} className="fill-slate-500" style={{ fontSize: 9 }}>
        {t(lang, "market")}
      </text>
      <text
        x={labelWidth + barWidth + 10}
        y={12}
        className="fill-slate-500"
        style={{ fontSize: 9 }}
      >
        KPAX
      </text>

      {rows.map((key, i) => {
        const mVal = marketOdds[key] ?? 0;
        const kVal = kpaxOdds[key] ?? 0;
        const y = i * rowHeight + 24;
        const deviation = kVal - mVal;

        return (
          <g key={key}>
            {/* 标签 */}
            <text
              x={0}
              y={y + 14}
              className="fill-slate-300 font-medium"
              style={{ fontSize: 11 }}
              textLength={labels[key].length > 10 ? labelWidth - 4 : undefined}
              lengthAdjust="spacingAndGlyphs"
            >
              {labels[key].length > 12 ? labels[key].slice(0, 11) + "…" : labels[key]}
            </text>

            {/* 市场条 */}
            <rect
              x={labelWidth}
              y={y}
              width={Math.max(mVal * barWidth, 2)}
              height={10}
              rx={3}
              fill="#64748b"
              opacity={0.6}
            />

            {/* KPAX 条 */}
            <rect
              x={labelWidth}
              y={y + 14}
              width={Math.max(kVal * barWidth, 2)}
              height={10}
              rx={3}
              fill="#3b82f6"
              opacity={0.85}
            />

            {/* 数值 */}
            <text
              x={labelWidth + barWidth + 10}
              y={y + 8}
              className="fill-slate-500"
              style={{ fontSize: 10 }}
            >
              {(mVal * 100).toFixed(1)}%
            </text>
            <text
              x={labelWidth + barWidth + 10}
              y={y + 22}
              className="fill-blue-400 font-medium"
              style={{ fontSize: 10 }}
            >
              {(kVal * 100).toFixed(1)}%
            </text>

            {/* 偏差标注 */}
            {Math.abs(deviation) > 0.005 && (
              <text
                x={labelWidth + barWidth + 55}
                y={y + 15}
                style={{ fontSize: 9 }}
                className={
                  deviation > 0 ? "fill-green-400" : "fill-red-400"
                }
              >
                {deviation > 0 ? "+" : ""}
                {(deviation * 100).toFixed(1)}
              </text>
            )}
          </g>
        );
      })}

      {/* 图例 */}
      <g transform={`translate(${labelWidth}, ${svgHeight - 10})`}>
        <rect width={8} height={4} rx={2} fill="#64748b" opacity={0.6} />
        <text x={12} y={4} className="fill-slate-500" style={{ fontSize: 8 }}>
          {t(lang, "market")}
        </text>
        <rect x={50} width={8} height={4} rx={2} fill="#3b82f6" opacity={0.85} />
        <text x={62} y={4} className="fill-slate-500" style={{ fontSize: 8 }}>
          KPAX
        </text>
      </g>
    </svg>
  );
}
