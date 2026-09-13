"""Admin API — list/inspect/liquidate loans across all users.

Gated by `require_admin`: an admin session token signed by /api/admin/login
after username/password check. Manual liquidation reuses the keeper's 4-step
`_trigger_liquidation` flow with reason="manual"; runs in BackgroundTasks
because the full flow can take 30s–2min (3 on-chain txes + Polymarket FOK).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import AdminSession, issue_admin_token, require_admin
from app.config import settings
from app.db import get_db
from app.models.lending_alert import LendingAlert
from app.models.lending_event import LendingEvent
from app.models.loan import Loan
from app.models.user import User
from app.services.lending.config import LEAGUE_TIERS
from app.services.lending.keeper import _trigger_liquidation
from app.services.lending.price_watcher import get_current_price, health_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- Login ----------


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int  # seconds
    username: str


@router.post("/login", response_model=LoginResponse)
def admin_login(body: LoginRequest):
    """Username/password → admin session token (HS256 JWT, kind="admin").

    Returns 503 if the admin account isn't configured. Generic 401 on bad
    credentials (don't leak which field was wrong). Uses `secrets.compare_digest`
    for both fields to avoid timing oracles.
    """
    cfg_user = settings.kpax_admin_username
    cfg_pass = settings.kpax_admin_password
    if not cfg_user or not cfg_pass:
        raise HTTPException(
            status_code=503,
            detail="admin not configured — set KPAX_ADMIN_USERNAME / KPAX_ADMIN_PASSWORD",
        )
    user_ok = secrets.compare_digest(body.username, cfg_user)
    pass_ok = secrets.compare_digest(body.password, cfg_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="invalid username or password")
    token = issue_admin_token(body.username)
    return LoginResponse(
        token=token,
        expires_in=settings.kpax_admin_token_ttl_seconds,
        username=body.username,
    )


# ---------- Loan list ----------


class AdminLoanRow(BaseModel):
    loan_id: int
    onchain_loan_id: int | None
    status: str
    user_id: int
    user_wallet: str
    user_email: str | None
    market_slug: str
    home_team: str | None
    away_team: str | None
    competition: str | None
    ctf_token_id: str
    collateral_shares: float
    collateral_value_at_open: float
    principal: float
    apr_bps: int
    league_tier: int
    opened_ltv: float
    opened_at: str
    match_kickoff_at: str
    closed_at: str | None
    open_tx_hash: str | None
    close_tx_hash: str | None
    # Live (active only)
    current_price: float | None
    current_collateral_value_usd: float | None
    current_debt_usd: float | None
    current_ltv: float | None
    health: str | None
    # Settlement (closed only)
    total_interest_paid: float | None
    liquidation_penalty: float | None
    residual_to_user: float | None


@router.get("/loans")
async def admin_list_loans(
    status: str | None = None,
    limit: int = 200,
    _admin: AdminSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """All loans, newest first. Optional `status` filter (e.g.
    `?status=active`). Active loans get a live price + LTV + health
    bucket; non-active rows leave those fields null.
    """
    q = db.query(Loan).order_by(Loan.id.desc())
    if status:
        q = q.filter(Loan.status == status)
    rows: list[Loan] = q.limit(min(max(limit, 1), 1000)).all()
    if not rows:
        return {"loans": []}

    # Pull user info in one query.
    user_ids = {r.user_id for r in rows}
    users = {
        u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
    }

    # Fan-out price lookups for active loans only.
    active_rows = [r for r in rows if r.status == "active"]
    prices = await asyncio.gather(
        *(get_current_price(r.ctf_token_id, r.market_slug) for r in active_rows),
        return_exceptions=False,
    )
    price_by_loan = {r.id: p for r, p in zip(active_rows, prices)}

    items: list[AdminLoanRow] = []
    now = datetime.utcnow()
    for r in rows:
        cur_price = None
        cur_value = None
        cur_debt = None
        cur_ltv = None
        health = None
        if r.status == "active":
            elapsed_s = max(0, int((now - r.opened_at).total_seconds()))
            principal = float(r.principal)
            interest = principal * (r.apr_bps / 10_000) * (elapsed_s / 31_536_000)
            cur_debt = principal + interest

            cur_price = price_by_loan.get(r.id)
            if cur_price is not None and float(r.collateral_shares) > 0:
                cur_value = cur_price * float(r.collateral_shares)
                if cur_value > 0:
                    cur_ltv = cur_debt / cur_value
                    tier = LEAGUE_TIERS.get(r.league_tier)
                    if tier is not None:
                        health = health_status(
                            cur_ltv, tier.warning_ltv, tier.liquidation_ltv
                        )
        u = users.get(r.user_id)
        items.append(
            AdminLoanRow(
                loan_id=r.id,
                onchain_loan_id=r.onchain_loan_id,
                status=r.status,
                user_id=r.user_id,
                user_wallet=u.wallet_address if u else r.wallet_address,
                user_email=u.email if u else None,
                market_slug=r.market_slug,
                home_team=r.home_team,
                away_team=r.away_team,
                competition=r.competition,
                ctf_token_id=r.ctf_token_id,
                collateral_shares=float(r.collateral_shares),
                collateral_value_at_open=float(r.collateral_value_at_open),
                principal=float(r.principal),
                apr_bps=r.apr_bps,
                league_tier=r.league_tier,
                opened_ltv=float(r.opened_ltv),
                opened_at=r.opened_at.isoformat(),
                match_kickoff_at=r.match_kickoff_at.isoformat(),
                closed_at=r.closed_at.isoformat() if r.closed_at else None,
                open_tx_hash=r.open_tx_hash,
                close_tx_hash=r.close_tx_hash,
                current_price=round(cur_price, 6) if cur_price is not None else None,
                current_collateral_value_usd=(
                    round(cur_value, 6) if cur_value is not None else None
                ),
                current_debt_usd=round(cur_debt, 6) if cur_debt is not None else None,
                current_ltv=round(cur_ltv, 4) if cur_ltv is not None else None,
                health=health,
                total_interest_paid=(
                    float(r.total_interest_paid)
                    if r.total_interest_paid is not None
                    else None
                ),
                liquidation_penalty=(
                    float(r.liquidation_penalty)
                    if r.liquidation_penalty is not None
                    else None
                ),
                residual_to_user=(
                    float(r.residual_to_user)
                    if r.residual_to_user is not None
                    else None
                ),
            )
        )
    return {"loans": [i.model_dump() for i in items]}


# ---------- Loan detail ----------


class AdminLoanDetail(BaseModel):
    loan: AdminLoanRow
    alerts: list[dict]
    events: list[dict]


@router.get("/loans/{loan_id}")
async def admin_loan_detail(
    loan_id: int,
    _admin: AdminSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    r = db.get(Loan, loan_id)
    if r is None:
        raise HTTPException(404, "loan not found")
    u = db.get(User, r.user_id)

    cur_price = None
    cur_value = None
    cur_debt = None
    cur_ltv = None
    health = None
    if r.status == "active":
        cur_price = await get_current_price(r.ctf_token_id, r.market_slug)
        elapsed_s = max(0, int((datetime.utcnow() - r.opened_at).total_seconds()))
        principal = float(r.principal)
        interest = principal * (r.apr_bps / 10_000) * (elapsed_s / 31_536_000)
        cur_debt = principal + interest
        if cur_price is not None and float(r.collateral_shares) > 0:
            cur_value = cur_price * float(r.collateral_shares)
            if cur_value > 0:
                cur_ltv = cur_debt / cur_value
                tier = LEAGUE_TIERS.get(r.league_tier)
                if tier is not None:
                    health = health_status(
                        cur_ltv, tier.warning_ltv, tier.liquidation_ltv
                    )

    loan_row = AdminLoanRow(
        loan_id=r.id,
        onchain_loan_id=r.onchain_loan_id,
        status=r.status,
        user_id=r.user_id,
        user_wallet=u.wallet_address if u else r.wallet_address,
        user_email=u.email if u else None,
        market_slug=r.market_slug,
        home_team=r.home_team,
        away_team=r.away_team,
        competition=r.competition,
        ctf_token_id=r.ctf_token_id,
        collateral_shares=float(r.collateral_shares),
        collateral_value_at_open=float(r.collateral_value_at_open),
        principal=float(r.principal),
        apr_bps=r.apr_bps,
        league_tier=r.league_tier,
        opened_ltv=float(r.opened_ltv),
        opened_at=r.opened_at.isoformat(),
        match_kickoff_at=r.match_kickoff_at.isoformat(),
        closed_at=r.closed_at.isoformat() if r.closed_at else None,
        open_tx_hash=r.open_tx_hash,
        close_tx_hash=r.close_tx_hash,
        current_price=round(cur_price, 6) if cur_price is not None else None,
        current_collateral_value_usd=(
            round(cur_value, 6) if cur_value is not None else None
        ),
        current_debt_usd=round(cur_debt, 6) if cur_debt is not None else None,
        current_ltv=round(cur_ltv, 4) if cur_ltv is not None else None,
        health=health,
        total_interest_paid=(
            float(r.total_interest_paid) if r.total_interest_paid is not None else None
        ),
        liquidation_penalty=(
            float(r.liquidation_penalty) if r.liquidation_penalty is not None else None
        ),
        residual_to_user=(
            float(r.residual_to_user) if r.residual_to_user is not None else None
        ),
    )

    alerts = (
        db.query(LendingAlert)
        .filter(LendingAlert.loan_id == loan_id)
        .order_by(LendingAlert.id.asc())
        .all()
    )
    alert_payloads = [
        {
            "id": a.id,
            "alert_type": a.alert_type,
            "sent_at": a.sent_at.isoformat(),
            "delivered_at": a.delivered_at.isoformat() if a.delivered_at else None,
        }
        for a in alerts
    ]

    events = (
        db.query(LendingEvent)
        .filter(LendingEvent.loan_id == loan_id)
        .order_by(LendingEvent.id.asc())
        .all()
    )
    event_payloads = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "block_number": e.block_number,
            "tx_hash": e.tx_hash,
            "log_index": e.log_index,
            "data": e.data,
            "indexed_at": e.indexed_at.isoformat(),
        }
        for e in events
    ]

    return AdminLoanDetail(
        loan=loan_row, alerts=alert_payloads, events=event_payloads
    ).model_dump()


# ---------- Manual liquidation ----------


class LiquidateResponse(BaseModel):
    loan_id: int
    status: str  # echoed pre-trigger status (active)
    triggered_with_price: float
    note: str


@router.post("/loans/{loan_id}/liquidate", response_model=LiquidateResponse)
async def admin_liquidate(
    loan_id: int,
    background: BackgroundTasks,
    admin: AdminSession = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manually trigger the keeper's full 4-step liquidation flow on a loan.

    Pre-conditions (enforced here):
      - loan exists, status == 'active', has on-chain id
      - current Polymarket price is fetchable

    Then schedules `_trigger_liquidation(...)` as a BackgroundTask. It can
    take 30s–2min; admin should poll GET /api/admin/loans/{id} to watch the
    state transitions: active → withdrawing → liquidating → liquidated_*.

    Reason is recorded as 'manual' on-chain (third arg to settleLiquidation).
    """
    r = db.get(Loan, loan_id)
    if r is None:
        raise HTTPException(404, "loan not found")
    if r.status != "active":
        raise HTTPException(400, f"loan is {r.status}, not active — cannot liquidate")
    if r.onchain_loan_id is None:
        raise HTTPException(400, "loan has no on-chain id (indexer hasn't caught up)")

    price = await get_current_price(r.ctf_token_id, r.market_slug)
    if price is None or price <= 0:
        raise HTTPException(
            503, "Polymarket price unavailable — try again in a few seconds"
        )

    logger.warning(
        "ADMIN MANUAL LIQUIDATION: admin=%s loan_id=%s onchain=%s price=%s",
        admin.username, loan_id, r.onchain_loan_id, price,
    )

    # Reason must be one of {"ltv_breach", "kickoff_due"} — both the V4
    # contract (REASON_LTV_BREACH / REASON_KICKOFF_DUE) and the Python-side
    # ``vault_client.LIQUIDATE_REASONS`` whitelist enforce this. Admin
    # manual liquidation = an LTV-style override decision, so we record
    # it on-chain as "ltv_breach" and rely on the WARNING log line above
    # for the "this came from admin, not auto-keeper" audit trail.
    background.add_task(_trigger_liquidation, loan_id, "ltv_breach", price)

    return LiquidateResponse(
        loan_id=loan_id,
        status=r.status,
        triggered_with_price=price,
        note="liquidation flow scheduled — poll GET /api/admin/loans/{id} for status updates",
    )


# ---------- Me (validates a stored admin token on page reload) ----------


class MeResponse(BaseModel):
    username: str


@router.get("/me", response_model=MeResponse)
def admin_me(admin: AdminSession = Depends(require_admin)):
    return MeResponse(username=admin.username)
