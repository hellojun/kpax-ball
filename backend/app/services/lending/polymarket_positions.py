"""Fetch a user's Polymarket CTF positions and annotate them with
KPAX Lending eligibility (league tier + max borrowable)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.services.lending.config import (
    LEAGUE_TIERS,
    MIN_HOURS_BEFORE_KICKOFF_TO_BORROW,
)
from app.services.lending.league_tier import classify_league_tier

logger = logging.getLogger(__name__)

POLYMARKET_DATA_API = "https://data-api.polymarket.com"

# Football / soccer keyword detection — used to distinguish between
# "this is a football market we don't yet support (unsupported_league)" and
# "this isn't football at all (non_football)" so the Assets page can show
# a different reason + CTA per case.
_FOOTBALL_HINTS = re.compile(
    r"\b(football|soccer|fc|fcb|epl|premier|laliga|la-liga|bundesliga|"
    r"serie-?a|ligue-?1|ucl|champions-league|world-cup|world\s+cup|"
    r"euros?|copa|mls|fa-cup|wsl)\b",
    re.IGNORECASE,
)

# Inferred reasons — kept as plain strings so the FE can branch on them
# without a shared enum schema. Matches design/prototype-v1.html card states.
INELIGIBLE_NON_FOOTBALL = "non_football"
INELIGIBLE_UNSUPPORTED_LEAGUE = "unsupported_league"
INELIGIBLE_ZERO_VALUE = "zero_value"
INELIGIBLE_KICKOFF_TOO_CLOSE = "kickoff_too_close"


_KICKOFF_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def kickoff_from_slug(slug: str | None) -> datetime | None:
    """Heuristic: pull a YYYY-MM-DD anywhere inside a Polymarket event slug
    (e.g. `epl-wol-tot-2026-04-25` or `epl-not-new-2026-05-10-more-markets`)
    and return 14:00 UTC on that date. The 14:00 placeholder is intentional
    until we wire Polymarket Gamma for precise kickoff — picker pre-flight
    and `prepare_borrow` share the same heuristic so their decisions agree.
    Returns None when no parseable date is present."""
    if not slug:
        return None
    m = _KICKOFF_DATE_RE.search(slug)
    if m is None:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 14, 0, 0)
    except ValueError:
        return None


async def fetch_user_positions(wallet_address: str) -> list[dict[str, Any]]:
    """Return raw positions from Polymarket Data API. Empty list on error."""
    url = f"{POLYMARKET_DATA_API}/positions"
    # sizeThreshold filters out dust positions; keep it small so single-share
    # moneyline buys still show up.
    params = {"user": wallet_address, "sizeThreshold": "0.01"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("polymarket positions fetch failed for %s: %s", wallet_address, exc)
        return []

    logger.info(
        "polymarket positions for %s: got %d rows",
        wallet_address,
        len(data) if isinstance(data, list) else 0,
    )
    return data if isinstance(data, list) else []


def annotate_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add `league_tier`, `borrow_eligible`, `max_borrowable_usd` to each position.

    Polymarket Data API returns keys like:
      - asset: CTF tokenId (string)
      - conditionId
      - size: shares (number)
      - avgPrice, curPrice: per-share price
      - title, eventSlug, slug
      - outcome: "Yes" / "No" or the chosen outcome name
    """
    enriched: list[dict[str, Any]] = []
    for p in positions:
        size = _to_float(p.get("size"))
        cur_price = _to_float(p.get("curPrice") or p.get("avgPrice"))
        value_usd = size * cur_price if size and cur_price else 0.0

        # MVP: 拼多个字段做联赛分档关键词检索 — 单看 title 会漏掉
        # 像 "Will Wolverhampton Wanderers FC win..." 这种没明写联赛的 sub-market；
        # eventSlug (`epl-wol-tot-...`) 里通常有 `epl` / `ucl` 之类前缀。
        competition_hint = " ".join(
            str(v) for v in [
                p.get("competition"),
                p.get("eventTitle"),
                p.get("title"),
                p.get("eventSlug"),
                p.get("slug"),
            ] if v
        )
        tier = classify_league_tier(competition_hint)

        max_ltv = LEAGUE_TIERS[tier].max_ltv if tier else 0.0
        max_borrowable = round(value_usd * max_ltv, 2)
        # Pre-flight kickoff gate — same window the prepare_borrow endpoint
        # enforces. Without this the picker happily lets the user click
        # "去借款" on a position that's about to revert with an HTTP 400
        # the moment they click "Borrow" inside the dialog.
        kickoff = kickoff_from_slug(p.get("eventSlug") or p.get("slug"))
        kickoff_blocked = (
            kickoff is not None
            and kickoff
            < datetime.utcnow() + timedelta(hours=MIN_HOURS_BEFORE_KICKOFF_TO_BORROW)
        )
        eligible = tier is not None and value_usd > 0 and not kickoff_blocked

        # Classify *why* this position is ineligible so the Assets page can
        # show a different card variant + CTA per reason. Order matters: a
        # zero-value position counts as "no value" first; then we ask "is it
        # football at all"; finally fall back to "football but unsupported
        # league". Kickoff window check runs LAST — only a position that's
        # otherwise borrowable can be blocked by the imminent-match rule.
        ineligible_reason: str | None
        if eligible:
            ineligible_reason = None
        elif value_usd <= 0:
            ineligible_reason = INELIGIBLE_ZERO_VALUE
        elif tier is None and _FOOTBALL_HINTS.search(competition_hint):
            ineligible_reason = INELIGIBLE_UNSUPPORTED_LEAGUE
        elif tier is None:
            ineligible_reason = INELIGIBLE_NON_FOOTBALL
        else:
            ineligible_reason = INELIGIBLE_KICKOFF_TOO_CLOSE

        enriched.append(
            {
                "asset": p.get("asset"),
                "condition_id": p.get("conditionId"),
                "event_slug": p.get("eventSlug") or p.get("slug"),
                "title": p.get("title"),
                "outcome": p.get("outcome"),
                "size": size,
                "current_price": cur_price,
                "value_usd": round(value_usd, 2),
                "competition_hint": competition_hint.strip() or None,
                "league_tier": tier,
                "borrow_eligible": eligible,
                "ineligible_reason": ineligible_reason,
                "max_borrowable_usd": max_borrowable,
            }
        )
    return enriched


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
