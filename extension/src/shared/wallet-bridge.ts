/**
 * Side-panel-side wallet bridge. Provides a `Signer` (compatible with
 * safe-approval.ts) backed by the content script's `KPAX_ETH_REQUEST` relay,
 * which in turn talks to `window.ethereum` via a MAIN-world page bridge.
 *
 * The user must be on a Polymarket tab when invoked — that's where our
 * content script is injected and where MetaMask injects window.ethereum.
 */

import type { Address, Hex } from "viem";

import type { SafeTypedData, Signer } from "./safe-approval";
import { polygonReadRpc } from "./wallet";

/** Methods that may take a long time because the user is interacting with
 *  MetaMask (popup, signing, broadcasting). All other RPC reads should be
 *  fast and treated as failures if they hang. */
const INTERACTIVE_METHODS = new Set([
  "eth_requestAccounts",
  "eth_signTypedData_v4",
  "eth_signTypedData_v3",
  "eth_signTypedData",
  "personal_sign",
  "eth_sign",
  "eth_sendTransaction",
  "wallet_switchEthereumChain",
  "wallet_addEthereumChain",
]);

/** Errors thrown by ethRequest. We surface the original MM error code so
 *  callers can branch (e.g. user-rejection 4001 vs rate-limit -32603). */
class WalletBridgeError extends Error {
  code: number;
  constructor(message: string, code: number) {
    super(message);
    this.code = code;
  }
}

/** True iff this looks like MetaMask's "internal RPC backend exhausted"
 *  error. MM's underlying Infura connection is rate-limited per-key, and
 *  on a heavy origin (Polymarket) it's easy to drain. The error always
 *  comes back as JSON-RPC -32603 with "rate limited" in the message. */
function isRateLimit(code: number, msg: string): boolean {
  if (code !== -32603) return false;
  return msg.toLowerCase().includes("rate limit");
}

/** Single round-trip to the page bridge, no retries. */
async function ethRequestOnce<T>(
  method: string,
  params: unknown[],
): Promise<T> {
  const tabs = await chrome.tabs.query({
    active: true,
    currentWindow: true,
    url: "https://polymarket.com/*",
  });
  const tabId = tabs[0]?.id;
  if (!tabId) {
    throw new Error(
      "Open a Polymarket page first — KPAX needs the active tab to talk to your wallet.",
    );
  }

  const timeoutMs = INTERACTIVE_METHODS.has(method) ? 5 * 60 * 1000 : 30 * 1000;

  let resp:
    | { ok: true; result: T }
    | { ok: false; error: { code: number; message: string } };
  try {
    resp = (await Promise.race([
      chrome.tabs.sendMessage(tabId, {
        type: "KPAX_ETH_REQUEST",
        method,
        params,
      }),
      new Promise((_, reject) =>
        setTimeout(
          () =>
            reject(
              new Error(
                `Timed out waiting for wallet response (${method}). Check the MetaMask icon for pending requests.`,
              ),
            ),
          timeoutMs,
        ),
      ),
    ])) as typeof resp;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("Receiving end does not exist") || msg.includes("Could not establish connection")) {
      throw new Error(
        "Refresh the Polymarket page (Cmd+R) — the wallet bridge isn't loaded yet.",
      );
    }
    throw err;
  }

  if (!resp || (resp as { ok?: boolean }).ok === undefined) {
    throw new Error("Wallet bridge: no response (is the page bridge installed?)");
  }
  if (!resp.ok) {
    throw new WalletBridgeError(
      resp.error.message ?? "wallet error",
      resp.error.code ?? -32000,
    );
  }
  return resp.result;
}

/** Send a JSON-RPC request to the active Polymarket tab.
 *
 * Auto-retries on MetaMask's internal rate-limit (-32603 "rate limited").
 * This error originates inside MM's `addDappTransaction` flow — typically
 * either Blockaid security simulation or MM's built-in Infura — both of
 * which are saturated by Polymarket's own RPC traffic, the user's other
 * wallet extensions (OKX/Phantom/Backpack) sharing the same window, and
 * heavy in-page hooks. Our flow only ever issues 2-3 MM calls per borrow,
 * so retrying with backoff is the right move; lock+unlock works but
 * shouldn't be the user's first line of defense.
 *
 * Schedule (interactive sign/send): 6s → 14s → 25s, total ~45s before giving up.
 * Schedule (read): 2.5s → 6s, total ~8s. (We mostly bypass MM for reads
 * already, so this branch rarely fires.)
 */
async function ethRequest<T>(method: string, params: unknown[] = []): Promise<T> {
  const isInteractive = INTERACTIVE_METHODS.has(method);
  const backoffMs = isInteractive ? [6000, 14000, 25000] : [2500, 6000];

  let lastErr: unknown;
  for (let attempt = 0; attempt <= backoffMs.length; attempt++) {
    try {
      return await ethRequestOnce<T>(method, params);
    } catch (err) {
      lastErr = err;
      if (err instanceof WalletBridgeError && isRateLimit(err.code, err.message)) {
        if (attempt < backoffMs.length) {
          const wait = backoffMs[attempt];
          console.warn(
            `[KPAX wallet-bridge] MetaMask rate-limited on ${method}, ` +
              `retrying in ${wait}ms (attempt ${attempt + 1}/${backoffMs.length})`,
          );
          await new Promise((r) => setTimeout(r, wait));
          continue;
        }
      }
      throw err;
    }
  }
  throw lastErr;
}

