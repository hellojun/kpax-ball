"""Keeper loop — Sprint 4 V2 5-step real liquidation (CLOB V2 + PM proxy).

CLOB V2 (cutover 2026-04-28) disallows EOA-direct trading; orders must come
from the PM proxy ("deposit wallet") that holds CTF/USDC. So the 4-step V1
flow grows to 5 steps, with two new on-chain hops around the proxy:

  ① withdrawCtfForLiquidation (on-chain)        — vault → keeper EOA (CTF)
                                                  status: active → withdrawing
  ② ctf.safeTransferFrom (on-chain)              — keeper EOA → keeper proxy (CTF)
  ③ Polymarket CLOB V2 sell (off-chain)          — proxy CTF → buyer
                                                   buyer pUSD → proxy
                                                  ⚠️ irreversible boundary
  ④ Relayer Batch [pusd.transfer] (on-chain)     — proxy → vault (pUSD)
  ⑤ settleLiquidation (on-chain)                 — vault unwraps pUSD → USDC.e
                                                   then distributes:
                                                    LP ← principal,
                                                    treasury ← interest+penalty,
                                                    borrower ← residual
                                                  status: withdrawing → liquidating
                                                  → (indexer) → liquidated_*

Atomicity (rollback semantics):
  - step1 fail (revert)         → no state mutation, status untouched, retry next tick
  - step2 fail (EOA→proxy CTF)  → vault.returnCtfFromKeeper, status → active
  - step3 fail (CLOB)           → factory.proxy([ctf.safeTransferFrom]) to yank
                                   CTF back to EOA, then vault.returnCtfFromKeeper,
                                   status → active
  - step4 fail (proxy→vault $)  → CTF gone, pUSD in proxy. Reaper retries.
                                   Admin recovery: re-fire Relayer transfer.
  - step5 fail (settle)         → pUSD held in vault (not yet unwrapped).
                                   Admin: setPaused(true) + emergencyWithdrawERC20(pUSD, ...).

Past step 3 (the CLOB sell), state is **not** rollback-able — CTF is sold for
pUSD on-chain. Failures past that point require admin recovery; the keeper
logs + alerts but doesn't try to "unsell".

The terminal state (`liquidated_ltv` / `liquidated_kickoff`) is written by the
event indexer when LoanLiquidated arrives — not here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import sentry_sdk
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal
from app.models.loan import Loan
from app.services.lending.config import KICKOFF_BUFFER_HOURS, LEAGUE_TIERS
from app.services.lending.polymarket_seller import (
    SellResult,
    get_polymarket_seller,
)
from app.services.lending.price_watcher import get_current_price
from app.services.lending.pm_relayer import get_pm_relayer
from app.services.lending.vault_client import (
    KeeperKeyMissing,
    LiquidationFailed,
    POLYMARKET_CTF_ADDRESS,
    get_vault_client,
)

logger = logging.getLogger(__name__)


# ---------- main loop ----------


async def keeper_loop() -> None:
    sentry_sdk.set_tag("loop", "keeper")
    interval = settings.keeper_interval_seconds
    logger.info("keeper loop starting, interval=%ss", interval)

    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("keeper tick failed")
            sentry_sdk.capture_exception()
        await asyncio.sleep(interval)


async def _tick() -> None:
    async with AsyncSessionLocal() as session:
        await _scan_kickoff_due(session)
        await _scan_ltv_breach(session)


# ---------- scans ----------


async def _scan_kickoff_due(session: AsyncSession) -> None:
    threshold = datetime.utcnow() + timedelta(hours=KICKOFF_BUFFER_HOURS)
    result = await session.execute(
        select(Loan).where(
            Loan.status == "active",
            Loan.match_kickoff_at <= threshold,
        )
    )
    for loan in result.scalars().all():
        price = await get_current_price(loan.ctf_token_id, loan.market_slug)
        if price is None:
            logger.warning(
                "kickoff_due: price unavailable loan=%s slug=%s",
                loan.id, loan.market_slug,
            )
            continue
        await _trigger_liquidation(loan.id, "kickoff_due", price)


async def _scan_ltv_breach(session: AsyncSession) -> None:
    result = await session.execute(select(Loan).where(Loan.status == "active"))
    loans = result.scalars().all()
    if not loans:
        return
    pairs = {(loan.market_slug, loan.ctf_token_id) for loan in loans}
    prices: dict[tuple[str, str], float | None] = {}
    for slug, token_id in pairs:
        prices[(slug, token_id)] = await get_current_price(token_id, slug)

    for loan in loans:
        price = prices.get((loan.market_slug, loan.ctf_token_id))
        if price is None:
            continue
        ltv = _compute_ltv(loan, price)
        tier_cfg = LEAGUE_TIERS.get(int(loan.league_tier))
        if tier_cfg is None:
            continue
        if ltv >= tier_cfg.liquidation_ltv:
            await _trigger_liquidation(loan.id, "ltv_breach", price)


def _compute_ltv(loan: Loan, current_price: float) -> float:
    """Snapshot LTV. Uses principal as debt approximation — interest accrues
    on-chain and is small relative to LTV thresholds for keeper screening."""
    shares = float(loan.collateral_shares)
    if shares <= 0 or current_price <= 0:
        return 1e9
    collateral_value = shares * current_price
    if collateral_value <= 0:
        return 1e9
    return float(loan.principal) / collateral_value


# ---------- units helpers ----------


def _shares_e6(collateral_shares: Decimal) -> int:
    """CTF base units (6-decimal). Used for ERC-1155 transfers (keeper EOA →
    proxy, and proxy → keeper EOA on rollback). Loan.collateral_shares is
    Decimal(30,6) so 1.612800 shares → 1_612_800 base units."""
    return int(Decimal(str(collateral_shares)) * Decimal(10) ** 6)


def _shares_float(collateral_shares: Decimal) -> float:
    """Float share count for the CLOB SDK. The SDK takes a float `amount`
    on `MarketOrderArgs` and handles base-unit scaling internally. Don't
    truncate — V1 used `int(Decimal(...))` which silently dropped fractional
    shares (a 1.6128-share loan only sold 1, leaving 0.6128 stranded)."""
    return float(Decimal(str(collateral_shares)))


def _expected_proceeds_e6(collateral_shares: Decimal, price_e6: int) -> int:
    """Expected USDC proceeds in 6-decimal base units. shares × priceE6 cancels
    out the 1e6 scaling on both sides (shares_micro × priceE6 / 1e6)."""
    return int(Decimal(str(collateral_shares)) * Decimal(price_e6))


# ---------- 6-step trigger ----------


async def _trigger_liquidation(loan_id: int, reason: str, price: float) -> None:
    sentry_sdk.set_extra("loan_id", loan_id)
    sentry_sdk.set_extra("reason", reason)

    price_e6 = int(price * 1_000_000)
    if not (0 < price_e6 <= 1_000_000):
        logger.warning("bad price loan=%s price=%s", loan_id, price)
        return

    proxy_address = settings.keeper_proxy_address
    if not proxy_address:
        logger.warning(
            "keeper_proxy_address not configured — cannot run V2 liquidation "
            "loan=%s. Set KEEPER_PROXY_ADDRESS to the keeper's PM Safe proxy.",
            loan_id,
        )
        return

    # Step 1 — vault → keeper EOA (CTF). Locks the row, status active→withdrawing.
    snapshot = await _step1_withdraw(loan_id, price_e6)
    if snapshot is None:
        return  # not eligible / step1 failed / mock-mode (status unchanged)

    # Step 2 — keeper EOA → proxy (CTF, ERC-1155). On failure: returnCtfFromKeeper.
    if not await _step2_eoa_to_proxy(loan_id, snapshot, proxy_address):
        await _rollback_returnCtf_eoa_to_vault(
            loan_id, snapshot["onchain_loan_id"], "step2 EOA→proxy failed",
        )
        return

    # Step 3 — CLOB sell from proxy. ⚠️ irreversible past this point.
    expected_e6 = _expected_proceeds_e6(snapshot["collateral_shares"], price_e6)
    min_proceeds_e6 = (
        expected_e6 * (10_000 - settings.keeper_sell_slippage_bps) // 10_000
    )
    sell = await _step3_sell(
        loan_id,
        snapshot["ctf_token_id"],
        snapshot["shares_float"],
        min_proceeds_e6,
    )
    if sell is None or not sell.success:
        # CTF still in proxy — pull back via Safe exec then returnCtf.
        await _rollback_proxy_to_vault(
            loan_id, snapshot, proxy_address, "step3 sell failed",
        )
        return

    # ====== past here: CTF sold, USDC in proxy. No rollback possible. ======

    # Step 4 — proxy → vault (pUSD) via PM Relayer. Vault unwraps to USDC.e in step 5.
    if not await _step4_proxy_to_vault_pusd(
        loan_id, sell.actual_proceeds_e6,
    ):
        # pUSD stuck in proxy. Reaper retries; admin can re-fire Relayer exec.
        return

    # Step 5 — vault.settleLiquidation.
    if not await _step5_settle(
        loan_id, snapshot["onchain_loan_id"], reason, sell.actual_proceeds_e6,
    ):
        # USDC in vault free balance, settle reverted.
        # admin: `emergencyWithdrawERC20`.
        return


# ---------- step impls ----------


async def _step1_withdraw(loan_id: int, price_e6: int) -> dict | None:
    """Lock the row, status active→withdrawing, send withdrawCtfForLiquidation.
    Returns a snapshot of fields needed by later steps, or None to abort."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            row = await session.execute(
                select(Loan).where(Loan.id == loan_id).with_for_update()
            )
            loan = row.scalar_one_or_none()
            if loan is None or loan.status != "active":
                return None
            if loan.onchain_loan_id is None:
                logger.warning(
                    "step1: no onchain_loan_id loan=%s — indexer hasn't caught up",
                    loan_id,
                )
                return None

            try:
                receipt = await get_vault_client().withdraw_ctf_for_liquidation(
                    int(loan.onchain_loan_id), price_e6
                )
            except KeeperKeyMissing:
                logger.warning(
                    "keeper_private_key not set — skipping step1 loan=%s", loan_id,
                )
                return None
            except (LiquidationFailed, Exception) as exc:
                logger.error("step1 withdraw failed loan=%s: %s", loan_id, exc)
                sentry_sdk.capture_exception(exc)
                return None

            loan.status = "withdrawing"
            loan.withdrawn_at = datetime.utcnow()
            logger.info(
                "step1 withdraw sent loan=%s tx=%s", loan_id, receipt.tx_hash,
            )
            return {
                "onchain_loan_id": int(loan.onchain_loan_id),
                "ctf_token_id": loan.ctf_token_id,
                "collateral_shares": loan.collateral_shares,
                "shares_e6": _shares_e6(loan.collateral_shares),
                "shares_float": _shares_float(loan.collateral_shares),
            }


