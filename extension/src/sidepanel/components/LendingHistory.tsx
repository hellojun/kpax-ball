import { useEffect, useState } from "react";
import { BarChart3, FolderOpen } from "lucide-react";
import { t } from "@shared/i18n";
import type { Lang } from "@shared/types";
import { fetchLoans, type LoanItem } from "@shared/lending-api";

interface Props {
  lang: Lang;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; loans: LoanItem[] };

export default function LendingHistory({ lang }: Props) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchLoans();
        if (!cancelled) setState({ kind: "ready", loans: r.loans });
      } catch {
        if (!cancelled) setState({ kind: "error" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-center text-xs text-slate-500">
        {t(lang, "lendingHistoryLoading")}
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4 text-center text-xs text-red-400">
        {t(lang, "lendingHistoryLoadError")}
      </div>
    );
  }

  // Active / pending loans now live on the Assets page. This view is
  // history-only: repaid + liquidated + failed_repay + failed.
  const allLoans = state.loans;
  const historyLoans = allLoans.filter(
    (l) => l.status !== "active" && l.status !== "pending",
  );
  const activeCount = allLoans.filter(
    (l) => l.status === "active" || l.status === "pending",
  ).length;

  if (historyLoans.length === 0 && activeCount === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-800 bg-slate-900/40 p-6 text-center">
        <div className="mb-2 flex justify-center text-slate-500">
          <BarChart3 size={32} strokeWidth={1.5} />
        </div>
        <div className="text-sm text-slate-400">
          {t(lang, "profileActivityComingSoon")}
        </div>
      </div>
    );
  }

  // Lifetime stats — the question this page answers is "how much have I used
  // KPAX over time", so prefer cumulative figures over current counts.
  const repaidCount = historyLoans.filter((l) => l.status === "repaid").length;
  const liquidatedCount = historyLoans.filter((l) =>
    l.status.startsWith("liquidated"),
  ).length;
  const lifetimeBorrowed = historyLoans.reduce((acc, l) => acc + l.principal, 0);
  const lifetimeInterest = historyLoans.reduce(
    (acc, l) => acc + (l.total_interest_paid ?? 0),
    0,
  );

  return (
    <div className="space-y-3">
      {/* Lifetime triple-stat */}
      <div className="grid grid-cols-3 gap-2">
        <LifetimeStat
          value={String(historyLoans.length)}
          label={lang === "zh" ? "已结束" : "Closed"}
        />
        <LifetimeStat
          value={`$${lifetimeBorrowed.toFixed(2)}`}
          label={lang === "zh" ? "累计借出" : "Borrowed"}
        />
        <LifetimeStat
          value={`$${lifetimeInterest.toFixed(4)}`}
          label={lang === "zh" ? "累计利息" : "Interest paid"}
        />
      </div>

      {/* Pointer to the Assets page where active loans live now. */}
      {activeCount > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-blue-500/30 bg-blue-500/5 p-3 text-xs text-blue-200">
          <FolderOpen size={14} className="shrink-0 text-blue-300" strokeWidth={2.25} />
          <span>
            {lang === "zh"
              ? `当前 ${activeCount} 笔活跃借款在 "我的资产" 中管理`
              : `${activeCount} active loan${activeCount === 1 ? "" : "s"} live on the Assets page`}
          </span>
        </div>
      )}

      {/* Status filter chips */}
      <div className="flex flex-wrap gap-2 text-[11px] text-slate-400">
        <Stat
          label={`${lang === "zh" ? "已还" : "Repaid"} ${repaidCount}`}
          tone="green"
        />
        {liquidatedCount > 0 && (
          <Stat
            label={`${lang === "zh" ? "已强平" : "Liquidated"} ${liquidatedCount}`}
            tone="slate"
          />
        )}
      </div>

      <div className="space-y-2">
        {historyLoans.map((l) => (
          <LoanCard key={l.loan_id} loan={l} lang={lang} />
        ))}
      </div>
    </div>
  );
}

