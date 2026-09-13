"""Alert engine — every `alert_engine_interval_seconds`, scans active loans
and emits at most-once alerts in 4 categories:

  * `kickoff_4h` — `match_kickoff_at` within (now+2h, now+4h]
  * `kickoff_2h` — `match_kickoff_at` within (now,    now+2h]
  * `ltv_70`     — current LTV ≥ tier.warning_ltv     (tier 1: 0.70)
  * `ltv_80`     — current LTV ≥ tier.liquidation_ltv (tier 1: 0.80, also keeper trigger)

Idempotency: `UNIQUE(loan_id, alert_type)` on `lending_alerts` makes each
alert at-most-once across the loan's lifetime. We use `INSERT … ON CONFLICT
DO NOTHING` on PG and catch `IntegrityError` on SQLite (via SAVEPOINT).
The alert sits in DB with `delivered_at IS NULL` until the Service Worker
polls `/api/lending/alerts/pending` and acks via `/alerts/ack`.

Note: alert thresholds are tier-aware (ltv_80 alert fires at 0.75 for tier 2,
at 0.65 for tier 3). The alert *type* is fixed for UI consistency.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal
from app.models.lending_alert import LendingAlert
from app.models.loan import Loan
from app.services.lending.config import (
    KICKOFF_BUFFER_HOURS,
    KICKOFF_WARN_HOURS,
    LEAGUE_TIERS,
)
from app.services.lending.price_watcher import get_current_price

logger = logging.getLogger(__name__)

ALERT_LTV_70 = "ltv_70"
ALERT_LTV_80 = "ltv_80"
ALERT_KICKOFF_4H = "kickoff_4h"
ALERT_KICKOFF_2H = "kickoff_2h"


# ---------- main loop ----------


async def alert_engine_loop() -> None:
    sentry_sdk.set_tag("loop", "alert")
    interval = settings.alert_engine_interval_seconds
    logger.info("alert_engine starting, interval=%ss", interval)

    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("alert_engine tick failed")
            sentry_sdk.capture_exception()
        await asyncio.sleep(interval)


async def _tick() -> None:
    async with AsyncSessionLocal() as session:
        candidates = await _gather_candidates(session)
        for loan_id, alert_type in candidates:
            await _try_emit(session, loan_id, alert_type)
        await session.commit()


# ---------- candidate gathering ----------


async def _gather_candidates(session: AsyncSession) -> list[tuple[int, str]]:
    result = await session.execute(select(Loan).where(Loan.status == "active"))
    loans = result.scalars().all()
    if not loans:
        return []

    now = datetime.utcnow()
    kickoff_2h_threshold = now + timedelta(hours=KICKOFF_BUFFER_HOURS)
    kickoff_4h_threshold = now + timedelta(hours=KICKOFF_WARN_HOURS)

    pairs = {(loan.market_slug, loan.ctf_token_id) for loan in loans}
    prices: dict[tuple[str, str], float | None] = {}
    for slug, token_id in pairs:
        prices[(slug, token_id)] = await get_current_price(token_id, slug)

    out: list[tuple[int, str]] = []
    for loan in loans:
        # Kickoff alerts: pick the strongest applicable bucket.
        if now < loan.match_kickoff_at:
            if loan.match_kickoff_at <= kickoff_2h_threshold:
                out.append((loan.id, ALERT_KICKOFF_2H))
            elif loan.match_kickoff_at <= kickoff_4h_threshold:
                out.append((loan.id, ALERT_KICKOFF_4H))

        # LTV alerts: ltv_80 supersedes ltv_70 — emit only the higher band.
        price = prices.get((loan.market_slug, loan.ctf_token_id))
        if price is None:
            continue
        ltv = _compute_ltv(loan, price)
        tier_cfg = LEAGUE_TIERS.get(int(loan.league_tier))
        if tier_cfg is None:
            continue
        if ltv >= tier_cfg.liquidation_ltv:
            out.append((loan.id, ALERT_LTV_80))
        elif ltv >= tier_cfg.warning_ltv:
            out.append((loan.id, ALERT_LTV_70))
    return out


def _compute_ltv(loan: Loan, current_price: float) -> float:
    shares = float(loan.collateral_shares)
    if shares <= 0 or current_price <= 0:
        return 1e9
    cv = shares * current_price
    return float(loan.principal) / cv if cv > 0 else 1e9


# ---------- emit ----------


async def _try_emit(session: AsyncSession, loan_id: int, alert_type: str) -> None:
    """Insert (loan_id, alert_type). UNIQUE makes this idempotent across ticks
    and worker restarts; SAVEPOINT keeps a duplicate from poisoning the batch."""
    try:
        async with session.begin_nested():
            session.add(LendingAlert(loan_id=loan_id, alert_type=alert_type))
        logger.info("alert emitted loan=%s type=%s", loan_id, alert_type)
    except IntegrityError:
        # Duplicate (loan_id, alert_type) — expected behaviour, just skip.
        pass
