"""Report generator — structure debate results into a readable report.

Takes raw debate messages + moderator summary and produces a
structured FullReport JSON for the frontend.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

from app.services.ai_provider import chat_completion_json

logger = logging.getLogger(__name__)


@dataclass
class CoreJudgment:
    home_win_pct: float
    away_win_pct: float
    draw_pct: float
    confidence: str  # "high" | "medium" | "low"
    confidence_reason: str
    market_deviation: dict  # {"home": -0.07, "away": +0.01, "draw": +0.06}


@dataclass
class AnalysisSection:
    dimension: str  # "tactical_network", "statistical_model", etc.
    title: str
    content: str


@dataclass
class KeyVariable:
    description: str
    impact: str  # What changes if this happens


@dataclass
class ExpertDisagreement:
    expert_a: str
    expert_b: str
    topic: str
    summary: str


@dataclass
class FullReport:
    core_judgment: CoreJudgment
    sections: list[AnalysisSection]
    key_variables: list[KeyVariable]
    disagreements: list[ExpertDisagreement]
    historical_accuracy: str | None = None


REPORT_SYNTHESIS_PROMPTS = {
    "zh": """根据专家辩论，生成简洁的分析报告 JSON（不要 markdown 包裹）。所有字段用中文，每个字段不超过 30 字。""",
    "en": """Based on expert debate, generate a concise analysis report JSON (no markdown). Keep each field under 30 words.""",
}

REPORT_SCHEMA = """
{{"core_judgment":{{"home_win_pct":<0-1>,"away_win_pct":<0-1>,"draw_pct":<0-1>,"confidence":"<high|medium|low>","confidence_reason":"<一句话>"}},"sections":[{{"dimension":"summary","title":"<标题>","content":"<核心结论，2句话>"}},{{"dimension":"risk","title":"<标题>","content":"<主要风险，2句话>"}}],"key_variables":[{{"description":"<变量>","impact":"<影响>"}}],"disagreements":[{{"expert_a":"<名>","expert_b":"<名>","topic":"<分歧>","summary":"<一句话>"}}]}}

概率之和=1.0。sections 只要 2 个。key_variables 1-2 个。disagreements 0-1 个。尽量精简。
- Be specific and data-driven, not generic"""

def _get_report_prompt(language: str) -> str:
    prefix = REPORT_SYNTHESIS_PROMPTS.get(language, REPORT_SYNTHESIS_PROMPTS["zh"])
    return prefix + REPORT_SCHEMA


async def generate_report(
    debate_messages: list[dict],
    final_summary: str,
    market_data: dict,
    match_context: dict,
    polymarket_odds: dict,
    language: str = "zh",
) -> dict:
    """Generate a structured report from debate results.

    Returns a FullReport-compatible dict for the frontend.
    """
    # Build the debate transcript for LLM
    transcript_lines = []
    for msg in debate_messages:
        transcript_lines.append(
            f"[Round {msg['round']} — {msg['expert']}]:\n{msg['content']}"
        )
    transcript = "\n\n".join(transcript_lines)

    odds_str = ", ".join(f"{k}: {v:.0%}" for k, v in polymarket_odds.items() if v)

    user_prompt = (
        f"Match: {match_context.get('home_team', '?')} vs {match_context.get('away_team', '?')}\n"
        f"Competition: {match_context.get('competition', '?')}\n"
        f"Polymarket odds: {odds_str}\n\n"
        f"## Expert Debate Transcript\n{transcript}\n\n"
        f"## Moderator Final Summary\n{final_summary}\n\n"
        f"Synthesize this debate into a structured report."
    )

    from app.config import settings
    result = await chat_completion_json(
        messages=[
            {"role": "system", "content": _get_report_prompt(language)},
            {"role": "user", "content": user_prompt},
        ],
        model=settings.quick_preview_model,  # glm-4-plus, JSON 输出可靠
        temperature=0.3,
        max_tokens=4096,
    )

    # Calculate market deviation
    cj = result.get("core_judgment", {})
    deviation = {}
    for key, pct_key in [("home", "home_win_pct"), ("away", "away_win_pct"), ("draw", "draw_pct")]:
        kpax_val = cj.get(pct_key, 0)
        market_val = polymarket_odds.get(key, 0)
        if kpax_val or market_val:
            deviation[key] = round(kpax_val - market_val, 4)

    # Build frontend-compatible response
    return {
        "coreJudgment": {
            "homeWinPct": cj.get("home_win_pct", 0.33),
            "awayWinPct": cj.get("away_win_pct", 0.33),
            "drawPct": cj.get("draw_pct", 0.34),
            "confidence": cj.get("confidence", "medium"),
            "confidenceReason": cj.get("confidence_reason", ""),
            "marketDeviation": deviation,
        },
        "sections": [
            {
                "dimension": s.get("dimension", ""),
                "title": s.get("title", ""),
                "content": s.get("content", ""),
            }
            for s in result.get("sections", [])
        ],
        "keyVariables": [
            {
                "description": kv.get("description", ""),
                "impact": kv.get("impact", ""),
            }
            for kv in result.get("key_variables", [])
        ],
        "disagreements": [
            {
                "expertA": d.get("expert_a", ""),
                "expertB": d.get("expert_b", ""),
                "topic": d.get("topic", ""),
                "summary": d.get("summary", ""),
            }
            for d in result.get("disagreements", [])
        ],
        "historicalAccuracy": None,
    }
