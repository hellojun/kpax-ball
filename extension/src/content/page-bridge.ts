/**
 * Page bridge — runs in the MAIN world (the page's actual JS context, not the
 * content script's isolated world) so it can see `window.ethereum` injected by
 * MetaMask / Rabby / Coinbase Wallet. The content script (isolated world)
 * cannot reach window.ethereum directly.
 *
 * Protocol (window.postMessage):
 *   request : { source: "kpax-eth-bridge", direction: "to-page",
 *               id: string, method: string, params: unknown[] }
 *   response: { source: "kpax-eth-bridge", direction: "from-page",
 *               id: string, ok: true, result: unknown }
 *           | { source: "kpax-eth-bridge", direction: "from-page",
 *               id: string, ok: false, error: { code: number, message: string } }
 *
 * This file is injected as a string by the content script (see eth-bridge.ts).
 * Keep it dependency-free and IIFE-friendly.
 */
(function pageBridge() {
  const SRC = "kpax-eth-bridge";

  // Wait for an injected provider; MetaMask sometimes injects after page load.
  function getProvider(): { request: (args: { method: string; params?: unknown[] }) => Promise<unknown> } | null {
    const w = window as unknown as { ethereum?: unknown };
    return (w.ethereum as { request: (args: { method: string; params?: unknown[] }) => Promise<unknown> } | undefined) ?? null;
  }

  window.addEventListener("message", async (event) => {
    if (event.source !== window) return;
    const data = event.data as
      | { source?: string; direction?: string; id?: string; method?: string; params?: unknown[] }
      | undefined;
    if (!data || data.source !== SRC || data.direction !== "to-page") return;

    const id = data.id;
    if (!id || !data.method) return;

    const provider = getProvider();
    if (!provider) {
      window.postMessage(
        {
          source: SRC,
          direction: "from-page",
          id,
          ok: false,
          error: { code: -32601, message: "No injected wallet (window.ethereum)" },
        },
        "*",
      );
      return;
    }

    console.log("[KPAX page-bridge] forwarding to wallet:", data.method, data.params);
    try {
      const result = await provider.request({ method: data.method, params: data.params ?? [] });
      console.log("[KPAX page-bridge] wallet replied OK:", data.method, result);
      window.postMessage(
        { source: SRC, direction: "from-page", id, ok: true, result },
        "*",
      );
    } catch (err) {
      const e = err as { code?: number; message?: string };
      console.warn("[KPAX page-bridge] wallet replied ERR:", data.method, err);
      window.postMessage(
        {
          source: SRC,
          direction: "from-page",
          id,
          ok: false,
          error: {
            code: typeof e.code === "number" ? e.code : -32000,
            message: typeof e.message === "string" ? e.message : String(err),
          },
        },
        "*",
      );
    }
  });

  // Optional handshake so the content script can detect provider readiness.
  window.postMessage({ source: SRC, direction: "from-page", event: "ready", hasProvider: !!getProvider() }, "*");
})();
