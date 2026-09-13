import { AlertTriangle, Ban, CircleDollarSign, Lock } from "lucide-react";
import type { Lang } from "@shared/types";
import type { LendingPosition, LoanItem } from "@shared/lending-api";

interface Props {
  position: LendingPosition;
  /** Active loan against this CTF, if any. Drives the "borrowed" variant. */
  loan: LoanItem | null;
  lang: Lang;
  onBorrow: () => void;
  onRepay: () => void;
}

export default function AssetCard({
  position,
  loan,
  lang,
  onBorrow,
  onRepay,
}: Props) {
  if (loan && (loan.status === "active" || loan.status === "pending")) {
    return (
      <BorrowedCard
        position={position}
        loan={loan}
        lang={lang}
        onRepay={onRepay}
      />
    );
  }
  if (position.borrow_eligible) {
    return (
      <EligibleCard
        position={position}
        lang={lang}
        onBorrow={onBorrow}
      />
    );
  }
  if (position.ineligible_reason === "unsupported_league") {
    return <UnsupportedLeagueCard position={position} lang={lang} />;
  }
  if (position.ineligible_reason === "kickoff_too_close") {
    return <KickoffClosedCard position={position} lang={lang} />;
  }
  return <OtherCard position={position} lang={lang} />;
}

// ---------- variant: kickoff <24h ----------

function KickoffClosedCard({
  position,
  lang,
}: {
  position: LendingPosition;
  lang: Lang;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-3.5 space-y-2 opacity-80">
      <CardHeader
        position={position}
        grayscale
        rightBadge={
          <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-0 text-[10px] text-amber-300 shrink-0">
            {lang === "zh" ? "即将开赛" : "Soon"}
          </span>
        }
      />
      <div className="flex items-start gap-1.5 rounded-lg bg-slate-800/40 px-2.5 py-1.5 text-[11px] text-slate-400">
        <Lock size={12} strokeWidth={2.25} className="shrink-0 mt-0.5" />
        <span>
          {lang === "zh"
            ? "比赛 24 小时内开赛，已停止借款"
            : "Match starts within 24h — borrowing is closed"}
        </span>
      </div>
    </div>
  );
}

// ---------- variant: borrowed (active loan) ----------

