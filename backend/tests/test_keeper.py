"""Keeper tick tests — V3 5-step liquidation (post pUSD migration).

The keeper drives a 5-step state machine:
   step1 → vault.withdrawCtfForLiquidation       (CTF: vault → keeper EOA)
   step2 → vault_client.transfer_ctf_to_proxy    (CTF: keeper EOA → keeper proxy)
   step3 → polymarket sell                       (off-chain — pUSD → proxy)
   step4 → pm_relayer.transfer_erc20(pUSD)       (pUSD: proxy → vault)
   step5 → vault.settleLiquidation               (vault unwraps pUSD → USDC.e + distributes)

Rollback paths:
   step2 fail → vault.returnCtfFromKeeper           (CTF: keeper EOA → vault)
   step3 fail → relayer.transfer_erc1155 + returnCtf (CTF: proxy → keeper EOA → vault)

These tests assert the right calls land in the right order through `fake_vault.calls`,
`fake_polymarket.calls`, and `fake_relayer.calls`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.loan import Loan
from app.services.lending import keeper

pytestmark = pytest.mark.usefixtures("patch_session_factory")


def _vault_steps(fake_vault) -> list[str]:
    return [c["step"] for c in fake_vault.calls]


# ---------- LTV thresholds ----------


async def test_ltv_below_threshold_does_not_trigger(
    session, seed_loan, fake_vault, fake_polymarket, fake_price
):
    """tier 1 liquidation_ltv = 0.80. principal=400 / shares=1000 / price=0.6 →
    cv=600, LTV=0.667 < 0.80 → no trigger."""
    await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.6)
    await keeper._tick()
    assert fake_vault.calls == []
    assert fake_polymarket.calls == []


async def test_ltv_at_threshold_triggers_full_5_step(
    session, seed_loan, fake_vault, fake_polymarket, fake_relayer, fake_price
):
    """price=0.5 → LTV exactly 0.80 → trigger. Verify all 5 steps fired."""
    loan = await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)
    fake_polymarket.proceeds_e6 = 500_000_000  # 500 pUSD (1:1 with USDC.e)

    await keeper._tick()

    assert _vault_steps(fake_vault) == ["withdraw", "ctf_to_proxy", "settle"]
    assert len(fake_polymarket.calls) == 1
    assert [c["step"] for c in fake_relayer.calls] == ["relayer_erc20"]

    withdraw = fake_vault.calls[0]
    assert withdraw["loan_id"] == loan.onchain_loan_id
    assert withdraw["current_price_e6"] == 500_000

    sell = fake_polymarket.calls[0]
    assert sell["shares"] == 1_000  # user-size; SDK ×1e6 internally

    relayer_call = fake_relayer.calls[0]
    assert relayer_call["amount"] == 500_000_000

    settle = fake_vault.calls[2]
    assert settle["reason"] == "ltv_breach"
    assert settle["actual_proceeds_e6"] == 500_000_000

    await session.refresh(loan)
    assert loan.status == "liquidating"  # awaiting LoanLiquidated indexer
    assert loan.close_tx_hash and loan.close_tx_hash.startswith("0xfake-settle-")
    assert loan.withdrawn_at is not None
    assert loan.liquidating_at is not None


# ---------- kickoff window ----------


async def test_kickoff_inside_2h_triggers(
    session, seed_loan, fake_vault, fake_polymarket, fake_relayer, fake_price
):
    kickoff_soon = datetime.utcnow() + timedelta(hours=1, minutes=30)
    await seed_loan(
        session,
        match_kickoff_at=kickoff_soon,
        principal=10,
        collateral_shares=1000,
    )
    fake_price.set(0.9)
    fake_polymarket.proceeds_e6 = 900_000_000

    await keeper._tick()

    settle_calls = [c for c in fake_vault.calls if c["step"] == "settle"]
    assert any(c["reason"] == "kickoff_due" for c in settle_calls)


async def test_kickoff_outside_window_does_not_trigger(
    session, seed_loan, fake_vault, fake_polymarket, fake_price
):
    kickoff_far = datetime.utcnow() + timedelta(hours=5)
    await seed_loan(
        session,
        match_kickoff_at=kickoff_far,
        principal=10,
        collateral_shares=1000,
    )
    fake_price.set(0.9)

    await keeper._tick()

    assert all(c.get("reason") != "kickoff_due" for c in fake_vault.calls)


# ---------- idempotency ----------


async def test_repeated_tick_does_not_double_send(
    session, seed_loan, fake_vault, fake_polymarket, fake_relayer, fake_price
):
    """After a successful tick, status='liquidating'. Second tick must skip."""
    loan = await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)
    fake_polymarket.proceeds_e6 = 500_000_000

    await keeper._tick()
    settle_count_after_first = sum(1 for c in fake_vault.calls if c["step"] == "settle")
    assert settle_count_after_first == 1

    await keeper._tick()
    settle_count_after_second = sum(1 for c in fake_vault.calls if c["step"] == "settle")
    assert settle_count_after_second == 1, "second tick must not re-fire"

    await session.refresh(loan)
    assert loan.status == "liquidating"


# ---------- sell failure → rollback ----------


async def test_sell_failure_returns_ctf_and_resets_loan(
    session, seed_loan, fake_vault, fake_relayer, fake_price, monkeypatch
):
    """If Polymarket sell fails (slippage / no liquidity), the keeper must yank
    CTF back from the proxy → keeper EOA → vault and reset status → active."""
    from app.services.lending import polymarket_seller as ps_mod

    failing_seller = ps_mod.FakePolymarketSeller(succeed=False, error="no liquidity")
    monkeypatch.setattr(ps_mod, "_seller", failing_seller)

    loan = await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)

    await keeper._tick()

    # Keeper's vault_client steps: withdraw → ctf_to_proxy → return.
    # In between, the proxy→EOA hop goes through the relayer (CTF rollback).
    steps = _vault_steps(fake_vault)
    assert steps == ["withdraw", "ctf_to_proxy", "return"]
    assert [c["step"] for c in fake_relayer.calls] == ["relayer_erc1155"]
    assert len(failing_seller.calls) == 1

    await session.refresh(loan)
    assert loan.status == "active", "rollback must reset status to active"
    assert loan.withdrawn_at is None


# ---------- step1 failure ----------


async def test_step1_withdraw_failure_keeps_active(
    session, seed_loan, fake_polymarket, fake_price, monkeypatch
):
    """If withdrawCtfForLiquidation reverts, no CTF moved. Status stays
    active so the next tick retries."""
    from app.services.lending import vault_client
    from conftest import FakeVaultClient

    fake = FakeVaultClient(fail_step="withdraw")
    monkeypatch.setattr(vault_client, "_vault_client", fake)

    loan = await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)

    await keeper._tick()

    assert _vault_steps(fake) == ["withdraw"]  # only withdraw attempted
    assert fake_polymarket.calls == [], "sell must NOT happen if withdraw failed"

    await session.refresh(loan)
    assert loan.status == "active"


# ---------- mock mode (no keeper key) ----------


async def test_no_keeper_key_does_not_block_loop(
    session, seed_loan, fake_polymarket, fake_price, monkeypatch
):
    from app.services.lending import vault_client
    from conftest import FakeVaultClient

    fake = FakeVaultClient(no_key=True)
    monkeypatch.setattr(vault_client, "_vault_client", fake)

    loan = await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)
    await keeper._tick()  # must not raise

    await session.refresh(loan)
    assert loan.status == "active"


# ---------- defensive ----------


async def test_skips_loans_without_onchain_id(
    session, seed_loan, fake_vault, fake_polymarket, fake_price
):
    await seed_loan(
        session,
        principal=400,
        collateral_shares=1000,
        onchain_loan_id=None,
    )
    fake_price.set(0.5)
    await keeper._tick()
    assert fake_vault.calls == []


async def test_price_unavailable_skips_loan(
    session, seed_loan, fake_vault, fake_polymarket, fake_price
):
    await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(None)
    await keeper._tick()
    assert fake_vault.calls == []
