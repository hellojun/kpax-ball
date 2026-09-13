"""Quick preview generator — fast analysis in <2 seconds.

Single LLM call with compact prompt to generate a quick assessment
of a football market, comparing Polymarket odds to data-driven analysis.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime

from app.config import settings
from app.services.ai_provider import _stream_until_json, chat_completion_json
from app.services.football_data import get_match_context_quick, get_match_context_structured
from app.services.market_parser import parse_market

logger = logging.getLogger(__name__)

# In-memory cache for quick previews (market_slug -> {data, expires_at})
_preview_cache: dict[str, dict] = {}


QUICK_PREVIEW_PROMPTS = {
    "zh": """你是足球分析专家。根据球队近况、积分排名、交锋记录和赔率数据，评估 Polymarket 盘口。
综合考虑：球队状态趋势、主客场差异、关键伤病影响、赔率是否反映了真实实力差距。

只返回 JSON（不要 markdown 包裹、不要解释）：
{{"summary":"<一句话核心洞察，30字以内>","kpaxOdds":{{"home":<0-1>,"away":<0-1>,"draw":<0-1>}},"confidence":"<high|medium|low>","confidenceReason":"<一句话原因，20字以内>"}}

要求：概率之和=1.0；指出与市场赔率的最大偏差方向。""",

    "en": """You are a football analysis expert. Assess this Polymarket market using team form, standings, H2H records and odds data.
Consider: form trends, home/away factors, key injuries, whether odds reflect true strength gap.

Return JSON ONLY (no markdown, no explanation):
{{"summary":"<one sentence insight>","kpaxOdds":{{"home":<0-1>,"away":<0-1>,"draw":<0-1>}},"confidence":"<high|medium|low>","confidenceReason":"<one sentence>"}}

