"""One-shot recovery: call ``vault.settleLiquidation`` directly for a
specific loan that's stuck after step 4 (pUSD already in vault).

Used when an admin-manual liquidation made it through step 4 but blew up
in step 5 — for example because the keeper passed a non-whitelisted
reason ("manual") to ``vault_client.settle_liquidation`` (which the V4
contract also rejects via REASON_LTV_BREACH / REASON_KICKOFF_DUE check).

Usage:
    cd backend && .venv/bin/python -m scripts.settle_loan_67 \
        --loan-id-db 69 --onchain-loan-id 10 --actual-proceeds-e6 950600
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models.loan import Loan
from app.services.lending.vault_client import LIQUIDATE_REASONS, get_vault_client


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--loan-id-db", type=int, required=True,
                   help="Loan row id in the DB (sanity check + audit only).")
    p.add_argument("--onchain-loan-id", type=int, required=True,
                   help="On-chain loan id (the one passed to settleLiquidation).")
    p.add_argument("--actual-proceeds-e6", type=int, required=True,
                   help="Actual proceeds in USDC base units (6 decimals). Use the "
                        "real CLOB-fill amount, not the pre-trade quote estimate.")
    p.add_argument("--reason", choices=list(LIQUIDATE_REASONS), default="ltv_breach")
    args = p.parse_args()

    async with AsyncSessionLocal() as s:
        r = (await s.execute(select(Loan).where(Loan.id == args.loan_id_db))).scalar_one_or_none()
        if r is None:
            print(f"loan {args.loan_id_db} not in DB"); sys.exit(1)
        print(f"loan {args.loan_id_db} state:")
        print(f"  status               : {r.status}")
        print(f"  onchain_loan_id      : {r.onchain_loan_id}")
        print(f"  collateral_shares    : {r.collateral_shares}")
        print(f"  entry_price          : {r.entry_price}")
        if r.status not in ("withdrawing", "liquidating"):
            print(f"  refusing — status must be withdrawing/liquidating")
            sys.exit(1)
        if r.onchain_loan_id != args.onchain_loan_id:
            print(f"  refusing — DB row's onchain_loan_id={r.onchain_loan_id} "
                  f"!= --onchain-loan-id={args.onchain_loan_id}")
            sys.exit(1)

    print(f"\nsubmitting vault.settleLiquidation({args.onchain_loan_id}, "
          f"{args.reason!r}, {args.actual_proceeds_e6}) via keeper EOA...")
    vc = get_vault_client()
    receipt = await vc.settle_liquidation(
        loan_id=args.onchain_loan_id,
        reason=args.reason,
        actual_proceeds_e6=args.actual_proceeds_e6,
    )
    print(f"\nSUCCESS")
    print(f"  tx hash: {receipt.tx_hash}")
    print(f"  block  : {getattr(receipt, 'block_number', '?')}")
    print(f"  status : {getattr(receipt, 'status', '?')}")
    print()
    print("DB status will be updated by the lending event indexer once it")
    print("picks up the on-chain `LoanLiquidated` event (active → liquidated_*).")


if __name__ == "__main__":
    asyncio.run(main())