async def _step2_eoa_to_proxy(
    loan_id: int, snapshot: dict, proxy_address: str,
) -> bool:
    """Move CTF from keeper EOA → keeper PM proxy (V2 deposit wallet).
    Pure on-chain ERC-1155 safeTransferFrom; the EOA is the holder so no
    approval is needed."""
    try:
        receipt = await get_vault_client().transfer_ctf_to_proxy(
            token_id=int(snapshot["ctf_token_id"]),
            amount_e6=int(snapshot["shares_e6"]),
            proxy_address=proxy_address,
        )
        logger.info(
            "step2 EOA→proxy CTF sent loan=%s amount_e6=%s tx=%s",
            loan_id, snapshot["shares_e6"], receipt.tx_hash,
        )
        return True
    except Exception as exc:
        logger.error("step2 EOA→proxy CTF failed loan=%s: %s", loan_id, exc)
        sentry_sdk.capture_exception(exc)
        return False


async def _step3_sell(
    loan_id: int,
    token_id: str,
    shares: float,
    min_proceeds_e6: int,
) -> SellResult | None:
    """Off-chain Polymarket sell from the proxy. No on-chain side effects in
    this codepath — fills happen on the PM Exchange contract via the SDK,
    which we don't wait for here. SDK returns success once the order fills."""
    try:
        result = await get_polymarket_seller().sell_ctf_market(
            token_id, shares, min_proceeds_e6=min_proceeds_e6,
        )
    except NotImplementedError as exc:
        logger.error(
            "step3 polymarket integration not wired loan=%s: %s", loan_id, exc,
        )
        sentry_sdk.capture_exception(exc)
        return None
    except Exception as exc:
        logger.exception("step3 sell crashed loan=%s", loan_id)
        sentry_sdk.capture_exception(exc)
        return None
    if not result.success:
        logger.warning("step3 sell failed loan=%s: %s", loan_id, result.error)
    return result


