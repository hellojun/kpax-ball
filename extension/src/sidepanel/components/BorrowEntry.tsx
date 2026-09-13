import { useEffect, useRef, useState } from "react";
import { CircleDollarSign, FolderOpen, Inbox, Key, RotateCw } from "lucide-react";
import { t } from "@shared/i18n";
import type { FootballMarket, Lang } from "@shared/types";
import {
  fetchLoans,
  fetchPositions,
  fetchTosStatus,
  type LendingPosition,
  type LoanItem,
  type PositionsResponse,
  type TosStatus,
} from "@shared/lending-api";
import MagicGuide from "./MagicGuide";
import TosModal from "./TosModal";

interface Props {
  market: FootballMarket;
  lang: Lang;
  expectedEoa: string;
  /** CTFs that App treats as "loan just opened" before fetchLoans confirms.
   *  Owned by App so the optimistic state survives BorrowEntry unmount. */
  optimisticBorrowedCtfs: Set<string>;
  /** Bumped by App after every borrow/repay event. We watch it as a useEffect
   *  dep to trigger reloadAll. */
  lendingReloadVersion: number;
  /** Called after each successful fetchLoans so App can drop optimistic
   *  entries that the real "active" data has caught up on. */
  onLoansLoaded: (activeLoans: LoanItem[]) => void;
  /** User clicked "Borrow →" — hand the captured snapshot back to App which
   *  owns the BorrowFlowDialog. */
  onStartBorrow: (session: { proxy: string; position: LendingPosition }) => void;
  /** User clicked "还款" on an active loan — hand the loan to App which owns
   *  the RepayModal. */
  onStartRepay: (loan: LoanItem) => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; error: string }
  | { kind: "ready"; data: PositionsResponse; matches: LendingPosition[] };