function BorrowedCard({
  position,
  loan,
  lang,
  onRepay,
}: {
  position: LendingPosition;
  loan: LoanItem;
  lang: Lang;
  onRepay: () => void;
}) {
  const owed = loan.current_debt_usd ?? loan.principal;
  const ltv = loan.current_ltv;
  const health = loan.health_status ?? "healthy";

  // Tier-specific liquidation threshold (matches backend config). Used to
  // anchor the progress bar's "100%" + the warning chip text.
  const liqLtv = TIER_LIQUIDATION[loan.league_tier] ?? 0.8;
  const warnLtv = TIER_WARNING[loan.league_tier] ?? 0.7;

  // Progress is the raw LTV on a 0–100% scale (NOT scaled to liq). This way
  // the fill width matches the LTV number the user reads above the bar:
  // 51% LTV = ~half-filled bar, with the warning + liq tick marks rendered
  // as absolute-positioned overlays at their real positions on the same
  // scale. Clamped to 100% so a post-liquidation bar still fits.
  const barFrac = ltv != null ? Math.min(1, ltv) : 0;

  // Expected price drop until liquidation (CTF prices fall = collateral
  // shrinks = LTV rises). Only show this if we have a current price snapshot.
  const liqDrop = computeLiquidationDrop(loan, liqLtv);

  const tone = healthTone(health);

  return (
    <div className={`rounded-xl border p-3.5 space-y-2.5 ${tone.border} ${tone.bg} ${tone.shadow}`}>
      <CardHeader
        position={position}
        rightBadge={
          <span className={`rounded-full border px-1.5 py-0 text-[10px] shrink-0 ${tone.badge}`}>
            {lang === "zh" ? "借款中" : "Borrowed"}
          </span>
        }
      />

      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-lg bg-slate-900/60 px-2.5 py-1.5">
          <div className="text-[10px] text-slate-500">
            {lang === "zh" ? "应还" : "Owed"}
          </div>
          <div className="font-semibold tabular-nums text-slate-100">
            ${owed.toFixed(4)}
          </div>
        </div>
        <div className="rounded-lg bg-slate-900/60 px-2.5 py-1.5">
          <div className="text-[10px] text-slate-500">
            {lang === "zh" ? "LTV · 健康" : "LTV · Health"}
          </div>
          <div className={`font-semibold ${tone.text}`}>
            {ltv != null ? `${(ltv * 100).toFixed(0)}%` : "—"} ·{" "}
            {healthLabel(health, lang)}
          </div>
        </div>
      </div>

      <div>
        {/* The bar uses a true 0–100% LTV scale (NOT scaled to liq) so
            the fill width visually matches the LTV number. Warning and
            liquidation thresholds are absolute-positioned tick marks on
            the bar, and the labels below land at the same percentage
            positions — so 51% LTV reads as "just past half" with the
            warning tick clearly to the right, instead of looking like the
            user has already crossed the warning line. */}
        <div className="relative h-1.5 w-full rounded-full bg-slate-800 overflow-hidden">
          <div
            className={`h-full ${tone.bar} transition-all`}
            style={{ width: `${(barFrac * 100).toFixed(1)}%` }}
          />
          <div
            className="absolute top-0 h-full w-px bg-amber-300/80"
            style={{ left: `${(warnLtv * 100).toFixed(1)}%` }}
          />
          <div
            className="absolute top-0 h-full w-px bg-red-300/80"
            style={{ left: `${(liqLtv * 100).toFixed(1)}%` }}
          />
        </div>
        <div className="relative mt-0.5 h-3 text-[9px] text-slate-500">
          <span className="absolute left-0">0%</span>
          <span
            className="absolute -translate-x-1/2 text-amber-400 whitespace-nowrap"
            style={{ left: `${(warnLtv * 100).toFixed(1)}%` }}
          >
            {(warnLtv * 100).toFixed(0)}%
          </span>
          <span
            className="absolute -translate-x-1/2 text-red-400 whitespace-nowrap"
            style={{ left: `${(liqLtv * 100).toFixed(1)}%` }}
          >
            {(liqLtv * 100).toFixed(0)}%
          </span>
          <span className="absolute right-0">100%</span>
        </div>
      </div>

      {liqDrop != null && (health === "warn" || health === "caution") && (
        <div className="flex items-center gap-1.5 rounded-md bg-amber-500/10 border border-amber-500/30 px-2 py-1 text-[10px] text-amber-200">
          <AlertTriangle size={12} strokeWidth={2.25} className="shrink-0" />
          <span>
            {lang === "zh"
              ? `抵押价格再跌 $${liqDrop.toFixed(2)} 触发强平`
              : `Collateral drops $${liqDrop.toFixed(2)} more → liquidation`}
          </span>
        </div>
      )}

      <button
        onClick={onRepay}
        className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-md hover:shadow-cyan-500/30 px-3 py-2 text-xs font-semibold text-white transition-all"
      >
        {lang === "zh"
          ? `还款 $${owed.toFixed(4)}`
          : `Repay $${owed.toFixed(4)}`}
      </button>
    </div>
  );
}

// ---------- variant: eligible (no active loan) ----------

