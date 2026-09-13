import { API_BASE_URL } from "./constants";
import { getToken } from "./auth";

/** LTV tier row in /api/lending/config */
export interface LendingTier {
  tier: 1 | 2 | 3;
  label: string;
  max_ltv: number;
  warning_ltv: number;
  liquidation_ltv: number;
  min_market_depth_usd: number;
}

export interface LendingConfig {
  tos_version: string;
  vault_address: string;
  apr_bps: number;
  liquidation_penalty_bps: number;
  kickoff_buffer_hours: number;
  kickoff_warn_hours: number;
  min_hours_before_kickoff_to_borrow: number;
  max_borrow_per_user_usd: number;
  max_total_tvl_usd: number;
  tiers: LendingTier[];
}

export interface TosStatus {
  current_version: string;
  user_version: string | null;
  accepted: boolean;
}

/** Backend-classified reason a position can't be borrowed against. Drives
 *  which AssetCard variant (and CTA) we render.
 *
 *   non_football        → Tennis / politics / etc — KPAX scope only
 *   unsupported_league  → Football, but not Premier League / World Cup yet —
 *                         user can request notification
 *   zero_value          → Stale dust position with $0 mark — hide details
 */
export type IneligibleReason =
  | "non_football"
  | "unsupported_league"
  | "zero_value"
  | "kickoff_too_close";

export interface LendingPosition {
  asset: string;
  condition_id: string;
  event_slug: string | null;
  title: string | null;
  outcome: string | null;
  size: number;
  current_price: number;
  value_usd: number;
  competition_hint: string | null;
  league_tier: 1 | 2 | 3 | null;
  borrow_eligible: boolean;
  /** null when borrow_eligible is true. */
  ineligible_reason: IneligibleReason | null;
  max_borrowable_usd: number;
}

export type ProxyKind = "safe" | "magic" | "none";

export interface ProxyCandidate {
  kind: "safe" | "magic";
  address: string;
  deployed: boolean;
}

export interface ProxyDetectionResponse {
  eoa: string;
  kind: ProxyKind;
  proxy: string | null;
  candidates: ProxyCandidate[];
}

export interface PositionsResponse {
  wallet_address: string;
  proxy: string | null;
  proxy_kind: ProxyKind;
  positions: LendingPosition[];
  eligible_count: number;
  eligible_total_value_usd: number;
}

async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = await getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

async function apiRequest<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers = await authHeaders();
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    // FastAPI puts the human-readable reason in `body.detail`. Surface it so
    // the UI doesn't show a bare "failed: 400" — users / devs need the
    // actual validation message ("Match starts in <24h", "Principal exceeds
    // ...", etc.). Falls back to status code if body isn't JSON.
    let reason = "";
    try {
      const text = await resp.text();
      try {
        const data = JSON.parse(text);
        if (typeof data?.detail === "string") reason = data.detail;
        else if (data?.detail) reason = JSON.stringify(data.detail);
        else if (typeof data?.message === "string") reason = data.message;
        else reason = text;
      } catch {
        reason = text;
      }
    } catch {
      // body unreadable — leave reason empty, fall back to status only
    }
    const msg = reason
      ? `${reason} (HTTP ${resp.status})`
      : `Lending API ${method} ${path} failed: ${resp.status}`;
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

export async function fetchLendingConfig(): Promise<LendingConfig> {
  return apiRequest("GET", "/api/lending/config");
}

export interface PoolBalanceResponse {
  /** Vault's `lpPoolBalance` in USDC.e (2-decimal rounded). The contract
   *  reverts openLoan when principal > this. */
  pool_balance_usd: number;
}

export async function fetchPoolBalance(): Promise<PoolBalanceResponse> {
  return apiRequest("GET", "/api/lending/pool-balance");
}

export async function fetchTosStatus(): Promise<TosStatus> {
  return apiRequest("GET", "/api/lending/tos");
}

export async function acceptTos(tosVersion: string): Promise<{ accepted: boolean; tos_version: string }> {
  return apiRequest("POST", "/api/lending/tos/accept", { tos_version: tosVersion });
}

export async function fetchPositions(): Promise<PositionsResponse> {
  return apiRequest("GET", "/api/lending/positions");
}

export async function fetchProxy(): Promise<ProxyDetectionResponse> {
  return apiRequest("GET", "/api/lending/proxy");
}

export interface PrepareBorrowResponse {
  loan_id: number;
  vault_address: string;
  to: string;
  data: string;
  value: string;
  chain_id: number;
  proxy: string;
  ctf_token_id: string;
  shares: string;
  principal_base_units: string;
  match_kickoff_unix: number;
  league_tier: number;
  apr_bps: number;
}

