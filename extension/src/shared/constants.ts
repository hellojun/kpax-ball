/** KPAX Ball backend API base URL */
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "https://kpax.bout.network";

/** Privy auth page (external page that runs Privy SDK and posts back to KPAX backend) */
export const PRIVY_AUTH_URL =
  import.meta.env.VITE_PRIVY_AUTH_URL || "https://kpax.bout.network/auth/";

/** Polymarket Gamma API */
export const POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com";

/** Polymarket CLOB API */
export const POLYMARKET_CLOB_URL = "https://clob.polymarket.com";

/** Cache TTL in milliseconds */
export const PREVIEW_CACHE_TTL = 60 * 60 * 1000; // 1 hour
export const DEEP_ANALYSIS_CACHE_TTL = 24 * 60 * 60 * 1000; // 24 hours
