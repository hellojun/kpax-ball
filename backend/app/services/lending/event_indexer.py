"""LendingVault event indexer (worker loop).

Pulls `eth_getLogs` from `MAX(lending_events.block_number) + 1` (or
`settings.lending_vault_deploy_block` on a fresh DB) up to `latest`,
decodes via `event_decoder`, writes audit rows to `lending_events`,
and reflects state on the corresponding `Loan` row.

State reflection is the safety-net for keeper-driven liquidations:
- `LoanOpened`     — confirm-borrow is the primary writer; we backfill if missed
- `LoanRepaid`     — `repaid` + `closed_at` + `total_interest_paid`
- `LoanLiquidated` — `liquidated_ltv`|`liquidated_kickoff` + `residual_to_user`
                     + `closed_at` (this is the *only* writer for liquidated_*)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import sentry_sdk
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal
from app.models.indexer_cursor import IndexerCursor
from app.models.lending_event import LendingEvent
from app.models.loan import Loan
from app.services.lending.event_decoder import (
    EVENT_TOPIC_MAP,
    CtfReturnedFromKeeperEvent,
    LiquidationStartedEvent,
    LoanLiquidatedEvent,
    LoanOpenedEvent,
    LoanRepaidEvent,
    decode_event_log,
    event_type_for_topic0,
)

logger = logging.getLogger(__name__)

# Cap a single eth_getLogs window. publicnode chokes well before 5k blocks
# (read timeout @ ~1.3k); 200 is a safe ceiling for a steady tick, but it
# still times out on cold starts or under load — we shrink dynamically
# down to MIN_BATCH on ReadTimeout and grow back to MAX_BATCH on success.
# Move to Alchemy / QuickNode archive nodes for faster catch-up — see
# plan §K1.
GET_LOGS_BATCH_BLOCKS_MAX = 200
GET_LOGS_BATCH_BLOCKS_MIN = 25
# publicnode (and most free Polygon RPCs) prune logs older than a few hours.
# If our cursor is older than the retention window, eth_getLogs fails. We
# clamp the cursor forward and accept that any LoanOpened/Repaid/Liquidated
# events older than this are lost — for the lending worker that's fine: the
# borrow/repay UX paths already write Loan rows independently, and indexer
# is the safety net + reflection layer, not the source of truth.
RPC_PRUNE_BUFFER_BLOCKS = 5_000


# ---------- raw RPC ----------


async def _rpc(rpc_url: str, method: str, params: list[Any]) -> Any:
    # 60s timeout: publicnode.com can take 30-45s on a 200-block eth_getLogs
    # range with our 4-topic OR filter, especially under load.
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        resp.raise_for_status()
        body = resp.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("message", "rpc error"))
    return body.get("result")


def _hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.startswith("0x"):
        return int(value, 16)
    return int(value or 0)


# ---------- indexer state ----------


CURSOR_NAME = "lending"


async def _start_block(session: AsyncSession) -> int:
    """Read the persisted cursor; fall back to MAX(lending_events.block_number)
    if the cursor row hasn't been seeded yet (DB upgraded from a build that
    didn't have IndexerCursor); finally fall back to the configured deploy
    block on a truly fresh DB."""
    row = await session.execute(
        select(IndexerCursor).where(IndexerCursor.name == CURSOR_NAME)
    )
    cur = row.scalar_one_or_none()
    if cur is not None:
        return int(cur.next_block)
    row = await session.execute(select(func.max(LendingEvent.block_number)))
    last = row.scalar()
    if last is not None:
        return int(last) + 1
    return max(0, int(settings.lending_vault_deploy_block))


async def _save_cursor(session: AsyncSession, next_block: int) -> None:
    row = await session.execute(
        select(IndexerCursor).where(IndexerCursor.name == CURSOR_NAME)
    )
    cur = row.scalar_one_or_none()
    if cur is None:
        session.add(IndexerCursor(name=CURSOR_NAME, next_block=next_block))
    else:
        cur.next_block = next_block


async def _latest_block(rpc_url: str) -> int:
    return _hex_int(await _rpc(rpc_url, "eth_blockNumber", []))


async def _fetch_logs(
    rpc_url: str, from_block: int, to_block: int, vault_addr: str
) -> list[dict]:
    # OR-filter on topic0 across our white-listed events. (Pass list-of-list to
    # mean "topic0 ∈ {…}".)
    topics0 = sorted({t.lower() for t in EVENT_TOPIC_MAP.values()})
    params = [
        {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": vault_addr,
            "topics": [topics0],
        }
    ]
    return await _rpc(rpc_url, "eth_getLogs", params) or []


# ---------- per-log processing ----------


async def _process_log(session: AsyncSession, log: dict) -> None:
    """Decode + persist a single log. Wraps in a SAVEPOINT so one bad log
    doesn't poison the batch."""
    topics = log.get("topics") or []
    if not topics:
        return
    topic0 = topics[0]
    event_type = event_type_for_topic0(topic0)
    if event_type is None:
        return  # not one of ours

    tx_hash = log.get("transactionHash")
    log_index = _hex_int(log.get("logIndex"))
    block_number = _hex_int(log.get("blockNumber"))
    if not tx_hash:
        return

    # Short-circuit if we've already indexed this log.
    existing = await session.execute(
        select(LendingEvent.id).where(
            LendingEvent.tx_hash == tx_hash,
            LendingEvent.log_index == log_index,
        )
    )
    if existing.scalar() is not None:
        return

    try:
        decoded = decode_event_log(log)
    except Exception as exc:
        logger.warning("decode failed event=%s tx=%s: %s", event_type, tx_hash, exc)
        sentry_sdk.capture_exception(exc)
        decoded = None

    loan_db: Loan | None = None
    if decoded is not None and getattr(decoded, "loan_id", None) is not None:
        row = await session.execute(
            select(Loan).where(Loan.onchain_loan_id == int(decoded.loan_id))
        )
        loan_db = row.scalar_one_or_none()

    # Reflect state. Each handler is responsible for skipping no-ops.
    if isinstance(decoded, LoanOpenedEvent):
        loan_db = await _on_loan_opened(session, decoded, tx_hash) or loan_db
    elif isinstance(decoded, LoanRepaidEvent):
        await _on_loan_repaid(decoded, loan_db)
    elif isinstance(decoded, LoanLiquidatedEvent):
        await _on_loan_liquidated(decoded, loan_db)
    elif isinstance(decoded, LiquidationStartedEvent):
        await _on_liquidation_started(decoded, loan_db)
    elif isinstance(decoded, CtfReturnedFromKeeperEvent):
        await _on_ctf_returned(decoded, loan_db)
    # TreasurySet is audit-only — write LendingEvent row but no Loan mutation.

    try:
        async with session.begin_nested():
            session.add(
                LendingEvent(
                    loan_id=loan_db.id if loan_db else None,
                    event_type=event_type,
                    block_number=block_number,
                    tx_hash=tx_hash,
                    log_index=log_index,
                    data=_serialize(decoded) if decoded else {"raw_log": log},
                )
            )
    except IntegrityError:
        # Race with another process / replay — UNIQUE(tx_hash, log_index) wins.
        pass


