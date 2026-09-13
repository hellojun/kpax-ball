import { useEffect, useState } from "react";
import { RotateCcw, X } from "lucide-react";
import type { Lang } from "@shared/types";
import {
  confirmRepay,
  prepareRepay,
  type LoanItem,
  type PrepareRepayResponse,
} from "@shared/lending-api";
import { polygonReadRpc } from "@shared/wallet";
import { createBridgeSigner } from "@shared/wallet-bridge";
import { encodeFunctionData, type Hex } from "viem";
import Stepper, { type StepperStep } from "./Stepper";

interface Props {
  loan: LoanItem;
  lang: Lang;
  /** EOA the user logged into KPAX with — must match MetaMask's active account. */
  expectedEoa: string;
  onClose: () => void;
  onRepaid: () => void;
}

/**
 * V4 repay flow:
 *   1. usdc.approve(vault, totalDebt + 1%)   — only if current allowance is short
 *   2. vault.repay(onchainLoanId)
 *
 * Both calls hit MetaMask via `eth_sendTransaction`. We MUST wait for the
 * approve to mine before broadcasting repay (otherwise repay races allowance).
 */
type Status =
  | { kind: "loading" }
  | { kind: "ready"; needApproval: boolean }
  | { kind: "submitting" }
  | { kind: "approving" }
  | { kind: "approve-mining"; txHash: Hex }
  | { kind: "repay-signing" }
  | { kind: "repay-mining"; txHash: Hex }
  | { kind: "done"; txHash: Hex; interestPaidUsd: number | null }
  | { kind: "error"; message: string };

const ERC20_ALLOWANCE_ABI = [
  {
    name: "allowance",
    type: "function",
    stateMutability: "view",
    inputs: [
      { name: "owner", type: "address" },
      { name: "spender", type: "address" },
    ],
    outputs: [{ name: "", type: "uint256" }],
  },
] as const;

const ERC20_APPROVE_ABI = [
  {
    name: "approve",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [{ name: "", type: "bool" }],
  },
] as const;

