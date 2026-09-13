"""Football data aggregation — real data from API-Football + LLM fallback.

数据来源优先级：
1. API-Football (api-sports.io) — 真实积分榜、近况、H2H（2 次 API 调用）
2. LLM 知识库 — API 不可用或联赛未覆盖时的 fallback
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.services.ai_provider import chat_completion_json

logger = logging.getLogger(__name__)


@dataclass
class TeamForm:
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0


@dataclass
class MatchContext:
    home_team: str
    away_team: str
    competition: str
    home_form: TeamForm | None = None
    away_form: TeamForm | None = None
    head_to_head: list[dict] | None = None
    home_injuries: list[str] = field(default_factory=list)
    away_injuries: list[str] = field(default_factory=list)
    home_league_position: int | None = None
    away_league_position: int | None = None
    analysis_date: str = ""


FOOTBALL_DATA_PROMPT = """You are a football data analyst. Given a match, provide factual data.
Return ONLY valid JSON matching this schema (no markdown, no extra text):

{{
  "home_form": {{
    "played": <int, last 5 matches>,
    "wins": <int>,
    "draws": <int>,
    "losses": <int>,
    "goals_for": <int>,
    "goals_against": <int>,
    "points": <int>
  }},
  "away_form": {{
    "played": <int, last 5 matches>,
    "wins": <int>,
    "draws": <int>,
    "losses": <int>,
    "goals_for": <int>,
    "goals_against": <int>,
    "points": <int>
  }},
  "head_to_head": [
    {{"date": "<YYYY-MM-DD>", "home": "<team>", "away": "<team>", "score": "<X-X>", "competition": "<comp>"}}
  ],
  "home_injuries": ["<player name> (<injury>)"],
  "away_injuries": ["<player name> (<injury>)"],
  "home_league_position": <int or null>,
  "away_league_position": <int or null>
}}

