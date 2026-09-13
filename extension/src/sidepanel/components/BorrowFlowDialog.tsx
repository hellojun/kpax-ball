import { useEffect, useState } from "react";
import { AlertTriangle, Sparkles, X } from "lucide-react";
import type { Lang } from "@shared/types";
import {
  confirmBorrow,
  fetchLendingConfig,
  fetchPoolBalance,
  fetchRiskAssessment,
  prepareBorrow,
  type LendingPosition,
  type PrepareBorrowResponse,
  type RiskAssessmentResponse,
} from "@shared/lending-api";
import {
  buildApprovalSafeTx,
  buildExecTransactionData,
  buildSafeTypedData,
  isVaultApproved,
  POLYMARKET_CTF,
  POLYGON_CHAIN_ID,
  readSafeNonce,
} from "@shared/safe-approval";
import { createBridgeSigner } from "@shared/wallet-bridge";
import Stepper, { type StepperStep } from "./Stepper";

// Per-tier liquidation thresholds (mirrors backend LEAGUE_TIERS). Used to
// compute the per-slider-amount liquidation price displayed in the risk panel.
const TIER_LIQUIDATION_LTV: Record<number, number> = {
  1: 0.8,
  2: 0.75,
  3: 0.65,
};
const TIER_WARNING_LTV: Record<number, number> = {
  1: 0.7,
  2: 0.65,
  3: 0.55,
};

interface Props {
  proxy: string;
  position: LendingPosition;
  lang: Lang;
  /** EOA the user logged into KPAX with — must match MetaMask's active account. */
  expectedEoa: string;
  onClose: () => void;
  /** Fired the moment the borrow tx is broadcast or confirmed on-chain so the
   *  parent can optimistically mark this CTF as "has active loan" + start a
   *  background refresh, without waiting for the user to dismiss the dialog
   *  AND without depending on Polymarket Data API to have caught up to the
   *  on-chain state (it can lag 10–30s after the collateral transfer). */
  onSettled?: (borrowed: { ctfTokenId: string; principalUsd: number }) => void;
}

/**
 * State machine:
 *   loading            initial approval check
 *   needs-approval     show "Authorize" button → triggers Safe.execTransaction
 *   signing            MetaMask popup #1 (EIP-712)
 *   broadcasting       MetaMask popup #2 (sendTransaction)
 *   approved           approval done; show amount slider → prepare-borrow
 *   borrow-signing     MetaMask popup for openLoan
 *   borrowed           openLoan tx broadcast; show tx hash
 *   error              any failure surface
 */
type Status =
  | { kind: "loading" }
  | { kind: "needs-approval" }
  | { kind: "preparing-approval" }   // user clicked Authorize; waiting for nonce/typed-data
  | { kind: "signing" }
  | { kind: "broadcasting" }
  | { kind: "approved" }
  | { kind: "preparing-borrow" }     // user clicked Borrow; waiting for prepare-borrow
  | { kind: "borrow-signing" }
  | { kind: "borrow-confirming"; txHash: string; loanId: number }
  | { kind: "borrowed"; txHash: string; loanId: number; onchainLoanId: number | null }
  | { kind: "error"; message: string };

