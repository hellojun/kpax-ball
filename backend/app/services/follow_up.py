"""Follow-up question handler — single expert answers the user's question.

基于已有的专家辩论记录 + 用户追问，由一位专家直接回答。
不重跑辩论，不重新生成报告，只给出针对性回答。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from app.services.ai_provider import chat_completion

logger = logging.getLogger(__name__)

FOLLOW_UP_SYSTEM = {
    "zh": """你是 KPAX Ball 的足球分析专家。
你刚刚参与了一场关于足球盘口的专家辩论，现在用户有一个追问。

基于你对这场比赛的分析和辩论记录，直接回答用户的问题。
要求：
- 简洁有力，控制在 150 字以内
- 用要点列表回答
- 如果问题会影响概率判断，说明调整方向和幅度
- 用中文回复""",

    "en": """You are a KPAX Ball football analysis expert.
You just participated in an expert debate about a football market. The user has a follow-up question.

Based on your analysis and debate, answer the question directly.
Rules:
- Concise, under 100 words
- Use bullet points
- If the question affects probability, state the adjustment direction and magnitude
- Respond in English""",
}


async def run_follow_up(
    slug: str,
    question: str,
    polymarket_odds: dict | None,
    debate_messages: list[dict],
    previous_report: dict,
    language: str = "zh",
) -> AsyncGenerator[dict, None]:
    """Single expert answers the follow-up question."""
    yield {"type": "status", "content": "正在回应追问..." if language == "zh" else "Processing follow-up..."}

    odds = polymarket_odds or {}
    odds_str = ", ".join(f"{k}: {v:.0%}" for k, v in odds.items() if v)

    # 上一份报告核心判断
    prev_cj = previous_report.get("coreJudgment", {})
    prev_summary = (
        f"当前判断: 主胜{prev_cj.get('homeWinPct', 0):.0%} "
        f"平{prev_cj.get('drawPct', 0):.0%} "
        f"客胜{prev_cj.get('awayWinPct', 0):.0%} "
        f"(置信度: {prev_cj.get('confidence', '?')})"
    )

    # 精简辩论摘要
    debate_summary = "\n".join(
        f"[{m.get('expert', '?')}]: {m.get('content', '')[:150]}"
        for m in debate_messages[-6:]
    )

    user_prompt = (
        f"Polymarket 赔率: {odds_str}\n"
        f"{prev_summary}\n\n"
        f"辩论摘要:\n{debate_summary}\n\n"
        f"用户追问: {question}"
    )

    try:
        from app.config import settings
        answer = await chat_completion(
            messages=[
                {"role": "system", "content": FOLLOW_UP_SYSTEM.get(language, FOLLOW_UP_SYSTEM["zh"])},
                {"role": "user", "content": user_prompt},
            ],
            model=settings.quick_preview_model,  # glm-4-plus，快且可靠
            temperature=0.4,
            max_tokens=500,
        )

        if not answer or not answer.strip():
            yield {"type": "error", "content": "专家未能生成回答，请重试" if language == "zh" else "Expert failed to generate answer, please retry"}
            return

        yield {
            "type": "followup_answer",
            "content": answer,
            "question": question,
        }

    except Exception as exc:
        logger.error("Follow-up failed: %s", exc)
        yield {"type": "error", "content": str(exc)}
