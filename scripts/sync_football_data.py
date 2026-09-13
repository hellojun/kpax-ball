"""Football data sync script.

Periodically fetches and caches football data for known markets.
Run via cron or scheduler: python scripts/sync_football_data.py

In MVP, this pre-warms the LLM-based data cache for active markets.
Phase 2 will add direct FBref/StatsBomb API integration.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add backend to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import SessionLocal
from app.models.market import Market
from app.services.football_data import get_match_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def sync_active_markets():
    """Refresh football data for all active markets."""
    db = SessionLocal()
    try:
        markets = db.query(Market).all()
        logger.info("Found %d markets to sync", len(markets))

        for market in markets:
            try:
                logger.info(
                    "Syncing data for %s vs %s (%s)",
                    market.home_team,
                    market.away_team,
                    market.competition,
                )
                context = await get_match_context(
                    market.home_team,
                    market.away_team,
                    market.competition,
                )
                logger.info("  -> OK: %s", list(context.keys()))
            except Exception as exc:
                logger.error(
                    "  -> FAILED for market %d: %s", market.id, exc
                )

        logger.info("Sync complete")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(sync_active_markets())
