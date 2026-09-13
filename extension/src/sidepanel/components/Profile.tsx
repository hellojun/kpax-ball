import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  Copy,
  ExternalLink,
  HelpCircle,
  RotateCw,
  Shield,
  TrendingUp,
  User,
  Wallet,
  X,
} from "lucide-react";
import type { UserInfo } from "@shared/auth";
import { t } from "@shared/i18n";
import type { Lang } from "@shared/types";
import {
  fetchLoans,
  fetchPositions,
  type LendingPosition,
  type LoanItem,
} from "@shared/lending-api";
import {
  POLYGON_USDC_E_ADDRESS,
  formatUsdc,
  type UsdcBalance,
} from "@shared/wallet";
import AssetsPage from "./AssetsPage";
import LendingHistory from "./LendingHistory";

interface ProfileProps {
  user: UserInfo;
  balance: UsdcBalance | null;
  balanceLoading: boolean;
  lang: Lang;
  onBack: () => void;
  onRefresh: () => void;
  onSignOut: () => void;
  // ---- Lending coordination (passed through from App) ----
  /** CTFs the user *just* opened a loan against — used by AssetsPage to
   *  optimistically render the "borrowed" card before /loans confirms. */
  optimisticBorrowedCtfs: Set<string>;
  /** App bumps this after every borrow/repay tx so AssetsPage refetches. */
  lendingReloadVersion: number;
  /** AssetsPage hands the freshly-fetched active loans up so App can drop
   *  optimistic-borrowed entries that real data has caught up on. */
  onLoansLoaded: (activeLoans: LoanItem[]) => void;
  /** AssetsPage triggers borrow flow → App owns the BorrowFlowDialog. */
  onStartBorrow: (session: { proxy: string; position: LendingPosition }) => void;
  /** AssetsPage triggers repay flow → App owns the RepayModal. */
  onStartRepay: (loan: LoanItem) => void;
}

type SubView = "main" | "history";
type Tab = "account" | "lending" | "insurance" | "leverage";

