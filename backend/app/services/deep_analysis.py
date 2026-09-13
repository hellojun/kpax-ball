"""Deep analysis engine — full expert debate for football markets."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator

from app.services.debate_engine import (
    DebateMessage,
    generate_agents,
    generate_final_summary,
    run_debate_stream,
)
from app.services.football_data import get_match_context_structured
from app.services.market_parser import parse_market
from app.services.quick_preview import _build_context_string
from app.services.report_generator import generate_report

logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    "zh": {
        "fetching": "正在从 Polymarket 获取盘口数据...",
        "not_football": "该盘口未被识别为足球市场。",
        "analyzing": "正在分析 {home} vs {away}（{comp}）...",
        "gathering": "正在收集足球数据...",
        "assembling": "正在组建专家团...",
        "panel": "专家团：{names}",
        "generating": "正在生成最终报告...",
        "complete": "分析完成。",
    },
    "en": {
        "fetching": "Fetching market data from Polymarket...",
        "not_football": "This market is not recognized as a football market.",
        "analyzing": "Analyzing {home} vs {away} ({comp})...",
        "gathering": "Gathering football data...",
        "assembling": "Assembling expert panel...",
        "panel": "Expert panel: {names}",
        "generating": "Generating final report...",
        "complete": "Analysis complete.",
    },
}


def _msg(language: str, key: str, **kwargs: str) -> str:
    msgs = STATUS_MESSAGES.get(language, STATUS_MESSAGES["zh"])
    return msgs[key].format(**kwargs)


async def run_deep_analysis(
    slug: str,
    polymarket_odds: dict | None = None,
    user_context: str | None = None,
    language: str = "zh",
    home_team: str | None = None,
    away_team: str | None = None,
    competition: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Run full expert debate analysis. Yields SSE-compatible dicts."""
    start_time = time.time()

    # Step 1: 优先使用前端传来的球队信息
    if home_team and away_team:
        competition = competition or "Football"
        odds = polymarket_odds or {}
    else:
        yield {"type": "status", "content": _msg(language, "fetching")}
        market = await parse_market(slug)
        if not market.get("is_football"):
            yield {"type": "error", "content": _msg(language, "not_football")}
            return
        home_team = market["home_team"]
        away_team = market["away_team"]
        competition = market["competition"]
        odds = polymarket_odds or market.get("polymarket_odds", {})

    yield {
        "type": "status",
        "content": _msg(language, "analyzing", home=home_team, away=away_team, comp=competition),
    }

    # Step 2: Get football context
    yield {"type": "status", "content": _msg(language, "gathering")}
    context = await get_match_context_structured(home_team, away_team, competition)
    match_info = _build_context_string(context, odds)

    # 保存不含用户追问的原始 match_info，用于 ZEP 写入
    base_match_info = match_info

    if user_context:
        match_info += f"\n\nUser's additional context: {user_context}"

    # ZEP: 检索历史分析记忆
    from app.services.zep_manager import get_match_history, get_team_insights
    zep_history = get_match_history(home_team, away_team)
    if zep_history:
        match_info += f"\n\n历史分析记忆:\n{zep_history}"

    # Step 3: Generate expert agents
    yield {"type": "status", "content": _msg(language, "assembling")}
    agents = generate_agents(match_info, odds, language=language)

    expert_names = [a.name for a in agents if not a.is_moderator]
    yield {
        "type": "status",
        "content": _msg(language, "panel", names=", ".join(expert_names)),
    }

    # Step 4: Run debate
    debate_history: list[DebateMessage] = []

    async for msg in run_debate_stream(agents, num_rounds=2):
        debate_history.append(msg)

        if msg.role == "moderator":
            yield {
                "type": "moderator",
                "content": msg.content,
                "round": msg.round_number,
            }
        else:
            yield {
                "type": "expert",
                "expert": msg.expert,
                "role": msg.role,
                "round": msg.round_number,
                "content": msg.content,
            }

    # Step 5: Generate final summary
    yield {"type": "status", "content": _msg(language, "generating")}

    final_summary = await generate_final_summary(agents, debate_history, odds)

    # Step 6: Structure the report
    try:
        report = await generate_report(
            debate_messages=[
                {"expert": m.expert, "role": m.role, "content": m.content, "round": m.round_number}
                for m in debate_history
            ],
            final_summary=final_summary,
            market_data={"home_team": home_team, "away_team": away_team, "competition": competition},
            match_context={
                "home_team": home_team,
                "away_team": away_team,
                "competition": competition,
            },
            polymarket_odds=odds,
            language=language,
        )

        elapsed = time.time() - start_time
        logger.info("Deep analysis completed in %.1fs for %s", elapsed, slug)

        yield {"type": "report", "content": report}

        # ZEP: 写入辩论总结（不含用户追问，避免临时假设污染历史记忆）
        if not user_context:
            try:
                from app.services.zep_manager import push_debate_summary
                push_debate_summary(
                    home_team=home_team,
                    away_team=away_team,
                    competition=competition,
                    consensus=final_summary[:500],
                    disagreements="",
                    key_variables="",
                )
            except Exception as e:
                logger.warning("ZEP debate summary push failed: %s", e)

            # ZEP: 专家认知蒸馏（仅首次分析，追问场景跳过）
            try:
                from app.services.agent_memory import distill_expert_cognition
                for agent in agents:
                    if agent.is_moderator:
                        continue
                    agent_msgs = [m.content for m in debate_history if m.expert == agent.name]
                    if agent_msgs:
                        await distill_expert_cognition(
                            expert_role=agent.role,
                            expert_name=agent.name,
                            debate_content="\n".join(agent_msgs),
                            match_context=f"{home_team} vs {away_team} {competition}",
                        )
            except Exception as e:
                logger.warning("Expert cognition distillation failed: %s", e)

    except Exception as exc:
        logger.error("Report generation failed: %s", exc, exc_info=True)
        yield {
            "type": "report",
            "content": {
                "coreJudgment": {
                    "homeWinPct": odds.get("home", 0.33),
                    "awayWinPct": odds.get("away", 0.33),
                    "drawPct": odds.get("draw", 0.34),
                    "confidence": "low",
                    "confidenceReason": "报告生成失败" if language == "zh" else "Report generation failed",
                    "marketDeviation": {},
                },
                "sections": [
                    {
                        "dimension": "summary",
                        "title": "专家辩论总结" if language == "zh" else "Expert Debate Summary",
                        "content": final_summary,
                    }
                ],
                "keyVariables": [],
                "disagreements": [],
                "historicalAccuracy": None,
            },
        }

    yield {"type": "status", "content": _msg(language, "complete")}