Rules: probabilities must sum to 1.0; highlight biggest market deviation.""",
}


async def generate_preview(
    slug: str, polymarket_odds: dict | None = None, language: str = "zh",
    home_team: str | None = None, away_team: str | None = None, competition: str | None = None,
) -> dict:
    """Generate a quick preview for a football market.

    优化后的流程（目标 <2s）：
    1. parse_market (Gamma API)                      — 串行，必须先拿到球队名
    2. football_data + ZEP 检索                      — 并发
    3. LLM 分析（跳过单独的伤病 LLM，让主 LLM 一次搞定） — 串行，依赖 2
    4. ZEP 写入                                       — fire-and-forget，不等返回
    """
    import asyncio

    # Check cache
    cache_key = _cache_key(slug, polymarket_odds)
    cached = _get_cached(cache_key)
    if cached:
        return cached

    start_time = time.time()

    def _lap(label: str) -> None:
        elapsed = (time.time() - start_time) * 1000
        logger.info("[PERF] %s: %.0fms (total %.0fms)", label, elapsed - _lap.prev, elapsed)
        _lap.prev = elapsed
    _lap.prev = 0  # type: ignore[attr-defined]

    # Step 1: 优先使用前端传来的球队信息，fallback 到 parse_market
    if home_team and away_team:
        _lap("skip_parse (frontend data)")
    else:
        market = await parse_market(slug)
        _lap("parse_market")
        if not market.get("is_football"):
            return {
                "summary": "Not a football market",
                "kpaxOdds": {},
                "marketDeviation": {},
                "confidence": "low",
                "confidenceReason": "Market not recognized as football",
            }
        home_team = market["home_team"]
        away_team = market["away_team"]
        competition = market.get("competition", "Football")

    competition = competition or "Football"
    odds = polymarket_odds or {}

    # Step 2: 并发获取 football data + ZEP 历史（省掉串行等待）
    from app.services.zep_manager import get_match_history

    async def _get_zep() -> str:
        return get_match_history(home_team, away_team)

    context, zep_context = await asyncio.gather(
        get_match_context_quick(home_team, away_team, competition),
        _get_zep(),
    )
    _lap("football_data + zep")

    # Step 3: Build prompt and call LLM（单次调用，不再单独查伤病）
    context_str = _build_context_string(context, odds)
    if zep_context:
        context_str += f"\n\n历史分析记忆:\n{zep_context}"
    try:
        msgs = [
            {"role": "system", "content": QUICK_PREVIEW_PROMPTS.get(language, QUICK_PREVIEW_PROMPTS["zh"])},
            {"role": "user", "content": context_str},
        ]
        model = settings.quick_preview_model
        # 优先流式早停（快），失败则回退非流式（稳）
        result = await _stream_until_json(model, msgs, temperature=0.1, max_tokens=1024)
        if result is None:
            result = await chat_completion_json(msgs, model=model, temperature=0.1, max_tokens=1024)
        _lap("llm_call")
    except Exception as exc:
        logger.error("Quick preview LLM call failed: %s", exc)
        return {
            "summary": f"Analysis pending for {home_team} vs {away_team}",
            "kpaxOdds": odds,
            "marketDeviation": {},
            "confidence": "low",
            "confidenceReason": "Analysis temporarily unavailable",
        }

    # Step 4: Calculate deviations
    kpax_odds = result.get("kpax_odds") or result.get("kpaxOdds") or {}
    deviation = {}
    for key in ("home", "away", "draw"):
        kpax_val = kpax_odds.get(key, 0)
        market_val = odds.get(key, 0)
        if kpax_val or market_val:
            deviation[key] = round(kpax_val - market_val, 4)

    latency_ms = int((time.time() - start_time) * 1000)
    logger.info("Quick preview generated in %dms for %s", latency_ms, slug)

    preview = {
        "summary": result.get("summary", ""),
        "kpaxOdds": kpax_odds,
        "marketDeviation": deviation,
        "confidence": result.get("confidence", "medium"),
        "confidenceReason": result.get("confidence_reason") or result.get("confidenceReason") or "",
    }

    # Step 5: ZEP 写入 — fire-and-forget，不阻塞返回
    from app.services.zep_manager import push_analysis_result

    def _push_zep():
        try:
            push_analysis_result(
                home_team=home_team,
                away_team=away_team,
                competition=competition,
                kpax_odds=kpax_odds,
                market_odds=odds,
                summary=preview["summary"],
                confidence=preview["confidence"],
                slug=slug,
            )
        except Exception as e:
            logger.warning("ZEP push failed (non-blocking): %s", e)

    asyncio.get_event_loop().run_in_executor(None, _push_zep)

    # Cache result
    _set_cached(cache_key, preview)

    return preview


def _build_context_string(context, odds: dict) -> str:
    """Build a compact context string for the LLM prompt."""
    lines = [
        f"Match: {context.home_team} vs {context.away_team}",
        f"Competition: {context.competition}",
        f"Date: {context.analysis_date or datetime.utcnow().strftime('%Y-%m-%d')}",
    ]

    if odds:
        odds_str = ", ".join(f"{k}: {v}" for k, v in odds.items())
        lines.append(f"Polymarket odds: {odds_str}")

    if context.home_league_position:
        lines.append(f"{context.home_team} league position: {context.home_league_position}")
    if context.away_league_position:
        lines.append(f"{context.away_team} league position: {context.away_league_position}")

    if context.home_form:
        f = context.home_form
        lines.append(
            f"{context.home_team} last {f.played}: "
            f"{f.wins}W {f.draws}D {f.losses}L, {f.goals_for}GF {f.goals_against}GA"
        )
    if context.away_form:
        f = context.away_form
        lines.append(
            f"{context.away_team} last {f.played}: "
            f"{f.wins}W {f.draws}D {f.losses}L, {f.goals_for}GF {f.goals_against}GA"
        )

    if context.head_to_head:
        lines.append("Recent H2H:")
        for h2h in context.head_to_head[:3]:
            lines.append(f"  {h2h.get('date', '?')}: {h2h.get('home', '?')} {h2h.get('score', '?')} {h2h.get('away', '?')}")

    if context.home_injuries:
        lines.append(f"{context.home_team} injuries: {', '.join(context.home_injuries[:3])}")
    if context.away_injuries:
        lines.append(f"{context.away_team} injuries: {', '.join(context.away_injuries[:3])}")

    return "\n".join(lines)


def _cache_key(slug: str, odds: dict | None) -> str:
    raw = f"{slug}:{json.dumps(odds or {}, sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key: str) -> dict | None:
    entry = _preview_cache.get(key)
    if not entry:
        return None
    if time.time() > entry["expires_at"]:
        del _preview_cache[key]
        return None
    return entry["data"]


def _set_cached(key: str, data: dict) -> None:
    _preview_cache[key] = {
        "data": data,
        "expires_at": time.time() + settings.preview_cache_ttl,
    }
