import type { Lang } from "@shared/types";
import { t } from "@shared/i18n";

interface Props {
  confidence: "high" | "medium" | "low";
  reason?: string;
  lang: Lang;
}

const COLORS = {
  high: "bg-green-500/20 text-green-400 border-green-500/30",
  medium: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  low: "bg-red-500/20 text-red-400 border-red-500/30",
};

export default function ConfidenceBadge({ confidence, reason, lang }: Props) {
  const label = confidence === "high" ? t(lang, "confidenceHigh") : confidence === "medium" ? t(lang, "confidenceMedium") : t(lang, "confidenceLow");

  return (
    <div className="inline-flex flex-col gap-1">
      <span className={`inline-block rounded-full border px-3 py-1 text-xs font-medium ${COLORS[confidence]}`}>
        {t(lang, "confidence")}: {label}
      </span>
      {reason && <span className="text-[10px] text-slate-500">{reason}</span>}
    </div>
  );
}
