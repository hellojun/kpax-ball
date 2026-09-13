import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { PrivyProvider } from "@privy-io/react-auth";
import App from "./App";
import "./styles.css";

const PRIVY_APP_ID = import.meta.env.VITE_PRIVY_APP_ID ?? "";

if (!PRIVY_APP_ID) {
  document.body.innerText =
    "Missing VITE_PRIVY_APP_ID. Set it in auth-page/.env and rebuild.";
} else {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <PrivyProvider
        appId={PRIVY_APP_ID}
        config={{
          // Wallet-only: KPAX Lending requires the user's own Polymarket signer
          // (MetaMask / WalletConnect-compatible). Email/Google login would
          // produce a fresh Privy embedded wallet that has no Polymarket history.
          loginMethods: ["wallet"],
          appearance: {
            theme: "dark",
            accentColor: "#3b82f6",
            logo: undefined,
            walletList: [
              "detected_wallets",
              "metamask",
              "coinbase_wallet",
              "wallet_connect",
              "rainbow",
              "rabby_wallet",
            ],
          },
          embeddedWallets: {
            createOnLogin: "off",
          },
        }}
      >
        <App />
      </PrivyProvider>
    </StrictMode>,
  );
}