function EligibleCard({
  position,
  lang,
  onBorrow,
}: {
  position: LendingPosition;
  lang: Lang;
  onBorrow: () => void;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 space-y-2.5">
      <CardHeader
        position={position}
        rightBadge={<TierBadge tier={position.league_tier} lang={lang} />}
      />

      <div className="rounded-lg bg-slate-800/40 px-2.5 py-2 text-[11px] text-slate-300 flex items-center justify-between">
        <span className="flex items-center gap-1.5">
          <CircleDollarSign size={14} className="text-emerald-400" />
          {lang === "zh" ? "可借最高" : "Max borrow"}
        </span>
        <span className="font-semibold text-slate-100 tabular-nums">
          ${position.max_borrowable_usd.toFixed(2)}{" "}
          <span className="text-slate-500 font-normal">· 12% APR</span>
        </span>
      </div>

      <button
        onClick={onBorrow}
        className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-md hover:shadow-cyan-500/30 px-3 py-2 text-xs font-semibold text-white transition-all"
      >
        {lang === "zh" ? "借出 USDC" : "Borrow USDC"}
      </button>
    </div>
  );
}

// ---------- variant: unsupported league (football, but not in scope) ----------

function UnsupportedLeagueCard({
  position,
  lang,
}: {
  position: LendingPosition;
  lang: Lang;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-3.5 space-y-2 opacity-80">
      <CardHeader
        position={position}
        grayscale
        rightBadge={
          <span className="rounded-full border border-slate-700 bg-slate-800 px-1.5 py-0 text-[10px] text-slate-500 shrink-0">
            {lang === "zh" ? "未支持" : "Unsupported"}
          </span>
        }
      />
      <div className="flex items-start gap-1.5 rounded-lg bg-slate-800/40 px-2.5 py-1.5 text-[11px] text-slate-400">
        <Lock size={12} strokeWidth={2.25} className="shrink-0 mt-0.5" />
        <span>
          {lang === "zh"
            ? "该联赛暂未上线借贷 · 当前支持 Premier League · World Cup"
            : "Lending isn't live for this league yet · supported: Premier League · World Cup"}
        </span>
      </div>
      {/* "Notify me" CTA — non-blocking; quietly logs the user's interest.
          Routes to a no-op for now; backend endpoint is on the Phase 2 list. */}
      <button
        disabled
        className="w-full rounded-lg border border-slate-700 px-3 py-1.5 text-[11px] text-slate-500 cursor-not-allowed"
      >
        {lang === "zh" ? "支持后通知我（即将上线）" : "Notify me (coming soon)"}
      </button>
    </div>
  );
}

// ---------- variant: non-football / zero-value ----------

function OtherCard({
  position,
  lang,
}: {
  position: LendingPosition;
  lang: Lang;
}) {
  const isZero = position.ineligible_reason === "zero_value";
  const badge = isZero
    ? lang === "zh" ? "已结算" : "Settled"
    : lang === "zh" ? "非足球" : "Non-football";
  const desc = isZero
    ? lang === "zh"
      ? "该持仓当前估值为 $0"
      : "This position currently marks at $0"
    : lang === "zh"
      ? "KPAX 当前仅支持足球持仓"
      : "KPAX currently only supports football positions";

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-3.5 space-y-2 opacity-70">
      <CardHeader
        position={position}
        grayscale
        rightBadge={
          <span className="rounded-full border border-slate-700 bg-slate-800 px-1.5 py-0 text-[10px] text-slate-500 shrink-0">
            {badge}
          </span>
        }
      />
      <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
        {!isZero && <Ban size={12} strokeWidth={2.25} className="shrink-0" />}
        {desc}
      </div>
    </div>
  );
}

// ---------- shared subcomponents ----------

function CardHeader({
  position,
  rightBadge,
  grayscale,
}: {
  position: LendingPosition;
  rightBadge?: React.ReactNode;
  grayscale?: boolean;
}) {
  const valueLine =
    position.value_usd > 0
      ? `${formatShares(position.size)} · $${position.value_usd.toFixed(2)}`
      : "—";

  return (
    <div className="flex items-start gap-2">
      <span className={`text-base ${grayscale ? "grayscale" : ""}`}>⚽</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-slate-100 truncate">
          {position.title ?? "—"}
        </div>
        <div className="text-[11px] text-slate-500 truncate">
          {position.outcome ? `${position.outcome} · ` : ""}
          {valueLine}
        </div>
      </div>
      {rightBadge}
    </div>
  );
}

