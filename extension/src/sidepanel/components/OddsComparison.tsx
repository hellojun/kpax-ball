import type { Lang } from "@shared/types";
import { t } from "@shared/i18n";

interface Props {
  polymarketOdds: Record<string, number>;
  kpaxOdds: Record<string, number>;
  marketDeviation: Record<string, number>;
  lang: Lang;
}

export default function OddsComparison({ polymarketOdds, kpaxOdds, marketDeviation, lang }: Props) {
  const outcomes = Object.keys(kpaxOdds);

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/50 p-3">
      <div className="mb-3 text-xs font-medium text-slate-400">{t(lang, "oddsComparison")}</div>
      <div className="mb-2 flex items-center justify-between text-[10px] text-slate-500">
        <span>{t(lang, "outcome")}</span>
        <div className="flex items-center gap-6">
          <span>{t(lang, "market")}</span>
          <span>KPAX</span>
          <span className="w-12 text-right">{t(lang, "gap")}</span>
        </div>
      </div>
      {outcomes.map((outcome) => {
        const market = polymarketOdds[outcome] ?? 0;
        const kpax = kpaxOdds[outcome] ?? 0;
        const deviation = marketDeviation[outcome] ?? 0;
        const label = outcome === "home" ? t(lang, "home") : outcome === "away" ? t(lang, "away") : t(lang, "draw");

        return (
          <div key={outcome} className="mb-2">
            <div className="flex items-center justify-between text-sm">
              <span className="w-16 text-slate-300">{label}</span>
              <div className="flex items-center gap-4">
                <span className="w-10 text-right text-slate-500">{(market * 100).toFixed(1)}%</span>
                <span className="w-10 text-right font-medium text-white">{(kpax * 100).toFixed(1)}%</span>
                <span className={`w-14 text-right text-xs font-medium ${deviation > 0.02 ? "text-green-400" : deviation < -0.02 ? "text-red-400" : "text-slate-500"}`}>
                  {deviation > 0 ? "+" : ""}{(deviation * 100).toFixed(1)}%
                </span>
              </div>
            </div>
            <div className="mt-1 flex gap-0.5">
              <div className="h-1.5 rounded-full bg-slate-600" style={{ width: `${market * 100}%` }} />
              <div className="h-1.5 rounded-full bg-blue-500" style={{ width: `${kpax * 100}%` }} />
            </div>
          </div>
        );
      })}
      <div className="mt-2 flex items-center gap-3 text-[10px] text-slate-600">
        <span className="flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-full bg-slate-600" />{t(lang, "market")}</span>
        <span className="flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-full bg-blue-500" />KPAX</span>
      </div>
    </div>
  );
}
