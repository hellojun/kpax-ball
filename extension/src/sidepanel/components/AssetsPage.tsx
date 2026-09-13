import { useEffect, useMemo, useState } from "react";
import { Inbox, Key, RotateCw } from "lucide-react";
import { t } from "@shared/i18n";
import type { Lang } from "@shared/types";
import {
  fetchLoans,
  fetchPositions,
  fetchTosStatus,
  type LendingPosition,
  type LoanItem,
  type PositionsResponse,
  type TosStatus,
} from "@shared/lending-api";
import AssetCard from "./AssetCard";
import MagicGuide from "./MagicGuide";
import TosModal from "./TosModal";

interface Props {
  lang: Lang;
  /** Bumped by App after every borrow/repay. We watch it as a useEffect dep
   *  to refetch positions + loans. */
  lendingReloadVersion: number;
  /** Called after each successful fetchLoans so App can drop optimistic
   *  borrowed-CTF entries that the real "active" rows have caught up on. */
  onLoansLoaded: (activeLoans: LoanItem[]) => void;
  /** CTFs that App treats as "loan just opened" before /loans confirms. */
  optimisticBorrowedCtfs: Set<string>;
  /** User clicked "借出 USDC" on an asset card. App owns the BorrowFlowDialog. */
  onStartBorrow: (session: { proxy: string; position: LendingPosition }) => void;
  /** User clicked "还款" on a card. App owns the RepayModal. */
  onStartRepay: (loan: LoanItem) => void;
  /** Navigate to the loan history sub-page. Optional — not used when `embedded`
   *  (Profile already owns the history entry). */
  onViewHistory?: () => void;
  /** When true, render only the [filter chips + cards] body — skip the
   *  triple-stat overview, the page-level "我的资产 + 刷新" header, and the
   *  footer history link. The Lending tab in Profile uses this to inline the
   *  cards directly under its own overview, removing the old "click to
   *  expand" indirection. */
  embedded?: boolean;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; error: string }
  | { kind: "ready"; data: PositionsResponse; loans: LoanItem[] };

type Filter = "all" | "eligible" | "borrowed" | "ineligible";