export default function BorrowEntry({
  market,
  lang,
  expectedEoa: _expectedEoa,
  optimisticBorrowedCtfs,
  lendingReloadVersion,
  onLoansLoaded,
  onStartBorrow,
  onStartRepay,
}: Props) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [tos, setTos] = useState<TosStatus | null>(null);
  const [showTos, setShowTos] = useState(false);
  const [showMagicGuide, setShowMagicGuide] = useState(false);
  const [activeLoans, setActiveLoans] = useState<LoanItem[]>([]);
  // Which CTF asset (token id) the user picked when the same event has
  // multiple positions (moneyline + spread + total + ...). Each PM market
  // is its own CTF token and can be independently borrowed against.
  // Null until first matches arrive; reset on market change.
  const [selectedAsset, setSelectedAsset] = useState<string | null>(null);
  // Outstanding retry timers, so we can cancel them on unmount.
  const retryTimers = useRef<number[]>([]);

  useEffect(() => {
    return () => {
      retryTimers.current.forEach((id) => window.clearTimeout(id));
      retryTimers.current = [];
    };
  }, []);

  const [syncing, setSyncing] = useState(false);

  /** Refetch every piece of on-chain-derived state: positions (collateral
   *  changes after borrow/repay) and loans (status flips). Each fetch is
   *  isolated so a slow / flaky Polymarket Data API call doesn't block the
   *  loans refresh — the active-loans list flipping from 1 → 0 right after a
   *  successful repay matters more than getting fresh CTF balances. The
   *  parent (App) handles wallet-balance refresh via bumpLendingReload(). */
  async function reloadAll() {
    setSyncing(true);
    const loansP = fetchLoans()
      .then((r) => {
        const active = r.loans.filter((l) => l.status === "active");
        setActiveLoans(active);
        // Hand the active-loans list up to App so it can drop optimistic
        // entries whose real "active" row has now landed.
        onLoansLoaded(active);
        console.log(
          "[KPAX] reloadAll loans:",
          r.loans.length,
          "total,",
          active.length,
          "active",
        );
      })
      .catch((e) => console.warn("[KPAX] reloadAll loans failed:", e));

    const positionsP = fetchPositions()
      .then((positionsResp) => {
        const matches = findMatchingPositions(positionsResp.positions, market);
        setState({ kind: "ready", data: positionsResp, matches });
      })
      .catch((e) => console.warn("[KPAX] reloadAll positions failed:", e));

    await Promise.allSettled([loansP, positionsP]);
    setSyncing(false);
  }

  /** After borrow/repay we can't trust a single reloadAll — Polymarket Data
   *  API + backend confirm-borrow can both lag. Schedule a few extra refreshes
   *  so the UI heals automatically rather than leaving the user stuck on a
   *  stale view. */
  function scheduleReloadRetries(
    delaysMs: number[] = [2000, 5000, 10000, 20000, 40000],
  ) {
    retryTimers.current.forEach((id) => window.clearTimeout(id));
    retryTimers.current = delaysMs.map((d) =>
      window.setTimeout(() => {
        void reloadAll();
      }, d),
    );
  }

  // Whenever App bumps the reload version (after any borrow/repay event),
  // refetch + start the staggered retry schedule. This replaces the inline
  // `void reloadAll(); scheduleReloadRetries()` calls that used to live next
  // to setBorrowSession / onClose etc.
  useEffect(() => {
    if (lendingReloadVersion === 0) return; // skip initial mount
    void reloadAll();
    scheduleReloadRetries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lendingReloadVersion]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [positionsResp, tosResp, loansResp] = await Promise.all([
          fetchPositions(),
          fetchTosStatus(),
          fetchLoans().catch(() => ({ loans: [] as LoanItem[] })),
        ]);
        if (cancelled) return;
        const matches = findMatchingPositions(positionsResp.positions, market);
        setState({ kind: "ready", data: positionsResp, matches });
        setTos(tosResp);
        const active = loansResp.loans.filter((l) => l.status === "active");
        setActiveLoans(active);
        onLoansLoaded(active);
      } catch (e) {
        if (cancelled) return;
        setState({
          kind: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market.slug, market.conditionId]);

  function handleClick() {
    if (state.kind !== "ready" || !state.data.proxy) return;
    const sel = pickSelected(state.matches, selectedAsset);
    if (!sel) return;
    if (tos && !tos.accepted) {
      setShowTos(true);
      return;
    }
    onStartBorrow({
      proxy: state.data.proxy,
      position: sel,
    });
  }

  // Reset the picker selection whenever the user navigates to a different
  // event (so we don't carry over a stale asset from the prior match).
  useEffect(() => {
    setSelectedAsset(null);
  }, [market.slug, market.conditionId]);

  // ---------- render ----------

  if (state.kind === "loading") {
    return (
      <ShellCard>
        <div className="text-xs text-slate-400">
          {t(lang, "lendingBorrowEntryLoading")}
        </div>
      </ShellCard>
    );
  }

  if (state.kind === "error") {
    return (
      <ShellCard>
        <div className="text-xs text-red-400">
          {t(lang, "lendingBorrowEntryError")}
        </div>
      </ShellCard>
    );
  }

  const activeLoansSection =
    activeLoans.length > 0 ? (
      <ActiveLoansCard
        lang={lang}
        loans={activeLoans}
        onRepay={(l) => onStartRepay(l)}
        syncing={syncing}
        onRefresh={() => void reloadAll()}
      />
    ) : null;

  // proxy-kind branching: each Polymarket account type gets its own UX.
  // RepayModal lives at the App level — the only thing we need here is to
  // call onStartRepay from ActiveLoansCard, which is already wired below.
  if (state.data.proxy_kind === "magic") {
    return (
      <>
        {activeLoansSection}
        <MagicProxyCard lang={lang} onOpen={() => setShowMagicGuide(true)} />
        {showMagicGuide && (
          <MagicGuide lang={lang} onClose={() => setShowMagicGuide(false)} />
        )}
      </>
    );
  }

  if (state.data.proxy_kind === "none") {
    return (
      <>
        {activeLoansSection}
        <NoProxyCard lang={lang} />
      </>
    );
  }

  // proxy_kind === "safe": continue normal positions flow
  const { matches } = state;
  const selected = pickSelected(matches, selectedAsset);

  const hasActiveOnThisCtf =
    !!selected &&
    (activeLoans.some((l) => l.ctf_token_id === selected.asset) ||
      optimisticBorrowedCtfs.has(selected.asset));

  const refreshButton = (
    <RefreshButton
      syncing={syncing}
      lang={lang}
      onClick={() => void reloadAll()}
    />
  );

  const picker =
    matches.length >= 2 ? (
      <PositionPicker
        positions={matches}
        selectedAsset={selected?.asset ?? null}
        onSelect={(asset) => setSelectedAsset(asset)}
        lang={lang}
      />
    ) : null;

  const statusCard = !selected ? (
    <ShellCard>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-slate-500">
          {t(lang, "lendingBorrowEntryNoPosition")}
        </div>
        {refreshButton}
      </div>
    </ShellCard>
  ) : !selected.borrow_eligible ? (
    <ShellCard>
      {picker}
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-slate-500">
          {t(
            lang,
            selected.ineligible_reason === "kickoff_too_close"
              ? "lendingBorrowEntryKickoffClose"
              : "lendingBorrowEntryUnsupported",
          )}
        </div>
        {refreshButton}
      </div>
    </ShellCard>
  ) : hasActiveOnThisCtf ? (
    <ShellCard>
      {picker}
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-amber-300/80">
          {t(lang, "lendingBorrowEntryHasActive")}
        </div>
        {refreshButton}
      </div>
    </ShellCard>
  ) : (
    <ShellCard>
      <div className="mb-1 flex items-center gap-2">
        <CircleDollarSign size={16} className="text-emerald-400" strokeWidth={2.25} />
        <span className="text-sm font-semibold text-slate-100">
          {t(lang, "lendingBorrowEntryTitle")}
        </span>
        <TierBadge tier={selected.league_tier} lang={lang} />
        <span className="ml-auto">{refreshButton}</span>
      </div>
      {picker}
      <div className="mb-2 text-xs text-slate-400">
        {t(lang, "lendingBorrowEntryHolding")
          .replace("{shares}", formatShares(selected.size))
          .replace("{value}", formatUsd(selected.value_usd))}
      </div>
      <div className="mb-3 text-xs text-slate-400">
        {t(lang, "lendingBorrowEntryMax").replace(
          "{max}",
          formatUsd(selected.max_borrowable_usd),
        )}
      </div>
      <button
        onClick={handleClick}
        className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-2 text-sm font-semibold text-white transition-all"
      >
        {t(lang, "lendingBorrowEntryCta")}
      </button>
    </ShellCard>
  );

  return (
    <>
      {activeLoansSection}
      {statusCard}
      {showTos && tos && (
        <TosModal
          tosVersion={tos.current_version}
          lang={lang}
          onAccepted={() => {
            setShowTos(false);
            setTos({ ...tos, accepted: true, user_version: tos.current_version });
            // TOS-accept opens the borrow flow. Snapshot {proxy, position}
            // RIGHT NOW from `state` + selectedAsset and hand it up to App.
            if (state.kind === "ready" && state.data.proxy) {
              const sel = pickSelected(state.matches, selectedAsset);
              if (sel) {
                onStartBorrow({
                  proxy: state.data.proxy,
                  position: sel,
                });
              }
            }
          }}
          onCancel={() => setShowTos(false)}
        />
      )}
    </>
  );
}

