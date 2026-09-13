/**
 * Minimal Polygon USDC.e balance reader.
 *
 * No web3 library dependency — we just encode `balanceOf(address)` by hand and
 * call a public Polygon JSON-RPC endpoint.
 *
 * V4 vault underlying is USDC.e (`0x2791…4174`), not native USDC. The header
 * balance and Profile explorer link must match — sending native USDC at this
 * point fails any vault interaction (V4 `initializeV3` migrated underlying to
 * USDC.e — see CLAUDE.md "Lending vault — current state (V4)").
 */

/** Bridged USDC.e on Polygon — V4 vault underlying. */
export const POLYGON_USDC_E_ADDRESS =
  "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";

/** Public Polygon RPC (free, no API key required).
 *  We deliberately avoid `polygon-rpc.com` — Polygon labs disabled API-key-less
 *  access on that endpoint, so it returns `403 tenant disabled`. publicnode is
 *  community-run and currently open. */
const POLYGON_RPC_URL = "https://polygon-bor-rpc.publicnode.com";

/** USDC.e on Polygon has 6 decimals (same as native USDC). */
const USDC_DECIMALS = 6;

/** `balanceOf(address)` function selector = keccak256("balanceOf(address)")[:4]. */
const BALANCE_OF_SELECTOR = "0x70a08231";

export interface UsdcBalance {
  /** Human-readable USDC.e amount, e.g. 123.45 */
  amount: number;
  /** Raw base units as a string, e.g. "123450000" */
  raw: string;
  /** When we fetched it (ms since epoch) */
  fetchedAt: number;
}

/**
 * Read the wallet's USDC.e balance on Polygon.
 *
 * Throws on network errors / malformed responses. Callers are responsible for
 * showing a placeholder while this is in flight.
 */
export async function getUsdcBalance(walletAddress: string): Promise<UsdcBalance> {
  if (!isValidAddress(walletAddress)) {
    throw new Error(`Invalid wallet address: ${walletAddress}`);
  }

  const data = BALANCE_OF_SELECTOR + padAddress(walletAddress);

  const resp = await fetch(POLYGON_RPC_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "eth_call",
      params: [{ to: POLYGON_USDC_E_ADDRESS, data }, "latest"],
    }),
  });

  if (!resp.ok) {
    throw new Error(`RPC error: ${resp.status} ${resp.statusText}`);
  }

  const body = await resp.json();
  if (body.error) {
    throw new Error(`RPC error: ${body.error.message ?? JSON.stringify(body.error)}`);
  }

  const hex = body.result as string;
  if (typeof hex !== "string" || !hex.startsWith("0x")) {
    throw new Error("Malformed RPC response");
  }

  const raw = BigInt(hex).toString();
  const amount = Number(BigInt(hex)) / 10 ** USDC_DECIMALS;
  return { amount, raw, fetchedAt: Date.now() };
}

/** 0x + 40 hex chars. */
function isValidAddress(s: string): boolean {
  return /^0x[0-9a-fA-F]{40}$/.test(s);
}

/** Lowercase the address and pad it to 32 bytes (64 hex chars), stripping "0x". */
function padAddress(addr: string): string {
  return addr.toLowerCase().replace(/^0x/, "").padStart(64, "0");
}

/** Format amount with comma thousands separators and 2 decimals. */
export function formatUsdc(amount: number): string {
  return amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * Send a Polygon JSON-RPC call directly to publicnode, bypassing MetaMask.
 *
 * Why bypass MM for reads: MetaMask routes RPC through Infura (or a
 * user-configured endpoint) and aggressively rate-limits when no API key is
 * set. Polling `eth_getTransactionReceipt` every 2s for a minute, plus a few
 * `eth_call` reads, can easily trip the throttle and then break the FOLLOWING
 * `eth_sendTransaction` with the same "rate limit" error. Routing reads
 * straight to a public RPC keeps MM cleared for the interactive (sign/send)
 * calls that actually need it.
 */
export async function polygonReadRpc<T>(
  method: string,
  params: unknown[],
): Promise<T> {
  const resp = await fetch(POLYGON_RPC_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  if (!resp.ok) {
    throw new Error(`Polygon RPC ${method} failed: ${resp.status} ${resp.statusText}`);
  }
  const body = await resp.json();
  if (body.error) {
    throw new Error(
      `Polygon RPC ${method}: ${body.error.message ?? JSON.stringify(body.error)}`,
    );
  }
  return body.result as T;
}