# ---------- state reflection ----------


async def _on_loan_opened(
    session: AsyncSession, ev: LoanOpenedEvent, tx_hash: str
) -> Loan | None:
    """Safety net: if `confirm-borrow` failed to flip status, do it here."""
    row = await session.execute(
        select(Loan).where(Loan.open_tx_hash == tx_hash, Loan.status == "pending")
    )
    pending = row.scalar_one_or_none()
    if pending is not None:
        pending.status = "active"
        pending.onchain_loan_id = int(ev.loan_id)
        return pending
    # Already active but onchain_loan_id never got filled (rare): backfill.
    row = await session.execute(
        select(Loan).where(
            Loan.open_tx_hash == tx_hash,
            Loan.onchain_loan_id.is_(None),
        )
    )
    leftover = row.scalar_one_or_none()
    if leftover is not None:
        leftover.onchain_loan_id = int(ev.loan_id)
        return leftover
    return None


async def _on_loan_repaid(ev: LoanRepaidEvent, loan_db: Loan | None) -> None:
    if loan_db is None:
        logger.warning("LoanRepaid but no Loan with onchain_loan_id=%s", ev.loan_id)
        return
    if loan_db.status not in ("active", "liquidating", "pending"):
        return  # already terminal
    loan_db.status = "repaid"
    loan_db.closed_at = datetime.utcnow()
    loan_db.total_interest_paid = Decimal(ev.interest_paid) / Decimal(10**6)


async def _on_loan_liquidated(ev: LoanLiquidatedEvent, loan_db: Loan | None) -> None:
    if loan_db is None:
        logger.warning("LoanLiquidated but no Loan with onchain_loan_id=%s", ev.loan_id)
        return
    # Accept all non-terminal states — keeper may have missed an intermediate
    # DB write but the chain event is authoritative.
    if loan_db.status in ("repaid", "liquidated_ltv", "liquidated_kickoff", "closed"):
        return
    # Delegate to the shared finalize routine so the indexer + worker reaper
    # + admin recovery script all agree on which Loan fields settle to which
    # event values. Previously this handler only wrote status + closed_at +
    # residual_to_user and left total_interest_paid / liquidation_penalty
    # NULL — diverged from apply_liquidation_to_loan and broke the loans
    # endpoint's settlement-flow display for keeper-completed liquidations.
    from app.services.lending.loan_finalizer import apply_liquidation_to_loan

    apply_liquidation_to_loan(loan_db, ev)


