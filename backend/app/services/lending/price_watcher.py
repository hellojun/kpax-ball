"""Polymarket CTF price watcher.

For an active loan we need the **current** market price of its CTF
collateral (not the price at borrow time, which is frozen on Loan.entry_price).
This module is the single source of truth for "what is this token worth right
now?" — Keeper liquidation, dashboard health bars, and pre-liquidation alerts
all read from here.

Source: Polymarket Gamma API event endpoint, which returns each market's
`clobTokenIds` and parallel `outcomePrices` arrays. We map our token id to
its index, then read the matching price.

We deliberately use Gamma rather than the CLOB midpoint endpoint because
Gamma covers more markets (some closed/illiquid markets have no order book
but still have a last-known outcome price), and one Gamma call returns all
markets in an event so multiple loans on the same event share a fetch.

Caching: 60s in-memory TTL keyed by (slug, token_id). Short enough that
liquidation calls aren't acting on stale prices; long enough that frontend
reads don't hammer Gamma. Multi-process deployments can layer Redis here
later — the public function signature stays.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
PRICE_TTL_SECONDS = 60

# (slug, token_id) -> (price, fetched_at_unix)
_price_cache: dict[tuple[str, str], tuple[float, float]] = {}


async def get_current_price(token_id: str, slug: str) -> float | None:
    """Return current Polymarket outcome price of a CTF token, or None if
    we couldn't resolve it (slug missing, market closed, network error)."""
    if not token_id or not slug:
        return None

    now = time.time()
    cached = _price_cache.get((slug, token_id))
    if cached and now - cached[1] < PRICE_TTL_SECONDS:
        return cached[0]

    price = await _fetch_price_via_gamma(token_id, slug)
    if price is not None:
        _price_cache[(slug, token_id)] = (price, now)
    return price


async def _fetch_price_via_gamma(token_id: str, slug: str) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{GAMMA_API}/events", params={"slug": slug})
            resp.raise_for_status()
            events = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Gamma price fetch failed slug=%s: %s", slug, exc)
        return None

    if not isinstance(events, list) or not events:
        return None

    target = str(token_id)
    for market in events[0].get("markets", []):
        ids = _maybe_parse(market.get("clobTokenIds"))
        prices = _maybe_parse(market.get("outcomePrices"))
        if not isinstance(ids, list) or not isinstance(prices, list):
            continue
        for idx, tid in enumerate(ids):
            if str(tid) == target and idx < len(prices):
                try:
                    return float(prices[idx])
                except (TypeError, ValueError):
                    return None
    return None


def _maybe_parse(v: Any) -> Any:
    """Gamma sometimes returns these arrays as JSON-encoded strings."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return v


def health_status(ltv: float, warning_ltv: float, liquidation_ltv: float) -> str:
    """Bucket a loan's current LTV into a health label for UI + alerts.

      healthy    LTV < 0.85 × warning_ltv
      caution    0.85 × warning_ltv ≤ LTV < warning_ltv
      warn       warning_ltv ≤ LTV < liquidation_ltv
      liquidate  LTV ≥ liquidation_ltv
    """
    if ltv >= liquidation_ltv:
        return "liquidate"
    if ltv >= warning_ltv:
        return "warn"
    if ltv >= warning_ltv * 0.85:
        return "caution"
    return "healthy"
