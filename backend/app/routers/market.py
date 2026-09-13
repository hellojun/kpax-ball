from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/market", tags=["market"])


class MarketDetectResponse(BaseModel):
    is_football: bool
    home_team: str | None = None
    away_team: str | None = None
    competition: str | None = None
    market_type: str | None = None
    polymarket_odds: dict | None = None


@router.get("/detect", response_model=MarketDetectResponse)
async def detect_market(slug: str):
    """Detect if a Polymarket slug is a football market and extract structured data."""
    from app.services.market_parser import parse_market

    return await parse_market(slug)


class MarketDataResponse(BaseModel):
    home_team: str
    away_team: str
    competition: str
    recent_form: dict | None = None
    head_to_head: list | None = None
    injuries: list | None = None


@router.get("/data", response_model=MarketDataResponse)
async def get_market_data(home_team: str, away_team: str, competition: str):
    """Get aggregated football data for a match."""
    from app.services.football_data import get_match_context

    return await get_match_context(home_team, away_team, competition)