export async function prepareBorrow(
  asset: string,
  principalUsd: number,
): Promise<PrepareBorrowResponse> {
  return apiRequest("POST", "/api/lending/prepare-borrow", {
    asset,
    principal_usd: principalUsd,
  });
}

export interface ConfirmBorrowResponse {
  loan_id: number;
  status: "active" | "failed" | "pending" | string;
  onchain_loan_id: number | null;
  open_tx_hash: string;
  block_number: number;
}

export async function confirmBorrow(
  loanId: number,
  txHash: string,
): Promise<ConfirmBorrowResponse> {
  return apiRequest("POST", "/api/lending/confirm-borrow", {
    loan_id: loanId,
    tx_hash: txHash,
  });
}

export interface RiskAssessmentResponse {
  asset: string;
  recommended_borrow_usd: number;
  recommended_ltv: number;
  max_borrow_usd: number;
  max_ltv: number;
  league_tier: 1 | 2 | 3;
  risk_score: number; // 1-10
  liquidation_probability_estimate: number;
  risk_reasoning: string;
  key_risks: { factor: string; impact: string }[];
}

export async function fetchRiskAssessment(
  asset: string,
): Promise<RiskAssessmentResponse> {
  return apiRequest("POST", "/api/lending/risk-assessment", { asset });
}

// ---------------------------------------------------------------- Loans / repay

export interface LoanItem {
  loan_id: number;
  onchain_loan_id: number | null;
  status: string; // active | pending | repaid | liquidated_* | failed_repay
  market_slug: string;
  title: string | null;
  ctf_token_id: string;
  collateral_shares: number;
  collateral_value_at_open: number;
  principal: number;
  apr_bps: number;
  league_tier: 1 | 2 | 3;
  opened_ltv: number;
  opened_at: string;
  match_kickoff_at: string;
  closed_at: string | null;
  open_tx_hash: string | null;
  close_tx_hash: string | null;
  current_debt_usd: number | null;
  interest_so_far_usd: number | null;
  current_collateral_price: number | null;
  current_collateral_value_usd: number | null;
  current_ltv: number | null;
  health_status: "healthy" | "caution" | "warn" | "liquidate" | null;
  total_interest_paid: number | null;
  /** Liquidation funds-flow (only for liquidated_* loans). All in USDC.e. */
  liq_proceeds_usd: number | null;
  liq_principal_repaid_usd: number | null;
  liq_to_treasury_usd: number | null;
  liq_residual_usd: number | null;
}

export interface LoansResponse {
  loans: LoanItem[];
}

export async function fetchLoans(): Promise<LoansResponse> {
  return apiRequest("GET", "/api/lending/loans");
}

export interface PrepareRepayResponse {
  loan_id: number;
  onchain_loan_id: number;
  vault_address: string;
  /** USDC.e (V4 vault underlying). */
  usdc_address: string;
  total_debt_base_units: string;
  total_debt_usd: number;
  principal_usd: number;
  interest_usd: number;
  /** Always == vault_address. */
  repay_to: string;
  /** 0x-prefixed calldata for `vault.repay(loanId)`. */
  repay_data: string;
}

export async function prepareRepay(loanId: number): Promise<PrepareRepayResponse> {
  return apiRequest("POST", "/api/lending/prepare-repay", { loan_id: loanId });
}

export interface ConfirmRepayResponse {
  loan_id: number;
  status: "repaid" | "failed" | string;
  close_tx_hash: string;
  block_number: number;
  interest_paid_usd: number | null;
}

export async function confirmRepay(
  loanId: number,
  txHash: string,
): Promise<ConfirmRepayResponse> {
  return apiRequest("POST", "/api/lending/confirm-repay", {
    loan_id: loanId,
    tx_hash: txHash,
  });
}

// ---------------------------------------------------------------- Alerts

export type LendingAlertType = "ltv_70" | "ltv_80" | "kickoff_4h" | "kickoff_2h";

export interface LendingAlert {
  id: number;
  loan_id: number;
  alert_type: LendingAlertType;
  sent_at: string; // ISO
}

/** SW polls this every minute. Server returns this user's alerts where
 *  delivered_at IS NULL. */
export async function fetchPendingAlerts(): Promise<LendingAlert[]> {
  return apiRequest("GET", "/api/lending/alerts/pending");
}

/** Mark alerts delivered after chrome.notifications.create() resolved. */
export async function ackAlerts(
  alertIds: number[],
): Promise<{ acknowledged: number }> {
  return apiRequest("POST", "/api/lending/alerts/ack", { alert_ids: alertIds });
}
