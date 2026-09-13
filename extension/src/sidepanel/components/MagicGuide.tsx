import { Info, Key } from "lucide-react";
import { t } from "@shared/i18n";
import type { Lang } from "@shared/types";

interface Props {
  lang: Lang;
  onClose: () => void;
}

/** Onboarding shown when KPAX detects the user has a Polymarket proxy of
 *  Magic-link type (email/Google/Apple login). They need to export their
 *  Magic-derived private key into MetaMask before they can borrow. */
export default function MagicGuide({ lang, onClose }: Props) {
  return (
    <div
      className="fixed inset-0 z-30 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start gap-3">
          <span className="shrink-0 flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500/15 border border-amber-500/30">
            <Key size={18} className="text-amber-400" strokeWidth={2.25} />
          </span>
          <h3 className="text-base font-semibold text-slate-100 mt-1">
            {t(lang, "lendingMagicTitle")}
          </h3>
        </div>

        <p className="mb-2 text-xs leading-relaxed text-slate-300">
          {t(lang, "lendingMagicLine1")}
        </p>
        <p className="mb-3 text-xs leading-relaxed text-slate-300">
          {t(lang, "lendingMagicLine2")}
        </p>

        <ol className="mb-3 space-y-2 text-xs text-slate-300">
          {[
            t(lang, "lendingMagicStep1"),
            t(lang, "lendingMagicStep2"),
            t(lang, "lendingMagicStep3"),
          ].map((step, i) => (
            <li key={i} className="flex gap-2">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-500/20 text-[10px] font-semibold text-blue-300">
                {i + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>

        <div className="mb-4 flex items-start gap-1.5 rounded-lg border border-slate-700 bg-slate-800/40 p-2 text-[11px] text-slate-400">
          <Info size={12} strokeWidth={2.25} className="shrink-0 mt-0.5" />
          <span>{t(lang, "lendingMagicSafetyNote")}</span>
        </div>

        <a
          href="https://polymarket.com/settings"
          target="_blank"
          rel="noopener noreferrer"
          className="block w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-2.5 text-center text-sm font-semibold text-white transition-all"
        >
          {t(lang, "lendingMagicCta")}
        </a>
      </div>
    </div>
  );
}
