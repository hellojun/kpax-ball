/**
 * Service Worker — background process for the extension.
 *
 * Responsibilities:
 * 1. Route messages between Content Script and Side Panel
 * 2. Call KPAX backend API
 * 3. Manage local cache (chrome.storage.local)
 * 4. Open/close Side Panel
 */

import { getPreview } from "@shared/api";
import { PREVIEW_CACHE_TTL } from "@shared/constants";
import type { ExtensionMessage, FootballMarket, QuickPreview } from "@shared/types";
import { setupLendingNotifications } from "./lending-notifications";

let activeMarket: FootballMarket | null = null;

// Mount the lending alert poller. Idempotent across SW restarts (chrome.alarms
// persists across SW lifecycle, so a re-mount just refreshes the period).
setupLendingNotifications();

// Listen for messages from Content Script and Side Panel
chrome.runtime.onMessage.addListener(
  (message: ExtensionMessage, _sender, sendResponse) => {
    switch (message.type) {
      case "MARKET_DETECTED":
        activeMarket = message.market;
        // Enable side panel for this tab
        break;

      case "MARKET_LEFT":
        activeMarket = null;
        break;

      case "REQUEST_PREVIEW":
        handlePreviewRequest(message.slug).then(sendResponse);
        return true; // async response

      case "REQUEST_DEEP_ANALYSIS":
        // Forward to Side Panel via port or storage
        break;
    }
  }
);

// ---------------------------------------------------------------- tab-scoped panel
//
// We mimic Claude's extension exactly: NO `side_panel.default_path` in the
// manifest and NO `setPanelBehavior` call. The panel is created entirely
// programmatically and bound to the originating tab via `open({tabId})`.
// Chrome handles show/hide automatically as the user switches tabs:
//   - switch away from the originating tab → panel hides
//   - switch back to the originating tab    → panel reappears
//   - other tabs (where setOptions was never called) → no panel at all
// This is the behavior the user actually wants, and it cannot be achieved
// with `default_path` + `setPanelBehavior` (which makes the panel global).

const SIDE_PANEL_PATH = "src/sidepanel/index.html";

chrome.action.onClicked.addListener((tab) => {
  if (!tab.id) return;
  // CRITICAL: don't `await` between the user gesture and `sidePanel.open` —
  // Chrome only allows opening the side panel from within a user gesture, and
  // an `await` consumes the gesture token. Fire both calls synchronously
  // (the same pattern Claude's extension uses).
  const tabId = tab.id;
  chrome.sidePanel.setOptions({
    tabId,
    path: SIDE_PANEL_PATH,
    enabled: true,
  });
  chrome.sidePanel.open({ tabId }).catch((err) => {
    console.warn("[KPAX] sidePanel.open failed", err);
  });
});

async function handlePreviewRequest(slug: string): Promise<QuickPreview | null> {
  // Check cache first
  const cacheKey = `preview_${slug}`;
  const cached = await getCached<QuickPreview>(cacheKey);
  if (cached) return cached;

  // Call KPAX API
  try {
    const preview = await getPreview(
      slug,
      activeMarket?.polymarketOdds
    );
    await setCache(cacheKey, preview, PREVIEW_CACHE_TTL);
    return preview;
  } catch (err) {
    console.error("[KPAX] Preview request failed:", err);
    return null;
  }
}

// Simple cache helpers using chrome.storage.local
async function getCached<T>(key: string): Promise<T | null> {
  const result = await chrome.storage.local.get(key);
  const entry = result[key];
  if (!entry) return null;
  if (Date.now() > entry.expiresAt) {
    await chrome.storage.local.remove(key);
    return null;
  }
  return entry.data as T;
}

async function setCache(key: string, data: unknown, ttlMs: number) {
  await chrome.storage.local.set({
    [key]: { data, expiresAt: Date.now() + ttlMs },
  });
}
