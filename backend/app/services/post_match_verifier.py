"""Post-match verifier — compare predictions to actual results.

Runs after matches complete to track KPAX prediction accuracy.
Calculates binary accuracy, Brier score, and deviation correctness.
"""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.market import Market
from app.models.verification import MatchResult, VerificationRecord

logger = logging.getLogger(__name__)


async def get_stats(db: Session, competition: str | None = None) -> dict:
    """Get historical prediction accuracy stats."""
    query = db.query(VerificationRecord)

    if competition:
        query = (
            query.join(Market, VerificationRecord.market_id == Market.id)
            .filter(Market.competition == competition)
        )

    total = query.count()
    if total == 0:
        return {
            "total_verified": 0,
            "correct_outcomes": 0,
            "accuracy_rate": 0.0,
            "avg_brier_score": 0.0,
        }

    correct = query.filter(VerificationRecord.correct_outcome == True).count()
    avg_brier = db.query(func.avg(VerificationRecord.brier_score)).scalar() or 0.0

    return {
        "total_verified": total,
        "correct_outcomes": correct,
        "accuracy_rate": round(correct / total, 4) if total > 0 else 0.0,
        "avg_brier_score": round(float(avg_brier), 4),
    }


async def verify_market(market_id: int, db: Session) -> dict:
    """Verify a specific market's prediction against actual result.

    1. Check if match result exists
    2. Find the latest KPAX analysis for this market
    3. Calculate accuracy metrics
    4. Store VerificationRecord
    """
    # Get match result
    result = (
        db.query(MatchResult)
        .filter(MatchResult.market_id == market_id)
        .first()
    )
    if not result:
        return {"status": "no_result", "market_id": market_id}

    # Get latest analysis
    analysis = (
        db.query(Analysis)
        .filter(Analysis.market_id == market_id)
        .order_by(Analysis.created_at.desc())
        .first()
    )
    if not analysis:
        return {"status": "no_analysis", "market_id": market_id}

    # Check if already verified
    existing = (
        db.query(VerificationRecord)
        .filter(
            VerificationRecord.analysis_id == analysis.id,
            VerificationRecord.market_id == market_id,
        )
        .first()
    )
    if existing:
        return {
            "status": "already_verified",
            "market_id": market_id,
            "correct_outcome": existing.correct_outcome,
            "brier_score": existing.brier_score,
        }

    # Calculate metrics
    actual_outcome = result.outcome  # "home" | "away" | "draw"

    # Binary accuracy: did we predict the right winner?
    kpax_probs = {
        "home": analysis.kpax_odds_home,
        "away": analysis.kpax_odds_away,
        "draw": analysis.kpax_odds_draw or 0,
    }
    predicted_outcome = max(kpax_probs, key=kpax_probs.get)
    correct_outcome = predicted_outcome == actual_outcome

    # Brier score: probability calibration (lower is better)
    brier_score = _calculate_brier_score(kpax_probs, actual_outcome)

    # Store verification
    record = VerificationRecord(
        analysis_id=analysis.id,
        market_id=market_id,
        correct_outcome=correct_outcome,
        brier_score=brier_score,
        deviation_correct=None,  # TODO: compare with market deviation direction
    )
    db.add(record)
    db.commit()

    logger.info(
        "Verified market %d: correct=%s, brier=%.4f",
        market_id, correct_outcome, brier_score,
    )

    return {
        "status": "verified",
        "market_id": market_id,
        "actual_outcome": actual_outcome,
        "predicted_outcome": predicted_outcome,
        "correct_outcome": correct_outcome,
        "brier_score": round(brier_score, 4),
    }


async def run_batch_verification(db: Session) -> dict:
    """Verify all markets with results that haven't been verified yet.

    Intended to be called by a scheduled task (e.g., daily cron).
    """
    # Find match results without corresponding verification records
    verified_market_ids = (
        db.query(VerificationRecord.market_id).distinct().subquery()
    )
    unverified = (
        db.query(MatchResult)
        .filter(~MatchResult.market_id.in_(verified_market_ids))
        .all()
    )

    results = {"verified": 0, "skipped": 0, "errors": 0}

    for match_result in unverified:
        try:
            result = await verify_market(match_result.market_id, db)
            if result["status"] == "verified":
                results["verified"] += 1
            else:
                results["skipped"] += 1
        except Exception as exc:
            logger.error("Verification failed for market %d: %s", match_result.market_id, exc)
            results["errors"] += 1

    return results


def _calculate_brier_score(probabilities: dict, actual_outcome: str) -> float:
    """Calculate Brier score for a prediction.

    Brier = sum((predicted_i - actual_i)^2) for each outcome.
    Lower is better. Perfect = 0, worst = 2.
    """
    score = 0.0
    for outcome, prob in probabilities.items():
        actual = 1.0 if outcome == actual_outcome else 0.0
        score += (prob - actual) ** 2
    return score
