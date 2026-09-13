import type { FullReport as FullReportType, Lang } from "@shared/types";
import { t } from "@shared/i18n";
import ConfidenceBadge from "./ConfidenceBadge";
import OddsComparison from "./OddsComparison";
import DonutChart from "./charts/DonutChart";
import OddsBarChart from "./charts/OddsBarChart";

interface Props {
  report: FullReportType;
  polymarketOdds: Record<string, number>;
  lang: Lang;
}

export default function FullReportView({ report, polymarketOdds, lang }: Props) {
  const { coreJudgment, sections, keyVariables, disagreements } = report;

  const kpaxOdds: Record<string, number> = {
    home: coreJudgment.homeWinPct,
    away: coreJudgment.awayWinPct,
    draw: coreJudgment.drawPct,
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-700 bg-slate-900 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">{t(lang, "kpaxAssessment")}</h3>
          <ConfidenceBadge confidence={coreJudgment.confidence} reason={coreJudgment.confidenceReason} lang={lang} />
        </div>
        <DonutChart
          home={coreJudgment.homeWinPct}
          draw={coreJudgment.drawPct}
          away={coreJudgment.awayWinPct}
          lang={lang}
          size={130}
        />
        <div className="mt-3">
          <OddsBarChart
            marketOdds={polymarketOdds}
            kpaxOdds={kpaxOdds}
            lang={lang}
          />
        </div>
      </div>

      {sections.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-xs font-medium text-slate-400">{t(lang, "detailedAnalysis")}</h3>
          {sections.map((section, i) => (
            <div key={i} className="rounded-lg border border-slate-700/50 bg-slate-900/50 p-3">
              <div className="mb-2 text-xs font-medium text-slate-300">{section.title}</div>
              <div className="whitespace-pre-wrap text-xs leading-relaxed text-slate-400">{section.content}</div>
            </div>
          ))}
        </div>
      )}

      {keyVariables.length > 0 && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
          <h3 className="mb-2 text-xs font-medium text-amber-400">{t(lang, "keyVariables")}</h3>
          <div className="space-y-2">
            {keyVariables.map((kv, i) => (
              <div key={i} className="text-xs">
                <div className="font-medium text-slate-300">{kv.description}</div>
                <div className="text-slate-500">{t(lang, "impact")}: {kv.impact}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {disagreements.length > 0 && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-900/50 p-3">
          <h3 className="mb-2 text-xs font-medium text-slate-400">{t(lang, "expertDisagreements")}</h3>
          <div className="space-y-2">
            {disagreements.map((d, i) => (
              <div key={i} className="text-xs">
                <div className="mb-0.5 text-slate-500">
                  {d.expertA} {t(lang, "vs")} {d.expertB} {t(lang, "on")} <span className="text-slate-300">{d.topic}</span>
                </div>
                <div className="text-slate-400">{d.summary}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {report.historicalAccuracy && (
        <div className="text-center text-[10px] text-slate-600">
          {t(lang, "historicalAccuracy")}: {report.historicalAccuracy}
        </div>
      )}

      <p className="text-center text-[10px] text-slate-600">{t(lang, "disclaimer")}</p>
    </div>
  );
}
