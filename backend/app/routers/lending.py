"""KPAX Lending API (Sprint 1 + proxy detection + Sprint 2 borrow flow)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, current_user
from app.config import settings
from app.db import get_db
from app.models.lending_alert import LendingAlert
from app.models.lending_event import LendingEvent
from app.models.lending_tos import TosAcceptance
from app.models.loan import Loan
from app.services.lending.config import (
    APR_BPS,
    CURRENT_TOS_VERSION,
    LEAGUE_TIERS,
    MAX_BORROW_PER_USER_USD,
    MIN_HOURS_BEFORE_KICKOFF_TO_BORROW,
    config_payload,
)
from app.services.lending.polymarket_positions import (
    annotate_positions,
    fetch_user_positions,
    kickoff_from_slug,
)
from app.services.lending.event_decoder import (
    wait_for_loan_opened,
    wait_for_loan_repaid,
)
from app.services.lending.price_watcher import get_current_price, health_status
from app.services.lending.proxy_resolver import detect_proxy
from app.services.lending.risk_assessor import assess_risk, to_payload as risk_to_payload
from app.services.lending.vault_calldata import (
    encode_open_loan,
    encode_repay,
)

router = APIRouter(prefix="/api/lending", tags=["lending"])


# ---------- Config ----------


@router.get("/config")
def get_config():
    """Return LTV tiers, APR, ToS version, gating limits."""
    return config_payload()


class PoolBalanceResponse(BaseModel):
    """Live LP-pool capacity in USDC.e. Mirrors the vault's `lpPoolBalance`
    view — the contract reverts `openLoan` with InsufficientLiquidity when
    `principal > lpPoolBalance`, so the frontend uses this to clamp the
    borrow slider before the user pays gas to find out."""
    pool_balance_usd: float


@router.get("/pool-balance", response_model=PoolBalanceResponse)
async def get_pool_balance():
    from app.services.lending.vault_client import get_vault_client

    base_units = await get_vault_client().lp_pool_balance()
    # USDC.e is 6 decimals; round to 2 to match the rest of the dollar UI.
    return PoolBalanceResponse(pool_balance_usd=round(base_units / 1_000_000, 2))


# ---------- Terms of Service ----------


class TosStatus(BaseModel):
    current_version: str
    user_version: str | None
    accepted: bool


class TosAcceptRequest(BaseModel):
    tos_version: str


class TosAcceptResponse(BaseModel):
    accepted: bool
    tos_version: str


@router.get("/tos", response_model=TosStatus)
def get_tos_status(
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    latest = (
        db.query(TosAcceptance)
        .filter(TosAcceptance.user_id == user.user_id)
        .order_by(TosAcceptance.accepted_at.desc())
        .first()
    )
    if latest is None:
        return TosStatus(
            current_version=CURRENT_TOS_VERSION,
            user_version=None,
            accepted=False,
        )
    return TosStatus(
        current_version=CURRENT_TOS_VERSION,
        user_version=latest.tos_version,
        accepted=latest.tos_version == CURRENT_TOS_VERSION,
    )


@router.post("/tos/accept", response_model=TosAcceptResponse)
def accept_tos(
    body: TosAcceptRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Record user's acceptance of a specific ToS version.

    Idempotent: if the user already signed this version, return success without
    inserting a duplicate row.
    """
    existing = (
        db.query(TosAcceptance)
        .filter(
            TosAcceptance.user_id == user.user_id,
            TosAcceptance.tos_version == body.tos_version,
        )
        .first()
    )
    if existing is None:
        db.add(
            TosAcceptance(
                user_id=user.user_id,
                tos_version=body.tos_version,
                signature="clicked",
            )
        )
        db.commit()

    return TosAcceptResponse(accepted=True, tos_version=body.tos_version)


# ---------- Proxy detection ----------


class ProxyCandidatePayload(BaseModel):
    kind: str  # "safe" | "magic"
    address: str
    deployed: bool


class ProxyDetectionResponse(BaseModel):
    eoa: str
    kind: str  # "safe" | "magic" | "none"
    proxy: str | None
    candidates: list[ProxyCandidatePayload]


