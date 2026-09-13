/**
 * Content Script — 注入 Polymarket 页面
 *
 * 每 15 秒扫描一次页面，检测是否为足球盘口。
 * 如果是，提取球赛信息发给 Side Panel。
 */

import { isPolymarketEventPage } from "./detector";
import { extractMarketData } from "./extractor";
import "./eth-bridge"; // installs the chrome.runtime.onMessage handler for KPAX_ETH_REQUEST
import type { ExtensionMessage, FootballMarket, Theme } from "@shared/types";

const SCAN_INTERVAL = 1_000; // 1 秒

let lastMarketKey: string | null = null;
let lastTheme: Theme | null = null;
let sentMarketLeft = false;
// Number of consecutive scans where extractMarketData() returned null while
// the URL still looked like a match page. We only count those toward
// MARKET_LEFT — a single transient null (Polymarket SPA briefly re-rendering,
// MM popup stealing focus, an unrelated DOM mutation racing with the scan)
// is not a real "user navigated away" signal. Without this debounce, the
// 1Hz scan was unmounting the side panel's BorrowEntry mid-borrow and
// destroying the BorrowFlowDialog while the user was still signing in MM.
let consecutiveExtractFailures = 0;
const MARKET_LEFT_THRESHOLD = 3;

async function scan() {
  // 记录扫描时的 URL，提取完后对比——如果 URL 已变说明在 SPA 切换中，丢弃结果
  const urlAtStart = location.href;

  if (!isPolymarketEventPage()) {
    // URL-based check is deterministic — fire MARKET_LEFT immediately.
    consecutiveExtractFailures = 0;
    if (lastMarketKey) {
      lastMarketKey = null;
      chrome.runtime.sendMessage({ type: "MARKET_LEFT" } as ExtensionMessage);
    }
    return;
  }

  const market = await extractMarketData();

  // SPA 切换中 URL 已变，本次结果无效
  if (location.href !== urlAtStart) return;

  if (!market) {
    consecutiveExtractFailures += 1;
    if (consecutiveExtractFailures >= MARKET_LEFT_THRESHOLD) {
      lastMarketKey = null;
      if (!sentMarketLeft) {
        sentMarketLeft = true;
        chrome.storage.local.remove("kpax_market");
        chrome.runtime.sendMessage({ type: "MARKET_LEFT" } as ExtensionMessage).catch(() => {});
      }
    }
    return;
  }
  consecutiveExtractFailures = 0;
  sentMarketLeft = false;

  const hasOdds = Object.keys(market.polymarketOdds || {}).length > 0;
  const marketKey = `${market.slug}|${market.homeTeam}|${market.awayTeam}|${hasOdds}`;
  if (marketKey !== lastMarketKey) {
    lastMarketKey = marketKey;
    const msg: ExtensionMessage = { type: "MARKET_DETECTED", market };
    chrome.runtime.sendMessage(msg);
    chrome.storage.local.set({ kpax_market: market });
    injectKpaxIcon(market);
  }
}

function injectKpaxIcon(market: FootballMarket) {
  if (document.getElementById("kpax-ball-icon")) return;

  const icon = document.createElement("div");
  icon.id = "kpax-ball-icon";
  icon.title = `KPAX: ${market.homeTeam} vs ${market.awayTeam}`;
  icon.textContent = "K";
  icon.addEventListener("click", () => {
    chrome.runtime.sendMessage({
      type: "OPEN_SIDEPANEL",
    } as ExtensionMessage);
  });

  document.body.appendChild(icon);
}

/** 检测 Polymarket 当前主题（dark/light） */
function detectTheme(): Theme {
  const html = document.documentElement;

  // 检查 html class 或 data 属性
  if (html.classList.contains("dark") || html.getAttribute("data-theme") === "dark") {
    return "dark";
  }
  if (html.classList.contains("light") || html.getAttribute("data-theme") === "light") {
    return "light";
  }

  // 检查 body 背景色
  const bg = getComputedStyle(document.body).backgroundColor;
  if (bg) {
    const match = bg.match(/(\d+)/g);
    if (match && match.length >= 3) {
      const brightness = (parseInt(match[0]) + parseInt(match[1]) + parseInt(match[2])) / 3;
      return brightness < 128 ? "dark" : "light";
    }
  }

  // 检查 color-scheme
  const colorScheme = getComputedStyle(html).colorScheme;
  if (colorScheme?.includes("light")) return "light";

  return "dark";
}

function syncTheme() {
  const theme = detectTheme();
  if (theme !== lastTheme) {
    lastTheme = theme;
    chrome.runtime.sendMessage({ type: "THEME_CHANGED", theme } as ExtensionMessage);
    chrome.storage.local.set({ kpax_theme: theme });
  }
}

// 首次扫描（无条件写入主题，确保 Side Panel 能读到）
scan();
const initialTheme = detectTheme();
lastTheme = initialTheme;
chrome.storage.local.set({ kpax_theme: initialTheme });

// 每 15 秒扫描（兜底）
setInterval(() => {
  scan();
  syncTheme();
}, SCAN_INTERVAL);

// Side Panel 打开时可能请求当前主题
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "REQUEST_RESCAN") {
    lastMarketKey = null;
    sentMarketLeft = false;
    scan();
  }
  if (msg.type === "REQUEST_THEME") {
    const theme = detectTheme();
    chrome.storage.local.set({ kpax_theme: theme });
    chrome.runtime.sendMessage({ type: "THEME_CHANGED", theme } as ExtensionMessage);
  }
});

// 监听 Polymarket 主题切换（立即响应）
new MutationObserver(syncTheme).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ["class", "data-theme", "style"],
});