async def _step4_proxy_to_vault_pusd(loan_id: int, amount_e6: int) -> bool:
    """V3 step 4: move pUSD out of the keeper proxy into the vault via PM
    Relayer's gasless ``/submit``. PM CLOB V2 sales settle in pUSD (not
    USDC); the vault then unwraps pUSD → USDC.e inside
    ``settleLiquidation``.

    Failure here means CTF is gone but pUSD is stranded in the keeper
    proxy. Reaper / admin must re-fire this transfer to recover.
    """
    try:
        relayer = get_pm_relayer()
        receipt = await relayer.transfer_erc20(
            token=settings.polymarket_pusd_address,
            to=settings.kpax_vault_address,
            amount=amount_e6,
        )
        logger.info(
            "step4 proxy→vault pUSD sent loan=%s amount_e6=%s tx=%s",
            loan_id, amount_e6, receipt.transaction_hash,
        )
        return True
    except Exception as exc:
        logger.exception(
            "step4 proxy→vault pUSD crashed loan=%s — pUSD stuck in proxy. "
            "ADMIN ACTION REQUIRED (re-fire wallet.execute).",
            loan_id,
        )
        sentry_sdk.capture_exception(exc)
        return False


async def _step5_settle(
    loan_id: int,
    onchain_loan_id: int,
    reason: str,
    actual_proceeds_e6: int,
) -> bool:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            row = await session.execute(
                select(Loan).where(Loan.id == loan_id).with_for_update()
            )
            loan = row.scalar_one_or_none()
            if loan is None or loan.status != "withdrawing":
                logger.warning(
                    "step5: unexpected loan status=%s loan=%s",
                    loan.status if loan else None, loan_id,
                )
                return False
            try:
                receipt = await get_vault_client().settle_liquidation(
                    onchain_loan_id, reason, actual_proceeds_e6,
                )
            except Exception as exc:
                logger.exception(
                    "step5 settle crashed loan=%s — USDC in vault free balance. "
                    "ADMIN ACTION REQUIRED (emergencyWithdrawERC20).",
                    loan_id,
                )
                sentry_sdk.capture_exception(exc)
                return False
            loan.status = "liquidating"
            loan.close_tx_hash = receipt.tx_hash
            loan.liquidating_at = datetime.utcnow()
            logger.info(
                "step5 settle sent loan=%s reason=%s actual_e6=%s tx=%s",
                loan_id, reason, actual_proceeds_e6, receipt.tx_hash,
            )
            return True


