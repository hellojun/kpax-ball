import { POLYMARKET_GAMMA_URL, POLYMARKET_CLOB_URL } from "./constants";

/** Fetch event data from Polymarket Gamma API */
export async function fetchEvent(slug: string) {
  const resp = await fetch(
    `${POLYMARKET_GAMMA_URL}/events?slug=${encodeURIComponent(slug)}`
  );
  if (!resp.ok) return null;
  const data = await resp.json();
  return Array.isArray(data) ? data[0] : data;
}

/** Fetch market data from Polymarket Gamma API */
export async function fetchMarket(slug: string) {
  const resp = await fetch(
    `${POLYMARKET_GAMMA_URL}/markets?slug=${encodeURIComponent(slug)}`
  );
  if (!resp.ok) return null;
  const data = await resp.json();
  return Array.isArray(data) ? data[0] : data;
}

/** Fetch order book from CLOB API */
export async function fetchOrderBook(tokenId: string) {
  const resp = await fetch(
    `${POLYMARKET_CLOB_URL}/book?token_id=${encodeURIComponent(tokenId)}`
  );
  if (!resp.ok) return null;
  return resp.json();
}

/** Fetch price history from CLOB API */
export async function fetchPriceHistory(conditionId: string) {
  const resp = await fetch(
    `${POLYMARKET_CLOB_URL}/prices-history?market=${encodeURIComponent(conditionId)}&interval=max&fidelity=60`
  );
  if (!resp.ok) return null;
  return resp.json();
}
