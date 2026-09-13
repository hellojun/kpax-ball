/**
 * Lending alert Service Worker module — polls
 * `/api/lending/alerts/pending` every minute via `chrome.alarms`, creates
 * `chrome.notifications` for each new alert, then acks them server-side so
 * they don't repeat. Click → side panel.
 *
 * Mount from `background/index.ts` once at module load with
 * `setupLendingNotifications()`.
 */
import { getToken } from "@shared/auth";
import { t } from "@shared/i18n";
import {
  ackAlerts,
  fetchPendingAlerts,
  type LendingAlert,
  type LendingAlertType,
} from "@shared/lending-api";
import type { Lang } from "@shared/types";

const ALARM_NAME = "kpax-lending-poll";
const POLL_PERIOD_MIN = 1;
const SIDE_PANEL_PATH = "src/sidepanel/index.html";
const NOTIFICATION_ID_PREFIX = "kpax-alert-";

let mounted = false;

export function setupLendingNotifications(): void {
  if (mounted) return;
  mounted = true;

  chrome.alarms.create(ALARM_NAME, { periodInMinutes: POLL_PERIOD_MIN });

  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_NAME) void pollAndNotify();
  });

  chrome.notifications.onClicked.addListener((notificationId) => {
    if (!notificationId.startsWith(NOTIFICATION_ID_PREFIX)) return;
    openSidePanelOnActiveTab();
    chrome.notifications.clear(notificationId);
  });

  // Run once at SW boot so a freshly-installed extension doesn't wait a
  // whole minute for the first poll.
  void pollAndNotify();
}

function detectLang(): Lang {
  try {
    const ui = chrome.i18n.getUILanguage().toLowerCase();
    return ui.startsWith("zh") ? "zh" : "en";
  } catch {
    return "zh";
  }
}

function alertBody(type: LendingAlertType, lang: Lang): string {
  switch (type) {
    case "ltv_70":
      return t(lang, "lendingAlertLtv70");
    case "ltv_80":
      return t(lang, "lendingAlertLtv80");
    case "kickoff_4h":
      return t(lang, "lendingAlertKickoff4h");
    case "kickoff_2h":
      return t(lang, "lendingAlertKickoff2h");
  }
}

async function pollAndNotify(): Promise<void> {
  const token = await getToken();
  if (!token) return; // not signed in — nothing to alert on

  let alerts: LendingAlert[];
  try {
    alerts = await fetchPendingAlerts();
  } catch (err) {
    console.warn("[KPAX] alert poll failed", err);
    return;
  }
  if (alerts.length === 0) return;

  const lang = detectLang();
  const ackable: number[] = [];

  for (const alert of alerts) {
    try {
      await chrome.notifications.create(
        `${NOTIFICATION_ID_PREFIX}${alert.id}`,
        {
          type: "basic",
          iconUrl: "icons/icon-128.png",
          title: "KPAX Lending",
          message: alertBody(alert.alert_type, lang),
          priority: 2,
          requireInteraction:
            alert.alert_type === "ltv_80" ||
            alert.alert_type === "kickoff_2h",
        },
      );
      ackable.push(alert.id);
    } catch (err) {
      console.warn("[KPAX] notification.create failed", err);
    }
  }

  if (ackable.length > 0) {
    try {
      await ackAlerts(ackable);
    } catch (err) {
      // If ack fails, alerts surface again next tick — annoying but safe.
      console.warn("[KPAX] ack failed; alerts will resurface next tick", err);
    }
  }
}

function openSidePanelOnActiveTab(): void {
  // Mirror background/index.ts pattern: don't `await` between the user gesture
  // (notification click) and sidePanel.open, otherwise Chrome eats the gesture.
  chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
    if (!tab?.id) return;
    const tabId = tab.id;
    chrome.sidePanel.setOptions({
      tabId,
      path: SIDE_PANEL_PATH,
      enabled: true,
    });
    chrome.sidePanel.open({ tabId }).catch((err) => {
      console.warn("[KPAX] sidePanel.open from alert click failed", err);
    });
  });
}