@router.get("/proxy", response_model=ProxyDetectionResponse)
async def get_proxy(user: CurrentUser = Depends(current_user)):
    """Derive the user's Polymarket proxy from their EOA + check on-chain.

    Returns:
      kind="safe"  → the user logged into Polymarket via MetaMask/external wallet
      kind="magic" → user logged in via email/Google → must export private key
                     into MetaMask before they can sign approvals
      kind="none"  → user never traded on Polymarket → no positions to borrow against
    """
    result = await detect_proxy(user.wallet_address, settings.polygon_rpc_url)
    return ProxyDetectionResponse(
        eoa=user.wallet_address,
        kind=result.kind,
        proxy=result.proxy,
        candidates=[
            ProxyCandidatePayload(kind=c.kind, address=c.address, deployed=c.deployed)
            for c in result.candidates
        ],
    )


# ---------- Positions ----------


@router.get("/positions")
async def get_positions(user: CurrentUser = Depends(current_user)):
    """Return the user's Polymarket CTF positions enriched with Lending
    eligibility (league tier + max borrowable).

    Pipeline:
      1. Derive user's Polymarket proxy address from their EOA.
      2. Query Polymarket Data API with the proxy (NOT the EOA — positions
         live in the proxy contract, not in the user's signing key).
      3. Annotate each position with league tier + borrow eligibility.

    Returns `proxy: null` and an empty positions list if the user has no
    deployed Polymarket proxy yet.
    """
    detection = await detect_proxy(user.wallet_address, settings.polygon_rpc_url)

    if detection.proxy is None:
        return {
            "wallet_address": user.wallet_address,
            "proxy": None,
            "proxy_kind": detection.kind,
            "positions": [],
            "eligible_count": 0,
            "eligible_total_value_usd": 0.0,
        }

    raw = await fetch_user_positions(detection.proxy)
    enriched = annotate_positions(raw)
    eligible = [p for p in enriched if p["borrow_eligible"]]
    return {
        "wallet_address": user.wallet_address,
        "proxy": detection.proxy,
        "proxy_kind": detection.kind,
        "positions": enriched,
        "eligible_count": len(eligible),
        "eligible_total_value_usd": round(sum(p["value_usd"] for p in eligible), 2),
    }


# ---------- Borrow: prepare ----------


class PrepareBorrowRequest(BaseModel):
    asset: str = Field(description="Polymarket CTF tokenId (decimal string)")
    principal_usd: float = Field(gt=0, description="USDC amount to borrow")


class PrepareBorrowResponse(BaseModel):
    loan_id: int
    vault_address: str
    to: str  # always == vault_address
    data: str  # 0x-prefixed calldata
    value: str  # always "0x0"
    chain_id: int  # 137 = Polygon mainnet
    # echoed back for the frontend's display + sanity check
    proxy: str  # PM Safe-style proxy holding the CTF (collateralSource)
    ctf_token_id: str
    shares: str
    principal_base_units: str
    match_kickoff_unix: int
    league_tier: int
    apr_bps: int


def _vault_address() -> str:
    from app.services.lending.config import LENDING_VAULT_ADDRESS
    return settings.kpax_vault_address or LENDING_VAULT_ADDRESS


