"""Mark a stuck 'withdrawing' loan as manually liquidated (DB only).

Used when keeper step3 (CLOB sell) fails and admin manually sells CTF
via PM UI. Proceeds stay in PM account; vault on-chain state keeps
loan.withdrawn=true; LP absorbs the principal loss.

Usage:
    cd backend && python -m scripts.fix_stuck_loan 63
    cd backend && python -m scripts.fix_stuck_loan 64
    cd backend && python -m scripts.fix_stuck_loan 63 64   # multiple
"""

import sys
from datetime import datetime

from app.db import SessionLocal
from app.models.loan import Loan


def fix_loan(db, loan_id: int) -> bool:
    loan = db.get(Loan, loan_id)
    if loan is None:
        print(f"  ERROR: loan {loan_id} not found")
        return False

    print(f"  Before: id={loan.id} onchain={loan.onchain_loan_id} "
          f"status={loan.status} principal={loan.principal}")

    if loan.status not in ("withdrawing", "active"):
        print(f"  SKIP: status='{loan.status}' is already terminal or unexpected")
        return False

    loan.status = "liquidated_ltv"
    loan.closed_at = datetime.utcnow()
    loan.total_interest_paid = 0
    loan.liquidation_penalty = 0
    loan.residual_to_user = 0

    print(f"  After:  status={loan.status} closed_at={loan.closed_at.isoformat()}")
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.fix_stuck_loan <loan_id> [loan_id ...]")
        sys.exit(1)

    loan_ids = [int(x) for x in sys.argv[1:]]
    db = SessionLocal()
    try:
        changed = 0
        for lid in loan_ids:
            print(f"Loan #{lid}:")
            if fix_loan(db, lid):
                changed += 1
        if changed:
            db.commit()
            print(f"\nCommitted {changed} loan(s).")
        else:
            print("\nNothing to commit.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