export default function RepayModal({
  loan,
  lang,
  expectedEoa,
  onClose,
  onRepaid,
}: Props) {
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [prep, setPrep] = useState<PrepareRepayResponse | null>(null);
  const [debtBuf, setDebtBuf] = useState<bigint>(0n);
  const [flowNeedsApproval, setFlowNeedsApproval] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await prepareRepay(loan.loan_id);
        if (cancelled) return;
        setPrep(p);

        const totalDebt = BigInt(p.total_debt_base_units);
        // Approve buffer: debt + 1% to absorb a few seconds of interest accrual
        // between the approve and repay txs.
        const buffer = (totalDebt * 1n) / 100n;
        const approveTarget = totalDebt + (buffer === 0n ? 1n : buffer);
        setDebtBuf(approveTarget);

        const allowanceData = encodeFunctionData({
          abi: ERC20_ALLOWANCE_ABI,
          functionName: "allowance",
          args: [
            expectedEoa as `0x${string}`,
            p.vault_address as `0x${string}`,
          ],
        });
        const result = await polygonReadRpc<Hex>("eth_call", [
          { to: p.usdc_address, data: allowanceData },
          "latest",
        ]);
        const allowance = BigInt(result || "0x0");

        if (cancelled) return;
        setStatus({ kind: "ready", needApproval: allowance < totalDebt });
      } catch (e) {
        if (cancelled) return;
        setStatus({
          kind: "error",
          message: friendly(e, lang),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loan.loan_id]);

  async function handleRepay() {
    if (!prep) return;
    const needApproval = status.kind === "ready" && status.needApproval;
    setFlowNeedsApproval(needApproval);
    setStatus({ kind: "submitting" });
    try {
      // Re-validate loan status server-side. The modal's initial
      // prepareRepay (in useEffect) ran when the loan was active, but
      // liquidation could have started in another tab / via admin since.
      // Backend returns 400 "loan is liquidating, not active" — surface
      // that as a clean error instead of letting MetaMask choke on a
      // would-revert tx. (Recompute debtBuf from the fresh quote so we
      // don't re-approve a stale total.)
      const fresh = await prepareRepay(loan.loan_id);
      setPrep(fresh);
      const freshDebt = BigInt(fresh.total_debt_base_units);
      const buffer = (freshDebt * 1n) / 100n;
      const freshDebtBuf = freshDebt + (buffer === 0n ? 1n : buffer);
      setDebtBuf(freshDebtBuf);

      const signer = await createBridgeSigner(expectedEoa);

      if (needApproval) {
        setStatus({ kind: "approving" });
        const data = encodeFunctionData({
          abi: ERC20_APPROVE_ABI,
          functionName: "approve",
          args: [prep.vault_address as `0x${string}`, debtBuf],
        });
        const approveHash = await signer.sendTransaction({
          to: prep.usdc_address as `0x${string}`,
          data,
        });
        setStatus({ kind: "approve-mining", txHash: approveHash });
        await waitForReceipt(approveHash);
      }

      setStatus({ kind: "repay-signing" });
      const txHash = await signer.sendTransaction({
        to: prep.repay_to as `0x${string}`,
        data: prep.repay_data as `0x${string}`,
      });

      setStatus({ kind: "repay-mining", txHash });
      try {
        const c = await confirmRepay(loan.loan_id, txHash);
        if (c.status === "repaid") {
          setStatus({
            kind: "done",
            txHash,
            interestPaidUsd: c.interest_paid_usd,
          });
          onRepaid();
        } else {
          setStatus({
            kind: "error",
            message:
              lang === "zh"
                ? "链上交易失败。请去 Polygonscan 查交易详情。"
                : "Transaction failed on-chain. Check Polygonscan.",
          });
        }
      } catch {
        setStatus({ kind: "done", txHash, interestPaidUsd: null });
        onRepaid();
      }
    } catch (e) {
      if (isUserRejection(e)) {
        setStatus({ kind: "ready", needApproval });
      } else {
        setStatus({ kind: "error", message: friendly(e, lang) });
      }
    }
  }

  const inFlight =
    status.kind === "submitting" ||
    status.kind === "approving" ||
    status.kind === "approve-mining" ||
    status.kind === "repay-signing" ||
    status.kind === "repay-mining" ||
    status.kind === "done";
  const handleBackdrop = inFlight ? undefined : onClose;
  const closeDisabled =
    status.kind === "submitting" ||
    status.kind === "approving" ||
    status.kind === "approve-mining" ||
    status.kind === "repay-signing" ||
    status.kind === "repay-mining";

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
            {status.kind === "done"
              ? lang === "zh" ? "✓ 还款成功" : "✓ Loan repaid"
              : lang === "zh" ? "还款" : "Repay loan"}
          </h3>
          <button
            onClick={onClose}
            disabled={closeDisabled}
            className="text-slate-400 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            aria-label="close"
            title={
              closeDisabled
                ? lang === "zh"
                  ? "交易进行中，请勿关闭"
                  : "Transaction in progress — don't close"
                : undefined
            }
          >
            <X size={18} />
          </button>
        </div>

        {(() => {
          const needApproval =
            status.kind === "ready" ? status.needApproval : flowNeedsApproval;
          const steps = deriveRepaySteps(status.kind, needApproval, lang);
          return steps ? <div className="mb-3"><Stepper steps={steps} /></div> : null;
        })()}

        <div className="mb-3 rounded-lg border border-slate-800 bg-slate-800/40 px-3 py-2 text-[11px] text-slate-400 flex items-center justify-between gap-2">
          <span className="truncate">
            {loan.market_slug || (lang === "zh" ? "未命名比赛" : "untitled market")}
          </span>
          <span className="text-slate-500 shrink-0">Loan #{loan.loan_id}</span>
        </div>

        {status.kind === "loading" && (
          <p className="text-xs text-slate-400">
            {lang === "zh" ? "正在读取应还金额..." : "Reading debt amount..."}
          </p>
        )}

        {status.kind === "ready" && prep && (
          <>
            <div className="mb-3 rounded-xl border border-blue-500/30 bg-gradient-to-b from-blue-500/15 via-blue-500/5 to-slate-900/0 p-5 text-center shadow-inner shadow-blue-500/5">
              <div className="text-[11px] text-slate-400 mb-1 uppercase tracking-wider">
                {lang === "zh" ? "应还总额" : "Total to repay"}
              </div>
              <div className="text-4xl font-bold text-slate-50 tabular-nums leading-tight">
                ${prep.total_debt_usd.toFixed(4)}
              </div>
              <div className="mt-2 text-[11px] text-slate-400 tabular-nums">
                {lang === "zh" ? "本金" : "Principal"}{" "}
                <span className="text-slate-200">${prep.principal_usd.toFixed(2)}</span>
                <span className="text-slate-700"> · </span>
                {lang === "zh" ? "利息" : "Interest"}{" "}
                <span className="text-slate-200">${prep.interest_usd.toFixed(4)}</span>
              </div>
            </div>

            <button
              onClick={handleRepay}
              className="w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-3 text-sm font-semibold text-white transition-all"
            >
              {lang === "zh"
                ? `还款 $${prep.total_debt_usd.toFixed(4)}`
                : `Repay $${prep.total_debt_usd.toFixed(4)}`}
            </button>
            <div className="mt-1.5 text-center text-[11px] text-slate-500">
              {status.needApproval
                ? lang === "zh"
                  ? "需要 2 次 MetaMask 确认 · 约 20 秒"
                  : "2 MetaMask confirmations · ~20 seconds"
                : lang === "zh"
                  ? "需要 1 次 MetaMask 确认 · 约 10 秒"
                  : "1 MetaMask confirmation · ~10 seconds"}
            </div>

            <div className="mt-3 flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-800/40 p-3 text-[11px] text-slate-400 leading-relaxed">
              <RotateCcw size={13} strokeWidth={2.25} className="shrink-0 mt-0.5 text-emerald-400" />
              <span>
                {lang === "zh"
                  ? "还款后，你抵押的 CTF 立即退回 Polymarket Safe，可继续交易。"
                  : "Once repaid, your CTF collateral returns to your Polymarket Safe immediately."}
              </span>
            </div>
          </>
        )}

        {status.kind === "submitting" && (
          <SpinnerHint
            text={
              lang === "zh"
                ? "提交中... 即将弹出 MetaMask"
                : "Submitting... MetaMask will pop up shortly"
            }
          />
        )}

        {status.kind === "approving" && (
          <Hint
            text={
              lang === "zh"
                ? "👉 在 MetaMask 里点确认（1/2 · USDC.e 授权）"
                : "👉 Confirm in MetaMask (1/2 · USDC.e approval)"
            }
          />
        )}

        {status.kind === "approve-mining" && (
          <Hint
            text={
              lang === "zh"
                ? "⏳ 等 USDC.e 授权上链（~2-5 秒），然后会自动弹第 2 笔"
                : "⏳ Waiting for USDC.e approval to mine (~2-5s), then MetaMask will prompt again"
            }
          />
        )}

        {status.kind === "repay-signing" && (
          <Hint
            text={
              lang === "zh"
                ? `👉 在 MetaMask 里点确认（${flowNeedsApproval ? "2/2 · " : ""}还款）`
                : `👉 Confirm in MetaMask (${flowNeedsApproval ? "2/2 · " : ""}repay)`
            }
          />
        )}

        {status.kind === "repay-mining" && (
          <Hint
            text={
              lang === "zh"
                ? "⏳ 正在确认还款..."
                : "⏳ Confirming on-chain..."
            }
          />
        )}

        {status.kind === "done" && (
          <>
            <div className="rounded-lg border border-green-500/30 bg-green-500/10 p-3 text-xs text-green-300 space-y-1">
              <div className="font-semibold">
                {lang === "zh" ? "✓ 还款完成！" : "✓ Loan repaid!"}
              </div>
              {status.interestPaidUsd !== null && (
                <div className="text-[11px] text-green-200/80">
                  {lang === "zh" ? "实付利息" : "Interest paid"} $
                  {status.interestPaidUsd.toFixed(6)}
                </div>
              )}
              <div className="text-[11px] text-green-200/80">
                <a
                  href={`https://polygonscan.com/tx/${status.txHash}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono underline hover:text-green-200"
                >
                  {shortHash(status.txHash)}
                </a>
              </div>
              <div className="pt-1 text-[11px] text-slate-400">
                {lang === "zh"
                  ? "抵押的 CTF 已经退回到你的 Polymarket Safe。"
                  : "Your CTF collateral is back in your Polymarket Safe."}
              </div>
            </div>
            <button
              onClick={onClose}
              className="mt-3 w-full rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-3 py-2.5 text-sm font-semibold text-white transition-all"
            >
              {lang === "zh" ? "完成" : "Done"}
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

function shortHash(h: string) {
  return h.length > 14 ? `${h.slice(0, 10)}…${h.slice(-8)}` : h;
}

async function waitForReceipt(
  txHash: Hex,
  timeoutMs = 60_000,
  pollEveryMs = 2_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const receipt = await polygonReadRpc<{ status?: string } | null>(
      "eth_getTransactionReceipt",
      [txHash],
    );
    if (receipt) {
      if (receipt.status === "0x1") return;
      throw new Error(`approve tx reverted on-chain: ${txHash}`);
    }
    await new Promise((r) => setTimeout(r, pollEveryMs));
  }
  throw new Error(`approve tx not mined within ${timeoutMs / 1000}s`);
}

function isUserRejection(e: unknown): boolean {
  const code = (e as { code?: number })?.code;
  if (code === 4001) return true;
  const msg = (e instanceof Error ? e.message : String(e)).toLowerCase();
  return msg.includes("user denied") || msg.includes("user rejected");
}

function deriveRepaySteps(
  kind: Status["kind"],
  needsApproval: boolean,
  lang: Lang,
): StepperStep[] | null {
  if (kind === "loading" || kind === "error" || kind === "done") return null;

  if (needsApproval) {
    const labels =
      lang === "zh"
        ? ["USDC.e 授权", "还款", "完成"]
        : ["USDC.e approve", "Repay", "Done"];
    if (kind === "ready" || kind === "submitting" || kind === "approving") {
      return [
        { label: labels[0], state: "current" },
        { label: labels[1], state: "upcoming" },
        { label: labels[2], state: "upcoming" },
      ];
    }
    if (kind === "approve-mining") {
      return [
        { label: labels[0], state: "current" },
        { label: labels[1], state: "upcoming" },
        { label: labels[2], state: "upcoming" },
      ];
    }
    if (kind === "repay-signing" || kind === "repay-mining") {
      return [
        { label: labels[0], state: "done" },
        { label: labels[1], state: "current" },
        { label: labels[2], state: "upcoming" },
      ];
    }
  }

  const labels = lang === "zh" ? ["还款", "完成"] : ["Repay", "Done"];
  if (kind === "ready" || kind === "submitting" || kind === "repay-signing" || kind === "repay-mining") {
    return [
      { label: labels[0], state: "current" },
      { label: labels[1], state: "upcoming" },
    ];
  }
  return null;
}

function friendly(e: unknown, lang: Lang): string {
  console.error("[KPAX Repay] raw error:", e);
  const raw = e instanceof Error ? e.message : String(e);
  const lower = raw.toLowerCase();
  if (lower.includes("nonce too low")) {
    return lang === "zh"
      ? "MetaMask 的 nonce 不同步。关掉所有 MM 弹窗，重试一次即可。"
      : "MetaMask nonce out of sync. Close any pending MM popup and retry.";
  }
  if (lower.includes("rate limit")) {
    return lang === "zh"
      ? "MetaMask 内部限流。建议：1) 关掉 OKX / Phantom 等其它钱包扩展；2) MetaMask 设置 → 安全和隐私 → 关掉 Blockaid 提醒；3) 锁定再解锁 MetaMask。"
      : "MetaMask rate-limited. Try: 1) disable OKX / Phantom; 2) MetaMask Settings → Security & Privacy → disable Blockaid; 3) lock+unlock MetaMask.";
  }
  return raw;
}