/** Resolve the user's picked position from the available matches. Falls back
 *  to the first match (which is the highest-priority — condition_id > slug
 *  exact > slug stem) when nothing is selected or the selection is stale. */
function pickSelected(
  matches: LendingPosition[],
  selectedAsset: string | null,
): LendingPosition | null {
  if (matches.length === 0) return null;
  if (selectedAsset) {
    const found = matches.find((m) => m.asset === selectedAsset);
    if (found) return found;
  }
  return matches[0];
}

function ActiveLoansCard({
  lang,
  loans,
  onRepay,
  syncing,
  onRefresh,
}: {
  lang: Lang;
  loans: LoanItem[];
  onRepay: (l: LoanItem) => void;
  syncing: boolean;
  onRefresh: () => void;
}) {
  return (
    <div className="mt-3 rounded-xl border border-cyan-500/25 bg-cyan-500/5 p-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-cyan-200">
        <FolderOpen size={16} className="shrink-0 text-cyan-300" strokeWidth={2.25} />
        <span className="flex-1 truncate">
          {lang === "zh"
            ? `我的活跃借款 (${loans.length})`
            : `Active loans (${loans.length})`}
        </span>
        {syncing && (
          <span
            className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-cyan-300 border-t-transparent"
            title={lang === "zh" ? "刷新中..." : "Refreshing..."}
          />
        )}
        <button
          onClick={onRefresh}
          disabled={syncing}
          className="flex items-center gap-1 rounded border border-cyan-500/30 px-1.5 py-0.5 text-[10px] font-normal text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          title={lang === "zh" ? "手动刷新" : "Refresh"}
        >
          <RotateCw size={10} strokeWidth={2.25} />
          {lang === "zh" ? "刷新" : "Refresh"}
        </button>
      </div>
      <div className="space-y-2">
        {loans.map((l) => (
          <div
            key={l.loan_id}
            className="rounded-lg border border-cyan-500/20 bg-slate-900/40 p-2.5"
          >
            <div className="mb-0.5 truncate text-xs text-slate-300">
              {l.market_slug}
            </div>
            <div className="mb-2 flex items-baseline justify-between text-[11px] text-slate-400">
              <span>
                {lang === "zh" ? "应还" : "owe"}{" "}
                <span className="font-semibold text-slate-100">
                  ${(l.current_debt_usd ?? l.principal).toFixed(4)}
                </span>
              </span>
              <span>
                {lang === "zh" ? "本金" : "principal"} $
                {l.principal.toFixed(2)}
              </span>
            </div>
            <button
              onClick={() => onRepay(l)}
              className="w-full rounded-md bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-md hover:shadow-cyan-500/30 px-2 py-1.5 text-xs font-semibold text-white transition-all"
            >
              {lang === "zh" ? "还款" : "Repay"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- subcomponents ----------

/** Radio-style picker shown when the same event has 2+ CTF positions
 *  (e.g. moneyline + spread + total). Each row is a separate, independently-
 *  borrowable CTF token — the user picks which one to collateralize. */
function PositionPicker({
  positions,
  selectedAsset,
  onSelect,
  lang,
}: {
  positions: LendingPosition[];
  selectedAsset: string | null;
  onSelect: (asset: string) => void;
  lang: Lang;
}) {
  return (
    <div className="mb-3 space-y-1.5">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
        {lang === "zh"
          ? `选择持仓 (${positions.length})`
          : `Choose position (${positions.length})`}
      </div>
      {positions.map((p) => {
        const active = p.asset === selectedAsset;
        return (
          <button
            key={p.asset}
            type="button"
            onClick={() => onSelect(p.asset)}
            className={`flex w-full items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors ${
              active
                ? "border-emerald-500/50 bg-emerald-500/10"
                : "border-slate-700/60 bg-slate-900/40 hover:border-slate-600 hover:bg-slate-800/40"
            }`}
          >
            <span className="flex min-w-0 items-center gap-2">
              <span
                className={`inline-flex h-3 w-3 shrink-0 items-center justify-center rounded-full border ${
                  active ? "border-emerald-400" : "border-slate-600"
                }`}
              >
                {active && (
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                )}
              </span>
              <span className="min-w-0 truncate text-xs text-slate-200">
                {positionLabel(p, lang)}
              </span>
            </span>
            <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
              ${formatUsd(p.value_usd)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Compact, human-friendly label for a CTF position in the picker.
 *
 *  PM titles are verbose ("Spread: Newcastle United FC (-1.5)"); we reduce
 *  them to a market-type prefix + key fact:
 *    Spread  → "让分 NEW -1.5"      / "Spread NEW -1.5"
 *    Total   → "大小球 O 2.5"        / "O/U O 2.5"
 *    BTTS    → "双方进球 是/否"      / "BTTS Yes/No"
 *    Moneyline (3-way) → "胜负盘 NEW" or "胜负盘 平" / "Moneyline NEW" or "Moneyline Draw"
 *  Anything we can't classify falls back to the raw PM title.
 */
function positionLabel(p: LendingPosition, lang: Lang): string {
  const title = p.title ?? "";
  const outcome = p.outcome ?? "";

  // Spread (handicap) — title is e.g. "Spread: Newcastle United FC (-1.5)"
  // or "(+1.5)". Allow an optional leading +/- inside the parens; missing
  // sign is treated as positive.
  if (/^\s*spread\b/i.test(title)) {
    const raw = title.match(/\(([+-]?\d+(?:\.\d+)?)\)/)?.[1] ?? "";
    const sign = raw && !raw.startsWith("-") && !raw.startsWith("+")
      ? "+" + raw
      : raw;
    const team = shortTeamCode(outcome);
    const prefix = lang === "zh" ? "让分" : "Spread";
    return [prefix, team, sign].filter(Boolean).join(" ");
  }

  // Total (over/under)
  if (/^\s*total\b/i.test(title) || /\b(over|under)\b/i.test(outcome)) {
    const line = title.match(/\b(\d+(?:\.\d+)?)\b/)?.[1] ?? "";
    const ou = /under/i.test(outcome) ? "U" : "O";
    const prefix = lang === "zh" ? "大小球" : "O/U";
    return [prefix, ou, line].filter(Boolean).join(" ");
  }

  // Both Teams to Score
  if (/both\s+teams.*score/i.test(title)) {
    const isYes = /^\s*yes\s*$/i.test(outcome);
    const yn = lang === "zh" ? (isYes ? "是" : "否") : isYes ? "Yes" : "No";
    return `${lang === "zh" ? "双方进球" : "BTTS"} ${yn}`;
  }

  // Moneyline (3-way) — outcome is a team name or "Draw".
  if (outcome) {
    if (/^\s*draw\s*$/i.test(outcome)) {
      return lang === "zh" ? "胜负盘 平" : "Moneyline Draw";
    }
    const team = shortTeamCode(outcome);
    if (team) return `${lang === "zh" ? "胜负盘" : "Moneyline"} ${team}`;
  }

  // Last-resort fallback — keep raw title or asset stub.
  return title || `${p.asset.slice(0, 10)}…`;
}

/** Strip noise ("AFC " prefix, " FC" / " AFC" / " CF" suffix) and return the
 *  first significant word's first 3 chars uppercased. Mirrors the EPL slug
 *  abbreviations PM uses in URLs (e.g. NEW, NFO, ARS, TOT). Falls back to
 *  the input itself if shortening would yield nothing. */
function shortTeamCode(team: string): string {
  if (!team) return "";
  const cleaned = team
    .replace(/^AFC\s+/i, "")
    .replace(/\s+(FC|AFC|CF)$/i, "")
    .trim();
  const first = cleaned.split(/\s+/)[0] || cleaned;
  const code = first.slice(0, 3).toUpperCase();
  return code || team;
}

function ShellCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-3 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      {children}
    </div>
  );
}

/** Manual reload trigger surfaced on every status card so the user can force
 *  a refetch when they've just bought/sold on Polymarket and the side panel
 *  hasn't picked up the new position yet (we have no automatic listener for
 *  external PM trades — see CLAUDE/PR notes on the position-refresh design). */
function RefreshButton({
  syncing,
  lang,
  onClick,
}: {
  syncing: boolean;
  lang: Lang;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={syncing}
      title={t(lang, "lendingBorrowEntryRefreshTitle")}
      aria-label={t(lang, "lendingBorrowEntryRefreshTitle")}
      className="shrink-0 rounded-md p-1 text-slate-400 hover:text-slate-100 hover:bg-slate-800/60 transition-colors disabled:opacity-60"
    >
      <RotateCw size={13} className={syncing ? "animate-spin" : ""} />
    </button>
  );
}

function MagicProxyCard({
  lang,
  onOpen,
}: {
  lang: Lang;
  onOpen: () => void;
}) {
  return (
    <div className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
      <div className="mb-2 flex items-center gap-2">
        <Key size={16} className="text-amber-400" strokeWidth={2.25} />
        <span className="text-sm font-semibold text-amber-300">
          {t(lang, "lendingMagicTitle")}
        </span>
      </div>
      <div className="mb-3 text-xs text-amber-200/80">
        {t(lang, "lendingMagicLine1")}
      </div>
      <button
        onClick={onOpen}
        className="w-full rounded-lg border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 px-3 py-2 text-sm font-medium text-amber-200 transition-colors"
      >
        {t(lang, "lendingMagicCta")}
      </button>
    </div>
  );
}

function NoProxyCard({ lang }: { lang: Lang }) {
  return (
    <div className="mt-3 rounded-xl border border-dashed border-slate-700 bg-slate-900/40 p-4 text-center">
      <div className="mb-2 flex justify-center text-slate-500">
        <Inbox size={28} strokeWidth={1.5} />
      </div>
      <div className="mb-1 text-sm font-medium text-slate-300">
        {t(lang, "lendingNoProxyTitle")}
      </div>
      <div className="text-xs text-slate-500">
        {t(lang, "lendingNoProxyDesc")}
      </div>
    </div>
  );
}

function TierBadge({ tier, lang }: { tier: 1 | 2 | 3 | null; lang: Lang }) {
  if (!tier) return null;
  const key =
    tier === 1
      ? "lendingTierTop"
      : tier === 2
        ? "lendingTierMid"
        : "lendingTierLow";
  const color =
    tier === 1
      ? "bg-green-500/10 text-green-400 border-green-500/30"
      : tier === 2
        ? "bg-blue-500/10 text-blue-400 border-blue-500/30"
        : "bg-amber-500/10 text-amber-400 border-amber-500/30";
  return (
    <span
      className={`ml-auto rounded-full border px-2 py-0.5 text-[10px] font-medium ${color}`}
    >
      {t(lang, key as "lendingTierTop")}
    </span>
  );
}

function formatShares(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return n.toFixed(n >= 10 ? 0 : 1);
}

function formatUsd(n: number): string {
  if (n >= 1000) return n.toFixed(0);
  return n.toFixed(2);
}

/** Find every Polymarket Data-API position that belongs to the currently-
 *  viewed event — moneyline + spread + total + BTTS + props are independent
 *  CTF tokens but live under the same event (or its `-more-markets` sibling).
 *
 *  Returns positions in priority order, deduped by `asset` (CTF token id):
 *    1. conditionId exact (unique per market)
 *    2. event_slug exact (moneyline lives on the main event)
 *    3. event_slug shares the date+teams stem (PM puts spread/total/BTTS
 *       under a `<slug>-more-markets` sibling event — confirmed on 2026-05-09
 *       EPL NFO-NEW: page slug `epl-not-new-2026-05-10`, spread eventSlug
 *       `epl-not-new-2026-05-10-more-markets`)
 *    4. title contains both team full names (last-resort catch-all)
 *
 *  Caller picks one to use as collateral via the PositionPicker UI.
 */
function findMatchingPositions(
  positions: LendingPosition[],
  market: FootballMarket,
): LendingPosition[] {
  const seen = new Set<string>();
  const out: LendingPosition[] = [];
  const push = (p: LendingPosition) => {
    if (!p.asset || seen.has(p.asset)) return;
    seen.add(p.asset);
    out.push(p);
  };

  if (market.conditionId) {
    for (const p of positions) {
      if (p.condition_id === market.conditionId) push(p);
    }
  }

  if (market.slug) {
    for (const p of positions) {
      if (p.event_slug === market.slug) push(p);
    }
    const sep = market.slug + "-";
    for (const p of positions) {
      if (
        p.event_slug &&
        (p.event_slug.startsWith(sep) ||
          market.slug.startsWith(p.event_slug + "-"))
      ) {
        push(p);
      }
    }
  }

  const home = market.homeTeam?.toLowerCase() ?? "";
  const away = market.awayTeam?.toLowerCase() ?? "";
  if (home && away) {
    for (const p of positions) {
      const title = p.title?.toLowerCase() ?? "";
      if (title.includes(home) && title.includes(away)) push(p);
    }
  }

  return out;
}

