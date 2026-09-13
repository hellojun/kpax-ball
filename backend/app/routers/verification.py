from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(prefix="/api/verification", tags=["verification"])


class VerificationStats(BaseModel):
    total_verified: int
    correct_outcomes: int
    accuracy_rate: float
    avg_brier_score: float


@router.get("/stats", response_model=VerificationStats)
async def get_verification_stats(
    competition: str | None = None,
    db: Session = Depends(get_db),
):
    """Get historical prediction accuracy stats."""
    from app.services.post_match_verifier import get_stats

    return await get_stats(db, competition)


class VerifyRequest(BaseModel):
    market_id: int


@router.post("/check")
async def verify_match(body: VerifyRequest, db: Session = Depends(get_db)):
    """Manually trigger verification for a specific market."""
    from app.services.post_match_verifier import verify_market

    return await verify_market(body.market_id, db)
