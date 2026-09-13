"""Force-finalize a loan whose on-chain `settleLiquidation` succeeded but
whose DB row never reached a terminal state — typically because the
event indexer was lagging and the stuck-liquidating reaper rolled status
back to `active` (V1 reaper bug; V2 reaper checks the receipt first).

Usage:
    cd backend && python -m scripts.finalize_settled_loan 70
    cd backend && python -m scripts.finalize_settled_loan 70 71 72
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models.lending_event import LendingEvent  # noqa: F401  (register table)
from app.models.loan import Loan
from app.models.user import User  # noqa: F401  (register FK target)
from app.services.lending.loan_finalizer import finalize_from_close_tx


async def finalize(loan_pk: int) -> bool:
    async with AsyncSessionLocal() as session:
        loan = (
            await session.execute(select(Loan).where(Loan.id == loan_pk))
        ).scalar_one_or_none()
        if loan is None:
            print(f"ERROR: loan id={loan_pk} not found")
            return False
        if loan.status in ("liquidated_ltv", "liquidated_kickoff", "repaid", "closed"):
            print(f"SKIP: loan {loan_pk} already terminal ({loan.status})")
            return False
        if loan.close_tx_hash is None:
            print(f"ERROR: loan {loan_pk} has no close_tx_hash; nothing to finalize")
            return False

        before = loan.status
        terminal = await finalize_from_close_tx(session, loan)
        if terminal is None:
            print(
                f"SKIP loan {loan_pk}: close_tx={loan.close_tx_hash} not confirmed "
                f"or no LoanLiquidated log under vault"
            )
            return False
        await session.commit()
        print(
            f"Loan #{loan.id} (onchain={loan.onchain_loan_id}): "
            f"{before} → {terminal}, residual={loan.residual_to_user}"
        )
        return True


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.finalize_settled_loan <loan_id> [loan_id ...]")
        sys.exit(1)
    ok = 0
    for arg in sys.argv[1:]:
        if await finalize(int(arg)):
            ok += 1
    print(f"\nFinalized {ok}/{len(sys.argv)-1} loan(s).")


if __name__ == "__main__":
    asyncio.run(main())
