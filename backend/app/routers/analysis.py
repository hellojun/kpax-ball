import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import current_user

router = APIRouter(prefix="/api/analysis", tags=["analysis"], dependencies=[Depends(current_user)])


class PreviewRequest(BaseModel):
    slug: str
    polymarket_odds: dict | None = None
    home_team: str | None = None
    away_team: str | None = None
    competition: str | None = None
    language: str = "zh"


class PreviewResponse(BaseModel):
    summary: str
    kpaxOdds: dict
    marketDeviation: dict
    confidence: str
    confidenceReason: str


@router.post("/preview", response_model=PreviewResponse)
async def quick_preview(body: PreviewRequest):
    """Generate a quick preview analysis (<2s) for a football market."""
    from app.services.quick_preview import generate_preview

    return await generate_preview(
        body.slug, body.polymarket_odds, body.language,
        home_team=body.home_team, away_team=body.away_team, competition=body.competition,
    )


class DeepAnalysisRequest(BaseModel):
    slug: str
    polymarket_odds: dict | None = None
    user_context: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    competition: str | None = None
    language: str = "zh"


@router.post("/deep")
async def deep_analysis(body: DeepAnalysisRequest):
    """Run full expert debate analysis (1-3min). Returns SSE stream."""
    from app.services.deep_analysis import run_deep_analysis

    async def event_generator():
        async for msg in run_deep_analysis(
            body.slug, body.polymarket_odds, body.user_context, body.language,
            home_team=body.home_team, away_team=body.away_team, competition=body.competition,
        ):
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(), media_type="text/event-stream"
    )


class FollowUpRequest(BaseModel):
    slug: str
    question: str
    polymarket_odds: dict | None = None
    debate_messages: list[dict]  # 前端传回已有的辩论记录
    previous_report: dict        # 前端传回已有的报告
    language: str = "zh"


@router.post("/followup")
async def follow_up(body: FollowUpRequest):
    """Re-generate report with a follow-up question, skipping expert debate."""
    from app.services.follow_up import run_follow_up

    async def event_generator():
        async for msg in run_follow_up(
            slug=body.slug,
            question=body.question,
            polymarket_odds=body.polymarket_odds,
            debate_messages=body.debate_messages,
            previous_report=body.previous_report,
            language=body.language,
        ):
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(), media_type="text/event-stream"
    )