async def _on_liquidation_started(
    ev: LiquidationStartedEvent, loan_db: Loan | None
) -> None:
    """Safety net: keeper crashed between sending the on-chain tx and writing
    DB withdrawn_at. Indexer brings DB up to date."""
    if loan_db is None:
        return
    if loan_db.status == "active":
        loan_db.status = "withdrawing"
    if loan_db.withdrawn_at is None:
        loan_db.withdrawn_at = datetime.utcnow()


async def _on_ctf_returned(
    ev: CtfReturnedFromKeeperEvent, loan_db: Loan | None
) -> None:
    """Safety net for the rollback path."""
    if loan_db is None:
        return
    if loan_db.status == "withdrawing":
        loan_db.status = "active"
    loan_db.withdrawn_at = None


def _serialize(ev: object) -> dict:
    out: dict = {}
    for k, v in vars(ev).items():
        out[k] = v if isinstance(v, (int, str, float, bool)) else str(v)
    return out


# ---------- main loop ----------


def _resolve_vault_address() -> str:
    addr = settings.kpax_vault_address
    if addr:
        return addr
    from app.services.lending.config import LENDING_VAULT_ADDRESS

    return LENDING_VAULT_ADDRESS


async def event_indexer_loop() -> None:
    """Forever loop. Cancel-friendly via asyncio.CancelledError propagation.

    `batch_state` is mutated across ticks so the loop can shrink its
    `eth_getLogs` window on timeout and grow it back on success. Initial
    value is the upper bound; the loop drops it to MIN on `httpx.ReadTimeout`
    and restores it on a clean tick.
    """
    sentry_sdk.set_tag("loop", "indexer")
    interval = settings.event_indexer_interval_seconds
    rpc_url = settings.polygon_rpc_url
    vault_addr = _resolve_vault_address()
    logger.info("event_indexer starting vault=%s rpc=%s", vault_addr, rpc_url)

    batch_state = {"size": GET_LOGS_BATCH_BLOCKS_MAX}

    while True:
        try:
            await _tick(rpc_url, vault_addr, batch_state)
        except asyncio.CancelledError:
            raise
        except httpx.ReadTimeout:
            new_size = max(GET_LOGS_BATCH_BLOCKS_MIN, batch_state["size"] // 2)
            if new_size != batch_state["size"]:
                logger.warning(
                    "indexer eth_getLogs ReadTimeout — shrinking batch %s → %s",
                    batch_state["size"], new_size,
                )
                batch_state["size"] = new_size
            else:
                logger.warning(
                    "indexer eth_getLogs ReadTimeout at min batch %s — RPC is "
                    "overloaded, will retry next tick",
                    batch_state["size"],
                )
        except Exception:
            logger.exception("event_indexer tick failed")
            sentry_sdk.capture_exception()
        await asyncio.sleep(interval)


async def _tick(rpc_url: str, vault_addr: str, batch_state: dict) -> None:
    async with AsyncSessionLocal() as session:
        start = await _start_block(session)
        latest = await _latest_block(rpc_url)
        if start > latest:
            return
        if latest - start > RPC_PRUNE_BUFFER_BLOCKS:
            clamped = latest - RPC_PRUNE_BUFFER_BLOCKS
            logger.warning(
                "indexer cursor %s older than RPC prune window; clamping to %s "
                "(events in [%s, %s) are skipped)",
                start, clamped, start, clamped,
            )
            start = clamped
        batch = batch_state["size"]
        end = min(latest, start + batch - 1)
        behind = latest - end
        logger.info(
            "indexer tick: start=%s end=%s latest=%s batch=%s behind_after=%s",
            start, end, latest, batch, behind,
        )
        logs = await _fetch_logs(rpc_url, start, end, vault_addr)
        for log in logs:
            try:
                await _process_log(session, log)
            except Exception:
                logger.exception("process_log failed log=%s", log)
                sentry_sdk.capture_exception()
        # Persist cursor *after* the fetch succeeded so empty windows still
        # advance — without this, an empty range would re-scan forever
        # (`_start_block` previously derived from MAX(events) only).
        await _save_cursor(session, end + 1)
        await session.commit()
        if logs:
            logger.info(
                "indexed %s logs blocks %s..%s", len(logs), start, end,
            )
        # Successful tick — try growing the batch back toward MAX so a
        # transient slowdown doesn't permanently shrink throughput.
        if batch < GET_LOGS_BATCH_BLOCKS_MAX:
            grown = min(GET_LOGS_BATCH_BLOCKS_MAX, batch * 2)
            if grown != batch:
                logger.info("indexer batch growing %s → %s", batch, grown)
                batch_state["size"] = grown
