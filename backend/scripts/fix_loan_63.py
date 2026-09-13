"""One-shot: mark loan #63 as manually liquidated (DB only).

Context (2026-05-08):
  - Keeper step1+step2 succeeded (CTF moved vault → keeper EOA → PM proxy)
  - Step3 CLOB sell got 403 geo-block; rollback crashed (relayer API key missing)
  - Admin manually sold CTF via PM UI; proceeds (pUSD) remain in PM account
  - On-chain vault still has loan.withdrawn=true for onchain_loan_id=4
  - LP absorbs the small principal loss (test loan)

Run:
    cd backend && python -m scripts.fix_loan_63
"""

from datetime import datetime

from app.db import SessionLocal
from app.models.loan import Loan

LOAN_ID = 63

def main():
    db = SessionLocal()
    try:
        loan = db.get(Loan, LOAN_ID)
        if loan is None:
            print(f"ERROR: loan {LOAN_ID} not found")
            return

        print(f"Before: id={loan.id} onchain={loan.onchain_loan_id} "
              f"status={loan.status} principal={loan.principal}")

        if loan.status != "withdrawing":
            print(f"WARNING: expected status='withdrawing', got '{loan.status}'")
            confirm = input("Continue anyway? [y/N] ")
            if confirm.lower() != "y":
                print("Aborted.")
                return

        loan.status = "liquidated_ltv"
        loan.closed_at = datetime.utcnow()
        # No settlement fields — proceeds never reached vault
        loan.total_interest_paid = 0
        loan.liquidation_penalty = 0
        loan.residual_to_user = 0

        db.commit()
        print(f"After:  id={loan.id} status={loan.status} "
              f"closed_at={loan.closed_at.isoformat()}")
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
