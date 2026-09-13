import type { FootballMarket, QuickPreview, Lang } from "@shared/types";
import { t } from "@shared/i18n";
import DonutChart from "./charts/DonutChart";
import OddsBarChart from "./charts/OddsBarChart";

interface Props {
  market: FootballMarket;
  preview: QuickPreview;
  lang: Lang;
  onDeepAnalysis: () => void;
}

const CONFIDENCE_COLORS = {
  high: "bg-green-500/20 text-green-400 border-green-500/30",
  medium: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  low: "bg-red-500/20 text-red-400 border-red-500/30",
};

export default function QuickPreviewCard({
  market,
  preview,
  lang,
  onDeepAnalysis,
}: Props) {
  const confLabel = t(lang, preview.confidence === "high" ? "confidenceHigh" : preview.confidence === "medium" ? "confidenceMedium" : "confidenceLow");

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
      <div className="mb-3 text-xs text-slate-500">{market.competition}</div>
      <h2 className="mb-4 text-lg font-semibold text-white">
        {market.homeTeam} vs {market.awayTeam}
      </h2>

      <p className="mb-4 text-sm text-slate-300">{preview.summary}</p>

      <span
        className={`mb-4 inline-block rounded-full border px-3 py-1 text-xs font-medium ${CONFIDENCE_COLORS[preview.confidence]}`}
      >
        {t(lang, "confidence")}: {confLabel}
      </span>

      {/* KPAX 概率分布 */}
      <div className="mb-4 mt-4">
        <DonutChart
          home={preview.kpaxOdds.home ?? 0}
          draw={preview.kpaxOdds.draw ?? 0}
          away={preview.kpaxOdds.away ?? 0}
          homeLabel={shortName(market.homeTeam)}
          awayLabel={shortName(market.awayTeam)}
          lang={lang}
          size={130}
        />
      </div>

      {/* 赔率对比条形图 */}
      <div className="mb-4 rounded-lg border border-slate-700 bg-slate-800/50 p-3">
        <div className="mb-1 text-xs font-medium text-slate-400">
          {t(lang, "marketVsKpax")}
        </div>
        <OddsBarChart
          marketOdds={market.polymarketOdds as Record<string, number>}
          kpaxOdds={preview.kpaxOdds}
          lang={lang}
          homeLabel={shortName(market.homeTeam)}
          awayLabel={shortName(market.awayTeam)}
        />
      </div>

      <button
        onClick={onDeepAnalysis}
        className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-4 py-2.5 text-sm font-semibold text-white transition-all"
      >
        {t(lang, "viewFullAnalysis")}
      </button>

      <p className="mt-3 text-center text-[10px] text-slate-600">
        {t(lang, "disclaimer")}
      </p>
    </div>
  );
}

/** 球队简称：去掉 FC/AFC/SC 等通用后缀，保留有区分度的名字 */
function shortName(name: string): string {
  const cleaned = name.replace(/\s+(FC|AFC|SC|CF)$/i, "").trim();
  if (!cleaned) return name;
  // 如果还是太长（>10字符），取最后一个有意义的词
  if (cleaned.length > 10) {
    const words = cleaned.split(" ");
    return words[words.length - 1];
  }
  return cleaned;
}
