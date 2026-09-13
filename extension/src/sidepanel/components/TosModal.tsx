import { useState } from "react";
import { t } from "@shared/i18n";
import type { Lang } from "@shared/types";
import { acceptTos } from "@shared/lending-api";

interface Props {
  tosVersion: string;
  lang: Lang;
  onAccepted: () => void;
  onCancel: () => void;
}

export default function TosModal({ tosVersion, lang, onAccepted, onCancel }: Props) {
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleContinue() {
    if (!agreed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await acceptTos(tosVersion);
      onAccepted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-30 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-3 text-base font-semibold text-slate-100">
          {t(lang, "lendingTosTitle")}
        </h3>

        <ul className="mb-4 space-y-2 text-xs leading-relaxed text-slate-300">
          <li>• {t(lang, "lendingTosLine1")}</li>
          <li>• {t(lang, "lendingTosLine2")}</li>
          <li>• {t(lang, "lendingTosLine3")}</li>
          <li>• {t(lang, "lendingTosLine4")}</li>
        </ul>

        <div className="mb-3 text-[11px] text-slate-500">
          {t(lang, "lendingTosReadFull")}: <code className="text-slate-400">{tosVersion}</code>
        </div>

        <label className="mb-4 flex items-start gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 shrink-0 accent-blue-500"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
          />
          <span className="text-xs text-slate-300">
            {t(lang, "lendingTosAgreeCheckbox")}
          </span>
        </label>

        {error && (
          <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-xs text-red-400">
            {error}
          </div>
        )}

        <div className="flex gap-2">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="flex-1 rounded-lg border border-slate-700 bg-slate-800/40 px-3 py-2 text-sm font-medium text-slate-300 hover:bg-slate-800/70 disabled:opacity-50 transition-colors"
          >
            {t(lang, "lendingTosCancel")}
          </button>
          <button
            onClick={handleContinue}
            disabled={!agreed || submitting}
            className="flex-1 rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 disabled:bg-none disabled:bg-slate-700 disabled:text-slate-500 disabled:shadow-none px-3 py-2 text-sm font-semibold text-white transition-all"
          >
            {t(lang, "lendingTosContinue")}
          </button>
        </div>
      </div>
    </div>
  );
}
