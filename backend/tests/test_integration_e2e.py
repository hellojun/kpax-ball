"""End-to-end integration test — V3 5-step liquidation closed loop.

Walks the full path with FakeVaultClient + FakePolymarketSeller + FakePMRelayer:

  1. Seed an active Loan with onchain_loan_id wired up
  2. Drop price below tier.liquidation_ltv so keeper fires
  3. `keeper._tick()`:
       step1 → vault.withdrawCtfForLiquidation     → status: active → withdrawing
       step2 → vault.transfer_ctf_to_proxy         (CTF: keeper EOA → keeper proxy)
       step3 → polymarket CLOB V2 sell             (off-chain, pUSD lands in proxy)
       step4 → pm_relayer.transfer_erc20(pUSD)     (proxy → vault)
       step5 → vault.settleLiquidation             → status: withdrawing → liquidating
  4. Simulate chain emitting LoanLiquidated → event_indexer
  5. Verify final state: status=liquidated_ltv, residual filled, audit row in lending_events
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.lending_alert import LendingAlert
from app.models.lending_event import LendingEvent
from app.models.loan import Loan
from app.services.lending import alert_engine, event_indexer, keeper

from _log_helpers import make_loan_liquidated_log

pytestmark = pytest.mark.usefixtures("patch_session_factory")


async def test_e2e_ltv_breach_full_loop(
    session, seed_loan, fake_vault, fake_polymarket, fake_relayer, fake_price
):
    onchain_id = 4242
    loan = await seed_loan(
        session,
        principal=400,
        collateral_shares=1000,
        onchain_loan_id=onchain_id,
        ctf_token_id="123456789",  # step 2 calls int() on this; must parse cleanly
        status="active",
    )
    fake_price.set(0.5)
    fake_polymarket.proceeds_e6 = 500_000_000  # 500 pUSD (1:1 with USDC.e)

    # Steps 1–5 happen in one tick.
    await keeper._tick()

    vault_steps = [c["step"] for c in fake_vault.calls]
    assert vault_steps == ["withdraw", "ctf_to_proxy", "settle"]
    assert len(fake_polymarket.calls) == 1
    relayer_steps = [c["step"] for c in fake_relayer.calls]
    assert relayer_steps == ["relayer_erc20"]

    # Step 4 fires a pUSD transfer (proxy → vault).
    relayer_call = fake_relayer.calls[0]
    assert relayer_call["amount"] == 500_000_000

    settle_call = fake_vault.calls[2]
    assert settle_call["loan_id"] == onchain_id
    assert settle_call["reason"] == "ltv_breach"
    assert settle_call["actual_proceeds_e6"] == 500_000_000

    await session.refresh(loan)
    assert loan.status == "liquidating"
    assert loan.withdrawn_at is not None
    assert loan.liquidating_at is not None
    settle_tx = loan.close_tx_hash
    assert settle_tx and settle_tx.startswith("0xfake-settle-")

    # Step 5: chain emits LoanLiquidated. Indexer transitions to terminal.
    log = make_loan_liquidated_log(
        loan_id=onchain_id,
        tx_hash=settle_tx,
        log_index=0,
        block_number=1_000,
        reason="ltv_breach",
        actual_proceeds=500_000_000,
        to_lp=400_000_000,
        to_treasury=10_000_000,
        residual=90_000_000,
    )
    await event_indexer._process_log(session, log)
    await session.commit()
    await session.refresh(loan)

    assert loan.status == "liquidated_ltv"
    assert loan.closed_at is not None
    assert float(loan.residual_to_user) == 90.0

    audit = (
        await session.execute(
            select(LendingEvent).where(LendingEvent.tx_hash == settle_tx)
        )
    ).scalar_one()
    assert audit.event_type == "LoanLiquidated"
    assert audit.loan_id == loan.id


async def test_e2e_kickoff_due_full_loop(
    session, seed_loan, fake_vault, fake_polymarket, fake_relayer, fake_price
):
    onchain_id = 7777
    loan = await seed_loan(
        session,
        principal=10,
        collateral_shares=1000,
        match_kickoff_at=datetime.utcnow() + timedelta(hours=1, minutes=30),
        onchain_loan_id=onchain_id,
        ctf_token_id="987654321",
        status="active",
    )
    fake_price.set(0.9)
    fake_polymarket.proceeds_e6 = 900_000_000

    await keeper._tick()

    settle_calls = [c for c in fake_vault.calls if c["step"] == "settle"]
    assert len(settle_calls) == 1
    assert settle_calls[0]["reason"] == "kickoff_due"
    assert settle_calls[0]["loan_id"] == onchain_id

    await session.refresh(loan)
    assert loan.status == "liquidating"

    log = make_loan_liquidated_log(
        loan_id=onchain_id,
        tx_hash=loan.close_tx_hash,
        log_index=0,
        block_number=2_000,
        reason="kickoff_due",
        actual_proceeds=900_000_000,
        to_lp=10_000_000,
        to_treasury=18_000_000,
        residual=872_000_000,
    )
    await event_indexer._process_log(session, log)
    await session.commit()
    await session.refresh(loan)

    assert loan.status == "liquidated_kickoff"
    assert float(loan.residual_to_user) == 872.0


async def test_e2e_alerts_alongside_keeper(
    session, seed_loan, fake_vault, fake_polymarket, fake_relayer, fake_price
):
    """ltv_80 alert + keeper liquidation must coexist in a single tick."""
    await seed_loan(
        session,
        principal=400,
        collateral_shares=1000,
        onchain_loan_id=99,
        ctf_token_id="555555555",
        status="active",
    )
    fake_price.set(0.5)
    fake_polymarket.proceeds_e6 = 500_000_000

    # Alert engine BEFORE keeper, while loan is still 'active' (alert engine
    # filters by status='active').
    await alert_engine._tick()
    await keeper._tick()

    alerts = (await session.execute(select(LendingAlert))).scalars().all()
    types = {a.alert_type for a in alerts}
    assert "ltv_80" in types
    assert all(a.delivered_at is None for a in alerts)

    settle_calls = [c for c in fake_vault.calls if c["step"] == "settle"]
    assert len(settle_calls) == 1
