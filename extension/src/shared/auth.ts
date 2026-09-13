/**
 * Privy-based authentication (via a hosted auth page + backend exchange).
 *
 * Flow:
 *   1. signIn() opens a popup pointing at PRIVY_AUTH_URL. The hosted page
 *      runs the Privy SDK, obtains a Privy access token + wallet address,
 *      then posts them to the opener via postMessage.
 *   2. We forward those to POST /api/auth/privy/exchange, which validates
 *      the Privy token and returns a KPAX session JWT.
 *   3. The KPAX JWT + user profile are cached in chrome.storage.local.
 */
import { API_BASE_URL, PRIVY_AUTH_URL } from "./constants";

export interface UserInfo {
  wallet_address: string;
  email: string | null;
  privy_user_id: string;
}

interface PrivyPostMessage {
  type: "kpax-privy-auth";
  privy_access_token: string;
  wallet_address: string;
  email?: string | null;
}

const TOKEN_KEY = "kpax_jwt";
const USER_KEY = "kpax_user";

export async function signIn(): Promise<UserInfo> {
  const payload = await openPrivyAuthPopup();

  const resp = await fetch(`${API_BASE_URL}/api/auth/privy/exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      privy_access_token: payload.privy_access_token,
      wallet_address: payload.wallet_address,
      email: payload.email ?? null,
    }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Auth exchange failed: ${resp.status} ${text}`);
  }

  const data: { kpax_jwt: string; user: UserInfo } = await resp.json();
  await chrome.storage.local.set({
    [TOKEN_KEY]: data.kpax_jwt,
    [USER_KEY]: data.user,
  });
  return data.user;
}

export async function signOut(): Promise<void> {
  await chrome.storage.local.remove([TOKEN_KEY, USER_KEY]);
}

export async function getCachedUser(): Promise<UserInfo | null> {
  const data = await chrome.storage.local.get(USER_KEY);
  return (data[USER_KEY] as UserInfo | undefined) ?? null;
}

export async function getToken(): Promise<string | null> {
  const data = await chrome.storage.local.get(TOKEN_KEY);
  return (data[TOKEN_KEY] as string | undefined) ?? null;
}

// ---------- popup + postMessage bridge ----------

async function openPrivyAuthPopup(): Promise<PrivyPostMessage> {
  // Use a normal window (not type: "popup"), otherwise Chrome blocks the
  // secondary OAuth popup that Privy opens for Google/Apple/Twitter login.
  const authUrl = `${PRIVY_AUTH_URL}?ext_id=${encodeURIComponent(chrome.runtime.id)}`;

  // Center on the primary display. screen.availWidth/Height excludes dock/taskbar.
  const width = 460;
  const height = 720;
  const left = Math.max(0, Math.round((window.screen.availWidth - width) / 2));
  const top = Math.max(0, Math.round((window.screen.availHeight - height) / 2));

  const win = await chrome.windows.create({
    url: authUrl,
    type: "normal",
    width,
    height,
    left,
    top,
    focused: true,
  });

  if (!win.id) throw new Error("Failed to open auth window");
  const windowId = win.id;

  return new Promise<PrivyPostMessage>((resolve, reject) => {
    const timeoutMs = 5 * 60 * 1000;
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("Login timed out"));
    }, timeoutMs);

    const onRemoved = (closedId: number) => {
      if (closedId === windowId) {
        cleanup();
        reject(new Error("Login window closed"));
      }
    };

    const onMessage = (
      message: unknown,
      sender: chrome.runtime.MessageSender,
      sendResponse: (response?: unknown) => void,
    ) => {
      if (!isPrivyPayload(message)) return;
      // Only accept messages from the hosted Privy auth origin.
      try {
        if (sender.url) {
          const origin = new URL(sender.url).origin;
          if (!isAllowedAuthOrigin(origin)) return;
        }
      } catch {
        return;
      }
      sendResponse({ ok: true });
      cleanup();
      chrome.windows.remove(windowId).catch(() => {});
      resolve(message);
    };

    const cleanup = () => {
      clearTimeout(timer);
      chrome.windows.onRemoved.removeListener(onRemoved);
      chrome.runtime.onMessageExternal.removeListener(onMessage);
    };

    chrome.windows.onRemoved.addListener(onRemoved);
    chrome.runtime.onMessageExternal.addListener(onMessage);
  });
}

function isAllowedAuthOrigin(origin: string): boolean {
  try {
    const authOrigin = new URL(PRIVY_AUTH_URL).origin;
    if (origin === authOrigin) return true;
  } catch {
    /* fall through */
  }
  // Allow localhost on any port for development.
  return origin.startsWith("http://localhost");
}

function isPrivyPayload(value: unknown): value is PrivyPostMessage {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    v.type === "kpax-privy-auth" &&
    typeof v.privy_access_token === "string" &&
    typeof v.wallet_address === "string"
  );
}