export default function Profile({
  user,
  balance,
  balanceLoading,
  lang,
  onBack,
  onRefresh,
  onSignOut,
  optimisticBorrowedCtfs,
  lendingReloadVersion,
  onLoansLoaded,
  onStartBorrow,
  onStartRepay,
}: ProfileProps) {
  const [showDeposit, setShowDeposit] = useState(false);
  const [subView, setSubView] = useState<SubView>("main");
  const [activeTab, setActiveTab] = useState<Tab>("account");

  // Profile-level lending data fetch — drives the lending-tab summary +
  // the red-dot badge on the lending tab. Refetches whenever App bumps
  // lendingReloadVersion (i.e. after every borrow/repay tx).
  const [loans, setLoans] = useState<LoanItem[] | null>(null);
  const [positions, setPositions] = useState<LendingPosition[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      fetchLoans().catch(() => ({ loans: [] as LoanItem[] })),
      fetchPositions().catch(() => ({
        positions: [] as LendingPosition[],
        proxy: null,
        proxy_kind: "none",
        wallet_address: "",
        eligible_count: 0,
        eligible_total_value_usd: 0,
      })),
    ]).then(([l, p]) => {
      if (cancelled) return;
      setLoans(l.loans);
      setPositions(p.positions);
    });
    return () => {
      cancelled = true;
    };
  }, [lendingReloadVersion]);

  // History is the only sub-view now — assets are inlined directly in the
  // Lending tab. Back from history returns to the lending tab (preserved
  // since activeTab state is unchanged).
  if (subView === "history") {
    return <LendingPage lang={lang} onBack={() => setSubView("main")} />;
  }

  // Red-dot signal on the lending tab — surfaces a risk warning even when
  // the user is on a different tab. Triggers on caution / warn / liquidate
  // health (anything not "healthy").
  const lendingNeedsAttention = (loans ?? []).some(
    (l) =>
      (l.status === "active" || l.status === "pending") &&
      l.health_status != null &&
      l.health_status !== "healthy",
  );

  return (
    <div className="flex flex-col min-h-full bg-slate-950 text-slate-100">
      {/* Header */}
      <div className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-800 bg-slate-950/95 backdrop-blur px-4 py-2">
        <button
          onClick={onBack}
          className="flex items-center gap-0.5 text-sm text-slate-400 hover:text-slate-200 transition-colors"
          title={t(lang, "profileBack")}
        >
          <ChevronLeft size={18} />
          <span>{t(lang, "profileBack")}</span>
        </button>
        <span className="text-sm font-semibold text-slate-200">
          {t(lang, "profileTitle")}
        </span>
        <button
          onClick={onRefresh}
          disabled={balanceLoading}
          className="text-slate-500 hover:text-slate-200 disabled:opacity-50 transition-colors"
          title={t(lang, "profileRefresh")}
        >
          {balanceLoading ? (
            <Spinner />
          ) : (
            <RotateCw size={16} strokeWidth={2.25} />
          )}
        </button>
      </div>

      <TabBar
        activeTab={activeTab}
        onChange={setActiveTab}
        lendingBadge={lendingNeedsAttention}
        lang={lang}
      />

      <div className="flex-1 p-4 space-y-4">
        {activeTab === "account" && (
          <AccountTab
            user={user}
            balance={balance}
            balanceLoading={balanceLoading}
            lang={lang}
            onShowDeposit={() => setShowDeposit(true)}
            onSignOut={onSignOut}
          />
        )}
        {activeTab === "lending" && (
          <LendingTab
            lang={lang}
            loans={loans}
            positions={positions}
            lendingReloadVersion={lendingReloadVersion}
            optimisticBorrowedCtfs={optimisticBorrowedCtfs}
            onLoansLoaded={onLoansLoaded}
            onStartBorrow={onStartBorrow}
            onStartRepay={onStartRepay}
            onOpenHistory={() => setSubView("history")}
          />
        )}
        {activeTab === "insurance" && <InsuranceTab lang={lang} />}
        {activeTab === "leverage" && <LeverageTab lang={lang} />}
      </div>

      {showDeposit && (
        <DepositModal
          address={user.wallet_address}
          lang={lang}
          onClose={() => setShowDeposit(false)}
        />
      )}
    </div>
  );
}

// ============================================================================
// Tab bar
// ============================================================================

function TabBar({
  activeTab,
  onChange,
  lendingBadge,
  lang,
}: {
  activeTab: Tab;
  onChange: (t: Tab) => void;
  /** Red-dot indicator on the lending tab. */
  lendingBadge: boolean;
  lang: Lang;
}) {
  const tabs: { id: Tab; icon: LucideIcon; label: string }[] = [
    { id: "account", icon: User, label: lang === "zh" ? "账户" : "Account" },
    { id: "lending", icon: Wallet, label: lang === "zh" ? "借贷" : "Lending" },
    { id: "insurance", icon: Shield, label: lang === "zh" ? "保险" : "Insurance" },
    { id: "leverage", icon: TrendingUp, label: lang === "zh" ? "杠杆" : "Leverage" },
  ];
  return (
    <div className="sticky top-[42px] z-10 flex items-stretch border-b border-slate-800 bg-slate-950/95 backdrop-blur px-1">
      {tabs.map((tab) => {
        const isActive = tab.id === activeTab;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={
              "relative flex-1 flex flex-col items-center gap-1 py-2.5 text-[11px] transition-all duration-200 outline-none focus-visible:bg-cyan-500/10 " +
              (isActive
                ? "text-cyan-300"
                : "text-slate-500 hover:text-slate-200 hover:bg-slate-900/50")
            }
          >
            <span className="relative leading-none">
              <Icon size={20} strokeWidth={2} />
              {tab.id === "lending" && lendingBadge && (
                <span className="absolute -right-1.5 -top-0.5 h-2 w-2 rounded-full bg-red-500 ring-2 ring-slate-950 animate-pulse" />
              )}
            </span>
            <span className={isActive ? "font-semibold" : ""}>{tab.label}</span>
            {/* Active indicator carries the brand gradient (emerald→cyan).
                Same color appears on every primary CTA across the product so
                users consistently associate the gradient with KPAX. */}
            {isActive && (
              <span className="absolute bottom-0 left-1/2 -translate-x-1/2 h-0.5 w-12 rounded-full bg-gradient-to-r from-emerald-400 to-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.5)]" />
            )}
          </button>
        );
      })}
    </div>
  );
}