Rules:
- Use your most recent knowledge for the current season
- head_to_head: last 5 meetings between these two teams
- injuries: known major injuries/suspensions only
- If unsure about specific data, use reasonable estimates based on known standings
- All data should reflect the state BEFORE the upcoming match"""


async def get_match_context(
    home_team: str, away_team: str, competition: str
) -> dict:
    """Aggregate match context — real API data + LLM fallback."""
    context = await get_match_context_structured(home_team, away_team, competition)
    return {
        "home_team": context.home_team,
        "away_team": context.away_team,
        "competition": context.competition,
        "recent_form": {
            "home": asdict(context.home_form) if context.home_form else None,
            "away": asdict(context.away_form) if context.away_form else None,
        },
        "head_to_head": context.head_to_head,
        "injuries": {
            "home": context.home_injuries,
            "away": context.away_injuries,
        },
        "home_league_position": context.home_league_position,
        "away_league_position": context.away_league_position,
    }


async def get_match_context_structured(
    home_team: str, away_team: str, competition: str
) -> MatchContext:
    """Return structured MatchContext — API-Football first, LLM fallback."""
    from app.services.football_api import get_real_match_data
    from app.config import settings

    today = datetime.utcnow().strftime("%Y-%m-%d")
    context = MatchContext(
        home_team=home_team,
        away_team=away_team,
        competition=competition,
        analysis_date=today,
    )

    # ── 第一步：尝试 API-Football ──────────────────────────────────────────
    if settings.api_football_key:
        try:
            real = await get_real_match_data(home_team, away_team, competition)
            if real.get("data_source") == "api-football":
                if "home_form" in real:
                    hf = real["home_form"]
                    context.home_form = TeamForm(
                        played=hf.get("played", 0),
                        wins=hf.get("wins", 0),
                        draws=hf.get("draws", 0),
                        losses=hf.get("losses", 0),
                        goals_for=hf.get("goals_for", 0),
                        goals_against=hf.get("goals_against", 0),
                        points=hf.get("points", 0),
                    )
                    context.home_league_position = real.get("home_league_position")
                if "away_form" in real:
                    af = real["away_form"]
                    context.away_form = TeamForm(
                        played=af.get("played", 0),
                        wins=af.get("wins", 0),
                        draws=af.get("draws", 0),
                        losses=af.get("losses", 0),
                        goals_for=af.get("goals_for", 0),
                        goals_against=af.get("goals_against", 0),
                        points=af.get("points", 0),
                    )
                    context.away_league_position = real.get("away_league_position")
                if "head_to_head" in real:
                    context.head_to_head = real["head_to_head"]

                logger.info("Using API-Football data for %s vs %s", home_team, away_team)
                # 伤病数据 LLM 补充（API-Football 伤病查询需要 fixture_id，成本较高）
                await _fill_injuries_via_llm(context, today)
                return context
        except Exception as exc:
            logger.warning("API-Football failed, falling back to LLM: %s", exc)

    # ── 第二步：LLM fallback ───────────────────────────────────────────────
    return await _fetch_context_via_llm(home_team, away_team, competition)


async def get_match_context_quick(
    home_team: str, away_team: str, competition: str
) -> MatchContext:
    """快速分析专用——只拉 API-Football 数据，跳过伤病 LLM 调用。

    比 get_match_context_structured 少一次 LLM 往返（~1-2s）。
    如果 API-Football 不可用，返回空 context 让主 LLM 自行判断。
    """
    from app.services.football_api import get_real_match_data
    from app.config import settings

    today = datetime.utcnow().strftime("%Y-%m-%d")
    context = MatchContext(
        home_team=home_team,
        away_team=away_team,
        competition=competition,
        analysis_date=today,
    )

    if settings.api_football_key:
        try:
            real = await get_real_match_data(home_team, away_team, competition)
            if real.get("data_source") == "api-football":
                if "home_form" in real:
                    hf = real["home_form"]
                    context.home_form = TeamForm(
                        played=hf.get("played", 0),
                        wins=hf.get("wins", 0),
                        draws=hf.get("draws", 0),
                        losses=hf.get("losses", 0),
                        goals_for=hf.get("goals_for", 0),
                        goals_against=hf.get("goals_against", 0),
                        points=hf.get("points", 0),
                    )
                    context.home_league_position = real.get("home_league_position")
                if "away_form" in real:
                    af = real["away_form"]
                    context.away_form = TeamForm(
                        played=af.get("played", 0),
                        wins=af.get("wins", 0),
                        draws=af.get("draws", 0),
                        losses=af.get("losses", 0),
                        goals_for=af.get("goals_for", 0),
                        goals_against=af.get("goals_against", 0),
                        points=af.get("points", 0),
                    )
                    context.away_league_position = real.get("away_league_position")
                if "head_to_head" in real:
                    context.head_to_head = real["head_to_head"]
                # 不调用 _fill_injuries_via_llm，由主 LLM 自行推断
        except Exception as exc:
            logger.warning("API-Football failed in quick mode: %s", exc)

    return context


INJURY_PROMPT = """You are a football data analyst. Return ONLY valid JSON:
{{
  "home_injuries": ["<player> (<injury/suspension>)"],
  "away_injuries": ["<player> (<injury/suspension>)"]
}}
Rules: List only confirmed major injuries or suspensions. Max 5 per team. Use empty list if none known."""


async def _fill_injuries_via_llm(context: MatchContext, today: str) -> None:
    """用 LLM 补充伤病数据（API-Football 获取伤病需消耗额外请求）。"""
    try:
        from app.config import settings
        data = await chat_completion_json(
            messages=[
                {"role": "system", "content": INJURY_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Match: {context.home_team} vs {context.away_team}\n"
                        f"Competition: {context.competition}\n"
                        f"Date: around {today}"
                    ),
                },
            ],
            model=settings.quick_preview_model,
            temperature=0.1,
            max_tokens=400,
        )
        context.home_injuries = data.get("home_injuries", [])
        context.away_injuries = data.get("away_injuries", [])
    except Exception as exc:
        logger.warning("Injury LLM fill failed: %s", exc)


async def _fetch_context_via_llm(
    home_team: str, away_team: str, competition: str
) -> MatchContext:
    """Use LLM to synthesize football data for a match."""
    today = datetime.utcnow().strftime("%Y-%m-%d")

    user_prompt = (
        f"Match: {home_team} vs {away_team}\n"
        f"Competition: {competition}\n"
        f"Date context: around {today}\n\n"
        f"Provide current season data for this match."
    )

    try:
        from app.config import settings
        data = await chat_completion_json(
            messages=[
                {"role": "system", "content": FOOTBALL_DATA_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=settings.quick_preview_model,
            temperature=0.2,
            max_tokens=1500,
        )

        context = MatchContext(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            analysis_date=today,
        )

        if "home_form" in data:
            context.home_form = TeamForm(**data["home_form"])
        if "away_form" in data:
            context.away_form = TeamForm(**data["away_form"])
        context.head_to_head = data.get("head_to_head", [])
        context.home_injuries = data.get("home_injuries", [])
        context.away_injuries = data.get("away_injuries", [])
        context.home_league_position = data.get("home_league_position")
        context.away_league_position = data.get("away_league_position")

        return context

    except Exception as exc:
        logger.warning("LLM football data fetch failed: %s", exc)
        return MatchContext(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            analysis_date=today,
        )