@router.post("/prepare-borrow", response_model=PrepareBorrowResponse)
async def prepare_borrow(
    body: PrepareBorrowRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Validate the borrow intent and return calldata for `LendingVault.openLoan(...)`.
    Frontend hands the returned `{to, data, value}` to MetaMask via
    `eth_sendTransaction`. A `pending` loan row is created here; /confirm-borrow
    flips it to `active` once the on-chain LoanOpened event lands.
    """
    # 1. ToS gate.
    tos = (
        db.query(TosAcceptance)
        .filter(TosAcceptance.user_id == user.user_id, TosAcceptance.tos_version == CURRENT_TOS_VERSION)
        .first()
    )
    if tos is None:
        raise HTTPException(400, f"Sign Terms of Service ({CURRENT_TOS_VERSION}) first")

    # 1b. One-loan-per-CTF rule. `active` is a hard block — must repay first.
    active_existing = (
        db.query(Loan)
        .filter(
            Loan.user_id == user.user_id,
            Loan.ctf_token_id == body.asset,
            Loan.status == "active",
        )
        .first()
    )
    if active_existing is not None:
        raise HTTPException(
            400,
            f"You already have an active loan on this position (loan "
            f"#{active_existing.id}). Repay it first before borrowing again.",
        )

    # 1c. Sweep abandoned `pending` drafts on this user × CTF that never made
    # it on-chain. They accumulate every time the user clicks "Borrow" but
    # closes MetaMask without signing. Mark them as `failed` so they stop
    # cluttering the dashboard's "active" count and don't trip future guards.
    abandoned = (
        db.query(Loan)
        .filter(
            Loan.user_id == user.user_id,
            Loan.ctf_token_id == body.asset,
            Loan.status == "pending",
            Loan.open_tx_hash.is_(None),
        )
        .all()
    )
    for row in abandoned:
        row.status = "failed"
    if abandoned:
        db.commit()

    # 2. Re-derive proxy from EOA + fetch current positions.
    detection = await detect_proxy(user.wallet_address, settings.polygon_rpc_url)
    if detection.proxy is None or detection.kind != "safe":
        raise HTTPException(
            400,
            "No Safe-type Polymarket proxy detected for your wallet. "
            "(Magic-link users must export their private key to MetaMask first.)",
        )

    raw = await fetch_user_positions(detection.proxy)
    positions = annotate_positions(raw)
    match = next((p for p in positions if str(p.get("asset")) == body.asset), None)
    if match is None:
        raise HTTPException(404, "Asset not found in your current Polymarket positions")
    if not match["borrow_eligible"]:
        raise HTTPException(
            400,
            "This position isn't eligible: unsupported league or zero value.",
        )

    league_tier = int(match["league_tier"])
    tier_cfg = LEAGUE_TIERS[league_tier]
    value_usd = float(match["value_usd"])
    max_borrow_for_pos = value_usd * tier_cfg.max_ltv

    # 3. Validate principal vs LTV ceiling and per-user cap.
    if body.principal_usd > max_borrow_for_pos + 0.01:  # tiny epsilon for fp rounding
        raise HTTPException(
            400,
            f"Principal ${body.principal_usd:.2f} exceeds league {league_tier} LTV cap "
            f"({tier_cfg.max_ltv * 100:.0f}% of ${value_usd:.2f} = ${max_borrow_for_pos:.2f})",
        )
    if body.principal_usd > MAX_BORROW_PER_USER_USD:
        raise HTTPException(
            400,
            f"Principal exceeds the MVP per-user cap of ${MAX_BORROW_PER_USER_USD}",
        )

    # 4. Validate kickoff window (>= 24h before match).
    kickoff = kickoff_from_slug(match.get("event_slug"))
    if kickoff is None:
        # Conservative fallback: 7 days out. Still enforces the 24h gate.
        kickoff = datetime.utcnow() + timedelta(days=7)
    if kickoff < datetime.utcnow() + timedelta(hours=MIN_HOURS_BEFORE_KICKOFF_TO_BORROW):
        raise HTTPException(
            400,
            f"Match starts in <{MIN_HOURS_BEFORE_KICKOFF_TO_BORROW}h. Borrow earlier or pass.",
        )

    # 5. Encode V4 calldata (6-arg openLoan, USDC.e directly to borrower EOA).
    shares_int = int(Decimal(str(match["size"])) * Decimal(10) ** 6)  # CTF uses 6-dec like USDC
    principal_base = int(Decimal(str(body.principal_usd)) * Decimal(10) ** 6)  # USDC base units
    vault = _vault_address()
    calldata = encode_open_loan(
        collateral_source=detection.proxy,
        ctf_token_id=int(body.asset),
        shares=shares_int,
        principal=principal_base,
        match_kickoff=int(kickoff.timestamp()),
        league_tier=league_tier,
    )

    # 6. Insert pending Loan row. confirm-borrow will fill in onchain_loan_id +
    # open_tx_hash and flip status to 'active'.
    loan = Loan(
        user_id=user.user_id,
        wallet_address=user.wallet_address,
        ctf_token_id=body.asset,
        market_slug=match.get("event_slug") or "",
        home_team=None,
        away_team=None,
        competition=match.get("competition_hint"),
        collateral_shares=Decimal(str(match["size"])),
        collateral_value_at_open=Decimal(str(value_usd)),
        entry_price=Decimal(str(match.get("current_price") or 0)),
        principal=Decimal(str(body.principal_usd)),
        apr_bps=APR_BPS,
        league_tier=league_tier,
        opened_ltv=Decimal(str(round(body.principal_usd / value_usd, 4))) if value_usd > 0 else Decimal("0"),
        status="pending",
        match_kickoff_at=kickoff,
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)

    return PrepareBorrowResponse(
        loan_id=loan.id,
        vault_address=vault,
        to=vault,
        data=calldata,
        value="0x0",
        chain_id=137,
        proxy=detection.proxy,
        ctf_token_id=body.asset,
        shares=str(shares_int),
        principal_base_units=str(principal_base),
        match_kickoff_unix=int(kickoff.timestamp()),
        league_tier=league_tier,
        apr_bps=APR_BPS,
    )


# ---------- Borrow: confirm ----------


class ConfirmBorrowRequest(BaseModel):
    loan_id: int
    tx_hash: str


class ConfirmBorrowResponse(BaseModel):
    loan_id: int
    status: str  # "active" | "failed"
    onchain_loan_id: int | None
    open_tx_hash: str
    block_number: int


@router.post("/confirm-borrow", response_model=ConfirmBorrowResponse)
async def confirm_borrow(
    body: ConfirmBorrowRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Wait for the openLoan tx to mine, decode `LoanOpened`, flip the
    pending Loan row to active. Times out after 60s — frontend can retry."""
    loan = db.get(Loan, body.loan_id)
    if loan is None:
        raise HTTPException(404, "loan not found")
    if loan.user_id != user.user_id:
        raise HTTPException(403, "not your loan")
    if loan.status != "pending":
        # Idempotent: already confirmed.
        return ConfirmBorrowResponse(
            loan_id=loan.id,
            status=loan.status,
            onchain_loan_id=loan.onchain_loan_id,
            open_tx_hash=loan.open_tx_hash or body.tx_hash,
            block_number=0,
        )

    vault = _vault_address()
    try:
        result = await wait_for_loan_opened(
            settings.polygon_rpc_url, body.tx_hash, vault, timeout_s=60
        )
    except TimeoutError:
        raise HTTPException(202, "tx not mined yet — retry in a few seconds")

    if not result.success or result.loan_event is None:
        loan.status = "failed"
        loan.open_tx_hash = body.tx_hash
        db.commit()
        return ConfirmBorrowResponse(
            loan_id=loan.id,
            status="failed",
            onchain_loan_id=None,
            open_tx_hash=body.tx_hash,
            block_number=result.block_number,
        )

    ev = result.loan_event
    loan.status = "active"
    loan.onchain_loan_id = ev.loan_id
    loan.open_tx_hash = body.tx_hash
    db.commit()
    return ConfirmBorrowResponse(
        loan_id=loan.id,
        status="active",
        onchain_loan_id=ev.loan_id,
        open_tx_hash=body.tx_hash,
        block_number=result.block_number,
    )


# ---------- Loans list ----------


class LoanItem(BaseModel):
    loan_id: int
    onchain_loan_id: int | None
    status: str
    market_slug: str
    title: str | None
    ctf_token_id: str
    collateral_shares: float
    collateral_value_at_open: float
    principal: float
    apr_bps: int
    league_tier: int
    opened_ltv: float
    opened_at: str  # ISO
    match_kickoff_at: str  # ISO
    closed_at: str | None
    open_tx_hash: str | None
    close_tx_hash: str | None
    # Live, only for active loans
    current_debt_usd: float | None
    interest_so_far_usd: float | None
    # Live collateral mark, fetched from Polymarket Gamma (active only)
    current_collateral_price: float | None
    current_collateral_value_usd: float | None
    current_ltv: float | None
    health_status: str | None  # healthy | caution | warn | liquidate
    # Final settlement, only for repaid / liquidated loans
    total_interest_paid: float | None
    # Liquidation flow breakdown — only for liquidated_* loans, sourced from
    # the `LoanLiquidated` event row in lending_events. All in USDC.e.
    liq_proceeds_usd: float | None  # actual_proceeds — gross CTF sale
    liq_principal_repaid_usd: float | None  # to_lp
    liq_to_treasury_usd: float | None  # to_treasury (interest + penalty)
    liq_residual_usd: float | None  # residual_to_borrower


def _liq_breakdown(data: dict | None) -> dict:
    """Convert a `LoanLiquidated` event JSON payload (raw 6-decimal ints) into
    the four LoanItem `liq_*_usd` fields. Returns all-None when the event is
    missing — keeps the field set stable so pydantic doesn't choke."""
    if not data:
        return {
            "liq_proceeds_usd": None,
            "liq_principal_repaid_usd": None,
            "liq_to_treasury_usd": None,
            "liq_residual_usd": None,
        }

    def _to_usd(v) -> float | None:
        if v is None:
            return None
        try:
            return round(int(v) / 1_000_000, 6)
        except (TypeError, ValueError):
            return None

    return {
        "liq_proceeds_usd": _to_usd(data.get("actual_proceeds")),
        "liq_principal_repaid_usd": _to_usd(data.get("to_lp")),
        "liq_to_treasury_usd": _to_usd(data.get("to_treasury")),
        "liq_residual_usd": _to_usd(data.get("residual_to_borrower")),
    }


@router.get("/loans")
async def list_loans(
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """All of the user's loans, newest first. For active ones, also include
    live debt + Polymarket-mark collateral value + LTV + health bucket so the
    frontend can render a health bar without a second round-trip."""
    import asyncio

    rows = (
        db.query(Loan)
        .filter(Loan.user_id == user.user_id)
        .order_by(Loan.id.desc())
        .all()
    )

    # Fan-out price lookups for all active loans concurrently. The 60s in-memory
    # cache in price_watcher dedupes hits when multiple loans share a slug.
    active_rows = [r for r in rows if r.status == "active"]
    prices = await asyncio.gather(
        *(get_current_price(r.ctf_token_id, r.market_slug) for r in active_rows),
        return_exceptions=False,
    )
    price_by_loan = {r.id: p for r, p in zip(active_rows, prices)}

    # Pull liquidation event payloads for liquidated_* rows so we can render
    # the funds-flow breakdown (CTF sold / principal repaid / interest /
    # residual) without a second round-trip per card.
    liquidated_loan_ids = [
        r.id for r in rows if r.status.startswith("liquidated")
    ]
    liq_event_by_loan: dict[int, dict] = {}
    if liquidated_loan_ids:
        liq_events = (
            db.query(LendingEvent)
            .filter(
                LendingEvent.event_type == "LoanLiquidated",
                LendingEvent.loan_id.in_(liquidated_loan_ids),
            )
            .all()
        )
        for ev in liq_events:
            liq_event_by_loan[ev.loan_id] = ev.data or {}

    items: list[LoanItem] = []
    now = datetime.utcnow()
    for r in rows:
        debt = None
        interest = None
        cur_price = None
        cur_value = None
        cur_ltv = None
        health = None
        if r.status == "active":
            elapsed_s = max(0, int((now - r.opened_at).total_seconds()))
            principal = float(r.principal)
            interest = principal * (r.apr_bps / 10_000) * (elapsed_s / 31_536_000)
            debt = principal + interest

            cur_price = price_by_loan.get(r.id)
            if cur_price is not None and float(r.collateral_shares) > 0:
                cur_value = cur_price * float(r.collateral_shares)
                if cur_value > 0:
                    cur_ltv = debt / cur_value
                    tier = LEAGUE_TIERS.get(r.league_tier)
                    if tier is not None:
                        health = health_status(
                            cur_ltv, tier.warning_ltv, tier.liquidation_ltv
                        )
        items.append(
            LoanItem(
                loan_id=r.id,
                onchain_loan_id=r.onchain_loan_id,
                status=r.status,
                market_slug=r.market_slug,
                title=None,
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
                current_debt_usd=round(debt, 6) if debt is not None else None,
                interest_so_far_usd=round(interest, 6) if interest is not None else None,
                current_collateral_price=round(cur_price, 6) if cur_price is not None else None,
                current_collateral_value_usd=round(cur_value, 6) if cur_value is not None else None,
                current_ltv=round(cur_ltv, 4) if cur_ltv is not None else None,
                health_status=health,
                total_interest_paid=float(r.total_interest_paid) if r.total_interest_paid is not None else None,
                **_liq_breakdown(liq_event_by_loan.get(r.id)),
            )
        )
    return {"loans": [i.model_dump() for i in items]}


# ---------- Repay: prepare ----------


class PrepareRepayRequest(BaseModel):
    loan_id: int


class PrepareRepayResponse(BaseModel):
    loan_id: int
    onchain_loan_id: int
    vault_address: str
    usdc_address: str  # USDC.e — V4 vault underlying
    total_debt_base_units: str  # USDC.e base units (6 decimals)
    total_debt_usd: float
    principal_usd: float
    interest_usd: float
    repay_to: str  # always == vault_address
    repay_data: str  # 0x-prefixed calldata for vault.repay(loanId)


@router.post("/prepare-repay", response_model=PrepareRepayResponse)
async def prepare_repay(
    body: PrepareRepayRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """V4 repay: borrower EOA approves USDC.e to the vault, then calls
    `vault.repay(loanId)` directly. Frontend submits both via MetaMask.

    The actual on-chain repay uses `block.timestamp` so debt may be slightly
    higher when the tx mines than what we report here. To avoid an under-pull
    we recommend approving with a small buffer (the frontend already adds ~1%).
    """
    loan = db.get(Loan, body.loan_id)
    if loan is None:
        raise HTTPException(404, "loan not found")
    if loan.user_id != user.user_id:
        raise HTTPException(403, "not your loan")
    if loan.status != "active":
        raise HTTPException(400, f"loan is {loan.status}, not active")
    if loan.onchain_loan_id is None:
        raise HTTPException(400, "loan has no on-chain id (confirm-borrow not run?)")

    now = datetime.utcnow()
    elapsed_s = max(0, int((now - loan.opened_at).total_seconds()))
    principal_base = int(Decimal(str(loan.principal)) * Decimal(10) ** 6)
    interest_base = (principal_base * loan.apr_bps * elapsed_s) // (10_000 * 31_536_000)
    total_base = principal_base + interest_base

    vault = _vault_address()
    calldata = encode_repay(loan.onchain_loan_id)

    return PrepareRepayResponse(
        loan_id=loan.id,
        onchain_loan_id=loan.onchain_loan_id,
        vault_address=vault,
        usdc_address=settings.polymarket_usdce_address,
        total_debt_base_units=str(total_base),
        total_debt_usd=float(Decimal(total_base) / Decimal(10) ** 6),
        principal_usd=float(loan.principal),
        interest_usd=float(Decimal(interest_base) / Decimal(10) ** 6),
        repay_to=vault,
        repay_data=calldata,
    )


# ---------- Repay: confirm ----------


class ConfirmRepayRequest(BaseModel):
    loan_id: int
    tx_hash: str


class ConfirmRepayResponse(BaseModel):
    loan_id: int
    status: str  # "repaid" | "failed"
    close_tx_hash: str
    block_number: int
    interest_paid_usd: float | None


@router.post("/confirm-repay", response_model=ConfirmRepayResponse)
async def confirm_repay(
    body: ConfirmRepayRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    loan = db.get(Loan, body.loan_id)
    if loan is None:
        raise HTTPException(404, "loan not found")
    if loan.user_id != user.user_id:
        raise HTTPException(403, "not your loan")
    if loan.status == "repaid":
        return ConfirmRepayResponse(
            loan_id=loan.id,
            status="repaid",
            close_tx_hash=loan.close_tx_hash or body.tx_hash,
            block_number=0,
            interest_paid_usd=float(loan.total_interest_paid or 0),
        )
    if loan.status != "active":
        raise HTTPException(400, f"loan is {loan.status}, not active")

    vault = _vault_address()
    try:
        result = await wait_for_loan_repaid(
            settings.polygon_rpc_url, body.tx_hash, vault, timeout_s=60
        )
    except TimeoutError:
        raise HTTPException(202, "tx not mined yet — retry in a few seconds")

    if not result.success or result.repaid is None:
        loan.status = "failed_repay"
        loan.close_tx_hash = body.tx_hash
        db.commit()
        return ConfirmRepayResponse(
            loan_id=loan.id,
            status="failed",
            close_tx_hash=body.tx_hash,
            block_number=result.block_number,
            interest_paid_usd=None,
        )

    interest_usd = float(Decimal(result.repaid.interest_paid) / Decimal(10) ** 6)
    loan.status = "repaid"
    loan.close_tx_hash = body.tx_hash
    loan.closed_at = datetime.utcnow()
    loan.total_interest_paid = Decimal(str(interest_usd))
    db.commit()

    return ConfirmRepayResponse(
        loan_id=loan.id,
        status="repaid",
        close_tx_hash=body.tx_hash,
        block_number=result.block_number,
        interest_paid_usd=interest_usd,
    )


# ---------- Risk assessment ----------


class RiskAssessmentRequest(BaseModel):
    asset: str = Field(description="Polymarket CTF tokenId (decimal string)")


@router.post("/risk-assessment")
async def get_risk_assessment(
    body: RiskAssessmentRequest,
    user: CurrentUser = Depends(current_user),
):
    """Recommend a borrow LTV / amount for a given Polymarket position.

    Sprint 2 uses a deterministic heuristic (league tier + time-to-kickoff +
    market edge). Future iterations may replace `assess_risk` with an LLM
    call once we have ground-truth liquidation data to evaluate against.
    """
    detection = await detect_proxy(user.wallet_address, settings.polygon_rpc_url)
    if detection.proxy is None:
        raise HTTPException(404, "no Polymarket proxy detected")

    raw = await fetch_user_positions(detection.proxy)
    positions = annotate_positions(raw)
    match = next((p for p in positions if str(p.get("asset")) == body.asset), None)
    if match is None:
        raise HTTPException(404, "asset not found in your positions")
    if match.get("league_tier") is None or float(match.get("value_usd") or 0) <= 0:
        raise HTTPException(400, "position is not borrow-eligible")

    kickoff = kickoff_from_slug(match.get("event_slug"))
    assessment = assess_risk(
        asset=body.asset,
        league_tier=int(match["league_tier"]),
        value_usd=float(match["value_usd"]),
        current_price=float(match.get("current_price") or 0.5),
        kickoff_at=kickoff,
        market_title=match.get("title"),
    )
    return risk_to_payload(assessment)


# ---------- Alerts (Sprint 3 — pull/ack pattern) ----------


class LendingAlertItem(BaseModel):
    id: int
    loan_id: int
    alert_type: str  # ltv_70 | ltv_80 | kickoff_4h | kickoff_2h
    sent_at: str  # ISO


class LendingAlertAckRequest(BaseModel):
    alert_ids: list[int]


class LendingAlertAckResponse(BaseModel):
    acknowledged: int


@router.get("/alerts/pending", response_model=list[LendingAlertItem])
def list_pending_alerts(
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Service Worker polls this every minute via chrome.alarms. Returns the
    user's alerts with `delivered_at IS NULL` (haven't been pushed yet)."""
    rows = (
        db.query(LendingAlert)
        .join(Loan, LendingAlert.loan_id == Loan.id)
        .filter(Loan.user_id == user.user_id, LendingAlert.delivered_at.is_(None))
        .order_by(LendingAlert.id.asc())
        .all()
    )
    return [
        LendingAlertItem(
            id=r.id,
            loan_id=r.loan_id,
            alert_type=r.alert_type,
            sent_at=r.sent_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/alerts/ack", response_model=LendingAlertAckResponse)
def ack_alerts(
    body: LendingAlertAckRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Mark alerts as delivered after the SW has fired chrome.notifications.
    Filters by user ownership so a malicious caller can't ack others' alerts."""
    if not body.alert_ids:
        return LendingAlertAckResponse(acknowledged=0)
    now = datetime.utcnow()
    rows = (
        db.query(LendingAlert)
        .join(Loan, LendingAlert.loan_id == Loan.id)
        .filter(
            LendingAlert.id.in_(body.alert_ids),
            Loan.user_id == user.user_id,
            LendingAlert.delivered_at.is_(None),
        )
        .all()
    )
    for r in rows:
        r.delivered_at = now
    if rows:
        db.commit()
    return LendingAlertAckResponse(acknowledged=len(rows))
