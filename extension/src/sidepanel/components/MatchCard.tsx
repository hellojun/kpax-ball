import type { FootballMarket, Lang } from "@shared/types";
import { t } from "@shared/i18n";
import DonutChart from "./charts/DonutChart";

interface Props {
  market: FootballMarket;
  lang: Lang;
  onQuickAnalysis: () => void;
  onDeepAnalysis: () => void;
  isLoading: boolean;
}

export default function MatchCard({
  market,
  lang,
  onQuickAnalysis,
  onDeepAnalysis,
  isLoading,
}: Props) {
  const odds = market.polymarketOdds;
  const hasOdds = Object.keys(odds).length > 0;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
      {/* 赛事 */}
      <div className="mb-1 text-xs text-slate-500">
        {market.competition || t(lang, "competition")}
      </div>

      {/* 球队 */}
      <h2 className="mb-4 text-lg font-bold text-white">
        {market.homeTeam} vs {market.awayTeam}
      </h2>

      {/* 市场赔率 — 环形图 */}
      {hasOdds && (
        <div className="mb-4 rounded-lg bg-slate-800/60 p-3">
          <div className="mb-2 text-xs font-medium text-slate-400">
            {t(lang, "marketOdds")}
          </div>
          <DonutChart
            home={odds.home ?? 0}
            draw={odds.draw ?? 0}
            away={odds.away ?? 0}
            homeLabel={shortName(market.homeTeam)}
            awayLabel={shortName(market.awayTeam)}
            lang={lang}
            size={140}
          />
        </div>
      )}

      {/* 附加信息 */}
      <div className="mb-4 flex items-center gap-4 text-[10px] text-slate-600">
        {market.endDate && (
          <span>
            {t(lang, "endDate")}: {market.endDate.slice(0, 10)}
          </span>
        )}
        {market.volume > 0 && (
          <span>
            {t(lang, "volume")}: ${formatVolume(market.volume)}
          </span>
        )}
      </div>

      {/* 操作按钮 */}
      <div className="flex gap-2">
        <button
          onClick={onQuickAnalysis}
          disabled={isLoading}
          className="flex-1 rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-4 py-2.5 text-sm font-semibold text-white transition-all disabled:bg-none disabled:bg-slate-700 disabled:text-slate-500 disabled:shadow-none disabled:cursor-not-allowed"
        >
          {t(lang, "quickAnalysis")}
        </button>
        <button
          onClick={onDeepAnalysis}
          disabled={isLoading}
          className="flex-1 rounded-lg border border-cyan-500/40 px-4 py-2.5 text-sm font-semibold text-cyan-300 transition-colors hover:bg-cyan-500/10 hover:border-cyan-500/60 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t(lang, "deepAnalysis")}
        </button>
      </div>
    </div>
  );
}

function OddsPill({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-1 flex-col items-center rounded-md bg-slate-700/50 px-2 py-2">
      <span className="text-[10px] text-slate-400">{label}</span>
      <span className="text-sm font-bold text-white">
        {(value * 100).toFixed(1)}%
      </span>
    </div>
  );
}

/** 球队简称：去掉 FC/AFC 后缀，保留核心名 */
function shortName(name: string): string {
  return name.replace(/\s+(FC|AFC|SC|CF)$/i, "").trim() || name;
}

function formatVolume(v: number): string {
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "K";
  return v.toFixed(0);
}