function LifetimeStat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-center">
      <div className="text-xl font-bold tabular-nums text-slate-50">{value}</div>
      <div className="text-[10px] text-slate-500 mt-0.5">{label}</div>
    </div>
  );
}

function Stat({
  label,
  tone,
}: {
  label: string;
  tone: "blue" | "green" | "slate";
}) {
  const cls =
    tone === "blue"
      ? "border-blue-500/30 bg-blue-500/10 text-blue-200"
      : tone === "green"
        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
        : "border-slate-700 bg-slate-800/50 text-slate-300";
  return (
    <span className={`rounded-full border px-2.5 py-0.5 text-[10px] ${cls}`}>
      {label}
    </span>
  );
}

function LoanCard({ loan, lang }: { loan: LoanItem; lang: Lang }) {
  const status = statusLabel(loan.status, lang);
  const tone = statusTone(loan.status);
  const isActive = loan.status === "active" || loan.status === "pending";
  const isLiquidated = loan.status.startsWith("liquidated");
  const hasLiqFlow =
    isLiquidated &&
    (loan.liq_proceeds_usd != null ||
      loan.liq_principal_repaid_usd != null ||
      loan.liq_to_treasury_usd != null ||
      loan.liq_residual_usd != null);

  return (
    <div className={`rounded-xl border ${tone.border} ${tone.bg} p-3`}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className={`text-[10px] font-semibold ${tone.text}`}>
          {status}
        </span>
        <span className="text-[10px] text-slate-500">#{loan.loan_id}</span>
      </div>

      <div className="mb-2 truncate text-xs font-medium text-slate-200">
        {loan.market_slug}
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <Field
          label={t(lang, "lendingHistoryPrincipal")}
          value={`$${loan.principal.toFixed(2)}`}
        />
        {isActive && loan.current_debt_usd != null ? (
          <Field
            label={t(lang, "lendingHistoryDebt")}
            value={`$${loan.current_debt_usd.toFixed(4)}`}
            highlight
          />
        ) : !hasLiqFlow && loan.total_interest_paid != null ? (
          <Field
            label={t(lang, "lendingHistoryInterest")}
            value={`$${loan.total_interest_paid.toFixed(4)}`}
          />
        ) : (
          <span />
        )}
        {isActive && loan.current_collateral_value_usd != null && (
          <Field
            label={t(lang, "lendingHistoryCollateral")}
            value={`$${loan.current_collateral_value_usd.toFixed(2)}`}
          />
        )}
        {isActive && loan.current_ltv != null && (
          <Field
            label="LTV"
            value={`${(loan.current_ltv * 100).toFixed(1)}%`}
          />
        )}
        <Field
          label={t(lang, "lendingHistoryOpenedAt")}
          value={fmtDate(loan.opened_at)}
        />
        {loan.closed_at && (
          <Field
            label={t(lang, "lendingHistoryClosedAt")}
            value={fmtDate(loan.closed_at)}
          />
        )}
      </div>

      {isActive && loan.health_status && (
        <HealthPill status={loan.health_status} lang={lang} />
      )}

      {hasLiqFlow && <LiqFlowBlock loan={loan} lang={lang} />}

      {(loan.open_tx_hash || loan.close_tx_hash) && (
        <div className="mt-2 flex gap-3 text-[10px]">
          {loan.open_tx_hash && (
            <a
              href={`https://polygonscan.com/tx/${loan.open_tx_hash}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-500 hover:text-slate-300"
            >
              ↗ open
            </a>
          )}
          {loan.close_tx_hash && (
            <a
              href={`https://polygonscan.com/tx/${loan.close_tx_hash}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-500 hover:text-slate-300"
            >
              ↗ close
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-500">{label}</span>
      <span
        className={highlight ? "font-semibold text-slate-100" : "text-slate-300"}
      >
        {value}
      </span>
    </div>
  );
}

function LiqFlowBlock({ loan, lang }: { loan: LoanItem; lang: Lang }) {
  const fmt = (v: number | null) => (v != null ? `$${v.toFixed(4)}` : "—");
  return (
    <div className="mt-2 rounded-md border border-red-500/20 bg-red-500/[0.04] p-2">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-red-300/80">
        {t(lang, "lendingHistoryLiqFlowTitle")}
      </div>
      <div className="flex justify-between text-[11px]">
        <span className="text-slate-400">{t(lang, "lendingHistoryLiqProceeds")}</span>
        <span className="font-semibold tabular-nums text-slate-100">
          {fmt(loan.liq_proceeds_usd)}
        </span>
      </div>
      <div className="ml-2 mt-0.5 space-y-0.5 border-l border-slate-700/60 pl-2 text-[11px]">
        <FlowRow
          label={t(lang, "lendingHistoryLiqPrincipalRepaid")}
          value={fmt(loan.liq_principal_repaid_usd)}
        />
        <FlowRow
          label={t(lang, "lendingHistoryLiqInterest")}
          value={fmt(loan.liq_to_treasury_usd)}
        />
        <FlowRow
          label={t(lang, "lendingHistoryLiqResidual")}
          value={fmt(loan.liq_residual_usd)}
        />
      </div>
    </div>
  );
}

function FlowRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-500">└ {label}</span>
      <span className="tabular-nums text-slate-300">{value}</span>
    </div>
  );
}

function HealthPill({
  status,
  lang,
}: {
  status: "healthy" | "caution" | "warn" | "liquidate";
  lang: Lang;
}) {
  const cfg = {
    healthy: { cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300", key: "lendingHealthHealthy" },
    caution: { cls: "border-yellow-500/30 bg-yellow-500/10 text-yellow-200", key: "lendingHealthCaution" },
    warn: { cls: "border-orange-500/40 bg-orange-500/10 text-orange-200", key: "lendingHealthWarn" },
    liquidate: { cls: "border-red-500/40 bg-red-500/10 text-red-200", key: "lendingHealthLiquidate" },
  } as const;
  const c = cfg[status];
  return (
    <div className={`mt-2 rounded-md border px-2 py-1 text-[10px] ${c.cls}`}>
      {t(lang, c.key)}
    </div>
  );
}

function statusLabel(status: string, lang: Lang): string {
  switch (status) {
    case "active":
      return t(lang, "lendingHistoryStatusActive");
    case "pending":
      return t(lang, "lendingHistoryStatusPending");
    case "repaid":
      return t(lang, "lendingHistoryStatusRepaid");
    case "liquidated_ltv":
      return t(lang, "lendingHistoryStatusLiquidatedLtv");
    case "liquidated_kickoff":
      return t(lang, "lendingHistoryStatusLiquidatedKickoff");
    case "failed":
    case "failed_repay":
      return t(lang, "lendingHistoryStatusFailed");
    default:
      return t(lang, "lendingHistoryStatusUnknown");
  }
}

function statusTone(status: string): {
  border: string;
  bg: string;
  text: string;
} {
  switch (status) {
    case "active":
      return {
        border: "border-blue-500/30",
        bg: "bg-blue-500/5",
        text: "text-blue-300",
      };
    case "pending":
      return {
        border: "border-amber-500/30",
        bg: "bg-amber-500/5",
        text: "text-amber-300",
      };
    case "repaid":
      return {
        border: "border-emerald-500/30",
        bg: "bg-emerald-500/5",
        text: "text-emerald-300",
      };
    case "liquidated_ltv":
    case "liquidated_kickoff":
      return {
        border: "border-red-500/30",
        bg: "bg-red-500/5",
        text: "text-red-300",
      };
    default:
      return {
        border: "border-slate-700",
        bg: "bg-slate-900/40",
        text: "text-slate-400",
      };
  }
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${m}-${day} ${h}:${min}`;
}
