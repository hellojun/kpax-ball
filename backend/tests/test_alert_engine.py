"""Alert engine — uniqueness + delivered_at flow."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.lending_alert import LendingAlert
from app.services.lending import alert_engine


pytestmark = pytest.mark.usefixtures("patch_session_factory")


async def test_emits_ltv_80_above_liquidation(session, seed_loan, fake_price):
    """tier 1 liquidation_ltv = 0.80. price=0.5 → LTV=0.80 → ltv_80 alert."""
    await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)
    await alert_engine._tick()

    rows = (await session.execute(select(LendingAlert))).scalars().all()
    types = {r.alert_type for r in rows}
    assert "ltv_80" in types
    assert "ltv_70" not in types, "ltv_80 supersedes ltv_70"


async def test_emits_ltv_70_in_warning_band(session, seed_loan, fake_price):
    """tier 1 warning_ltv = 0.70. principal=400, shares=1000, price=0.55 →
    LTV ≈ 0.727 ∈ [0.70, 0.80) → ltv_70 only."""
    await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.55)
    await alert_engine._tick()

    rows = (await session.execute(select(LendingAlert))).scalars().all()
    types = [r.alert_type for r in rows]
    assert "ltv_70" in types
    assert "ltv_80" not in types


async def test_emits_kickoff_2h_inside_window(session, seed_loan, fake_price):
    kickoff = datetime.utcnow() + timedelta(hours=1, minutes=30)
    await seed_loan(
        session,
        match_kickoff_at=kickoff,
        principal=10,
        collateral_shares=1000,  # LTV very low → no LTV alert
    )
    fake_price.set(0.9)
    await alert_engine._tick()

    rows = (await session.execute(select(LendingAlert))).scalars().all()
    types = {r.alert_type for r in rows}
    assert "kickoff_2h" in types


async def test_emits_kickoff_4h_in_4h_band(session, seed_loan, fake_price):
    kickoff = datetime.utcnow() + timedelta(hours=3)
    await seed_loan(
        session,
        match_kickoff_at=kickoff,
        principal=10,
        collateral_shares=1000,
    )
    fake_price.set(0.9)
    await alert_engine._tick()
    rows = (await session.execute(select(LendingAlert))).scalars().all()
    types = {r.alert_type for r in rows}
    assert "kickoff_4h" in types
    assert "kickoff_2h" not in types


# ---------- idempotency ----------


async def test_repeated_tick_does_not_duplicate_alert(
    session, seed_loan, fake_price
):
    """UNIQUE(loan_id, alert_type) makes the alert at-most-once across ticks."""
    await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)
    await alert_engine._tick()
    await alert_engine._tick()
    await alert_engine._tick()
    rows = (
        await session.execute(
            select(LendingAlert).where(LendingAlert.alert_type == "ltv_80")
        )
    ).scalars().all()
    assert len(rows) == 1


# ---------- delivered_at default null ----------


async def test_new_alert_has_null_delivered_at(session, seed_loan, fake_price):
    await seed_loan(session, principal=400, collateral_shares=1000)
    fake_price.set(0.5)
    await alert_engine._tick()
    row = (await session.execute(select(LendingAlert))).scalars().first()
    assert row is not None
    assert row.delivered_at is None, "new alerts start un-delivered"


# ---------- non-active loans ignored ----------


async def test_non_active_loans_ignored(session, seed_loan, fake_price):
    await seed_loan(
        session,
        principal=400,
        collateral_shares=1000,
        status="repaid",
    )
    fake_price.set(0.5)
    await alert_engine._tick()

    rows = (await session.execute(select(LendingAlert))).scalars().all()
    assert rows == []
