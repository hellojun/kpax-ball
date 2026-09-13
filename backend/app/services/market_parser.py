"""Polymarket market parser — detect football markets and extract structured data.

Supports two slug formats:
- Event slugs: human-readable titles (e.g. "man-city-vs-arsenal-premier-league")
- Sports slugs: coded format (e.g. "epl-mac-cry-2026-03-21")
"""

import re

import httpx

from app.config import settings

# Keywords for football market detection
FOOTBALL_KEYWORDS = [
    "premier league", "epl", "world cup", "fifa",
    "champions league", "europa league",
    "la liga", "serie a", "bundesliga", "ligue 1",
]

TEAM_PATTERNS = [
    # "Team A vs Team B" or "Team A v Team B"
    r"^(.+?)\s+(?:vs\.?|v\.?)\s+(.+?)(?:\s*[-–—|]|$)",
]

COMPETITION_MAP = {
    "premier league": "Premier League",
    "epl": "Premier League",
    "world cup": "World Cup 2026",
    "fifa world cup": "World Cup 2026",
    "champions league": "Champions League",
}

# EPL team code -> full team name
EPL_TEAM_MAP = {
    "ars": "Arsenal",
    "avl": "Aston Villa",
    "bou": "Bournemouth",
    "bre": "Brentford",
    "bri": "Brighton",
    "che": "Chelsea",
    "cry": "Crystal Palace",
    "eve": "Everton",
    "ful": "Fulham",
    "ips": "Ipswich Town",
    "lei": "Leicester City",
    "liv": "Liverpool",
    "mac": "Man City",
    "mau": "Man United",
    "new": "Newcastle",
    "nfo": "Nottingham Forest",
    "sou": "Southampton",
    "tot": "Tottenham",
    "whu": "West Ham",
    "wol": "Wolves",
}


async def parse_market(slug: str) -> dict:
    """Fetch market from Gamma API and determine if it's a football market.

    Tries Gamma API first, falls back to slug parsing for sports URLs.
    """
    # Strategy 1: Try Gamma API
    result = await _try_gamma_api(slug)
    if result and result.get("is_football"):
        return result

    # Strategy 2: Parse sports slug directly (e.g. epl-mac-cry-2026-03-21)
    result = _parse_sports_slug(slug)
    if result:
        return result

    return {"is_football": False}


async def _try_gamma_api(slug: str) -> dict | None:
    """Try to fetch market data from Gamma API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.polymarket_gamma_url}/events",
                params={"slug": slug},
            )
            if resp.status_code != 200:
                return None

            events = resp.json()
            if not events:
                return None

        event = events[0] if isinstance(events, list) else events
        title = event.get("title", "") or event.get("question", "")

        # Check if football-related
        title_lower = title.lower()
        if not any(kw in title_lower for kw in FOOTBALL_KEYWORDS):
            return None

        # Extract teams
        home_team, away_team = _extract_teams(title)
        if not home_team:
            return None

        # Extract competition
        competition = _detect_competition(title_lower)

        # Extract odds from markets
        odds = _extract_odds(event)

        return {
            "is_football": True,
            "home_team": home_team,
            "away_team": away_team,
            "competition": competition,
            "market_type": "match_winner",
            "polymarket_odds": odds,
        }
    except Exception:
        return None


def _parse_sports_slug(slug: str) -> dict | None:
    """Parse a sports-format slug like epl-mac-cry-2026-03-21."""
    parts = slug.lower().split("-")

    # EPL format: epl-{home_code}-{away_code}-{year}-{month}-{day}
    if parts[0] == "epl" and len(parts) >= 4:
        home_code = parts[1]
        away_code = parts[2]
        home_team = EPL_TEAM_MAP.get(home_code)
        away_team = EPL_TEAM_MAP.get(away_code)

        if not home_team or not away_team:
            return None

        return {
            "is_football": True,
            "home_team": home_team,
            "away_team": away_team,
            "competition": "Premier League",
            "market_type": "match_winner",
            "polymarket_odds": {},
        }

    # UCL format: ucl-{home_code}-{away_code}-...
    if parts[0] == "ucl" and len(parts) >= 4:
        return {
            "is_football": True,
            "home_team": parts[1].title(),
            "away_team": parts[2].title(),
            "competition": "Champions League",
            "market_type": "match_winner",
            "polymarket_odds": {},
        }

    return None


def _extract_teams(title: str) -> tuple[str | None, str | None]:
    for pattern in TEAM_PATTERNS:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    return None, None


def _detect_competition(title_lower: str) -> str:
    for keyword, name in COMPETITION_MAP.items():
        if keyword in title_lower:
            return name
    return "Unknown"


def _extract_odds(event: dict) -> dict:
    markets = event.get("markets", [])
    if not markets:
        return {}

    market = markets[0]
    prices = market.get("outcomePrices", [])
    outcomes = market.get("outcomes", [])

    odds = {}
    for outcome, price in zip(outcomes, prices):
        try:
            odds[outcome.lower()] = float(price)
        except (ValueError, AttributeError):
            pass

    return odds
