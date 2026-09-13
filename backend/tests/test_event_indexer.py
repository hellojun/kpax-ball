"""Event indexer — state machine + idempotency on (tx_hash, log_index).

We test `_process_log` directly with synthetic log dicts. ABI encoding for the
non-indexed payload is delegated to `eth_abi.encode` so the same path
`_decode_*` exercises in production runs here too.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.lending_event import LendingEvent
from app.models.loan import Loan
from app.services.lending import event_indexer

from _log_helpers import (
    _hex_word,
    make_loan_liquidated_log,
    make_loan_opened_log,
    make_loan_repaid_log,
)

pytestmark = pytest.mark.usefixtures("patch_session_factory")


# ---------- LoanOpened: pending → active backfill ----------


async def test_loan_opened_flips_pending_to_active(session, seed_loan):
    loan = await seed_loan(
        session,
        status="pending",
        onchain_loan_id=None,
        open_tx_hash="0xtxhash-open-1",
    )
    log = make_loan_opened_log(
        loan_id=42, tx_hash="0xtxhash-open-1", log_index=3, block_number=100
    )

    await event_indexer._process_log(session, log)
    await session.commit()

    await session.refresh(loan)
    assert loan.status == "active"
    assert loan.onchain_loan_id == 42


# ---------- LoanRepaid → repaid ----------


async def test_loan_repaid_marks_repaid(session, seed_loan):
    loan = await seed_loan(session, onchain_loan_id=7, status="active")
    log = make_loan_repaid_log(
        loan_id=7, tx_hash="0xtxhash-repaid", log_index=1, block_number=200
    )

    await event_indexer._process_log(session, log)
    await session.commit()

    await session.refresh(loan)
    assert loan.status == "repaid"
    assert loan.closed_at is not None
    assert float(loan.total_interest_paid) == 1.0  # 1_000_000 / 1e6


# ---------- LoanLiquidated → liquidated_ltv / liquidated_kickoff ----------


async def test_loan_liquidated_ltv_marks_liquidated_ltv(session, seed_loan):
    loan = await seed_loan(session, onchain_loan_id=11, status="liquidating")
    log = make_loan_liquidated_log(
        loan_id=11,
        tx_hash="0xliq",
        log_index=0,
        block_number=300,
        reason="ltv_breach",
        actual_proceeds=400_000_000,
        to_lp=250_000_000,
        to_treasury=100_000_000,
        residual=50_000_000,
    )
    await event_indexer._process_log(session, log)
    await session.commit()

    await session.refresh(loan)
    assert loan.status == "liquidated_ltv"
    assert loan.closed_at is not None
    assert float(loan.residual_to_user) == 50.0


async def test_loan_liquidated_kickoff_marks_liquidated_kickoff(
    session, seed_loan
):
    loan = await seed_loan(session, onchain_loan_id=12, status="liquidating")
    log = make_loan_liquidated_log(
        loan_id=12,
        tx_hash="0xliq2",
        log_index=0,
        block_number=300,
        reason="kickoff_due",
        actual_proceeds=200_000_000,
        to_lp=200_000_000,
        to_treasury=0,
        residual=0,
    )
    await event_indexer._process_log(session, log)
    await session.commit()

    await session.refresh(loan)
    assert loan.status == "liquidated_kickoff"


# ---------- idempotency ----------


async def test_duplicate_log_does_not_create_duplicate_event(
    session, seed_loan
):
    """Replaying the same (tx_hash, log_index) must not create a second
    LendingEvent row — UNIQUE constraint is the floor."""
    await seed_loan(session, onchain_loan_id=99, status="active")
    log = make_loan_repaid_log(
        loan_id=99, tx_hash="0xdupe", log_index=2, block_number=400
    )

    await event_indexer._process_log(session, log)
    await session.commit()
    await event_indexer._process_log(session, log)
    await session.commit()

    rows = (await session.execute(select(LendingEvent))).scalars().all()
    assert len(rows) == 1


async def test_unknown_topic_is_ignored(session, seed_loan):
    """Logs whose topic0 isn't in our whitelist are silently skipped."""
    log = {
        "topics": ["0x" + "00" * 32, _hex_word(1)],
        "data": "0x",
        "transactionHash": "0xunknown",
        "logIndex": "0x0",
        "blockNumber": "0x0",
        "address": "0x" + "ee" * 20,
    }
    await event_indexer._process_log(session, log)
    await session.commit()
    rows = (await session.execute(select(LendingEvent))).scalars().all()
    assert rows == []


# ---------- LoanRepaid for unknown onchain id ----------


async def test_loan_repaid_for_unknown_loan_still_logs_event(session):
    """Even if we don't have a Loan row for this onchain id, LendingEvent
    audit row should still be written so the indexer cursor advances."""
    log = make_loan_repaid_log(
        loan_id=9999,
        tx_hash="0xorphan",
        log_index=0,
        block_number=500,
    )
    await event_indexer._process_log(session, log)
    await session.commit()

    rows = (await session.execute(select(LendingEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].loan_id is None  # FK left null
