"""API-Football 真实数据接入层。

使用 api-sports.io（Free 计划，100 次/天）获取：
- 联赛积分榜（含近况 form）
- H2H 历史交锋
- 球队 ID 查找

Free 计划限制：
- 不支持 last 参数，需用 season + league + status=FT
- 100 次/天

缓存策略（最大化请求复用）：
- 球队/联赛 ID：内存永久缓存（ID 不变）
- 积分榜：24 小时（含近况）
- H2H：24 小时
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://v3.football.api-sports.io"

# ── 联赛 ID 预置表（主要联赛）────────────────────────────────────────────
LEAGUE_IDS: dict[str, int] = {
    # 英超
    "premier league": 39,
    "epl": 39,
    # 世界杯
    "world cup": 1,
    "fifa world cup": 1,
    # 欧冠
    "champions league": 2,
    "uefa champions league": 2,
    # 欧联
    "europa league": 3,
    "uefa europa league": 3,
    # 欧会
    "conference league": 848,
    # 西甲
    "la liga": 140,
    # 德甲
    "bundesliga": 78,
    # 意甲
    "serie a": 135,
    # 法甲
    "ligue 1": 61,
    # 荷甲
    "eredivisie": 88,
    # 中超
    "chinese super league": 169,
    "csl": 169,
    # 美职联
    "mls": 253,
    # J联赛
    "j-league": 98,
    "j league": 98,
    # K联赛
    "k-league": 292,
    "k league": 292,
}

# ── 内存缓存（按 key → {data, expires_at}）────────────────────────────────
_cache: dict[str, dict] = {}


def _get_cache(key: str) -> Any | None:
    entry = _cache.get(key)
    if not entry:
        return None
    if time.time() > entry["expires_at"]:
        del _cache[key]
        return None
    return entry["data"]


def _set_cache(key: str, data: Any, ttl: int) -> None:
    _cache[key] = {"data": data, "expires_at": time.time() + ttl}


# ── HTTP 客户端（共享）────────────────────────────────────────────────────
def _headers() -> dict:
    return {"x-apisports-key": settings.api_football_key}


async def _get(path: str, params: dict) -> dict:
    """发起 GET 请求，返回完整响应 JSON。"""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{API_BASE}{path}", headers=_headers(), params=params)
        r.raise_for_status()
        return r.json()


# ── 工具函数 ─────────────────────────────────────────────────────────────

def _current_season() -> int:
    """根据当前月份推算赛季年份（欧洲足球赛季跨年）。"""
    now = datetime.utcnow()
    # 7 月前属于上一赛季（如 2025年4月 → 赛季 2024）
    return now.year - 1 if now.month < 7 else now.year


async def _resolve_active_season(league_id: int) -> int:
    """找到该联赛最新有积分数据的赛季（最多往前查 2 年）。"""
    base = _current_season()
    for delta in range(3):
        season = base - delta
        rows = await fetch_standings(league_id, season)
        if rows:
            return season
    return base


def _resolve_league_id(competition: str) -> int | None:
    return LEAGUE_IDS.get(competition.lower())


async def _resolve_team_id(team_name: str) -> int | None:
    """查找球队 ID，优先从缓存取。"""
    cache_key = f"team_id:{team_name.lower()}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        data = await _get("/teams", {"search": team_name})
        if not data.get("response"):
            return None

        # 优先精确匹配英格兰球队，再取第一个结果
        for item in data["response"]:
            t = item["team"]
            if t["name"].lower() == team_name.lower() and t.get("country") == "England":
                _set_cache(cache_key, t["id"], ttl=86400 * 30)  # 30天
                return t["id"]

        # fallback：第一个结果
        team_id = data["response"][0]["team"]["id"]
        _set_cache(cache_key, team_id, ttl=86400 * 30)
        return team_id
    except Exception as exc:
        logger.warning("Failed to resolve team ID for %s: %s", team_name, exc)
        return None


# ── 核心数据获取 ──────────────────────────────────────────────────────────

async def fetch_standings(league_id: int, season: int) -> list[dict]:
    """拉取积分榜，含近5场表现。缓存 24 小时。"""
    cache_key = f"standings:{league_id}:{season}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        data = await _get("/standings", {"league": league_id, "season": season})
        if not data.get("response"):
            return []
        rows = data["response"][0]["league"]["standings"][0]
        _set_cache(cache_key, rows, ttl=86400)
        return rows
    except Exception as exc:
        logger.warning("Failed to fetch standings league=%s season=%s: %s", league_id, season, exc)
        return []


async def fetch_h2h(team1_id: int, team2_id: int, season: int) -> list[dict]:
    """拉取两队历史交锋（当前赛季）。缓存 24 小时。"""
    cache_key = f"h2h:{min(team1_id, team2_id)}:{max(team1_id, team2_id)}:{season}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        data = await _get(
            "/fixtures/headtohead",
            {"h2h": f"{team1_id}-{team2_id}", "season": season},
        )
        fixtures = data.get("response", [])
        _set_cache(cache_key, fixtures, ttl=86400)
        return fixtures
    except Exception as exc:
        logger.warning("Failed to fetch H2H %s vs %s: %s", team1_id, team2_id, exc)
        return []


async def fetch_team_fixtures(team_id: int, league_id: int, season: int, limit: int = 5) -> list[dict]:
    """拉取球队已完成的最近比赛。缓存 6 小时。"""
    cache_key = f"fixtures:{team_id}:{league_id}:{season}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached[-limit:]

    try:
        data = await _get(
            "/fixtures",
            {"team": team_id, "league": league_id, "season": season, "status": "FT"},
        )
        fixtures = data.get("response", [])
        _set_cache(cache_key, fixtures, ttl=3600 * 6)
        return fixtures[-limit:]
    except Exception as exc:
        logger.warning("Failed to fetch fixtures team=%s: %s", team_id, exc)
        return []


# ── 高层接口：供 football_data.py 调用 ───────────────────────────────────

def _parse_form_from_standings(standings: list[dict], team_id: int) -> dict | None:
    """从积分榜行提取球队近况数据。"""
    for row in standings:
        if row["team"]["id"] == team_id:
            all_stats = row.get("all", {})
            return {
                "played": all_stats.get("played", 0),
                "wins": all_stats.get("win", 0),
                "draws": all_stats.get("draw", 0),
                "losses": all_stats.get("lose", 0),
                "goals_for": all_stats.get("goals", {}).get("for", 0),
                "goals_against": all_stats.get("goals", {}).get("against", 0),
                "points": row.get("points", 0),
                "form": row.get("form", ""),         # 近5场：如 "WWDLW"
                "rank": row.get("rank"),
            }
    return None


def _parse_h2h(fixtures: list[dict]) -> list[dict]:
    """格式化 H2H 数据供 prompt 使用。"""
    results = []
    for f in fixtures:
        fix = f.get("fixture", {})
        home = f.get("teams", {}).get("home", {})
        away = f.get("teams", {}).get("away", {})
        goals = f.get("goals", {})
        results.append({
            "date": fix.get("date", "")[:10],
            "home": home.get("name", ""),
            "away": away.get("name", ""),
            "score": f"{goals.get('home', '?')}-{goals.get('away', '?')}",
            "competition": f.get("league", {}).get("name", ""),
        })
    return results


async def get_real_match_data(
    home_team: str,
    away_team: str,
    competition: str,
) -> dict:
    """
    主入口：获取一场比赛的真实数据。

    返回格式（兼容 football_data.py 的 MatchContext）：
    {
        "home_form": {...},
        "away_form": {...},
        "head_to_head": [...],
        "home_league_position": int | None,
        "away_league_position": int | None,
        "data_source": "api-football" | "unavailable",
    }
    """
    if not settings.api_football_key:
        return {"data_source": "unavailable"}

    league_id = _resolve_league_id(competition)

    # 并发解析两队 ID
    import asyncio
    home_id, away_id = await asyncio.gather(
        _resolve_team_id(home_team),
        _resolve_team_id(away_team),
    )

    # 找到有数据的赛季（可能比当前赛季早一年）
    season = _current_season()
    if league_id:
        season = await _resolve_active_season(league_id)

    result: dict = {"data_source": "api-football", "season": season}

    if league_id and home_id and away_id:
        # 并发拉取积分榜 + H2H（仅 2 次 API 调用）
        standings, h2h_fixtures = await asyncio.gather(
            fetch_standings(league_id, season),
            fetch_h2h(home_id, away_id, season),
        )

        home_data = _parse_form_from_standings(standings, home_id)
        away_data = _parse_form_from_standings(standings, away_id)

        if home_data:
            result["home_form"] = home_data
            result["home_league_position"] = home_data.get("rank")
        if away_data:
            result["away_form"] = away_data
            result["away_league_position"] = away_data.get("rank")
        result["head_to_head"] = _parse_h2h(h2h_fixtures)

        logger.info(
            "API-Football data fetched: %s vs %s (league=%s, season=%s, h2h=%d)",
            home_team, away_team, league_id, season, len(h2h_fixtures),
        )
    elif home_id and away_id:
        # 联赛不在预置表中，只查 H2H（1 次 API 调用）
        h2h_fixtures = await fetch_h2h(home_id, away_id, season)
        result["head_to_head"] = _parse_h2h(h2h_fixtures)
        logger.info("API-Football H2H only (unknown league): %s vs %s", home_team, away_team)
    else:
        result["data_source"] = "unavailable"
        logger.warning("Cannot resolve team IDs for %s or %s", home_team, away_team)

    return result
