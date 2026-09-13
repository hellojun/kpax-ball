import { useCallback, useEffect, useState } from "react";
import { usePrivy, useWallets } from "@privy-io/react-auth";

/**
 * Hosted Privy login page opened by the KPAX Chrome extension.
 *
 * State machine:
 *   loading     -> Privy SDK still initializing
 *   connect     -> ready, no active session — show Connect button
 *   confirming  -> ready, session exists — show "Continue as X" + "Use different account"
 *   signing-in  -> Privy modal open (user explicitly clicked Connect)
 *   switching   -> running logout() before showing the Connect screen again
 *   sending     -> relaying Privy token to the extension via sendMessage
 *   done        -> all good; window auto-closes shortly
 *   error       -> surfaces err from any of the above
 */

type Status =
  | "loading"
  | "connect"
  | "confirming"
  | "signing-in"
  | "switching"
  | "sending"
  | "done"
  | "error";

const params = new URLSearchParams(window.location.search);
const EXTENSION_ID = params.get("ext_id");

export default function App() {
  const { ready, authenticated, user, login, logout, getAccessToken } = usePrivy();
  const { wallets } = useWallets();
  const [status, setStatus] = useState<Status>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Relay an authenticated session to the extension, then close the popup.
  const sendSession = useCallback(async () => {
    setStatus("sending");
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("No Privy access token");
      if (wallets.length === 0) throw new Error("No wallet available");

      const payload = {
        type: "kpax-privy-auth" as const,
        privy_access_token: token,
        wallet_address: wallets[0].address,
        email: extractEmail(user) ?? null,
      };
      await sendToExtension(payload);
      setStatus("done");
      setTimeout(() => window.close(), 800);
    } catch (err) {
      setStatus("error");
      setErrorMessage(
        err instanceof Error ? err.message : "Failed to relay login to KPAX",
      );
    }
  }, [getAccessToken, user, wallets]);

  // Once Privy is ready, decide initial UI state.
  useEffect(() => {
    if (!ready) return;
    setStatus((prev) => {
      if (prev !== "loading") return prev;
      return authenticated ? "confirming" : "connect";
    });
  }, [ready, authenticated]);

  // After login() succeeds, wallet appears; auto-send.
  useEffect(() => {
    if (status !== "signing-in") return;
    if (!authenticated) return;
    if (wallets.length === 0) return;
    void sendSession();
  }, [status, authenticated, wallets, sendSession]);

  // After logout() completes during a switch, fall back to the connect screen.
  useEffect(() => {
    if (status !== "switching") return;
    if (!authenticated) setStatus("connect");
  }, [status, authenticated]);

  const handleConnect = () => {
    setStatus("signing-in");
    login();
  };

  const handleContinue = () => {
    void sendSession();
  };

  const handleSwitchAccount = async () => {
    setStatus("switching");
    try {
      await logout();
    } catch (err) {
      setStatus("error");
      setErrorMessage(
        err instanceof Error ? err.message : "Failed to sign out",
      );
    }
  };

  const handleRetry = () => {
    setErrorMessage(null);
    setStatus(authenticated ? "confirming" : "connect");
  };

  // ----- render -----

  if (!EXTENSION_ID) {
    return (
      <Frame>
        <h1>Missing extension id</h1>
        <p>Open this page from the KPAX Chrome extension.</p>
      </Frame>
    );
  }

  if (status === "loading") {
    return (
      <Frame>
        <p className="muted">Loading…</p>
      </Frame>
    );
  }

  if (status === "sending") {
    return (
      <Frame>
        <h1>Connecting KPAX…</h1>
        <p className="muted">Handing session back to the extension.</p>
      </Frame>
    );
  }

  if (status === "done") {
    return (
      <Frame>
        <h1>All set!</h1>
        <p className="muted">You can close this window.</p>
      </Frame>
    );
  }

  if (status === "switching") {
    return (
      <Frame>
        <p className="muted">Signing out…</p>
      </Frame>
    );
  }

  if (status === "error") {
    return (
      <Frame>
        <h1>Something went wrong</h1>
        <p className="muted">{errorMessage}</p>
        <button onClick={handleRetry}>Try again</button>
        {authenticated && (
          <button className="ghost" onClick={handleSwitchAccount}>
            Sign out
          </button>
        )}
      </Frame>
    );
  }

  if (status === "confirming") {
    const label =
      extractDisplayLabel(user) ??
      (wallets[0]?.address ? shortAddress(wallets[0].address) : "this wallet");
    return (
      <Frame>
        <h1>Welcome back</h1>
        <p className="muted">
          Signed in as <strong>{label}</strong>.
        </p>
        <button onClick={handleContinue}>Continue</button>
        <button className="ghost" onClick={handleSwitchAccount}>
          Use a different account
        </button>
      </Frame>
    );
  }

  // "connect" (or fallback): show the Connect button.
  return (
    <Frame>
      <h1>Connect to KPAX Ball</h1>
      <p className="muted">
        Connect your Polymarket wallet to unlock AI analysis and Lending.
        Supports MetaMask, Coinbase Wallet, WalletConnect, Rainbow, Rabby.
      </p>
      <button onClick={handleConnect}>Connect wallet</button>
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="frame">
      <div className="logo">K</div>
      {children}
    </div>
  );
}

function shortAddress(addr: string): string {
  if (!addr || addr.length < 10) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

/** Extract an email from whichever Privy login provider was used. */
function extractEmail(user: unknown): string | null {
  if (!user || typeof user !== "object") return null;
  const u = user as Record<string, any>;
  return (
    u.email?.address ??
    u.google?.email ??
    u.apple?.email ??
    u.discord?.email ??
    null
  );
}

/**
 * Pick the nicest label to show the user on "Welcome back".
 * Priority: email > OAuth username/handle > wallet address.
 */
function extractDisplayLabel(user: unknown): string | null {
  if (!user || typeof user !== "object") return null;
  const u = user as Record<string, any>;
  const email = extractEmail(u);
  if (email) return email;
  return (
    u.google?.name ??
    u.apple?.name ??
    u.twitter?.username ??
    u.discord?.username ??
    u.farcaster?.username ??
    u.github?.username ??
    null
  );
}

async function sendToExtension(payload: {
  type: "kpax-privy-auth";
  privy_access_token: string;
  wallet_address: string;
  email: string | null;
}): Promise<void> {
  if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
    throw new Error("Not opened from a Chrome extension");
  }
  return new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendMessage(EXTENSION_ID!, payload, () => {
        const err = chrome.runtime.lastError;
        if (err) reject(new Error(err.message || "sendMessage failed"));
        else resolve();
      });
    } catch (e) {
      reject(e);
    }
  });
}