/** Return the connected EOA. Uses `eth_accounts` (silent, no popup, no rate
 *  limit) when the user is already connected; falls back to
 *  `eth_requestAccounts` only when no accounts are connected, so we don't
 *  trip MetaMask's connect-request throttle on repeat reads. */
export async function requestAccounts(): Promise<Address> {
  const existing = await ethRequest<string[]>("eth_accounts", []);
  if (existing && existing.length > 0) return existing[0] as Address;
  const fresh = await ethRequest<string[]>("eth_requestAccounts", []);
  if (!fresh?.length) throw new Error("No wallet account available");
  return fresh[0] as Address;
}

/** Get the active wallet chain id. Used to detect wrong-network situations. */
export async function getChainIdHex(): Promise<bigint> {
  const hex = await ethRequest<Hex>("eth_chainId", []);
  return BigInt(hex);
}

/** Build a `Signer` (per safe-approval.ts) that proxies signing/broadcasting
 *  through the content-script bridge to MetaMask.
 *
 *  Verifies the page has MetaMask permission for `expectedAddress` before
 *  returning. Without this, the first signTypedData/sendTransaction call
 *  comes back as MM error 4100 ("method has not been authorized") if the
 *  user hasn't pressed "Connect" on the active Polymarket tab — which is
 *  exactly what happens on a fresh tab or after MM permissions were revoked.
 *
 *  Cost: one silent `eth_accounts` read (no popup, no rate-limit hit on the
 *  hot path) per signer; falls back to `eth_requestAccounts` (popup) only
 *  when the page genuinely lacks permission. */
export async function createBridgeSigner(
  expectedAddress?: string,
): Promise<Signer> {
  let address: Address;
  if (expectedAddress) {
    const accounts = await ethRequest<string[]>("eth_accounts", []);
    const expectedLower = expectedAddress.toLowerCase();
    const granted = accounts.some((a) => a.toLowerCase() === expectedLower);
    if (!granted) {
      // MM may be locked / never connected on this tab / connected to a
      // different account. eth_requestAccounts forces the popup and returns
      // whichever accounts the user grants — typically a single one.
      const fresh = await ethRequest<string[]>("eth_requestAccounts", []);
      const ok = fresh.some((a) => a.toLowerCase() === expectedLower);
      if (!ok) {
        const want = `${expectedAddress.slice(0, 6)}…${expectedAddress.slice(-4)}`;
        const got = fresh[0] ? `${fresh[0].slice(0, 6)}…${fresh[0].slice(-4)}` : "(none)";
        throw new Error(
          `MetaMask is connected as ${got} but KPAX expected ${want}. ` +
            `Switch accounts in MetaMask, or log out of KPAX and back in with ${got}.`,
        );
      }
    }
    address = expectedAddress as Address;
  } else {
    address = await requestAccounts();
  }

  return {
    address,
    async getChainId() {
      return getChainIdHex();
    },
    async signTypedData(typedData: SafeTypedData) {
      const stringified = stringifyTypedData(typedData);
      return ethRequest<Hex>("eth_signTypedData_v4", [address, stringified]);
    },
    async sendTransaction({ to, data, value }) {
      const valueHex = value ? "0x" + value.toString(16) : "0x0";
      // Pre-estimate gas via publicnode and pass it to MM. MM's internal
      // `addDappTransaction` would otherwise call its own RPC backend
      // (Infura) for eth_estimateGas — which is exactly the path that's
      // returning -32603 "rate limited" when Polymarket has been hammering
      // the same MM instance. By providing `gas` we let MM skip that call.
      // Best-effort: if publicnode fails for any reason we fall through to
      // MM's default behavior. We add a 25% headroom over the estimate to
      // tolerate small differences in block state by the time MM mines it.
      let gasField: { gas?: Hex } = {};
      try {
        const estimateHex = await polygonReadRpc<Hex>("eth_estimateGas", [
          { from: address, to, data, value: valueHex },
        ]);
        const estimate = BigInt(estimateHex);
        const padded = (estimate * 125n) / 100n;
        gasField.gas = ("0x" + padded.toString(16)) as Hex;
      } catch (e) {
        console.warn(
          "[KPAX wallet-bridge] eth_estimateGas via publicnode failed; " +
            "falling back to MM-side estimation:",
          e,
        );
      }
      return ethRequest<Hex>("eth_sendTransaction", [
        { from: address, to, data, value: valueHex, ...gasField },
      ]);
    },
    async callRpc<T>(method: string, params: unknown[]): Promise<T> {
      return ethRequest<T>(method, params);
    },
  };
}

function shorten(addr: string): string {
  return addr.length > 14 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
}

/** Convert a typed-data payload with bigint fields into the
 *  string-encoded JSON that `eth_signTypedData_v4` accepts. */
function stringifyTypedData(td: SafeTypedData): string {
  const safe = JSON.parse(
    JSON.stringify(td, (_key, value) =>
      typeof value === "bigint" ? value.toString() : value,
    ),
  );
  return JSON.stringify(safe);
}