// ============================================================================
// Tab 1 · Account
// ============================================================================

function AccountTab({
  user,
  balance,
  balanceLoading,
  lang,
  onShowDeposit,
  onSignOut,
}: {
  user: UserInfo;
  balance: UsdcBalance | null;
  balanceLoading: boolean;
  lang: Lang;
  onShowDeposit: () => void;
  onSignOut: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const loginProvider = detectLoginProvider(user);
  const explorerUrl = `https://polygonscan.com/token/${POLYGON_USDC_E_ADDRESS}?a=${user.wallet_address}`;

  async function copyAddress() {
    try {
      await navigator.clipboard.writeText(user.wallet_address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be denied */
    }
  }

  return (
    <>
      {/* Profile card */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-emerald-500 to-cyan-500 text-white text-xl font-bold shrink-0 shadow-md shadow-cyan-500/20">
            {(user.email || user.wallet_address || "K")[0].toUpperCase()}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-slate-100">
              {user.email || shortAddress(user.wallet_address)}
            </div>
            <div className="text-xs text-slate-500">
              {t(lang, "profileSignedInVia")} ·{" "}
              {t(lang, providerI18nKey(loginProvider))}
            </div>
          </div>
        </div>
      </section>

      {/* Wallet card */}
      <section>
        <SectionHeader label={t(lang, "profileWallet")} />
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-4">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Polygon · USDC.e</span>
            <div className="flex items-center gap-2">
              <button
                onClick={copyAddress}
                className="text-slate-400 hover:text-slate-200 transition-colors"
                title={t(lang, "profileCopyAddress")}
              >
                {copied ? (
                  <Check size={15} className="text-emerald-400" />
                ) : (
                  <Copy size={15} />
                )}
              </button>
              <a
                href={explorerUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-slate-400 hover:text-slate-200 transition-colors"
                title={t(lang, "profileViewOnExplorer")}
              >
                <ExternalLink size={15} />
              </a>
            </div>
          </div>

          <div className="break-all rounded-lg bg-slate-800/50 px-3 py-2 text-xs font-mono text-slate-300">
            {user.wallet_address}
          </div>

          <div className="text-center py-2">
            <div className="text-3xl font-bold text-slate-50">
              {balance === null && balanceLoading ? (
                <Spinner />
              ) : (
                <>$<span>{formatUsdc(balance?.amount ?? 0)}</span></>
              )}
            </div>
            <div className="mt-1 text-xs text-slate-500">USDC.e · Polygon</div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={onShowDeposit}
              className="rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-2 text-sm font-semibold text-white transition-all"
            >
              {t(lang, "profileDeposit")}
            </button>
            <button
              disabled
              title={t(lang, "profileWithdrawSoon")}
              className="rounded-lg border border-slate-700 bg-slate-800/40 px-3 py-2 text-sm font-medium text-slate-500 cursor-not-allowed"
            >
              {t(lang, "profileWithdraw")}
            </button>
          </div>
        </div>
      </section>

      {/* Settings card */}
      <section>
        <SectionHeader label={t(lang, "profileSettings")} />
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 divide-y divide-slate-800">
          <SettingRow
            label={t(lang, "profileTheme")}
            value={t(lang, "profileThemeAuto")}
            dim
          />
          <button
            onClick={onSignOut}
            className="w-full px-4 py-3 text-left text-sm text-red-400 hover:bg-red-500/5 transition-colors"
          >
            {t(lang, "profileSignOut")}
          </button>
        </div>
      </section>
    </>
  );
}

// ============================================================================
// Tab 2 · Lending (live product)
// ============================================================================

function LendingTab({
  lang,
  loans,
  positions,
  lendingReloadVersion,
  optimisticBorrowedCtfs,
  onLoansLoaded,
  onStartBorrow,
  onStartRepay,
  onOpenHistory,
}: {
  lang: Lang;
  loans: LoanItem[] | null;
  positions: LendingPosition[] | null;
  // Forwarded straight through to the embedded AssetsPage:
  lendingReloadVersion: number;
  optimisticBorrowedCtfs: Set<string>;
  onLoansLoaded: (activeLoans: LoanItem[]) => void;
  onStartBorrow: (session: { proxy: string; position: LendingPosition }) => void;
  onStartRepay: (loan: LoanItem) => void;
  onOpenHistory: () => void;
}) {
  const [showHelp, setShowHelp] = useState(false);
  // Derive overview stats from the Profile-level loans + positions snapshot.
  // Uses unique-by-CTF for "borrowed count" so a loan and its (still-present)
  // position don't double-count.
  const activeLoans = (loans ?? []).filter(
    (l) => l.status === "active" || l.status === "pending",
  );
  const repaidLoans = (loans ?? []).filter((l) => l.status === "repaid");
  const borrowedCtfs = new Set(activeLoans.map((l) => l.ctf_token_id));
  const positionCtfs = new Set((positions ?? []).map((p) => p.asset));
  // Treat all known CTFs as "positions" — both the user's free positions and
  // the ones currently pledged as collateral (which Polymarket no longer lists).
  const positionsCount = new Set([...positionCtfs, ...borrowedCtfs]).size;
  const totalDebt = activeLoans.reduce(
    (acc, l) => acc + (l.current_debt_usd ?? l.principal),
    0,
  );
  const totalInterest = repaidLoans.reduce(
    (acc, l) => acc + (l.total_interest_paid ?? 0),
    0,
  );
  const totalBorrowedLifetime = repaidLoans.reduce(
    (acc, l) => acc + l.principal,
    0,
  );
  // Per-loan health status is now surfaced inside each AssetCard's progress
  // bar — no need for a roll-up "worst health" pill on this tab.
  // Lifetime totals (累计借出 / 累计利息 / 已结算笔数) are still shown on the
  // history sub-page; the lending tab itself only carries live state.
  void totalBorrowedLifetime;
  void totalInterest;
  void repaidLoans;

  return (
    <>
      {/* Overview triple. The "?" help button is anchored at the very top-
          right so the user can pop up the lending rules without scrolling
          past the cards. Educational content (APR / LTV caps / forced repay)
          used to live in a bottom card; promoting it to a modal frees the
          tab for the actual asset list. */}
      <section className="relative">
        <button
          onClick={() => setShowHelp(true)}
          aria-label={lang === "zh" ? "了解 KPAX 借贷" : "About KPAX Lending"}
          className="absolute -top-1 right-0 flex h-6 w-6 items-center justify-center rounded-full border border-slate-700 text-slate-400 hover:border-cyan-500/50 hover:text-cyan-300 transition-colors"
        >
          <HelpCircle size={13} />
        </button>
        <div className="grid grid-cols-3 gap-2">
          <Stat value={String(positionsCount)} label={lang === "zh" ? "持仓" : "Positions"} />
          <Stat value={String(activeLoans.length)} label={lang === "zh" ? "借款中" : "Borrowed"} />
          <Stat value={`$${totalDebt.toFixed(2)}`} label={lang === "zh" ? "应还总额" : "Owed"} tone="blue" />
        </div>
      </section>

      {/* Assets — inlined directly here. The "历史 ›" link inside AssetsPage's
          own filter row is the (only) entry to loan history; we no longer
          need a full-width history card on this tab. */}
      <section>
        <AssetsPage
          lang={lang}
          lendingReloadVersion={lendingReloadVersion}
          optimisticBorrowedCtfs={optimisticBorrowedCtfs}
          onLoansLoaded={onLoansLoaded}
          onStartBorrow={onStartBorrow}
          onStartRepay={onStartRepay}
          onViewHistory={onOpenHistory}
          embedded
        />
      </section>

      {showHelp && <LendingHelpModal lang={lang} onClose={() => setShowHelp(false)} />}
    </>
  );
}

/** Modal popped from the lending tab's "?" help icon. Same content the old
 *  bottom "了解 KPAX 借贷" card used to carry, just out of the way until the
 *  user asks for it. */
function LendingHelpModal({
  lang,
  onClose,
}: {
  lang: Lang;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-30 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-base font-semibold text-slate-100">
            {lang === "zh" ? "了解 KPAX 借贷" : "About KPAX Lending"}
          </h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 transition-colors"
            aria-label="close"
          >
            <X size={18} />
          </button>
        </div>
        <div className="space-y-2 text-xs text-slate-300">
          <Bullet>
            {lang === "zh"
              ? "12% APR · 利息按秒线性累计"
              : "12% APR · interest accrues per-second"}
          </Bullet>
          <Bullet>
            {lang === "zh"
              ? "联赛 LTV 上限：60% / 50% / 40%（按联赛分级）"
              : "Per-tier LTV cap: 60% / 50% / 40%"}
          </Bullet>
          <Bullet>
            {lang === "zh"
              ? "比赛开始前 2 小时强制还款"
              : "Forced repay 2h before kickoff"}
          </Bullet>
        </div>
        <div className="mt-4 flex gap-3 text-[11px]">
          <a
            href="https://github.com/kpax-ball"
            target="_blank"
            rel="noopener noreferrer"
            className="text-cyan-400 hover:text-cyan-300 transition-colors"
          >
            {lang === "zh" ? "完整规则 →" : "Full rules →"}
          </a>
          <a
            href="https://polygonscan.com/address/0x0000000000000000000000000000000000000000"
            target="_blank"
            rel="noopener noreferrer"
            className="text-slate-400 hover:text-slate-300 transition-colors"
          >
            {lang === "zh" ? "查看 vault 合约 ↗" : "Vault contract ↗"}
          </a>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Tab 3 · Insurance (coming soon)
// ============================================================================

function InsuranceTab({ lang }: { lang: Lang }) {
  return (
    <ComingSoonTab
      icon={<Shield size={48} className="text-emerald-400" strokeWidth={1.5} />}
      title={lang === "zh" ? "仓位保险" : "Position Insurance"}
      eta={lang === "zh" ? "敬请期待 · 预计 Q2 2026 上线" : "Coming soon · est. Q2 2026"}
      lang={lang}
      bullets={
        lang === "zh"
          ? [
              "抵御市场剧烈波动",
              "保护长期持仓的本金",
              "自动赔付（无需手动 claim）",
            ]
          : [
              "Hedge against sharp market drops",
              "Protect principal on long-held positions",
              "Auto-payout — no manual claim",
            ]
      }
      howItWorks={
        lang === "zh"
          ? "你支付小额保费（持仓价值的 ~1.5%/月）→ 持仓价格暴跌触发阈值时 KPAX 自动赔付差额。不需要手动 claim，按合约条款执行。"
          : "Pay a small premium (~1.5%/month of position value) → KPAX auto-pays the gap if collateral drops past the threshold. No manual claim — fully on-chain."
      }
      persona={
        lang === "zh"
          ? "长期持仓 + 已抵押借款的用户；不愿被强平、希望锁定下行的玩家。"
          : "Long-term holders with active loans; players who want to lock in downside risk."
      }
    />
  );
}

// ============================================================================
// Tab 4 · Leverage (coming soon)
// ============================================================================

function LeverageTab({ lang }: { lang: Lang }) {
  return (
    <ComingSoonTab
      icon={<TrendingUp size={48} className="text-orange-400" strokeWidth={1.75} />}
      title={lang === "zh" ? "倍率杠杆" : "Leverage"}
      eta={lang === "zh" ? "敬请期待 · 预计 Q3 2026 上线" : "Coming soon · est. Q3 2026"}
      lang={lang}
      bullets={
        lang === "zh"
          ? [
              "1 USDC 撬动最高 5 USDC 仓位",
              "多空双向 · 抢市场偏差",
              "自动止损 · 损失上限可设",
            ]
          : [
              "Up to 5× exposure on 1 USDC margin",
              "Long & short — capture market mispricing",
              "Auto stop-loss · capped downside",
            ]
      }
      riskWarning={
        lang === "zh"
          ? "杠杆放大收益的同时放大亏损。5 倍杠杆下，仓位反向波动 20% 即触发清算，本金归零。请勿投入承受能力之外的资金。"
          : "Leverage amplifies losses as much as gains. At 5× leverage, a 20% adverse move wipes out your margin. Never risk more than you can afford to lose."
      }
      howItWorks={
        lang === "zh"
          ? "用 KPAX 借贷池作为流动性，vault 自动管理保证金 + 强平。利息按秒计、与借贷同利率 12% APR。"
          : "Backed by the KPAX lending pool. The vault manages your margin + auto-liquidates if needed. Interest accrues per-second at 12% APR — same as Lending."
      }
    />
  );
}

// ============================================================================
// Shared coming-soon shell
// ============================================================================

function ComingSoonTab({
  icon,
  title,
  eta,
  lang,
  bullets,
  howItWorks,
  persona,
  riskWarning,
}: {
  icon: React.ReactNode;
  title: string;
  eta: string;
  lang: Lang;
  bullets: string[];
  howItWorks: string;
  persona?: string;
  riskWarning?: string;
}) {
  return (
    <>
      <section className="text-center pt-2 pb-1">
        <div className="mb-2 flex justify-center">{icon}</div>
        <h2 className="text-base font-semibold text-slate-100">{title}</h2>
        <div className="mt-1 text-xs text-slate-500">{eta}</div>
      </section>

      <section>
        <SectionHeader
          label={lang === "zh" ? "你能用它做什么" : "What it does"}
        />
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-2 text-xs text-slate-300">
          {bullets.map((b, i) => (
            <Bullet key={i} ok>
              {b}
            </Bullet>
          ))}
        </div>
      </section>

      {riskWarning && (
        <section>
          <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wider text-red-300 flex items-center gap-1.5">
            <AlertTriangle size={12} strokeWidth={2.5} />
            {lang === "zh" ? "风险提示" : "Risk warning"}
          </div>
          <div className="rounded-xl border border-red-500/40 bg-red-500/5 p-4 text-xs text-red-200/90 leading-relaxed">
            {riskWarning}
          </div>
        </section>
      )}

      <section>
        <SectionHeader
          label={lang === "zh" ? "工作原理" : "How it works"}
        />
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400 leading-relaxed">
          {howItWorks}
        </div>
      </section>

      {persona && (
        <section>
          <SectionHeader label={lang === "zh" ? "适合谁" : "Who it's for"} />
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-xs text-slate-400 leading-relaxed">
            {persona}
          </div>
        </section>
      )}
    </>
  );
}

// ============================================================================
// Small UI primitives used by tabs
// ============================================================================

function Stat({
  value,
  label,
  tone,
}: {
  value: string;
  label: string;
  tone?: "blue";
}) {
  // Cyan variant (brand-tinted) draws the eye to the focal stat (debt).
  // Same emerald→cyan signature as primary CTAs, kept low-saturation here
  // so it reads as "highlighted" not "shouting".
  const cls =
    tone === "blue"
      ? "border-cyan-500/30 bg-gradient-to-b from-cyan-500/10 to-cyan-500/0 text-cyan-200"
      : "border-slate-800 bg-slate-900/60 text-slate-50";
  const labelCls = tone === "blue" ? "text-cyan-300/80" : "text-slate-500";
  return (
    <div className={`rounded-xl border p-3 text-center ${cls}`}>
      <div className="text-xl font-bold tabular-nums">{value}</div>
      <div className={`text-[10px] mt-0.5 ${labelCls}`}>{label}</div>
    </div>
  );
}

function Bullet({
  children,
  ok,
}: {
  children: React.ReactNode;
  ok?: boolean;
}) {
  return (
    <div className="flex items-start gap-2">
      <span
        className={
          "shrink-0 mt-0.5 " + (ok ? "text-emerald-400" : "text-slate-600")
        }
      >
        {ok ? <Check size={14} strokeWidth={2.5} /> : "·"}
      </span>
      <span className="flex-1">{children}</span>
    </div>
  );
}

// ---------- subcomponents ----------

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
      {label}
    </div>
  );
}

/** Loan history page — sub-view of Profile, reached via Assets &rarr; "查看借款历史". */
function LendingPage({ lang, onBack }: { lang: Lang; onBack: () => void }) {
  return (
    <div className="flex flex-col min-h-full bg-slate-950 text-slate-100">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-800 bg-slate-950/95 backdrop-blur px-4 py-2">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
        >
          <span className="text-lg">‹</span>
          <span>{t(lang, "profileBack")}</span>
        </button>
        <span className="text-sm font-semibold text-slate-200">
          {lang === "zh" ? "借款历史" : "Loan history"}
        </span>
        <span className="w-12" />
      </div>
      <div className="flex-1 p-4">
        <LendingHistory lang={lang} />
      </div>
    </div>
  );
}

function SettingRow({
  label,
  value,
  dim,
}: {
  label: string;
  value: string;
  dim?: boolean;
}) {
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <div className="text-sm text-slate-200">{label}</div>
      <div className={`text-xs ${dim ? "text-slate-500" : "text-slate-300"}`}>
        {value}
      </div>
    </div>
  );
}

function DepositModal({
  address,
  lang,
  onClose,
}: {
  address: string;
  lang: Lang;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  }

  return (
    <div
      className="fixed inset-0 z-20 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-base font-semibold text-slate-100">
            {t(lang, "profileDepositModalTitle")}
          </h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 text-lg"
          >
            ✕
          </button>
        </div>
        <p className="text-xs text-slate-400 mb-3">
          {t(lang, "profileDepositModalDesc")}
        </p>
        <div className="mb-3 break-all rounded-lg bg-slate-800/70 px-3 py-3 text-xs font-mono text-slate-200">
          {address}
        </div>
        <button
          onClick={copy}
          className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-2.5 text-sm font-semibold text-white transition-all mb-2"
        >
          {copied
            ? t(lang, "profileAddressCopied")
            : t(lang, "profileCopyAddress")}
        </button>
        <div className="rounded-lg border border-yellow-700/40 bg-yellow-500/10 px-3 py-2 text-[11px] text-yellow-200/90">
          {t(lang, "profileDepositModalWarning")}
        </div>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-500 border-t-transparent align-middle" />
  );
}

// ---------- helpers ----------

function shortAddress(addr: string): string {
  if (!addr || addr.length < 10) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

type LoginProvider = "google" | "email" | "wallet" | "unknown";

function detectLoginProvider(user: UserInfo): LoginProvider {
  if (!user.email) return "wallet";
  // Rough guess — Privy signals this in user.google vs user.email but we don't
  // have that raw object in UserInfo. Good-enough UX:
  if (user.email.includes("@gmail.com") || user.email.includes("@googlemail"))
    return "google";
  return "email";
}

function providerI18nKey(p: LoginProvider) {
  switch (p) {
    case "google":
      return "profileSignedInViaGoogle" as const;
    case "email":
      return "profileSignedInViaEmail" as const;
    case "wallet":
      return "profileSignedInViaWallet" as const;
    default:
      return "profileSignedInViaEmail" as const;
  }
}
