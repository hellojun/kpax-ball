/**
 * Ethereum bridge — runs in the content script (isolated world). Relays
 * wallet RPC requests between the Side Panel (chrome.runtime) and the
 * page-bridge script (window.postMessage → MAIN world → window.ethereum).
 *
 * Side Panel sends:
 *   { type: "KPAX_ETH_REQUEST", method: string, params?: unknown[] }
 *
 * We respond via the sendResponse callback with:
 *   { ok: true, result }  |  { ok: false, error: { code, message } }
 */

const SRC = "kpax-eth-bridge";
const PAGE_BRIDGE_FLAG = "__KPAX_PAGE_BRIDGE_INSTALLED__";

/** Stringified body of page-bridge.ts. The build pipeline ships its compiled
 *  output as `page-bridge.js` next to content.js; we fetch it via
 *  chrome.runtime.getURL and inject it once per tab. */
async function ensurePageBridge(): Promise<void> {
  // Idempotent: if a previous content-script run already installed it,
  // the global flag is set on the page's window via the script we inject.
  // We can't read MAIN-world globals from the isolated world, so we just
  // inject every time — a duplicate install is cheap and harmless.
  const url = chrome.runtime.getURL("page-bridge.js");
  const script = document.createElement("script");
  script.src = url;
  script.dataset.kpaxBridge = "1";
  // Append, then remove on load — keeps the DOM clean.
  script.addEventListener("load", () => script.remove());
  (document.head ?? document.documentElement).appendChild(script);
  // Best-effort: page bridge posts "ready" — wait briefly so first request
  // doesn't race the script load.
  await waitForPageBridgeReady(500);
  // Reference the unused flag to keep TS happy if this becomes a real check later
  void PAGE_BRIDGE_FLAG;
}

function waitForPageBridgeReady(timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    const handler = (event: MessageEvent) => {
      if (event.source !== window) return;
      const data = event.data as { source?: string; direction?: string; event?: string };
      if (data?.source !== SRC || data?.direction !== "from-page") return;
      if (data.event === "ready") {
        clearTimeout(timer);
        window.removeEventListener("message", handler);
        resolve();
      }
    };
    window.addEventListener("message", handler);
  });
}

let installed = false;

async function relayRpc(method: string, params: unknown[]): Promise<unknown> {
  if (!installed) {
    await ensurePageBridge();
    installed = true;
  }
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const handler = (event: MessageEvent) => {
      if (event.source !== window) return;
      const data = event.data as {
        source?: string;
        direction?: string;
        id?: string;
        ok?: boolean;
        result?: unknown;
        error?: { code?: number; message?: string };
      };
      if (data?.source !== SRC || data?.direction !== "from-page" || data?.id !== id) return;
      window.removeEventListener("message", handler);
      if (data.ok) resolve(data.result);
      else reject(new Error(data.error?.message ?? "wallet error"));
    };
    window.addEventListener("message", handler);
    window.postMessage(
      { source: SRC, direction: "to-page", id, method, params },
      "*",
    );
  });
}

// Receive RPC requests from the Side Panel and proxy them to the page.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "KPAX_ETH_REQUEST") return false;

  const method = String(message.method ?? "");
  const params = Array.isArray(message.params) ? message.params : [];

  relayRpc(method, params).then(
    (result) => sendResponse({ ok: true, result }),
    (err: unknown) => {
      const e = err as { code?: number; message?: string };
      sendResponse({
        ok: false,
        error: {
          code: typeof e.code === "number" ? e.code : -32000,
          message: typeof e.message === "string" ? e.message : String(err),
        },
      });
    },
  );

  // Keep the message channel open for async response.
  return true;
});