# ---------- rollback paths ----------


async def _rollback_returnCtf_eoa_to_vault(
    loan_id: int, onchain_loan_id: int, why: str,
) -> None:
    """Step 2 failed (CTF still in keeper EOA). Vault pulls CTF back via
    `returnCtfFromKeeper` and we reset status → active."""
    logger.warning("rollback (eoa→vault): %s loan=%s", why, loan_id)
    try:
        await get_vault_client().return_ctf_from_keeper(onchain_loan_id)
    except Exception as exc:
        logger.exception(
            "rollback returnCtfFromKeeper crashed loan=%s — CTF stuck in "
            "keeper EOA. ADMIN ACTION REQUIRED.",
            loan_id,
        )
        sentry_sdk.capture_exception(exc)
        return
    await _reset_status_to_active(loan_id)


async def _rollback_proxy_to_vault(
    loan_id: int, snapshot: dict, proxy_address: str, why: str,
) -> None:
    """Step 3 failed (CTF in proxy). Two on-chain hops:
      (a) Safe exec: proxy → keeper EOA (ERC-1155 transfer)
      (b) vault.returnCtfFromKeeper

    Either step failing leaves the CTF in an intermediate location and
    requires admin recovery; we still try to reset DB status to surface the
    stuck state to the reaper."""
    logger.warning("rollback (proxy→vault): %s loan=%s", why, loan_id)
    try:
        relayer = get_pm_relayer()
        await relayer.transfer_erc1155(
            token=POLYMARKET_CTF_ADDRESS,
            to=get_vault_client().account.address,  # keeper EOA
            token_id=int(snapshot["ctf_token_id"]),
            amount=int(snapshot["shares_e6"]),
        )
        logger.info("rollback proxy→EOA CTF moved loan=%s", loan_id)
    except Exception as exc:
        logger.exception(
            "rollback proxy→EOA CTF crashed loan=%s — CTF stuck in proxy. "
            "ADMIN ACTION REQUIRED.",
            loan_id,
        )
        sentry_sdk.capture_exception(exc)
        return  # don't even try the vault pull, CTF isn't where vault expects

    try:
        await get_vault_client().return_ctf_from_keeper(snapshot["onchain_loan_id"])
    except Exception as exc:
        logger.exception(
            "rollback returnCtfFromKeeper crashed loan=%s — CTF stuck in "
            "keeper EOA after proxy→EOA rollback. ADMIN ACTION REQUIRED.",
            loan_id,
        )
        sentry_sdk.capture_exception(exc)
        return
    await _reset_status_to_active(loan_id)


async def _reset_status_to_active(loan_id: int) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            row = await session.execute(
                select(Loan).where(Loan.id == loan_id).with_for_update()
            )
            loan = row.scalar_one_or_none()
            if loan is None or loan.status != "withdrawing":
                logger.warning(
                    "rollback reset: unexpected loan status=%s loan=%s",
                    loan.status if loan else None, loan_id,
                )
                return
            loan.status = "active"
            loan.withdrawn_at = None
            logger.warning("rollback: loan=%s reset to active", loan_id)


# ---------- helpers ----------


