"""Lending worker entrypoint — `python -m app.worker`.

Runs four async loops in one event loop, but in a process separate from web
(see plan v0.2 §2):

  1. event_indexer_loop   — pull eth_getLogs, write LendingEvent + reflect Loan
  2. keeper_loop          — scan active loans, fire liquidate
  3. alert_engine_loop    — emit at-most-once alerts to lending_alerts
  4. stuck_liquidating_reaper — every 10 min, roll any `liquidating` row back
                                to `active` if its tx hasn't confirmed in 15 min

Single-instance protection: PG advisory lock (decimal `worker_advisory_lock_key`).
A second worker connection bounces immediately. On SQLite (dev) the lock call is
a no-op — only one process should ever run worker locally anyway.

Graceful shutdown: SIGTERM/SIGINT cancels in order
   keeper → indexer → alert → reaper
so no new liquidations fire after shutdown begins, and any in-flight events
finish persisting before the indexer task winds down.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta

import sentry_sdk
from sqlalchemy import select, text

from app.config import settings
from app.db import AsyncSessionLocal, async_engine
from app.models.loan import Loan
from app.services.lending.alert_engine import alert_engine_loop
from app.services.lending.event_indexer import event_indexer_loop
from app.services.lending.keeper import keeper_loop
from app.services.lending.loan_finalizer import finalize_from_close_tx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("kpax.worker")


# ---------- single-instance protection ----------


async def _acquire_advisory_lock_or_exit() -> None:
    """PG: take a session-scoped advisory lock; exit if another worker holds it.
    SQLite/non-PG: no-op (dev only — don't run two local workers)."""
    if not settings.database_url.lower().startswith(("postgres", "postgresql")):
        logger.warning(
            "advisory lock skipped (db is not Postgres) — do not run two workers"
        )
        return
    key = int(settings.worker_advisory_lock_key)
    # Important: hold the lock on a *connection* that lives for the whole worker
    # lifetime. We dispose it on shutdown along with `async_engine`.
    conn = await async_engine.connect()
    got = await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
    held = got.scalar()
    if not held:
        await conn.close()
        logger.error(
            "another worker holds the advisory lock (key=%s); exiting", key
        )
        sys.exit(1)
    # Stash the connection so it stays open for the worker's life. PG releases
    # the lock automatically when the connection drops (whether on graceful
    # shutdown or process kill).
    _LOCK_HOLDERS.append(conn)
    logger.info("advisory lock acquired key=%s", key)


_LOCK_HOLDERS: list = []


# ---------- stuck-liquidating reaper ----------

REAPER_INTERVAL_S = 600  # 10 min
STUCK_AFTER = timedelta(minutes=15)


async def stuck_liquidating_reaper_loop() -> None:
    sentry_sdk.set_tag("loop", "reaper")
    logger.info(
        "stuck-liquidating reaper starting, interval=%ss threshold=%s",
        REAPER_INTERVAL_S, STUCK_AFTER,
    )
    while True:
        try:
            await _reap_stuck()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reaper tick failed")
            sentry_sdk.capture_exception()
        await asyncio.sleep(REAPER_INTERVAL_S)


async def _reap_stuck() -> None:
    """Per-row triage of stuck `liquidating` loans.

    For each row whose `liquidating_at` is older than `STUCK_AFTER`:
      - If `close_tx_hash` confirmed on-chain and emitted `LoanLiquidated`:
        finalize directly (the indexer is lagging — don't wait for it).
      - Otherwise (tx never mined, dropped, or reverted): roll back to
        `active` so the next keeper tick can retry.

    The previous implementation rolled every stale row back unconditionally
    and lost terminal-state info because indexer lag exceeded the 15-min
    threshold (see incident with loan #70, 2026-05-09).
    """
    cutoff = datetime.utcnow() - STUCK_AFTER
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stuck = (
                await session.execute(
                    select(Loan)
                    .where(
                        Loan.status == "liquidating",
                        Loan.liquidating_at.is_not(None),
                        Loan.liquidating_at < cutoff,
                    )
                    .with_for_update()
                )
            ).scalars().all()
            finalized = 0
            rolled_back = 0
            for loan in stuck:
                terminal = await finalize_from_close_tx(session, loan)
                if terminal is not None:
                    finalized += 1
                    logger.warning(
                        "reaper finalized stuck loan=%s onchain=%s → %s "
                        "(indexer lagged behind confirmed close_tx)",
                        loan.id, loan.onchain_loan_id, terminal,
                    )
                    continue
                loan.status = "active"
                loan.liquidating_at = None
                rolled_back += 1
                logger.warning(
                    "reaper rolled back stuck loan=%s onchain=%s → active "
                    "(close_tx not confirmed or no LoanLiquidated log)",
                    loan.id, loan.onchain_loan_id,
                )
        if finalized or rolled_back:
            logger.info(
                "reaper tick: finalized=%s rolled_back=%s",
                finalized, rolled_back,
            )


# ---------- main ----------


async def _shutdown(tasks: list[asyncio.Task]) -> None:
    """Cancel in deliberate order: keeper first (no new liquidates), indexer
    second (let in-flight events finish), then alert + reaper."""
    order = ["keeper", "indexer", "alert", "reaper"]
    by_name = {t.get_name(): t for t in tasks}
    for name in order:
        t = by_name.get(name)
        if t is not None and not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            server_name="kpax-worker",
        )
    sentry_sdk.set_tag("service", "worker")

    await _acquire_advisory_lock_or_exit()

    tasks = [
        asyncio.create_task(event_indexer_loop(), name="indexer"),
        asyncio.create_task(keeper_loop(), name="keeper"),
        asyncio.create_task(alert_engine_loop(), name="alert"),
        asyncio.create_task(stuck_liquidating_reaper_loop(), name="reaper"),
    ]

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("worker up: %s tasks running", len(tasks))
    await stop_event.wait()
    logger.info("shutdown signal received; cancelling tasks")
    await _shutdown(tasks)

    # Release advisory lock + dispose engine.
    for conn in _LOCK_HOLDERS:
        try:
            await conn.close()
        except Exception:
            pass
    await async_engine.dispose()
    logger.info("worker stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