export default function AssetsPage({
  lang,
  lendingReloadVersion,
  onLoansLoaded,
  optimisticBorrowedCtfs,
  onStartBorrow,
  onStartRepay,
  onViewHistory,
  embedded = false,
}: Props) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [tos, setTos] = useState<TosStatus | null>(null);
  const [showTos, setShowTos] = useState(false);
  const [showMagicGuide, setShowMagicGuide] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [pendingBorrow, setPendingBorrow] = useState<{
    proxy: string;
    position: LendingPosition;
  } | null>(null);

  async function loadAll() {
    try {
      const [positionsResp, tosResp, loansResp] = await Promise.all([
        fetchPositions(),
        fetchTosStatus(),
        fetchLoans().catch(() => ({ loans: [] as LoanItem[] })),
      ]);
      setState({ kind: "ready", data: positionsResp, loans: loansResp.loans });
      setTos(tosResp);
      const active = loansResp.loans.filter(
        (l) => l.status === "active" || l.status === "pending",
      );
      onLoansLoaded(active);
    } catch (e) {
      setState({
        kind: "error",
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }

  useEffect(() => {
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // App bumps lendingReloadVersion after any borrow/repay tx. We refetch.
  useEffect(() => {
    if (lendingReloadVersion === 0) return;
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lendingReloadVersion]);

  // Build a lookup of CTF token id -> active loan (real or optimistic
  // placeholder). Optimistic loans only carry enough fields to render the
  // "Borrowed" card with placeholder values until /loans catches up.
  const loanByCtf = useMemo(() => {
    const map = new Map<string, LoanItem>();
    if (state.kind === "ready") {
      for (const l of state.loans) {
        if (l.status === "active" || l.status === "pending") {
          map.set(l.ctf_token_id, l);
        }
      }
    }
    return map;
  }, [state]);

  function handleBorrow(p: LendingPosition) {
    if (state.kind !== "ready" || !state.data.proxy) return;
    if (tos && !tos.accepted) {
      setPendingBorrow({ proxy: state.data.proxy, position: p });
      setShowTos(true);
      return;
    }
    onStartBorrow({ proxy: state.data.proxy, position: p });
  }

  // ---------- render ----------

  if (state.kind === "loading") {
    return (
      <Shell lang={lang}>
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-6 text-center text-xs text-slate-500">
          {lang === "zh" ? "加载持仓中..." : "Loading positions…"}
        </div>
      </Shell>
    );
  }

  if (state.kind === "error") {
    return (
      <Shell lang={lang}>
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4 text-center text-xs text-red-400">
          {lang === "zh" ? "加载失败：" : "Failed to load: "}
          {state.error}
        </div>
      </Shell>
    );
  }

  if (state.data.proxy_kind === "magic") {
    return (
      <Shell lang={lang}>
        <MagicProxyCard lang={lang} onOpen={() => setShowMagicGuide(true)} />
        {showMagicGuide && (
          <MagicGuide lang={lang} onClose={() => setShowMagicGuide(false)} />
        )}
      </Shell>
    );
  }

  if (state.data.proxy_kind === "none") {
    return (
      <Shell lang={lang}>
        <NoProxyCard lang={lang} />
      </Shell>
    );
  }

  // proxy_kind === "safe"
  const rawPositions = state.data.positions;

  // Merge in synthetic positions for active/pending loans whose CTF no longer
  // shows up in /positions. Once the user borrows, the CTF moves from the PM
  // proxy into the KPAX vault, so Polymarket's Data API stops returning it —
  // but the loan record is still alive in our DB. Without this synthesis the
  // borrowed card would silently disappear from the Assets page even though
  // 应还总额 still aggregates it correctly. We use loan fields to fill in just
  // enough of LendingPosition to drive the borrowed-card render path.
  const positionAssets = new Set(rawPositions.map((p) => p.asset));
  const synthesizedFromLoans: LendingPosition[] = state.loans
    .filter(
      (l) =>
        (l.status === "active" || l.status === "pending") &&
        !positionAssets.has(l.ctf_token_id),
    )
    .map((l) => loanToPosition(l));
  const positions: LendingPosition[] = [...rawPositions, ...synthesizedFromLoans];

  // For each position decide its "category" — used by both the overview
  // triple-stat and the filter chips.
  type Cat = "borrowed" | "eligible" | "ineligible";
  function categorize(p: LendingPosition): Cat {
    if (loanByCtf.has(p.asset) || optimisticBorrowedCtfs.has(p.asset)) {
      return "borrowed";
    }
    return p.borrow_eligible ? "eligible" : "ineligible";
  }
  const categorized = positions.map((p) => ({ p, cat: categorize(p) }));

  const counts = {
    all: categorized.length,
    borrowed: categorized.filter((c) => c.cat === "borrowed").length,
    eligible: categorized.filter((c) => c.cat === "eligible").length,
    ineligible: categorized.filter((c) => c.cat === "ineligible").length,
  };

  // Total value combines free positions + collateral value of borrowed CTFs.
  // The synthesized entries already carry a value_usd (= collateral mark) so
  // a single sum over `positions` is correct.
  const totalValue = positions.reduce((acc, p) => acc + (p.value_usd ?? 0), 0);
  const totalDebt = state.loans
    .filter((l) => l.status === "active" || l.status === "pending")
    .reduce((acc, l) => acc + (l.current_debt_usd ?? l.principal), 0);

  // Sort cards: warn/caution loans first → healthy loans → eligible →
  // ineligible. Within "borrowed", sort by descending LTV so the most-at-risk
  // float to the top.
  const order: Record<string, number> = {
    warn: 0,
    caution: 1,
    liquidate: 0, // bubble at-risk to top alongside warn
    healthy: 2,
  };
  const sorted = [...categorized].sort((a, b) => {
    const score = (c: typeof a) => {
      if (c.cat === "borrowed") {
        const loan = loanByCtf.get(c.p.asset);
        const h = loan?.health_status ?? "healthy";
        return 0 + (order[h] ?? 2) * 0.1; // 0.0 ~ 0.2
      }
      if (c.cat === "eligible") return 1;
      // ineligible: kickoff_too_close (otherwise borrowable, just timed out)
      // before unsupported_league before non_football before zero_value
      const r = c.p.ineligible_reason;
      if (r === "kickoff_too_close") return 2;
      if (r === "unsupported_league") return 3;
      if (r === "non_football") return 4;
      return 5;
    };
    return score(a) - score(b);
  });

  // Apply filter chip on top of the sorted list.
  const displayed = sorted.filter(({ cat }) => {
    if (filter === "all") return true;
    return cat === filter;
  });

  const repaidCount = state.loans.filter((l) => l.status === "repaid").length;

  return (
    <Shell lang={lang} onRefresh={embedded ? undefined : loadAll}>
      {/* Overview triple — skipped when embedded; the parent (LendingTab)
          already shows its own summary stats above. */}
      {!embedded && (
        <section>
          <div className="grid grid-cols-3 gap-2">
            <Stat value={String(positions.length)} label={lang === "zh" ? "总持仓" : "Positions"} />
            <Stat value={`$${totalValue.toFixed(2)}`} label={lang === "zh" ? "总价值" : "Total value"} />
            <Stat
              value={String(counts.borrowed)}
              label={lang === "zh" ? "借款中" : "Borrowed"}
              tone="blue"
              sub={totalDebt > 0 ? `${lang === "zh" ? "应还" : "Owed"} $${totalDebt.toFixed(2)}` : undefined}
            />
          </div>
        </section>
      )}

      {/* Filter chips. When embedded inside the Lending tab the row also
          carries the inline "历史 ›" link on the right — saves a full-width
          button and gets the user to history without leaving the chip line. */}
      <section>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            <Chip active={filter === "all"} onClick={() => setFilter("all")}>
              {lang === "zh" ? "全部" : "All"} {counts.all}
            </Chip>
            <Chip active={filter === "eligible"} onClick={() => setFilter("eligible")}>
              {lang === "zh" ? "可借" : "Eligible"} {counts.eligible}
            </Chip>
            <Chip active={filter === "borrowed"} onClick={() => setFilter("borrowed")}>
              {lang === "zh" ? "借款中" : "Borrowed"} {counts.borrowed}
            </Chip>
            <Chip active={filter === "ineligible"} onClick={() => setFilter("ineligible")}>
              {lang === "zh" ? "不支持" : "Other"} {counts.ineligible}
            </Chip>
          </div>
          {embedded && onViewHistory && (
            <button
              onClick={onViewHistory}
              className="text-[11px] text-slate-400 hover:text-cyan-300 transition-colors shrink-0"
            >
              {lang === "zh" ? "历史 ›" : "History ›"}
            </button>
          )}
        </div>
      </section>

      {/* cards */}
      {positions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/40 p-6 text-center">
          <div className="mb-3 flex justify-center text-slate-500">
            <Inbox size={36} strokeWidth={1.5} />
          </div>
          <div className="text-sm text-slate-300 mb-1">
            {lang === "zh" ? "你还没有 Polymarket 持仓" : "No Polymarket positions yet"}
          </div>
          <div className="text-xs text-slate-500">
            {lang === "zh"
              ? "在 Polymarket 上买一份你看好的比赛，KPAX 就能为你提供借贷"
              : "Buy a market on Polymarket first; KPAX can lend against it once you have a position."}
          </div>
        </div>
      ) : displayed.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-4 text-center text-xs text-slate-500">
          {lang === "zh" ? "此筛选下没有持仓" : "No positions match this filter"}
        </div>
      ) : (
        <div className="space-y-2">
          {displayed.map(({ p }) => (
            <AssetCard
              key={p.asset}
              position={p}
              loan={loanByCtf.get(p.asset) ?? null}
              lang={lang}
              onBorrow={() => handleBorrow(p)}
              onRepay={() => {
                const loan = loanByCtf.get(p.asset);
                if (loan) onStartRepay(loan);
              }}
            />
          ))}
        </div>
      )}

      {/* Footer history link — skipped when embedded; the parent (LendingTab)
          already renders a "借款历史" entry button. */}
      {!embedded && onViewHistory && (
        <button
          onClick={onViewHistory}
          className="block w-full rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-3 text-center text-xs text-slate-400 hover:bg-slate-800/40 transition-colors"
        >
          {lang === "zh"
            ? `查看借款历史 (${repaidCount} 笔已还款) ›`
            : `View loan history (${repaidCount} repaid) ›`}
        </button>
      )}

      {showTos && tos && (
        <TosModal
          tosVersion={tos.current_version}
          lang={lang}
          onAccepted={() => {
            setShowTos(false);
            setTos({ ...tos, accepted: true, user_version: tos.current_version });
            if (pendingBorrow) {
              onStartBorrow(pendingBorrow);
              setPendingBorrow(null);
            }
          }}
          onCancel={() => {
            setShowTos(false);
            setPendingBorrow(null);
          }}
        />
      )}
    </Shell>
  );
}

// ---------- subcomponents ----------

function Shell({
  children,
  lang,
  onRefresh,
}: {
  children: React.ReactNode;
  lang: Lang;
  onRefresh?: () => void;
}) {
  // Note: the back button + page title are provided by the parent (Profile)
  // since this page lives as a sub-view within Profile. We render only the
  // body content — the parent handles its own header bar.
  return (
    <div className="flex flex-col gap-3">
      {onRefresh && (
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {lang === "zh" ? "我的资产" : "My Assets"}
          </span>
          <button
            onClick={onRefresh}
            className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-slate-200 px-2 py-0.5 rounded border border-slate-700 hover:bg-slate-800 transition-colors"
          >
            <RotateCw size={11} strokeWidth={2.25} />
            {lang === "zh" ? "刷新" : "Refresh"}
          </button>
        </div>
      )}
      {children}
    </div>
  );
}

function Stat({
  value,
  label,
  sub,
  tone,
}: {
  value: string;
  label: string;
  sub?: string;
  tone?: "blue";
}) {
  const cls =
    tone === "blue"
      ? "border-blue-500/30 bg-blue-500/5 text-blue-300"
      : "border-slate-800 bg-slate-900/60 text-slate-50";
  const labelCls = tone === "blue" ? "text-blue-300/70" : "text-slate-500";
  return (
    <div className={`rounded-xl border p-3 text-center ${cls}`}>
      <div className="text-xl font-bold tabular-nums">{value}</div>
      <div className={`text-[10px] mt-0.5 ${labelCls}`}>{label}</div>
      {sub && <div className={`text-[10px] mt-0.5 ${labelCls}`}>{sub}</div>}
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "rounded-full px-2.5 py-1 transition-all " +
        (active
          ? "bg-gradient-to-r from-emerald-500 to-cyan-500 text-white font-semibold shadow-sm shadow-cyan-500/30"
          : "border border-slate-700 text-slate-300 hover:bg-slate-800 hover:border-slate-600 hover:text-slate-100")
      }
    >
      {children}
    </button>
  );
}

function MagicProxyCard({ lang, onOpen }: { lang: Lang; onOpen: () => void }) {
  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
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
    <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/40 p-6 text-center">
      <div className="mb-3 flex justify-center text-slate-500">
        <Inbox size={36} strokeWidth={1.5} />
      </div>
      <div className="mb-1 text-sm font-medium text-slate-300">
        {t(lang, "lendingNoProxyTitle")}
      </div>
      <div className="text-xs text-slate-500">{t(lang, "lendingNoProxyDesc")}</div>
    </div>
  );
}

/** Build a LendingPosition stand-in from an active loan whose collateral
 *  CTF no longer appears in /positions (it now sits in the KPAX vault). The
 *  card render path keys off `position.asset === loan.ctf_token_id`, so the
 *  loan's mark fields go straight into the position shape — borrow_eligible
 *  is forced to false because there's already a loan against it. */
function loanToPosition(loan: LoanItem): LendingPosition {
  const valueUsd =
    loan.current_collateral_value_usd ?? loan.collateral_value_at_open;
  return {
    asset: loan.ctf_token_id,
    condition_id: "",
    event_slug: loan.market_slug || null,
    title: loan.title ?? loan.market_slug ?? null,
    outcome: null,
    size: loan.collateral_shares,
    current_price: loan.current_collateral_price ?? 0,
    value_usd: valueUsd,
    competition_hint: null,
    league_tier: loan.league_tier,
    borrow_eligible: false,
    ineligible_reason: null,
    max_borrowable_usd: 0,
  };
}
