"""Finalize a loan from its on-chain `settleLiquidation` receipt.

Used by:
  - `scripts/finalize_settled_loan.py` — admin-driven one-shot recovery
  - `worker.stuck_liquidating_reaper_loop` — never roll back a `liquidating`
    row whose close_tx already confirmed; finalize it directly so the row
    reaches a terminal state without waiting for the (potentially lagging)
    event indexer.

Both call sites converge on `_apply_loan_liquidated` so the field math
matches `event_indexer._on_loan_liquidated` exactly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.lending_event import LendingEvent
from app.models.loan import Loan
from app.services.lending.event_decoder import (
    LOAN_LIQUIDATED_TOPIC0,
    LoanLiquidatedEvent,
    _decode_loan_liquidated_log,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _hex_int(v: Any) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.startswith("0x"):
        return int(v, 16)
    return int(v or 0)


async def _get_receipt(rpc_url: str, tx_hash: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getTransactionReceipt",
                "params": [tx_hash],
            },
        )
        r.raise_for_status()
        return r.json().get("result")


def _normalize_tx_hash(tx_hash: str) -> str:
    return tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash


async def fetch_settle_receipt(
    rpc_url: str, tx_hash: str
) -> dict | None:
    """Fetch a receipt and confirm it's a successful tx; otherwise None.
    Receipts that exist but reverted (status=0x0) return None — the caller
    treats that the same as 'tx never mined'."""
    receipt = await _get_receipt(rpc_url, _normalize_tx_hash(tx_hash))
    if receipt is None:
        return None
    if receipt.get("status") not in ("0x1", 1, "0x01"):
        return None
    return receipt


def extract_liquidated_log(
    receipt: dict, vault_addr: str
) -> tuple[LoanLiquidatedEvent, int, int] | None:
    """Find the `LoanLiquidated` log under `vault_addr` in a receipt and
    decode it. Returns (event, block_number, log_index) or None if absent.
    Settle txes that succeed but emit no LoanLiquidated (shouldn't happen
    under a healthy contract) return None — let the caller decide."""
    vault = vault_addr.lower()
    for log in receipt.get("logs") or []:
        if (log.get("address") or "").lower() != vault:
            continue
        topics = log.get("topics") or []
        if not topics or topics[0].lower() != LOAN_LIQUIDATED_TOPIC0.lower():
            continue
        ev = _decode_loan_liquidated_log(log)
        block_number = _hex_int(receipt.get("blockNumber"))
        log_index = _hex_int(log.get("logIndex"))
        return ev, block_number, log_index
    return None


def apply_liquidation_to_loan(loan: Loan, ev: LoanLiquidatedEvent) -> str:
    """Mutate a Loan row to reflect a confirmed LoanLiquidated event.
    Returns the terminal status string for logging."""
    terminal = (
        "liquidated_ltv" if ev.reason == "ltv_breach" else "liquidated_kickoff"
    )
    loan.status = terminal
    loan.closed_at = datetime.utcnow()
    loan.residual_to_user = Decimal(ev.residual_to_borrower) / Decimal(10**6)
    # The contract's settle path lumps interest + penalty into to_treasury;
    # we don't get the split back from the event. Mirror the indexer's
    # repaid path by recording the lump under interest_paid.
    loan.total_interest_paid = Decimal(ev.to_treasury) / Decimal(10**6)
    loan.liquidation_penalty = Decimal(0)
    loan.liquidating_at = None
    return terminal


async def finalize_from_close_tx(
    session: AsyncSession, loan: Loan
) -> str | None:
    """If `loan.close_tx_hash` confirmed and emits LoanLiquidated, finalize
    the row and write the matching `lending_events` audit row. Returns the
    terminal status on success, None if the tx isn't confirmed or no
    matching log was found.

    Caller is responsible for `session.commit()`. The function does not
    open its own transaction — it expects to run inside the caller's UoW.
    """
    if loan.close_tx_hash is None:
        return None
    rpc_url = settings.polygon_rpc_url
    vault_addr = (settings.kpax_vault_address or "").lower()
    if not vault_addr:
        return None
    try:
        receipt = await fetch_settle_receipt(rpc_url, loan.close_tx_hash)
    except Exception as exc:
        logger.warning(
            "finalize: receipt fetch failed loan=%s tx=%s: %s",
            loan.id, loan.close_tx_hash, exc,
        )
        return None
    if receipt is None:
        return None

    extracted = extract_liquidated_log(receipt, vault_addr)
    if extracted is None:
        return None
    ev, block_number, log_index = extracted

    if loan.onchain_loan_id is not None and int(loan.onchain_loan_id) != int(ev.loan_id):
        logger.error(
            "finalize: onchain_loan_id mismatch db=%s event=%s — refusing",
            loan.onchain_loan_id, ev.loan_id,
        )
        return None

    terminal = apply_liquidation_to_loan(loan, ev)

    tx_hash = _normalize_tx_hash(loan.close_tx_hash)
    existing = (
        await session.execute(
            select(LendingEvent.id).where(
                LendingEvent.tx_hash == tx_hash,
                LendingEvent.log_index == log_index,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            LendingEvent(
                loan_id=loan.id,
                event_type="LoanLiquidated",
                block_number=block_number,
                tx_hash=tx_hash,
                log_index=log_index,
                data={
                    "loan_id": int(ev.loan_id),
                    "reason": ev.reason,
                    "actual_proceeds": int(ev.actual_proceeds),
                    "to_lp": int(ev.to_lp),
                    "to_treasury": int(ev.to_treasury),
                    "residual_to_borrower": int(ev.residual_to_borrower),
                },
            )
        )
    return terminal
