"""Post-match batch verification script.

Runs verification for all completed but unverified markets.
Intended to be run daily (e.g., via cron at UTC 06:00).

Usage: python scripts/run_verification.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add backend to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import SessionLocal
from app.services.post_match_verifier import get_stats, run_batch_verification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Run batch verification and print summary."""
    db = SessionLocal()
    try:
        # Run verification
        logger.info("Starting batch verification...")
        results = await run_batch_verification(db)
        logger.info(
            "Verification complete: %d verified, %d skipped, %d errors",
            results["verified"],
            results["skipped"],
            results["errors"],
        )

        # Print current stats
        stats = await get_stats(db)
        logger.info("Overall stats:")
        logger.info("  Total verified: %d", stats["total_verified"])
        logger.info("  Correct outcomes: %d", stats["correct_outcomes"])
        logger.info("  Accuracy rate: %.1f%%", stats["accuracy_rate"] * 100)
        logger.info("  Avg Brier score: %.4f", stats["avg_brier_score"])

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