function TierBadge({
  tier,
  lang,
}: {
  tier: 1 | 2 | 3 | null;
  lang: Lang;
}) {
  if (tier == null) return null;
  const cls =
    tier === 1
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
      : tier === 2
        ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
        : "border-amber-500/40 bg-amber-500/10 text-amber-300";
  const label =
    tier === 1
      ? (lang === "zh" ? "顶级" : "Top")
      : tier === 2
        ? (lang === "zh" ? "次级" : "Mid")
        : (lang === "zh" ? "三级" : "Low");
  return (
    <span className={`rounded-full border px-1.5 py-0 text-[10px] shrink-0 ${cls}`}>
      ●{label}
    </span>
  );
}

// ---------- helpers ----------

const TIER_WARNING: Record<number, number> = { 1: 0.7, 2: 0.65, 3: 0.55 };
const TIER_LIQUIDATION: Record<number, number> = { 1: 0.8, 2: 0.75, 3: 0.65 };

/** Tone tokens per health bucket. The `shadow` field gives at-risk loans
 *  visual weight beyond just hue — a soft glow on warn/liquidate cards
 *  draws the eye when the asset list mixes safe + endangered loans. */
function healthTone(h: NonNullable<LoanItem["health_status"]>): {
  border: string;
  bg: string;
  badge: string;
  bar: string;
  text: string;
  shadow: string;
} {
  switch (h) {
    case "healthy":
      return {
        border: "border-blue-500/25",
        bg: "bg-blue-500/5",
        badge: "border-blue-500/40 bg-blue-500/10 text-blue-300",
        bar: "bg-gradient-to-r from-emerald-500 to-emerald-400",
        text: "text-emerald-300",
        shadow: "",
      };
    case "caution":
      return {
        border: "border-amber-500/30",
        bg: "bg-amber-500/5",
        badge: "border-amber-500/40 bg-amber-500/10 text-amber-300",
        bar: "bg-gradient-to-r from-emerald-500 via-amber-400 to-amber-500",
        text: "text-amber-300",
        shadow: "shadow-md shadow-amber-500/5",
      };
    case "warn":
      return {
        border: "border-amber-500/40",
        bg: "bg-amber-500/10",
        badge: "border-amber-500/50 bg-amber-500/20 text-amber-200",
        bar: "bg-gradient-to-r from-amber-500 to-orange-500",
        text: "text-orange-300",
        shadow: "shadow-lg shadow-amber-500/10",
      };
    case "liquidate":
      return {
        border: "border-red-500/40",
        bg: "bg-red-500/10",
        badge: "border-red-500/50 bg-red-500/20 text-red-200",
        bar: "bg-gradient-to-r from-orange-500 to-red-500",
        text: "text-red-300",
        shadow: "shadow-lg shadow-red-500/15 ring-1 ring-red-500/20",
      };
  }
}

function healthLabel(
  h: NonNullable<LoanItem["health_status"]>,
  lang: Lang,
): string {
  if (lang === "zh") {
    switch (h) {
      case "healthy": return "健康";
      case "caution": return "留意";
      case "warn": return "临近警戒";
      case "liquidate": return "触发清算";
    }
  }
  switch (h) {
    case "healthy": return "Healthy";
    case "caution": return "Caution";
    case "warn": return "Near liq.";
    case "liquidate": return "At-risk";
  }
}

function formatShares(n: number): string {
  if (!n || n <= 0) return "0";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return n.toFixed(n >= 10 ? 0 : 1);
}

/** How far the collateral price can drop before LTV hits the liquidation
 *  threshold. Returns USD per CTF token (price units). */
function computeLiquidationDrop(loan: LoanItem, liqLtv: number): number | null {
  const debt = loan.current_debt_usd;
  const price = loan.current_collateral_price;
  const shares = loan.collateral_shares;
  if (debt == null || price == null || !shares) return null;
  const liqPrice = debt / (shares * liqLtv);
  return Math.max(0, price - liqPrice);
}