export default function BorrowFlowDialog({
  proxy,
  position,
  lang,
  expectedEoa,
  onClose,
  onSettled,
}: Props) {
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [vault, setVault] = useState<string | null>(null);
  const [approvalTx, setApprovalTx] = useState<string | null>(null);
  const [risk, setRisk] = useState<RiskAssessmentResponse | null>(null);
  // Live LP pool capacity in USDC.e. null while loading; falsy fetch failure
  // means we let the contract's InsufficientLiquidity revert speak for itself.
  const [poolBalanceUsd, setPoolBalanceUsd] = useState<number | null>(null);
  // Minimum borrow amount. Kept tight so AI recommendations as low as ~$0.10
  // can be honored instead of being clamped up to a hardcoded floor. The
  // contract itself only requires `principal > 0`.
  const MIN_BORROW = 0.1;
  const [principal, setPrincipal] = useState<number>(
    Math.max(MIN_BORROW, Math.min(position.max_borrowable_usd, 0.5)),
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const config = await fetchLendingConfig();
        if (cancelled) return;
        setVault(config.vault_address);

        // Pull AI risk assessment in parallel — failure is non-fatal, we just
        // fall back to "no AI suggestion" UI.
        fetchRiskAssessment(position.asset)
          .then((r) => {
            if (cancelled) return;
            setRisk(r);
            setPrincipal(Math.max(MIN_BORROW, r.recommended_borrow_usd));
          })
          .catch(() => {
            /* AI hint optional */
          });

        // LP pool capacity — used to clamp the slider so we don't let the
        // user sign a tx that would revert with InsufficientLiquidity. Best-
        // effort: on RPC failure we leave it null and trust the contract.
        fetchPoolBalance()
          .then((p) => {
            if (cancelled) return;
            setPoolBalanceUsd(p.pool_balance_usd);
          })
          .catch(() => {
            /* fall through to contract revert */
          });

        const signer = await createBridgeSigner(expectedEoa);
        const approved = await isVaultApproved(
          signer,
          proxy as `0x${string}`,
          config.vault_address as `0x${string}`,
          POLYMARKET_CTF as `0x${string}`,
        );
        if (cancelled) return;
        setStatus({ kind: approved ? "approved" : "needs-approval" });
      } catch (e) {
        if (cancelled) return;
        setStatus({
          kind: "error",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [proxy, position.asset]);

  async function handleApprove() {
    if (!vault) return;
    setApprovalTx(null);
    setStatus({ kind: "preparing-approval" });
    try {
      // No eth_chainId preflight — that read counts toward MM's per-origin
      // rate budget. If user is on the wrong chain MM rejects the typed-data
      // sign with a clear error which we surface in the catch block.
      const signer = await createBridgeSigner(expectedEoa);
      const already = await isVaultApproved(
        signer,
        proxy as `0x${string}`,
        vault as `0x${string}`,
        POLYMARKET_CTF as `0x${string}`,
      );
      if (already) {
        setStatus({ kind: "approved" });
        return;
      }
      const nonce = await readSafeNonce(signer, proxy as `0x${string}`);
      const safeTx = buildApprovalSafeTx(vault as `0x${string}`, nonce);
      const typedData = buildSafeTypedData(
        proxy as `0x${string}`,
        POLYGON_CHAIN_ID,
        safeTx,
      );

      setStatus({ kind: "signing" });
      const signature = await signer.signTypedData(typedData);

      setStatus({ kind: "broadcasting" });
      const data = buildExecTransactionData(safeTx, signature);
      const hash = await signer.sendTransaction({ to: proxy as `0x${string}`, data });
      setApprovalTx(hash);
      setStatus({ kind: "approved" });
    } catch (e) {
      if (isUserRejection(e)) {
        setStatus({ kind: "needs-approval" });  // back to "Authorize" CTA
      } else {
        setStatus({ kind: "error", message: friendlyError(e, lang) });
      }
    }
  }

  async function handleBorrow() {
    if (!vault) return;
    setStatus({ kind: "preparing-borrow" });
    try {
      // 1. Server-side validation + calldata.
      let prep: PrepareBorrowResponse;
      try {
        prep = await prepareBorrow(position.asset, principal);
      } catch (e) {
        // Backend errors (LTV cap, kickoff window, etc.) come through fetch
        // with a non-2xx; surface the response body directly so the user
        // knows exactly what failed.
        throw new Error(
          e instanceof Error ? e.message : "prepare-borrow failed",
        );
      }

      // 2. Send the openLoan tx.
      setStatus({ kind: "borrow-signing" });
      const signer = await createBridgeSigner(expectedEoa);
      const txHash = await signer.sendTransaction({
        to: prep.to as `0x${string}`,
        data: prep.data as `0x${string}`,
        value: 0n,
      });

      // 3. Wait for it to mine + flip the pending Loan row to active.
      setStatus({ kind: "borrow-confirming", txHash, loanId: prep.loan_id });
      // Notify the parent the moment the tx is broadcast — even before the
      // backend has a chance to confirm — so the BorrowEntry can optimistically
      // hide the "borrow on this position" CTA. Without this, a user who
      // closes the side panel mid-confirmation reopens to a stale UI showing
      // the borrow button despite having an active loan in the DB.
      const borrowedRef = {
        ctfTokenId: position.asset,
        principalUsd: principal,
      };
      onSettled?.(borrowedRef);
      try {
        const confirm = await confirmBorrow(prep.loan_id, txHash);
        if (confirm.status === "active") {
          setStatus({
            kind: "borrowed",
            txHash,
            loanId: prep.loan_id,
            onchainLoanId: confirm.onchain_loan_id,
          });
          // Second notify after confirm so the parent can refresh again now
          // that the backend has flipped the loan row to "active".
          onSettled?.(borrowedRef);
        } else {
          setStatus({
            kind: "error",
            message:
              lang === "zh"
                ? "链上交易失败。请去 Polygonscan 查交易详情。"
                : "Transaction failed on-chain. Check the tx on Polygonscan.",
          });
        }
      } catch (e) {
        // confirm timed out: surface tx hash so user can check manually
        setStatus({
          kind: "borrowed",
          txHash,
          loanId: prep.loan_id,
          onchainLoanId: null,
        });
        onSettled?.(borrowedRef);
      }
    } catch (e) {
      if (isUserRejection(e)) {
        setStatus({ kind: "approved" });  // back to slider + Borrow CTA
      } else {
        setStatus({ kind: "error", message: friendlyError(e, lang) });
      }
    }
  }

  // While the user is mid-flight (signing in MM, broadcasting, waiting for
  // confirmation, or already borrowed and being shown the success state), we
  // must NOT let an accidental backdrop click discard the dialog. They'd lose
  // the loading indicator and have no idea whether their tx is in flight.
  const inFlight =
    status.kind === "preparing-approval" ||
    status.kind === "signing" ||
    status.kind === "broadcasting" ||
    status.kind === "preparing-borrow" ||
    status.kind === "borrow-signing" ||
    status.kind === "borrow-confirming" ||
    status.kind === "borrowed";
  const handleBackdrop = inFlight ? undefined : onClose;

  return (
    <div
      className="fixed inset-0 z-30 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={handleBackdrop}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-base font-semibold text-slate-100">
            {status.kind === "borrowed"
              ? lang === "zh"
                ? "✓ 借款成功"
                : "✓ Loan opened"
              : lang === "zh"
                ? "开始借款"
                : "Start borrowing"}
          </h3>
          <button
            onClick={onClose}
            disabled={
              status.kind === "preparing-approval" ||
              status.kind === "signing" ||
              status.kind === "broadcasting" ||
              status.kind === "preparing-borrow" ||
              status.kind === "borrow-signing" ||
              status.kind === "borrow-confirming"
            }
            className="text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            aria-label="close"
            title={
              status.kind === "borrow-signing" ||
              status.kind === "borrow-confirming"
                ? lang === "zh"
                  ? "交易进行中，请勿关闭"
                  : "Transaction in progress — don't close"
                : undefined
            }
          >
            <X size={18} />
          </button>
        </div>

        {/* Step indicator. Hidden for loading + error + success — they each
            replace it with their own focal element (spinner / error / receipt). */}
        {(() => {
          const steps = deriveSteps(status.kind, lang);
          return steps ? <div className="mb-3"><Stepper steps={steps} /></div> : null;
        })()}

        {/* Position summary */}
        <div className="mb-3 rounded-lg border border-slate-800 bg-slate-800/40 p-3 text-xs text-slate-300 space-y-1">
          <Row k={lang === "zh" ? "持仓" : "Position"} v={position.title ?? "—"} />
          <Row
            k={lang === "zh" ? "份额" : "Shares"}
            v={`${position.size.toFixed(2)}  (~$${position.value_usd.toFixed(2)})`}
          />
          <Row
            k={lang === "zh" ? "可借" : "Borrowable"}
            v={`$${position.max_borrowable_usd.toFixed(2)}`}
          />
          <Row k="proxy" v={shortAddress(proxy)} mono />
        </div>

        {status.kind === "loading" && (
          <p className="text-xs text-slate-400">
            {lang === "zh" ? "正在检查授权..." : "Checking approval..."}
          </p>
        )}

        {status.kind === "needs-approval" && (
          <NeedsApprovalView lang={lang} vault={vault} onApprove={handleApprove} />
        )}

        {status.kind === "preparing-approval" && (
          <SpinnerHint
            text={
              lang === "zh"
                ? "准备授权中... 即将弹出 MetaMask"
                : "Preparing approval... MetaMask will pop up shortly"
            }
          />
        )}

        {status.kind === "preparing-borrow" && (
          <SpinnerHint
            text={
              lang === "zh"
                ? "准备借款中... 即将弹出 MetaMask"
                : "Preparing borrow... MetaMask will pop up shortly"
            }
          />
        )}

        {status.kind === "signing" && (
          <Hint
            text={
              lang === "zh"
                ? "👉 在 MetaMask 里点确认（1/2 · 签名）。如果没看到弹窗，请点浏览器右上角 MetaMask 图标。"
                : "👉 Confirm in MetaMask (1/2 · sign). If no popup, click the MetaMask icon."
            }
          />
        )}

        {status.kind === "broadcasting" && (
          <Hint
            text={
              lang === "zh"
                ? "👉 在 MetaMask 里点确认（2/2 · 广播交易）。"
                : "👉 Confirm in MetaMask (2/2 · broadcast)."
            }
          />
        )}

        {status.kind === "approved" && (
          <ApprovedView
            lang={lang}
            approvalTx={approvalTx}
            position={position}
            risk={risk}
            principal={principal}
            min={MIN_BORROW}
            poolBalanceUsd={poolBalanceUsd}
            onPrincipalChange={setPrincipal}
            onBorrow={handleBorrow}
          />
        )}

        {status.kind === "borrow-signing" && (
          <Hint
            text={
              lang === "zh"
                ? "👉 在 MetaMask 里点确认。USDC.e 会打到你的钱包，可以转去 Polymarket 充值。"
                : "👉 Confirm in MetaMask. USDC.e will land in your wallet — deposit it to Polymarket to trade."
            }
          />
        )}

        {status.kind === "borrow-confirming" && (
          <Hint
            text={
              lang === "zh"
                ? "⏳ 正在确认借款（5-15 秒）..."
                : "⏳ Confirming on-chain (5–15s)..."
            }
          />
        )}

        {status.kind === "borrowed" && (
          <>
            <BorrowedView
              lang={lang}
              txHash={status.txHash}
              loanId={status.loanId}
              onchainLoanId={status.onchainLoanId}
            />
            <button
              onClick={onClose}
              className="mt-3 w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-2.5 text-sm font-semibold text-white transition-all"
            >
              {lang === "zh" ? "完成 · 查看活跃借款" : "Done · View active loan"}
            </button>
          </>
        )}

        {status.kind === "error" && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
            <div className="font-semibold mb-1">
              {lang === "zh" ? "出错了" : "Something went wrong"}
            </div>
            <div className="break-all">{status.message}</div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------- subviews ----------

function NeedsApprovalView({
  lang,
  vault,
  onApprove,
}: {
  lang: Lang;
  vault: string | null;
  onApprove: () => void;
}) {
  return (
    <>
      <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
        {lang === "zh"
          ? "首次借款前，需要让你的 Polymarket Safe 授权 KPAX 合约可以拉取这些抵押 token。这一步只用做一次。"
          : "Before your first loan, your Polymarket Safe must authorize the KPAX contract to pull collateral tokens. One-time setup."}
      </div>
      <div className="mb-3 text-[11px] text-slate-500 break-all">
        vault: <span className="font-mono text-slate-400">{vault}</span>
      </div>
      <button
        onClick={onApprove}
        className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-3 text-sm font-semibold text-white transition-all"
      >
        {lang === "zh" ? "授权 KPAX Vault" : "Authorize KPAX Vault"}
      </button>
      <div className="mt-1.5 text-center text-[11px] text-slate-500">
        {lang === "zh"
          ? "需要 2 次 MetaMask 确认 · 约 30 秒"
          : "2 MetaMask confirmations · ~30 seconds"}
      </div>
    </>
  );
}

function ApprovedView({
  lang,
  approvalTx,
  position,
  risk,
  principal,
  min,
  poolBalanceUsd,
  onPrincipalChange,
  onBorrow,
}: {
  lang: Lang;
  approvalTx: string | null;
  position: LendingPosition;
  risk: RiskAssessmentResponse | null;
  principal: number;
  min: number;
  /** Live LP pool capacity (USDC.e). null while loading or after RPC error —
   *  in which case we fall back to position.max_borrowable_usd alone and
   *  trust the contract's InsufficientLiquidity revert as the backstop. */
  poolBalanceUsd: number | null;
  onPrincipalChange: (n: number) => void;
  onBorrow: () => void;
}) {
  const positionMax = position.max_borrowable_usd;
  // Effective borrow ceiling = min(LTV-derived cap, live pool capacity).
  // `Math.max(..., min)` keeps the slider usable in degenerate cases where
  // poolBalance < min — we still clamp to min so the input isn't NaN, and
  // surface a "pool insufficient" banner (see below).
  const ltvAndPoolCeiling =
    poolBalanceUsd != null
      ? Math.min(positionMax, poolBalanceUsd)
      : positionMax;
  const max = Math.max(ltvAndPoolCeiling, min);
  // Pool drained below the floor — disable borrow entirely with a clear msg.
  const poolInsufficient =
    poolBalanceUsd != null && poolBalanceUsd < min;
  // User's slider value exceeds live pool — happens when the pool fetch lands
  // *after* the user already nudged the slider, or when the principal seed
  // (AI-recommended) outran the pool. Surface as a soft warning + disable
  // borrow; principal is auto-clamped on a delay below.
  const overPool =
    poolBalanceUsd != null && principal > poolBalanceUsd + 0.005;

  // If the pool capacity arrives lower than what the user has selected,
  // auto-clamp once. Without this, the slider visually stays at the old
  // position and the borrow button silently disables.
  useEffect(() => {
    if (poolBalanceUsd == null) return;
    if (principal > poolBalanceUsd) {
      onPrincipalChange(Math.max(min, Math.min(poolBalanceUsd, principal)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poolBalanceUsd]);
  const tier = position.league_tier ?? risk?.league_tier ?? 1;
  const liqLtv = TIER_LIQUIDATION_LTV[tier] ?? 0.8;
  const warnLtv = TIER_WARNING_LTV[tier] ?? 0.7;

  const currentLtv = position.value_usd > 0 ? principal / position.value_usd : 0;

  // Liquidation price = principal / (shares * liq_ltv). When the CTF price
  // falls to this level, LTV hits the liquidation threshold. We surface the
  // *distance* from current price so users see the safety buffer in $ terms.
  const liqPrice =
    position.size > 0 ? principal / (position.size * liqLtv) : 0;
  const dropToLiq = Math.max(0, position.current_price - liqPrice);
  const dropToLiqPct =
    position.current_price > 0 ? dropToLiq / position.current_price : 0;

  const overRecommended =
    risk !== null && principal > risk.recommended_borrow_usd + 0.005;
  const overWarning = currentLtv >= warnLtv;

  // 30-day repay estimate (12% APR · simple interest).
  const apr = 0.12;
  const interest30d = principal * apr * (30 / 365);
  const total30d = principal + interest30d;

  // LTV chip color tracks the same warn/liq thresholds as the post-borrow
  // LoanCard, so users get a consistent mental model across borrow/active.
  const ltvTone =
    currentLtv >= liqLtv
      ? { bg: "bg-red-500/10 border-red-500/40 text-red-300", label: lang === "zh" ? "强平" : "Liq" }
      : currentLtv >= warnLtv
        ? { bg: "bg-amber-500/10 border-amber-500/40 text-amber-300", label: lang === "zh" ? "警戒" : "Warn" }
        : currentLtv >= warnLtv * 0.8
          ? { bg: "bg-blue-500/10 border-blue-500/40 text-blue-300", label: lang === "zh" ? "留意" : "Caution" }
          : { bg: "bg-emerald-500/10 border-emerald-500/40 text-emerald-300", label: lang === "zh" ? "安全" : "Safe" };

  return (
    <>
      {approvalTx && (
        <div className="mb-3 rounded-lg border border-green-500/30 bg-green-500/10 p-2 text-[11px] text-green-300/90">
          {lang === "zh" ? "✓ 授权已完成 · " : "✓ Authorized · "}
          <a
            href={`https://polygonscan.com/tx/${approvalTx}`}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono underline hover:text-green-200"
          >
            {shortHash(approvalTx)}
          </a>
        </div>
      )}

      {/* Always-expanded risk panel: 3 metric tiles + AI suggestion bar. */}
      <RiskCard
        lang={lang}
        risk={risk}
        liqPrice={liqPrice}
        currentPrice={position.current_price}
        dropToLiqPct={dropToLiqPct}
        onUseRecommended={
          risk
            ? () =>
                onPrincipalChange(
                  Math.max(min, Math.min(max, risk.recommended_borrow_usd)),
                )
            : null
        }
      />

      {/* Slider with live LTV chip + 4-anchor risk scale below. */}
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-xs text-slate-400">
          {lang === "zh" ? "借款金额" : "Borrow amount"}
        </span>
        <span className="flex items-baseline gap-2 text-[10px] text-slate-500">
          {poolBalanceUsd != null && (
            <span title={lang === "zh" ? "资金池可借总额" : "Live LP pool capacity"}>
              {lang === "zh" ? "资金池" : "Pool"} ${poolBalanceUsd.toFixed(2)}
            </span>
          )}
          <span>
            {lang === "zh" ? "上限" : "max"} ${max.toFixed(2)}
          </span>
        </span>
      </div>
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-2xl font-bold text-slate-50 tabular-nums">
          ${principal.toFixed(2)}
        </span>
        <span className={`rounded-full border px-2 py-0.5 text-[10px] ${ltvTone.bg}`}>
          LTV {(currentLtv * 100).toFixed(0)}% · {ltvTone.label}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={0.01}
        value={principal}
        onChange={(e) => onPrincipalChange(parseFloat(e.target.value))}
        className="w-full accent-blue-500"
      />
      <SliderRiskScale
        lang={lang}
        min={min}
        max={max}
        recommended={risk?.recommended_borrow_usd ?? null}
        warnAt={position.value_usd * warnLtv}
        liqAt={position.value_usd * liqLtv}
      />

      {overRecommended && !overWarning && (
        <div className="mb-3 mt-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-2 text-[11px] text-amber-200">
          {lang === "zh"
            ? `⚠️ 超过 AI 建议的 $${risk!.recommended_borrow_usd.toFixed(2)}（${(
                risk!.recommended_ltv * 100
              ).toFixed(0)}% LTV）。强平风险上升，但仍在 ${(
                risk!.max_ltv * 100
              ).toFixed(0)}% 联赛上限内。`
            : `⚠️ Above AI's recommended $${risk!.recommended_borrow_usd.toFixed(
                2,
              )}. Still under the ${(risk!.max_ltv * 100).toFixed(
                0,
              )}% league cap, but liquidation risk is higher.`}
        </div>
      )}
      {overWarning && (
        <div className="mb-3 mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-[11px] text-amber-200">
          {lang === "zh"
            ? `⚠️ LTV 已超过 ${(warnLtv * 100).toFixed(0)}% 警戒线。再涨少量就会触发强平 — 抵押价格只需跌到 $${liqPrice.toFixed(3)} (现 $${position.current_price.toFixed(3)})。`
            : `⚠️ LTV is past the ${(warnLtv * 100).toFixed(0)}% warning. Liquidation triggers if collateral drops to $${liqPrice.toFixed(3)} (now $${position.current_price.toFixed(3)}).`}
        </div>
      )}

      {poolInsufficient && (
        <div className="mb-3 mt-2 rounded-lg border border-red-500/40 bg-red-500/10 p-2 text-[11px] text-red-200">
          {lang === "zh"
            ? `资金池余额仅 $${poolBalanceUsd!.toFixed(2)}，低于最低借款 $${min.toFixed(2)}。请稍后再试或联系管理员补充流动性。`
            : `Pool balance is $${poolBalanceUsd!.toFixed(2)}, below the $${min.toFixed(2)} minimum. Try again later or ask the admin to top up.`}
        </div>
      )}
      {!poolInsufficient && overPool && (
        <div className="mb-3 mt-2 rounded-lg border border-red-500/40 bg-red-500/10 p-2 text-[11px] text-red-200">
          {lang === "zh"
            ? `⚠️ 借款金额超过资金池可借 $${poolBalanceUsd!.toFixed(2)}。链上会以 InsufficientLiquidity 拒绝。`
            : `⚠️ Above pool capacity ($${poolBalanceUsd!.toFixed(2)}). The contract would revert with InsufficientLiquidity.`}
        </div>
      )}

      {/* "You borrow → you repay" equation. Replaces the older APR + daily-
          interest grid which forced users to do mental math. */}
      <div className="mt-3 mb-3 rounded-xl border border-slate-700/50 bg-gradient-to-b from-slate-800/80 to-slate-800/40 p-3 text-xs space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-slate-400">
            {lang === "zh" ? "你借出" : "You borrow"}
          </span>
          <span className="font-semibold text-slate-100 tabular-nums">
            ${principal.toFixed(2)} USDC.e
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-slate-400">
            {lang === "zh" ? "30 天后约还" : "Repay in 30 days"}
          </span>
          <span className="font-semibold text-slate-100 tabular-nums">
            ${total30d.toFixed(2)}{" "}
            <span className="text-slate-500 font-normal">
              ({lang === "zh" ? "利息" : "interest"} ${interest30d.toFixed(2)})
            </span>
          </span>
        </div>
        <div className="border-t border-slate-700/60 pt-1.5 text-[11px] text-slate-500">
          {lang === "zh"
            ? "年化 12% · 利息按秒线性累计 · 随时可提前还"
            : "12% APR · interest accrues per-second · repay anytime"}
        </div>
      </div>

      <button
        onClick={onBorrow}
        disabled={principal <= 0 || poolInsufficient || overPool}
        className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 disabled:bg-none disabled:bg-slate-700 disabled:text-slate-500 disabled:shadow-none px-3 py-3 text-sm font-semibold text-white transition-all"
      >
        {lang === "zh"
          ? `借出 $${principal.toFixed(2)} USDC.e`
          : `Borrow $${principal.toFixed(2)} USDC.e`}
      </button>
      <div className="mt-1.5 text-center text-[11px] text-slate-500">
        {lang === "zh"
          ? "需要 1 次 MetaMask 确认 · 约 10 秒"
          : "1 MetaMask confirmation · ~10 seconds"}
      </div>
    </>
  );
}

/** Color-coded scale below the borrow slider. Anchors at $0 / AI suggestion /
 *  warning LTV / liquidation LTV give the user a glance-able sense of "where
 *  on the safety spectrum am I dragging?" without needing them to do math. */
function SliderRiskScale({
  lang,
  min,
  max,
  recommended,
  warnAt,
  liqAt,
}: {
  lang: Lang;
  min: number;
  max: number;
  recommended: number | null;
  warnAt: number;
  liqAt: number;
}) {
  return (
    <div className="mt-1 flex justify-between text-[9px] text-slate-500 px-0.5">
      <span>${min.toFixed(2)}</span>
      {recommended != null && recommended < max && (
        <span className="text-emerald-400">
          {lang === "zh" ? "建议" : "rec"} ${recommended.toFixed(2)}
        </span>
      )}
      {warnAt < max && (
        <span className="text-amber-400">
          {lang === "zh" ? "警告" : "warn"} ${warnAt.toFixed(2)}
        </span>
      )}
      <span className="text-red-400">
        {lang === "zh" ? "强平" : "liq"} ${liqAt.toFixed(2)}
      </span>
    </div>
  );
}

/** Always-expanded risk panel with three metric tiles + AI suggestion bar.
 *
 *  Lives ABOVE the slider so users see the safety landscape before they pick a
 *  number. The "details" footer is still collapsible for users who want the
 *  prose reasoning + key-risk bullets. */
function RiskCard({
  lang,
  risk,
  liqPrice,
  currentPrice,
  dropToLiqPct,
  onUseRecommended,
}: {
  lang: Lang;
  risk: RiskAssessmentResponse | null;
  liqPrice: number;
  currentPrice: number;
  dropToLiqPct: number;
  onUseRecommended: (() => void) | null;
}) {
  const [open, setOpen] = useState(false);
  const scoreColor =
    risk == null
      ? "text-slate-400"
      : risk.risk_score <= 3
        ? "text-emerald-400"
        : risk.risk_score <= 6
          ? "text-amber-400"
          : "text-red-400";

  // Color the "distance to liq" tile by the same pct buckets the slider
  // chip uses, so they reinforce each other visually.
  const dropColor =
    dropToLiqPct >= 0.3
      ? "text-emerald-300"
      : dropToLiqPct >= 0.15
        ? "text-blue-300"
        : dropToLiqPct >= 0.05
          ? "text-amber-300"
          : "text-red-300";

  return (
    <div className="mb-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 text-xs space-y-3">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-semibold text-amber-200">
          <AlertTriangle size={13} strokeWidth={2.25} />
          {lang === "zh" ? "风险预览" : "Risk preview"}
        </span>
        <span className="text-[10px] text-slate-400">
          {lang === "zh" ? "基于当前价" : "at current price"} $
          {currentPrice.toFixed(3)}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg border border-slate-800/80 bg-gradient-to-b from-slate-900/80 to-slate-900/40 p-2">
          <div className="text-[10px] text-slate-400">
            {lang === "zh" ? "强平价" : "Liq price"}
          </div>
          <div className="text-sm font-bold text-amber-300 tabular-nums">
            ${liqPrice.toFixed(3)}
          </div>
        </div>
        <div className="rounded-lg border border-slate-800/80 bg-gradient-to-b from-slate-900/80 to-slate-900/40 p-2">
          <div className="text-[10px] text-slate-400">
            {lang === "zh" ? "距离强平" : "To liq"}
          </div>
          <div className={`text-sm font-bold ${dropColor} tabular-nums`}>
            -{(dropToLiqPct * 100).toFixed(0)}%
          </div>
        </div>
        <div className="rounded-lg border border-slate-800/80 bg-gradient-to-b from-slate-900/80 to-slate-900/40 p-2">
          <div className="text-[10px] text-slate-400">
            {lang === "zh" ? "风险评分" : "Risk"}
          </div>
          <div className={`text-sm font-bold ${scoreColor} tabular-nums`}>
            {risk ? `${risk.risk_score} / 10` : "—"}
          </div>
        </div>
      </div>

      {risk && (
        <div className="flex items-center gap-2 rounded-lg border border-blue-500/30 bg-gradient-to-br from-blue-500/15 to-blue-500/5 px-2.5 py-1.5">
          <Sparkles size={14} className="text-blue-300 shrink-0" strokeWidth={2.25} />
          <div className="flex-1 text-[11px] text-blue-200">
            {lang === "zh" ? "AI 建议借" : "AI recommends"}{" "}
            <span className="font-semibold text-blue-100">
              ${risk.recommended_borrow_usd.toFixed(2)}
            </span>{" "}
            <span className="text-blue-300/70">
              ({(risk.recommended_ltv * 100).toFixed(0)}% LTV ·{" "}
              {lang === "zh" ? "强平概率" : "liq prob"}{" "}
              {(risk.liquidation_probability_estimate * 100).toFixed(1)}%)
            </span>
          </div>
          {onUseRecommended && (
            <button
              onClick={onUseRecommended}
              className="rounded border border-blue-400/40 bg-blue-500/20 px-2 py-0.5 text-[10px] text-blue-200 hover:bg-blue-500/30 shrink-0"
            >
              {lang === "zh" ? "采用" : "Use"}
            </button>
          )}
        </div>
      )}

      {risk && (
        <button
          onClick={() => setOpen(!open)}
          className="text-[11px] text-slate-500 hover:text-slate-300"
        >
          {open
            ? lang === "zh"
              ? "收起 ▴"
              : "Hide ▴"
            : lang === "zh"
              ? "查看理由 ▾"
              : "Why ▾"}
        </button>
      )}
      {open && risk && (
        <div className="space-y-2 border-t border-slate-700/50 pt-2 text-[11px] text-slate-400">
          <div>{risk.risk_reasoning}</div>
          {risk.key_risks.length > 0 && (
            <ul className="space-y-1">
              {risk.key_risks.map((r, i) => (
                <li key={i}>
                  · <span className="text-slate-300">{r.factor}</span>{" "}
                  <span className="text-slate-500">— {r.impact}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function BorrowedView({
  lang,
  txHash,
  loanId,
  onchainLoanId,
}: {
  lang: Lang;
  txHash: string;
  loanId: number;
  onchainLoanId: number | null;
}) {
  const confirmed = onchainLoanId !== null;
  return (
    <div className="rounded-lg border border-green-500/30 bg-green-500/10 p-3 text-xs text-green-300 space-y-1">
      <div className="font-semibold">
        {confirmed
          ? lang === "zh"
            ? "✓ 借款已上链！"
            : "✓ Loan confirmed on-chain!"
          : lang === "zh"
            ? "⏳ 借款已广播（链上确认仍在进行中）"
            : "⏳ Loan broadcast (on-chain confirmation pending)"}
      </div>
      <div className="text-[11px] text-green-200/80">
        loan #{loanId}
        {confirmed ? ` · onchain #${onchainLoanId}` : ""} ·{" "}
        <a
          href={`https://polygonscan.com/tx/${txHash}`}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono underline hover:text-green-200"
        >
          {shortHash(txHash)}
        </a>
      </div>
      <div className="pt-1 text-[11px] text-slate-400">
        {lang === "zh"
          ? "USDC.e 已到你的 EOA。要在 Polymarket 上交易？打开 polymarket.com → Deposit → 转过去即可。下一步：仪表盘里的实时 LTV 监控（敬请期待）。"
          : "USDC.e is in your EOA. To trade on Polymarket: open polymarket.com → Deposit → transfer it over. Next: real-time LTV dashboard (coming)."}
      </div>
    </div>
  );
}

function Hint({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 text-xs text-blue-200">
      {text}
    </div>
  );
}

function SpinnerHint({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 text-xs text-blue-200">
      <span className="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-blue-400 border-t-transparent" />
      <span>{text}</span>
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-slate-500 shrink-0">{k}</span>
      <span className={`text-right truncate ${mono ? "font-mono" : ""}`}>{v}</span>
    </div>
  );
}

function shortAddress(a: string) {
  return a.length > 14 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

function shortHash(h: string) {
  return h.length > 14 ? `${h.slice(0, 10)}…${h.slice(-8)}` : h;
}

function isUserRejection(e: unknown): boolean {
  const code = (e as { code?: number })?.code;
  if (code === 4001) return true;
  const msg = (e instanceof Error ? e.message : String(e)).toLowerCase();
  return msg.includes("user denied") || msg.includes("user rejected");
}

/** Map the borrow status machine to a 3-step indicator. Returns null for
 *  loading / error / borrowed states which each have their own focal UI. */
function deriveSteps(kind: Status["kind"], lang: Lang): StepperStep[] | null {
  const labels =
    lang === "zh"
      ? ["授权 Vault", "设定金额", "确认借款"]
      : ["Authorize", "Set amount", "Confirm"];
  switch (kind) {
    case "loading":
    case "error":
    case "borrowed":
      return null;
    case "needs-approval":
    case "preparing-approval":
    case "signing":
    case "broadcasting":
      return [
        { label: labels[0], state: "current" },
        { label: labels[1], state: "upcoming" },
        { label: labels[2], state: "upcoming" },
      ];
    case "approved":
    case "preparing-borrow":
      return [
        { label: labels[0], state: "done" },
        { label: labels[1], state: "current" },
        { label: labels[2], state: "upcoming" },
      ];
    case "borrow-signing":
    case "borrow-confirming":
      return [
        { label: labels[0], state: "done" },
        { label: labels[1], state: "done" },
        { label: labels[2], state: "current" },
      ];
  }
}

function friendlyError(e: unknown, lang: Lang): string {
  console.error("[KPAX BorrowFlow] raw error:", e);
  const raw = e instanceof Error ? e.message : String(e);
  const lower = raw.toLowerCase();
  if (lower.includes("nonce too low")) {
    return lang === "zh"
      ? "MetaMask 的 nonce 不同步（可能你之前从别处发了一笔交易）。关掉所有 MM 弹窗，重试一次即可。"
      : "MetaMask nonce out of sync. Close any pending MM popup and retry.";
  }
  if (lower.includes("rate limit")) {
    return lang === "zh"
      ? "MetaMask 内部限流（已自动重试 3 次未成功）。建议：1) 关闭 OKX / Phantom / Backpack 等其它钱包扩展（它们和 MM 抢同一个页面的 RPC 通道）；2) MetaMask 设置 → 安全和隐私 → 关掉 Blockaid 安全提醒（首要怀疑对象）；3) 打开 MetaMask → 锁定再解锁可立即重置。"
      : "MetaMask rate-limited (auto-retried 3× and still failing). Try: 1) disable OKX / Phantom / Backpack — they share the same page RPC channel; 2) MetaMask Settings → Security & Privacy → disable Blockaid alerts (most likely culprit); 3) lock+unlock MetaMask to reset immediately.";
  }
  // Backend's kickoff-window 400 — should be unreachable now that the picker
  // pre-flight blocks <24h positions, but keep as a safety net for the race
  // where the user opened the dialog just before the boundary tipped.
  if (lower.includes("match starts in")) {
    return lang === "zh"
      ? "比赛 24 小时内开赛，已停止借款。请刷新看一下其它持仓。"
      : "Match starts within 24h — borrowing is closed. Refresh to see your other positions.";
  }
  return raw;
}
